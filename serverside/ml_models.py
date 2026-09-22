"""Lazy, process-wide model ownership. Only the audio worker uses speech models."""

from contextlib import contextmanager
import ctypes
import gc
import logging
import math
import os
from threading import RLock, Timer

import torch


logger = logging.getLogger(__name__)
device = os.getenv("WHISPERX_DEVICE") or ("cuda:0" if torch.cuda.is_available() else "cpu")


class RecordingSkipped(ValueError):
    """Audio cannot produce usable, language-appropriate transcription words."""


def release_unused_memory():
    """Release collectible tensors and, where supported, free allocator pages."""
    gc.collect()
    if torch.cuda.is_initialized():
        torch.cuda.empty_cache()
    try:
        trim = ctypes.CDLL(None).malloc_trim
    except (AttributeError, OSError):
        return
    trim.argtypes = [ctypes.c_size_t]
    trim.restype = ctypes.c_int
    trim(0)


class ModelRegistry:
    def __init__(self, idle_seconds=None):
        self._idle_seconds = float(
            os.getenv("VOXVAULT_MODEL_IDLE_SECONDS", "30") if idle_seconds is None else idle_seconds
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
            # A canceled timer may already be waiting for the lock.
            if self._active_sessions or generation != self._generation:
                return
            self._idle_timer = None
            loaded = any(model is not None for model in (
                self._transcription, self._diarization, self._speaker, self._text,
            )) or bool(self.alignment_models)
            self._transcription = None
            self._diarization = None
            self._speaker = None
            self._text = None
            self.alignment_models.clear()
            if loaded:
                try:
                    release_unused_memory()
                except Exception:
                    logger.exception("Could not reclaim all unused model memory")
                logger.info("Released idle inference models; they will reload on the next request")

    def transcription(self):
        with self._lock:
            if self._transcription is None:
                import whisperx

                name = os.getenv("WHISPERX_MODEL", "small")
                target = torch.device(device)
                logger.info("Initializing WhisperX transcription and VAD models: %s", name)
                self._transcription = whisperx.load_model(
                    name, target.type, device_index=target.index or 0,
                    compute_type=os.getenv(
                        "WHISPERX_COMPUTE_TYPE", "float16" if target.type == "cuda" else "int8",
                    ),
                    # Each job supplies its selected language explicitly.
                    language=None,
                    threads=int(os.getenv("WHISPERX_CPU_THREADS", "4")),
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
        token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
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
