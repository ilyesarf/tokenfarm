import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import db
import farm as sim

MAIN = "main"
SESSION_TTL = 300
WARN_AT = 60
SWEEP = 5
MAX_WORLDS = 24

ADMIN_KEY = os.getenv("TOKENFARM_ADMIN_KEY")
PACE = float(os.getenv("TOKENFARM_SECONDS_PER_HOUR", "5"))
INDEX = Path(__file__).parent / "index.html"

LOCK = asyncio.Lock()
SUBSCRIBERS = {}


@dataclass
class World:
    farm: sim.Farm
    ticker: asyncio.Task = None
    active: float = field(default_factory=time.monotonic)


WORLDS = {}


def spawn(world_id, config):
    WORLDS[world_id] = World(sim.Farm(config))
    WORLDS[world_id].ticker = asyncio.create_task(clock(world_id))
    save(world_id)
    return WORLDS[world_id]


def restore(world_id, blob):
    WORLDS[world_id] = World(sim.load(json.loads(blob)))
    WORLDS[world_id].ticker = asyncio.create_task(clock(world_id))
    return WORLDS[world_id]


def save(world_id):
    db.save(world_id, json.dumps(sim.dump(WORLDS[world_id].farm)))


def pick(world_id):
    world = WORLDS.get(world_id)
    if world is None:
        raise HTTPException(404, "that sim is gone")
    return world


def act(world_id):
    WORLDS[world_id].active = time.monotonic()


def life_left(world_id):
    world = WORLDS.get(world_id)
    if world_id == MAIN or world is None:
        return None
    return max(0, round(SESSION_TTL - (time.monotonic() - world.active)))


def deliver(world_id, choose):
    for queue, (wid, detail) in list(SUBSCRIBERS.items()):
        if wid != world_id:
            continue
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(choose(detail))


def publish(world_id, cursor):
    farm = WORLDS[world_id].farm
    events = sim.since(farm, cursor)
    payloads = {
        detail: {
            "world": world_id,
            "events": events,
            "state": sim.snapshot(farm, detail),
            "expires_in": life_left(world_id),
        }
        for detail in (False, True)
    }
    deliver(world_id, payloads.__getitem__)
    return payloads


async def clock(world_id):
    due = time.monotonic()
    while True:
        world = WORLDS.get(world_id)
        if world is None:
            return
        delay = world.farm.cfg.seconds_per_hour
        due += delay
        now = time.monotonic()
        if not now <= due <= now + delay * 2:
            due = now + delay
        await asyncio.sleep(due - now)
        async with LOCK:
            world = WORLDS.get(world_id)
            if world is None:
                return
            cursor = world.farm.cursor
            sim.tick(world.farm)
            save(world_id)
            publish(world_id, cursor)


async def janitor():
    while True:
        await asyncio.sleep(SWEEP)
        async with LOCK:
            for world_id in [name for name in WORLDS if name != MAIN]:
                left = life_left(world_id)
                if left:
                    if left <= WARN_AT:
                        deliver(world_id, lambda _: {"world": world_id, "expires_in": left})
                    continue
                WORLDS.pop(world_id).ticker.cancel()
                db.delete(world_id)
                deliver(world_id, lambda _: {"world": world_id, "gone": True})
                deliver(world_id, lambda _: None)


@asynccontextmanager
async def lifespan(app):
    saved = db.load_all()
    if MAIN in saved:
        restore(MAIN, saved.pop(MAIN))
    else:
        spawn(MAIN, sim.configure({"seconds_per_hour": PACE}))
    for world_id, blob in saved.items():
        restore(world_id, blob)
    sweeper = asyncio.create_task(janitor())
    yield
    sweeper.cancel()
    for world in WORLDS.values():
        world.ticker.cancel()


app = FastAPI(title="tokenfarm", lifespan=lifespan)


class Command(BaseModel):
    text: str


def admin(key):
    if ADMIN_KEY and key != ADMIN_KEY:
        raise HTTPException(403, "locked, send X-Admin-Key")


def build(values):
    try:
        return sim.configure(values)
    except sim.CommandError as error:
        raise HTTPException(400, str(error))


def settings(world_id):
    farm = WORLDS[world_id].farm
    return {
        "world": world_id,
        "shared": world_id == MAIN,
        "expires_in": life_left(world_id),
        "config": asdict(farm.cfg),
        "limits": sim.LIMITS,
        "rules": sim.rules(farm.cfg),
    }


@app.get("/")
async def index():
    return FileResponse(INDEX)


@app.get("/api/state")
async def state(world: str = MAIN, view: str = "agent"):
    async with LOCK:
        return sim.snapshot(pick(world).farm, view == "human")


@app.get("/api/events")
async def events(world: str = MAIN, since: int = 0):
    async with LOCK:
        farm = pick(world).farm
        return {"events": sim.since(farm, since), "cursor": farm.cursor}


@app.post("/api/command")
async def command(payload: Command, world: str = MAIN, view: str = "agent"):
    async with LOCK:
        farm = pick(world).farm
        cursor, revision = farm.cursor, farm.revision
        ok, message = sim.run(farm, payload.text)
        if farm.revision != revision:
            act(world)
            save(world)
        return {"ok": ok, "message": message, **publish(world, cursor)[view == "human"]}


@app.get("/api/config")
async def read_config(world: str = MAIN, x_admin_key: str = Header(None)):
    admin(x_admin_key)
    async with LOCK:
        pick(world)
        return settings(world)


@app.post("/api/worlds")
async def create_world(values: dict, x_admin_key: str = Header(None)):
    admin(x_admin_key)
    config = build(values)
    async with LOCK:
        if len(WORLDS) - 1 >= MAX_WORLDS:
            raise HTTPException(429, "too many sims running, try again shortly")
        world_id = uuid4().hex[:8]
        spawn(world_id, config)
        return settings(world_id)


@app.post("/api/config")
async def write_config(values: dict, world: str = MAIN, x_admin_key: str = Header(None)):
    admin(x_admin_key)
    config = build(values)
    async with LOCK:
        pick(world).farm = sim.Farm(config)
        act(world)
        save(world)
        publish(world, 0)
        return settings(world)


@app.post("/api/restart")
async def restart(world: str = MAIN, x_admin_key: str = Header(None)):
    admin(x_admin_key)
    async with LOCK:
        pick(world).farm.reset()
        act(world)
        save(world)
        publish(world, 0)
        return settings(world)


@app.post("/api/keepalive")
async def keepalive(world: str = MAIN, x_admin_key: str = Header(None)):
    admin(x_admin_key)
    async with LOCK:
        pick(world)
        act(world)
        return settings(world)


@app.get("/api/stream")
async def stream(world: str = MAIN, view: str = "agent"):
    detail = view == "human"
    queue = asyncio.Queue(maxsize=16)

    async def feed():
        try:
            async with LOCK:
                farm = pick(world).farm
                SUBSCRIBERS[queue] = (world, detail)
                opening = {
                    "world": world,
                    "events": [],
                    "state": sim.snapshot(farm, detail),
                    "expires_in": life_left(world),
                }
            yield f"data: {json.dumps(opening)}\n\n"
            while True:
                payload = await queue.get()
                if payload is None:
                    return
                yield f"data: {json.dumps(payload)}\n\n"
        finally:
            SUBSCRIBERS.pop(queue, None)

    return StreamingResponse(feed(), media_type="text/event-stream")
