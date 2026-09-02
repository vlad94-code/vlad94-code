"""catalog_search.parse_filters() — граница слова в регулярке «ток» (В-1)."""
import pytest

import catalog_search
from catalog_search import answer, filter_products, parse_filters


def test_current_regex_does_not_match_substring_of_potok():
    """«поток» содержит «ток» как подстроку — до фикса это ложно
    распознавалось как упоминание тока и подставляло случайное число."""
    filters = parse_filters("воздушный выключатель, поток 40 по трубе")
    assert "current" not in filters


def test_current_regex_still_matches_real_current_mention():
    filters = parse_filters("автомат с током 16А")
    assert filters.get("current") == 16.0


def test_current_after_context_still_works():
    filters = parse_filters("воздушный выключатель на 1600")
    assert filters.get("current") == 1600.0


def test_poles_cyrillic_r_is_recognised():
    """«3р» — кириллическая «р» вместо латинской «p». Пользователи набирают
    её с русской раскладки, и до фикса уточнение молча парсилось в пустой
    набор фильтров: сообщение переставало быть уточнением каталога и
    улетало в RAG."""
    assert parse_filters("3р").get("poles") == "3P"


def test_poles_cyrillic_p_abbreviation_is_recognised():
    """«3п» — общепринятое сокращение от «3 полюса»."""
    assert parse_filters("3п").get("poles") == "3P"


def test_poles_latin_p_still_works():
    assert parse_filters("3P").get("poles") == "3P"
    assert parse_filters("3 полюса").get("poles") == "3P"


def test_poles_does_not_match_rubles_or_words():
    """«р» как рубли и «п» внутри слова не должны становиться полюсами."""
    assert "poles" not in parse_filters("цена 3 рубля")
    assert "poles" not in parse_filters("нужно 2 позиции")


def test_cyrillic_poles_refines_existing_series_context():
    """Уточнение поверх прошлых фильтров: серия из прошлого сообщения
    сохраняется, полюса добавляются."""
    filters = parse_filters("3р", {"series": "YCB9-80M"})
    assert filters == {"series": "YCB9-80M", "poles": "3P"}


# --- Характеристика срабатывания (trip curve) --------------------------------
# Реальные значения в снимке CNC API: C (1551), B (1135), D (1054), K (180),
# Z (52). Кириллические омоглифы есть только у B/C/K — «В», «С», «К»;
# у D и Z русских двойников нет, поэтому для них проверяем только латиницу.

_ANCHOR = {"series": "YCB9-80M", "poles": "3P"}


@pytest.mark.parametrize("text,expected", [
    ("B", "B"), ("C", "C"), ("D", "D"), ("K", "K"), ("Z", "Z"),
    ("b", "B"), ("c", "C"), ("d", "D"), ("k", "K"), ("z", "Z"),
])
def test_curve_latin_letters_all_recognised(text, expected):
    assert parse_filters(text, _ANCHOR).get("curve") == expected


@pytest.mark.parametrize("text,expected", [
    ("В", "B"), ("в", "B"),   # кириллические В/в -> B
    ("С", "C"), ("с", "C"),   # кириллические С/с -> C
    ("К", "K"), ("к", "K"),   # кириллические К/к -> K
])
def test_curve_cyrillic_homoglyphs_recognised(text, expected):
    """Пользователь пишет по-русски и набирает «характеристика С» кириллицей."""
    assert parse_filters(text, _ANCHOR).get("curve") == expected


@pytest.mark.parametrize("text,expected", [
    ("характеристика С", "C"),
    ("кривая D", "D"),
    ("тип В", "B"),
])
def test_curve_with_russian_keyword_prefix(text, expected):
    assert parse_filters(text, _ANCHOR).get("curve") == expected


def test_curve_needs_an_anchor_and_is_not_grabbed_from_free_text():
    """Голая буква без контекста каталога по-прежнему не считается кривой
    (см. К-2: catch-all в knowledge_v2.py выдумывал факты из случайных букв)."""
    assert "curve" not in parse_filters("C")
    assert "curve" not in parse_filters("С")


