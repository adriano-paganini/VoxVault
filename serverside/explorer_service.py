"""Conversation discovery and confirmed speaker associations."""

import math
import logging
from functools import lru_cache

from sqlalchemy import and_, case, func, literal, literal_column, or_, select, text
from sqlalchemy.orm import joinedload, selectinload

from db.database import SessionLocal
from db.models import Person, Recording, TranscriptionChunk, TEXT_EMBEDDING_DIM, VOICE_EMBEDDING_DIM


logger = logging.getLogger(__name__)
MIN_TEXT_SIMILARITY = 0.80
MIN_SEARCH_WORDS = 3
KEYWORD_BONUS = 0.5
SEARCH_CANDIDATES = 200


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


def _nearest_query(model, column, reference, conditions=()):
    distance = column.cosine_distance(list(reference))
    # HNSW requires the raw distance operator in ascending ORDER BY, before
    # grouping, score conversion, thresholds, or metadata joins.
    return select(model.id, distance.label("distance")).where(
        column.is_not(None), *conditions,
    ).order_by(distance).limit(SEARCH_CANDIDATES)


def _nearest_ids(session, model, column, reference, conditions=(), min_similarity=-1):
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT set_config('hnsw.ef_search', :value, true)"),
                        {"value": str(SEARCH_CANDIDATES)})
    return [id for id, distance in session.execute(_nearest_query(model, column, reference, conditions))
            if distance is not None and math.isfinite(distance)
            and -1e-6 <= distance <= 2.000001 and 1 - distance >= min_similarity - 1e-6]


def _search_metadata(approximate=False):
    return {"approximate": approximate, "candidateLimit": SEARCH_CANDIDATES if approximate else None}


def _assignment_conditions(assignment):
    if assignment == "assigned":
        return [TranscriptionChunk.person_id.is_not(None)]
    if assignment == "unassigned":
        return [TranscriptionChunk.person_id.is_(None)]
    return []


def _score(value):
    if value is None or not math.isfinite(value):
        return None
    return max(-1.0, min(1.0, float(value)))


def _confidence(value):
    if value is None or not math.isfinite(value):
        return None
    return max(0.0, min(1.0, float(value)))


def _chunk_dict(chunk, similarity=None, match_score=None, keyword_match=None):
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
        "matchScore": match_score,
        "matched": keyword_match is not None,
        "matchType": None if keyword_match is None else "keyword" if keyword_match else "semantic",
    }


def _person_stats(person_ids):
    return (
        select(
            TranscriptionChunk.person_id.label("person_id"),
            func.count(TranscriptionChunk.id).label("chunk_count"),
            func.count(func.distinct(TranscriptionChunk.recording_id)).label("recording_count"),
            func.sum(TranscriptionChunk.word_count).label("word_count"),
        )
        .where(TranscriptionChunk.person_id.in_(person_ids))
        .group_by(TranscriptionChunk.person_id)
        .subquery()
    )


