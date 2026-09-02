"""Base interface for all answer engines."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.types import EngineResponse


class Engine(ABC):
    """One engine = one responsibility. Engines never call each other directly."""

    name: str = "base"

    @abstractmethod
    def can_handle(self, question: str, context: dict[str, Any]) -> float:
        """Return confidence 0.0–1.0 that this engine should handle the question."""

    @abstractmethod
    async def answer(self, question: str, context: dict[str, Any]) -> EngineResponse:
        """Produce an answer. Set handled=True only when the engine fully answers."""
