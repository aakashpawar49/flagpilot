"""Tiny SQLite store: each run is one JSON document."""

import sqlite3
import threading
from pathlib import Path

from .models import Run, now


class RunStore:
    def __init__(self, data_dir: str) -> None:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(Path(data_dir) / "flagpilot.db", check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            self._db.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            self._db.commit()

    def save(self, run: Run) -> Run:
        run.updated_at = now()
        with self._lock:
            self._db.execute(
                "INSERT INTO runs (id, data, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                (run.id, run.model_dump_json(), run.updated_at.isoformat()),
            )
            self._db.commit()
        return run

    def get(self, run_id: str) -> Run | None:
        with self._lock:
            row = self._db.execute("SELECT data FROM runs WHERE id = ?", (run_id,)).fetchone()
        return Run.model_validate_json(row[0]) if row else None

    def list(self) -> list[Run]:
        with self._lock:
            rows = self._db.execute("SELECT data FROM runs ORDER BY updated_at DESC").fetchall()
        return [Run.model_validate_json(r[0]) for r in rows]

    def get_value(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_value(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._db.commit()
