"""Tiny SQLite key/value cache.

Every external call (You.com, Parasail, scenario feed) is recorded here keyed by a hash of
the request. With VIGIL_OFFLINE=1 the clients serve from the cache only, which is what lets
the command station open and replay with no network at all.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class Cache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, ns TEXT, v TEXT, created REAL)"
        )
        self._conn.commit()

    @staticmethod
    def key(namespace: str, payload: Any) -> str:
        blob = json.dumps(payload, sort_keys=True, default=str).encode()
        return namespace + ":" + hashlib.sha256(blob).hexdigest()[:40]

    def get(self, k: str) -> Any | None:
        with self._lock:
            row = self._conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, k: str, value: Any, namespace: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO kv (k, ns, v, created) VALUES (?,?,?,?)",
                (k, namespace, json.dumps(value, default=str), time.time()),
            )
            self._conn.commit()

    def count(self, namespace: str | None = None) -> int:
        with self._lock:
            if namespace:
                return self._conn.execute("SELECT COUNT(*) FROM kv WHERE ns=?", (namespace,)).fetchone()[0]
            return self._conn.execute("SELECT COUNT(*) FROM kv").fetchone()[0]


_cache: Cache | None = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        from .config import settings

        _cache = Cache(settings.cache_path)
    return _cache


class OfflineMiss(RuntimeError):
    """Raised when VIGIL_OFFLINE=1 and a request is not in the cache."""
