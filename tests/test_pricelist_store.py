"""pricelist_store — импорт в изолированную БД, поиск аксессуаров, вычистка
предыдущей версии при повторной загрузке. Не трогает боевой data/knowledge.db."""
import sqlite3

import pytest

import catalog_search
import pricelist_store
from pricelist_parser import ParsedItem


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "pricelist_test.db"
    monkeypatch.setattr(pricelist_store, "DB_PATH", db_path)

    def _make_document(document_id: int, version: int = 1) -> None:
        connection = sqlite3.connect(db_path)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, version INTEGER, uploaded_at TEXT)"
            )
            connection.execute(
                "INSERT INTO documents (id, version, uploaded_at) VALUES (?, ?, ?)",
                (document_id, version, "2026-08-22T10:00:00"),
            )
            connection.commit()
        finally:
            connection.close()

    return _make_document


def _item(article: str, is_accessory: bool = False, series: str | None = None, compatible_series=None,
          sheet: str = "B(силовое)", size: str = "100") -> ParsedItem:
    return ParsedItem(
        article=article,
        sheet=sheet,
        category_code="B",
        type_field="Принадлежности к силовому оборудованию" if is_accessory else "Автоматический выключатель",
        name=f"Товар {article}",
        series=series,
        size=size,
        is_accessory=is_accessory,
        status="active",
        specs={"Типоразмер": size},
        compatible_series=compatible_series or [],
    )


def test_import_and_lookup_accessories(isolated_db):
    isolated_db(document_id=1)
    items = [
        _item("B0401711", is_accessory=True, compatible_series=["YCW3"]),
        _item("A000289", is_accessory=True, compatible_series=["YCB6H-63", "YCB7-63N"]),
        _item("B050044", series="YCQ2"),
    ]
    inserted = pricelist_store.import_items(1, items)
    assert inserted == 3

    rows = pricelist_store.accessories_for_series("YCW3")
    assert [row["article"] for row in rows] == ["B0401711"]

    rows = pricelist_store.accessories_for_series("ycb7-63n")  # case-insensitive
    assert [row["article"] for row in rows] == ["A000289"]

    assert pricelist_store.accessories_for_series("NOPE") == []


def test_reimport_prunes_previous_version(isolated_db):
    make_document = isolated_db
    make_document(document_id=1, version=1)
    pricelist_store.import_items(1, [_item("B0401711", is_accessory=True, compatible_series=["YCW3"])])
    assert len(pricelist_store.accessories_for_series("YCW3")) == 1

    make_document(document_id=2, version=2)
    pricelist_store.import_items(2, [_item("A000289", is_accessory=True, compatible_series=["YCW3"])])

    rows = pricelist_store.accessories_for_series("YCW3")
    assert [row["article"] for row in rows] == ["A000289"], "старая версия (B0401711) должна быть вычищена"


def test_discontinued_items_excluded_from_accessories(isolated_db):
    isolated_db(document_id=1)
    item = _item("B0401711", is_accessory=True, compatible_series=["YCW3"])
    discontinued = ParsedItem(**{**item.__dict__, "status": "discontinued"})
    pricelist_store.import_items(1, [discontinued])
    assert pricelist_store.accessories_for_series("YCW3") == []


def test_active_version_label(isolated_db):
    isolated_db(document_id=1, version=3)
    pricelist_store.import_items(1, [_item("B0401711", is_accessory=True, compatible_series=["YCW3"])])
    label = pricelist_store.active_version_label()
    assert "3" in label


def test_active_version_label_with_no_import_yet(isolated_db):
    isolated_db(document_id=1)
    assert pricelist_store.active_version_label() == "версия не определена"


# --- Группировка аксессуаров по Подтипу --------------------------------------

def _catalog_row(code, name, type_item, subtype=None):
    specs = {"Серия": "YCM8"}
    if subtype:
        specs["Подтип"] = subtype
    return {"vendor_code": code, "name": name, "type_item": type_item,
            "specification": [{"name": k, "value": v} for k, v in specs.items()]}