def test_curve_only_refinement_is_handled_by_answer():
    """Ключевой регресс: до фикса answer() считал fresh без prior, гейт кривой
    не открывался, fresh_keys оказывался пуст и уточнение «C» улетало в RAG —
    даже латиницей."""
    text, filters, handled, _w, _e = answer("C", dict(_ANCHOR))
    assert handled is True
    assert filters == {"series": "YCB9-80M", "poles": "3P", "curve": "C"}


def test_cyrillic_curve_only_refinement_is_handled_by_answer():
    text, filters, handled, _w, _e = answer("С", dict(_ANCHOR))
    assert handled is True
    assert filters.get("curve") == "C"


def test_curve_refinement_without_prior_still_falls_through():
    """Без предыдущего поиска «C» — не уточнение каталога."""
    assert answer("C", {})[:3] == (None, {}, False)


@pytest.mark.parametrize("text", [
    "какие есть автоматы с",   # ...автоматы с  -> оборванный предлог, не кривая C
    "что входит в",              # ...в        -> не кривая B
    "YCB9-80M в",
    "цена 3 рубля",
])
def test_trailing_russian_preposition_is_not_a_curve(text):
    """Хвостовая буква в длинном сообщении требует числового фильтра
    из ЭТОГО же сообщения. Перенесённый poles из prior её не открывает."""
    assert "curve" not in parse_filters(text, _ANCHOR)


def test_trailing_curve_after_numeric_filter_in_same_message():
    """Старая рабочая форма — как пишется реальное название товара."""
    assert parse_filters("YCB6H-63 10А 1P 4,5кА C", None).get("curve") == "C"
    assert parse_filters("10А 1P 4,5кА С", None).get("curve") == "C"


# --- Отключающая способность Icu ------------------------------------------------

@pytest.mark.parametrize("text", [
    "6кА",   # кириллица + кириллица
    "6кA",   # кириллица + латиница
    "6kA",   # латиница + латиница
    "6kА",   # латиница + кириллица
    "6 kA",
    "6 кА",
    "6КА",
])
def test_icu_accepts_every_ka_spelling(text):
    """«k» латинская и кириллическая неразличимы на экране; до фикса
    гейт принимал только кириллическую «к»."""
    assert parse_filters(text, None).get("icu") == 6.0


@pytest.mark.parametrize("text", [
    "Icu 50 kA", "Icu 50 кА",
    "отключающая способность 50 kA",
])
def test_icu_keyword_form_accepts_latin_k(text):
    assert parse_filters(text, None).get("icu") == 50.0


def test_icu_refinement_is_handled_by_answer_in_latin():
    """Отчёт пользователя: «6кА» после «3р» уходило в RAG."""
    text, filters, handled, _w, _e = answer("6kA", dict(_ANCHOR))
    assert handled is True
    assert filters == {"series": "YCB9-80M", "poles": "3P", "icu": 6.0}


def test_amperes_are_not_mistaken_for_kiloamperes():
    """«1600A» — ток, а не Icu; латинская «k» не должна это сломать."""
    assert "icu" not in parse_filters("1600A", None)
    assert "icu" not in parse_filters("автомат на 1600 А", None)


_PLAIN_ICU = "Номин. отключающая способность Icu (кА)"
_V400_ICU = "Номин. предельная отключающая способность Icu при напряжении 400/415В (kA)"
_V690_ICU = "Номин. предельная отключающая способность Icu при напряжении 660/690В (kA)"


def _row(code, specs):
    return {"vendor_code": code, "name": code, "type_item": "Тест",
            "specification": [{"name": k, "value": v} for k, v in specs.items()]}


@pytest.fixture
def fake_catalog(monkeypatch):
    rows = [
        _row("PLAIN6", {_PLAIN_ICU: "6"}),
        _row("PLAIN50", {_PLAIN_ICU: "50"}),
        # Воздушный выкл. без расцепителя: простого ключа нет вообще.
        _row("FRAME", {_V400_ICU: "135", _V690_ICU: "100"}),
        _row("NOICU", {"Цвет": "серый"}),
    ]
    monkeypatch.setattr(catalog_search, "products", lambda: rows)
    catalog_search.clear_cache()
    return rows


