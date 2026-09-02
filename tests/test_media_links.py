"""media_links — таблица номенклатуры «Артикул → фото и сертификат».

Ключ здесь артикул, а не серия (этим таблица отличается от passport_links):
у 11 552 позиций 654 разных фотографии и 14 сертификатов, один файл делят
сотни артикулов. Отсюда и кэш file_id по ссылке, а не по товару.

Столбцы ищутся по шапке: файл ведётся руками и приходит каждый раз со своим
набором и порядком столбцов. Боевой data/knowledge.db тесты не трогают.
"""
import sqlite3

import openpyxl
import pytest

import media_links
from media_links import MediaLinks

_PHOTO = "https://25.lgprk.ru/share?token=20644a7a-1661-48f1-b4cd-c881987105c246c638fa"
_CERT = "https://25.lgprk.ru/share?token=41660046-1e70-4af7-ab97-182ec10a97524990849a"
_MODEL = "https://25.lgprk.ru/share?token=11f73f7c-7419-4868-8d30-1826ebd95d23b223bdcc"

_HEADER = ["Артикул", "Описание", "Фото", "Ссылки на фото", "Сертификат", "Ссылки на сертификаты",
           "Название файла 3Dmodel", "ссылка на 3Dmodel"]


def _workbook(tmp_path, rows, header=None):
    path = tmp_path / "nomenclature.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(list(header if header is not None else _HEADER))
    for row in rows:
        sheet.append(list(row))
    workbook.save(path)
    workbook.close()
    return path


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "media_test.db"
    monkeypatch.setattr(media_links, "DB_PATH", db_path)

    def _make_document(document_id: int, version: int = 1) -> None:
        connection = sqlite3.connect(db_path)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, version INTEGER, uploaded_at TEXT)"
            )
            connection.execute(
                "INSERT INTO documents (id, version, uploaded_at) VALUES (?, ?, ?)",
                (document_id, version, "2026-08-29T10:00:00"),
            )
            connection.commit()
        finally:
            connection.close()

    return _make_document


# --- Разбор файла ------------------------------------------------------------

def test_an_article_row_carries_both_links(tmp_path):
    path = _workbook(tmp_path, [
        ["B05012", "YCM3YP-100", r".\Photo\YCM3YP-100.png", _PHOTO, r".\Certificates\C-00883.jpg", _CERT],
    ])
    assert media_links.parse_workbook(path) == [
        MediaLinks("B05012", _PHOTO, "YCM3YP-100.png", _CERT, "C-00883.jpg")
    ]


def test_columns_are_found_by_heading_not_by_position(tmp_path):
    """Файл ведётся руками: столбцы в нём переезжают от версии к версии."""
    path = _workbook(
        tmp_path,
        [[_CERT, "B05012", _PHOTO]],
        header=["Ссылки на сертификаты", "Артикул", "Ссылки на фото"],
    )
    assert media_links.parse_workbook(path) == [MediaLinks("B05012", _PHOTO, None, _CERT, None)]


def test_a_3d_model_is_read_from_its_own_columns(tmp_path):
    path = _workbook(tmp_path, [
        ["B05012", "YCM3YP-100", None, None, None, None, "CJX2-F1154-3D.stp", _MODEL],
    ])
    assert media_links.parse_workbook(path) == [
        MediaLinks("B05012", None, None, None, None, _MODEL, "CJX2-F1154-3D.stp")
    ]


def test_the_3d_link_column_is_not_mistaken_for_the_name_column(tmp_path):
    """«Название файла 3Dmodel» и «ссылка на 3Dmodel» отличаются одним словом:
    если имя разбирается раньше ссылки, оно забирает её столбец себе."""
    path = _workbook(
        tmp_path,
        [[_MODEL, "B05012", "CJX2-F1154-3D.stp"]],
        header=["ссылка на 3Dmodel", "Артикул", "Название файла 3Dmodel"],
    )
    found = media_links.parse_workbook(path)[0]
    assert found.model_url == _MODEL and found.model_name == "CJX2-F1154-3D.stp"