def _person_select(person_ids, similarity=None):
    stats = _person_stats(person_ids)
    return (
        select(
            Person,
            func.coalesce(stats.c.chunk_count, 0),
            func.coalesce(stats.c.recording_count, 0),
            func.coalesce(stats.c.word_count, 0),
            similarity if similarity is not None else literal(None),
        )
        .outerjoin(stats, stats.c.person_id == Person.id)
        .where(Person.id.in_(person_ids))
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
    row = session.execute(_person_select([person_id])).one_or_none()
    if row is None:
        raise ExplorerError(404, "Person not found.")
    return _person_dict(row)


def _chunk_page(session, conditions, similarity, limit, offset, order_by=None,
                match_score=None, keyword_match=None):
    total = session.scalar(
        select(func.count()).select_from(TranscriptionChunk).join(Recording).where(*conditions)
    )
    score = similarity if similarity is not None else literal(None)
    query = (
        select(TranscriptionChunk, score,
               match_score if match_score is not None else literal(None),
               keyword_match if keyword_match is not None else literal(None))
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
        "items": [_chunk_dict(chunk, value, rank, keyword) for chunk, value, rank, keyword in session.execute(query).all()],
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
        raise ExplorerError(503, "Semantic search is temporarily unavailable. Please retry shortly.") from exc


def _matching_chunks(q, assignment, reference):
    conditions = _assignment_conditions(assignment)
    if not q:
        return conditions, None, None, None
    similarity = _similarity(TranscriptionChunk.text_embedding, reference)
    keyword = TranscriptionChunk.text.icontains(q, autoescape=True)
    # Literal matches stay discoverable even when their vector is a weak match.
    conditions.extend([TranscriptionChunk.word_count >= MIN_SEARCH_WORDS,
                       or_(keyword, similarity >= MIN_TEXT_SIMILARITY)])
    match_score = func.coalesce(similarity, 0.0) + case((keyword, KEYWORD_BONUS), else_=0.0)
    return conditions, similarity, match_score, keyword


def search_chunks(q="", assignment="unassigned", limit=25, offset=0):
    q = q.strip()
    reference = query_embedding(q) if q else None
    conditions, similarity, match_score, keyword = _matching_chunks(q, assignment, reference)
    with SessionLocal() as session:
        order = [match_score.desc(), TranscriptionChunk.id] if q else None
        return {**_chunk_page(session, conditions, similarity, limit, offset, order, match_score, keyword),
                **_search_metadata()}


def _conversation_select(recording_ids):
    stats = select(
        TranscriptionChunk.recording_id.label("recording_id"),
        func.count().label("chunk_count"),
        func.sum(TranscriptionChunk.word_count).label("word_count"),
        func.max(TranscriptionChunk.end_ms).label("duration_ms"),
        func.count(func.distinct(TranscriptionChunk.person_id)).label("person_count"),
    ).where(TranscriptionChunk.recording_id.in_(recording_ids)).group_by(TranscriptionChunk.recording_id).subquery()
    preview = select(func.substr(TranscriptionChunk.text, 1, 240)).where(
        TranscriptionChunk.recording_id == Recording.id,
    ).order_by(TranscriptionChunk.start_ms, TranscriptionChunk.chunk_index, TranscriptionChunk.id).limit(1).scalar_subquery()
    return select(
        Recording, func.coalesce(stats.c.chunk_count, 0), func.coalesce(stats.c.word_count, 0),
        func.coalesce(stats.c.duration_ms, 0), func.coalesce(stats.c.person_count, 0), preview,
    ).outerjoin(stats, stats.c.recording_id == Recording.id).where(Recording.id.in_(recording_ids))


def _conversation_dict(row):
    recording, chunks, words, duration, people, preview = row
    return {
        "id": recording.id, "timestamp": recording.timestamp, "language": recording.language,
        "chunkCount": chunks, "wordCount": words, "durationMs": duration, "personCount": people,
        "preview": preview or "", "similarity": None,
        "matchScore": None, "matchedChunkIds": [],
    }


def list_conversations(q="", assignment="all", limit=25, offset=0):
    q = q.strip()
    reference = query_embedding(q) if q else None
    conditions, chunk_similarity, chunk_score, _ = _matching_chunks(q, assignment, reference)
    with SessionLocal() as session:
        order = []
        recording_conditions = []
        if assignment != "all":
            recording_conditions.append(select(TranscriptionChunk.id).where(
                TranscriptionChunk.recording_id == Recording.id, *_assignment_conditions(assignment),
            ).exists())
        if q:
            # Aggregate all qualifying chunks before pagination, without a candidate cap.
            matches = select(
                TranscriptionChunk.recording_id.label("recording_id"),
                func.max(chunk_similarity).label("similarity"),
                func.sum(chunk_score).label("match_score"),
            ).where(*conditions).group_by(TranscriptionChunk.recording_id).subquery()
            query = select(Recording.id, matches.c.similarity, matches.c.match_score).join(
                matches, matches.c.recording_id == Recording.id,
            )
            order = [matches.c.match_score.desc()]
        else:
            query = select(Recording.id, literal(None), literal(None)).where(*recording_conditions)
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        page = session.execute(
            query.order_by(*order, Recording.timestamp.desc(), Recording.id.desc()).limit(limit).offset(offset)
        ).all()
        by_id = {row[0].id: _conversation_dict(row) for row in session.execute(
            _conversation_select([id for id, _, _ in page]),
        )} if page else {}
        items = [{**by_id[id], "similarity": _score(similarity), "matchScore": match_score}
                 for id, similarity, match_score in page]
        if q and items:
            by_id = {item["id"]: item for item in items}
            for chunk_id, recording_id in session.execute(select(
                TranscriptionChunk.id, TranscriptionChunk.recording_id,
            ).where(TranscriptionChunk.recording_id.in_(by_id), *conditions).order_by(
                TranscriptionChunk.start_ms, TranscriptionChunk.chunk_index, TranscriptionChunk.id,
            )):
                by_id[recording_id]["matchedChunkIds"].append(chunk_id)
        return {"items": items, "total": total, "limit": limit, "offset": offset,
                **_search_metadata()}


def get_conversation(recording_id, q="", assignment="all"):
    q = q.strip()
    with SessionLocal() as session:
        row = session.execute(_conversation_select([recording_id])).one_or_none()
        if row is None:
            raise ExplorerError(404, "Conversation not found.")
        result = _conversation_dict(row)
        reference = query_embedding(q) if q else None
        matches = {}
        if q:
            conditions, similarity, match_score, keyword = _matching_chunks(q, assignment, reference)
            matches = {id: (value, rank, exact) for id, value, rank, exact in session.execute(select(
                TranscriptionChunk.id, similarity, match_score, keyword,
            ).where(TranscriptionChunk.recording_id == recording_id, *conditions))}
            result["similarity"] = _score(max((value for value, _, _ in matches.values() if value is not None), default=None))
            result["matchScore"] = math.fsum(rank for _, rank, _ in matches.values())
        chunks = session.scalars(select(TranscriptionChunk).where(
            TranscriptionChunk.recording_id == recording_id,
        ).options(
            joinedload(TranscriptionChunk.recording), joinedload(TranscriptionChunk.person),
            selectinload(TranscriptionChunk.words),
        ).order_by(TranscriptionChunk.start_ms, TranscriptionChunk.chunk_index, TranscriptionChunk.id)).all()
        result["chunks"] = [
            _chunk_dict(chunk, *matches.get(chunk.id, (None, None, None)))
            for chunk in chunks
        ]
        result["matchedChunkIds"] = [chunk.id for chunk in chunks if chunk.id in matches]
        return result


def get_chunk(chunk_id):
    with SessionLocal() as session:
        return _chunk_dict(_get_chunk(session, chunk_id))


def _person_name_order():
    return func.lower(func.coalesce(Person.name, literal_column("''"))).collate("C")


def list_persons(chunk_id=None, q="", limit=25, offset=0):
    with SessionLocal() as session:
        similarity = None
        reference = None
        name_order = _person_name_order()
        conditions = [name_order.startswith(q.strip().lower(), autoescape=True)] if q.strip() else []
        if chunk_id is not None:
            chunk = _get_chunk(session, chunk_id)
            reference = _valid_vector(chunk.voice_embedding)
            if reference is not None:
                similarity = _similarity(Person.voice_embedding, reference)
        order = [similarity.desc().nullslast()] if similarity is not None else []
        candidates = select(Person.id).where(*conditions).order_by(*order, name_order, Person.id)
        page_ids = session.scalars(candidates.limit(limit + 1).offset(offset)).all()
        total = session.scalar(select(func.count()).select_from(Person).where(*conditions))
        has_more = len(page_ids) > limit
        query = _person_select(page_ids[:limit], similarity)
        if similarity is not None:
            query = query.order_by(similarity.desc().nullslast())
        query = query.order_by(name_order, Person.id)
        return {"items": [_person_dict(row) for row in session.execute(query).all()],
                "total": total, "limit": limit, "offset": offset, "hasMore": has_more,
                **_search_metadata()}


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
        ids = _nearest_ids(session, TranscriptionChunk, TranscriptionChunk.voice_embedding, reference, conditions)
        conditions.append(TranscriptionChunk.id.in_(ids))
        return {**_chunk_page(
            session, conditions, similarity, limit, offset,
            [similarity.desc().nullslast(), TranscriptionChunk.id],
        ), **_search_metadata(True)}


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
        # Keep the recording timestamp so re-uploading cannot recreate deleted speech.
        session.delete(chunk)
        session.flush()
        for person in people.values():
            _recompute_person(session, person)
