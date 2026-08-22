"""Literature cache: disk-backed TTL cache for provider responses."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from ..ids import short_hash


class DiskCache:
    def __init__(self, root: Path, ttl_s: float = 7 * 24 * 3600):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_s = ttl_s
        self.hits = 0
        self.misses = 0

    def _path(self, namespace: str, key: str) -> Path:
        digest = short_hash(key, n=32)
        return self.root / f"{namespace}_{digest}.json"

    def get(self, namespace: str, key: str):
        path = self._path(namespace, key)
        if not path.exists():
            self.misses += 1
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.misses += 1
            return None
        if time.time() - doc.get("stored_at", 0) > self.ttl_s:
            self.misses += 1
            return None
        self.hits += 1
        return doc.get("payload")

    def put(self, namespace: str, key: str, payload) -> None:
        path = self._path(namespace, key)
        path.write_text(json.dumps({"stored_at": time.time(), "payload": payload}),
                        encoding="utf-8")

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses,
                "entries": len(list(self.root.glob("*.json")))}
