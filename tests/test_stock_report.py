"""stock_report — выгрузка остатков и приходов в Excel.

Остаток из API — это уже ДОСТУПНОЕ количество: сверка с дневным файлом 1С
показала, что у 243 позиций из 249 с резервом он совпадает с колонкой
«Доступно», а не с «В наличии». Нет в выгрузке самого резерва — того,
что позволяет менеджеру пойти и спросить, не протух ли чужой резерв.
"""
import datetime as dt

import pytest

import stock_report


CATALOG = [
    {"vendor_code": "A1", "name": "Автомат YCB9-80M 16А 1P", "type_item": "Модульный автомат", "unit_name": "шт"},
    {"vendor_code": "A2", "name": "Контактор CJX2-F 115A", "type_item": "Контактор", "unit_name": "шт"},
    {"vendor_code": "A3", "name": "Реле 55.32 AC220", "type_item": "Реле", "unit_name": "шт"},
    {"vendor_code": "A4", "name": "Нет ни остатка, ни прихода", "type_item": "Прочее", "unit_name": "шт"},
]
PRICES = {"A1": "304,92", "A2": "1 500,00", "A3": "513,85"}
STOCK = {"A1": 1030.0, "A3": 156.0}
TRANSIT = {
    "A1": [(40.0, "2026-09-07")],
    "A2": [(12.0, "2026-09-07"), (8.0, "2026-09-28")],
}
WHEN = dt.datetime(2026, 8, 25, 9, 30)


@pytest.fixture
def sheet():
    book = stock_report.build_report(CATALOG, PRICES, STOCK, TRANSIT, WHEN)
    return book.active


def _rows(sheet):
    return [[cell for cell in row] for row in sheet.iter_rows(values_only=True)]


def _header_row(sheet):
    for index, row in enumerate(_rows(sheet), start=1):
        if row and row[0] == "Артикул":
            return index, list(row)
    raise AssertionError("шапка не найдена")


def test_column_order_matches_the_1c_export(sheet):
    _, header = _header_row(sheet)
    assert header[:6] == ["Артикул", "Вид номенклатуры", "Наименование", "Ед. изм.", "Цена", "Доступно"]


def test_arrival_dates_become_columns_in_order(sheet):
    _, header = _header_row(sheet)
    assert header[6:] == ["Поступит 07.09.2026", "Поступит 28.09.2026", "Всего поступит"]


def test_sheet_warns_that_reserve_is_not_included():
    """Число верное, но чужого резерва в нём не видно: при нулевом
    доступном товар может лежать в резерве, который уже неактуален."""
    book = stock_report.build_report(CATALOG, PRICES, STOCK, TRANSIT, WHEN)
    text = "\n".join(str(cell) for row in _rows(book.active) for cell in row if cell)
    assert "резерв" in text.lower()
    assert "1С" in text
    assert "складской остаток" not in text.lower(), "число — доступное, не валовый остаток"


def test_generation_time_is_stated(sheet):
    text = "\n".join(str(cell) for row in _rows(sheet) for cell in row if cell)
    assert "25.08.2026" in text


def test_every_article_with_stock_or_incoming_is_listed(sheet):
    start, _ = _header_row(sheet)
    articles = [row[0] for row in _rows(sheet)[start:] if row and row[0]]
    assert articles == ["A1", "A2", "A3"]


def test_article_without_movement_is_left_out(sheet):
    start, _ = _header_row(sheet)
    articles = [row[0] for row in _rows(sheet)[start:] if row and row[0]]
    assert "A4" not in articles, "товар без остатка и без прихода в выгрузке не нужен"


def test_stock_and_price_are_numbers_not_text(sheet):
    start, _ = _header_row(sheet)
    first = _rows(sheet)[start]
    assert first[4] == 304.92
    assert first[5] == 1030


def test_incoming_lands_in_its_date_column(sheet):
    start, _ = _header_row(sheet)
    rows = {row[0]: row for row in _rows(sheet)[start:] if row and row[0]}
    assert rows["A1"][6] == 40 and rows["A1"][7] is None
    assert rows["A2"][6] == 12 and rows["A2"][7] == 8


def test_total_incoming_is_summed(sheet):
    start, _ = _header_row(sheet)
    rows = {row[0]: row for row in _rows(sheet)[start:] if row and row[0]}
    assert rows["A2"][8] == 20
    assert rows["A3"][8] is None


def test_article_only_in_transit_still_gets_its_catalogue_fields(sheet):
    start, _ = _header_row(sheet)
    rows = {row[0]: row for row in _rows(sheet)[start:] if row and row[0]}
    assert rows["A2"][1] == "Контактор"
    assert rows["A2"][2] == "Контактор CJX2-F 115A"
    assert rows["A2"][5] is None, "нулевое доступное пишем пустым, как в исходном файле"


def test_missing_price_is_left_empty(sheet):
    """336 товаров каталога не имеют цены в прайсе — ноль там был бы враньём."""
    book = stock_report.build_report(
        CATALOG, {}, {"A1": 5.0}, {}, WHEN
    )
    start, _ = _header_row(book.active)
    assert _rows(book.active)[start][4] is None


def test_filename_carries_the_date():
    assert stock_report.report_filename(WHEN) == "Остатки CNC 25.08.2026.xlsx"


def test_report_survives_an_empty_snapshot():
    book = stock_report.build_report([], {}, {}, {}, WHEN)
    _, header = _header_row(book.active)
    assert header == ["Артикул", "Вид номенклатуры", "Наименование", "Ед. изм.", "Цена", "Доступно", "Всего поступит"]
