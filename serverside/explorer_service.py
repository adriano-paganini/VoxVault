"""Conversation discovery and confirmed speaker associations."""

import logging
import math

from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.orm import joinedload, selectinload

from db.database import SessionLocal
from db.models import Person, Recording, TEXT_EMBEDDING_DIM, TranscriptionChunk, VOICE_EMBEDDING_DIM


logger = logging.getLogger(__name__)


class ExplorerError(ValueError):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _valid_vector(value, dimension=VOICE_EMBEDDING_DIM):
    if value is None:
        return None
    try:
        vector = [float(component) for component in value]
    except (TypeError, ValueError, OverflowError):
        return None
    if len(vector) != dimension or not all(math.isfinite(component) for component in vector):
        return None
    if not 0 < math.hypot(*vector) < math.inf:
        return None
    return vector


def weighted_voice_embedding(chunks):
    """Normalize the word-weighted mean of original, unnormalized chunk vectors."""
    samples = []
    total_weight = 0
    for chunk in chunks:
        vector = _valid_vector(chunk.voice_embedding)
        weight = chunk.word_count
        if vector is None or isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0:
            continue
        samples.append((vector, weight))
        total_weight += weight
    if not samples:
        return None, 0
    # Scale weights first so long recordings do not overflow an intermediate sum.
    try:
        average = [
            math.fsum(vector[index] * (weight / total_weight) for vector, weight in samples)
            for index in range(VOICE_EMBEDDING_DIM)
        ]
    except (OverflowError, ValueError):
        return None, total_weight
    norm = math.hypot(*average)
    if not 0 < norm < math.inf:
        return None, total_weight
    return [component / norm for component in average], total_weight


def _similarity(column, reference):
    distance = column.cosine_distance(reference)
    valid = and_(distance >= -1e-6, distance <= 2.000001)
    return case((valid, 1.0 - distance), else_=None)


def _score(value):
    if value is None or not math.isfinite(value):
        return None
    return max(-1.0, min(1.0, float(value)))


def _confidence(value):
    if value is None or not math.isfinite(value):
        return None
    return max(0.0, min(1.0, float(value)))


def _chunk_dict(chunk, similarity=None):
    return {
        "id": chunk.id,
        "recordingId": chunk.recording_id,
        "recordingTimestamp": chunk.recording.timestamp,
        "language": chunk.recording.language,
        "chunkIndex": chunk.chunk_index,
        "text": chunk.text,
        "wordCount": chunk.word_count,
        "startMs": chunk.start_ms,
        "endMs": chunk.end_ms,
        "personId": chunk.person_id,
        "personName": chunk.person.name if chunk.person is not None else None,
        "hasVoiceEmbedding": _valid_vector(chunk.voice_embedding) is not None,
        "words": [
            {
                "word": word.word,
                "index": word.word_index,
                "startMs": word.start_ms,
                "endMs": word.end_ms,
                "confidence": _confidence(word.confidence),
                "speakerLabel": word.raw_speaker_label,
            }
            for word in sorted(chunk.words, key=lambda item: item.word_index)
        ],
        "similarity": _score(similarity),
    }


def _person_stats():
    return (
        select(
            TranscriptionChunk.person_id.label("person_id"),
            func.count(TranscriptionChunk.id).label("chunk_count"),
            func.count(func.distinct(TranscriptionChunk.recording_id)).label("recording_count"),
            func.sum(TranscriptionChunk.word_count).label("word_count"),
        )
        .group_by(TranscriptionChunk.person_id)
        .subquery()
    )


def _person_select(similarity=None):
    stats = _person_stats()
    return (
        select(
            Person,
            func.coalesce(stats.c.chunk_count, 0),
            func.coalesce(stats.c.recording_count, 0),
            func.coalesce(stats.c.word_count, 0),
            similarity if similarity is not None else literal(None),
        )
        .outerjoin(stats, stats.c.person_id == Person.id)
    )


def _person_dict(row):
    person, chunk_count, recording_count, word_count, similarity = row
    return {
        "id": person.id,
        "name": person.name,
        "chunkCount": chunk_count,
        "recordingCount": recording_count,
        "wordCount": word_count,
        "hasVoiceEmbedding": _valid_vector(person.voice_embedding) is not None,
        "similarity": _score(similarity),
    }


