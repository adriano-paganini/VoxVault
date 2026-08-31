from pydantic import BaseModel
from fastapi import FastAPI

app = FastAPI()


class PingRequest(BaseModel):
    name: str


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.post("/ping")
async def print_ping(ping: PingRequest):
    print(f"Received ping: {ping.name}", flush=True)
    return {"message": f"Hello {ping.name}"}
