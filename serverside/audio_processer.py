from config import settings
import logging
import math
from dataclasses import asdict, dataclass
from functools import wraps
import torch
import db_interaction
import setup_service
from ml_models import RecordingSkipped, device, models

import db.models

logger = logging.getLogger(__name__)


def using_models(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with models.session():
            return function(*args, **kwargs)
    return wrapped


def get_speaker_model():
    return models.speaker()


def get_text_embedding_model():
    return models.text()

SAMPLE_RATE = 16000
CHANNELS = 1
MAX_WORDS_PER_CHUNK = 250


class EnrollmentError(ValueError):
    pass


@dataclass
class TranscriptWord:
    word: str
    start_ms: int | None
    end_ms: int | None
    speaker_label: str | None
    confidence: float | None

@dataclass
class AudioProcessingResult:
    timestamp: int
    language: str | None
    words: list[TranscriptWord]

    def to_dict(self):
        return {
            "timestamp": self.timestamp,
            "language": self.language,
            "words": [asdict(word) for word in self.words],
        }

def pcm_bytes_to_float32_mono(audio: bytes):
    import numpy as np

    samples = np.frombuffer(audio, dtype="<i2")
    waveform = samples.astype("float32")
    waveform *= 1.0 / 32768.0
    return waveform

def speech_segments(model, waveform):
    vad = model.vad_model
    segments = vad({"waveform": vad.preprocess_audio(waveform), "sample_rate": SAMPLE_RATE})
    chunks = vad.merge_chunks(
        segments, 30,
        onset=model._vad_params["vad_onset"], offset=model._vad_params["vad_offset"],
    )
    chunks = [chunk for chunk in chunks if chunk["end"] > chunk["start"]]
    if not chunks:
        raise RecordingSkipped("no speech detected")
    return chunks


def select_language(model, waveform, chunks, expected_languages):
    if len(expected_languages) == 1:
        language = expected_languages[0]
        logger.info("Using configured language: %s", language)
        return language
    if not expected_languages:
        raise RecordingSkipped("no expected languages configured")

    import numpy as np

    # Detect on up to 30 seconds of VAD speech, excluding leading silence and gaps.
    remaining = int(SAMPLE_RATE * settings.WHISPERX_LANGUAGE_SAMPLE_SECONDS)
    speech = []
    for chunk in chunks:
        for start, end in chunk["segments"]:
            samples = waveform[max(0, int(start * SAMPLE_RATE)):int(end * SAMPLE_RATE)][:remaining]
            if len(samples):
                speech.append(samples)
                remaining -= len(samples)
            if remaining == 0:
                break
        if remaining == 0:
            break
    if not speech:
        raise RecordingSkipped("no speech detected")
    # Faster Whisper exposes probability; WhisperX's wrapper drops it.
    language, probability, _ = model.model.detect_language(audio=np.concatenate(speech))
    if language in expected_languages:
        logger.info(
            "Detected configured language: %s (confidence=%s, accepted)", language, probability,
        )
        return language

    threshold = settings.WHISPERX_LANGUAGE_MIN_CONFIDENCE
    if not 0 <= threshold <= 1:
        raise ValueError("WHISPERX_LANGUAGE_MIN_CONFIDENCE must be between 0 and 1")
    if probability is None or not math.isfinite(probability) or not threshold <= probability <= 1:
        raise RecordingSkipped(f"unreliable language detection: {language} (confidence={probability})")
    raise RecordingSkipped(f"unexpected detected language: {language}")


def transcribe_speech(model, waveform, chunks, language):
    from whisperx.vads.vad import Vad

    class PreparedVad(Vad):
        """Feed the completed VAD pass back into WhisperX without repeating inference."""

        def __init__(self):
            pass

        @staticmethod
        def preprocess_audio(audio):
            return None

        def __call__(self, audio):
            return chunks

        @staticmethod
        def merge_chunks(segments, *args, **kwargs):
            return segments

    original_vad = model.vad_model
    model.vad_model = PreparedVad()
    try:
        return model.transcribe(
            waveform, batch_size=settings.WHISPERX_BATCH_SIZE, language=language,
        )
    finally:
        # Never leave a per-recording VAD adapter or tokenizer on the shared pipeline.
        model.vad_model = original_vad
        model.tokenizer = None


@using_models
@torch.inference_mode()
def transcribe_with_whisperx(audio: bytes, progress, enrollment=False) -> dict:
    import whisperx

    expected_languages = setup_service.get_expected_languages()
    models.restrict_alignment_languages(expected_languages)
    waveform = pcm_bytes_to_float32_mono(audio)

    progress("transcribing", "Loading the speech model and transcribing your recording.")
    model = models.transcription()
    chunks = speech_segments(model, waveform)
    language = select_language(model, waveform, chunks, expected_languages)
    if language != "en" and not model.model.model.is_multilingual:
        raise RecordingSkipped("the configured Whisper model supports only English")
    result = transcribe_speech(model, waveform, chunks, language)
    segments = [segment for segment in result.get("segments", []) if segment.get("text", "").strip()]
    if not segments:
        raise RecordingSkipped("no usable transcription after speech detection")

    progress("aligning", "Matching the spoken words to the audio.")
    align_model, metadata = models.alignment(language, expected_languages)
    result = whisperx.align(
        segments,
        align_model,
        metadata,
        waveform,
        device,
        return_char_alignments=False,
    )
    result["language"] = language
    if not any(
        word.start_ms is not None and word.end_ms is not None
        and 0 <= word.start_ms < word.end_ms <= len(waveform) * 1000 / SAMPLE_RATE
        and any(character.isalnum() for character in word.word)
        for word in result_to_words(result)
    ):
        raise RecordingSkipped("no usable aligned words")

    if not enrollment:
        diarize_model = models.diarization()
        if diarize_model is not None:
            diarize_segments = diarize_model(waveform)
            result = whisperx.assign_word_speakers(diarize_segments, result)

    return result

def result_to_words(result: dict) -> list[TranscriptWord]:
    words = []

    for segment in result.get("segments", []):
        for word in segment.get("words", []):
            if not word.get("word", "").strip():
                continue
            start = word.get("start")
            end = word.get("end")

            words.append(
                TranscriptWord(
                    word=word.get("word", ""),
                    start_ms=round(start * 1000) if start is not None else None,
                    end_ms=round(end * 1000) if end is not None else None,
                    speaker_label=word.get("speaker") or segment.get("speaker"),
                    confidence=word.get("score"),
                )
            )

    return words

@using_models
def process_audio_bytes(audio: bytes, timestamp: int, progress=None, enrollment=False) -> AudioProcessingResult:
    progress = progress or (lambda stage, message: None)
    progress("validating", "Checking the recording before processing.")
    if not audio or len(audio) % 2:
        raise EnrollmentError("The recording contains invalid audio. Please record the passage again.")
    if enrollment and not 30 <= len(audio) / (SAMPLE_RATE * CHANNELS * 2) <= 180:
        raise EnrollmentError("Please read the full passage in one recording lasting 30 seconds to 3 minutes.")

    result = transcribe_with_whisperx(audio, progress, enrollment=enrollment)
    processed = AudioProcessingResult(
        timestamp=timestamp,
        language=result.get("language"),
        words=result_to_words(result),
    )

    chunk_audio, chunk_object, word_object = extract_chunks(audio, processed)
    del result, word_object
    if not chunk_object or not any(character.isalnum() for chunk in chunk_object for character in chunk.text):
        raise RecordingSkipped("no usable aligned words")

    if enrollment and sum(chunk.word_count for chunk in chunk_object) < 50:
        raise EnrollmentError("Too little clear speech was detected. Read the full passage again in a quiet room.")

    progress("text_embedding", "Creating the transcript embeddings.")
    assign_text_embeddings(chunk_object)
    progress("voice_embedding", "Loading the voice model and creating your voice embedding.")
    assign_audio_embeddings(zip(chunk_object,chunk_audio))

    full_recording = db.models.Recording(timestamp =timestamp,
                                         language = processed.language,
                                         chunks = chunk_object)

    for chunk in chunk_object:
        chunk.recording = full_recording


    person = None
    if enrollment:
        from explorer_service import weighted_voice_embedding

        embedding, word_count = weighted_voice_embedding(chunk_object)
        if embedding is None:
            raise EnrollmentError("A voice profile could not be created. Please record the passage again.")
        person = db.models.Person(
            name="Me",
            voice_embedding=embedding,
            voice_embedding_word_count=word_count,
        )
        for chunk in chunk_object:
            chunk.person = person

    progress("saving", "Saving your recording and voice profile." if enrollment else "Saving your recording.")
    db_interaction.save_recording(full_recording, person=person)

    return processed

@using_models
def assign_audio_embeddings(chunk_data):
    for chunk, audio in chunk_data:
        waveform = pcm_bytes_to_float32_mono(audio)

        waveform_tensor = torch.from_numpy(waveform).unsqueeze(0).to(device)

        with torch.inference_mode():
            embedding = get_speaker_model().encode_batch(waveform_tensor)

        chunk.voice_embedding = (
            embedding.squeeze()
            .cpu()
            .numpy()
            .tolist()
        )

@using_models
def assign_text_embeddings(chunks:list[db.models.TranscriptionChunk]):
    if not chunks:
        return
    texts = [
        "passage: " + chunk.text for chunk in chunks
    ]
    with models.text_inference_lock, torch.inference_mode():
        embeddings = get_text_embedding_model().encode(
            texts,
            normalize_embeddings=True,
        )

    for chunk,embedding in zip(chunks, embeddings):
        chunk.text_embedding = embedding.tolist()

@using_models
def create_query_embedding(query: str) -> list[float]:
    with models.text_inference_lock, torch.inference_mode():
        embedding = get_text_embedding_model().encode(
            "query: " + query,
            normalize_embeddings=True,
        )

    return embedding.tolist()

def extract_chunks(audio: bytes, processed: AudioProcessingResult):
    words = [word for word in processed.words
             if word.start_ms is not None and word.end_ms is not None
             and 0 <= word.start_ms < word.end_ms <= len(audio) * 1000 / (SAMPLE_RATE * CHANNELS * 2)]

    if not words:
        return [], [], []

    chunks = []
    word_objects = []

    chunk_index = 0
    current_speaker = words[0].speaker_label

    current_chunk = db.models.TranscriptionChunk(
        chunk_index=chunk_index,
        start_ms=words[0].start_ms,
    )

    current_text = ""
    current_word_count = 0
    word_index = 0

    for word in words:

        # Speaker changed -> finish previous chunk first
        if word.speaker_label != current_speaker or current_word_count >= MAX_WORDS_PER_CHUNK:
            current_chunk.text = current_text
            current_chunk.word_count = current_word_count
            current_chunk.end_ms = previous_word.end_ms


            chunks.append(current_chunk)

            # Start new chunk
            chunk_index += 1
            current_speaker = word.speaker_label

            current_chunk = db.models.TranscriptionChunk(
                chunk_index=chunk_index,
                start_ms=word.start_ms,
            )

            current_text = ""
            current_word_count = 0

        # Add current word to current chunk
        current_text += " " + word.word
        current_word_count += 1

        word_object = db.models.TranscriptionWord(
            word_index=word_index,
            word=word.word,
            start_ms=word.start_ms,
            end_ms=word.end_ms,
            raw_speaker_label=word.speaker_label,
            confidence=word.confidence,
            chunk=current_chunk,
        )

        word_objects.append(word_object)

        word_index += 1
        previous_word = word

    # Finish final chunk
    current_chunk.text = current_text
    current_chunk.word_count = current_word_count
    current_chunk.end_ms = previous_word.end_ms

    chunks.append(current_chunk)

    # PCM16 = 2 bytes per sample
    bytes_per_ms = SAMPLE_RATE * CHANNELS * 2 / 1000

    chunk_audio = (
        memoryview(audio)[int(chunk.start_ms * bytes_per_ms):int(chunk.end_ms * bytes_per_ms)]
        for chunk in chunks
    )

    return chunk_audio, chunks, word_objects
