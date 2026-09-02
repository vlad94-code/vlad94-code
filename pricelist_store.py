"""Structured storage for the parsed CNC price-list (see pricelist_parser.py).

Lives in the same data/knowledge.db that core/documents.py (documents,
document_facts) and knowledge_matrix.py (FTS chunks) already use — this is
already the shared home for everything derived from an uploaded document,
not only full-text search content. pricelist_items.document_id is a real FK
into core.documents's `documents` table.

Only the LATEST price-list import is ever kept: unlike document_facts (kept
forever for archival lookups), old price-list rows have no product value
once a newer version replaces them, so import_items() prunes every row that
doesn't belong to the document it was just called with.

No price is stored anywhere in this schema — see pricelist_parser.py and
ARCHITECTURE.md §5.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import catalog_search
from pricelist_parser import ParsedItem

DB_PATH = Path("data") / "knowledge.db"

# Fixed logical name passed to core.documents.allocate_slot()/register_document()
# for every price-list upload, regardless of the vendor's real filename (which
# embeds a changing version string, e.g. "8865_248_...V3.8.2.xlsx") — using the
# real filename would make every upload a new original_name lineage and the
# existing supersede-on-reupload logic in core/documents.py would never fire.
LOGICAL_NAME = "pricelist.xlsx"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pricelist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    article TEXT NOT NULL,
    sheet TEXT NOT NULL,
    category_code TEXT,
    type_field TEXT NOT NULL,
    name TEXT,
    series TEXT,
    size TEXT,
    is_accessory INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    specs_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(document_id, sheet, article)
);
CREATE INDEX IF NOT EXISTS idx_pricelist_items_article ON pricelist_items(article);
CREATE INDEX IF NOT EXISTS idx_pricelist_items_series ON pricelist_items(series);

CREATE TABLE IF NOT EXISTS pricelist_accessory_compat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES pricelist_items(id),
    series TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pricelist_compat_series ON pricelist_accessory_compat(series);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(_SCHEMA)
    return connection


def import_items(document_id: int, items: list[ParsedItem]) -> int:
    """Replace the whole table content with `items` (belonging to
    `document_id`) and prune every row left over from a prior version.
    Returns the number of rows actually inserted."""
    with _connect() as connection:
        connection.execute(
            "DELETE FROM pricelist_accessory_compat WHERE item_id IN "
            "(SELECT id FROM pricelist_items WHERE document_id != ?)",
            (document_id,),
        )
        connection.execute("DELETE FROM pricelist_items WHERE document_id != ?", (document_id,))

        inserted = 0
        for item in items:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO pricelist_items "
                "(document_id, article, sheet, category_code, type_field, name, series, size, "
                " is_accessory, status, specs_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id, item.article, item.sheet, item.category_code, item.type_field,
                    item.name, item.series, item.size, int(item.is_accessory), item.status,
                    json.dumps(item.specs, ensure_ascii=False),
                ),
            )
            if not cursor.rowcount:
                continue  # duplicate (document_id, sheet, article) — data-quality quirk, not fatal
            inserted += 1
            item_id = cursor.lastrowid
            for series in item.compatible_series:
                series = series.strip().upper()
                if series:
                    connection.execute(
                        "INSERT INTO pricelist_accessory_compat (item_id, series) VALUES (?, ?)",
                        (item_id, series),
                    )
        connection.commit()
    return inserted


def known_series() -> list[str]:
    """All series that actually have accessory-compatibility rows, longest
    first — lets a caller scan free text for a real series token (e.g.
    CJX2-FN, AD22, LAY4) without hardcoding a prefix regex that would need
    manual upkeep every time CNC adds a new non-YC*-prefixed series."""
    with _connect() as connection:
        rows = connection.execute(
            "SELECT DISTINCT series FROM pricelist_accessory_compat ORDER BY LENGTH(series) DESC"
        ).fetchall()
    return [row[0] for row in rows]


def accessories_for_series(series: str, sheet: str | None = None) -> list[dict[str, Any]]:
    """Accessories/parts compatible with a product series, per the price list.

    `sheet` narrows the answer to one section of the price list. One series
    name can head two different sets: "YCM8" carries 148 accessories on
    "B(силовое)" and 22 on "EN (DC)", and the direct-current ones do not go on
    an alternating-current breaker. Callers that have only a series name (a
    human typing /accessories YCM8) cannot tell them apart and get both.
    """
    needle = series.strip().upper()
    if not needle:
        return []
    clauses = ["pc.series = ?", "pi.status != 'discontinued'"]
    params: list[Any] = [needle]
    if sheet:
        clauses.append("pi.sheet = ?")
        params.append(sheet)
    with _connect() as connection:
        rows = connection.execute(
            "SELECT pi.article, pi.sheet, pi.name, pi.size, pi.type_field, pi.specs_json "
            "FROM pricelist_items pi "
            "JOIN pricelist_accessory_compat pc ON pc.item_id = pi.id "
            "WHERE " + " AND ".join(clauses) + " ORDER BY pi.article",
            params,
        ).fetchall()
    return [
        {
            "article": row[0],
            "sheet": row[1],
            "name": row[2],
            "size": row[3],
            "type_field": row[4],
            "specs": json.loads(row[5]) if row[5] else {},
        }
        for row in rows
    ]


def format_accessories(series: str, rows: list[dict[str, Any]], limit: int = 20) -> str:
    """Render accessories_for_series() rows as a reply-ready text block —
    shared by AccessoryCompatibilityEngine and bot.py's /accessories command."""
    lines = [f"Аксессуары/принадлежности для серии {series} (по прайс-листу CNC):"]
    for row in rows[:limit]:
        parts = [row["article"]]
        if row.get("name"):
            parts.append(row["name"])
        line = " — ".join(parts)
        if row.get("size"):
            line += f" (типоразмер {row['size']})"
        lines.append(f"• {line}")
    if len(rows) > limit:
        lines.append(f"…и ещё {len(rows) - limit} позиций.")
    return "\n".join(lines)


