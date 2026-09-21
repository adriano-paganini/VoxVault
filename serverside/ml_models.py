"""Lazy, process-wide model ownership. Only the audio worker uses speech models."""

import logging
import os
from threading import RLock

import torch


logger = logging.getLogger(__name__)
device = os.getenv("WHISPERX_DEVICE") or ("cuda:0" if torch.cuda.is_available() else "cpu")


class RecordingSkipped(ValueError):
    """Audio cannot produce usable, language-appropriate transcription words."""


class ModelRegistry:
    def __init__(self):
        self._lock = RLock()
        self.text_inference_lock = RLock()
        self._transcription = None
        self._diarization = None
        self._speaker = None
        self._text = None
        self.alignment_models = {}

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
