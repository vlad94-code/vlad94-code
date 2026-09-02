"""Ссылки на паспорта изделий: разбор таблицы «Серия → ссылка» и подбор
ссылки под конкретный товар.

Источник — отдельный файл, а не API: поля `pictures`/`certificates` пусты у
всех 11942 товаров API, полей паспорта/РЭ/3D в схеме нет вовсе, в прайс-листе
столбцов со ссылками тоже нет (docs/MEDIA_LINKS.md). Файл ведётся вручную и
приходит наполовину пустым — нули и неразвёрнутые формулы `=VLOOKUP(...)` в
столбце ссылок означают «паспорта пока нет», и попасть в базу они не должны:
менеджеру нельзя прислать «0» вместо документа.

Хранение — в том же data/knowledge.db, что и pricelist_store: это общий дом
для всего, что получено из загруженного документа, а `document_id` —
настоящий FK в таблицу `documents` (core/documents.py). Как и у прайс-листа,
хранится только последняя загрузка: у старых ссылок нет архивной ценности,
их заменяет новая версия файла.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data") / "knowledge.db"

# Логическое имя для core.documents.allocate_slot()/register_document() —
# по той же причине, что и pricelist_store.LOGICAL_NAME: настоящее имя файла
# у каждой присланной версии своё («Новая таблица.xlsx», «паспорта_v2.xlsx»),
# и по нему supersede-логика никогда бы не сработала.
LOGICAL_NAME = "passports.xlsx"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS passport_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    series TEXT NOT NULL,
    url TEXT,                -- NULL = серия в таблице есть, паспорта ещё нет
    status TEXT,
    UNIQUE(document_id, series)
);
CREATE INDEX IF NOT EXISTS idx_passport_links_series ON passport_links(series);

-- Кэш уже отданных в Telegram файлов. Отдельная таблица, а не столбец в
-- passport_links: там строки вычищаются при каждой новой версии таблицы, а
-- file_id привязан к ссылке и переживает перезаливку. Ключ — сам URL: если
-- в новой версии файла у серии другая ссылка, это другой документ, и
-- прежний file_id к нему не относится.
CREATE TABLE IF NOT EXISTS passport_files (
    url TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    saved_at TEXT NOT NULL
);
"""

_SERIES_HEADER_RE = re.compile(r"^сери", re.I)
_URL_HEADER_RE = re.compile(r"ссылк|паспорт", re.I)
_STATUS_HEADER_RE = re.compile(r"^статус", re.I)

# Буквы обоих алфавитов и цифры: граница токена серии в наименовании товара.
# Дефис в границу намеренно не входит — большинство серий сами дефисные
# («YCB9ZF-100AP» внутри «YCB9ZF-100AP-2P 4G» — это совпадение), а вот
# «AFDD» внутри «AFDDX-12» — уже другое изделие.
_TOKEN_EDGE = r"[0-9A-Za-zА-Яа-яЁё]"


@dataclass(frozen=True)
class PassportLink:
    series: str
    url: str | None          # None — серия в таблице есть, паспорта ещё нет
    status: str | None = None


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(_SCHEMA)
    return connection


def parse_workbook(path: Path) -> list[PassportLink]:
    """Разобрать первый лист файла со ссылками на паспорта.

    Столбцы ищутся по шапке, а не по позиции: файл ведётся руками, столбцы в
    нём переезжают. Строки без настоящей ссылки (пусто, «0», формула) молча
    отбрасываются — это «паспорта ещё нет», а не ошибка файла.
    """
    import openpyxl

    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            raise ValueError("Файл пуст: нет даже строки заголовков.")

        series_at = url_at = status_at = None
        for index, title in enumerate(header):
            title = _clean(title)
            if title is None:
                continue
            if series_at is None and _SERIES_HEADER_RE.match(title):
                series_at = index
            elif url_at is None and _URL_HEADER_RE.search(title):
                url_at = index
            elif status_at is None and _STATUS_HEADER_RE.match(title):
                status_at = index
        if series_at is None or url_at is None:
            raise ValueError(
                "В первой строке нужны столбцы «Серия» и «Ссылки на паспорта» — "
                f"найдено: {', '.join(str(_clean(t)) for t in header if _clean(t))}."
            )

        links: list[PassportLink] = []
        for row in rows:
            series = _clean(row[series_at]) if len(row) > series_at else None
            if not series:
                continue
            url = _clean(row[url_at]) if len(row) > url_at else None
            if url is not None and not url.lower().startswith("http"):
                url = None      # «0» или неразвёрнутая формула = паспорта нет
            status = _clean(row[status_at]) if status_at is not None and len(row) > status_at else None
            links.append(PassportLink(series=series.upper(), url=url, status=status))
        return links
    finally:
        workbook.close()


