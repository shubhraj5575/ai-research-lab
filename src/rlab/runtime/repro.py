"""Canonical hashing and deterministic seed derivation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np


def canonical_json(obj: Any) -> str:
    """Stable serialization: sorted keys, no whitespace, floats via repr."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_default)


def _default(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    raise TypeError(f"not serializable: {type(obj)}")


def spec_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over the canonical form of an experiment configuration."""
    data = canonical_json(payload).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def derive_seed(seed_root: int, repetition: int) -> int:
    """Deterministic child seed from (root, repetition index).

    Deliberately independent of the variant index: all variants of one
    experiment share the same seed set, giving *common random numbers* —
    identical synthetic environments across variants — which enables paired
    statistical comparisons inside the analyst.
    """
    ss = np.random.SeedSequence(
        entropy=int(seed_root),
        spawn_key=(int(repetition),),
    )
    return int(ss.generate_state(1, dtype=np.uint32)[0])
