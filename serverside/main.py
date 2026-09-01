from io import BytesIO
from json import dumps
from urllib.parse import quote

import qrcode
import qrcode.image.svg
from encryption import (
    create_encryption_keys,
    get_public_key,
    key_exists,
    private_key_host_path,
    private_key_path,
)
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
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


def public_key_deep_link(public_key: str):
    return f"voxvault://setup?key={quote(public_key, safe='')}"


def public_key_intent_link(public_key: str):
    encoded_key = quote(public_key, safe="")
    return (
        "intent://setup"
        f"?key={encoded_key}"
        "#Intent;scheme=voxvault;package=com.paganini.voxvault;end"
    )


def public_key_qr_link(request: Request, public_key: str):
    return str(request.url_for("setup_deep_link")).split("?", 1)[0] + (
        f"?key={quote(public_key, safe='')}"
    )


def public_key_response(request: Request):
    key = get_public_key()
    return {
        "privateKeyPath": private_key_path(),
        "privateKeyHostPath": private_key_host_path(),
        "publicKey": key,
        "publicKeyDeepLink": public_key_deep_link(key),
        "publicKeyQrLink": public_key_qr_link(request, key),
    }


@app.get("/")
async def root():
    return {"message": "Hello World"}


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


@app.post("/upload")
async def upload(chunk: UploadChunkRequest):
    print(f"Received upload: {chunk.timestamp}, len:{len(chunk.data)}", flush=True)
    return {f"received : {len(chunk.data)}"}
