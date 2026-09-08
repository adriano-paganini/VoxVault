"""Conversation discovery and confirmed speaker associations."""

import math
import logging
from functools import lru_cache

from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.orm import joinedload, selectinload

from db.database import SessionLocal
from db.models import Person, Recording, TranscriptionChunk, TEXT_EMBEDDING_DIM, VOICE_EMBEDDING_DIM
from conversation_embeddings import update_conversation_embedding


logger = logging.getLogger(__name__)
DEFAULT_TEXT_SIMILARITY = 0.75


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
    distance = column.cosine_distance(list(reference))
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


@lru_cache(maxsize=128)
def query_embedding(q):
    try:
        from audio_processer import create_query_embedding

        vector = _valid_vector(create_query_embedding(q), TEXT_EMBEDDING_DIM)
        if vector is None:
            raise ValueError("Invalid text query vector")
        return tuple(vector)
    except Exception as exc:
        logger.exception("Could not create a conversation search embedding")
        raise ExplorerError(503, "Semantic search is temporarily unavailable. Try text search or retry shortly.") from exc


def _matching_chunks(q, mode, assignment, min_similarity, reference):
    conditions = []
    similarity = None
    if assignment == "assigned":
        conditions.append(TranscriptionChunk.person_id.is_not(None))
    elif assignment == "unassigned":
        conditions.append(TranscriptionChunk.person_id.is_(None))
    if q:
        if mode == "text":
            conditions.append(TranscriptionChunk.text.icontains(q, autoescape=True))
        else:
            similarity = _similarity(TranscriptionChunk.text_embedding, reference)
            conditions.append(similarity >= min_similarity)
    return conditions, similarity


def search_chunks(q="", mode="text", assignment="unassigned", limit=25, offset=0):
    q = q.strip()
    reference = query_embedding(q) if q and mode == "semantic" else None
    conditions, similarity = _matching_chunks(q, mode, assignment, DEFAULT_TEXT_SIMILARITY, reference)
    with SessionLocal() as session:
        order = [similarity.desc(), TranscriptionChunk.id] if similarity is not None else None
        return _chunk_page(session, conditions, similarity, limit, offset, order)


def _conversation_select(similarity=None):
    stats = select(
        TranscriptionChunk.recording_id.label("recording_id"),
        func.count().label("chunk_count"),
        func.sum(TranscriptionChunk.word_count).label("word_count"),
        func.max(TranscriptionChunk.end_ms).label("duration_ms"),
        func.count(func.distinct(TranscriptionChunk.person_id)).label("person_count"),
    ).group_by(TranscriptionChunk.recording_id).subquery()
    preview = select(func.substr(TranscriptionChunk.text, 1, 240)).where(
        TranscriptionChunk.recording_id == Recording.id,
    ).order_by(TranscriptionChunk.start_ms, TranscriptionChunk.chunk_index, TranscriptionChunk.id).limit(1).scalar_subquery()
    return select(
        Recording, func.coalesce(stats.c.chunk_count, 0), func.coalesce(stats.c.word_count, 0),
        func.coalesce(stats.c.duration_ms, 0), func.coalesce(stats.c.person_count, 0), preview,
        similarity if similarity is not None else literal(None),
    ).outerjoin(stats, stats.c.recording_id == Recording.id)


def _conversation_dict(row):
    recording, chunks, words, duration, people, preview, similarity = row
    return {
        "id": recording.id, "timestamp": recording.timestamp, "language": recording.language,
        "chunkCount": chunks, "wordCount": words, "durationMs": duration, "personCount": people,
        "preview": preview or "", "hasTextEmbedding": _valid_vector(recording.text_embedding, TEXT_EMBEDDING_DIM) is not None,
        "similarity": _score(similarity), "matchedChunkIds": [],
    }