@pytest.fixture
def accessory_catalog(monkeypatch):
    rows = [
        _catalog_row("A1", "Доп. контакт F4-DN22", "Принадлежности", "Дополнительный контакт"),
        _catalog_row("A2", "Моторный привод YCM8", "Принадлежности", "Моторный привод"),
        _catalog_row("A3", "Аварийный контакт", "Принадлежности", "Аварийный контакт"),
        # Без «Подтипа» — группа берётся из type_item (265 из 275 таких — тепловые реле).
        _catalog_row("A4", "Тепловое реле JR28-36", "Тепловое реле"),
    ]
    monkeypatch.setattr(catalog_search, "products", lambda: rows)
    monkeypatch.setattr(catalog_search, "price_map", lambda: {"A1": {"base_price": "100,00"}})
    monkeypatch.setattr(catalog_search, "stock_map", lambda: {"A1": 7})
    monkeypatch.setattr(catalog_search, "transit_map", lambda: {})
    catalog_search.clear_cache()
    yield rows
    catalog_search.clear_cache()


def _accessory_rows():
    return [
        {"article": "A4", "name": "из прайса", "size": None, "sheet": "", "type_field": "", "specs": {}},
        {"article": "A2", "name": "из прайса", "size": None, "sheet": "", "type_field": "", "specs": {}},
        {"article": "A1", "name": "из прайса", "size": None, "sheet": "", "type_field": "", "specs": {}},
        {"article": "A3", "name": "из прайса", "size": None, "sheet": "", "type_field": "", "specs": {}},
        {"article": "A9", "name": "Нет в каталоге", "size": None, "sheet": "", "type_field": "", "specs": {}},
    ]


def test_groups_are_alphabetical(accessory_catalog):
    groups = pricelist_store.group_accessories(_accessory_rows())
    assert [name for name, _ in groups] == [
        "Аварийный контакт", "Дополнительный контакт", "Моторный привод",
        "Прочие", "Тепловое реле",
    ]


def test_missing_subtype_falls_back_to_type_item(accessory_catalog):
    groups = dict(pricelist_store.group_accessories(_accessory_rows()))
    assert [r["article"] for r in groups["Тепловое реле"]] == ["A4"]


def test_accessory_absent_from_the_catalog_goes_to_the_leftovers(accessory_catalog):
    groups = dict(pricelist_store.group_accessories(_accessory_rows()))
    assert [r["article"] for r in groups["Прочие"]] == ["A9"]


def test_group_listing_carries_price_and_stock(accessory_catalog):
    groups = dict(pricelist_store.group_accessories(_accessory_rows()))
    text = pricelist_store.format_accessory_group("YCM8", "Дополнительный контакт", groups["Дополнительный контакт"])
    assert "A1" in text and "100,00 р." in text and "7 шт." in text


def test_group_listing_uses_the_pricelist_name_when_not_in_the_catalog(accessory_catalog):
    groups = dict(pricelist_store.group_accessories(_accessory_rows()))
    text = pricelist_store.format_accessory_group("YCM8", "Прочие", groups["Прочие"])
    assert "Нет в каталоге" in text


# --- Обратное направление: к чему подходит аксессуар ------------------

def test_accessory_is_recognised_as_such(isolated_db):
    isolated_db(document_id=1)
    pricelist_store.import_items(1, [
        _item("C000213", is_accessory=True, compatible_series=["CJX2I", "CJX2-D"]),
        _item("B030001", series="YCM3"),
    ])
    assert pricelist_store.is_accessory("C000213") is True
    assert pricelist_store.is_accessory("B030001") is False
    assert pricelist_store.is_accessory("НЕТТАКОГО") is False


def test_series_an_accessory_fits_are_sorted(isolated_db):
    """Карточка C000213 предлагала аксессуары к самой себе — этот
    товар сам аксессуар, и осмыслен только обратный вопрос."""
    isolated_db(document_id=1)
    pricelist_store.import_items(1, [
        _item("C000213", is_accessory=True, compatible_series=["CJX2S", "CJX2-D", "CJX2I"]),
    ])
    assert pricelist_store.series_for_accessory("C000213") == ["CJX2-D", "CJX2I", "CJX2S"]
    assert pricelist_store.series_for_accessory("c000213") == ["CJX2-D", "CJX2I", "CJX2S"]


