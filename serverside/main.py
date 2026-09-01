from fastapi import FastAPI
from pydantic import BaseModel, conint

app = FastAPI()

ShortInt = conint(ge=-32768, le=32767)

class PingRequest(BaseModel):
    name: str

class uploadChunkRequest(BaseModel):
    timestamp: int
    totalChunks: int
    chunkIndex: int
    data: list[ShortInt]

@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.post("/ping")
async def print_ping(ping: PingRequest):
    print(f"Received ping: {ping.name}", flush=True)
    return {"message": f"Hello {ping.name}"}


@app.post("/upload")
async def upload(chunk: uploadChunkRequest):
    print(f"Received upload: {chunk.timestamp}, len:{len(chunk.data)}", flush=True)
    return {f"received : {len(chunk.data)}"}