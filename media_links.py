"""Фото, сертификат и 3D-модель товара по артикулу: разбор таблицы номенклатуры.

Источник — тот же, что у паспортов: отдельный файл, а не API. Поля
`pictures`/`certificates` пусты у всех 11 942 товаров API, а в прайс-листе
столбцов со ссылками нет вовсе (docs/MEDIA_LINKS.md).

Отличие от passport_links.py — ключ. Паспорт один на серию, и таблица ведётся
по сериям; фотография же у каждого исполнения своя, и файл пришёл по
артикулам. Поэтому подбор здесь точный, без родства серий и разбора
наименований: артикул из таблицы совпадает с `vendor_code` 1С символ в символ
(проверено 29.08.2026 — все 11 552 строки нашлись в снимке каталога).

Файлы делятся между товарами: на 11 552 артикула приходится 654 разных
фотографии и 14 сертификатов. Поэтому кэш file_id ключуется ссылкой, а не
товаром, — иначе один и тот же сертификат качался бы пять тысяч раз.

Хранение — в data/knowledge.db, `document_id` — настоящий FK в `documents`
(core/documents.py). Как и у прайс-листа, живёт только последняя загрузка.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("data") / "knowledge.db"

# Логическое имя для core.documents.allocate_slot()/register_document(): у
# каждой присланной версии своё настоящее имя («NomenclatureCNC_updated (1).
# xlsx»), и по нему supersede-логика никогда бы не сработала.
LOGICAL_NAME = "nomenclature.xlsx"

# Что бот умеет отдавать из этой таблицы. Значения — ключ кэша file_id:
# Telegram выдаёт разные идентификаторы на один файл в зависимости от того,
# ушёл он фотографией или документом.
PHOTO = "photo"
CERT = "cert"
MODEL = "model"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    article TEXT NOT NULL,
    photo_url TEXT,
    photo_name TEXT,
    cert_url TEXT,
    cert_name TEXT,
    model_url TEXT,
    model_name TEXT,
    UNIQUE(document_id, article)
);
CREATE INDEX IF NOT EXISTS idx_media_links_article ON media_links(article);

-- Кэш уже отданных в Telegram файлов. Отдельная таблица, а не столбцы в
-- media_links: там строки вычищаются при каждой новой версии файла, а file_id
-- привязан к ссылке и переживает перезаливку. Качать заново 654 фотографии
-- из-за правки в столбце «Описание» было бы расточительно.
CREATE TABLE IF NOT EXISTS media_files (
    url TEXT NOT NULL,
    kind TEXT NOT NULL,
    file_id TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    PRIMARY KEY (url, kind)
);
"""

# Заголовки ищутся регулярками, а не по номеру столбца: файл ведётся руками и
# от версии к версии столбцы в нём переезжают. Ссылка проверяется раньше
# имени — «Ссылки на фото» содержит и «фото».
# Столбцы, дописанные к таблице уже после первой боевой заливки. CREATE TABLE
# IF NOT EXISTS существующую таблицу не трогает, поэтому на боевой базе они
# появляются только так. Кэш file_id при этом цел: он в своей таблице.
_ADDED_COLUMNS = (("model_url", "TEXT"), ("model_name", "TEXT"))

_ARTICLE_RE = re.compile(r"^артикул", re.I)
_PHOTO_URL_RE = re.compile(r"ссылк.*фото", re.I)
_PHOTO_NAME_RE = re.compile(r"^фото", re.I)
_CERT_URL_RE = re.compile(r"ссылк.*сертификат", re.I)
_CERT_NAME_RE = re.compile(r"^сертификат", re.I)
_MODEL_URL_RE = re.compile(r"ссылк.*3d", re.I)
_MODEL_NAME_RE = re.compile(r"3d", re.I)


@dataclass(frozen=True)
class MediaLinks:
    article: str
    photo_url: str | None
    photo_name: str | None      # имя файла из таблицы, до обращения к хостингу
    cert_url: str | None
    cert_name: str | None
    model_url: str | None = None
    model_name: str | None = None


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _url(value) -> str | None:
    """Ссылка или None. Нули и неразвёрнутые формулы =VLOOKUP(...) в этом
    файле означают «файла ещё нет»; менеджеру нельзя прислать «0» вместо
    фотографии."""
    text = _clean(value)
    if text is None or not text.lower().startswith("http"):
        return None
    return text


def _basename(value) -> str | None:
    """Имя файла из ячейки вида `.\\Photo\\YCM3YP-100.png` — папка не нужна."""
    text = _clean(value)
    if text is None:
        return None
    name = re.split(r"[\\/]", text)[-1].strip()
    return name or None


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(_SCHEMA)
    known = {row[1] for row in connection.execute("PRAGMA table_info(media_links)")}
    for column, kind in _ADDED_COLUMNS:
        if column not in known:
            connection.execute(f"ALTER TABLE media_links ADD COLUMN {column} {kind}")
    connection.commit()
    return connection