@pytest.fixture
def stub_catalog(monkeypatch):
    """Каталог 1С под контролем теста: имена серий в нём свои, не прайсовые."""
    def _install(rows):
        monkeypatch.setattr(catalog_search, "products", lambda: rows)
        catalog_search.clear_cache()

    yield _install
    catalog_search.clear_cache()


def test_a_dc_accessory_points_at_the_dc_series_not_the_ac_one(isolated_db, stub_catalog):
    """У принадлежности постоянного тока кнопка вела на 620 автоматов
    переменного тока: в прайсе обе серии зовутся «YCM8», а каталог 1С
    называет постоянную «YCM8 PV»."""
    isolated_db(document_id=1)
    pricelist_store.import_items(1, [
        _item("EN010005", is_accessory=True, compatible_series=["YCM8"],
              sheet="EN (DC)", size="250/320"),
        _item("EN010015", series="YCM8", sheet="EN (DC)", size="250"),
        _item("B020163", series="YCM8", sheet="B(силовое)", size="250"),
    ])
    stub_catalog([_catalog_product("EN010015", "YCM8 PV"), _catalog_product("B020163", "YCM8")])
    assert pricelist_store.series_for_accessory("EN010005") == ["YCM8 PV"]


def test_an_ac_accessory_keeps_pointing_at_the_ac_series(isolated_db, stub_catalog):
    isolated_db(document_id=1)
    pricelist_store.import_items(1, [
        _item("B0401711", is_accessory=True, compatible_series=["YCM8"],
              sheet="B(силовое)", size="250"),
        _item("EN010015", series="YCM8", sheet="EN (DC)", size="250"),
        _item("B020163", series="YCM8", sheet="B(силовое)", size="250"),
    ])
    stub_catalog([_catalog_product("EN010015", "YCM8 PV"), _catalog_product("B020163", "YCM8")])
    assert pricelist_store.series_for_accessory("B0401711") == ["YCM8"]


def test_main_product_fits_nothing_in_reverse(isolated_db):
    isolated_db(document_id=1)
    pricelist_store.import_items(1, [_item("B030001", series="YCM3")])
    assert pricelist_store.series_for_accessory("B030001") == []


# --- Серия берётся из прайса, а не из каталога 1С ----------------------------
# Каталог 1С называет серию постоянного тока «YCM8 PV», прайс-лист — «YCM8»:
# по именам они не сходились, и 72 товара DC получали «аксессуаров нет».
# Обратная беда там же: имя «YCM8» в прайсе носят два разных набора — лист
# «B(силовое)» и лист «EN (DC)», — и 92 автомата переменного тока получали
# аксессуары постоянного. Совместимость объявляет прайс-лист, поэтому и серия,
# и лист должны браться из его собственной строки товара.

def _catalog_product(code: str, series: str, size: str = "250"):
    return {"vendor_code": code, "name": code, "type_item": "Выключатель",
            "specification": [{"name": "Серия", "value": series},
                              {"name": "Типоразмер", "value": size}]}


def test_accessories_are_found_when_1c_spells_the_series_differently(isolated_db):
    isolated_db(document_id=1)
    pricelist_store.import_items(1, [
        _item("EN010015", series="YCM8", sheet="EN (DC)", size="250"),
        _item("EN010006", is_accessory=True, compatible_series=["YCM8"],
              sheet="EN (DC)", size="250/320"),
    ])
    rows = pricelist_store.accessories_for_product(_catalog_product("EN010015", "YCM8 PV"))
    assert [row["article"] for row in rows] == ["EN010006"]