def test_icu_filter_matches_plain_key(fake_catalog):
    assert [r["vendor_code"] for r in filter_products({"icu": 6.0})] == ["PLAIN6"]


def test_icu_filter_falls_back_to_voltage_qualified_keys(fake_catalog):
    """70 «Воздушных выкл. без расцепителя» несут Icu только
    с привязкой к напряжению — раньше они не находились ни по какому Icu."""
    assert [r["vendor_code"] for r in filter_products({"icu": 135.0})] == ["FRAME"]
    assert [r["vendor_code"] for r in filter_products({"icu": 100.0})] == ["FRAME"]


def test_icu_filter_excludes_products_without_the_attribute(fake_catalog):
    assert filter_products({"icu": 999.0}) == []


# --- Остальные основные реквизиты -------------------------------------------

K_CLASS = "Класс отключающей способности"
K_REL = "Расцепитель"
K_RELTYPE = "Тип расцепителя"
K_KIND = "Род тока"
K_FRAME = "Типоразмер"
K_DISC = "Выводится из ассортимента"


@pytest.mark.parametrize("text,expected", [
    ("класс N", "N"),
    ("класс отключающей способности H", "H"),
    ("250N", "N"),
    ("YCM3 E 100N 3P 100А 50кА", "N"),
])
def test_release_class_parsed(text, expected):
    assert parse_filters(text, None).get("release_class") == expected


@pytest.mark.parametrize("text,expected", [
    ("250N", 250.0),
    ("типоразмер 250", 250.0),
    ("габарит 630", 630.0),
])
def test_frame_size_parsed(text, expected):
    assert parse_filters(text, None).get("frame") == expected


def test_bare_number_stays_current_not_frame():
    """Типоразмер и ток делят 28 значений; голое число — ток."""
    f = parse_filters("автомат на 250 А", None)
    assert f.get("current") == 250.0
    assert "frame" not in f


def test_series_is_not_mistaken_for_frame_and_class():
    """«YCB9-80M» выглядит как «типоразмер 80 + класс M»."""
    f = parse_filters("YCB9-80M", None)
    assert f.get("series") == "YCB9-80M"
    assert "frame" not in f and "release_class" not in f


def test_short_release_codes_are_not_frame_and_class():
    """Расцепители 2M/3H/3M/2H — тоже «цифра+буква»."""
    for code in ("2M", "3H", "3M", "2H"):
        f = parse_filters(code, None)
        assert "frame" not in f, code


@pytest.mark.parametrize("text,expected", [
    ("расцепитель TMA", "TMA"),
    ("с расцепителем 3H", "3H"),
    ("расцепитель T/A", "T/A"),
])
def test_release_code_needs_keyword(text, expected):
    assert parse_filters(text, None).get("release") == expected


def test_bare_release_code_is_ignored():
    """«H» и «M» одновременно и классы, и расцепители — голыми не берём."""
    assert "release" not in parse_filters("H", None)


@pytest.mark.parametrize("text,expected", [
    ("электронный", "Электронный"),
    ("термомагнитный расцепитель", "Термомагнитный"),
    ("электромеханический", "Электромеханический"),
])
def test_release_type_parsed(text, expected):
    assert parse_filters(text, None).get("release_type") == expected


@pytest.mark.parametrize("text,expected", [
    ("DC", "DC"), ("AC", "AC"),
    ("постоянный ток", "DC"),
    ("переменный ток", "AC"),
])
def test_current_kind_parsed(text, expected):
    assert parse_filters(text, None).get("current_kind") == expected


def _srow(code, specs, name=None):
    return {"vendor_code": code, "name": name or code, "type_item": "Тест",
            "specification": [{"name": k, "value": v} for k, v in specs.items()]}


@pytest.fixture
def spec_catalog(monkeypatch):
    rows = [
        _srow("A1", {K_CLASS: "N", K_FRAME: "100", K_REL: "E", K_RELTYPE: "Электронный", K_KIND: "AC", K_DISC: "нет"}),
        _srow("A2", {K_CLASS: "H", K_FRAME: "250", K_REL: "TMA", K_RELTYPE: "Термомагнитный", K_KIND: "DC", K_DISC: "да"}),
    ]
    monkeypatch.setattr(catalog_search, "products", lambda: rows)
    catalog_search.clear_cache()
    return rows


