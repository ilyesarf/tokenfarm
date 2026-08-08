import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "tokenfarm.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_connection = sqlite3.connect(DB_PATH, check_same_thread=False)
_connection.execute(
    "CREATE TABLE IF NOT EXISTS sims "
    "(world_id TEXT PRIMARY KEY, farm TEXT NOT NULL, public INTEGER NOT NULL)"
)
_connection.commit()


def save(world_id, blob, public):
    _connection.execute(
        "INSERT INTO sims (world_id, farm, public) VALUES (?, ?, ?) "
        "ON CONFLICT(world_id) DO UPDATE SET farm = excluded.farm, public = excluded.public",
        (world_id, blob, int(public)),
    )
    _connection.commit()


def delete(world_id):
    _connection.execute("DELETE FROM sims WHERE world_id = ?", (world_id,))
    _connection.commit()


def load_all():
    rows = _connection.execute("SELECT world_id, farm, public FROM sims")
    return {world_id: (blob, bool(public)) for world_id, blob, public in rows}
