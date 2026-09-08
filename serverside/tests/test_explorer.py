from contextlib import ExitStack
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.ext.compiler import compiles, deregister
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.elements import BinaryExpression

import db_interaction
from db.models import (
    Base,
    Person,
    Recording,
    SetupState,
    TEXT_EMBEDDING_DIM,
    TranscriptionChunk,
    TranscriptionWord,
    VOICE_EMBEDDING_DIM,
)
import encryption
import explorer_service
import main
import setup_service


def vector(first=1, second=0, dimensions=VOICE_EMBEDDING_DIM):
    return [first, second] + [0] * (dimensions - 2)


def sqlite_cosine_distance(left, right):
    if left is None or right is None:
        return None
    left, right = np.asarray(json.loads(left)), np.asarray(json.loads(right))
    if left.shape != right.shape or not np.isfinite(left).all() or not np.isfinite(right).all():
        return None
    denominator = np.linalg.norm(left) * np.linalg.norm(right)
    if denominator == 0:
        return None
    return float(1 - np.clip(np.dot(left, right) / denominator, -1, 1))


def sqlite_binary(element, compiler, **kwargs):
    # Exercise the real pgvector queries on the same SQLite fixture as setup tests.
    if getattr(element.operator, "opstring", None) == "<=>":
        return "cosine_distance({}, {})".format(
            compiler.process(element.left, **kwargs),
            compiler.process(element.right, **kwargs),
        )
    return compiler.visit_binary(element, **kwargs)


class ExplorerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiles(BinaryExpression, "sqlite")(sqlite_binary)

    @classmethod
    def tearDownClass(cls):
        deregister(BinaryExpression)

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(TemporaryDirectory())
        database_url = os.getenv("EXPLORER_TEST_DATABASE_URL")
        schema = f"explorer_test_{uuid4().hex}"
        if database_url:
            self.engine = create_engine(database_url, execution_options={"schema_translate_map": {None: schema}})
        else:
            self.engine = create_engine(
                f"sqlite:///{directory}/test.db", connect_args={"check_same_thread": False}
            )
        self.stack.callback(self.engine.dispose)
        if database_url:
            with self.engine.begin() as connection:
                connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            self.stack.callback(self.drop_schema, schema)
        else:
            event.listen(
                self.engine,
                "connect",
                lambda connection, _: connection.create_function("cosine_distance", 2, sqlite_cosine_distance),
            )
            event.listen(self.engine, "connect", lambda connection, _: connection.create_collation(
                "C", lambda left, right: (left > right) - (left < right),
            ))
        self.sessions = sessionmaker(bind=self.engine)
        for module in (main, setup_service, db_interaction, explorer_service):
            self.stack.enter_context(patch.object(module, "SessionLocal", self.sessions))
        self.stack.enter_context(
            patch.object(main, "initialize_database", lambda: Base.metadata.create_all(self.engine))
        )
        self.stack.enter_context(patch.object(encryption, "FILEPATH", str(Path(directory) / "private.key")))
        self.client = self.stack.enter_context(TestClient(main.app))
        self.next_timestamp = 1_700_000_000_000

    def drop_schema(self, schema):
        with self.engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')

    def recording(self, timestamp=None, language="en"):
        self.next_timestamp += 1000
        with self.sessions.begin() as session:
            recording = Recording(timestamp=timestamp or self.next_timestamp, language=language)
            session.add(recording)
            session.flush()
            return recording.id

    def person(self, name="Alex", embedding=None, word_count=0):
        with self.sessions.begin() as session:
            person = Person(name=name, voice_embedding=embedding, voice_embedding_word_count=word_count)
            session.add(person)
            session.flush()
            return person.id

    def chunk(self, spoken="hello there", voice=None, words=None, person_id=None,
              recording_id=None, chunk_index=0, text_vector=None, confidences=None):
        recording_id = recording_id or self.recording()
        with self.sessions.begin() as session:
            chunk = TranscriptionChunk(
                recording_id=recording_id,
                person_id=person_id,
                chunk_index=chunk_index,
                text=spoken,
                word_count=len(spoken.split()) if words is None else words,
                start_ms=chunk_index * 1000,
                end_ms=(chunk_index + 1) * 1000,
                voice_embedding=voice if voice is not None else vector(),
                text_embedding=text_vector if text_vector is not None else vector(dimensions=TEXT_EMBEDDING_DIM),
            )
            if confidences is not None:
                chunk.words = [TranscriptionWord(
                    word_index=index,
                    word=word,
                    start_ms=index * 100,
                    end_ms=(index + 1) * 100,
                    raw_speaker_label="SPEAKER_00",
                    confidence=confidence,
                ) for index, word, confidence in confidences]
            session.add(chunk)
            session.flush()
            return chunk.id

    def get(self, endpoint, **params):
        response = self.client.get(f"/api/explorer/{endpoint}", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def assign(self, chunk_id, person_id):
        response = self.client.put(f"/api/explorer/chunks/{chunk_id}/person", json={"personId": person_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def assert_profile(self, person_id, expected, word_count):
        with self.sessions() as session:
            person = session.get(Person, person_id)
            self.assertEqual(person.voice_embedding_word_count, word_count)
            if expected is None:
                self.assertIsNone(person.voice_embedding)
            else:
                expected = np.asarray(expected, dtype=float)
                expected /= np.linalg.norm(expected)
                np.testing.assert_allclose(person.voice_embedding, expected, atol=1e-6)
                self.assertAlmostEqual(np.linalg.norm(person.voice_embedding), 1, places=6)

    def test_assignment_uses_word_weighted_raw_vectors_and_is_idempotent(self):
        person_id = self.person()
        short = self.chunk(voice=vector(2, 0), words=1)
        long = self.chunk(voice=vector(0, 4), words=3)
        self.assign(short, person_id)
        self.assign(long, person_id)
        self.assert_profile(person_id, vector(2, 12), 4)
        self.assign(long, person_id)
        self.assert_profile(person_id, vector(2, 12), 4)
        person = self.get(f"persons/{person_id}")
        self.assertEqual((person["chunkCount"], person["recordingCount"], person["wordCount"]), (2, 2, 4))

    def test_reassignment_and_unassignment_recompute_both_profiles(self):
        first, second = self.person("First"), self.person("Second")
        source = self.chunk(voice=vector(2, 0), words=2)
        other = self.chunk(voice=vector(0, 3), words=5)
        self.assign(source, first)
        self.assign(other, second)
        self.assign(source, second)
        self.assert_profile(first, None, 0)
        self.assert_profile(second, vector(4, 15), 7)
        self.assign(source, None)
        self.assert_profile(second, vector(0, 3), 5)
        self.assign(other, None)
        self.assert_profile(second, None, 0)
        with self.sessions() as session:
            self.assertIsNone(session.get(TranscriptionChunk, source).person_id)
            self.assertIsNone(session.get(TranscriptionChunk, other).person_id)

    def test_new_person_initializes_embedding_from_selected_chunk(self):
        chunk_id = self.chunk(voice=vector(3, 4), words=9)
        response = self.client.post("/api/explorer/persons", json={"name": "  Morgan  ", "chunkId": chunk_id})
        self.assertIn(response.status_code, (200, 201), response.text)
        result = response.json()
        self.assertEqual(result["person"]["name"], "Morgan")
        self.assertEqual(result["chunk"]["id"], chunk_id)
        self.assert_profile(result["person"]["id"], vector(3, 4), 9)
        with self.sessions() as session:
            self.assertEqual(session.get(TranscriptionChunk, chunk_id).person_id, result["person"]["id"])

    def test_delete_removes_words_and_recomputes_profile(self):
        person_id = self.person()
        recording_id = self.recording()
        removed = self.chunk(recording_id=recording_id, voice=vector(2, 0), words=1,
                             confidences=[(0, "hello", 0.9)])
        retained = self.chunk(recording_id=recording_id, chunk_index=1,
                              voice=vector(0, 4), words=3,
                              confidences=[(0, "there", 0.8)])
        self.assign(removed, person_id)
        self.assign(retained, person_id)
        response = self.client.delete(f"/api/explorer/chunks/{removed}")
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(response.content, b"")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assert_profile(person_id, vector(0, 4), 3)
        with self.sessions() as session:
            self.assertIsNone(session.get(TranscriptionChunk, removed))
            self.assertEqual(session.scalars(select(TranscriptionWord.chunk_id)).all(), [retained])
            self.assertIsNotNone(session.get(Recording, recording_id))
        self.assertEqual(self.get("chunks", assignment="all")["total"], 1)
        self.assertEqual(self.get(f"persons/{person_id}/chunks")["total"], 1)
        self.assertEqual(self.get(f"persons/{person_id}")["wordCount"], 3)
        self.assertEqual(self.client.get(f"/api/explorer/chunks/{removed}").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/explorer/chunks/{removed}").status_code, 404)

    def test_delete_last_sample_keeps_person_and_clears_profile(self):
        person_id = self.person()
        chunk_id = self.chunk(words=3)
        self.assign(chunk_id, person_id)
        self.assertEqual(self.client.delete(f"/api/explorer/chunks/{chunk_id}").status_code, 204)
        self.assert_profile(person_id, None, 0)
        person = self.get(f"persons/{person_id}")
        self.assertEqual((person["chunkCount"], person["recordingCount"], person["wordCount"]), (0, 0, 0))
        self.assertFalse(person["hasVoiceEmbedding"])

    def test_delete_unassigned_chunk_without_usable_embedding(self):
        chunk_id = self.chunk(voice=vector(0, 0))
        self.assertEqual(self.client.delete(f"/api/explorer/chunks/{chunk_id}").status_code, 204)
        self.assertEqual(self.get("chunks")["total"], 0)
        self.assertEqual(self.client.delete("/api/explorer/chunks/99999").status_code, 404)

    def test_delete_rolls_back_words_and_chunk_when_profile_update_fails(self):
        person_id = self.person()
        chunk_id = self.chunk(confidences=[(0, "hello", 0.9)])
        self.assign(chunk_id, person_id)
        with patch.object(explorer_service, "_recompute_person", side_effect=SQLAlchemyError("failed")):
            response = self.client.delete(f"/api/explorer/chunks/{chunk_id}")
        self.assertEqual(response.status_code, 503, response.text)
        self.assert_profile(person_id, vector(), 2)
        with self.sessions() as session:
            self.assertEqual(len(session.get(TranscriptionChunk, chunk_id).words), 1)

    def test_invalid_combined_profile_rolls_back_reassignment(self):
        first, second = self.person("First"), self.person("Second")
        source = self.chunk(voice=vector(1, 0), words=2)
        opposite = self.chunk(voice=vector(-1, 0), words=2)
        self.assign(source, first)
        self.assign(opposite, second)
        response = self.client.put(f"/api/explorer/chunks/{source}/person", json={"personId": second})
        self.assertEqual(response.status_code, 422, response.text)
        self.assert_profile(first, vector(1, 0), 2)
        self.assert_profile(second, vector(-1, 0), 2)
        with self.sessions() as session:
            self.assertEqual(session.get(TranscriptionChunk, source).person_id, first)
            self.assertEqual(session.get(TranscriptionChunk, opposite).person_id, second)

    def test_new_person_can_take_existing_association_without_leaving_old_profile(self):
        person_id = self.person()
        chunk_id = self.chunk(words=3)
        self.assign(chunk_id, person_id)
        response = self.client.post("/api/explorer/persons", json={"name": "Taylor", "chunkId": chunk_id})
        self.assertIn(response.status_code, (200, 201), response.text)
        self.assert_profile(person_id, None, 0)
        self.assert_profile(response.json()["person"]["id"], vector(), 3)

    def test_manual_assignment_without_valid_voice_preserves_empty_profiles(self):
        person_id = self.person()
        invalid_sources = [
            self.chunk(voice=vector(0, 0)),
            self.chunk(words=0),
            self.chunk(words=-1),
        ]
        # PostgreSQL already prevents these malformed vectors from being stored.
        corrupt_vectors = ("[1,0]", json.dumps(vector(float("nan"), 0))) if self.engine.dialect.name == "sqlite" else ()
        for raw_vector in corrupt_vectors:
            chunk_id = self.chunk()
            with self.engine.begin() as connection:
                connection.execute(
                    text("UPDATE transcription_chunk SET voice_embedding = :value WHERE id = :id"),
                    {"value": raw_vector, "id": chunk_id},
                )
            invalid_sources.append(chunk_id)
        for chunk_id in invalid_sources:
            with self.subTest(chunk_id=chunk_id):
                response = self.client.post("/api/explorer/persons", json={"name": "Invalid", "chunkId": chunk_id})
                self.assertEqual(response.status_code, 201, response.text)
                self.assertFalse(response.json()["person"]["hasVoiceEmbedding"])
                response = self.client.put(f"/api/explorer/chunks/{chunk_id}/person", json={"personId": person_id})
                self.assertEqual(response.status_code, 200, response.text)
                with self.sessions() as session:
                    self.assertEqual(session.get(TranscriptionChunk, chunk_id).person_id, person_id)
        self.assert_profile(person_id, None, 0)

    def test_rename_trims_name_and_rejects_blank_or_overlong_values(self):
        person_id = self.person()
        response = self.client.patch(f"/api/explorer/persons/{person_id}", json={"name": "  New Name  "})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.get(f"persons/{person_id}")["name"], "New Name")
        for name in ("", " \t ", "x" * 1000):
            response = self.client.patch(f"/api/explorer/persons/{person_id}", json={"name": name})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.get(f"persons/{person_id}")["name"], "New Name")

    def test_transcript_keeps_word_order_confidence_and_metadata(self):
        recording_id = self.recording(language="de")
        chunk_id = self.chunk(
            spoken="Hello, uncertain world!", recording_id=recording_id, words=3,
            confidences=[(2, "world!", .99), (0, "Hello,", .44), (1, "uncertain", None)],
        )
        chunk = self.get(f"chunks/{chunk_id}")
        self.assertEqual(chunk["text"], "Hello, uncertain world!")
        self.assertEqual([word["word"] for word in chunk["words"]], ["Hello,", "uncertain", "world!"])
        self.assertEqual([word["confidence"] for word in chunk["words"]], [.44, None, .99])
        self.assertEqual(chunk["recordingId"], recording_id)
        self.assertEqual(chunk["language"], "de")
        self.assertEqual((chunk["startMs"], chunk["endMs"], chunk["wordCount"]), (0, 1000, 3))
        self.assertTrue(chunk["hasVoiceEmbedding"])

    def test_browse_is_latest_first_and_paginates_all_chunks_without_model(self):
        ids = [self.chunk(spoken=f"chunk {index}") for index in range(7)]
        found = []
        for offset in range(0, 8, 2):
            page = self.get("chunks", limit=2, offset=offset)
            self.assertEqual((page["total"], page["limit"], page["offset"]), (7, 2, offset))
            found.extend(item["id"] for item in page["items"])
        self.assertEqual(found, list(reversed(ids)))

    def test_hybrid_keyword_search_escapes_literals_and_filters_assignments(self):
        person_id = self.person()
        literal = self.chunk(spoken="100% complete_under C:\\notes")
        self.chunk(spoken="1000 completeXunder C:notes", text_vector=vector(0, 1, TEXT_EMBEDDING_DIM))
        self.chunk(spoken="Another separate conversation", person_id=person_id)
        with patch.object(explorer_service, "query_embedding", return_value=vector(dimensions=TEXT_EMBEDDING_DIM)):
            for q in ("100%", "complete_", "COMPLETE_UNDER", "C:\\notes", "100% complete_under"):
                page = self.get("chunks", q=q)
                self.assertEqual([item["id"] for item in page["items"]], [literal], q)
                self.assertEqual(page["items"][0]["matchType"], "keyword")
                conversations = self.get("conversations", q=q, assignment="unassigned")
                self.assertEqual(conversations["items"][0]["matchedChunkIds"], [literal])
        self.assertEqual(self.get("chunks", assignment="assigned")["total"], 1)
        self.assertEqual(self.get("chunks", assignment="unassigned")["total"], 2)

    def test_chunk_search_combines_keyword_and_semantic_matches(self):
        person_id = self.person()
        unassigned = self.chunk(spoken="The Thursday meeting")
        assigned = self.chunk(spoken="The Thursday meeting", person_id=person_id)
        related = self.chunk(spoken="Meeting later this week", text_vector=vector(4, 0, TEXT_EMBEDDING_DIM))
        with patch.object(explorer_service, "query_embedding", return_value=vector(dimensions=TEXT_EMBEDDING_DIM)):
            page = self.get("chunks", q="THURSDAY")
            owned = self.get("chunks", q="Thursday", assignment="assigned")
        self.assertEqual([item["id"] for item in page["items"]], [unassigned, related])
        self.assertEqual([item["matchType"] for item in page["items"]], ["keyword", "semantic"])
        self.assertEqual([item["matchScore"] for item in page["items"]], [1.5, 1.0])
        self.assertEqual(self.get("chunks")["total"], 2)
        self.assertEqual(self.get("chunks", assignment="all")["total"], 3)
        self.assertEqual([item["id"] for item in owned["items"]], [assigned])

    def test_conversations_are_distinct_paginated_and_include_empty_recordings(self):
        first = self.recording()
        self.chunk(recording_id=first, words=3)
        self.chunk(recording_id=first, chunk_index=1, words=4)
        empty = self.recording()
        with patch.object(explorer_service, "query_embedding", side_effect=AssertionError("No inference for browse")):
            page = self.get("conversations", limit=1)
            detail = self.get(f"conversations/{empty}")
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["items"][0]["id"], empty)
        self.assertEqual(detail["chunks"], [])
        item = self.get("conversations", limit=1, offset=1)["items"][0]
        self.assertEqual((item["id"], item["chunkCount"], item["wordCount"]), (first, 2, 7))
        self.assertEqual(item["durationMs"], 2000)

    def test_full_conversation_keeps_context_and_orders_by_time_before_index(self):
        recording = self.recording()
        later = self.chunk(recording_id=recording, chunk_index=10, spoken="The deadline is Thursday.")
        early = self.chunk(recording_id=recording, chunk_index=0, spoken="What is the deadline?",
                           text_vector=vector(0, 1, TEXT_EMBEDDING_DIM))
        middle = self.chunk(recording_id=recording, chunk_index=4, spoken="Let me check.",
                            text_vector=vector(0, 1, TEXT_EMBEDDING_DIM))
        with self.sessions.begin() as session:
            session.get(TranscriptionChunk, early).chunk_index = 30
        with patch.object(explorer_service, "query_embedding", return_value=vector(dimensions=TEXT_EMBEDDING_DIM)):
            detail = self.get(f"conversations/{recording}", q="THURSDAY")
        self.assertEqual([item["id"] for item in detail["chunks"]], [early, middle, later])
        self.assertEqual(detail["matchedChunkIds"], [later])
        self.assertEqual([item["matched"] for item in detail["chunks"]], [False, False, True])
        self.assertEqual(detail["chunks"][0]["text"], "What is the deadline?")

    def test_semantic_search_groups_all_matches_and_preserves_unmatched_context(self):
        recording = self.recording()
        first = self.chunk(recording_id=recording, spoken="The annual budget", text_vector=vector(1, 0, TEXT_EMBEDDING_DIM))
        second = self.chunk(recording_id=recording, chunk_index=1, spoken="Funding the project", text_vector=vector(1, .5, TEXT_EMBEDDING_DIM))
        context = self.chunk(recording_id=recording, chunk_index=2, text_vector=vector(0, 1, TEXT_EMBEDDING_DIM))
        self.chunk(text_vector=vector(-1, 0, TEXT_EMBEDDING_DIM))
        with patch.object(explorer_service, "query_embedding", return_value=vector(dimensions=TEXT_EMBEDDING_DIM)):
            page = self.get("conversations", q="financial planning")
            detail = self.get(f"conversations/{recording}", q="financial planning")
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["matchedChunkIds"], [first, second])
        self.assertAlmostEqual(page["items"][0]["similarity"], 1)
        self.assertAlmostEqual(page["items"][0]["matchScore"], 1 + 1 / np.sqrt(1.25), places=6)
        self.assertAlmostEqual(detail["matchScore"], page["items"][0]["matchScore"])
        self.assertEqual(detail["matchedChunkIds"], [first, second])
        self.assertEqual(detail["chunks"][-1]["id"], context)
        self.assertFalse(detail["chunks"][-1]["matched"])

    def test_sum_of_hybrid_scores_ranks_conversations_before_pagination(self):
        focused = self.recording()
        focused_ids = [self.chunk(recording_id=focused, chunk_index=index, spoken="Banana bread recipe",
                                  text_vector=vector(similarity, np.sqrt(1 - similarity ** 2), TEXT_EMBEDDING_DIM))
                       for index, similarity in enumerate((.8, 1, .6))]
        mixed = self.recording()
        self.chunk(recording_id=mixed, spoken="Banana smoothie recipe", text_vector=vector(1, 0, TEXT_EMBEDDING_DIM))
        self.chunk(recording_id=mixed, chunk_index=1, text_vector=vector(0, 1, TEXT_EMBEDDING_DIM), words=9)
        self.chunk(spoken="unrelated", text_vector=vector(0, 1, TEXT_EMBEDDING_DIM))
        with patch.object(explorer_service, "query_embedding", return_value=vector(dimensions=TEXT_EMBEDDING_DIM)):
            first = self.get("conversations", q="banana", limit=1)
            second = self.get("conversations", q="banana", limit=1, offset=1)
        self.assertEqual(first["total"], 2)
        self.assertEqual(first["items"][0]["id"], focused)
        self.assertEqual(first["items"][0]["matchedChunkIds"], focused_ids)
        self.assertAlmostEqual(first["items"][0]["matchScore"], 3.9, places=6)
        self.assertEqual(second["items"][0]["id"], mixed)
        self.assertAlmostEqual(second["items"][0]["matchScore"], 1.5)

    def test_semantic_cutoff_excludes_weak_and_invalid_matches(self):
        strong = self.chunk(spoken="Talk about finances", text_vector=vector(.8, .6, TEXT_EMBEDDING_DIM))
        for first, second in ((.6, .8), (.77, np.sqrt(1 - .77 ** 2)), (0, 0), (-1, 0)):
            self.chunk(spoken="Different discussion topics", text_vector=vector(first, second, TEXT_EMBEDDING_DIM))
        with patch.object(explorer_service, "query_embedding", return_value=vector(dimensions=TEXT_EMBEDDING_DIM)):
            page = self.get("conversations", q="budget")
        self.assertEqual([id for item in page["items"] for id in item["matchedChunkIds"]], [strong])

    def test_noise_is_excluded_even_for_exact_matches_but_retained_in_full_dialogue(self):
        recording = self.recording()
        noise = [self.chunk(recording_id=recording, chunk_index=index, spoken=spoken)
                 for index, spoken in enumerate(("yes", "banana okay", ""))]
        semantic = self.chunk(recording_id=recording, chunk_index=3, spoken="Discuss the fruit")
        keyword = self.chunk(recording_id=recording, chunk_index=4, spoken="BANANA bread banana",
                             text_vector=vector(.6, .8, TEXT_EMBEDDING_DIM))
        noise_only = self.recording()
        self.chunk(recording_id=noise_only, spoken="banana")
        with patch.object(explorer_service, "query_embedding", return_value=vector(dimensions=TEXT_EMBEDDING_DIM)):
            page = self.get("conversations", q="banana")
            detail = self.get(f"conversations/{recording}", q="banana")
            chunks = self.get("chunks", q="banana")
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["matchedChunkIds"], [semantic, keyword])
        self.assertAlmostEqual(page["items"][0]["matchScore"], 2.1, places=6)
        self.assertEqual([chunk["id"] for chunk in chunks["items"]], [keyword, semantic])
        self.assertEqual([chunk["id"] for chunk in detail["chunks"]], [*noise, semantic, keyword])
        self.assertEqual([chunk["matchType"] for chunk in detail["chunks"]], [None, None, None, "semantic", "keyword"])
        self.assertTrue(all(chunk["matchScore"] is None for chunk in detail["chunks"][:3]))
        self.assertAlmostEqual(detail["chunks"][-1]["matchScore"], 1.1, places=6)
        self.assertAlmostEqual(detail["matchScore"], page["items"][0]["matchScore"])
        self.assertEqual(self.get("conversations")["total"], 2)

    def test_keyword_match_with_unusable_text_vector_has_a_finite_bonus(self):
        chunk = self.chunk(spoken="Banana bread recipe", text_vector=vector(0, 0, TEXT_EMBEDDING_DIM))
        with patch.object(explorer_service, "query_embedding", return_value=vector(dimensions=TEXT_EMBEDDING_DIM)):
            page = self.get("conversations", q="banana")
            detail = self.get(f"conversations/{page['items'][0]['id']}", q="banana")
        self.assertEqual(page["items"][0]["matchedChunkIds"], [chunk])
        self.assertEqual(page["items"][0]["matchScore"], .5)
        self.assertEqual(detail["chunks"][0]["matchType"], "keyword")
        self.assertIsNone(detail["chunks"][0]["similarity"])

    @unittest.skipUnless(os.getenv("EXPLORER_RUN_MODEL_TESTS") == "1", "Opt in to real E5 model inference")
    def test_real_model_banana_search_excludes_unrelated_topics_and_ranks_density(self):
        from audio_processer import assign_text_embeddings

        texts = [
            "We bought a bunch of bananas at the market. The ripe bananas will go into banana bread.",
            "Mash the banana with a fork and fold it into the batter. Banana bread tastes sweeter with ripe bananas.",
            "I eat a banana every morning. Bananas are a convenient snack after exercise.",
            "Add one banana to the smoothie before blending.",
            "The software deployment is scheduled for Thursday. We need to finish testing the database migration.",
            "The train to Vienna leaves from platform four at eight tomorrow morning.",
            "We picked apples in the orchard and baked an apple pie.",
            "yes", "okay", "banana okay",
        ]
        chunks = [TranscriptionChunk(text=text) for text in texts]
        assign_text_embeddings(chunks)
        dense, brief = self.recording(), self.recording()
        for index, chunk in enumerate(chunks):
            recording = dense if index < 3 else brief if index == 3 else self.recording()
            self.chunk(recording_id=recording, chunk_index=index, spoken=chunk.text,
                       text_vector=chunk.text_embedding)
        explorer_service.query_embedding.cache_clear()
        self.addCleanup(explorer_service.query_embedding.cache_clear)
        page = self.get("conversations", q="banana")
        self.assertEqual([item["id"] for item in page["items"]], [dense, brief])
        self.assertEqual([len(item["matchedChunkIds"]) for item in page["items"]], [3, 1])
        self.assertGreater(page["items"][0]["matchScore"], page["items"][1]["matchScore"])
        detail = self.get(f"conversations/{dense}", q="banana")
        self.assertEqual(detail["matchedChunkIds"], page["items"][0]["matchedChunkIds"])

    def test_conversation_search_counts_matches_beyond_nearest_candidate_limit(self):
        recording = self.recording()
        with self.sessions.begin() as session:
            session.add_all([TranscriptionChunk(
                recording_id=recording, chunk_index=index, text="Banana bread recipe", word_count=3,
                start_ms=index * 1000, end_ms=(index + 1) * 1000,
                text_embedding=vector(dimensions=TEXT_EMBEDDING_DIM), voice_embedding=vector(),
            ) for index in range(explorer_service.SEARCH_CANDIDATES + 5)])
        with patch.object(explorer_service, "query_embedding", return_value=vector(dimensions=TEXT_EMBEDDING_DIM)):
            page = self.get("conversations", q="banana")
            detail = self.get(f"conversations/{recording}", q="banana")
        item = page["items"][0]
        self.assertEqual(len(item["matchedChunkIds"]), 205)
        self.assertEqual(item["matchedChunkIds"], detail["matchedChunkIds"])
        self.assertAlmostEqual(item["matchScore"], 307.5)
        self.assertFalse(page["approximate"])

    def test_cached_inference_vectors_work_across_database_search_paths(self):
        recording = self.recording()
        chunk = self.chunk(recording_id=recording, spoken="The annual budget")
        explorer_service.query_embedding.cache_clear()
        self.addCleanup(explorer_service.query_embedding.cache_clear)
        with patch("audio_processer.create_query_embedding", return_value=vector(dimensions=TEXT_EMBEDDING_DIM)) as inference:
            page = self.get("conversations", q="budget")
            self.assertEqual(page["items"][0]["matchedChunkIds"], [chunk])
            detail = self.get(f"conversations/{recording}", q="budget")
            self.assertEqual(detail["matchedChunkIds"], [chunk])
            self.assertEqual(self.get("chunks", q="budget")["items"][0]["id"], chunk)
            inference.assert_called_once_with("budget")

    def test_conversation_assignment_filters_matches_and_score_but_retains_context(self):
        recording = self.recording()
        owner = self.person()
        assigned = self.chunk(recording_id=recording, spoken="Work is complete", person_id=owner)
        self.chunk(recording_id=recording, chunk_index=1, spoken="Work is complete")
        self.chunk(spoken="1000 completeXunder")
        with patch.object(explorer_service, "query_embedding", return_value=vector(dimensions=TEXT_EMBEDDING_DIM)):
            page = self.get("conversations", q="completion", assignment="assigned")
            detail = self.get(f"conversations/{recording}", q="completion", assignment="assigned")
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["matchedChunkIds"], [assigned])
        self.assertAlmostEqual(page["items"][0]["matchScore"], 1)
        self.assertAlmostEqual(detail["matchScore"], 1)
        self.assertEqual(detail["chunkCount"], 2)
        self.assertEqual(detail["matchedChunkIds"], [assigned])

    def test_save_and_delete_preserve_chunk_vectors_and_recording_identity(self):
        recording = Recording(timestamp=123456789, chunks=[TranscriptionChunk(
            chunk_index=0, text="Budget", word_count=1, start_ms=0, end_ms=1000,
            text_embedding=vector(1, 0, TEXT_EMBEDDING_DIM), voice_embedding=vector(),
        ), TranscriptionChunk(
            chunk_index=1, text="Schedule", word_count=3, start_ms=1000, end_ms=2000,
            text_embedding=vector(0, 1, TEXT_EMBEDDING_DIM), voice_embedding=vector(),
        )])
        db_interaction.save_recording(recording)
        with self.sessions() as session:
            stored = session.scalar(select(Recording).where(Recording.timestamp == 123456789))
            recording_id = stored.id
            ids = [chunk.id for chunk in stored.chunks]
            self.assertFalse(hasattr(stored, "text_embedding"))
            np.testing.assert_allclose(stored.chunks[0].text_embedding, vector(1, 0, TEXT_EMBEDDING_DIM))
        self.assertEqual(self.client.delete(f"/api/explorer/chunks/{ids[0]}").status_code, 204)
        with self.sessions() as session:
            remaining = session.get(Recording, recording_id).chunks
            self.assertEqual(len(remaining), 1)
            np.testing.assert_allclose(remaining[0].text_embedding, vector(0, 1, TEXT_EMBEDDING_DIM))
        self.assertEqual(self.client.delete(f"/api/explorer/chunks/{ids[1]}").status_code, 204)
        with self.sessions() as session:
            self.assertEqual(session.get(Recording, recording_id).chunks, [])

    def test_conversation_search_errors_are_bounded_and_not_cached(self):
        for params in ({"assignment": "invalid"}, {"limit": 101}, {"offset": -1}):
            self.assertEqual(self.client.get("/api/explorer/conversations", params=params).status_code, 422)
        self.assertEqual(self.client.get("/api/explorer/conversations/99999").status_code, 404)
        with patch.object(explorer_service, "query_embedding", side_effect=explorer_service.ExplorerError(503, "Model unavailable")):
            response = self.client.get("/api/explorer/conversations", params={"q": "budget"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_conversation_api_exposes_only_unified_search_parameters(self):
        paths = self.client.get("/openapi.json").json()["paths"]
        for endpoint in ("/api/explorer/conversations", "/api/explorer/conversations/{recording_id}", "/api/explorer/chunks"):
            parameters = {item["name"] for item in paths[endpoint]["get"]["parameters"]}
            self.assertNotIn("mode", parameters)
            self.assertNotIn("min_similarity", parameters)
            self.assertIn("q", parameters)

    def test_fresh_postgres_initialization_creates_schema_and_indexes_and_can_repeat(self):
        if self.engine.dialect.name != "postgresql":
            self.skipTest("Requires EXPLORER_TEST_DATABASE_URL")
        from db import database

        schema = f"fresh_test_{uuid4().hex}"
        with self.engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        self.stack.callback(self.drop_schema, schema)
        fresh_engine = self.engine.execution_options(schema_translate_map={None: schema})
        sessions = sessionmaker(bind=fresh_engine)
        with patch.object(database, "engine", fresh_engine):
            database.initialize_database()
            with sessions.begin() as session:
                session.add(SetupState(id=1, expected_languages=["de"]))
                session.add(Recording(timestamp=123456789, language="de"))
            database.initialize_database()
        with fresh_engine.connect() as connection:
            inspector = inspect(connection)
            self.assertEqual(set(inspector.get_table_names(schema=schema)), set(Base.metadata.tables))
            self.assertEqual({column["name"] for column in inspector.get_columns("recording", schema=schema)},
                             {"id", "timestamp", "language"})
            indexes = dict(connection.execute(text(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = :schema"
            ), {"schema": schema}).all())
            for name in ("ix_chunk_text_hnsw", "ix_person_voice_hnsw", "ix_chunk_voice_hnsw"):
                self.assertIn("USING hnsw", indexes[name])
                self.assertIn("vector_cosine_ops", indexes[name])
            self.assertIn("(recording_id, start_ms, chunk_index, id)", indexes["ix_chunk_recording_timeline"])
            self.assertIn("COLLATE \"C\"", indexes["ix_person_name_order"])
            self.assertIn("WHERE", indexes["ix_person_missing_voice_name"])
            self.assertIn("ix_transcription_word_chunk_id", indexes)
        with sessions() as session:
            self.assertEqual(session.get(SetupState, 1).expected_languages, ["de"])
            self.assertEqual(session.scalar(select(Recording.timestamp)), 123456789)

    def test_person_list_includes_empty_profiles_and_ranks_comparison(self):
        opposite = self.person("Opposite", vector(-1, 0))
        empty = self.person("Empty")
        same = self.person("Same", vector(5, 0))
        source = self.chunk(voice=vector(2, 0))
        people = self.get("persons")["items"]
        self.assertEqual({person["id"] for person in people}, {opposite, empty, same})
        people = self.get("persons", chunk_id=source)["items"]
        comparable = [person for person in people if person["similarity"] is not None]
        self.assertEqual([person["id"] for person in comparable], [same, opposite])
        self.assertAlmostEqual(comparable[0]["similarity"], 1, places=6)
        self.assertAlmostEqual(comparable[1]["similarity"], -1, places=6)
        empty_result = next(person for person in people if person["id"] == empty)
        self.assertFalse(empty_result["hasVoiceEmbedding"])
        self.assertIsNone(empty_result["similarity"])

    def test_person_chunks_include_recording_counts_and_all_pages(self):
        person_id = self.person("Known", vector())
        recording_id = self.recording()
        first = self.chunk(person_id=person_id, recording_id=recording_id, words=2)
        second = self.chunk(person_id=person_id, recording_id=recording_id, chunk_index=1, words=3)
        third = self.chunk(person_id=person_id, words=5)
        self.chunk()
        pages = [self.get(f"persons/{person_id}/chunks", limit=2, offset=offset) for offset in (0, 2)]
        self.assertEqual({chunk["id"] for page in pages for chunk in page["items"]}, {first, second, third})
        self.assertTrue(all(page["total"] == 3 for page in pages))
        self.assertTrue(all(chunk["similarity"] is not None for page in pages for chunk in page["items"]))
        person = self.get(f"persons/{person_id}")
        self.assertEqual((person["chunkCount"], person["recordingCount"], person["wordCount"]), (3, 2, 10))

    def test_speaker_pagination_ranks_all_profiles_and_filters_beyond_first_page(self):
        source = self.chunk()
        with self.sessions.begin() as session:
            session.add_all([Person(name=f"Other {index:03}", voice_embedding=vector(0, 1))
                             for index in range(explorer_service.SEARCH_CANDIDATES + 5)])
        closest = self.person("Closest", vector())
        missing = self.person("No voice")
        zero = self.person("Zero voice", vector(0, 0))
        first = self.get("persons", chunk_id=source, limit=25)
        self.assertEqual(first["total"], 208)
        self.assertEqual(first["items"][0]["id"], closest)
        self.assertTrue(first["hasMore"])
        last = self.get("persons", chunk_id=source, offset=200)
        self.assertFalse(last["hasMore"])
        self.assertEqual([person["id"] for person in last["items"][-2:]], [missing, zero])
        filtered = self.get("persons", chunk_id=source, q="Other 204")
        self.assertEqual([person["name"] for person in filtered["items"]], ["Other 204"])

    def test_similarity_ranks_candidates_and_distinguishes_scopes(self):
        owner, other_owner = self.person("Owner", vector()), self.person("Other", vector())
        source = self.chunk(voice=vector(2, 0), person_id=owner)
        known = self.chunk(voice=vector(1, 0), person_id=owner)
        other = self.chunk(voice=vector(3, 0), person_id=other_owner)
        unassigned = self.chunk(voice=vector(1, 1))
        farthest = self.chunk(voice=vector(-1, 0))
        self.chunk(voice=vector(0, 0))
        page = self.get("similar", chunk_id=source)
        self.assertEqual([item["id"] for item in page["items"]], [other, unassigned, farthest])
        self.assertEqual(page["total"], 3)
        self.assertAlmostEqual(page["items"][0]["similarity"], 1, places=6)
        self.assertAlmostEqual(page["items"][1]["similarity"], 1 / np.sqrt(2), places=6)
        page = self.get("similar", chunk_id=source, scope="unassigned")
        self.assertEqual([item["id"] for item in page["items"]], [unassigned, farthest])
        page = self.get("similar", chunk_id=source, scope="all")
        self.assertEqual({item["id"] for item in page["items"]}, {known, other, unassigned, farthest})
        page = self.get("similar", person_id=owner, limit=1, offset=1)
        self.assertEqual([item["id"] for item in page["items"]], [unassigned])
        self.assertEqual((page["total"], page["offset"]), (3, 1))

    def test_unassigned_source_includes_owned_candidates(self):
        person_id = self.person("Known", vector())
        source = self.chunk()
        known = self.chunk(person_id=person_id)
        page = self.get("similar", chunk_id=source)
        self.assertEqual([item["id"] for item in page["items"]], [known])
        self.assertEqual(self.get("similar", chunk_id=source, scope="unassigned")["items"], [])

    def test_missing_resources_and_invalid_requests_do_not_mutate(self):
        person_id = self.person()
        source = self.chunk()
        for path in ("chunks/99999", "persons/99999", "persons/99999/chunks"):
            self.assertEqual(self.client.get(f"/api/explorer/{path}").status_code, 404)
        response = self.client.get("/api/explorer/similar", params={"person_id": person_id})
        self.assertEqual(response.status_code, 409, response.text)
        for params in ({}, {"chunk_id": source, "person_id": person_id},
                       {"chunk_id": source, "scope": "unknown"}):
            response = self.client.get("/api/explorer/similar", params=params)
            self.assertEqual(response.status_code, 422, response.text)
        for params in ({"assignment": "unknown"}, {"limit": 0}, {"offset": -1}):
            self.assertEqual(self.client.get("/api/explorer/chunks", params=params).status_code, 422)
        response = self.client.put(f"/api/explorer/chunks/{source}/person", json={"personId": 99999})
        self.assertEqual(response.status_code, 404, response.text)
        response = self.client.post("/api/explorer/persons", json={"name": "Missing", "chunkId": 99999})
        self.assertEqual(response.status_code, 404, response.text)
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Person)), 1)
            self.assertIsNone(session.get(TranscriptionChunk, source).person_id)


if __name__ == "__main__":
    unittest.main()
