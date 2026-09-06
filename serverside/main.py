from contextlib import asynccontextmanager
from io import BytesIO
from json import dumps, loads
import logging
from os import getenv
from threading import Lock, Thread
from urllib.parse import quote

import qrcode
import qrcode.image.svg
from dotenv import load_dotenv

load_dotenv()
from Recording import Recording
from db.database import initialize_database, SessionLocal
from db.models import Recording as StoredRecording
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
from sqlalchemy import select


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    setup_service.initialize_setup()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

upload_events = []
recordings = {}
processing_recordings = set()
upload_lock = Lock()
processing_lock = Lock()
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


@app.get("/api/setup/status")
def setup_status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {**setup_service.get_status(), "keysExist": key_exists(),
            "publicUrl": getenv("VOXVAULT_PUBLIC_URL", "")}


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
    return {
        "uploads": upload_events,
    }


def process_recording(audio, timestamp, enrollment):
    try:
        # Serialize model inference to bound memory usage in the Docker service.
        with processing_lock:
            import audio_processer

            audio_processer.process_audio_bytes(
                audio, timestamp, enrollment=enrollment,
                progress=lambda stage, message: setup_service.update_progress(timestamp, stage, message)
                if enrollment else None,
            )
    except Exception as exc:
        logger.exception("Recording %s could not be processed", timestamp)
        message = (
            str(exc) if isinstance(exc, ValueError)
            else "Processing failed. Check the server logs and model access, then send the recording again."
        )
        if enrollment:
            setup_service.fail(timestamp, message)
        remember_upload_event({"status": "failed", "timestamp": timestamp})
    finally:
        with upload_lock:
            processing_recordings.discard(timestamp)


@app.post("/upload")
def upload(chunk: UploadChunkRequest):
    if not key_exists():
        raise HTTPException(status_code=409, detail="Create the server keys first.")

    with upload_lock:
        status = setup_service.get_status()
        if status["stage"] == "failed" and status["recordingTimestamp"] == chunk.timestamp:
            raise HTTPException(status_code=409, detail="Choose a retry option in the setup page before uploading again.")
        if chunk.timestamp in processing_recordings:
            return {"message": "Recording is already being processed."}
        with SessionLocal() as session:
            if session.scalar(select(StoredRecording.id).where(StoredRecording.timestamp == chunk.timestamp)):
                return {"message": "Recording is already saved."}

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
            del recordings[chunk.timestamp]
            Thread(target=process_recording,
                   args=(recording.complete, chunk.timestamp, enrollment), daemon=True).start()

    return {"message": f"received : {len(chunk.data)}"}
