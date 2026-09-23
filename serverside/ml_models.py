"""Lazy, process-wide model ownership. Only the audio worker uses speech models."""

from contextlib import contextmanager
import ctypes
import gc
import logging
import math
import os
from threading import RLock, Timer
from config import settings

logger = logging.getLogger(__name__)
device = settings.WHISPERX_DEVICE


class RecordingSkipped(ValueError):
    """Audio cannot produce usable, language-appropriate transcription words."""


def release_unused_memory():
    """Release collectible tensors and, where supported, free allocator pages."""
    gc.collect()
    try:
        trim = ctypes.CDLL(None).malloc_trim
    except (AttributeError, OSError):
        return
    trim.argtypes = [ctypes.c_size_t]
    trim.restype = ctypes.c_int
    trim(0)


def current_rss_mib():
    """Return the process's current resident memory, rather than its peak."""
    try:
        with open("/proc/self/statm", encoding="ascii") as statm:
            resident_pages = int(statm.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / 1024 ** 2
    except (IndexError, OSError, ValueError):
        return float("nan")


class ModelRegistry:
    def __init__(self, idle_seconds=None):
        self._idle_seconds = float(
            settings.VOXVAULT_MODEL_IDLE_SECONDS if idle_seconds is None else idle_seconds
        )
        if not math.isfinite(self._idle_seconds) or self._idle_seconds < 0:
            raise ValueError("VOXVAULT_MODEL_IDLE_SECONDS must be a finite nonnegative number")
        self._lock = RLock()
        self.text_inference_lock = RLock()
        self._active_sessions = 0
        self._generation = 0
        self._idle_timer = None
        self._transcription = None
        self._diarization = None
        self._speaker = None
        self._text = None
        self.alignment_models = {}

    @contextmanager
    def session(self):
        """Keep models alive until all concurrent and nested inference finishes."""
        with self._lock:
            self._active_sessions += 1
            self._generation += 1
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
        try:
            yield
        finally:
            with self._lock:
                self._active_sessions -= 1
                if self._active_sessions == 0:
                    self._idle_timer = Timer(
                        self._idle_seconds, self._release_idle, args=(self._generation,),
                    )
                    self._idle_timer.daemon = True
                    self._idle_timer.start()

    def _release_idle(self, generation):
        with self._lock:
            logger.info(
                "Idle model cleanup fired: active_sessions=%d, timer_generation=%d, "
                "current_generation=%d, current_rss=%.1f MiB",
                self._active_sessions, generation, self._generation, current_rss_mib(),
            )
            # A canceled timer may already be waiting for the lock.
            if self._active_sessions or generation != self._generation:
                logger.info(
                    "Skipping stale or busy idle model cleanup: active_sessions=%d, "
                    "timer_generation=%d, current_generation=%d",
                    self._active_sessions, generation, self._generation,
                )
                return
            self._idle_timer = None
            loaded = any(model is not None for model in (
                self._transcription, self._diarization, self._speaker, self._text,
            )) or bool(self.alignment_models)

            if self._transcription is not None:
                ctranslate_model = getattr(
                    getattr(self._transcription, "model", None), "model", None,
                )
                unload_model = getattr(ctranslate_model, "unload_model", None)
                if callable(unload_model):
                    try:
                        unload_model()
                    except Exception:
                        logger.exception("Could not explicitly unload the CTranslate2 Whisper model")
            self._transcription = None
            ctranslate_model = None
            unload_model = None
            self._reclaim_and_log("transcription/VAD")

            self.alignment_models.clear()
            self._reclaim_and_log("alignment models")

            self._diarization = None
            self._reclaim_and_log("diarization")

            self._speaker = None
            self._reclaim_and_log("speaker embedding model")

            self._text = None
            self._reclaim_and_log("text embedding model")

            if loaded:
                logger.info("Released idle inference models; they will reload on the next request")

    @staticmethod
    def _reclaim_and_log(category):
        try:
            release_unused_memory()
        except Exception:
            logger.exception("Could not reclaim all unused memory after releasing %s", category)
        logger.info("Released %s; current_rss=%.1f MiB", category, current_rss_mib())

    def transcription(self):
        with self._lock:
            if self._transcription is None:
                import whisperx

                name = settings.WHISPERX_MODEL
                logger.info("Initializing WhisperX transcription and VAD models: %s", name)
                self._transcription = whisperx.load_model(
                    name, device,
                    compute_type=settings.WHISPERX_COMPUTE_TYPE,
                    # Each job supplies its selected language explicitly.
                    language=None,
                    threads=settings.WHISPERX_CPU_THREADS,
                )
            return self._transcription

    def restrict_alignment_languages(self, languages):
        with self._lock:
            for language in list(self.alignment_models):
                if language not in languages:
                    del self.alignment_models[language]

    def alignment(self, language, expected_languages):
        if language not in expected_languages:
            raise RecordingSkipped(f"unexpected language: {language}")
        with self._lock:
            if language not in self.alignment_models:
                import whisperx
                from whisperx.alignment import DEFAULT_ALIGN_MODELS_HF, DEFAULT_ALIGN_MODELS_TORCH

                if language not in DEFAULT_ALIGN_MODELS_HF and language not in DEFAULT_ALIGN_MODELS_TORCH:
                    raise RecordingSkipped(f"no alignment model available for language: {language}")
                # Only the audio worker aligns; retaining other languages wastes RAM.
                self.alignment_models.clear()
                logger.info("Initializing alignment model: %s", language)
                model, metadata = whisperx.load_align_model(
                    language_code=language, device=device,
                )
                model.eval()
                self.alignment_models[language] = model, metadata
            return self.alignment_models[language]

    def diarization(self):
        token = settings.HUGGINGFACE_TOKEN
        if not token:
            return None
        with self._lock:
            if self._diarization is None:
                from whisperx.diarize import DiarizationPipeline

                logger.info("Initializing Pyannote diarization model")
                self._diarization = DiarizationPipeline(token=token, device=device)
            return self._diarization

    def speaker(self):
        with self._lock:
            if self._speaker is None:
                from speechbrain.inference.speaker import EncoderClassifier

                logger.info("Initializing speaker embedding model: speechbrain/spkrec-ecapa-voxceleb")
                self._speaker = EncoderClassifier.from_hparams(
                    source="speechbrain/spkrec-ecapa-voxceleb",
                    savedir="models/spkrec-ecapa-voxceleb",
                    run_opts={"device": device},
                )
            return self._speaker

    def text(self):
        with self._lock:
            if self._text is None:
                from sentence_transformers import SentenceTransformer

                logger.info("Initializing text embedding model: intfloat/multilingual-e5-small")
                self._text = SentenceTransformer("intfloat/multilingual-e5-small", device=device)
            return self._text


models = ModelRegistry()
