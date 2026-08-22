"""Domain registry: computational research areas the lab can explore."""

from __future__ import annotations

from .bandit.domain import BanditDomain
from .base import DomainPlugin, HypothesisDraft, Knob
from .optim.domain import OptimDomain

_REGISTRY: dict[str, type[DomainPlugin]] = {
    BanditDomain.name: BanditDomain,
    OptimDomain.name: OptimDomain,
}


def get_domain(name: str) -> DomainPlugin:
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise KeyError(
            f"unknown domain {name!r}; available: {sorted(_REGISTRY)}"
        ) from None


def list_domains() -> list[str]:
    return sorted(_REGISTRY)


__all__ = ["DomainPlugin", "Knob", "HypothesisDraft", "get_domain", "list_domains"]
