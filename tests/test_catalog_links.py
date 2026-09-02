"""catalog_links.py — справочник «каталог → ссылка на скачивание».

Файл ведётся руками и присылается целиком новой версией, поэтому проверяется
то же, что у passport_links: столбцы ищутся по шапке, а не по позиции, и
строка без настоящей ссылки в справочник не попадает — менеджеру нельзя
прислать «0» вместо каталога.

Отдельно проверяется ключ кнопки. Он считается от названия, а не от номера
строки: кнопка живёт в истории чата дольше, чем версия файла, и после
перестановки строк не должна открыть чужой каталог.
"""
from pathlib import Path

import openpyxl
import pytest

import catalog_links


def _workbook(rows: list[tuple], path: Path) -> Path:
    book = openpyxl.Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    book.save(path)
    return path


_HEADER = ("Название каталога", "ссылка на каталог")
_URL = "https://25.lgprk.ru/share?token=5f9c6a82-cf90-4f58-a689-3f5f81feec86c4564ab6"


def test_parse_workbook_reads_the_name_and_the_link(tmp_path):
    path = _workbook([_HEADER, ("каталог трансформаторы", _URL)], tmp_path / "c.xlsx")

    catalogs = catalog_links.parse_workbook(path)

    assert [(c.name, c.url) for c in catalogs] == [("каталог трансформаторы", _URL)]


def test_parse_workbook_finds_the_columns_by_header_not_by_position(tmp_path):
    """Файл ведётся руками, столбцы в нём переезжают."""
    path = _workbook(
        [("ссылка на каталог", "Название каталога"), (_URL, "каталог трансформаторы")],
        tmp_path / "c.xlsx",
    )

    catalogs = catalog_links.parse_workbook(path)

    assert [(c.name, c.url) for c in catalogs] == [("каталог трансформаторы", _URL)]


@pytest.mark.parametrize("junk", [None, "", "0", 0, "=VLOOKUP(A2;Лист2!A:B;2;0)"])
def test_parse_workbook_skips_a_row_without_a_real_link(tmp_path, junk):
    """Пустая ячейка, ноль или неразвёрнутая формула — «каталога пока нет»."""
    path = _workbook(
        [_HEADER, ("каталог трансформаторы", junk), ("каталог ячейки среднего напряжения", _URL)],
        tmp_path / "c.xlsx",
    )

    catalogs = catalog_links.parse_workbook(path)

    assert [c.name for c in catalogs] == ["каталог ячейки среднего напряжения"]


def test_parse_workbook_skips_a_link_without_a_name(tmp_path):
    path = _workbook([_HEADER, (None, _URL)], tmp_path / "c.xlsx")

    assert catalog_links.parse_workbook(path) == []


def test_parse_workbook_rejects_a_file_without_the_needed_columns(tmp_path):
    path = _workbook([("артикул", "цена"), ("B030001", 100)], tmp_path / "c.xlsx")

    with pytest.raises(ValueError, match="Название"):
        catalog_links.parse_workbook(path)


def test_parse_workbook_rejects_an_empty_file(tmp_path):
    path = _workbook([], tmp_path / "c.xlsx")

    with pytest.raises(ValueError):
        catalog_links.parse_workbook(path)


# --- Ключ кнопки --------------------------------------------------------------

def test_key_is_the_same_for_the_same_name():
    """Кнопка из вчерашнего сообщения должна открыть тот же каталог."""
    assert catalog_links.Catalog("каталог трансформаторы", _URL).key == \
        catalog_links.Catalog("каталог трансформаторы", "https://другая.ссылка").key


def test_key_differs_between_catalogs():
    keys = {catalog_links.Catalog(name, _URL).key
            for name in ("каталог трансформаторы", "каталог воздушные выключатели")}
    assert len(keys) == 2


def test_key_fits_telegram_callback_data():
    """callback_data — не больше 64 байт вместе с префиксом «cat:»."""
    key = catalog_links.Catalog("каталог выключатели-нагрузки рубильники,ПВР,плавкие вставки", _URL).key
    assert len(f"cat:{key}".encode()) <= 64


# --- Подпись ------------------------------------------------------------------
# Слово «каталог» повторяется в каждом названии, и в списке кнопок это шум —
# но убрать его нечем: половина названий стоит в родительном падеже
# («каталог модульного оборудования»), и без морфологии кнопка получилась бы
# «Модульного оборудования». Разнобой в одной клавиатуре хуже повтора слова,
# поэтому подпись — всё название целиком, как в файле.

def test_title_keeps_the_whole_name_from_the_file():
    assert catalog_links.Catalog("каталог трансформаторы", _URL).title == "Каталог трансформаторы"


def test_title_leaves_a_latin_series_name_alone():
    """capitalize() опускает хвост строки — «PV» из названия пропал бы."""
    assert catalog_links.Catalog("каталог оборудования постоянного тока PV", _URL).title == \
        "Каталог оборудования постоянного тока PV"


# --- Справочник целиком -------------------------------------------------------

def test_catalogs_reads_the_file_next_to_the_bot(tmp_path, monkeypatch):
    path = _workbook([_HEADER, ("каталог трансформаторы", _URL)], tmp_path / "c.xlsx")
    monkeypatch.setattr(catalog_links, "XLSX_PATH", path)
    catalog_links.forget()

    assert [c.name for c in catalog_links.catalogs()] == ["каталог трансформаторы"]


def test_catalogs_is_empty_when_the_file_is_missing(tmp_path, monkeypatch, caplog):
    """Бот не должен падать из-за забытого файла — команда честно скажет,
    что справочник недоступен."""
    monkeypatch.setattr(catalog_links, "XLSX_PATH", tmp_path / "нет-такого.xlsx")
    catalog_links.forget()

    assert catalog_links.catalogs() == []


def test_catalogs_is_empty_when_the_file_is_broken(tmp_path, monkeypatch):
    path = tmp_path / "c.xlsx"
    path.write_bytes(b"not a workbook")
    monkeypatch.setattr(catalog_links, "XLSX_PATH", path)
    catalog_links.forget()

    assert catalog_links.catalogs() == []


def test_find_returns_the_catalog_behind_the_button(tmp_path, monkeypatch):
    path = _workbook([_HEADER, ("каталог трансформаторы", _URL)], tmp_path / "c.xlsx")
    monkeypatch.setattr(catalog_links, "XLSX_PATH", path)
    catalog_links.forget()

    found = catalog_links.find(catalog_links.catalogs()[0].key)

    assert found is not None and found.url == _URL


def test_find_returns_nothing_for_a_catalog_that_left_the_file(tmp_path, monkeypatch):
    path = _workbook([_HEADER, ("каталог трансформаторы", _URL)], tmp_path / "c.xlsx")
    monkeypatch.setattr(catalog_links, "XLSX_PATH", path)
    catalog_links.forget()

    assert catalog_links.find("deadbeef") is None


# --- Настоящий файл в репозитории ---------------------------------------------

def test_the_shipped_file_parses():
    """Справочник лежит в knowledge/ и читается как есть — без пересборки."""
    catalog_links.forget()
    catalogs = catalog_links.catalogs()

    assert len(catalogs) == 10
    assert all(c.url.startswith("https://") for c in catalogs)
    assert len({c.key for c in catalogs}) == len(catalogs)
