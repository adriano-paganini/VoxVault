from contextlib import asynccontextmanager
from io import BytesIO
from json import dumps, loads
from os import getenv
from pathlib import Path
import subprocess
from threading import Thread
from urllib.parse import quote

import qrcode
import qrcode.image.svg
from dotenv import load_dotenv

load_dotenv()
import audio_processer

from Recording import Recording
from db.database import initialize_database
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
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, conint


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

ShortInt = conint(ge=-32768, le=32767)
upload_events = []
recordings = {}
PCM_SAMPLE_RATE = 16000
PCM_CHANNELS = 1

class PingRequest(BaseModel):
    name: str

class UploadChunkRequest(BaseModel):
    timestamp: int
    totalChunks: int
    chunkIndex: int
    encryptedSerializedSymmetricKey: str
    data: str


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
    return {"message": "Hello World"}


@app.get("/ui", include_in_schema=False)
async def ui():
    return FileResponse("static/index.html")


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
    if request.url.path == "/upload":
        body_text = (await request.body()).decode("utf-8", errors="replace")
        try:
            body = loads(body_text)
        except ValueError:
            body = body_text

        event = {
            "status": "rejected",
            "errors": exc.errors(),
            "body": body,
        }
        remember_upload_event(event)
        print(f"Rejected upload: {dumps(event)}", flush=True)

    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


@app.get("/api/uploads")
async def uploads():
    return {
        "uploads": upload_events,
    }


@app.post("/upload")
async def upload(chunk: UploadChunkRequest):
    upload_json = model_to_json(chunk)
    remember_upload_event({
        "status": "accepted",
        "body": model_to_dict(chunk),
    })
    print(f"Received upload: {upload_json}", flush=True)

    recording = recordings.get(chunk.timestamp)
    if recording is None:
        recording = Recording(
            chunk.timestamp,
            chunk.totalChunks,
            chunk.encryptedSerializedSymmetricKey,
        )
        recordings[chunk.timestamp] = recording

    recording.add_chunk(chunk.chunkIndex, chunk.data)

    if recording.stitch():
        Thread(
            target=audio_processer.process_audio_bytes,
            args=(recording.complete, chunk.timestamp),
            daemon=True,
        ).start()
        del recordings[chunk.timestamp]

    return {
        "message": f"received : {len(chunk.data)}",
    }
