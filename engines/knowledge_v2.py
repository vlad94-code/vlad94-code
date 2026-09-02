"""Extended Knowledge Engine for technical terminology and series characteristics.

This module extracts technical definitions and characteristic data from parsed catalogs.
Handles questions like:
- "Что такое характеристика срабатывания C?"
- "Какие характеристики бывают у автоматических выключателей?"
- "Есть ли автоматы с характеристикой K?"
- "Что означает IP20?"
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.documents import active_parsed_paths, registered_parsed_names

CATALOG_DIR = Path("uploads")


@dataclass(frozen=True)
class TechnicalAnswer:
    """Technical explanation with source reference."""
    question_type: str  # 'definition', 'series_has_feature', 'feature_explanation'
    text: str
    source: str
    page: Optional[int] = None
    confidence: float = 1.0


# Standard circuit breaker tripping characteristics per IEC 60898
TRIPPING_CHARACTERISTICS = {
    "B": {
        "range": "3-5 × In",
        "application": "Активные нагрузки (освещение, розетки)",
        "description": "Мгновенное расцепление при токе 3-5 кратном номинальному"
    },
    "C": {
        "range": "5-10 × In",
        "application": "Универсальная: смешанные нагрузки, небольшие двигатели",
        "description": "Мгновенное расцепление при токе 5-10 кратном номинальному"
    },
    "D": {
        "range": "10-20 × In",
        "application": "Высокие пусковые токи (трансформаторы, мощные двигатели)",
        "description": "Мгновенное расцепление при токе 10-20 кратном номинальному"
    },
    "K": {
        "range": "8-14 × In",
        "application": "Защита двигателей, трансформаторов, индуктивных нагрузок",
        "description": "Мгновенное расцепление при токе 8-14 кратном номинальному"
    },
    "Z": {
        "range": "2-3 × In",
        "application": "Полупроводники, электроника, цепи с малыми перегрузками",
        "description": "Мгновенное расцепление при токе 2-3 кратном номинальному"
    }
}


def _page_sections(text: str):
    """Split catalog text by pages."""
    matches = list(re.finditer(r"^## Страница (\d+)\s*$", text, re.M))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        yield int(match.group(1)), text[match.end():end]


def _find_series_on_page(section: str, series: str) -> bool:
    """Check if series is mentioned on this page."""
    patterns = [
        rf"серии\s+{re.escape(series)}",
        rf"{re.escape(series)}\s*[-–]",
        rf"выключатель\s+{re.escape(series)}",
        rf"автомат\s+{re.escape(series)}",
    ]
    return any(re.search(p, section, re.I) for p in patterns)


def _extract_characteristics_from_section(section: str) -> list[str]:
    """Extract tripping characteristics (B, C, D, K, Z) from catalog section."""
    chars = set()

    # Pattern 1: "Характеристики: B,C,D" or "Характеристики: B,С,D /7"
    for match in re.finditer(r"характеристик[аи][\s:]+([A-ZКВСDOZ,\s/]+)", section, re.I):
        for char in re.findall(r"\b([BCDKZ])\b", match.group(1), re.I):
            chars.add(char.upper())

    # Pattern 2: "Термомагнитная хар-ка отключения B,C,D"
    for match in re.finditer(r"термомагнитн[а-я]*\s+хар[-\.]к[а-я]*\s+отключен[а-я]*[:\s]*([A-ZКВСDOZ,\s]+)", section, re.I):
        for char in re.findall(r"\b([BCDKZ])\b", match.group(1), re.I):
            chars.add(char.upper())

    # Deliberately no "any standalone B/C/D/K/Z letter near the word
    # характеристика" fallback here: it used to flag unrelated letters (units,
    # abbreviations, headings) as tripping characteristics and cite the page as
    # a source for something the page never actually said.
    return sorted(chars)


def explain_tripping_characteristic(character: str) -> TechnicalAnswer | None:
    """Explain what a tripping characteristic means (B, C, D, K, Z)."""
    char = character.upper().strip()
    if char not in TRIPPING_CHARACTERISTICS:
        return None

    info = TRIPPING_CHARACTERISTICS[char]
    text = (
        f"Характеристика {char} — время-токовая характеристика срабатывания автоматического выключателя.\n\n"
        f"📊 Диапазон срабатывания: {info['range']} от номинального тока\n\n"
        f"🔧 Применение: {info['application']}\n\n"
        f"💡 Описание: {info['description']}"
    )
    return TechnicalAnswer(
        question_type="definition",
        text=text,
        source="IEC 60898 (стандарт)",
        confidence=1.0
    )


def find_series_with_characteristic(character: str) -> TechnicalAnswer | None:
    """Find which CNC series have the specified tripping characteristic."""
    char = character.upper().strip()
    if char not in "BCDKZ":
        return None
    
    registered_paths = active_parsed_paths(CATALOG_DIR)
    legacy_paths = {
        path for path in CATALOG_DIR.glob("*.parsed.md") 
        if path.name not in registered_parsed_names()
    }
    paths = registered_paths | legacy_paths
    
    series_found = []
    sources = []
    
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for page, section in _page_sections(text):
            page_chars = _extract_characteristics_from_section(section)
            if char in page_chars:
                # Try to identify series name from section
                series_match = re.search(
                    r"(серии\s+)?([A-Z]{2,}[A-Z0-9]*(?:-\d+)?)",
                    section[:500],  # Look in first 500 chars
                    re.I
                )
                if series_match:
                    series_name = series_match.group(2)
                    if series_name not in ["PDF", "MD", "IEC", "EN"]:
                        series_found.append(series_name)
                        sources.append(f"{path.name}, стр. {page}")
    
    if not series_found:
        # Fallback: at least confirm the characteristic exists
        return explain_tripping_characteristic(char)
    
    unique_series = list(dict.fromkeys(series_found))  # Preserve order, remove duplicates
    text = (
        f"✅ Характеристика {char} встречается в следующих сериях CNC Electric:\n\n"
        + "\n".join(f"• {s}" for s in unique_series[:10])
        + ("\n\\n... и другие серии" if len(unique_series) > 10 else "")
        + f"\n\nℹ️ Всего найдено: {len(unique_series)} серий"
        + f"\n\n📚 Источники: {', '.join(sources[:3])}"
    )
    
    return TechnicalAnswer(
        question_type="series_has_feature",
        text=text,
        source=sources[0] if sources else "каталоги CNC Electric",
        confidence=0.9
    )


def list_all_characteristics() -> TechnicalAnswer | None:
    """List all available tripping characteristics with explanations."""
    registered_paths = active_parsed_paths(CATALOG_DIR)
    legacy_paths = {
        path for path in CATALOG_DIR.glob("*.parsed.md") 
        if path.name not in registered_parsed_names()
    }
    paths = registered_paths | legacy_paths
    
    all_chars = set()
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for _, section in _page_sections(text):
            chars = _extract_characteristics_from_section(section)
            all_chars.update(chars)
    
    # Always include standard characteristics even if not found in catalogs
    all_chars.update(TRIPPING_CHARACTERISTICS.keys())
    
    lines = ["📋 Характеристики срабатывания автоматических выключателей:\n"]
    for char in sorted(all_chars):
        if char in TRIPPING_CHARACTERISTICS:
            info = TRIPPING_CHARACTERISTICS[char]
            lines.append(f"{char} ({info['range']}): {info['application']}")
    
    text = "\n\n".join(lines)
    
    return TechnicalAnswer(
        question_type="feature_explanation",
        text=text,
        source="каталоги CNC Electric + IEC 60898",
        confidence=1.0
    )


def answer_technical_question(question: str) -> TechnicalAnswer | None:
    """Main entry point: analyze question and route to appropriate handler."""
    q_normalized = question.lower()
    
    # Pattern 1: "Что такое характеристика C/K/Z?" or "Что такое характеристика срабатывания C?"
    match = re.search(r'что\s+такое\s+[хх]арактеристик[а-я]*\s*(?:срабатывания)?\s*([bcdkz])\b', q_normalized, re.I)
    if match:
        char = match.group(1).upper()
        return explain_tripping_characteristic(char)
    
    # Pattern 2: "Есть ли товары с характеристикой C?" or "Какие серии имеют характеристику K?"
    match = re.search(r'(есть\s+ли|какие\s+(товары|серии|автоматы|выключатели))\s+.*?[хх]арактеристик[а-я]*\s*(?:срабатывания)?\s*([bcdkz])\b', q_normalized, re.I)
    if match:
        char = match.group(3).upper()
        return find_series_with_characteristic(char)
    
    # Pattern 3: "Какие характеристики бывают?" or "Список характеристик" or "Все характеристики"
    if re.search(r'(какие\s+[хх]арактеристик[а-я]*\s+бывают|список\s+[хх]арактеристик|все\s+[хх]арактеристик)', q_normalized, re.I):
        return list_all_characteristics()
    
    # Pattern 4: Just "характеристика C" or "C характеристика" (normalized)
    match = re.search(r'\b([bcdkz])\s*[хх]арактеристик', q_normalized, re.I)
    if match:
        char = match.group(1).upper()
        return explain_tripping_characteristic(char)
    
    # Alternative Pattern 4: "характеристика C" (letter after)
    match = re.search(r'[хх]арактеристик[а-я]*\s*(?:срабатывания)?\s*([bcdkz])\b', q_normalized, re.I)
    if match:
        char = match.group(1).upper()
        return explain_tripping_characteristic(char)
    
    # Pattern 5: "расшифруй характеристику D"
    if re.search(r'расшифров[а-я]+\s+[хх]арактеристик', q_normalized, re.I):
        char_match = re.search(r'\b([bcdkz])\b', q_normalized, re.I)
        if char_match:
            return explain_tripping_characteristic(char_match.group(1).upper())
    
    return None


# Export for adapters.py
def get_technical_answer(question: str) -> tuple[str, list[str]] | None:
    """Adapter interface for Knowledge Engine v2."""
    answer = answer_technical_question(question)
    if not answer:
        return None
    return (answer.text, [answer.source])
