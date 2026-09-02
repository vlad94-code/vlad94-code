"""Source kinds and priority hierarchy.

Higher priority (lower number) wins when facts conflict.
See ARCHITECTURE.md § «Иерархия приоритетов источников».
"""
from __future__ import annotations

from enum import Enum, IntEnum


class SourcePriority(IntEnum):
    """Lower value = higher trust for confirming current product status."""

    PASSPORT = 1          # актуальный паспорт / тех. документация
    API = 2               # API / 1С — цена, остаток, актуальные позиции
    CATALOG_CURRENT = 3   # последняя версия каталога
    CATALOG_ARCHIVE = 4   # архивный каталог — справка, не доказательство наличия
    MAIL = 5              # история переписки — практический опыт
    LEXICON = 6           # approved синонимы
    ANALYTICS = 7         # агрегаты запросов — не техническая истина
    MANUAL = 0            # проверенный ответ инженера — выше всего


class SourceKind(str, Enum):
    PASSPORT = "passport"
    INSTRUCTION = "instruction"
    CERTIFICATE = "certificate"
    DRAWING = "drawing"
    CATALOG = "catalog"
    API_PRODUCTS = "api_products"
    API_PRICES = "api_prices"
    API_STOCK = "api_stock"
    API_TRANSIT = "api_transit"
    MAIL = "mail"
    LEXICON = "lexicon"
    ANALYTICS = "analytics"
    MANUAL = "manual"
    PARSED_CATALOG = "parsed_catalog"


_PRIORITY_MAP: dict[SourceKind, SourcePriority] = {
    SourceKind.MANUAL: SourcePriority.MANUAL,
    SourceKind.PASSPORT: SourcePriority.PASSPORT,
    SourceKind.INSTRUCTION: SourcePriority.PASSPORT,
    SourceKind.CERTIFICATE: SourcePriority.PASSPORT,
    SourceKind.DRAWING: SourcePriority.PASSPORT,
    SourceKind.API_PRODUCTS: SourcePriority.API,
    SourceKind.API_PRICES: SourcePriority.API,
    SourceKind.API_STOCK: SourcePriority.API,
    SourceKind.API_TRANSIT: SourcePriority.API,
    SourceKind.CATALOG: SourcePriority.CATALOG_CURRENT,
    SourceKind.PARSED_CATALOG: SourcePriority.CATALOG_CURRENT,
    SourceKind.MAIL: SourcePriority.MAIL,
    SourceKind.LEXICON: SourcePriority.LEXICON,
    SourceKind.ANALYTICS: SourcePriority.ANALYTICS,
}


def source_priority_for(kind: SourceKind, *, archived: bool = False) -> SourcePriority:
    if kind in (SourceKind.CATALOG, SourceKind.PARSED_CATALOG) and archived:
        return SourcePriority.CATALOG_ARCHIVE
    return _PRIORITY_MAP[kind]
