from db.models import Recording, TranscriptionChunk, TranscriptionWord, Person
from db.database import SessionLocal


def save_recording(recording: Recording):
    with SessionLocal.begin() as session:
        session.add(recording)