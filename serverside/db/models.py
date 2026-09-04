from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    DateTime,
    Double,
    ForeignKey,
    Integer,
    Text,
    func,
    text as sql_text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    pass


class Recording(Base):
    __tablename__ = "recording"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )

    timestamp: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
    )

    language: Mapped[str | None] = mapped_column(Text)

    chunks: Mapped[list[TranscriptionChunk]] = relationship(
        back_populates="recording",
        cascade="all, delete-orphan",
    )


class Person(Base):
    __tablename__ = "person"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )

    name: Mapped[str | None] = mapped_column(Text)

    embedding: Mapped[list[float] | None] = mapped_column(
        VECTOR(384)
    )

    embedding_word_count: Mapped[int] = mapped_column(
        Integer,
        server_default=sql_text("0"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    chunks: Mapped[list[TranscriptionChunk]] = relationship(
        back_populates="person"
    )

    speaker_embeddings: Mapped[list[SpeakerEmbedding]] = relationship(
        back_populates="person"
    )


class TranscriptionChunk(Base):
    __tablename__ = "transcription_chunk"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )

    recording_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("recording.id"),
    )

    person_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("person.id"),
    )

    name: Mapped[str | None] = mapped_column(Text)

    chunk_index: Mapped[int] = mapped_column(Integer)

    text: Mapped[str | None] = mapped_column(Text)

    word_count: Mapped[int] = mapped_column(
        Integer,
        server_default=sql_text("0"),
    )

    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)

    raw_speaker_label: Mapped[str | None] = mapped_column(Text)

    embedding: Mapped[list[float] | None] = mapped_column(
        VECTOR(384)
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    recording: Mapped[Recording] = relationship(
        back_populates="chunks"
    )

    person: Mapped[Person | None] = relationship(
        back_populates="chunks"
    )

    words: Mapped[list[TranscriptionWord]] = relationship(
        back_populates="chunk",
        cascade="all, delete-orphan",
    )

    speaker_embeddings: Mapped[list[SpeakerEmbedding]] = relationship(
        back_populates="chunk",
        cascade="all, delete-orphan",
    )


class SpeakerEmbedding(Base):
    __tablename__ = "speaker_embedding"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )

    person_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("person.id"),
    )

    chunk_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("transcription_chunk.id"),
    )

    embedding: Mapped[list[float]] = mapped_column(
        VECTOR(384)
    )

    word_count: Mapped[int] = mapped_column(
        Integer,
        server_default=sql_text("0"),
    )

    distance: Mapped[float | None] = mapped_column(Double)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    person: Mapped[Person | None] = relationship(
        back_populates="speaker_embeddings"
    )

    chunk: Mapped[TranscriptionChunk] = relationship(
        back_populates="speaker_embeddings"
    )


class TranscriptionWord(Base):
    __tablename__ = "transcription_word"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
    )

    chunk_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("transcription_chunk.id"),
    )

    word_index: Mapped[int] = mapped_column(Integer)

    word: Mapped[str] = mapped_column(Text)

    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)

    raw_speaker_label: Mapped[str | None] = mapped_column(Text)

    confidence: Mapped[float] = mapped_column(Double)

    chunk: Mapped[TranscriptionChunk] = relationship(
        back_populates="words"
    )
