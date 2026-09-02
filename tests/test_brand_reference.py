"""Паспорт бренда: ответы, которых в системе не было вообще.

Проверяем исходник справочника, а не поисковый индекс: индекс
пересобирается отдельной командой (`python unique_answers.py`), и тест,
зависящий от неё, падал бы у любого, кто её не запускал.
"""
from pathlib import Path

from unique_answers import SOURCE_PATH, parse_source

REQUIRED = [
    "гарант",
    "где вы находитесь",
    "как купить",
    "физическим лицам",
    "минимальн",
    "рекламац",
    "сертификат",
    "сроки поставки",
    "доставля",
    "реквизиты",
    "юридическ",
    "дистрибьютор",
]


def _entries():
    return parse_source(Path(SOURCE_PATH).read_text(encoding="utf-8"))


def test_brand_section_exists():
    categories = {e.category for e in _entries()}
    assert "О компании CNC Electric" in categories


def test_every_brand_topic_is_covered():
    questions = " ".join(e.question.lower() for e in _entries())
    missing = [topic for topic in REQUIRED if topic not in questions]
    assert not missing, f"нет вопросов про: {missing}"


def test_bank_account_is_never_disclosed():
    body = Path(SOURCE_PATH).read_text(encoding="utf-8")
    assert "40702810838000035040" not in body, "расчётный счёт не выдаётся ботом (спека §3.4)"
    assert "30101810400000000225" not in body


def test_industrial_wording_rule():
    # Проверяем запрет на "бытовой" только в разделе о компании CNC,
    # чтобы не переписывать технический текст в других разделах,
    # где это слово описывает защищаемую нагрузку, а не продукцию CNC.
    entries = _entries()
    cnc_entries = [e for e in entries if e.category == "О компании CNC Electric"]
    body = " ".join(e.question.lower() + " " + e.answer.lower() for e in cnc_entries)
    assert "бытов" not in body, "весь ассортимент CNC промышленный — слово запрещено в разделе о компании"