def parse_workbook(path: Path) -> list[MediaLinks]:
    """Разобрать первый лист таблицы номенклатуры.

    Строки без артикула и строки, где нет ни одной настоящей ссылки, молча
    отбрасываются: в отличие от паспортов, пустая строка здесь ничего не
    запрещает — хранить её незачем.
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

        at: dict[str, int] = {}
        for index, title in enumerate(header):
            title = _clean(title)
            if title is None:
                continue
            for key, pattern in (
                ("article", _ARTICLE_RE),
                ("photo_url", _PHOTO_URL_RE),
                ("cert_url", _CERT_URL_RE),
                ("model_url", _MODEL_URL_RE),
                ("photo_name", _PHOTO_NAME_RE),
                ("cert_name", _CERT_NAME_RE),
                ("model_name", _MODEL_NAME_RE),
            ):
                if key not in at and pattern.search(title):
                    at[key] = index
                    break

        if "article" not in at:
            raise ValueError(
                "В первой строке нужен столбец «Артикул» — найдено: "
                f"{', '.join(str(_clean(t)) for t in header if _clean(t))}."
            )
        if not {"photo_url", "cert_url", "model_url"} & at.keys():
            raise ValueError(
                "Нет ни одного столбца со ссылками: нужны «Ссылки на фото», "
                "«Ссылки на сертификаты» и/или «ссылка на 3Dmodel»."
            )

        def cell(row, key):
            index = at.get(key)
            return row[index] if index is not None and len(row) > index else None

        links: list[MediaLinks] = []
        for row in rows:
            article = _clean(cell(row, "article"))
            if not article:
                continue
            photo_url = _url(cell(row, "photo_url"))
            cert_url = _url(cell(row, "cert_url"))
            model_url = _url(cell(row, "model_url"))
            if photo_url is None and cert_url is None and model_url is None:
                continue
            links.append(MediaLinks(
                article=article.upper(),
                photo_url=photo_url,
                photo_name=_basename(cell(row, "photo_name")) if photo_url else None,
                cert_url=cert_url,
                cert_name=_basename(cell(row, "cert_name")) if cert_url else None,
                model_url=model_url,
                model_name=_basename(cell(row, "model_name")) if model_url else None,
            ))
        return links
    finally:
        workbook.close()


def import_links(document_id: int, links: list[MediaLinks]) -> int:
    """Заменить содержимое таблицы строками этой версии файла.

    Возвращает число вставленных строк. Кэш file_id не трогается: он привязан
    к ссылке, а не к версии таблицы.
    """
    with _connect() as connection:
        connection.execute("DELETE FROM media_links WHERE document_id != ?", (document_id,))
        inserted = 0
        for link in links:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO media_links "
                "(document_id, article, photo_url, photo_name, cert_url, cert_name, "
                "model_url, model_name) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (document_id, link.article.strip().upper(),
                 link.photo_url, link.photo_name, link.cert_url, link.cert_name,
                 link.model_url, link.model_name),
            )
            inserted += cursor.rowcount
        connection.commit()
    return inserted


def for_article(article: str | None) -> MediaLinks | None:
    """Фото, сертификат и 3D-модель этого артикула, или None."""
    needle = (article or "").strip().upper()
    if not needle:
        return None
    with _connect() as connection:
        row = connection.execute(
            "SELECT article, photo_url, photo_name, cert_url, cert_name, "
            "model_url, model_name FROM media_links WHERE article = ?", (needle,)
        ).fetchone()
    return MediaLinks(*row) if row else None


def counts() -> tuple[int, int, int]:
    """Сколько артикулов с фотографией, с сертификатом и с 3D-моделью."""
    with _connect() as connection:
        return connection.execute(
            "SELECT COUNT(photo_url), COUNT(cert_url), COUNT(model_url) FROM media_links"
        ).fetchone()


def distinct_urls(kind: str) -> list[str]:
    """Ссылки без повторов — для прогрева кэша: греть надо файлы, а не товары."""
    column = {PHOTO: "photo_url", CERT: "cert_url", MODEL: "model_url"}[kind]
    with _connect() as connection:
        rows = connection.execute(
            f"SELECT DISTINCT {column} FROM media_links WHERE {column} IS NOT NULL ORDER BY {column}"
        ).fetchall()
    return [row[0] for row in rows]


def cached_file_id(url: str, kind: str) -> str | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT file_id FROM media_files WHERE url = ? AND kind = ?", (url, kind)
        ).fetchone()
    return row[0] if row else None


def remember_file_id(url: str, kind: str, file_id: str) -> None:
    with _connect() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO media_files (url, kind, file_id, saved_at) VALUES (?, ?, ?, ?)",
            (url, kind, file_id, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()


def forget_file_id(url: str, kind: str) -> None:
    """Забыть file_id, который Telegram больше не принимает (сменился токен
    бота). Не повод отказать менеджеру — качаем заново."""
    with _connect() as connection:
        connection.execute("DELETE FROM media_files WHERE url = ? AND kind = ?", (url, kind))
        connection.commit()
