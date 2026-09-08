"""Conversation vectors use the same text embedding space as their chunks."""

import math

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.models import Recording, TEXT_EMBEDDING_DIM, TranscriptionChunk


EMBEDDING_VERSION = 1


def conversation_embedding(chunks):
    samples = []
    for chunk in chunks:
        weight = chunk.word_count
        if isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0:
            continue
        try:
            vector = [float(value) for value in chunk.text_embedding]
        except (TypeError, ValueError, OverflowError):
            continue
        norm = math.hypot(*vector)
        if len(vector) != TEXT_EMBEDDING_DIM or not 0 < norm < math.inf:
            continue
        samples.append(([value / norm for value in vector], weight))
    total = sum(weight for _, weight in samples)
    if not total:
        return None
    average = [
        math.fsum(vector[index] * (weight / total) for vector, weight in samples)
        for index in range(TEXT_EMBEDDING_DIM)
    ]
    norm = math.hypot(*average)
    return [value / norm for value in average] if 0 < norm < math.inf else None


def update_conversation_embedding(recording, chunks):
    recording.text_embedding = conversation_embedding(chunks)
    recording.text_embedding_version = EMBEDDING_VERSION


def backfill_conversation_embeddings(session):
    # Bounded batches keep startup migration memory independent of archive size.
    while True:
        recordings = session.scalars(
            select(Recording)
            .where(Recording.text_embedding_version < EMBEDDING_VERSION)
            .order_by(Recording.id).limit(100)
            .options(selectinload(Recording.chunks).load_only(
                TranscriptionChunk.text_embedding, TranscriptionChunk.word_count,
            ))
        ).all()
        if not recordings:
            return
        for recording in recordings:
            update_conversation_embedding(recording, recording.chunks)
        session.flush()
