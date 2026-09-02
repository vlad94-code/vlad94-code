"""catalog_parser — структурированный разбор докс с проверенными ответами
техслужбы CNC (Question/Technical Answer стили). uploads/ в .gitignore,
тесты пропускаются, если файла нет локально."""
from pathlib import Path

import docx
import pytest

from catalog_parser import _extract_verified_answers, _is_placeholder_answer, _is_verified_answers_document

DOCX_PATH = Path("uploads") / "Вопросы_технической_службе_по_подбору_аналогов_CNC_2026_08_20_2.docx"

requires_docx = pytest.mark.skipif(not DOCX_PATH.exists(), reason=f"{DOCX_PATH} отсутствует локально")


@pytest.fixture(scope="module")
def document():
    return docx.Document(DOCX_PATH)


@requires_docx
def test_detected_as_verified_answers_document(document):
    assert _is_verified_answers_document(document) is True


@requires_docx
def test_extracts_19_real_entries_across_4_categories(document):
    entries = _extract_verified_answers(document)
    assert len(entries) == 19
    categories = {entry.category for entry in entries}
    assert categories == {
        "УЗО и дифференциальные автоматы",
        "Воздушные автоматические выключатели YCW3",
        "Автоматы в литом корпусе YCM1, YCM3 и YCM8",
        "Рубильники, контакторы и защита двигателя",
    }
    # "Формат ответа" — служебный раздел, не категория оборудования.
    assert "Формат ответа" not in categories


@requires_docx
def test_placeholder_answers_excluded(document):
    entries = _extract_verified_answers(document)
    for entry in entries:
        assert not _is_placeholder_answer(entry.answer), entry.question


@requires_docx
def test_multi_paragraph_answer_joined(document):
    entries = _extract_verified_answers(document)
    entry = next(e for e in entries if "Диапазоны настроек YCW3" in e.question)
    assert "2М/2Н" in entry.answer and "3М/3Н" in entry.answer


@requires_docx
def test_malformed_question_answer_kept(document):
    entries = _extract_verified_answers(document)
    entry = next(e for e in entries if "Разделение обычных и селективных" in e.question)
    assert "неверно" in entry.answer


def test_placeholder_detection():
    assert _is_placeholder_answer("введите ответ, артикулы или ссылку на таблицу")
    assert _is_placeholder_answer("Нужно выделить в отдельную задачу. Таких таблиц у нас просто нет и нужно делать с 0.")
    assert not _is_placeholder_answer("Ответ технической службы: Селективных исполнений нет.")
