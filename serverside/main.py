from io import BytesIO

import qrcode
import qrcode.image.svg
from encryption import (
    create_encryption_keys,
    get_public_key,
    key_exists,
    private_key_host_path,
    private_key_path,
)
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, conint

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

ShortInt = conint(ge=-32768, le=32767)

class PingRequest(BaseModel):
    name: str

class UploadChunkRequest(BaseModel):
    timestamp: int
    totalChunks: int
    chunkIndex: int
    data: list[ShortInt]


class QrCodeRequest(BaseModel):
    text: str


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/ui", include_in_schema=False)
async def ui():
    return FileResponse("static/index.html")


@app.get("/api/keys/status")
async def key_status():
    return {
        "keysExist": key_exists(),
        "privateKeyPath": private_key_path(),
        "privateKeyHostPath": private_key_host_path(),
    }


@app.post("/api/keys/create")
async def create_keys():
    return create_encryption_keys()


@app.get("/api/keys/public")
async def public_key():
    if not key_exists():
        raise HTTPException(status_code=404, detail="Keys have not been created yet")

    return {
        "privateKeyPath": private_key_path(),
        "privateKeyHostPath": private_key_host_path(),
        "publicKey": get_public_key(),
    }


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


@app.post("/upload")
async def upload(chunk: UploadChunkRequest):
    print(f"Received upload: {chunk.timestamp}, len:{len(chunk.data)}", flush=True)
    return {f"received : {len(chunk.data)}"}
