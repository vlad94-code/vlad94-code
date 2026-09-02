"""Orchestrator: routes questions to engines in priority order.

Phase 1: wraps existing modules via adapters.
Phase 2+: add RAG, analytics logging, conflict detection.

Used by bot.py as the first routing stage before OpenAI RAG fallback.
"""
from __future__ import annotations

import logging
from typing import Any

from core.types import EngineResponse
from engines.adapters import AccessoryCompatibilityEngine, DocumentSemanticEngine, KnowledgeEngine, ProductDetailEngine, ProductEngine, ReferenceEngine
from engines.base import Engine

logger = logging.getLogger(__name__)

# Order matters: first engine that handles wins.
# AccessoryCompatibilityEngine MUST precede ProductEngine — both can extract
# a bare "series" filter from a question like "какие аксессуары подходят к
# YCW3?" (catalog_search.parse_filters() sees only the series token), and
# ProductEngine would otherwise silently answer with a plain list of YCW3
# breakers instead of accessories.
LOCAL_ENGINES: list[Engine] = [
    AccessoryCompatibilityEngine(),
    # ReferenceEngine (справочник, который ведёт инженер) идёт перед
    # KnowledgeEngine (разбор характеристик по каталогам) и перед
    # ProductEngine: подтверждённый инженером ответ старше по приоритету
    # источников и каталога, и разбора характеристик (ARCHITECTURE §5,
    # пункт 1a) — если запись в справочнике есть, она должна победить
    # раньше, чем движки ниже успеют предложить свой (менее авторитетный)
    # ответ на тот же вопрос.
    ReferenceEngine(),
    # Справочник впереди каталога: подтверждённый ответ на «чем отличается»
    # или «что входит в комплектацию» ценнее списка товаров, а
    # ProductEngine возвращал handled=True и закрывал такие вопросы до
    # того, как справочник их видел (спека §1.2).
    KnowledgeEngine(),
    ProductEngine(),
    ProductDetailEngine(),
    # Последним — поиск по смыслу по всему массиву документов (паспорта,
    # каталоги, в т.ч. OCR). Финальный резерв на свободные вопросы, которые
    # не разобрали точные движки выше: раньше это была работа внешнего ИИ
    # (Claude), теперь — локальный смысловой поиск по документам. Отдаёт
    # дословный абзац с источником; если ничего не найдено, вопрос уходит
    # человеку.
    DocumentSemanticEngine(),
]


async def route_local(question: str, context: dict[str, Any]) -> EngineResponse:
    """Try local (offline) engines in order. Returns first handled response."""
    for engine in LOCAL_ENGINES:
        try:
            confidence = engine.can_handle(question, context)
            if confidence <= 0:
                continue
            response = await engine.answer(question, context)
            if response.handled:
                logger.info("Engine %s handled question (confidence=%.2f)", engine.name, confidence)
                return response
        except Exception:
            logger.exception("Engine %s failed", engine.name)
    return EngineResponse.not_handled("router")
