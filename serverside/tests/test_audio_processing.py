from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

import numpy as np
import whisperx

import audio_processer as audio
import ml_models
from ml_models import ModelRegistry, RecordingSkipped
from processing_queue import ProcessingQueue


class AudioProcessingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.registry = ModelRegistry()
        self.stack.enter_context(patch.object(audio, "models", self.registry))
        self.languages = self.stack.enter_context(patch.object(
            audio.setup_service, "get_expected_languages", return_value=("en",),
        ))
        self.model = Mock()
        self.vad = self.model.vad_model
        self.model._vad_params = {"vad_onset": .5, "vad_offset": .363}
        self.vad.merge_chunks.return_value = [
            {"start": 0.1, "end": .4, "segments": [(.1, .4)]},
        ]
        self.model.model.detect_language.return_value = ("de", .95, [])
        self.model.transcribe.return_value = {
            "language": "en", "segments": [{"text": "hello", "start": .1, "end": .4}],
        }
        self.loader = self.stack.enter_context(patch.object(whisperx, "load_model", return_value=self.model))
        self.align_loader = self.stack.enter_context(patch.object(
            whisperx, "load_align_model", return_value=(Mock(), {"language": "en"}),
        ))
        self.align = self.stack.enter_context(patch.object(whisperx, "align", side_effect=lambda *a, **kw: {
            "segments": [{"words": [{"word": "hello", "start": .1, "end": .4, "score": .99}]}],
        }))
        self.diarization = Mock()
        self.diarization_loader = self.stack.enter_context(patch(
            "whisperx.diarize.DiarizationPipeline", return_value=self.diarization,
        ))
        self.stack.enter_context(patch.object(whisperx, "assign_word_speakers", side_effect=lambda d, r: r))
        self.speaker = Mock()
        self.speaker.encode_batch.return_value = audio.torch.ones((1, 1, 192))
        self.speaker_loader = self.stack.enter_context(patch(
            "speechbrain.inference.speaker.EncoderClassifier.from_hparams", return_value=self.speaker,
        ))
        self.text = Mock()
        self.text.encode.side_effect = lambda values, **kw: np.ones((len(values), 384))
        self.text_loader = self.stack.enter_context(patch(
            "sentence_transformers.SentenceTransformer", return_value=self.text,
        ))
        self.stack.enter_context(patch.object(ml_models.settings, "HUGGINGFACE_TOKEN", "test"))
        self.stack.enter_context(patch.object(audio.settings, "WHISPERX_LANGUAGE_MIN_CONFIDENCE", 0.7))
        self.save = self.stack.enter_context(patch.object(audio.db_interaction, "save_recording"))
        self.pcm = np.ones(8000, dtype="<i2").tobytes()

    def process(self, timestamp=1):
        return audio.process_audio_bytes(self.pcm, timestamp)

    def assert_no_downstream(self):
        self.align_loader.assert_not_called()
        self.align.assert_not_called()
        self.diarization_loader.assert_not_called()
        self.speaker_loader.assert_not_called()
        self.text_loader.assert_not_called()
        self.save.assert_not_called()

    def test_single_language_bypasses_detection_and_keeps_short_speech(self):
        self.languages.return_value = ("de",)
        result = self.process()
        self.assertEqual((result.language, len(result.words)), ("de", 1))
        self.model.model.detect_language.assert_not_called()
        self.model.detect_language.assert_not_called()
        self.assertEqual(self.model.transcribe.call_args.kwargs["language"], "de")
        self.assertEqual(self.align_loader.call_args.kwargs["language_code"], "de")
        self.save.assert_called_once()

    def test_multiple_languages_accept_valid_detection_and_preserve_language(self):
        self.languages.return_value = ("en", "de")
        result = self.process()
        self.assertEqual(result.language, "de")
        self.assertEqual(self.model.transcribe.call_args.kwargs["language"], "de")
        detected_audio = self.model.model.detect_language.call_args.kwargs["audio"]
        self.assertEqual(len(detected_audio), 4800)
        self.assertEqual(self.save.call_args.args[0].language, "de")

    def test_language_sample_duration_is_independent_of_idle_cleanup(self):
        waveform = np.ones(audio.SAMPLE_RATE * 10)
        chunks = [{"segments": [(0, 10)]}]
        with patch.object(audio.settings, "VOXVAULT_MODEL_IDLE_SECONDS", 0), patch.object(
            audio.settings, "WHISPERX_LANGUAGE_SAMPLE_SECONDS", 3,
        ):
            language = audio.select_language(self.model, waveform, chunks, ("en", "de"))
        self.assertEqual(language, "de")
        self.assertEqual(len(self.model.model.detect_language.call_args.kwargs["audio"]), audio.SAMPLE_RATE * 3)

    def test_enrollment_uses_configured_language_and_skips_diarization(self):
        self.languages.return_value = ("de",)
        result = audio.transcribe_with_whisperx(self.pcm, lambda *args: None, enrollment=True)
        self.assertEqual(result["language"], "de")
        self.model.model.detect_language.assert_not_called()
        self.diarization_loader.assert_not_called()
        self.assertEqual(self.align_loader.call_args.kwargs["language_code"], "de")

    def test_unexpected_detection_never_reaches_alignment(self):
        self.languages.return_value = ("en", "de")
        for language in ("jw", "nn"):
            with self.subTest(language=language):
                self.model.model.detect_language.return_value = (language, .98, [])
                with self.assertRaisesRegex(RecordingSkipped, "unexpected"):
                    self.process()
        self.model.transcribe.assert_not_called()
        self.assert_no_downstream()

    def test_expected_language_bypasses_reliability_filter(self):
        self.languages.return_value = ("en", "de")
        for confidence in (.1, None, float("nan"), float("inf")):
            with self.subTest(confidence=confidence):
                self.model.model.detect_language.return_value = ("en", confidence, [])
                result = self.process()
                self.assertEqual(result.language, "en")

    def test_unexpected_low_or_missing_confidence_skips_as_unreliable(self):
        self.languages.return_value = ("en", "de")
        for confidence in (.1, None, float("nan"), float("inf")):
            with self.subTest(confidence=confidence):
                self.model.model.detect_language.return_value = ("jw", confidence, [])
                with self.assertRaisesRegex(RecordingSkipped, "unreliable"):
                    self.process()
        self.model.transcribe.assert_not_called()
        self.assert_no_downstream()

    def test_no_speech_exits_before_detection_transcription_or_downstream_models(self):
        self.languages.return_value = ("en", "de")
        self.vad.merge_chunks.return_value = []
        with self.assertRaisesRegex(RecordingSkipped, "no speech"):
            self.process()
        self.model.model.detect_language.assert_not_called()
        self.model.transcribe.assert_not_called()
        self.assert_no_downstream()

    def test_empty_transcript_does_not_align_or_save(self):
        self.model.transcribe.return_value = {"segments": []}
        with self.assertRaisesRegex(RecordingSkipped, "no usable transcription"):
            self.process()
        self.assert_no_downstream()

    def test_unaligned_or_empty_words_do_not_diarize_embed_or_save(self):
        self.align.side_effect = None
        self.align.return_value = {"segments": [{"words": [{"word": "hello"}]}]}
        with self.assertRaisesRegex(RecordingSkipped, "no usable aligned words"):
            self.process()
        self.diarization_loader.assert_not_called()
        self.speaker_loader.assert_not_called()
        self.text_loader.assert_not_called()
        self.save.assert_not_called()

    def test_accepted_language_without_alignment_is_skipped(self):
        self.languages.return_value = ("jw",)
        with self.assertRaisesRegex(RecordingSkipped, "no alignment model"):
            self.process()
        self.assert_no_downstream()

    def test_models_are_loaded_once_for_successive_recordings(self):
        for timestamp in range(3):
            self.process(timestamp)
        for loader in (self.loader, self.align_loader, self.diarization_loader,
                       self.speaker_loader, self.text_loader):
            loader.assert_called_once()
        self.assertEqual(self.diarization.call_count, 3)
        self.assertEqual(self.speaker.encode_batch.call_count, 3)
        self.assertEqual(self.save.call_count, 3)
        self.assertEqual(self.vad.call_count, 3)
        self.assertIs(self.model.vad_model, self.vad)
        self.assertIsNone(self.model.tokenizer)

    def test_asr_failure_restores_vad_and_next_recording_processes(self):
        self.model.transcribe.side_effect = RuntimeError("inference failed")
        with self.assertRaises(RuntimeError):
            self.process()
        self.assertIs(self.model.vad_model, self.vad)
        self.model.transcribe.side_effect = None
        self.process()
        self.loader.assert_called_once()

    def test_concurrent_text_model_initialization_loads_once(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.registry.text(), range(12)))
        self.assertTrue(all(result is self.text for result in results))
        self.text_loader.assert_called_once()

    def test_idle_cleanup_unloads_and_measures_each_model_category(self):
        registry = ModelRegistry()
        transcription = Mock()
        ctranslate_model = transcription.model.model
        ctranslate_model.unload_model.side_effect = lambda: self.assertIs(
            registry._transcription, transcription,
        )
        registry._transcription = transcription
        registry.alignment_models["en"] = (Mock(), {})
        registry._diarization = Mock()
        registry._speaker = Mock()
        registry._text = Mock()

        with patch.object(ml_models, "release_unused_memory") as reclaim, patch.object(
            ml_models, "current_rss_mib", side_effect=[2500, 1800, 1400, 1000, 800, 700],
        ) as rss, self.assertLogs("ml_models", level="INFO") as logs:
            registry._release_idle(0)

        ctranslate_model.unload_model.assert_called_once_with()
        self.assertIsNone(registry._transcription)
        self.assertEqual(registry.alignment_models, {})
        self.assertIsNone(registry._diarization)
        self.assertIsNone(registry._speaker)
        self.assertIsNone(registry._text)
        self.assertEqual(reclaim.call_count, 5)
        self.assertEqual(rss.call_count, 6)
        output = "\n".join(logs.output)
        self.assertIn("active_sessions=0, timer_generation=0, current_generation=0", output)
        for category in (
            "transcription/VAD", "alignment models", "diarization",
            "speaker embedding model", "text embedding model",
        ):
            self.assertIn(f"Released {category}; current_rss=", output)

    def test_prepared_vad_uses_installed_whisperx_transcribe_without_another_vad_pass(self):
        from whisperx.asr import FasterWhisperPipeline

        class Pipeline:
            transcribe = FasterWhisperPipeline.transcribe

            def __call__(self, segments, **kwargs):
                for segment in segments:
                    yield {"text": "hello", "avg_logprob": -.1}

        pipeline = Pipeline()
        pipeline.vad_model = self.vad
        pipeline._vad_params = self.model._vad_params
        pipeline.model = self.model.model
        pipeline.tokenizer = None
        pipeline.preset_language = None
        pipeline.suppress_numerals = False
        pipeline.detect_language = Mock(side_effect=AssertionError("automatic detection"))
        waveform = audio.pcm_bytes_to_float32_mono(self.pcm)
        chunks = audio.speech_segments(pipeline, waveform)
        with patch("whisperx.asr.Tokenizer"):
            result = audio.transcribe_speech(pipeline, waveform, chunks, "de")
        self.assertEqual(result["language"], "de")
        self.assertEqual(result["segments"][0]["text"], "hello")
        self.vad.assert_called_once()
        self.assertIs(pipeline.vad_model, self.vad)