@pytest.mark.parametrize("filters,expected", [
    ({"release_class": "N"}, ["A1"]),
    ({"frame": 250.0}, ["A2"]),
    ({"release": "TMA"}, ["A2"]),
    ({"release_type": "Электронный"}, ["A1"]),
    ({"current_kind": "DC"}, ["A2"]),
])
def test_new_filters_select_products(spec_catalog, filters, expected):
    assert [r["vendor_code"] for r in filter_products(filters)] == expected


def test_discontinued_product_is_shown_with_a_warning(spec_catalog):
    """Снятые с производства не прячем — остатки продаются — но помечаем."""
    text = catalog_search.result_text(filter_products({"current_kind": "DC"}), {})
    assert "A2" in text
    assert "снят" in text.lower()
    clean = catalog_search.result_text(filter_products({"current_kind": "AC"}), {})
    assert "снят" not in clean.lower()


def test_detail_does_not_dump_the_raw_discontinued_flag(spec_catalog):
    """«Выводится из ассортимента: нет» читается как оговорка,
    хотя значит ровно обратное — фабрика товар производит."""
    ok = catalog_search.detail("A1")
    assert K_DISC not in ok
    assert "снят" not in ok.lower()
    gone = catalog_search.detail("A2")
    assert K_DISC not in gone
    assert "Снято с производства" in gone


# --- Вёрстка карточки и списка ---------------------------------------------

def _prow(code, name, type_item, specs=None, desc=None):
    return {"vendor_code": code, "name": name, "type_item": type_item,
            "item_description": desc or "",
            "specification": [{"name": k, "value": v} for k, v in (specs or {}).items()]}


@pytest.fixture
def shop(monkeypatch):
    """Каталог + оперативные данные для четырёх состояний наличия."""
    rows = [
        _prow("S1", "Модульный автоматический выключатель YCB6H-63 10А 1P",
              "Модульный автоматический выключатель",
              {"Серия": "YCB6H-63", K_DISC: "нет"}, "Описание S1"),
        _prow("S2", "Автомат YCB9-80M 16А 1P", "Автомат", {K_DISC: "нет"}),
        _prow("S3", "Автомат YCB9-80M 25А 1P", "Автомат", {K_DISC: "нет"}),
        _prow("S4", "Автомат YCB9-80M 32А 1P", "Автомат", {K_DISC: "да"}),
        _prow("S5", "Предохранитель XRNT-12", "Высоковольтный предохранитель"),
    ]
    monkeypatch.setattr(catalog_search, "products", lambda: rows)
    monkeypatch.setattr(catalog_search, "stock_map", lambda: {"S1": 1030, "S5": 3})
    monkeypatch.setattr(catalog_search, "transit_map", lambda: {"S2": [(4, "2026-09-07")]})
    monkeypatch.setattr(catalog_search, "price_map",
                        lambda: {"S1": {"base_price": "304,92"}, "S2": {"base_price": "10,00"},
                                 "S3": {"base_price": "20,00"}, "S4": {"base_price": "30,00"}})
    return rows


def test_display_name_strips_the_type_prefix(shop):
    assert catalog_search.display_name(shop[0]) == "YCB6H-63 10А 1P"


def test_display_name_kept_whole_when_it_does_not_start_with_the_type(shop):
    """У 19% товаров name не начинается с type_item."""
    assert catalog_search.display_name(shop[4]) == "Предохранитель XRNT-12"


@pytest.mark.parametrize("code,expected", [
    ("S1", "1030 шт."),                                   # есть остаток
    ("S2", "на складе нет, ожидается 4 шт. к 07.09.2026"),  # идёт приход
    ("S3", "под заказ"),                                # фабрика производит
    ("S4", "нет на складе"),                             # снято с производства
])
def test_availability_states(shop, code, expected):
    row = next(r for r in shop if r["vendor_code"] == code)
    assert catalog_search.availability(row) == expected


def test_price_line_says_so_when_the_item_is_not_in_the_pricelist(shop):
    assert catalog_search.price_line("S5") == "нет в прайсе"
    assert catalog_search.price_line("S1") == "304,92 р."


