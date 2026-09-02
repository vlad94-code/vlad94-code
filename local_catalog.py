"""Deterministic fallback for exact series-and-value questions in parsed catalogues."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core.documents import active_parsed_paths, registered_parsed_names

CATALOG_DIR = Path("uploads")


@dataclass(frozen=True)
class CatalogAnswer:
    text: str
    source: str
    page: int


def _article(question: str) -> str | None:
    tokens = re.findall(r"\b[A-Za-z0-9][A-Za-z0-9._/-]{3,}\b", question)
    candidates = [item for item in tokens if any(char.isdigit() for char in item)]
    return max(candidates, key=len) if candidates else None


def _page_sections(text: str):
    matches = list(re.finditer(r"^## Страница (\d+)\s*$", text, re.M))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        yield int(match.group(1)), text[match.end():end]


def max_nominal_current(question: str) -> CatalogAnswer | None:
    """Answer 'maximum nominal current' from the exact product-series page.

    The page boundary matters: an entire catalogue can contain larger currents from
    other series, so values are read only until the page's Accessories section.
    """
    normalized = question.lower()
    if not ("максим" in normalized and "номиналь" in normalized and "ток" in normalized):
        return None
    article = _article(question)
    if not article:
        return None
    registered_paths = active_parsed_paths(CATALOG_DIR)
    legacy_paths = {
        path for path in CATALOG_DIR.glob("*.parsed.md") if path.name not in registered_parsed_names()
    }
    paths = registered_paths | legacy_paths
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for page, section in _page_sections(text):
            product_table = re.split(r"\bАксессуары\b", section, maxsplit=1, flags=re.I)[0]
            # Do not treat an accessory compatible with another series as that
            # product's page (e.g. YCB9-80M accessories on a YCB9-63 page).
            heading = re.sub(r"\s+", " ", product_table)
            if not re.search(r"выключатель\s+" + re.escape(article), heading, re.I):
                continue
            currents = [int(value) for value in re.findall(r"(?<![A-Za-zА-Яа-я0-9])(\d{1,4})\s*[АA](?![A-Za-zА-Яа-я])", product_table)]
            if currents:
                maximum = max(currents)
                return CatalogAnswer(
                    text=(
                        f"Максимальный номинальный ток {article} — {maximum} А. "
                        f"По каталогу: страница {page}, строка {maximum}A перед разделом «Аксессуары»."
                    ),
                    source=path.name,
                    page=page,
                )
    return None
