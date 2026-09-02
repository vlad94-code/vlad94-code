"""Shared data models for engines and knowledge bases."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any

from core.sources import SourceKind, SourcePriority


class FactStatus(str, Enum):
    CURRENT = "current"       # АКТУАЛЬНО
    STALE = "stale"           # УСТАРЕЛО
    CONFLICT = "conflict"     # КОНФЛИКТ
    CANDIDATE = "candidate"   # КАНДИДАТ (ожидает проверки)


@dataclass(frozen=True)
class Source:
    """Provenance metadata for a single fact."""

    kind: SourceKind
    name: str                          # «Каталог YCW3 v3.2», «products.json»
    document_date: date | None = None
    version: str | None = None
    priority: SourcePriority | None = None
    archived: bool = False
    page: int | None = None
    url_or_path: str | None = None

    def effective_priority(self) -> SourcePriority:
        from core.sources import source_priority_for

        if self.priority is not None:
            return self.priority
        return source_priority_for(self.kind, archived=self.archived)


@dataclass(frozen=True)
class Fact:
    """A single attributable piece of knowledge."""

    entity: str                        # серия, модель, артикул
    attribute: str                     # номинальный_ток, Icu, …
    value: str
    source: Source
    status: FactStatus = FactStatus.CURRENT
    recorded_at: datetime | None = None

    def format_with_source(self) -> str:
        src = self.source
        head = f"{self.attribute}: {self.value} (источник: {src.name}"
        tail_parts: list[str] = []
        if src.version:
            tail_parts.append(f"v{src.version}")
        if src.document_date:
            tail_parts.append(str(src.document_date))
        if tail_parts:
            return head + ", " + ", ".join(tail_parts) + ")"
        return head + ")"


@dataclass
class KnowledgeConflict:
    """Two or more facts about the same entity+attribute from different sources."""

    entity: str
    attribute: str
    facts: list[Fact]
    detected_at: datetime = field(default_factory=datetime.utcnow)
    resolved: bool = False
    resolution_note: str | None = None

    @property
    def winning_fact(self) -> Fact | None:
        if not self.facts:
            return None
        return min(self.facts, key=lambda f: f.source.effective_priority())


@dataclass
class EngineResponse:
    """Uniform response from any engine."""

    text: str
    handled: bool
    engine_name: str
    sources: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    conflicts: list[KnowledgeConflict] = field(default_factory=list)
    context_update: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0

    @classmethod
    def not_handled(cls, engine_name: str) -> EngineResponse:
        return cls(text="", handled=False, engine_name=engine_name)
