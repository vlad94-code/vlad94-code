"""engines/knowledge_v2.py — удаление _normalize_chars()/Pattern 3 (К-2) и
markdown-«**» из ответов (В-3, см. bot.py: reply_text() без parse_mode,
значит «**» приходит пользователю как два голых символа звёздочки)."""
from engines.knowledge_v2 import (
    _extract_characteristics_from_section,
    explain_tripping_characteristic,
    find_series_with_characteristic,
    list_all_characteristics,
)


def test_extract_characteristics_ignores_unrelated_standalone_letters():
    """До К-2 «Pattern 3» помечал характеристикой любую одинокую букву
    B/C/D/K/Z рядом со словом «характеристики» — например букву в названии
    модели, — и код цитировал страницу как источник для того, чего там не
    было. Явного списка характеристик здесь нет, значит и находок быть не должно."""
    section = (
        "Технические характеристики автоматического выключателя YCB1\n"
        "Модель D предназначена для сетей 380В.\n"
        "Степень защиты IP20.\n"
    )
    assert _extract_characteristics_from_section(section) == []


def test_extract_characteristics_still_finds_explicit_list():
    section = "Характеристики: B, C, D /7\n"
    assert _extract_characteristics_from_section(section) == ["B", "C", "D"]


def test_extract_characteristics_still_finds_trip_curve_phrase():
    section = "Термомагнитная хар-ка отключения: B, K\n"
    assert _extract_characteristics_from_section(section) == ["B", "K"]


def test_explain_tripping_characteristic_plain_latin_input():
    answer = explain_tripping_characteristic("c")
    assert answer is not None
    assert answer.text.startswith("Характеристика C")
    assert "**" not in answer.text


def test_explain_tripping_characteristic_rejects_unknown_letter():
    assert explain_tripping_characteristic("Q") is None


def test_list_all_characteristics_has_no_markdown_bold():
    answer = list_all_characteristics()
    assert answer is not None
    assert "**" not in answer.text


def test_find_series_with_characteristic_has_no_markdown_bold():
    answer = find_series_with_characteristic("C")
    assert answer is not None
    assert "**" not in answer.text