def test_a_row_with_only_a_photo_keeps_the_photo(tmp_path):
    path = _workbook(tmp_path, [["E010076", "Кнопка", r".\Photo\AD16.jpg", _PHOTO, None, None]])
    assert media_links.parse_workbook(path) == [MediaLinks("E010076", _PHOTO, "AD16.jpg", None, None)]


def test_a_row_without_any_link_is_dropped(tmp_path):
    """Пустая строка ничего не запрещает и ничего не даёт — хранить её незачем."""
    path = _workbook(tmp_path, [["B05012", "YCM3YP-100", None, None, None, None]])
    assert media_links.parse_workbook(path) == []


def test_a_file_with_only_the_3d_column_is_accepted(tmp_path):
    """Столбцы 3D — такие же ссылки, как фото и сертификат: файла с одними
    только моделями достаточно, чтобы таблица имела смысл."""
    path = _workbook(tmp_path, [["B05012", _MODEL]],
                     header=["Артикул", "ссылка на 3Dmodel"])
    assert media_links.parse_workbook(path)[0].model_url == _MODEL


def test_a_row_without_an_article_is_dropped(tmp_path):
    path = _workbook(tmp_path, [[None, "Без артикула", None, _PHOTO, None, None]])
    assert media_links.parse_workbook(path) == []


def test_a_zero_instead_of_a_link_is_not_a_link(tmp_path):
    """В таблицу попадают нули и неразвёрнутые формулы — менеджеру нельзя
    прислать «0» вместо фотографии."""
    path = _workbook(tmp_path, [
        ["B05012", "YCM3YP-100", None, 0, None, "=VLOOKUP(A2,Spisok!A:B,2,0)"],
    ])
    assert media_links.parse_workbook(path) == []


def test_the_article_is_stored_upper_case_and_trimmed(tmp_path):
    path = _workbook(tmp_path, [["  b05012 ", "YCM3YP-100", None, _PHOTO, None, None]])
    assert media_links.parse_workbook(path)[0].article == "B05012"


def test_the_file_name_keeps_only_the_name_not_the_folder(tmp_path):
    path = _workbook(tmp_path, [["B05012", "x", r".\Photo\sub\YCM3YP-100.png", _PHOTO, None, None]])
    assert media_links.parse_workbook(path)[0].photo_name == "YCM3YP-100.png"


def test_a_file_without_the_article_column_is_rejected(tmp_path):
    path = _workbook(tmp_path, [[_PHOTO]], header=["Ссылки на фото"])
    with pytest.raises(ValueError, match="Артикул"):
        media_links.parse_workbook(path)


def test_a_file_without_any_link_column_is_rejected(tmp_path):
    path = _workbook(tmp_path, [["B05012"]], header=["Артикул"])
    with pytest.raises(ValueError, match="ссылк"):
        media_links.parse_workbook(path)


# --- Импорт и подбор ---------------------------------------------------------

def test_an_imported_article_is_found_by_its_code(isolated_db):
    isolated_db(1)
    media_links.import_links(1, [MediaLinks("B05012", _PHOTO, "YCM3YP-100.png", _CERT, "C-00883.jpg")])
    found = media_links.for_article("B05012")
    assert found.photo_url == _PHOTO and found.cert_name == "C-00883.jpg"


def test_the_article_is_matched_regardless_of_case(isolated_db):
    isolated_db(1)
    media_links.import_links(1, [MediaLinks("B05012", _PHOTO, None, None, None)])
    assert media_links.for_article(" b05012 ").photo_url == _PHOTO


def test_an_unknown_article_has_no_media(isolated_db):
    isolated_db(1)
    media_links.import_links(1, [MediaLinks("B05012", _PHOTO, None, None, None)])
    assert media_links.for_article("D020186") is None


def test_a_new_version_of_the_table_replaces_the_previous_one(isolated_db):
    isolated_db(1)
    isolated_db(2, version=2)
    media_links.import_links(1, [MediaLinks("B05012", _PHOTO, None, None, None)])
    media_links.import_links(2, [MediaLinks("D020186", _PHOTO, None, None, None)])
    assert media_links.for_article("B05012") is None
    assert media_links.for_article("D020186") is not None


