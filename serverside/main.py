from io import BytesIO
from json import dumps, loads
from os import getenv
from pathlib import Path
import subprocess
from urllib.parse import quote
import base64

import qrcode
import qrcode.image.svg
from encryption import (
    create_encryption_keys,
    get_public_key,
    key_exists,
    private_key_host_path,
    private_key_path,
    decrypt_data,
)
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, conint

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

ShortInt = conint(ge=-32768, le=32767)
upload_events = []
SERVER_DEBUG_VERSION = "upload-debug-2026-09-03-1"
DEBUG_AUDIO_DIR = Path(getenv("VOXVAULT_DEBUG_AUDIO_DIR", "debug_audio"))
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


def save_debug_mp3(pcm_bytes: bytes, timestamp: int, chunk_index: int) -> str:
    DEBUG_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DEBUG_AUDIO_DIR / f"{timestamp}_chunk_{chunk_index:03d}.mp3"

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "s16le",
            "-ar",
            str(PCM_SAMPLE_RATE),
            "-ac",
            str(PCM_CHANNELS),
            "-i",
            "pipe:0",
            "-codec:a",
            "libmp3lame",
            str(output_path),
        ],
        input=pcm_bytes,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    return str(output_path)


def public_key_deep_link(public_key: str):
    return f"voxvault://setup?key={quote(public_key, safe='')}"


def public_key_intent_link(public_key: str):
    encoded_key = quote(public_key, safe="")
    return (
        "intent://setup"
        f"?key={encoded_key}"
        "#Intent;scheme=voxvault;package=com.paganini.voxvault;end"
    )


def public_base_url():
    base_url = getenv("VOXVAULT_PUBLIC_BASE_URL", "").strip()
    return base_url.rstrip("/")


def public_key_qr_link(request: Request, public_key: str):
    base_url = public_base_url()

    if not base_url:
        return public_key_deep_link(public_key)

    return f"{base_url}/setup?key={quote(public_key, safe='')}"


def public_key_response(request: Request):
    key = get_public_key()
    return {
        "privateKeyPath": private_key_path(),
        "privateKeyHostPath": private_key_host_path(),
        "publicKey": key,
        "publicKeyDeepLink": public_key_deep_link(key),
        "publicKeyIntentLink": public_key_intent_link(key),
        "publicKeyQrLink": public_key_qr_link(request, key),
    }


@app.get("/")
async def root():
    return {"message": "Hello World", "serverDebugVersion": SERVER_DEBUG_VERSION}


@app.get("/ui", include_in_schema=False)
async def ui():
    return FileResponse("static/index.html")


@app.get("/setup", include_in_schema=False)
async def setup_deep_link(key: str):
    intent_link = public_key_intent_link(key)
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
      window.location.href = {dumps(intent_link)};
      setTimeout(() => {{
        window.location.href = {dumps(deep_link)};
      }}, 600);
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
    keys["publicKeyIntentLink"] = public_key_intent_link(keys["publicKey"])
    keys["publicKeyQrLink"] = public_key_qr_link(request, keys["publicKey"])
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
        public_key_response(request)["publicKeyQrLink"],
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
        "serverDebugVersion": SERVER_DEBUG_VERSION,
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

    decoded_encrypted_data = base64.b64decode(chunk.data)
    decoded_symmetric_encryption_key = base64.b64decode(chunk.encryptedSerializedSymmetricKey)
    # 1. decrypt the decoded_encrypted_data
    decrypted_pcm_bytes = decrypt_data(
        decoded_encrypted_data,
        decoded_symmetric_encryption_key,
    )
    debug_mp3_path = save_debug_mp3(
        decrypted_pcm_bytes,
        chunk.timestamp,
        chunk.chunkIndex,
    )
    #2. store the decrypted Data temporarily, until all chunks have been received.
    # some custom data-type ideally
    #3. if the custom-data-type is complete, put all chunks together
    #4. convert complete object to text and store it
    #5. extract voice-embeddings
    return {
        "message": f"received : {len(chunk.data)}",
        "debugMp3Path": debug_mp3_path,
    }