def import_links(document_id: int, links: list[PassportLink]) -> int:
    """Заменить содержимое таблицы ссылками этой версии файла.

    Возвращает число вставленных строк (одна серия в файле может встречаться
    дважды — например, общий паспорт на YCHGL/YCHGLB; дубль не фатален)."""
    with _connect() as connection:
        connection.execute("DELETE FROM passport_links WHERE document_id != ?", (document_id,))
        inserted = 0
        for link in links:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO passport_links (document_id, series, url, status) "
                "VALUES (?, ?, ?, ?)",
                (document_id, link.series.strip().upper(), link.url, link.status),
            )
            inserted += cursor.rowcount
        connection.commit()
    return inserted


def _all_links() -> list[PassportLink]:
    """Все ссылки, длинные серии первыми — чтобы «YC7VAN» проверялось раньше
    «YC7VA» и товар не получил паспорт соседнего исполнения."""
    with _connect() as connection:
        rows = connection.execute(
            "SELECT series, url, status FROM passport_links ORDER BY LENGTH(series) DESC, series"
        ).fetchall()
    return [PassportLink(series=row[0], url=row[1], status=row[2]) for row in rows]


def _related(catalogue_series: str, table_series: str) -> bool:
    """Родство серий: одна начинается с другой («YCB9ZF» и «YCB9ZF-100AP»,
    «AFDD L1» и «AFDD»). Без этой проверки паспорт уезжает к чужому изделию:
    у тепловых реле серии JR28 в наименовании назван контактор, к которому
    реле подходит («Тепловое реле JR28-11.5 1.6-2.5A CJX2-K»), и все 67
    таких реле получали паспорт контактора CJX2-K.
    """
    return catalogue_series.startswith(table_series) or table_series.startswith(catalogue_series)


def link_for(series: str | None, name: str | None = None) -> PassportLink | None:
    """Паспорт для товара с серией `series` и наименованием `name`.

    Сначала точное совпадение серии — так подобрано 66 серий из 70 в первом
    присланном файле. Остальные не совпадают ни в одну сторону: в каталоге
    «YCB9ZF», в таблице «YCB9ZF-100AP»; в каталоге «AFDD L1», в таблице
    «AFDD». Хуже того, у YC7VA-3 и YC7VAN-1 серия каталога одна и та же —
    «YC7», и различить их можно только по наименованию. Поэтому второй
    заход — поиск самой длинной родственной серии таблицы как отдельного
    токена в наименовании.

    Серия, у которой в таблице стоит «сделать», молчит намеренно: её строка
    без ссылки перебивает более общую серию. Иначе исполнение YCM8 PV
    (паспорта ещё нет) выдавало бы паспорт обычного YCM8 — а это другой
    аппарат, на постоянный ток и полторы тысячи вольт.
    """
    needle = (series or "").strip().upper()
    if not needle:
        return None
    with _connect() as connection:
        row = connection.execute(
            "SELECT series, url, status FROM passport_links WHERE series = ?", (needle,)
        ).fetchone()
    if row:
        return PassportLink(series=row[0], url=row[1], status=row[2]) if row[1] else None

    haystack = (name or "").upper()
    if not haystack:
        return None
    for link in _all_links():
        if not _related(needle, link.series):
            continue
        pattern = rf"(?<!{_TOKEN_EDGE}){re.escape(link.series)}(?!{_TOKEN_EDGE})"
        if re.search(pattern, haystack):
            return link if link.url else None
    return None


def cached_file_id(url: str) -> str | None:
    """file_id уже загруженного в Telegram паспорта — чтобы не качать его
    второй раз: паспорта CNC весят 5–13 МБ, а сайт отдаёт их медленно
    (замер 27.08.2026: 770 КБ за 54 с)."""
    with _connect() as connection:
        row = connection.execute("SELECT file_id FROM passport_files WHERE url = ?", (url,)).fetchone()
    return row[0] if row else None


def remember_file_id(url: str, file_id: str) -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT INTO passport_files (url, file_id, saved_at) VALUES (?, ?, ?) "
            "ON CONFLICT(url) DO UPDATE SET file_id = excluded.file_id, saved_at = excluded.saved_at",
            (url, file_id, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()


def forget_file_id(url: str) -> None:
    """Забыть file_id, который Telegram больше не принимает (сменился токен
    бота — старые file_id вместе с ним становятся чужими)."""
    with _connect() as connection:
        connection.execute("DELETE FROM passport_files WHERE url = ?", (url,))
        connection.commit()


def count() -> int:
    """Сколько серий сейчас с паспортом — для сообщения о загрузке. Строки
    без ссылки в базе тоже есть, но паспортом не считаются."""
    with _connect() as connection:
        return int(
            connection.execute("SELECT COUNT(*) FROM passport_links WHERE url IS NOT NULL").fetchone()[0]
        )
