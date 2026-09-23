from contextlib import asynccontextmanager
from io import BytesIO
from json import dumps, loads
import logging
from threading import Lock
from urllib.parse import quote

import qrcode
import qrcode.image.svg
from config import settings
from Recording import Recording
from db.database import initialize_database, SessionLocal
from db.models import Recording as StoredRecording
from explorer_api import router as explorer_router
import setup_service
from encryption import (
    create_encryption_keys,
    get_public_key,
    key_exists,
    private_key_host_path,
    private_key_path,
)
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, model_validator
from processing_queue import ProcessingQueue
from sqlalchemy import select


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await run_in_threadpool(initialize_database)
    await run_in_threadpool(setup_service.initialize_setup)
    worker = ProcessingQueue(process_recording, finish_recording)
    worker.start()
    _app.state.processing_queue = worker
    try:
        yield
    finally:
        await run_in_threadpool(worker.close)
        with upload_lock:
            recordings.clear()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(explorer_router)

upload_events = []
recordings = {}
processing_recordings = set()
upload_lock = Lock()
upload_events_lock = Lock()
logger = logging.getLogger(__name__)

class PingRequest(BaseModel):
    name: str

class UploadChunkRequest(BaseModel):
    timestamp: int = Field(gt=0, le=2**63 - 1)
    totalChunks: int = Field(ge=1, le=20000)
    chunkIndex: int = Field(ge=0)
    encryptedSerializedSymmetricKey: str = Field(min_length=1)
    data: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_chunk_index(self):
        if self.chunkIndex >= self.totalChunks:
            raise ValueError("chunkIndex must be smaller than totalChunks")
        return self


class SetupStepRequest(BaseModel):
    step: str


class LanguageSettingsRequest(BaseModel):
    expectedLanguages: list[str] = Field(min_length=1, max_length=100)


class QrCodeRequest(BaseModel):
    text: str


def model_to_dict(model: BaseModel):
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def model_to_json(model: BaseModel):
    if hasattr(model, "model_dump_json"):
        return model.model_dump_json()
    return model.json()


def remember_upload_event(event):
    with upload_events_lock:
        upload_events.insert(0, event)
        del upload_events[10:]

def public_key_deep_link(public_key: str):
    return f"voxvault://setup?key={quote(public_key, safe='')}"


def public_key_response(request: Request):
    key = get_public_key()
    return {
        "privateKeyPath": private_key_path(),
        "privateKeyHostPath": private_key_host_path(),
        "publicKey": key,
        "publicKeyDeepLink": public_key_deep_link(key),
    }


@app.get("/")
async def root():
    return RedirectResponse("/ui")


@app.get("/ui", include_in_schema=False)
async def ui():
    return FileResponse("static/index.html", headers={"Cache-Control": "no-store"})


@app.get("/ui/explorer", include_in_schema=False)
async def explorer_ui():
    return FileResponse("static/explorer.html", headers={"Cache-Control": "no-store"})


@app.get("/api/setup/status")
def setup_status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {**setup_service.get_status(), "keysExist": key_exists(),
            "publicUrl": settings.VOXVAULT_PUBLIC_URL}


@app.post("/api/setup/step")
def setup_step(body: SetupStepRequest):
    if not key_exists():
        raise HTTPException(status_code=409, detail="Create the server keys first.")
    try:
        with upload_lock:
            previous = setup_service.get_status()
            if previous["recordingTimestamp"] in processing_recordings:
                raise ValueError("Processing is finishing. Please try again in a moment.")
            setup_service.advance(body.step)
            if previous["stage"] == "failed":
                recordings.pop(previous["recordingTimestamp"], None)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return setup_service.get_status()


@app.get("/api/setup/languages")
def setup_languages():
    return {"languages": setup_service.available_languages()}


@app.put("/api/setup/languages")
def update_languages(body: LanguageSettingsRequest):
    try:
        languages = setup_service.set_expected_languages(body.expectedLanguages)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"expectedLanguages": languages}