class ProcessingQueueTests(unittest.TestCase):
    def test_sequential_jobs_failure_isolation_disk_spool_and_draining_shutdown(self):
        started, release = Event(), Event()
        finished = []
        order = []
        active = 0
        peak = 0

        def process(job):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            order.append(job.timestamp)
            try:
                self.assertEqual(job.path.read_bytes(), b"audio")
                if job.timestamp == 1:
                    started.set()
                    if not release.wait(5):
                        raise RuntimeError("Test did not release job")
                    raise RuntimeError("bad recording")
            finally:
                active -= 1

        with TemporaryDirectory() as directory, patch.object(audio.settings, "VOXVAULT_PROCESSING_DIR", Path(directory)):
            worker = ProcessingQueue(process, finished.append)
            worker.start()
            try:
                for timestamp in range(1, 6):
                    worker.enqueue(b"audio", timestamp, False)
                self.assertTrue(started.wait(5))
                self.assertEqual(order, [1])
                self.assertEqual(len(list(Path(directory).glob("*/*.pcm"))), 5)
            finally:
                release.set()
                worker.close()
            self.assertEqual(order, [1, 2, 3, 4, 5])
            self.assertEqual(finished, order)
            self.assertEqual(peak, 1)
            self.assertFalse(worker._thread.is_alive())
            self.assertEqual(list(Path(directory).iterdir()), [])
            with self.assertRaisesRegex(RuntimeError, "shutting down"):
                worker.enqueue(b"audio", 6, False)


if __name__ == "__main__":
    unittest.main()