def test_one_series_name_on_two_sheets_is_not_one_set_of_accessories(isolated_db):
    isolated_db(document_id=1)
    pricelist_store.import_items(1, [
        _item("B020163", series="YCM8", sheet="B(силовое)", size="250"),
        _item("B0401711", is_accessory=True, compatible_series=["YCM8"],
              sheet="B(силовое)", size="250/320"),
        _item("EN010006", is_accessory=True, compatible_series=["YCM8"],
              sheet="EN (DC)", size="250/320"),
    ])
    rows = pricelist_store.accessories_for_product(_catalog_product("B020163", "YCM8"))
    assert [row["article"] for row in rows] == ["B0401711"], "постоянный ток к переменному не подходит"


def test_a_product_outside_the_pricelist_still_matches_by_series(isolated_db):
    """429 товаров каталога в прайсе отсутствуют — для них остаётся старый путь."""
    isolated_db(document_id=1)
    pricelist_store.import_items(1, [
        _item("B0401711", is_accessory=True, compatible_series=["YCW3"],
              sheet="B(силовое)", size="100"),
    ])
    rows = pricelist_store.accessories_for_product(_catalog_product("НЕТ-В-ПРАЙСЕ", "YCW3", size="100"))
    assert [row["article"] for row in rows] == ["B0401711"]


# --- Совместимость аксессуара с КОНКРЕТНЫМ товаром ---------------------------
# Подбор только по серии показывал к B05012 (YCM3, типоразмер 100, 3P) все
# шесть выкатных корзин YCM3, включая 400/630 и 4P. Прайс объявляет ограничения
# множествами через слэш: size="100/160/250", "Количество полюсов"="3P/4P".

def _product(**spec_values):
    return {"vendor_code": "P1", "name": "P1", "type_item": "Автомат",
            "specification": [{"name": k, "value": v} for k, v in spec_values.items()]}


def _accessory(name="Аксессуар", size=None, **specs):
    return {"article": "A1", "name": name, "size": size, "sheet": "", "type_field": "", "specs": specs}


B05012 = {"Серия": "YCM3", "Типоразмер": "100",
          "Количество полюсов": "3P",
          "Номинальный ток In (А)": "100",
          "Тип расцепителя": "Электронный"}


def test_accessory_without_constraints_fits_anything():
    """Не объявил ничего — значит не ограничивает. Так устроены 1032 из 1045."""
    assert pricelist_store.compatible_with(_product(**B05012), _accessory()) is True


def test_frame_size_set_is_honoured():
    product = _product(**B05012)
    assert pricelist_store.compatible_with(product, _accessory(size="100/160/250")) is True
    assert pricelist_store.compatible_with(product, _accessory(size="400/630")) is False


def test_poles_are_honoured_including_the_typo_spelling():
    """В прайсе встречается «Кол-о полюсов» — опечатка у 20 позиций."""
    product = _product(**B05012)
    poles = "Количество полюсов"
    typo = "Кол-о полюсов"
    assert pricelist_store.compatible_with(product, _accessory(**{poles: "3P"})) is True
    assert pricelist_store.compatible_with(product, _accessory(**{poles: "4P"})) is False
    assert pricelist_store.compatible_with(product, _accessory(**{poles: "3P/4P"})) is True
    assert pricelist_store.compatible_with(product, _accessory(**{typo: "4P"})) is False


def test_current_set_is_compared_as_numbers():
    product = _product(**B05012)
    key = "Номинальный ток In(А)"
    assert pricelist_store.compatible_with(product, _accessory(**{key: "40/100/160"})) is True
    assert pricelist_store.compatible_with(product, _accessory(**{key: "9/12/18/25"})) is False


def test_release_type_is_honoured():
    product = _product(**B05012)
    key = "Тип расцепителя"
    assert pricelist_store.compatible_with(product, _accessory(**{key: "Электронный"})) is True
    assert pricelist_store.compatible_with(product, _accessory(**{key: "Термомагнитный"})) is False


def test_a_constraint_the_product_does_not_declare_is_ignored():
    """У литого корпуса нет «Исполнения» — сравнивать не с чем, значит пропускаем."""
    product = _product(**B05012)
    assert pricelist_store.compatible_with(product, _accessory(name="Рамка F")) is True