@app.get("/setup", include_in_schema=False)
async def setup_deep_link(key: str):
    deep_link = public_key_deep_link(key)
    return HTMLResponse(f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Open VoxVault</title>
  </head>
  <body>
    <p>Opening VoxVault...</p>
    <p><a href="{deep_link}">Open VoxVault</a></p>
    <script>
      window.location.href = {dumps(deep_link)};
    </script>
  </body>
</html>""")


@app.get("/api/keys/status")
async def key_status():
    return {
        "keysExist": key_exists(),
        "privateKeyPath": private_key_path(),
        "privateKeyHostPath": private_key_host_path(),
    }


@app.post("/api/keys/create")
async def create_keys(request: Request):
    keys = create_encryption_keys()
    keys["publicKeyDeepLink"] = public_key_deep_link(keys["publicKey"])
    return keys


@app.get("/api/keys/public")
async def public_key(request: Request):
    if not key_exists():
        raise HTTPException(status_code=404, detail="Keys have not been created yet")

    return public_key_response(request)


@app.get("/api/keys/qrcode")
async def public_key_qr(request: Request):
    if not key_exists():
        raise HTTPException(status_code=404, detail="Keys have not been created yet")

    qr = qrcode.make(
        public_key_response(request)["publicKeyDeepLink"],
        image_factory=qrcode.image.svg.SvgPathImage,
    )
    stream = BytesIO()
    qr.save(stream)
    return Response(
        content=stream.getvalue(),
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/qrcode")
async def qr_code(request: QrCodeRequest):
    qr = qrcode.make(request.text, image_factory=qrcode.image.svg.SvgPathImage)
    stream = BytesIO()
    qr.save(stream)
    return Response(content=stream.getvalue(), media_type="image/svg+xml")


@app.post("/ping")
async def print_ping(ping: PingRequest):
    print(f"Received ping: {ping.name}", flush=True)
    return {"message": f"Hello {ping.name}"}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = [{key: error[key] for key in ("type", "loc", "msg") if key in error}
              for error in exc.errors()]
    if request.url.path == "/upload":
        body_text = (await request.body()).decode("utf-8", errors="replace")
        try:
            body = loads(body_text)
        except ValueError:
            body = body_text

        event = {
            "status": "rejected",
            "errors": errors,
        }
        remember_upload_event(event)
        if isinstance(body, dict) and isinstance(body.get("timestamp"), int):
            await run_in_threadpool(
                setup_service.fail, body["timestamp"],
                "An upload chunk was invalid. Please send the recording again.",
            )

    return JSONResponse(
        status_code=422,
        content={"detail": errors},
    )


@app.get("/api/uploads")
async def uploads():
    with upload_events_lock:
        return {"uploads": list(upload_events)}


@app.get("/api/uploads/{timestamp}")
def upload_status(timestamp: int, response: Response):
    response.headers["Cache-Control"] = "no-store"
    with upload_lock:
        if timestamp in processing_recordings:
            return {"complete": True, "receivedChunks": []}
        with SessionLocal() as session:
            if session.scalar(select(StoredRecording.id).where(StoredRecording.timestamp == timestamp)):
                return {"complete": True, "receivedChunks": []}
        recording = recordings.get(timestamp)
        if recording is None:
            return {"complete": False, "receivedChunks": []}
        return {"complete": False, "totalChunks": recording.total_chunks,
                "receivedChunks": sorted(recording.chunks)}


def process_recording(job):
    timestamp, enrollment = job.timestamp, job.enrollment
    logger.info("Processing recording %s", timestamp)
    try:
        import audio_processer

        processed = audio_processer.process_audio_bytes(
            job.path.read_bytes(), timestamp, enrollment=enrollment,
            progress=lambda stage, message: setup_service.update_progress(timestamp, stage, message)
            if enrollment else None,
        )
        logger.info("Finished recording %s: %s words", timestamp, len(processed.words))
        remember_upload_event({"status": "processed", "timestamp": timestamp})
    except Exception as exc:
        from ml_models import RecordingSkipped

        skipped = isinstance(exc, RecordingSkipped)
        if skipped:
            logger.info("Skipping recording %s: %s", timestamp, exc)
        else:
            logger.exception("Recording %s could not be processed", timestamp)
        event = {"status": "skipped" if skipped else "failed", "timestamp": timestamp}
        if skipped:
            event["reason"] = str(exc)
        remember_upload_event(event)
        message = (
            str(exc) if isinstance(exc, ValueError)
            else "Processing failed. Check the server logs and model access, then send the recording again."
        )
        if enrollment:
            setup_service.fail(timestamp, message)


def finish_recording(timestamp):
    with upload_lock:
        processing_recordings.discard(timestamp)


@app.post("/upload")
def upload(chunk: UploadChunkRequest, request: Request):
    if not key_exists():
        raise HTTPException(status_code=409, detail="Create the server keys first.")

    with upload_lock:
        status = setup_service.get_status()
        if status["stage"] == "failed" and status["recordingTimestamp"] == chunk.timestamp:
            raise HTTPException(status_code=409, detail="Choose a retry option in the setup page before uploading again.")
        if chunk.timestamp in processing_recordings:
            return {"message": "Recording is already being processed.", "recordingComplete": True}
        with SessionLocal() as session:
            if session.scalar(select(StoredRecording.id).where(StoredRecording.timestamp == chunk.timestamp)):
                return {"message": "Recording is already saved.", "recordingComplete": True}

        enrollment = setup_service.claim_upload(chunk.timestamp, chunk.totalChunks)
        try:
            recording = recordings.get(chunk.timestamp)
            if recording is None:
                recording = Recording(chunk.timestamp, chunk.totalChunks, chunk.encryptedSerializedSymmetricKey)
                recordings[chunk.timestamp] = recording
            if (recording.total_chunks != chunk.totalChunks
                    or recording.unprocessed_symmetric_key != chunk.encryptedSerializedSymmetricKey):
                raise ValueError("Recording metadata changed during upload.")
            recording.add_chunk(chunk.chunkIndex, chunk.data)
            if enrollment:
                setup_service.update_progress(
                    chunk.timestamp, "receiving", "Receiving and decrypting your recording.",
                    received_chunks=len(recording.chunks),
                )
            complete = recording.stitch()
        except Exception as exc:
            recordings.pop(chunk.timestamp, None)
            logger.warning("Could not decrypt recording %s: %s", chunk.timestamp, type(exc).__name__)
            if enrollment:
                setup_service.fail(chunk.timestamp, "The recording could not be decrypted. Check the public key on your phone and try again.")
            raise HTTPException(status_code=400, detail="Invalid recording data or encryption key.") from exc

        remember_upload_event({"status": "accepted", "timestamp": chunk.timestamp,
                               "chunkIndex": chunk.chunkIndex, "totalChunks": chunk.totalChunks})
        if complete:
            if enrollment:
                setup_service.update_progress(chunk.timestamp, "validating", "Recording received. Waiting to check the audio.")
            processing_recordings.add(chunk.timestamp)
            try:
                request.app.state.processing_queue.enqueue(
                    recording.complete, chunk.timestamp, enrollment,
                )
            except Exception as exc:
                processing_recordings.discard(chunk.timestamp)
                recordings.pop(chunk.timestamp, None)
                logger.exception("Could not queue recording %s", chunk.timestamp)
                if enrollment:
                    setup_service.fail(chunk.timestamp, "Could not queue the recording. Please send it again.")
                raise HTTPException(status_code=503, detail="Could not queue the recording. Please retry.") from exc
            del recordings[chunk.timestamp]

    return {"message": f"received : {len(chunk.data)}", "recordingComplete": complete}