def test_single_result_renders_the_full_card(shop):
    text = catalog_search.result_text([shop[0]], {})
    assert text.startswith("Артикул: S1")
    for line in ("Тип: Модульный автоматический выключатель",
                 "Наименование: YCB6H-63 10А 1P",
                 "Тарифная цена: 304,92 р.",
                 "Наличие на складе: 1030 шт.",
                 "Серия: YCB6H-63",
                 "Описание: Описание S1"):
        assert line in text, line


def test_single_result_card_does_not_repeat_the_type_inside_the_name(shop):
    text = catalog_search.result_text([shop[0]], {})
    assert text.count("Модульный автоматический выключатель") == 1


def test_several_results_render_the_compact_list(shop):
    text = catalog_search.result_text(shop[1:4], {})
    assert "Артикул: " not in text
    assert "• S2 · YCB9-80M 16А 1P · 10,00 р. · ожидается 4 шт." in text
    assert "• S3 · YCB9-80M 25А 1P · 20,00 р. · под заказ" in text


def test_compact_list_marks_discontinued(shop):
    text = catalog_search.result_text(shop[1:5], {})
    s4 = next(l for l in text.splitlines() if l.startswith("• S4"))
    assert "⚠" in s4
    s3 = next(l for l in text.splitlines() if l.startswith("• S3"))
    assert "⚠" not in s3


def test_detail_renders_the_same_card(shop):
    assert catalog_search.detail("S1") == catalog_search.result_text([shop[0]], {})


def test_frame_size_follows_the_series_on_the_card(monkeypatch):
    """Типоразмер — сразу за Серией, перед номинальным током."""
    row = _prow("F1", "Автомат YCM3 100N", "Автомат", {
        "Серия": "YCM3",
        "Номинальный ток In (А)": "100",
        K_FRAME: "100",
        "Количество полюсов": "3P",
    })
    monkeypatch.setattr(catalog_search, "products", lambda: [row])
    monkeypatch.setattr(catalog_search, "stock_map", lambda: {})
    monkeypatch.setattr(catalog_search, "transit_map", lambda: {})
    monkeypatch.setattr(catalog_search, "price_map", lambda: {})
    keys = [l.split(":")[0] for l in catalog_search.card(row).splitlines() if ":" in l]
    assert keys.index(K_FRAME) == keys.index("Серия") + 1
    assert keys.index(K_FRAME) < keys.index("Номинальный ток In (А)")


def test_card_skips_frame_size_when_the_product_has_none(monkeypatch):
    row = _prow("F2", "Автомат YCB9-80M 16А", "Автомат", {
        "Серия": "YCB9-80M",
        "Номинальный ток In (А)": "16",
    })
    monkeypatch.setattr(catalog_search, "products", lambda: [row])
    monkeypatch.setattr(catalog_search, "stock_map", lambda: {})
    monkeypatch.setattr(catalog_search, "transit_map", lambda: {})
    monkeypatch.setattr(catalog_search, "price_map", lambda: {})
    assert K_FRAME not in catalog_search.card(row)


# --- Серии из снимка -------------------------------------------------------
# Регулярка знала только ycm/ycw/ycb — 105 серий из 150 (4692 товара,
# 39% каталога) были каталожному поиску невидимы.

_SERIES_SNAPSHOT = [
    "CJX2-F", "CJX2-FN", "CJX2s-M", "LAY5", "NT", "AD22", "D11", "YCB9-80M",
    "YCB9-80M DB", "YCIS8", "AFDD L1", "55.32",
    # Грязь из 1С, которая не должна стать серией:
    "S", "архив", "нужно продать", "Розетка реле",
]


@pytest.fixture
def snapshot_series(monkeypatch):
    rows = [
        {"vendor_code": "V%d" % i, "name": name, "type_item": "Тест",
         "specification": [{"name": "Серия", "value": name}]}
        for i, name in enumerate(_SERIES_SNAPSHOT)
    ]
    monkeypatch.setattr(catalog_search, "products", lambda: rows)
    catalog_search.clear_cache()
    catalog_search.series_matcher.cache_clear()
    yield rows
    catalog_search.series_matcher.cache_clear()


