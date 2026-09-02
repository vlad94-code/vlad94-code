"""Live data adapter for CNC Russia product, price and stock endpoints."""
from __future__ import annotations

from lexicon import resolve_category
import json
import os
import re
from difflib import SequenceMatcher
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ENDPOINTS = {
    "Технические характеристики": "https://api.cncrussia.com/products/",
    "Прайс-лист": "https://api.cncrussia.com/prices/",
    "Остатки на складе": "https://api.cncrussia.com/stock-balances/",
    "Товар в пути": "https://api.cncrussia.com/GoodsInTransit/",
}


class CncApi:
    def __init__(self) -> None:
        self.username = os.environ.get("CNC_API_USERNAME", "")
        self.password = os.environ.get("CNC_API_PASSWORD", "")
        self.query_param = os.environ.get("CNC_API_QUERY_PARAM", "search")
        self.enabled = bool(self.password)

    async def lookup(self, question: str) -> str:
        """Legacy live lookup. Kept for compatibility; normal bot flow uses lookup_local()."""
        article = self._extract_article(question)
        if not article:
            return ""
        local = self._lookup_exports(article)
        if local:
            return local
        return ""


    def lookup_category_local(self, question: str) -> str:
        """Answer product-category questions locally, including small typos.

        Category questions must not depend on OpenAI/RAG.  The CNC API snapshot
        already contains a reliable ``type_item`` field, so we use it as the
        source of truth and tolerate common one-letter typos in the user's text.
        """
        normalized = self._normalize_text(question)
        category = self._detect_category(normalized)
        if not category:
            return ""

        rows = self._load_product_rows()
        matches = [
            row for row in rows
            if isinstance(row, dict) and self._row_matches_category(row, category)
        ]
        if not matches:
            return f"По текущему локальному каталогу категория «{category['label']}» не найдена."

        series_counts: dict[str, int] = {}
        examples: dict[str, dict[str, Any]] = {}
        for row in matches:
            series = self._row_series(row) or "Без указанной серии"
            series_counts[series] = series_counts.get(series, 0) + 1
            examples.setdefault(series, row)

        lines = [
            f"Да. В локальном каталоге найдено {len(matches)} позиций: {category['label']}.",
            "",
            "Серии:",
        ]
        for series, count in sorted(series_counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"• {series} — {count} позиций")

        lines.extend(["", "Примеры:"])
        for series in sorted(examples):
            row = examples[series]
            lines.append(
                f"• {row.get('name', 'Без названия')} — артикул {row.get('vendor_code', 'не указан')}"
            )

        lines.extend([
            "",
            "Источник: локальный снимок каталога CNC Russia API. "
            "Характеристики обновляются ежедневно в 06:00 МСК.",
        ])
        return "\n".join(lines)

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text.lower().replace("ё", "е")
        text = re.sub(r"[^a-zа-я0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _similar(cls, a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()

    @classmethod
    def _phrase_fuzzy_match(cls, text: str, phrase: str, threshold: float = 0.82) -> bool:
        """Match a phrase even if one word has a small typo (e.g. выклчатель)."""
        text_tokens = text.split()
        phrase_tokens = phrase.split()
        if len(text_tokens) < len(phrase_tokens):
            return False
        for i in range(len(text_tokens) - len(phrase_tokens) + 1):
            window = text_tokens[i:i + len(phrase_tokens)]
            scores = [cls._similar(a, b) for a, b in zip(window, phrase_tokens)]
            # Exact words are always accepted; typo tolerance is only used for
            # longer words so that short words do not create false positives.
            ok = all(
                a == b or (len(b) >= 5 and score >= threshold)
                for a, b, score in zip(window, phrase_tokens, scores)
            )
            if ok:
                return True
        return False

    @classmethod
    def _detect_category(cls, text: str) -> dict[str, Any] | None:
        # 1) Локальный лексикон: опечатки + синонимы.
        resolved = resolve_category(text)
        if resolved:
            return {"label": resolved["label"], "types": resolved["types"]}

        # 2) Запасной вариант: нечёткий поиск по реальным многословным
        #    type_item из снимка API (работает для будущих категорий).
        rows = cls._load_product_rows()
        type_counts: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            item_type = str(row.get("type_item", "")).strip()
            if len(item_type.split()) >= 2:
                type_counts[item_type] = type_counts.get(item_type, 0) + 1
        candidates = sorted(type_counts, key=lambda value: (-len(value.split()), -type_counts[value], value))
        for item_type in candidates:
            phrase = cls._normalize_text(item_type)
            if cls._phrase_fuzzy_match(text, phrase):
                return {"label": item_type.lower(), "types": {item_type}}
        return None

    @staticmethod
    def _row_series(row: dict[str, Any]) -> str:
        series = str(row.get("series", "")).strip()
        if series:
            return series
        for spec in row.get("specification", []) or []:
            if isinstance(spec, dict) and str(spec.get("name", "")).strip().lower() == "серия":
                return str(spec.get("value", "")).strip()
        return ""

    @classmethod
    def _row_matches_category(cls, row: dict[str, Any], category: dict[str, Any]) -> bool:
        return str(row.get("type_item", "")).strip() in category["types"]

    @staticmethod
    @lru_cache(maxsize=1)
    def _load_product_rows() -> list[Any]:
        path = Path("data") / "api_exports" / "products.json"
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(payload, dict):
            catalog = payload.get("catalog")
            if isinstance(catalog, list):
                return catalog
            return next((value for value in payload.values() if isinstance(value, list)), [])
        return payload if isinstance(payload, list) else []

    @staticmethod
    @lru_cache(maxsize=1)
    def _export_records() -> list[dict[str, Any]]:
        """Every record from data/api_exports/*.json, read and parsed once per
        snapshot instead of on every user question — that repeated read+parse
        (plus a fresh json.dumps of every record just to substring-match it)
        was the ~8s latency behind each free-text answer."""
        records: list[dict[str, Any]] = []
        directory = Path("data") / "api_exports"
        for path in directory.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            values = next((value for value in payload.values() if isinstance(value, list)), []) if isinstance(payload, dict) else payload
            records.extend(value for value in values if isinstance(value, dict))
        return records

    @classmethod
    def _series_of(cls, record: dict[str, Any]) -> str:
        series = str(record.get("series", "")).strip()
        if series:
            return series
        for item in record.get("specification", []) or []:
            if isinstance(item, dict) and str(item.get("name", "")).strip().lower() == "серия":
                return str(item.get("value", "")).strip()
        return ""

    @staticmethod
    @lru_cache(maxsize=1)
    def _export_index() -> dict[str, list[dict[str, Any]]]:
        """vendor_code -> records: O(1) path for the common "Артикул: ..." lookup."""
        index: dict[str, list[dict[str, Any]]] = {}
        for record in CncApi._export_records():
            code = str(record.get("vendor_code", "")).upper()
            if code:
                index.setdefault(code, []).append(record)
        return index

    @classmethod
    @lru_cache(maxsize=1)
    def _export_haystacks(cls) -> list[tuple[str, dict[str, Any]]]:
        """(searchable text, record) pairs for series/free-text lookups that
        don't match a vendor_code — e.g. "YCW3" is only present in some
        records' name, not in their vendor_code, and some terms only appear
        in item_description or a specification value (not just "Серия").
        Includes those fields too, same as the old per-question
        json.dumps(record) search did — but, like that old search, this is
        precomputed ONCE per snapshot (invalidated by clear_product_cache()
        on sync), not rebuilt per question, so full-text coverage doesn't
        bring back the ~8s per-answer cost that per-question json.dumps did."""
        haystacks: list[tuple[str, dict[str, Any]]] = []
        for record in cls._export_records():
            spec_text = " ".join(
                f"{item.get('name', '')} {item.get('value', '')}"
                for item in record.get("specification", []) or []
                if isinstance(item, dict)
            )
            text = (
                f"{record.get('vendor_code', '')} {record.get('name', '')} "
                f"{record.get('item_description', '')} {cls._series_of(record)} {spec_text}"
            ).upper()
            haystacks.append((text, record))
        return haystacks

    @staticmethod
    def clear_product_cache() -> None:
        CncApi._load_product_rows.cache_clear()
        CncApi._export_records.cache_clear()
        CncApi._export_index.cache_clear()
        CncApi._export_haystacks.cache_clear()

    def lookup_records(self, question: str) -> list[dict[str, Any]]:
        """The matching snapshot records themselves, unformatted.

        lookup_local() renders these for the *model*: 20 records with every
        specification field spelled out, which is right for a prompt and
        unreadable in a chat window. Callers that show the result to a person
        take the records from here and render them with catalog_search, so the
        bot has exactly one product layout instead of two.
        """
        if not self.enabled:
            return []
        article = self._extract_article(question)
        if not article:
            return []
        needle = article.upper()
        records = self._export_index().get(needle)
        if records is None:
            records = [record for haystack, record in self._export_haystacks() if needle in haystack]
        return records

    def lookup_local(self, question: str) -> str:
        """Read only the latest locally synchronised API snapshot.

        This method intentionally never performs a network request.
        """
        if not self.enabled:
            return ""
        article = self._extract_article(question)
        if not article:
            return ""
        return self._lookup_exports(article)

    def _lookup_exports(self, article: str) -> str:
        """Read the freshly synchronised local API snapshot without another download."""
        needle = article.upper()
        records = self._export_index().get(needle)
        if records is None:
            records = [record for haystack, record in self._export_haystacks() if needle in haystack]
        if not records:
            return ""
        maximum = self._max_nominal_current(records)
        summary = (
            f"Проверенный расчёт API для серии {article}: максимальный номинальный ток "
            f"среди актуальных вариантов — {self._format_number(maximum)} А."
            if maximum is not None else ""
        )
        listing = "API-снимок:\n" + self._format_records(records)
        return "\n".join(part for part in (summary, listing) if part)

    @staticmethod
    def _format_records(records: list[dict[str, Any]], limit: int = 20) -> str:
        """Render only the fields the model needs, not the raw record JSON.

        A raw json.dumps of up to 20 full product records (nested
        specification arrays, ids, service fields) multiplied the token cost
        of every question that reached this branch. A short field list is
        both cheaper and easier for the model to read correctly.
        """
        lines = []
        for record in records[:limit]:
            head = " — ".join(
                str(record[key]) for key in ("vendor_code", "name", "type_item") if record.get(key)
            )
            specs = {
                str(item.get("name", "")).strip(): str(item.get("value", "")).strip()
                for item in record.get("specification", []) or []
                if isinstance(item, dict) and item.get("name") and item.get("value")
            }
            line = f"• {head}"
            if specs:
                line += " (" + "; ".join(f"{key}: {value}" for key, value in specs.items()) + ")"
            lines.append(line)
        if len(records) > limit:
            lines.append(f"…и ещё {len(records) - limit} позиций.")
        return "\n".join(lines)

    @staticmethod
    def _save_cache(article: str, data: dict[str, Any]) -> None:
        """Keep the latest live API snapshot locally; it is replaced on each lookup."""
        cache_dir = Path("data") / "api_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        safe_article = re.sub(r"[^A-Za-z0-9._-]", "_", article)
        snapshot = {"article": article, "updated_at": datetime.now(timezone.utc).isoformat(), "sources": data}
        (cache_dir / f"{safe_article}.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def _max_nominal_current(cls, payload: Any) -> float | None:
        """Find max current in differently shaped product API JSON responses."""
        values: list[float] = []

        def is_current_key(key: object) -> bool:
            normalized = re.sub(r"[^a-zа-я]", "", str(key).lower())
            return (
                ("\u043d\u043e\u043c\u0438\u043d" in normalized and "\u0442\u043e\u043a" in normalized)
                or ("nominal" in normalized and "current" in normalized)
                or normalized in {"in", "ratedcurrent", "ratedcurrenta"}
            )

        def numbers(value: Any) -> list[float]:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return [float(value)]
            if isinstance(value, str):
                return [float(item.replace(",", ".")) for item in re.findall(r"\d+(?:[,.]\d+)?", value)]
            if isinstance(value, list):
                return [number for item in value for number in numbers(item)]
            if isinstance(value, dict):
                return [number for item in value.values() for number in numbers(item)]
            return []

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if is_current_key(key):
                        values.extend(numbers(item))
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(payload)
        return max(values) if values else None

    @staticmethod
    def _format_number(value: float) -> str:
        return str(int(value)) if value.is_integer() else str(value).replace(".", ",")

    @staticmethod
    def _extract_article(question: str) -> str:
        """Accept `Артикул: YCB9-63-1P-C16` and common article-like tokens."""
        explicit = re.search(r"артикул\s*[:№#-]?\s*([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9._/-]+)", question, re.I)
        if explicit:
            return explicit.group(1)
        tokens = re.findall(r"\b[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9._/-]{3,}\b", question)
        candidates = [token for token in tokens if any(char.isdigit() for char in token)]
        return max(candidates, key=len) if candidates else ""
