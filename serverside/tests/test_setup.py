import base64
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np
import torch
import tink
from tink import cleartext_keyset_handle, hybrid, streaming_aead
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

import audio_processer
import db_interaction
from db.models import Base, Person, Recording, SetupState, TranscriptionChunk
import encryption
import main
import setup_service


class Buffer(BytesIO):
    def close(self):
        pass


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(TemporaryDirectory())
        self.engine = create_engine(f"sqlite:///{directory}/test.db", connect_args={"check_same_thread": False})
        self.addCleanup(self.engine.dispose)
        self.sessions = sessionmaker(bind=self.engine)
        for module in (main, setup_service, db_interaction):
            self.stack.enter_context(patch.object(module, "SessionLocal", self.sessions))
        self.stack.enter_context(patch.object(main, "initialize_database", lambda: Base.metadata.create_all(self.engine)))
        self.stack.enter_context(patch.object(encryption, "FILEPATH", str(Path(directory) / "private.key")))
        main.recordings.clear()
        main.processing_recordings.clear()
        main.upload_events.clear()
        self.release = Event()
        self.release.set()
        self.transcribing = Event()
        self.stack.enter_context(patch.object(audio_processer, "transcribe_with_whisperx", side_effect=self.transcribe))
        text_model = Mock()
        text_model.encode.side_effect = lambda texts, **kwargs: np.ones((len(texts), 384))
        speaker_model = Mock()
        speaker_model.encode_batch.return_value = torch.ones((1, 1, 192))
        self.stack.enter_context(patch.object(audio_processer, "get_text_embedding_model", return_value=text_model))
        self.stack.enter_context(patch.object(audio_processer, "get_speaker_model", return_value=speaker_model))
        self.client = self.stack.enter_context(TestClient(main.app))

    def tearDown(self):
        self.release.set()
        self.wait_for(lambda: not main.processing_recordings)

    def transcribe(self, audio, progress, enrollment=False):
        progress("transcribing", "Transcribing your recording.")
        self.transcribing.set()
        if not self.release.wait(5):
            raise RuntimeError("Test did not release transcription")
        progress("aligning", "Aligning spoken words.")
        return {"language": "en", "segments": [{"words": [
            {"word": "hello", "start": i / 2, "end": (i + 1) / 2, "score": .99}
            for i in range(60)
        ]}]}

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(.01)
        self.fail("Background processing did not reach the expected state")

    def status(self):
        response = self.client.get("/api/setup/status")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def arm(self):
        self.assertEqual(self.client.post("/api/keys/create").status_code, 200)
        for step in ("reading", "awaiting_upload"):
            self.assertEqual(self.client.post("/api/setup/step", json={"step": step}).status_code, 200)

    def chunks(self, timestamp=123456, count=2, seconds=31):
        symmetric = tink.new_keyset_handle(streaming_aead.streaming_aead_key_templates.AES256_GCM_HKDF_4KB)
        serialized = Buffer()
        cleartext_keyset_handle.write(tink.BinaryKeysetWriter(serialized), symmetric)
        public = encryption._load_hpke_private_keyset_handle().public_keyset_handle()
        wrapped = public.primitive(hybrid.HybridEncrypt).encrypt(serialized.getvalue(), b"")
        pcm = np.full(16000 * seconds, 1000, dtype="<i2").tobytes()
        chunks = []
        for index in range(count):
            output = Buffer()
            with symmetric.primitive(streaming_aead.StreamingAead).new_encrypting_stream(output, b"") as stream:
                stream.write(pcm[len(pcm) * index // count:len(pcm) * (index + 1) // count])
            chunks.append({"timestamp": timestamp, "chunkIndex": index, "totalChunks": count,
                           "encryptedSerializedSymmetricKey": base64.b64encode(wrapped).decode(),
                           "data": base64.b64encode(output.getvalue()).decode()})
        return chunks

    def send(self, chunks):
        for chunk in chunks:
            self.assertEqual(self.client.post("/upload", json=chunk).status_code, 200)

    def test_keys_required_and_existing_keys_reused(self):
        self.assertFalse(self.status()["keysExist"])
        self.assertEqual(self.client.post("/api/setup/step", json={"step": "reading"}).status_code, 409)
        first = self.client.post("/api/keys/create").json()
        second = self.client.post("/api/keys/create").json()
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["publicKey"], second["publicKey"])
        self.assertNotIn("privateKey", second)
        self.assertEqual(self.status()["stage"], "device")
        self.assertEqual(self.client.get("/api/keys/qrcode").headers["content-type"], "image/svg+xml")
        self.assertEqual(self.client.get("/", follow_redirects=False).headers["location"], "/ui")

    def test_encrypted_upload_progress_and_profile_persist(self):
        self.arm()
        chunks = self.chunks()
        self.send([chunks[1], chunks[1]])
        status = self.status()
        self.assertEqual((status["stage"], status["receivedChunks"], status["totalChunks"]), ("receiving", 1, 2))
        self.release.clear()
        self.send([chunks[0]])
        self.assertTrue(self.transcribing.wait(5))
        self.assertEqual(self.status()["stage"], "transcribing")
        self.assertEqual(self.client.post("/api/setup/step", json={"step": "reading"}).status_code, 409)
        self.send(chunks)
        self.release.set()
        self.wait_for(lambda: self.status()["stage"] == "complete")
        with self.sessions() as session:
            person = session.scalar(select(Person))
            self.assertEqual(person.name, "Me")
            self.assertEqual(len(person.voice_embedding), 192)
            self.assertAlmostEqual(float(np.linalg.norm(person.voice_embedding)), 1, places=5)
            self.assertEqual(person.voice_embedding_word_count, 60)
            self.assertEqual(session.scalar(select(TranscriptionChunk.person_id)), person.id)
            self.assertEqual(session.scalar(select(func.count()).select_from(Recording)), 1)
        setup_service.initialize_setup()
        self.assertEqual(self.status()["stage"], "complete")
        self.send(chunks)
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Person)), 1)
        self.assertEqual([item["stage"] for item in self.status()["history"]], [
            "awaiting_upload", "receiving", "validating", "transcribing", "aligning",
            "text_embedding", "voice_embedding", "saving", "complete",
        ])
        self.assertEqual(self.client.get("/api/setup/status").headers["cache-control"], "no-store")
        self.assertNotIn("data", self.client.get("/api/uploads").text)

    def test_restart_recovers_reading_and_marks_incomplete_upload_for_retry(self):
        self.arm()
        setup_service.initialize_setup()
        self.assertEqual(self.status()["stage"], "awaiting_upload")
        self.send(self.chunks()[:1])
        setup_service.initialize_setup()
        self.assertEqual(self.status()["stage"], "failed")
        self.assertIn("restarted", self.status()["error"])
        self.assertEqual(self.client.post("/api/setup/step", json={"step": "awaiting_upload"}).status_code, 200)
        self.assertEqual(self.status()["receivedChunks"], 0)
        self.assertFalse(main.recordings)

    def test_invalid_chunk_is_rejected_and_can_be_retried(self):
        self.arm()
        chunks = self.chunks()
        broken = {**chunks[0], "data": "not base64"}
        self.assertEqual(self.client.post("/upload", json=broken).status_code, 400)
        self.assertEqual(self.status()["stage"], "failed")
        self.assertEqual(self.client.post("/upload", json=chunks[0]).status_code, 409)
        self.assertEqual(self.client.post("/api/setup/step", json={"step": "awaiting_upload"}).status_code, 200)
        self.send(chunks)
        self.wait_for(lambda: self.status()["stage"] == "complete")

    def test_invalid_index_and_changed_metadata_do_not_stitch(self):
        self.arm()
        chunks = self.chunks()
        invalid = {**chunks[0], "chunkIndex": 2}
        self.assertEqual(self.client.post("/upload", json=invalid).status_code, 422)
        self.send(chunks[:1])
        invalid = {**chunks[1], "totalChunks": 3}
        self.assertEqual(self.client.post("/upload", json=invalid).status_code, 400)
        self.assertEqual(self.status()["stage"], "failed")

    def test_short_recording_is_not_enrolled(self):
        self.arm()
        self.send(self.chunks(seconds=5))
        self.wait_for(lambda: self.status()["stage"] == "failed")
        self.assertIn("30 seconds", self.status()["error"])
        with self.sessions() as session:
            self.assertIsNone(session.scalar(select(Person)))

    def test_processing_failure_rolls_back_profile_and_allows_retry(self):
        self.arm()
        chunks = self.chunks()
        with patch.object(setup_service, "complete_in_session", side_effect=RuntimeError("Database failure")):
            self.send(chunks)
            self.wait_for(lambda: self.status()["stage"] == "failed")
            self.wait_for(lambda: not main.processing_recordings)
        with self.sessions() as session:
            self.assertIsNone(session.scalar(select(Person)))
            self.assertIsNone(session.scalar(select(Recording)))
        self.client.post("/api/setup/step", json={"step": "awaiting_upload"})
        self.send(chunks)
        self.wait_for(lambda: self.status()["stage"] == "complete")

    def test_regular_upload_does_not_enroll_or_change_setup(self):
        self.client.post("/api/keys/create")
        self.send(self.chunks())
        self.wait_for(lambda: not main.processing_recordings)
        self.assertEqual(self.status()["stage"], "device")
        with self.sessions() as session:
            self.assertIsNotNone(session.scalar(select(Recording)))
            self.assertIsNone(session.scalar(select(Person)))

    def test_chunk_boundaries_skip_unaligned_words_and_preserve_chunk_size(self):
        words = [audio_processer.TranscriptWord("word", i * 10, (i + 1) * 10, None, .9) for i in range(501)]
        words.insert(10, audio_processer.TranscriptWord("unaligned", None, None, None, None))
        result = audio_processer.AudioProcessingResult(1, "en", words)
        audio, chunks, _ = audio_processer.extract_chunks(bytes(16000 * 2 * 10), result)
        self.assertEqual([chunk.word_count for chunk in chunks], [250, 250, 1])
        self.assertTrue(all(audio))


if __name__ == "__main__":
    unittest.main()
