from __future__ import annotations

import hashlib
import json
import time
from typing import Any


class DebateCache:
    def __init__(self, *, ttl_seconds: int) -> None:
        self.ttl = ttl_seconds
        self._store: dict[str, tuple[float, dict[str, Any]]] = {}

    def make_key(self, *, task: str, files: dict[str, str], mode: str) -> str:
        payload = json.dumps(
            {"task": task, "mode": mode, "files": sorted(files.items())},
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, payload = entry
        if time.time() - ts > self.ttl:
            del self._store[key]
            return None
        return payload

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._store[key] = (time.time(), value)
