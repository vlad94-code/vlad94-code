"""Ссылки на каталоги для скачивания: разбор таблицы «Каталог → ссылка».

Источник — отдельный файл, как у паспортов и фотографий: каталоги лежат на
share-хостинге 25.lgprk.ru, и ни в API, ни в прайс-листе ссылок на них нет.
Отличие от passport_links.py и media_links.py — размер и способ обновления.
Там таблицы на тысячи строк, они приезжают в бота загрузкой документа и
живут в data/knowledge.db. Здесь десяток строк, которые меняются несколько
раз в год: файл лежит в репозитории рядом с кодом и читается как есть,
без загрузки, без базы и без пересборки — прислали новую версию, положили
поверх, выкатили.

Строки без настоящей ссылки молча отбрасываются — как и в соседних модулях,
«0» или неразвёрнутая формула означают «каталога пока нет», и присылать их
человеку вместо документа нельзя.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

logger = logging.getLogger(__name__)

XLSX_PATH = Path("knowledge") / "catalog_links.xlsx"

_NAME_HEADER_RE = re.compile(r"назван", re.I)
_URL_HEADER_RE = re.compile(r"ссылк", re.I)
@dataclass(frozen=True)
class Catalog:
    name: str       # как в файле: «каталог трансформаторы»
    url: str

    @cached_property
    def key(self) -> str:
        """Ключ кнопки — от названия, а не от номера строки в файле.

        Кнопка остаётся в истории чата дольше, чем версия справочника. По
        номеру строки нажатие после обновления файла открыло бы соседний
        каталог; по ключу оно открывает свой каталог либо честно сообщает,
        что такого каталога больше нет. Заодно длинное русское название не
        влезло бы в 64 байта callback_data.
        """
        return hashlib.sha1(self.name.encode()).hexdigest()[:8]

    @property
    def title(self) -> str:
        """Подпись кнопки и заголовок ответа — название целиком, с большой буквы.

        Повторяющееся в каждой строке слово «каталог» напрашивается выбросить,
        но половина названий стоит в родительном падеже («каталог модульного
        оборудования»), и без морфологии кнопка вышла бы «Модульного
        оборудования». Разнобой в одной клавиатуре хуже повтора слова.

        Поднимается только первая буква: capitalize() опустил бы хвост строки,
        и «каталог оборудования постоянного тока PV» стал бы «…тока pv».
        """
        return self.name[:1].upper() + self.name[1:]


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_workbook(path: Path) -> list[Catalog]:
    """Разобрать первый лист файла со ссылками на каталоги.

    Столбцы ищутся по шапке, а не по позиции: файл ведётся руками, столбцы в
    нём переезжают.
    """
    import openpyxl

    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        rows = workbook.worksheets[0].iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            raise ValueError("Файл пуст: нет даже строки заголовков.")

        name_at = url_at = None
        for index, title in enumerate(header):
            title = _clean(title)
            if title is None:
                continue
            if name_at is None and _NAME_HEADER_RE.search(title):
                name_at = index
            elif url_at is None and _URL_HEADER_RE.search(title):
                url_at = index
        if name_at is None or url_at is None:
            raise ValueError(
                "В первой строке нужны столбцы «Название каталога» и «ссылка на каталог» — "
                f"найдено: {', '.join(str(_clean(t)) for t in header if _clean(t))}."
            )

        catalogs: list[Catalog] = []
        for row in rows:
            name = _clean(row[name_at]) if len(row) > name_at else None
            url = _clean(row[url_at]) if len(row) > url_at else None
            if not name or not url or not url.lower().startswith("http"):
                continue        # «0» или неразвёрнутая формула = каталога нет
            catalogs.append(Catalog(name=name, url=url))
        return catalogs
    finally:
        workbook.close()


_cache: list[Catalog] | None = None


def forget() -> None:
    """Забыть прочитанное — для тестов и для перечитывания после подмены файла."""
    global _cache
    _cache = None


def catalogs() -> list[Catalog]:
    """Весь справочник. Читается один раз, дальше из памяти.

    Забытый или испорченный файл — не повод ронять бота: справочник остаётся
    пустым, а команда честно сообщает, что каталоги недоступны.
    """
    global _cache
    if _cache is None:
        try:
            _cache = parse_workbook(XLSX_PATH)
        except Exception:
            logger.warning("Справочник каталогов %s не прочитан", XLSX_PATH, exc_info=True)
            _cache = []
    return _cache


def find(key: str) -> Catalog | None:
    """Каталог по ключу кнопки. None — кнопка из старого сообщения, а такого
    каталога в нынешней версии файла уже нет."""
    return next((catalog for catalog in catalogs() if catalog.key == key), None)
