import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import farm as sim

SECONDS_PER_HOUR = float(os.getenv("TOKENFARM_SECONDS_PER_HOUR", "5"))
INDEX = Path(__file__).parent / "index.html"

FARM = sim.Farm()
LOCK = asyncio.Lock()
SUBSCRIBERS = {}


def publish(cursor):
    events = sim.since(FARM, cursor)
    payloads = {
        detail: {"events": events, "state": sim.snapshot(FARM, detail)} for detail in (False, True)
    }
    for queue, detail in list(SUBSCRIBERS.items()):
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(payloads[detail])
    return payloads


async def clock():
    due = time.monotonic()
    while True:
        due += SECONDS_PER_HOUR
        await asyncio.sleep(max(0, due - time.monotonic()))
        async with LOCK:
            cursor = FARM.cursor
            sim.tick(FARM)
            publish(cursor)


@asynccontextmanager
async def lifespan(app):
    ticker = asyncio.create_task(clock())
    yield
    ticker.cancel()


app = FastAPI(title="tokenfarm", lifespan=lifespan)


class Command(BaseModel):
    text: str


@app.get("/")
async def index():
    return FileResponse(INDEX)


@app.get("/api/state")
async def state(view: str = "agent"):
    async with LOCK:
        return sim.snapshot(FARM, view == "human")


@app.get("/api/events")
async def events(since: int = 0):
    async with LOCK:
        return {"events": sim.since(FARM, since), "cursor": FARM.cursor}


@app.post("/api/command")
async def command(payload: Command, view: str = "agent"):
    async with LOCK:
        cursor = FARM.cursor
        ok, message = sim.run(FARM, payload.text)
        return {"ok": ok, "message": message, **publish(cursor)[view == "human"]}


@app.get("/api/stream")
async def stream(view: str = "agent"):
    detail = view == "human"
    queue = asyncio.Queue(maxsize=16)
    SUBSCRIBERS[queue] = detail

    async def feed():
        try:
            async with LOCK:
                opening = {"events": [], "state": sim.snapshot(FARM, detail)}
            yield f"data: {json.dumps(opening)}\n\n"
            while True:
                yield f"data: {json.dumps(await queue.get())}\n\n"
        finally:
            SUBSCRIBERS.pop(queue, None)

    return StreamingResponse(feed(), media_type="text/event-stream")
