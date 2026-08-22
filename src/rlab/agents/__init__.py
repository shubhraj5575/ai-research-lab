"""The research agent team."""

from .analyst import DataAnalyst
from .base import Agent
from .critic import CriticAgent
from .designer import DesignError, ExperimentDesigner
from .director import IterationOutcome, ResearchDirector, SessionContext
from .hypothesis import Champion, HypothesisAgent, ResearchMemory
from .implementation import ImplementationAgent
from .literature import LiteratureAgent
from .reasoning import HeuristicReasoner, LLMReasoner, ReasonedText

__all__ = [
    "Agent", "ResearchDirector", "SessionContext", "IterationOutcome",
    "LiteratureAgent", "HypothesisAgent", "ExperimentDesigner", "DesignError",
    "ImplementationAgent", "DataAnalyst", "CriticAgent",
    "Champion", "ResearchMemory",
    "HeuristicReasoner", "LLMReasoner", "ReasonedText",
]
