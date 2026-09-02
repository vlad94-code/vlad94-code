"""Local searchable matrix built from parsed catalogues and API exports."""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path

from core.documents import active_parsed_paths, registered_parsed_names

DB_PATH = Path("data") / "knowledge.db"
CATALOG_DIR = Path("uploads")
API_DIR = Path("data") / "api_exports"
CACHE_DIR = Path("data") / "api_cache"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA busy_timeout = 5000")
    # WAL: readers (search()) are not blocked while rebuild() holds a write
    # transaction, and vice versa — without it the bot freezes for every user
    # while a catalogue/API sync is rebuilding the matrix.
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(source, kind, article, page, text, tokenize='unicode61')"
    )
    return connection


def _pages(path: Path):
    text = path.read_text(encoding="utf-8", errors="ignore")
    parts = re.split(r"(?=^## Страница \d+\s*$)", text, flags=re.M)
    for part in parts:
        match = re.match(r"^## Страница (\d+)\s*$", part, re.M)
        if match:
            yield match.group(1), part


def _article(record: object) -> str:
    if not isinstance(record, dict):
        return ""
    for key, value in record.items():
        if re.sub(r"[^a-zа-я]", "", str(key).lower()) in {"артикул", "article", "sku", "code", "vendorcode"}:
            return str(value)
    return ""


def _records(path: Path):
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            yield from csv.DictReader(stream)
        return
    content = path.read_text(encoding="utf-8", errors="ignore")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        yield {"text": content}
        return
    if isinstance(payload, list):
        yield from payload
    elif isinstance(payload, dict):
        # API exports may wrap product rows in data/items/result.
        for value in payload.values():
            if isinstance(value, list):
                yield from value
                return
        yield payload


def rebuild() -> tuple[int, int]:
    """Recreate the matrix. Returns (catalogue page count, API record count)."""
    # Read the document registry (a separate connection to the same file)
    # BEFORE opening our own write transaction below — otherwise the second
    # connection's implicit "CREATE TABLE IF NOT EXISTS" blocks on our lock.
    registered_paths = active_parsed_paths(CATALOG_DIR)
    all_registered_names = set(registered_parsed_names())
    catalogue_paths = list(registered_paths) + [
        path for path in CATALOG_DIR.glob("*.parsed.md") if path.name not in all_registered_names
    ]
    with _connect() as connection:
        connection.execute("DELETE FROM chunks")
        pages = records = 0
        # Until the registry was added, catalogues were stored without metadata.
        # Keep those legacy files searchable; once a document is registered, only
        # its current parsed version participates in the matrix.
        for path in catalogue_paths:
            for page, text in _pages(path):
                connection.execute("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)", (path.name, "catalog", "", page, text))
                pages += 1
        for directory in (API_DIR, CACHE_DIR):
            if not directory.exists():
                continue
            for path in directory.glob("*.*"):
                for record in _records(path):
                    text = json.dumps(record, ensure_ascii=False) if not isinstance(record, str) else record
                    connection.execute("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)", (path.name, "api", _article(record), "", text))
                    records += 1
    return pages, records


# Служебные слова вопроса. Список намеренно короткий и ручной, а не готовый
# «русский стоп-лист»: те содержат «лучше», «больше», «конечно» и подобные,
# а для каталога электрооборудования это содержательные слова, по которым
# как раз и надо искать. Убираем только предлоги, союзы и вопросительные
# слова — то, что есть почти на каждой странице и потому ничего не различает.
_STOPWORDS = frozenset({
    "и", "в", "во", "на", "с", "со", "к", "ко", "у", "о", "об", "обо", "от", "до", "за",
    "по", "для", "из", "при", "через", "над", "под", "между", "про",
    "не", "ни", "же", "ли", "бы", "а", "но", "или", "то",
    "что", "чем", "чего", "как", "какой", "какая", "какие", "какое", "каких", "каким",
    "это", "этот", "эта", "эти", "тот", "та", "те", "так", "там", "тут",
    "есть", "быть", "был", "была", "было", "были",
    "я", "ты", "вы", "мы", "он", "она", "они", "его", "её", "их", "нас", "вам", "мне",
    "где", "когда", "куда", "если", "чтобы", "уже", "ещё", "еще",
})


