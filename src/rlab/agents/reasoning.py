"""Reasoning backends for agents.

Two honest modes:

``heuristic``  – deterministic rule-based composition. Every sentence is
grounded in structured data handed to the reasoner (analysis numbers, critique
findings, source metadata). No language model involved; output is fully
reproducible. This is the DEFAULT and requires no credentials.

``llm``        – calls Anthropic or OpenAI chat APIs when configured AND an
API key is present in the environment. Used for narrative polish on top of
the same structured data; never used for numeric claims.

Every agent output records which mode produced it so dashboards and papers
can attribute provenance honestly.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReasonedText:
    text: str
    reasoner: str = "heuristic"          # "heuristic" | "llm"
    model: str = ""
    notes: str = ""


class HeuristicReasoner:
    """Template-based composition over caller-supplied facts.

    ``render(template_key, facts)`` returns deterministic prose. The class is
    deliberately boring: its value is that it can never fabricate a number.
    """

    name = "heuristic"

    def render(self, sections: list[tuple[str, str]], lead: str) -> ReasonedText:
        parts = [lead]
        for heading, body in sections:
            if body:
                parts.append(f"{heading}: {body}")
        return ReasonedText(text="\n\n".join(parts), reasoner=self.name)


class LLMReasoner:
    """Optional narrative layer backed by a hosted LLM API.

    Only activates when provider+model are configured and the matching key is
    present in the environment. Failures degrade gracefully: callers receive
    the heuristic text plus an explanatory note.
    """

    name = "llm"

    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model or {
            "anthropic": "claude-3-5-haiku-latest",
            "openai": "gpt-4o-mini",
        }.get(provider, "")

    def available(self) -> bool:
        if self.provider not in ("anthropic", "openai") or not self.model:
            return False
        return bool(self._api_key())

    def _api_key(self) -> str:
        if self.provider == "anthropic":
            return os.environ.get("ANTHROPIC_API_KEY", "")
        if self.provider == "openai":
            return os.environ.get("OPENAI_API_KEY", "")
        return ""

    def narrate(self, instruction: str, facts: dict[str, Any],
                fallback: str) -> ReasonedText:
        if not self.available():
            return ReasonedText(
                text=fallback,
                reasoner="heuristic-fallback",
                notes=f"LLM unavailable (provider={self.provider!r}, key present={bool(self._api_key())})",
            )
        prompt = (
            "You are assisting a research lab report. Use ONLY the facts in the "
            "JSON below. Do not invent numbers. Write 1-2 concise paragraphs.\n\n"
            f"Instruction: {instruction}\n\nFacts:\n{json.dumps(facts, indent=2)}"
        )
        try:
            text = self._call_api(prompt)
            return ReasonedText(text=text, reasoner="llm", model=self.model)
        except Exception as exc:
            return ReasonedText(text=fallback, reasoner="heuristic-fallback",
                                notes=f"LLM call failed: {exc}")

    def _call_api(self, prompt: str) -> str:
        if self.provider == "anthropic":
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps({
                    "model": self.model,
                    "max_tokens": 700,
                    "messages": [{"role": "user", "content": prompt}],
                }).encode(),
                headers={
                    "content-type": "application/json",
                    "x-api-key": self._api_key(),
                    "anthropic-version": "2023-06-01",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read())
            return body["content"][0]["text"]
        # openai-compatible chat completions
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps({
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 700,
            }).encode(),
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {self._api_key()}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read())
        return body["choices"][0]["message"]["content"]


def make_reasoners(cfg) -> tuple[HeuristicReasoner, LLMReasoner | None]:
    heuristic = HeuristicReasoner()
    llm: LLMReasoner | None = None
    if cfg.reasoner == "llm" and cfg.llm_provider:
        candidate = LLMReasoner(cfg.llm_provider, cfg.llm_model)
        llm = candidate if candidate.available() else None
    return heuristic, llm