def is_accessory(article: str) -> bool:
    """Is this article itself an accessory rather than a main product?

    The card for C000213 (доп. контакты F4-DN22) offered accessories *for an
    accessory*, because the offer was decided from the product's own "Серия"
    (CJX2i, which does have accessories). What actually matters is this flag:
    1045 of the price list's items are accessories, 17249 are main products.
    """
    needle = article.strip().upper()
    if not needle:
        return False
    with _connect() as connection:
        row = connection.execute(
            "SELECT 1 FROM pricelist_items WHERE UPPER(article) = ? AND is_accessory = 1 LIMIT 1",
            (needle,),
        ).fetchone()
    return row is not None


def _catalogue_names(connection: sqlite3.Connection, series: str, sheet: str) -> list[str]:
    """Как каталог 1С называет серию, под которой в прайсе стоят эти товары."""
    articles = [
        row[0] for row in connection.execute(
            "SELECT article FROM pricelist_items "
            "WHERE UPPER(series) = ? AND sheet = ? AND is_accessory = 0",
            (series.upper(), sheet),
        )
    ]
    catalogue = catalog_search.by_vendor_code()
    names: list[str] = []
    for code in articles:
        product = catalogue.get(str(code).upper())
        if product is None:
            continue
        name = (catalog_search.series(product) or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def series_for_accessory(article: str) -> list[str]:
    """Series this accessory fits — the compatibility table read backwards,
    named the way the catalogue names them.

    825 of 1025 accessories fit exactly one series and none fits more than ten,
    so the answer is always short enough to show as buttons.

    The name is translated through the products that actually stand under it,
    because the button leads into the catalogue and the two sources spell the
    same series differently: the price list writes "YCM8" on both its
    alternating- and its direct-current sheet, while 1С calls the latter
    "YCM8 PV". Untranslated, a direct-current accessory sent the reader to 620
    alternating-current breakers. A series with no product in the catalogue
    keeps its price-list name — it is the only one there is.
    """
    needle = article.strip().upper()
    if not needle:
        return []
    with _connect() as connection:
        rows = connection.execute(
            "SELECT DISTINCT pc.series, pi.sheet FROM pricelist_accessory_compat pc "
            "JOIN pricelist_items pi ON pi.id = pc.item_id "
            "WHERE UPPER(pi.article) = ? AND pi.is_accessory = 1 "
            "ORDER BY pc.series",
            (needle,),
        ).fetchall()
        names: list[str] = []
        for series, sheet in rows:
            names.extend(_catalogue_names(connection, series, sheet) or [series])
    return sorted(dict.fromkeys(names))


# What an accessory may declare about the product it fits, mapped to the
# catalogue field it is compared against. The price list writes every one of
# these as a slash-separated set — size "100/160/250", poles "3P/4P", current
# "9/12/18/25" — and an accessory constrains ONLY on what it actually declares:
# 1032 of 1045 declare nothing beyond the series and fit the whole of it.
#
# "Кол-о полюсов" is a typo in the source data, on 20 rows; both spellings count.
# Deliberately absent: "Исполнение" and "Напряжение катушки". On an accessory
# those hold its own coil voltage ("AC220V"), while on a product "Исполнение"
# means выкатной/стационарный — the same field name over two incompatible
# vocabularies. Matching them to each other would compare "выкатной" against
# "AC220V" and silently reject every such accessory.
_COMPAT_FIELDS = {
    "Количество полюсов": ("Количество полюсов", "Кол-о полюсов"),
    "Номинальный ток In (А)": ("Номинальный ток In(А)",),
    "Тип расцепителя": ("Тип расцепителя",),
}

# Withdrawable/fixed is recorded nowhere but the tail of the accessory's name,
# on 13 rows of 1045: "…YCW3 1600 F" against "…YCW8 HU 3P W". The absence of a
# suffix cannot mean "выкатной" — for the other 1032 the distinction simply does
# not apply — so an unmarked accessory stays unconstrained.
_EXECUTION_SUFFIX = re.compile(r"\s([FW])$", re.I)
_EXECUTION_BY_SUFFIX = {"F": "стационарный", "W": "выкатной"}


def _value_fits(product_value: str, declared: Any) -> bool:
    """Does the product's value appear in the set the accessory declares?"""
    if not declared or not product_value:
        return True
    wanted = str(product_value).strip()
    tokens = [t.strip() for t in str(declared).split("/") if t.strip()]
    if wanted in tokens:
        return True
    number = catalog_search.to_number(wanted)
    return number is not None and any(catalog_search.to_number(t) == number for t in tokens)


def implied_executions(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Fill in the half of an F/W pair that the price list left unmarked.

    Исполнение is written two ways. Four groups spell both halves out
    ("…YCW8 HU 3P F" / "…YCW8 HU 3P W"). Five — the YCW3 door frames — mark only
    the fixed one, and the unmarked twin beside it is the withdrawable variant:
    same name, same типоразмер, one letter apart.

    The inference is drawn from the twin's existence, never from the name alone.
    That keeps it to those five groups: the other 1032 accessories have no
    F-marked counterpart, so for them исполнение genuinely does not apply and
    they stay unconstrained.
    """
    groups: dict[tuple[str, Any], list[tuple[str, str | None]]] = {}
    for row in rows:
        name = str(row.get("name") or "").strip()
        match = _EXECUTION_SUFFIX.search(name)
        stem = _EXECUTION_SUFFIX.sub("", name)
        letter = match.group(1).upper() if match else None
        groups.setdefault((stem, row.get("size")), []).append((str(row.get("article", "")), letter))
    implied = {}
    for members in groups.values():
        letters = {letter for _, letter in members if letter}
        if letters != {"F"}:
            continue  # already explicit, or nothing to infer from
        for article, letter in members:
            if letter is None:
                implied[article] = _EXECUTION_BY_SUFFIX["W"]
    return implied


def compatible_with(product: dict[str, Any], accessory: dict[str, Any],
                    implied: dict[str, str] | None = None) -> bool:
    """Can this accessory go on this particular product?

    Series alone is too coarse: B05012 (YCM3, типоразмер 100, 3P) was offered
    all six YCM3 withdrawable cradles, including the 400/630 and the 4P ones.
    """
    values = catalog_search.spec(product)
    if not _value_fits(values.get("Типоразмер"), accessory.get("size")):
        return False
    specs = accessory.get("specs") or {}
    for catalogue_key, accessory_keys in _COMPAT_FIELDS.items():
        declared = next((specs[k] for k in accessory_keys if specs.get(k)), None)
        if not _value_fits(values.get(catalogue_key), declared):
            return False
    match = _EXECUTION_SUFFIX.search(str(accessory.get("name") or "").strip())
    execution = (_EXECUTION_BY_SUFFIX[match.group(1).upper()] if match
                 else (implied or {}).get(str(accessory.get("article", ""))))
    if execution and values.get("Исполнение"):
        return values["Исполнение"] == execution
    return True


def listing_for_product(article: str) -> tuple[str, str] | None:
    """Серия и лист прайс-листа для товара — или None, если его там нет.

    Совместимость объявляет прайс-лист (ARCHITECTURE.md §5, п.3a), поэтому и
    серия для поиска аксессуаров берётся из его собственной строки товара.
    Имя серии в каталоге 1С — другое поле из другого источника, и совпадать
    они не обязаны: серию «YCM8 PV» прайс-лист называет «YCM8».
    """
    needle = article.strip().upper()
    if not needle:
        return None
    with _connect() as connection:
        row = connection.execute(
            "SELECT series, sheet FROM pricelist_items "
            "WHERE UPPER(article) = ? AND is_accessory = 0 AND series IS NOT NULL "
            "LIMIT 1",
            (needle,),
        ).fetchone()
    return (row[0], row[1]) if row and row[0] else None


def accessories_for_product(product: dict[str, Any]) -> list[dict[str, Any]]:
    """Accessories for this product's series, narrowed to what actually fits."""
    listing = listing_for_product(str(product.get("vendor_code", "")))
    if listing is not None:
        rows = accessories_for_series(listing[0], sheet=listing[1])
    else:
        # 429 товаров каталога из 11942 в прайс-листе отсутствуют — для них
        # остаётся единственное, что есть: имя серии из каталога.
        rows = accessories_for_series(catalog_search.series(product))
    implied = implied_executions(rows)
    return [row for row in rows if compatible_with(product, row, implied)]


# Accessories carrying neither "Подтип" nor a catalogue record at all.
LEFTOVER_GROUP = "Прочие"


def group_accessories(rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Bucket accessories by their "Подтип", alphabetically by group name.

    Grouping is what makes a series' accessories fit a Telegram message at all:
    YCM8 has 170 of them, about 12000 characters against a 4096 limit, while no
    single subtype exceeds 35 rows. 849 of 970 accessories carry "Подтип"; of
    the rest, 265 of 275 are thermal relays, whose type_item names the group
    just as well, so it stands in when the attribute is missing.
    """
    catalog = catalog_search.by_vendor_code()
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        product = catalog.get(str(row.get("article", "")).upper())
        name = ""
        if product is not None:
            name = (catalog_search.spec(product).get("Подтип", "").strip()
                    or str(product.get("type_item", "")).strip())
        buckets.setdefault(name or LEFTOVER_GROUP, []).append(row)
    return sorted(buckets.items(), key=lambda item: item[0].lower())


def format_accessory_group(subject: str, group: str, rows: list[dict[str, Any]],
                           show_stock: bool = True) -> str:
    """One accessory per line — article, name, price, stock — the same layout
    the catalogue itself uses for a list of candidates. Price and stock come
    from the catalogue snapshot (99% of accessory articles are in it); the six
    that are not fall back to the name the price list carries."""
    catalog = catalog_search.by_vendor_code()
    lines = [f"{group} для {subject} — {len(rows)} поз.:", ""]
    for row in rows:
        code = str(row.get("article", "")).upper()
        product = catalog.get(code)
        if product is None:
            lines.append(f"• {code} · {row.get('name') or '—'} · нет в каталоге")
            continue
        line = (f"• {code} · {catalog_search.display_name(product)}"
                f" · {catalog_search.price_line(code)}"
                f" · {catalog_search.availability(product, short=True, show_stock=show_stock)}")
        if catalog_search.discontinued(product):
            line += " ⚠️"
        lines.append(line)
    return "\n".join(lines)


def active_version_label() -> str:
    """Human-readable stamp of which price-list import backs current answers."""
    with _connect() as connection:
        row = connection.execute(
            "SELECT d.version, d.uploaded_at FROM documents d "
            "JOIN pricelist_items pi ON pi.document_id = d.id LIMIT 1"
        ).fetchone()
    if not row:
        return "версия не определена"
    version, uploaded_at = row
    return f"версия {version}, загружен {str(uploaded_at)[:19].replace('T', ' ')}"
