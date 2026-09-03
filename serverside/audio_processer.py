import csv
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


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
    print(f"Processing recording {timestamp}: {len(audio)} PCM bytes", flush=True)
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
    return processed
