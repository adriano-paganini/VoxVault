from datetime import datetime, timezone

from sqlalchemy import select

from db.database import SessionLocal
from db.models import SetupState


PROCESSING_STAGES = {
    "receiving", "validating", "transcribing", "aligning",
    "text_embedding", "voice_embedding", "saving",
}
READING_TEXT = (
    "This morning, I opened the window and listened to the sounds outside. "
    "A bicycle rolled past the garden, and a gentle breeze moved through the trees. "
    "I made a cup of tea, picked up my notebook, and began to plan the day. "
    "There were a few ordinary things to do: buy fresh vegetables, return a book, "
    "and call a friend who lives across town.\n\n"
    "Later, I walked along the river. The water reflected the clouds, while small "
    "waves broke against the stones. Near the bridge, someone was playing a familiar "
    "tune. I paused for a moment, then continued at an easy pace.\n\n"
    "I enjoy noticing these small details. A conversation, a new idea, or a quiet "
    "afternoon can be worth remembering. When I speak, my voice has its own rhythm "
    "and tone. Today I am reading clearly, comfortably, and naturally, just as I "
    "would when telling a story to someone I know."
)


def _state(session):
    return session.execute(
        select(SetupState).where(SetupState.id == 1).with_for_update()
    ).scalar_one()


def _transition(state, stage, message):
    state.stage = stage
    state.history = [*state.history, {
        "stage": stage,
        "message": message,
        "time": datetime.now(timezone.utc).isoformat(),
    }][-30:]


def initialize_setup():
    with SessionLocal.begin() as session:
        state = session.get(SetupState, 1)
        if state is None:
            session.add(SetupState(id=1))
        elif state.stage in PROCESSING_STAGES:
            state.error = "The server restarted during setup. Please send the recording again."
            _transition(state, "failed", state.error)


def get_status():
    with SessionLocal() as session:
        state = session.get(SetupState, 1)
        return {
            "stage": state.stage,
            "recordingTimestamp": state.recording_timestamp,
            "receivedChunks": state.received_chunks,
            "totalChunks": state.total_chunks,
            "personId": state.person_id,
            "error": state.error,
            "history": state.history,
            "readingText": READING_TEXT,
            "expectedLanguages": state.expected_languages,
        }


def get_expected_languages():
    with SessionLocal() as session:
        return tuple(session.get(SetupState, 1).expected_languages)


def available_languages():
    # This module contains constants and utilities; it does not load ML models.
    from whisperx.utils import LANGUAGES

    return [{"code": code, "name": name.title()}
            for code, name in sorted(LANGUAGES.items(), key=lambda item: item[1])]


def set_expected_languages(languages):
    supported = {language["code"] for language in available_languages()}
    normalized = list(dict.fromkeys(language.strip().lower() for language in languages))
    if not normalized or any(language not in supported for language in normalized):
        raise ValueError("Select at least one valid Whisper language code.")
    with SessionLocal.begin() as session:
        _state(session).expected_languages = normalized
    return normalized


def advance(step):
    allowed = {
        "reading": {"device", "reading", "awaiting_upload", "failed"},
        "awaiting_upload": {"reading", "awaiting_upload", "failed"},
    }
    with SessionLocal.begin() as session:
        state = _state(session)
        if state.stage not in allowed.get(step, set()):
            raise ValueError("Setup cannot change steps while a recording is being processed.")
        if state.stage == step:
            return
        state.recording_timestamp = None
        state.received_chunks = 0
        state.total_chunks = 0
        state.error = None
        state.history = []
        message = (
            "Ready for your reading on the phone."
            if step == "reading" else "Waiting for your recording."
        )
        _transition(state, step, message)


def claim_upload(timestamp, total_chunks):
    """The next recording sent after the upload step belongs to this setup."""
    with SessionLocal.begin() as session:
        state = _state(session)
        if state.stage == "awaiting_upload":
            state.recording_timestamp = timestamp
            state.total_chunks = total_chunks
            _transition(state, "receiving", "Receiving and decrypting your recording.")
        return state.recording_timestamp == timestamp and state.stage in PROCESSING_STAGES


def update_progress(timestamp, stage, message, received_chunks=None):
    with SessionLocal.begin() as session:
        state = _state(session)
        if state.recording_timestamp != timestamp or state.stage not in PROCESSING_STAGES:
            return
        if received_chunks is not None:
            state.received_chunks = received_chunks
        if state.stage != stage:
            _transition(state, stage, message)


def fail(timestamp, message):
    with SessionLocal.begin() as session:
        state = _state(session)
        if state.recording_timestamp == timestamp and state.stage in PROCESSING_STAGES:
            state.error = message
            _transition(state, "failed", message)


def complete_in_session(session, timestamp, person_id):
    state = _state(session)
    if state.recording_timestamp != timestamp or state.stage != "saving":
        raise ValueError("The voice setup is no longer active for this recording.")
    state.person_id = person_id
    state.error = None
    _transition(state, "complete", "Your voice profile is saved. Setup is complete.")