def _is_identifier(token: str) -> bool:
    """Артикул или код серии: в токене есть и буква, и цифра (YCM3E, B052730,
    3P, 1600А). Именно такой токен различает ответ, тогда как «какие»,
    «регулировки» и «у» встречаются на десятках страниц."""
    return any(character.isdigit() for character in token) and any(
        character.isalpha() for character in token
    )


def _query_variants(question: str) -> list[str]:
    """Запросы FTS5 от самого требовательного к самому мягкому.

    Зачем вообще несколько. При одном OR-запросе редкий артикул тонет:
    в индексе рядом с сотней страниц каталогов лежат ~25 000 товарных
    записей API, где этот же артикул встречается сотни раз, поэтому его
    IDF низкий, а «какие регулировки у» совпадает целиком. Реальный
    результат: на вопрос про YCM3YP выдача начиналась с YCM8T/A и YCM8RT.
    Ответ про другой аппарат — худший вид ошибки, поэтому артикул из
    вопроса делается обязательным, а не просто «более весомым».

    Мягкие варианты нужны, чтобы требовательность не оборачивалась пустой
    выдачей: незнакомый или опечатанный код серии не должен оставлять
    пользователя вообще без ответа.
    """
    tokens = re.findall(r"[A-Za-zА-Яа-я0-9]+", question)[-12:]
    identifiers = list(dict.fromkeys(token for token in tokens if _is_identifier(token)))
    words = list(dict.fromkeys(
        token for token in tokens
        if not _is_identifier(token) and token.lower() not in _STOPWORDS
    ))[-8:]

    identifier_expression = " AND ".join(f'"{token}"' for token in identifiers)
    word_expression = " OR ".join(f'"{token}"' for token in words)

    variants: list[str] = []
    if identifier_expression and word_expression:
        variants.append(f"{identifier_expression} AND ({word_expression})")
    if identifier_expression:
        variants.append(identifier_expression)
    # Сравнение двух аппаратов («чем YCM3 отличается от YCM1») требовать
    # целиком в одном фрагменте нельзя: описания линеек лежат на разных
    # страницах, и строгий AND возвращал одну страницу вместо четырёх.
    # Поэтому каждый артикул добирается ещё и поодиночке.
    if len(identifiers) > 1:
        variants.extend(f'"{token}"' for token in identifiers)
    if word_expression:
        variants.append(word_expression)
    return variants


def search(question: str, limit: int = 4) -> list[dict[str, str]]:
    variants = _query_variants(question)
    if not variants or not DB_PATH.exists():
        return []
    # Варианты идут от строгого к мягкому, а результаты копятся: точный
    # фрагмент занимает первые места, а оставшиеся слоты добираются мягкими
    # запросами. Возвращать только первый непустой вариант нельзя — на
    # сравнительном вопросе это оставляло одну страницу вместо четырёх.
    found: list[dict[str, str]] = []
    # Ключ включает текст: у записей API страница пустая, а источник общий
    # (products.json), поэтому пары «источник + страница» им не хватает —
    # без текста все товарные записи схлопнулись бы в одну.
    seen: set[tuple[str, str, str]] = set()
    keys = ("source", "kind", "article", "page", "text")
    with _connect() as connection:
        for expression in variants:
            rows = connection.execute(
                "SELECT source, kind, article, page, snippet(chunks, 4, '[', ']', '…', 24) FROM chunks "
                "WHERE chunks MATCH ? ORDER BY rank LIMIT ?",
                (expression, limit),
            ).fetchall()
            for row in rows:
                record = dict(zip(keys, row))
                identity = (record["source"], record["page"], record["text"])
                if identity in seen:
                    continue
                seen.add(identity)
                found.append(record)
                if len(found) >= limit:
                    return found
    return found
