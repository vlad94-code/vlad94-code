"""pricelist_parser.parse_workbook() against the real CNC price-list file.

uploads/ is in .gitignore, so the file never reaches git and these tests
skip cleanly on a checkout that doesn't have it locally."""
from pathlib import Path

import pytest

from pricelist_parser import parse_workbook

PRICELIST_PATH = Path("uploads") / "8865_248_Prays-list-CNC_V3.8.2.xlsx"

requires_pricelist = pytest.mark.skipif(not PRICELIST_PATH.exists(), reason=f"{PRICELIST_PATH} отсутствует локально")


@pytest.fixture(scope="module")
def items():
    return parse_workbook(PRICELIST_PATH)


@requires_pricelist
def test_row_counts_per_sheet_within_tolerance(items):
    # Верифицировано вручную на этой версии файла; допуск на случай
    # небольших правок в будущей версии прайс-листа.
    expected = {
        "A (модульное)": 4551,
        "B(силовое)": 3523,
        "С(коммутация)": 1166,
        "EN (DC)": 1029,
        "D (реле)": 15,
        "E (светосигн)": 1231,
        "F(блоки питания)": 83,
        "G (измерительное оборудование)": 279,
        "Выведено из ассортимента": 6570,
    }
    counts: dict[str, int] = {}
    for item in items:
        counts[item.sheet] = counts.get(item.sheet, 0) + 1
    for sheet, expected_count in expected.items():
        actual = counts.get(sheet, 0)
        assert abs(actual - expected_count) <= max(5, expected_count * 0.05), (
            f"{sheet}: ожидали ~{expected_count}, получили {actual}"
        )


@requires_pricelist
def test_accessory_detection_via_type_column(items):
    # Регресс на правило: "Тип" содержит "Принадлеж", не "Аксессуары"/"Подтип".
    expected = {
        "A (модульное)": 49,
        "B(силовое)": 588,
        "С(коммутация)": 249,
        "EN (DC)": 51,
        "E (светосигн)": 33,
    }
    counts: dict[str, int] = {}
    for item in items:
        if item.is_accessory:
            counts[item.sheet] = counts.get(item.sheet, 0) + 1
    for sheet, expected_count in expected.items():
        assert counts.get(sheet, 0) == expected_count, f"{sheet}: ожидали {expected_count} аксессуаров"


@requires_pricelist
def test_known_accessory_row(items):
    row = next(item for item in items if item.article == "B0401711")
    assert row.is_accessory is True
    assert row.compatible_series == ["YCW3"]
    assert row.series is None
    assert row.size == "2000"
    assert row.sheet == "B(силовое)"


@requires_pricelist
def test_multi_series_accessory(items):
    row = next(item for item in items if item.article == "A000289")
    assert row.compatible_series == ["YCB6H-63", "YCB7-63N", "YCB6HLE-63", "YCB6HLN-63"]


@requires_pricelist
def test_non_accessory_row_keeps_single_series(items):
    row = next(item for item in items if item.sheet == "B(силовое)" and not item.is_accessory and item.series)
    assert row.compatible_series == []
    assert row.series


@requires_pricelist
def test_discontinued_in_stock_detected(items):
    assert any(item.status == "discontinued_in_stock" for item in items)


@requires_pricelist
def test_price_column_never_stored(items):
    for item in items[:200]:
        assert not any(key.startswith("Цена") for key in item.specs)