@pytest.mark.parametrize("text,expected", [
    ("CJX2-F", "CJX2-F"),
    ("LAY5", "LAY5"),
    ("YCIS8", "YCIS8"),
    ("AD22", "AD22"),
    ("контактор CJX2-F на 115А", "CJX2-F"),
    ("cjx2-f", "CJX2-F"),
])
def test_series_recognised_from_the_snapshot(snapshot_series, text, expected):
    assert parse_filters(text, None).get("series") == expected


@pytest.mark.parametrize("text,expected", [
    ("CJX2-FN 150A", "CJX2-FN"),        # не «CJX2-F» + мусор
    ("YCB9-80M DB 3P", "YCB9-80M DB"),  # не «YCB9-80M»
    ("AFDD L1 C63А", "AFDD L1"),        # серия с пробелом
])
def test_longest_series_name_wins(snapshot_series, text, expected):
    assert parse_filters(text, None).get("series") == expected


@pytest.mark.parametrize("text", [
    "какой ток у автомата", "нужна кнопка", "сколько стоит штука",
    "нужно продать остатки",   # грязное значение из 1С
    "это архив",                    # грязное значение из 1С
])
def test_prose_and_dirty_values_are_not_series(snapshot_series, text):
    assert "series" not in parse_filters(text, None)


def test_single_letter_series_is_ignored(snapshot_series):
    """В снимке есть серия «S» у одного товара — как отдельное
    слово она совпала бы со слишком многим."""
    assert "series" not in parse_filters("нужен S", None)
    assert "S" not in catalog_search.series_matcher()[1].values()


def test_known_series_beats_the_article_guess(snapshot_series):
    """«D11» подходит под шаблон артикула (буква + 2 цифры) и до
    фикса искался как артикул — и находил ноль позиций."""
    f = parse_filters("D11", None)
    assert f.get("series") == "D11"
    assert "article" not in f


def test_yc_fallback_regex_still_works_without_a_snapshot(monkeypatch):
    """Если снимка нет вообще, старая регулярка остаётся запасным вариантом."""
    monkeypatch.setattr(catalog_search, "products", lambda: [])
    catalog_search.clear_cache()
    catalog_search.series_matcher.cache_clear()
    try:
        assert parse_filters("YCB9-80M", None).get("series") == "YCB9-80M"
    finally:
        catalog_search.series_matcher.cache_clear()


@pytest.mark.parametrize("text,expected", [
    ("СJX2-F", "CJX2-F"),        # кириллическая С
    ("CJX2-Р", None),            # Р -> p, такой серии нет — и не надо придумывать
    ("АD22", "AD22"),            # кириллическая А
    # Кириллическая Н — это латинская H, а не N; похожего на N в кириллице
    # нет вовсе, так что «НT» не должно молча стать серией NT.
    ("НT", None),
])
def test_series_typed_with_cyrillic_lookalikes(snapshot_series, text, expected):
    """Имена серий набирают руками, и кириллическая «С» неотличима
    от латинской «C». В самом каталоге CNC такая запись тоже есть:
    «Супрессор для контакторов СJX2s-M»."""
    assert parse_filters(text, None).get("series") == expected


def test_folding_does_not_invent_series_in_russian_text(snapshot_series):
    """На 34506 строках реального каталога свёртка не дала ни одного
    ложного срабатывания."""
    for text in ("какой ток у этого автомата", "нужна кнопка красная",
                 "срок поставки месяц", "тепловое реле на тридцать ампер"):
        assert "series" not in parse_filters(text, None), text


# --- Диалог не должен захлопываться -----------------------------------------

_ANCHOR_S = {"series": "YCM3"}


@pytest.mark.parametrize("text,expected", [
    ("N", "N"), ("H", "H"), ("M", "M"), ("S", "S"), ("L", "L"),
    ("n", "N"),
    ("Н", "H"),   # кириллическая Н выглядит как H
    ("М", "M"),   # кириллическая М выглядит как M
])
def test_bare_class_letter_is_a_refinement(text, expected):
    """Клиент отвечает на подсказку одной буквой. Значения класса
    (H/M/N/S/L) и характеристики (B/C/D/K/Z) не пересекаются ни одной
    буквой, поэтому голая буква разводится однозначно."""
    assert parse_filters(text, _ANCHOR_S).get("release_class") == expected


