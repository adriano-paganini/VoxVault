from __future__ import annotations

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    Double,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Text,
    func,
    literal_column,
    or_,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


TEXT_EMBEDDING_DIM = 384
VOICE_EMBEDDING_DIM = 192


class Base(DeclarativeBase):
    pass


class Recording(Base):
    __tablename__ = "recording"

    id: Mapped[int] = mapped_column(
        primary_key=True
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
        primary_key=True
    )

    name: Mapped[str | None] = mapped_column(Text)

    voice_embedding: Mapped[list[float] | None] = mapped_column(
        VECTOR(VOICE_EMBEDDING_DIM)
    )

    voice_embedding_word_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    chunks: Mapped[list[TranscriptionChunk]] = relationship(
        back_populates="person"
    )


class SetupState(Base):
    __tablename__ = "setup_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    stage: Mapped[str] = mapped_column(Text, default="device")
    recording_timestamp: Mapped[int | None] = mapped_column(BigInteger)
    received_chunks: Mapped[int] = mapped_column(Integer, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"))
    error: Mapped[str | None] = mapped_column(Text)
    history: Mapped[list[dict]] = mapped_column(JSON, default=list)
    expected_languages: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: ["en"], server_default='["en"]',
    )


class TranscriptionChunk(Base):
    __tablename__ = "transcription_chunk"

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    recording_id: Mapped[int] = mapped_column(
        ForeignKey("recording.id"), index=True,
    )

    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("person.id"), index=True,
    )

    chunk_index: Mapped[int] = mapped_column(Integer)

    text: Mapped[str] = mapped_column(Text)

    word_count: Mapped[int] = mapped_column(Integer)

    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)

    text_embedding: Mapped[list[float]] = mapped_column(
        VECTOR(TEXT_EMBEDDING_DIM)
    )

    voice_embedding: Mapped[list[float]] = mapped_column(
        VECTOR(VOICE_EMBEDDING_DIM)
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


class TranscriptionWord(Base):
    __tablename__ = "transcription_word"

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("transcription_chunk.id"), index=True,
    )

    word_index: Mapped[int] = mapped_column(Integer)

    word: Mapped[str] = mapped_column(Text)

    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)

    raw_speaker_label: Mapped[str | None] = mapped_column(Text)

    confidence: Mapped[float | None] = mapped_column(Double)

    chunk: Mapped[TranscriptionChunk] = relationship(
        back_populates="words"
    )


Index(
    "ix_chunk_text_hnsw", TranscriptionChunk.text_embedding,
    postgresql_using="hnsw", postgresql_ops={"text_embedding": "vector_cosine_ops"},
).ddl_if(dialect="postgresql")
Index(
    "ix_person_voice_hnsw", Person.voice_embedding,
    postgresql_using="hnsw", postgresql_ops={"voice_embedding": "vector_cosine_ops"},
).ddl_if(dialect="postgresql")
Index(
    "ix_chunk_voice_hnsw", TranscriptionChunk.voice_embedding,
    postgresql_using="hnsw", postgresql_ops={"voice_embedding": "vector_cosine_ops"},
).ddl_if(dialect="postgresql")
Index(
    "ix_chunk_recording_timeline", TranscriptionChunk.recording_id,
    TranscriptionChunk.start_ms, TranscriptionChunk.chunk_index, TranscriptionChunk.id,
)
_person_name_index_expression = func.lower(func.coalesce(Person.name, literal_column("''"))).collate("C")
Index("ix_person_name_order", _person_name_index_expression, Person.id).ddl_if(dialect="postgresql")
Index(
    "ix_person_missing_voice_name", _person_name_index_expression, Person.id,
    postgresql_where=or_(Person.voice_embedding.is_(None), Person.voice_embedding.max_inner_product(Person.voice_embedding) == 0),
).ddl_if(dialect="postgresql")