def test_counts_report_articles_by_photo_certificate_and_model(isolated_db):
    isolated_db(1)
    media_links.import_links(1, [
        MediaLinks("B05012", _PHOTO, None, _CERT, None, _MODEL, None),
        MediaLinks("E010076", _PHOTO, None, None, None),
    ])
    assert media_links.counts() == (2, 1, 1)


def test_distinct_urls_collapse_a_file_shared_by_many_articles(isolated_db):
    """Прогрев кэша гоняет файлы, а не товары: один сертификат стоит у 5115
    артикулов, и качать его пять тысяч раз незачем."""
    isolated_db(1)
    media_links.import_links(1, [
        MediaLinks("B05012", _PHOTO, None, _CERT, None, _MODEL, None),
        MediaLinks("E010076", _PHOTO, None, _CERT, None, _MODEL, None),
    ])
    assert media_links.distinct_urls("photo") == [_PHOTO]
    assert media_links.distinct_urls("cert") == [_CERT]
    assert media_links.distinct_urls("model") == [_MODEL]


def test_a_model_is_stored_and_found_by_the_article(isolated_db):
    isolated_db(1)
    media_links.import_links(1, [
        MediaLinks("B05012", None, None, None, None, _MODEL, "CJX2-F1154-3D.stp"),
    ])
    found = media_links.for_article("B05012")
    assert found.model_url == _MODEL and found.model_name == "CJX2-F1154-3D.stp"


def test_a_database_from_before_the_models_gains_the_new_columns(isolated_db, tmp_path):
    """Боевая база создана прежней схемой, а CREATE TABLE IF NOT EXISTS её не
    меняет: без миграции 3D-столбцов там не появится и после перезаливки."""
    isolated_db(1)
    connection = sqlite3.connect(media_links.DB_PATH)
    try:
        connection.execute("DROP TABLE IF EXISTS media_links")
        connection.execute(
            "CREATE TABLE media_links ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "document_id INTEGER NOT NULL REFERENCES documents(id),"
            "article TEXT NOT NULL, photo_url TEXT, photo_name TEXT,"
            "cert_url TEXT, cert_name TEXT, UNIQUE(document_id, article))"
        )
        connection.commit()
    finally:
        connection.close()

    media_links.import_links(1, [
        MediaLinks("B05012", None, None, None, None, _MODEL, "CJX2-F1154-3D.stp"),
    ])
    assert media_links.for_article("B05012").model_url == _MODEL


# --- Кэш file_id -------------------------------------------------------------

def test_a_remembered_file_id_is_given_back(isolated_db):
    isolated_db(1)
    media_links.remember_file_id(_PHOTO, "photo", "AgACAgIAAxkBAAI")
    assert media_links.cached_file_id(_PHOTO, "photo") == "AgACAgIAAxkBAAI"


def test_a_photo_and_a_document_are_cached_separately(isolated_db):
    """Telegram выдаёт разные file_id на одну и ту же ссылку в зависимости от
    того, ушла она фотографией или документом; перепутать их нельзя."""
    isolated_db(1)
    media_links.remember_file_id(_CERT, "cert", "BQACAgIAAxkBAAI")
    assert media_links.cached_file_id(_CERT, "photo") is None


def test_a_rejected_file_id_is_forgotten(isolated_db):
    isolated_db(1)
    media_links.remember_file_id(_PHOTO, "photo", "AgACAgIAAxkBAAI")
    media_links.forget_file_id(_PHOTO, "photo")
    assert media_links.cached_file_id(_PHOTO, "photo") is None


def test_the_cache_survives_a_new_version_of_the_table(isolated_db):
    """Ссылки вычищаются при каждой перезаливке, а file_id привязан к самой
    ссылке: заново качать 654 фотографии из-за правки в столбце «Описание»
    было бы расточительно."""
    isolated_db(1)
    isolated_db(2, version=2)
    media_links.import_links(1, [MediaLinks("B05012", _PHOTO, None, None, None)])
    media_links.remember_file_id(_PHOTO, "photo", "AgACAgIAAxkBAAI")
    media_links.import_links(2, [MediaLinks("B05012", _PHOTO, None, None, None)])
    assert media_links.cached_file_id(_PHOTO, "photo") == "AgACAgIAAxkBAAI"
