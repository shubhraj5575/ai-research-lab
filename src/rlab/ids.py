"""Short, sortable, collision-resistant identifiers."""

from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # Crockford-ish, no confusing chars
_PREFIX = {
    "session": "rs",
    "hypothesis": "hy",
    "experiment": "ex",
    "run": "rn",
    "analysis": "an",
    "critique": "cr",
    "source": "sr",
    "gap": "gp",
    "event": "ev",
    "artifact": "ar",
}


def _entropy(n: int = 8) -> str:
    # 40 bits of randomness: collision-safe for our volumes.
    return "".join(_ALPHABET[b % len(_ALPHABET)] for b in secrets.token_bytes(n))


def new_id(kind: str) -> str:
    """Return e.g. ``ex_k3v9m2q7``. Unknown kinds get no prefix."""
    prefix = _PREFIX.get(kind)
    body = f"{int(time.time() * 1000) % 10**10:010d}{_entropy()}"[-12:]
    return f"{prefix}_{body}" if prefix else _entropy(12)


def short_hash(data: str | bytes, n: int = 16) -> str:
    import hashlib

    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:n]
