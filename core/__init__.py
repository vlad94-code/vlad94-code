"""Core types and source hierarchy for the CNC Russia AI support system."""

from core.sources import SourceKind, SourcePriority, source_priority_for
from core.types import EngineResponse, Fact, FactStatus, KnowledgeConflict

__all__ = [
    "EngineResponse",
    "Fact",
    "FactStatus",
    "KnowledgeConflict",
    "SourceKind",
    "SourcePriority",
    "source_priority_for",
]
