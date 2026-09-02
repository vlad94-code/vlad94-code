"""Thin adapters wrapping existing modules into the Engine interface.

These wrap current code without rewriting it. Used by router.py in Phase 1.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import pricelist_store
import catalog_search
import reference_lookup
import unique_answers
from catalog_search import FILTER_KEYS as catalog_search_filter_keys, answer as catalog_search_answer, detail as catalog_detail, parse_filters as catalog_search_parse_filters
from core.types import EngineResponse
from engines.base import Engine
from local_catalog import max_nominal_current
from engines.knowledge_v2 import get_technical_answer


# Filters that describe a whole class of products rather than pinpointing one.
# On their own they are too weak a signal to claim a technical question: the
# user isn't asking to browse, and a plain product listing would ignore what was
# actually asked. Series was the original member; "Тип расцепителя" and "Род
# тока" behave identically ("чем отличается электронный расцепитель от
# термомагнитного", "подходит ли DC автомат для солнечных панелей"), so they
# joined it when those attributes became filterable.
_WEAK_FILTER_KEYS = frozenset({"series", "release_type", "current_kind"})

_TECHNICAL_INTENT_RE = re.compile(
    r"можно ли|входит\w*|допустимо|аналог|эквивалент|замен|вместо|отлича|"
    r"разниц|селективн|поддержива|совместим|подходит|подойд|комплектаци|"
    # Справочные вопросы про саму серию: регулировки расцепителя и описание
    # линейки есть в справочнике (§5 п.1a), а catalog_search на них отвечает
    # списком товаров или «подходящих товаров не найдено» — на вопрос про
    # диапазоны уставок YCM3E это был буквально ответ «не найдено».
    r"регулировк|настройк|уставк|представляет|линейк",
    re.I,
)

# Консультационный вопрос: просят не отфильтровать каталог, а выбрать.
# Отличается от _TECHNICAL_INTENT_RE тем, что отменяет подбор даже при
# СИЛЬНОМ фильтре: «какая серия выключателей в литом корпусе лучше» разбирается
# в type_item, то есть проверки на слабые фильтры тут не хватает, а ответом
# приходило «Найдено 1268 позиций» со списком фасетов.
_ADVISORY_INTENT_RE = re.compile(
    r"лучше|получше|выбрать|подобрать|посовет|рекоменд|предпочт|что взять|стоит брать",
    re.I,
)

# Объяснительный вопрос: человек просит объяснить, а не отфильтровать
# каталог. Отменяет подбор безусловно, даже при сильных фильтрах: «зачем
# нужен контактор» разбирается в type_item, и ответом приходило «Найдено
# 877 позиций» — список вместо объяснения. Справочник (§5 п.1a) на такие
# вопросы отвечает, поэтому движок обязан их пропустить.
_EXPLANATORY_INTENT_RE = re.compile(
    r"что такое|зачем|для чего нужен|расшифру|как работает|в ч[её]м разница|"
    r"чем отлича|что означает|объясни",
    re.I,
)

# Фильтры, которые описывают класс товаров целиком — серия и категория.
# Пока названа только категория, «какая лучше» списком не отвечается. Как
# только появились точные параметры (ток, полюса, Icu, артикул), человек уже
# подбирает, а не советуется, и список с фасетами ему как раз полезен.
_BROWSE_FILTER_KEYS = _WEAK_FILTER_KEYS | {"type_item"}


def _with_accessory_series(update: dict[str, Any], filters: dict[str, Any]) -> dict[str, Any]:
    """Tell bot.py which single product this answer is about, if any.

    Only a single matching product earns an accessory offer — with a list of
    candidates on screen there is no one product to accessorise. Both the
    article and the series go across: the article decides *which* offer (a main
    product gets "нужны аксессуары?", an accessory gets "к чему подходит"), the
    series answers it. Carried in context_update rather than a new
    EngineResponse field: it is exactly the "what the engine learned about this
    turn" channel that already exists.
    """
    product = catalog_search.sole_product(filters)
    if product is not None:
        update["sole_article"] = str(product.get("vendor_code", "")).upper()
        update["accessory_series"] = catalog_search.series(product).upper() or None
    return update


class ProductEngine(Engine):
    """Wraps catalog_search.py — deterministic product catalogue search."""

    name = "product"

    def __init__(self) -> None:
        # router.route_local() always calls can_handle() immediately followed by
        # answer() on the same (question, context) — without this cache that ran
        # the same catalog scan twice per user message.
        self._cache: tuple[str, dict[str, Any], tuple[Any, ...]] | None = None

    def _search(self, question: str, context: dict[str, Any]) -> tuple[Any, ...]:
        prior = context.get("catalog_filters", {})
        cached = self._cache
        if cached is not None and cached[0] == question and cached[1] == prior:
            return cached[2]
        if _EXPLANATORY_INTENT_RE.search(question):
            result = catalog_search.Answer(None, prior, False)
            self._cache = (question, prior, result)
            return result
        # A bare series mention inside an obviously technical/comparison
        # question ("можно ли заменить YCB9RL-63B на...", "входит ли корзина
        # в YCW3?") is too weak a signal to claim: the user isn't asking to
        # browse/filter products, and catalog_search can't answer "можно ли/
        # входит ли/чем отличается" anyway — it would return a plain product
        # listing that ignores the actual question. Let it fall through to
        # KnowledgeEngine/RAG, which can search real technical Q&A content
        # (verified on real paraphrased questions: without this check, 5 of
        # 8 such questions were claimed here before RAG ever saw them).
        fresh = catalog_search_parse_filters(question, None)
        fresh_keys = {k for k in catalog_search_filter_keys if k in fresh}
        technical = fresh_keys and fresh_keys <= _WEAK_FILTER_KEYS and _TECHNICAL_INTENT_RE.search(question)
        advisory = fresh_keys <= _BROWSE_FILTER_KEYS and _ADVISORY_INTENT_RE.search(question)
        if technical or advisory:
            result = catalog_search.Answer(None, prior, False)
        else:
            result = catalog_search_answer(question, prior)
        self._cache = (question, prior, result)
        return result

    def can_handle(self, question: str, context: dict[str, Any]) -> float:
        return 1.0 if self._search(question, context).handled else 0.0

    async def answer(self, question: str, context: dict[str, Any]) -> EngineResponse:
        found = self._search(question, context)
        if not found.handled:
            return EngineResponse.not_handled(self.name)
        update = _with_accessory_series({"catalog_filters": found.filters}, found.filters)
        if found.wider:
            # Nothing in this selection can meet the request — the transport
            # offers to look for it catalogue-wide rather than let the client
            # hit a ceiling and leave.
            update["wider_search"] = found.wider
        if found.explains:
            # This text already answers on the merits (catalog_search's
            # _no_such_value branch) — a client-facing caller must not
            # redraw it into a plain listing of catalog_filters.
            update["explains_selection"] = True
        return EngineResponse(
            text=found.text,
            handled=True,
            engine_name=self.name,
            # No source line: for a catalogue answer it is a constant, so it
            # carries no information and just pads every reply. Where the
            # source actually varies — the price list version, a knowledge
            # base document, a Claude answer — it is still reported.
            context_update=update,
        )


class ProductDetailEngine(Engine):
    """Exact article lookup via catalog_search.detail()."""

    name = "product_detail"
    _ARTICLE_RE = re.compile(
        r"(?:артикул\s*[:№#-]?\s*)?([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9._/-]{3,})",
        re.I,
    )

    def can_handle(self, question: str, context: dict[str, Any]) -> float:
        match = self._ARTICLE_RE.search(question)
        if not match or not any(c.isdigit() for c in match.group(1)):
            return 0.0
        code = match.group(1).upper()
        if not code.startswith(("B", "A", "C", "D", "E", "F", "G", "S", "Y")):
            return 0.0
        text = catalog_detail(code)
        return 1.0 if "не найден" not in text.lower() else 0.0

    async def answer(self, question: str, context: dict[str, Any]) -> EngineResponse:
        match = self._ARTICLE_RE.search(question)
        if not match:
            return EngineResponse.not_handled(self.name)
        code = match.group(1).upper()
        text = catalog_detail(code)
        if "не найден" in text.lower():
            return EngineResponse.not_handled(self.name)
        return EngineResponse(
            text=text,
            handled=True,
            engine_name=self.name,
            # No source line: for a catalogue answer it is a constant, so it
            # carries no information and just pads every reply. Where the
            # source actually varies — the price list version, a knowledge
            # base document, a Claude answer — it is still reported.
            context_update=_with_accessory_series({}, {"article": code}),
        )


class KnowledgeEngine(Engine):
    """Wraps local_catalog.py + knowledge_v2.py — technical Q&A from catalogs."""

    name = "knowledge"

    def __init__(self) -> None:
        # Same reasoning as ProductEngine._cache: avoid re-scanning catalog
        # files in answer() right after can_handle() already found the result.
        self._cache: tuple[str, Any, Any] | None = None

    def _lookup(self, question: str) -> tuple[Any, Any]:
        cached = self._cache
        if cached is not None and cached[0] == question:
            return cached[1], cached[2]
        result_v2 = get_technical_answer(question)
        result_v1 = max_nominal_current(question) if not result_v2 else None
        self._cache = (question, result_v2, result_v1)
        return result_v2, result_v1

    def can_handle(self, question: str, context: dict[str, Any]) -> float:
        result_v2, result_v1 = self._lookup(question)
        return 1.0 if (result_v2 or result_v1) else 0.0

    async def answer(self, question: str, context: dict[str, Any]) -> EngineResponse:
        result_v2, result = self._lookup(question)
        if result_v2:
            text, sources = result_v2
            return EngineResponse(
                text=text,
                handled=True,
                engine_name=self.name,
                sources=sources,
                confidence=0.95,
            )

        if not result:
            return EngineResponse.not_handled(self.name)
        return EngineResponse(
            text=result.text,
            handled=True,
            engine_name=self.name,
            sources=[result.source],
        )


_ACCESSORY_INTENT_RE = re.compile(r"аксессуар|принадлежност|запчаст|совместим", re.I)


class AccessoryCompatibilityEngine(Engine):
    """Wraps pricelist_store.py — accessory/part compatibility with a
    product series, and only that. Must run before ProductEngine in
    router.LOCAL_ENGINES: a question like "какие аксессуары подходят к
    YCW3?" already extracts filters={"series": "YCW3"} via
    catalog_search.parse_filters(), which is exactly what ProductEngine
    needs to answer (wrongly, with a plain list of YCW3 breakers) — this
    engine has to claim the question first.

    can_handle() only claims the question when the extracted filters are
    *bare series* (nothing else) — mirroring ProductEngine._search's own
    fresh_keys == {"series"} guard. A question that also carries poles/
    current/icu/article ("YCW3 3P 50кА совместим с YCB9?") is a product
    question wearing accessory-ish vocabulary, not a real accessory
    request, and must fall through to ProductEngine/KnowledgeEngine instead.

    catalog_search.parse_filters()'s series regex only recognizes YCM/YCW/
    YCB-prefixed series, so a bare mention of a non-YC*-prefixed series in
    free text (CJX2-FN, AD22, LAY4, D11, NT, ...) extracts no series at all.
    When that happens (no filters extracted whatsoever), fall back to
    scanning the question against pricelist_store's real known series list
    — keeps free text consistent with /accessories <series>, which already
    bypasses parse_filters entirely, without hardcoding a prefix regex.
    """

    name = "accessory_compat"

    def __init__(self) -> None:
        # Same reasoning as ProductEngine._cache above: router.route_local()
        # calls can_handle() immediately followed by answer() on the same
        # (question, context) — without this, both re-ran parse_filters()
        # and, for non-YC*-prefixed series, pricelist_store.known_series()
        # (a sqlite query) a second time for no new information.
        self._cache: tuple[str, dict[str, Any], str | None] | None = None

    def _extract_series(self, question: str, context: dict[str, Any]) -> str | None:
        prior = context.get("catalog_filters", {})
        cached = self._cache
        if cached is not None and cached[0] == question and cached[1] == prior:
            return cached[2]
        filters = catalog_search_parse_filters(question, prior)
        fresh_keys = {k for k in ("type_item", "series", "article", "current", "poles", "icu") if k in filters}
        if fresh_keys == {"series"}:
            series = filters["series"]
        elif fresh_keys:
            series = None
        else:
            upper_question = question.upper()
            series = next((s for s in pricelist_store.known_series() if s in upper_question), None)
        self._cache = (question, prior, series)
        return series

    def can_handle(self, question: str, context: dict[str, Any]) -> float:
        if not _ACCESSORY_INTENT_RE.search(question):
            return 0.0
        return 1.0 if self._extract_series(question, context) else 0.0

    async def answer(self, question: str, context: dict[str, Any]) -> EngineResponse:
        series = self._extract_series(question, context)
        if not series:
            return EngineResponse.not_handled(self.name)
        # accessories_for_series()/active_version_label() open a synchronous
        # sqlite3 connection (re-running the schema DDL each time) — must be
        # offloaded so a single lookup doesn't stall the event loop for every
        # other user's concurrent Telegram update.
        rows = await asyncio.to_thread(pricelist_store.accessories_for_series, series)
        if not rows:
            return EngineResponse.not_handled(self.name)
        version_label = await asyncio.to_thread(pricelist_store.active_version_label)
        return EngineResponse(
            text=pricelist_store.format_accessories(series, rows),
            handled=True,
            engine_name=self.name,
            sources=[f"прайс-лист CNC ({version_label})"],
        )


class ReferenceEngine(Engine):
    """Справочник уникальных ответов, который ведёт инженер.

    Отдаёт ответ дословно: генерации здесь нет вовсе. До этого движка
    справочник читался только через Claude, а клиенту Claude закрыт —
    паспорт бренда и подтверждённые техответы до него не доходили.
    """

    name = "reference"

    def __init__(self) -> None:
        # Та же причина, что у ProductEngine._cache: router.route_local()
        # вызывает can_handle() и сразу следом answer() на одной и той же
        # (question, context) — без кэша reference_lookup.lookup() сканировал
        # бы записи справочника дважды на каждый вопрос.
        self._cache: tuple[str, reference_lookup.Match | None] | None = None

    def _lookup(self, question: str) -> reference_lookup.Match | None:
        cached = self._cache
        if cached is not None and cached[0] == question:
            return cached[1]
        match = reference_lookup.lookup(question)
        self._cache = (question, match)
        return match

    def can_handle(self, question: str, context: dict[str, Any]) -> float:
        match = self._lookup(question)
        return match.score if match else 0.0

    async def answer(self, question: str, context: dict[str, Any]) -> EngineResponse:
        match = self._lookup(question)
        if not match:
            return EngineResponse.not_handled(self.name)
        return EngineResponse(
            text=match.answer,
            handled=True,
            engine_name=self.name,
            # Та же метка, что у остальных путей справочника (spec §5):
            # rstrip(":") — sources исторически хранит имя источника без
            # завершающего двоеточия из ANSWER_PREFIX.
            sources=[unique_answers.ANSWER_PREFIX.rstrip(":")],
        )
