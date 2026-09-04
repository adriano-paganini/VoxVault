import csv
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import db.models

SAMPLE_RATE = 16000
CHANNELS = 1
DEBUG_AUDIO_DIR = Path(os.getenv("VOXVAULT_DEBUG_AUDIO_DIR", "debug_audio"))
DEBUG_TRANSCRIPT_DIR = Path(os.getenv("VOXVAULT_DEBUG_TRANSCRIPT_DIR", "debug_transcripts"))


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
    return samples.astype("float32") / 32768.0


def transcribe_with_whisperx(audio: bytes) -> dict:
    import torch
    import whisperx

    device = os.getenv("WHISPERX_DEVICE")
    if not device:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    model_name = os.getenv("WHISPERX_MODEL", "small")
    compute_type = os.getenv(
        "WHISPERX_COMPUTE_TYPE",
        "float16" if device == "cuda" else "int8",
    )
    batch_size = int(os.getenv("WHISPERX_BATCH_SIZE", "8"))

    waveform = pcm_bytes_to_float32_mono(audio)

    model = whisperx.load_model(model_name, device, compute_type=compute_type)
    result = model.transcribe(waveform, batch_size=batch_size)

    align_model, metadata = whisperx.load_align_model(
        language_code=result["language"],
        device=device,
    )
    result = whisperx.align(
        result["segments"],
        align_model,
        metadata,
        waveform,
        device,
        return_char_alignments=False,
    )

    hf_token = os.getenv("HUGGINGFACE_TOKEN")
    if hf_token:
        from whisperx.diarize import DiarizationPipeline

        diarize_model = DiarizationPipeline(token=hf_token, device=device)
        diarize_segments = diarize_model(waveform)
        result = whisperx.assign_word_speakers(diarize_segments, result)

    return result


def result_to_words(result: dict) -> list[TranscriptWord]:
    words = []

    for segment in result.get("segments", []):
        for word in segment.get("words", []):
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


def write_debug_conversation_csv(result: AudioProcessingResult) -> str:
    DEBUG_TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DEBUG_TRANSCRIPT_DIR / f"{result.timestamp}_conversation.csv"

    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=["start_ms", "end_ms", "speaker_label", "confidence", "word"],
        )
        writer.writeheader()
        for word in result.words:
            writer.writerow(asdict(word))

    return str(output_path)


def write_debug_recording_mp3(audio: bytes, timestamp: int) -> str | None:
    DEBUG_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DEBUG_AUDIO_DIR / f"{timestamp}_recording.mp3"

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "s16le",
                "-ar",
                str(SAMPLE_RATE),
                "-ac",
                str(CHANNELS),
                "-i",
                "pipe:0",
                "-codec:a",
                "libmp3lame",
                str(output_path),
            ],
            input=audio,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        print(f"Could not write debug MP3 for recording {timestamp}: {error}", flush=True)
        return None

    print(f"Wrote debug MP3 for recording {timestamp}: {output_path}", flush=True)
    return str(output_path)


def process_audio_bytes(audio: bytes, timestamp: int) -> AudioProcessingResult:
    debug_mp3_path = write_debug_recording_mp3(audio, timestamp)

    result = transcribe_with_whisperx(audio)
    processed = AudioProcessingResult(
        timestamp=timestamp,
        language=result.get("language"),
        words=result_to_words(result),
    )
    debug_csv_path = write_debug_conversation_csv(processed)

    print(
        f"Processed recording {timestamp}: {len(processed.words)} words. CSV: {debug_csv_path}. MP3: {debug_mp3_path}",
        flush=True,
    )

    chunk_audio, chunk_object, word_object = extract_chunks(audio, processed)

    # extract text-embeddings

    # extract audio-embeddings

    return processed


def extract_chunks(audio: bytes, processed: AudioProcessingResult):
    words = processed.words

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
        if word.speaker_label != current_speaker:
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
        current_text += word.word
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

    chunk_audio = []

    for chunk in chunks:
        starting_byte = int(chunk.start_ms * bytes_per_ms)
        ending_byte = int(chunk.end_ms * bytes_per_ms)

        chunk_audio.append(
            audio[starting_byte:ending_byte]
        )

    return chunk_audio, chunks, word_objects
