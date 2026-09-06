from db.models import Recording, TranscriptionChunk, TranscriptionWord, Person
from db.database import SessionLocal


def save_recording(recording: Recording, person: Person | None = None):
    with SessionLocal.begin() as session:
        session.add(recording)
        if person is not None:
            from setup_service import complete_in_session

            session.add(person)
            session.flush()
            complete_in_session(session, recording.timestamp, person.id)