def list_conversations(q="", mode="semantic", assignment="all", limit=25, offset=0, min_similarity=DEFAULT_TEXT_SIMILARITY):
    q = q.strip()
    reference = query_embedding(q) if q and mode != "text" else None
    conditions, chunk_similarity = _matching_chunks(q, mode, assignment, min_similarity, reference)
    matches = select(
        TranscriptionChunk.recording_id.label("recording_id"),
        func.max(chunk_similarity).label("similarity") if chunk_similarity is not None else literal(None).label("similarity"),
    ).where(*conditions).group_by(TranscriptionChunk.recording_id).subquery()
    theme_search = bool(q and mode == "conversation")
    similarity = _similarity(Recording.text_embedding, reference) if theme_search else matches.c.similarity
    query = _conversation_select(similarity)
    if theme_search:
        query = query.where(similarity >= min_similarity)
        if assignment != "all":
            assignment_conditions, _ = _matching_chunks("", mode, assignment, min_similarity, None)
            query = query.where(select(TranscriptionChunk.id).where(
                TranscriptionChunk.recording_id == Recording.id, *assignment_conditions,
            ).exists())
    elif q or assignment != "all":
        query = query.join(matches, matches.c.recording_id == Recording.id)
    else:
        query = query.outerjoin(matches, matches.c.recording_id == Recording.id)
    order = [similarity.desc().nullslast()] if q and mode != "text" else []
    with SessionLocal() as session:
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        items = [_conversation_dict(row) for row in session.execute(
            query.order_by(*order, Recording.timestamp.desc(), Recording.id.desc()).limit(limit).offset(offset)
        )]
        if q and items:
            by_id = {item["id"]: item for item in items}
            for chunk_id, recording_id in session.execute(select(
                TranscriptionChunk.id, TranscriptionChunk.recording_id,
            ).where(TranscriptionChunk.recording_id.in_(by_id), *conditions).order_by(
                TranscriptionChunk.start_ms, TranscriptionChunk.chunk_index, TranscriptionChunk.id,
            )):
                by_id[recording_id]["matchedChunkIds"].append(chunk_id)
        return {"items": items, "total": total, "limit": limit, "offset": offset}


def get_conversation(recording_id, q="", mode="semantic", assignment="all", min_similarity=DEFAULT_TEXT_SIMILARITY):
    q = q.strip()
    with SessionLocal() as session:
        row = session.execute(_conversation_select().where(Recording.id == recording_id)).one_or_none()
        if row is None:
            raise ExplorerError(404, "Conversation not found.")
        result = _conversation_dict(row)
        reference = query_embedding(q) if q and mode != "text" else None
        matches = {}
        if q:
            conditions, similarity = _matching_chunks(q, mode, assignment, min_similarity, reference)
            matches = dict(session.execute(select(
                TranscriptionChunk.id, similarity if similarity is not None else literal(None),
            ).where(TranscriptionChunk.recording_id == recording_id, *conditions)).all())
            if mode == "conversation":
                result["similarity"] = _score(session.scalar(select(
                    _similarity(Recording.text_embedding, reference),
                ).where(Recording.id == recording_id)))
        chunks = session.scalars(select(TranscriptionChunk).where(
            TranscriptionChunk.recording_id == recording_id,
        ).options(
            joinedload(TranscriptionChunk.recording), joinedload(TranscriptionChunk.person),
            selectinload(TranscriptionChunk.words),
        ).order_by(TranscriptionChunk.start_ms, TranscriptionChunk.chunk_index, TranscriptionChunk.id)).all()
        result["chunks"] = [
            {**_chunk_dict(chunk, matches.get(chunk.id)), "matched": chunk.id in matches}
            for chunk in chunks
        ]
        result["matchedChunkIds"] = [chunk.id for chunk in chunks if chunk.id in matches]
        return result


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


def _assign_in_session(session, chunk, people, person_id):
    previous_id = chunk.person_id
    chunk.person = people.get(person_id)
    session.flush()
    for affected_id in sorted({value for value in (previous_id, person_id) if value is not None}):
        _recompute_person(session, people[affected_id], require_embedding=(
            affected_id == person_id and weighted_voice_embedding([chunk])[0] is not None
        ))
    session.flush()
    return {
        "person": _person_response(session, person_id) if person_id is not None else None,
        "chunk": _chunk_dict(chunk),
    }


def create_person(name, chunk_id):
    name = _name(name)
    with SessionLocal.begin() as session:
        chunk = _get_chunk(session, chunk_id, lock=True)
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
        people = _lock_persons(session, [chunk.person_id, person_id])
        return _assign_in_session(session, chunk, people, person_id)


def delete_chunk(chunk_id):
    with SessionLocal.begin() as session:
        chunk = _get_chunk(session, chunk_id, lock=True)
        people = _lock_persons(session, [chunk.person_id])
        recording = session.scalar(select(Recording).where(
            Recording.id == chunk.recording_id,
        ).with_for_update())
        # Keep the recording timestamp so re-uploading cannot recreate deleted speech.
        session.delete(chunk)
        session.flush()
        for person in people.values():
            _recompute_person(session, person)
        remaining = session.scalars(select(TranscriptionChunk).where(
            TranscriptionChunk.recording_id == recording.id,
        )).all()
        update_conversation_embedding(recording, remaining)