def _get_person(session, person_id):
    person = session.get(Person, person_id)
    if person is None:
        raise ExplorerError(404, "Person not found.")
    return person


def _get_chunk(session, chunk_id, lock=False):
    query = select(TranscriptionChunk).where(TranscriptionChunk.id == chunk_id)
    if lock:
        query = query.with_for_update()
    chunk = session.execute(query).scalar_one_or_none()
    if chunk is None:
        raise ExplorerError(404, "Conversation chunk not found.")
    return chunk


def _person_response(session, person_id):
    row = session.execute(_person_select().where(Person.id == person_id)).one_or_none()
    if row is None:
        raise ExplorerError(404, "Person not found.")
    return _person_dict(row)


def _chunk_page(session, conditions, similarity, limit, offset, order_by=None):
    total = session.scalar(
        select(func.count()).select_from(TranscriptionChunk).join(Recording).where(*conditions)
    )
    score = similarity if similarity is not None else literal(None)
    query = (
        select(TranscriptionChunk, score)
        .join(Recording)
        .where(*conditions)
        .options(
            joinedload(TranscriptionChunk.recording),
            joinedload(TranscriptionChunk.person),
            selectinload(TranscriptionChunk.words),
        )
        .order_by(*(order_by or [Recording.timestamp.desc(), TranscriptionChunk.chunk_index, TranscriptionChunk.id]))
        .limit(limit)
        .offset(offset)
    )
    return {
        "items": [_chunk_dict(chunk, value) for chunk, value in session.execute(query).all()],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _query_embedding(q):
    try:
        import audio_processer
        from main import processing_lock

        with processing_lock:
            vector = _valid_vector(audio_processer.create_query_embedding(q), TEXT_EMBEDDING_DIM)
        if vector is None:
            raise ValueError("The text embedding model returned an invalid vector.")
        return vector
    except Exception as exc:
        logger.exception("Conversation semantic search could not generate a query embedding")
        raise ExplorerError(503, "Semantic search is unavailable. Try text search or retry shortly.") from exc


def search_chunks(q="", mode="semantic", assignment="all", limit=25, offset=0):
    conditions = []
    if assignment == "assigned":
        conditions.append(TranscriptionChunk.person_id.is_not(None))
    elif assignment == "unassigned":
        conditions.append(TranscriptionChunk.person_id.is_(None))
    similarity = None
    order_by = None
    q = q.strip()
    if q:
        if mode == "text":
            conditions.append(TranscriptionChunk.text.icontains(q, autoescape=True))
        else:
            similarity = _similarity(TranscriptionChunk.text_embedding, _query_embedding(q))
            conditions.append(similarity.is_not(None))
            order_by = [similarity.desc().nullslast(), TranscriptionChunk.id]
    with SessionLocal() as session:
        return _chunk_page(session, conditions, similarity, limit, offset, order_by)


def get_chunk(chunk_id):
    with SessionLocal() as session:
        return _chunk_dict(_get_chunk(session, chunk_id))


def list_persons(chunk_id=None):
    with SessionLocal() as session:
        similarity = None
        if chunk_id is not None:
            chunk = _get_chunk(session, chunk_id)
            reference = _valid_vector(chunk.voice_embedding)
            if reference is not None:
                similarity = _similarity(Person.voice_embedding, reference)
        query = _person_select(similarity)
        if similarity is not None:
            query = query.order_by(similarity.desc().nullslast())
        query = query.order_by(func.lower(func.coalesce(Person.name, "")), Person.id)
        return {"items": [_person_dict(row) for row in session.execute(query).all()]}


def get_person(person_id):
    with SessionLocal() as session:
        return _person_response(session, person_id)


def person_chunks(person_id, limit=25, offset=0):
    with SessionLocal() as session:
        person = _get_person(session, person_id)
        reference = _valid_vector(person.voice_embedding)
        similarity = _similarity(TranscriptionChunk.voice_embedding, reference) if reference is not None else None
        return _chunk_page(
            session, [TranscriptionChunk.person_id == person_id], similarity, limit, offset,
        )


def similar_chunks(chunk_id=None, person_id=None, scope="other", limit=25, offset=0):
    if (chunk_id is None) == (person_id is None):
        raise ExplorerError(422, "Provide exactly one of chunk_id or person_id.")
    with SessionLocal() as session:
        conditions = []
        if chunk_id is not None:
            source = _get_chunk(session, chunk_id)
            selected_person_id = source.person_id
            conditions.append(TranscriptionChunk.id != source.id)
        else:
            source = _get_person(session, person_id)
            selected_person_id = source.id
        reference = _valid_vector(source.voice_embedding)
        if reference is None:
            raise ExplorerError(409, "This selection has no usable voice embedding.")
        if scope == "unassigned":
            conditions.append(TranscriptionChunk.person_id.is_(None))
        elif scope == "other" and selected_person_id is not None:
            conditions.append(or_(
                TranscriptionChunk.person_id.is_(None),
                TranscriptionChunk.person_id != selected_person_id,
            ))
        similarity = _similarity(TranscriptionChunk.voice_embedding, reference)
        conditions.append(similarity.is_not(None))
        return _chunk_page(
            session, conditions, similarity, limit, offset,
            [similarity.desc().nullslast(), TranscriptionChunk.id],
        )


def _name(name):
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
        raise ExplorerError(422, "Enter a person name between 1 and 200 characters.")
    return name.strip()


def rename_person(person_id, name):
    name = _name(name)
    with SessionLocal.begin() as session:
        person = session.execute(
            select(Person).where(Person.id == person_id).with_for_update()
        ).scalar_one_or_none()
        if person is None:
            raise ExplorerError(404, "Person not found.")
        person.name = name
        session.flush()
        return _person_response(session, person.id)


def _lock_persons(session, person_ids):
    ids = sorted({person_id for person_id in person_ids if person_id is not None})
    if not ids:
        return {}
    # Chunk mutations lock the chunk first, then affected people in ID order.
    people = session.execute(
        select(Person).where(Person.id.in_(ids)).order_by(Person.id).with_for_update()
    ).scalars().all()
    if len(people) != len(ids):
        raise ExplorerError(404, "Person not found.")
    return {person.id: person for person in people}


def _recompute_person(session, person, require_embedding=False):
    chunks = session.execute(
        select(TranscriptionChunk).where(TranscriptionChunk.person_id == person.id)
    ).scalars().all()
    embedding, weight = weighted_voice_embedding(chunks)
    if require_embedding and embedding is None:
        raise ExplorerError(422, "These speech samples cannot form a valid voice profile.")
    person.voice_embedding = embedding
    person.voice_embedding_word_count = weight if embedding is not None else 0


def _require_speech(chunk):
    embedding, _weight = weighted_voice_embedding([chunk])
    if embedding is None:
        raise ExplorerError(422, "This chunk has no usable voice embedding with spoken words.")


def _assign_in_session(session, chunk, people, person_id):
    previous_id = chunk.person_id
    chunk.person = people.get(person_id)
    session.flush()
    for affected_id in sorted({value for value in (previous_id, person_id) if value is not None}):
        _recompute_person(session, people[affected_id], require_embedding=affected_id == person_id)
    session.flush()
    return {
        "person": _person_response(session, person_id) if person_id is not None else None,
        "chunk": _chunk_dict(chunk),
    }


def create_person(name, chunk_id):
    name = _name(name)
    with SessionLocal.begin() as session:
        chunk = _get_chunk(session, chunk_id, lock=True)
        _require_speech(chunk)
        # Lock the existing profile before creating the new one to keep lock order stable.
        people = _lock_persons(session, [chunk.person_id])
        person = Person(name=name, voice_embedding_word_count=0)
        session.add(person)
        session.flush()
        people[person.id] = person
        return _assign_in_session(session, chunk, people, person.id)


def assign_person(chunk_id, person_id):
    with SessionLocal.begin() as session:
        chunk = _get_chunk(session, chunk_id, lock=True)
        if person_id is not None:
            _require_speech(chunk)
        people = _lock_persons(session, [chunk.person_id, person_id])
        return _assign_in_session(session, chunk, people, person_id)


def delete_chunk(chunk_id):
    with SessionLocal.begin() as session:
        chunk = _get_chunk(session, chunk_id, lock=True)
        people = _lock_persons(session, [chunk.person_id])
        # Keep the recording timestamp so re-uploading cannot recreate deleted speech.
        session.delete(chunk)
        session.flush()
        for person in people.values():
            _recompute_person(session, person)
