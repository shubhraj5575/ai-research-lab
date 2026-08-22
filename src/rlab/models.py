"""Core domain models.

Plain dataclasses with explicit ``to_dict``/``from_dict`` so every artifact we
persist is a stable, versioned JSON document. No ORM magic: the store layer
maps these onto SQLite explicitly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------
class HypothesisStatus(StrEnum):
    PROPOSED = "proposed"
    TESTING = "testing"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"
    SUPERSEDED = "superseded"


class ExperimentStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class CritiqueVerdict(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"
    REJECT = "reject"


class OriginKind(StrEnum):
    INITIAL = "initial"
    LIT_GAP = "literature_gap"
    PRIOR_RESULT = "prior_result"
    CRITIC = "critic"
    DIRECTOR = "director"


def _now() -> float:
    return time.time()


# --------------------------------------------------------------------------
# Literature
# --------------------------------------------------------------------------
@dataclass
class Source:
    id: str
    session_id: str
    kind: str                      # "arxiv" | "seed_corpus"
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    url: str | None = None
    abstract: str = ""
    tags: list[str] = field(default_factory=list)     # extracted keyphrases
    relevance: float | None = None                    # 0..1 vs research question
    fetched_at: float = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class ResearchGap:
    id: str
    session_id: str
    description: str
    evidence_source_ids: list[str] = field(default_factory=list)
    score: float = 0.0             # heuristic priority for the director
    created_at: float = field(default_factory=_now)


# --------------------------------------------------------------------------
# Hypotheses
# --------------------------------------------------------------------------
@dataclass
class Hypothesis:
    id: str
    session_id: str
    number: int                                    # 1-based within session
    claim: str                                     # what we believe
    reasoning: str                                 # why we believe it a priori
    expected_result: str                           # quantitative expectation
    falsification_condition: str                   # what observation would kill it
    required_experiment: str                       # human-readable experiment contract
    origin: OriginKind = OriginKind.INITIAL
    parent_experiment_id: str | None = None        # experiment that motivated it
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    confidence: float | None = None                # post-analysis posterior-ish score
    resolution_note: str = ""
    # structured prediction used for automated falsification checks:
    predicted_variant: str | None = None   # variant label expected to win
    predicted_metric: str | None = None    # metric name it should win on
    created_at: float = field(default_factory=_now)
    resolved_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["origin"] = str(self.origin)
        d["status"] = str(self.status)
        return d


# --------------------------------------------------------------------------
# Experiments & runs
# --------------------------------------------------------------------------
@dataclass
class ExperimentConfig:
    """Canonical, hashable description of one experiment.

    ``domain`` names the registered domain plugin. ``task`` selects the
    environment/problem family inside the domain. ``variants`` are the
    configurations being compared; each is {name: {param: value}}.
    ``baseline`` names the variant others are compared against.
    """

    domain: str
    task: str
    variants: dict[str, dict[str, Any]]
    baseline: str
    n_seeds: int
    seed_root: int
    budget_label: str = ""         # free-form, e.g. "T=5000 steps"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Experiment:
    id: str
    session_id: str
    hypothesis_id: str
    iteration: int
    config: ExperimentConfig
    spec_hash: str                 # sha256 of canonical config JSON
    code_version: str              # template bundle content hash
    git_commit: str                # HEAD at submission time ("unknown" if absent)
    env_json: dict[str, Any]       # python/platform/numpy snapshot
    dataset_ref: dict[str, Any]    # how synthetic data is derived (params+seeds)
    status: ExperimentStatus = ExperimentStatus.PENDING
    error: str = ""
    wall_ms: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    artifact_dir: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["config"] = self.config.to_dict()
        d["status"] = str(self.status)
        return d


@dataclass
class RunResult:
    """Outcome of ONE seeded repetition of an experiment variant."""

    id: str
    experiment_id: str
    variant: str
    seed: int
    metrics: dict[str, float]
    series: dict[str, list[float]] = field(default_factory=dict)  # e.g. regret curve
    wall_ms: float = 0.0
    status: str = "ok"             # ok | failed
    error: str = ""
    result_hash: str = ""          # hash of canonical metrics json

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# --------------------------------------------------------------------------
# Analysis & critique
# --------------------------------------------------------------------------
@dataclass
class Comparison:
    variant_a: str
    variant_b: str
    metric: str
    n_a: int
    n_b: int
    mean_a: float
    mean_b: float
    delta: float                                  # mean_b - mean_a
    ci_low: float                                 # bootstrap CI of delta
    ci_high: float
    p_value: float                                # Welch / paired as appropriate
    test: str                                     # "welch_t" | "paired_t" | "mann_whitney_u"
    effect_size: float                            # Cohen's d
    significant: bool                             # after correction
    better: str                                   # which variant is better on this metric


@dataclass
class Analysis:
    id: str
    experiment_id: str
    primary_metric: str
    direction: str                                 # "minimize" | "maximize"
    comparisons: list[Comparison] = field(default_factory=list)
    summary: str = ""
    best_variant: str = ""
    ranking: list[tuple[str, float]] = field(default_factory=list)  # [(variant, mean_metric)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "experiment_id": self.experiment_id,
            "primary_metric": self.primary_metric,
            "direction": self.direction,
            "comparisons": [c.__dict__.copy() for c in self.comparisons],
            "summary": self.summary,
            "best_variant": self.best_variant,
            "ranking": [[v, m] for v, m in self.ranking],
        }


@dataclass
class CritiqueFinding:
    code: str                     # machine-readable, e.g. "SMALL_SAMPLE"
    severity: str                 # "info" | "minor" | "major" | "blocker"
    message: str
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Critique:
    id: str
    experiment_id: str
    hypothesis_id: str
    verdict: CritiqueVerdict
    issues: list[CritiqueFinding] = field(default_factory=list)
    text: str = ""                # human-readable argumentation
    repro_check_passed: bool | None = None      # deterministic rerun verification
    created_at: float = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "experiment_id": self.experiment_id,
            "hypothesis_id": self.hypothesis_id,
            "verdict": str(self.verdict),
            "issues": [i.to_dict() for i in self.issues],
            "text": self.text,
            "repro_check_passed": self.repro_check_passed,
            "created_at": self.created_at,
        }