def test_fixed_and_withdrawable_suffix_is_honoured():
    """Суффикс F/W в наименовании — единственное место, где прайс различает
    стационарное и выкатное исполнение (13 позиций из 1045)."""
    fixed = _product(Серия="YCW3", Исполнение="стационарный")
    draw = _product(Серия="YCW3", Исполнение="выкатной")
    frame_f = _accessory(name="Уплотнительная рамка YCW3 1600 F")
    frame_w = _accessory(name="Межфазные перегородки YCW8 HU 3P W")
    assert pricelist_store.compatible_with(fixed, frame_f) is True
    assert pricelist_store.compatible_with(draw, frame_f) is False
    assert pricelist_store.compatible_with(draw, frame_w) is True
    assert pricelist_store.compatible_with(fixed, frame_w) is False


def test_unmarked_accessory_is_not_assumed_withdrawable():
    """Правило «нет пометки — значит выкатной» вывести нельзя: у 1032 из 1045
    пометки нет вовсе, и исполнение для них просто не важно."""
    fixed = _product(Серия="YCW3", Исполнение="стационарный")
    assert pricelist_store.compatible_with(fixed, _accessory(name="Доп. контакт YCW3")) is True


# --- Непомеченный близнец пары «… F» ----------------------------------------
# Прайс подписывает исполнение двумя способами: явной парой F/W (4 группы,
# YCW8) и только буквой F (5 групп, уплотнительные рамки YCW3). Во втором
# случае непомеченная половина — выкатная; это выводится из наличия близнеца,
# а не из названия, поэтому остальных 1032 аксессуаров правило не касается.

def _frame(article, name, size="1600"):
    return {"article": article, "name": name, "size": size,
            "sheet": "", "type_field": "", "specs": {}}


_PAIR = [
    _frame("B040171", "Уплотнительная рамка дверного выреза YCW3 1600"),
    _frame("B051727", "Уплотнительная рамка дверного выреза YCW3 1600 F"),
]


def test_unmarked_twin_of_an_f_row_is_withdrawable():
    implied = pricelist_store.implied_executions(_PAIR)
    assert implied == {"B040171": "выкатной"}


def test_unmarked_row_without_a_twin_stays_unconstrained():
    lone = [_frame("B051535", "Уплотнительная рамка дверного выреза YCW3 2500", size="2500")]
    assert pricelist_store.implied_executions(lone) == {}


def test_explicit_pair_needs_no_inference():
    explicit = [
        _frame("B051762", "Уплотнительная рамка YCW8 HU 2500 F", size="2500"),
        _frame("B051764", "Уплотнительная рамка YCW8 HU 2500 W", size="2500"),
    ]
    assert pricelist_store.implied_executions(explicit) == {}


def test_twins_are_matched_within_one_size_only():
    """Рамка на 1600 и рамка на 2000 — разные позиции, не пара."""
    mixed = [
        _frame("B040171", "Уплотнительная рамка дверного выреза YCW3 1600", size="1600"),
        _frame("B051728", "Уплотнительная рамка дверного выреза YCW3 2000 F", size="2000"),
    ]
    assert pricelist_store.implied_executions(mixed) == {}


def test_inferred_execution_filters_the_frame():
    fixed = _product(Серия="YCW3", Исполнение="стационарный")
    draw = _product(Серия="YCW3", Исполнение="выкатной")
    unmarked, marked = _PAIR
    implied = pricelist_store.implied_executions(_PAIR)
    assert pricelist_store.compatible_with(draw, unmarked, implied) is True
    assert pricelist_store.compatible_with(fixed, unmarked, implied) is False
    assert pricelist_store.compatible_with(fixed, marked, implied) is True
    assert pricelist_store.compatible_with(draw, marked, implied) is False


def test_without_the_inference_an_unmarked_row_fits_both():
    """Без сведений о близнеце ограничивать нечем — так и остаётся."""
    fixed = _product(Серия="YCW3", Исполнение="стационарный")
    assert pricelist_store.compatible_with(fixed, _PAIR[0]) is True