def test_bare_curve_letter_still_wins_its_own_letters():
    for text, expected in (("C", "C"), ("D", "D"), ("B", "B"), ("K", "K"), ("Z", "Z")):
        f = parse_filters(text, _ANCHOR_S)
        assert f.get("curve") == expected, text
        assert "release_class" not in f, text


def test_bare_letter_still_needs_catalogue_context():
    assert "release_class" not in parse_filters("N", None)


@pytest.fixture
def varied(monkeypatch):
    def row(code, frame, current, release):
        return {"vendor_code": code, "name": code, "type_item": "Автомат",
                "specification": [{"name": "Серия", "value": "YCM3"},
                                  {"name": K_FRAME, "value": frame},
                                  {"name": "Номинальный ток In (А)", "value": current},
                                  {"name": K_REL, "value": release}]}
    rows = [row("P1", "100", "100", "E"), row("P2", "160", "100", "T/A"),
            row("P3", "250", "250", "E"), row("P4", "100", "250", "MA")]
    monkeypatch.setattr(catalog_search, "products", lambda: rows)
    monkeypatch.setattr(catalog_search, "price_map", lambda: {})
    monkeypatch.setattr(catalog_search, "stock_map", lambda: {})
    monkeypatch.setattr(catalog_search, "transit_map", lambda: {})
    catalog_search.clear_cache()
    yield rows
    catalog_search.clear_cache()


def test_hint_lists_only_what_still_differs(varied):
    """Статичная подсказка предлагала полюса и кА, уже заданные,
    и молчала про типоразмер и расцепитель, которые реально различались."""
    text = catalog_search.result_text(filter_products({"series": "YCM3"}), {"series": "YCM3"})
    assert K_FRAME in text
    assert K_REL in text
    assert "100" in text and "T/A" in text
    assert "Серия" not in text.split("различ")[-1]  # одинакова у всех — не предлагаем


def test_impossible_refinement_keeps_the_previous_selection(varied):
    """Клиент набрал 110А, которого нет. До фикса фильтр застревал
    навсегда: всё последующее тоже давало ноль, и диалог был окончен."""
    prior = {"series": "YCM3"}
    text, filters, handled, _w, _e = answer("110A", prior)
    assert handled is True
    assert filters == prior, "невозможный фильтр не должен сохраняться"
    assert "110" in text
    assert "100" in text and "250" in text, "надо показать, что есть"


def test_dialogue_survives_an_impossible_value(varied):
    """После промаха следующее уточнение должно работать."""
    f = {"series": "YCM3"}
    _, f, _, _w, _e = answer("110A", f)
    text, f, handled, _w, _e = answer("типоразмер 100", f)
    assert handled is True
    assert [r["vendor_code"] for r in filter_products(f)] == ["P1", "P4"]


def test_no_results_without_any_prior_is_still_reported(varied):
    text, filters, handled, _w, _e = answer("типоразмер 999", {})
    assert handled is True
    assert "не найден" in text.lower() or "999" in text


def test_any_offered_value_can_be_typed_back(varied):
    """Общее правило: если бот предложил значение, он обязан его
    принять. Иначе диалог прерывается на том, что сам же и предложил:
    «Расцепитель: E, T/A, Y…» — а голое «E» уходило в RAG."""
    prior = {"series": "YCM3"}
    text, filters, handled, _w, _e = answer("E", prior)
    assert handled is True
    assert filters.get("release") == "E"
    assert [r["vendor_code"] for r in filter_products(filters)] == ["P1", "P3"]


def test_offered_value_is_case_insensitive(varied):
    _, filters, handled, _w, _e = answer("t/a", {"series": "YCM3"})
    assert handled is True and filters.get("release") == "T/A"


def test_offered_value_needs_a_live_selection(varied):
    """Без начатого поиска голое «E» ничего не значит."""
    assert answer("E", {})[:3] == (None, {}, False)


def test_unoffered_text_still_falls_through(varied):
    """Произвольный текст не должен становиться фильтром."""
    text, filters, handled, _w, _e = answer("а доставка когда", {"series": "YCM3"})
    assert handled is False


# --- Подбор ближайшего по току и Icu ------------------------------------------
# Правила заказчика: ток — никогда ниже запрошенного; Icu — допуск
# ±10 кА, иначе только выше.

@pytest.fixture
def ladder(monkeypatch):
    """Ряд YCB9-80M: токи 1…80 А, Icu только 6 и 10 кА."""
    rows = []
    for i, cur in enumerate([1, 2, 4, 6, 10, 16, 20, 25, 32, 40, 50, 63, 80]):
        rows.append({"vendor_code": "L%d" % i, "name": "L%d" % i, "type_item": "Автомат",
                     "specification": [{"name": "Серия", "value": "YCB9-80M"},
                                       {"name": "Номинальный ток In (А)", "value": str(cur)},
                                       {"name": _PLAIN_ICU, "value": "6" if i % 2 else "10"}]})
    monkeypatch.setattr(catalog_search, "products", lambda: rows)
    monkeypatch.setattr(catalog_search, "price_map", lambda: {})
    monkeypatch.setattr(catalog_search, "stock_map", lambda: {})
    monkeypatch.setattr(catalog_search, "transit_map", lambda: {})
    catalog_search.clear_cache()
    yield rows
    catalog_search.clear_cache()


def _offer_line(text, head):
    return next((l for l in text.splitlines() if head in l), "")


def test_current_never_offers_lower_values(ladder):
    """Клиенту, которому нужно 30 А, автомат на 25 А предлагать нельзя."""
    text, filters, handled, _w, _e = answer("30А", {"series": "YCB9-80M"})
    assert handled is True and filters == {"series": "YCB9-80M"}
    line = _offer_line(text, "Подходят")
    assert line, text
    for low in ("1", "2", "4", "6", "10", "16", "20", "25"):
        assert (" %s," % low) not in line and (" %s" % low) != line[-len(low) - 1:], line
    assert "32" in line and "80" in line


def test_current_names_the_nearest_fit(ladder):
    text, _, _, _w, _e = answer("30А", {"series": "YCB9-80M"})
    assert "32" in _offer_line(text, "Ближайш")


def test_icu_accepts_ten_kiloamp_tolerance(ladder):
    """15 кА при ряде 6/10: оба в допуске ±10."""
    text, _, handled, _w, _e = answer("15 кА", {"series": "YCB9-80M"})
    assert handled is True
    assert "10" in _offer_line(text, "Подходят")


def test_tolerance_recommends_the_closest_not_the_smallest(ladder):
    """При запросе 15 кА и ряде 6/10 рекомендовать надо 10:
    оно ближе к запрошенному и консервативнее по отключающей способности."""
    text, _, _, _w, _e = answer("15 кА", {"series": "YCB9-80M"})
    assert "10" in _offer_line(text, "Ближайш")
    assert "6" not in _offer_line(text, "Ближайш")


def test_no_fit_at_all_reports_the_ceiling_and_offers_a_wider_search(ladder):
    """В серии максимум 80 А — молчать нельзя, иначе снова тупик."""
    result = answer("100А", {"series": "YCB9-80M"})
    assert result.handled is True
    assert result.filters == {"series": "YCB9-80M"}
    assert "80" in result.text
    assert result.wider == ("current", 100.0)


def test_icu_above_everything_also_offers_a_wider_search(ladder):
    result = answer("50 кА", {"series": "YCB9-80M"})
    assert result.wider == ("icu", 50.0)


def test_a_fitting_refinement_carries_no_wider_offer(ladder):
    result = answer("32А", {"series": "YCB9-80M"})
    assert result.wider is None
    assert result.filters.get("current") == 32.0


def test_wide_result_asks_to_narrow_down():
    products = [
        {"vendor_code": f"B{i:06d}", "name": f"Автомат {i}",
         "specification": [{"name": "Серия", "value": "YCB9"}]}
        for i in range(40)
    ]
    text = catalog_search.result_text(products, {})
    assert "уточните" in text.lower()
    assert "40" in text
