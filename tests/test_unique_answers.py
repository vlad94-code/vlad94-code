"""Справочник уникальных ответов: knowledge/unique_answers.md → unique_answers.py.

Файл ведётся инженером вручную, поэтому тесты защищают ровно две вещи:
разбор человеческого формата (заголовки, многострочные ответы, «Контекст:»)
и честность авторства — записи справочника не должны выдавать себя за
документ технической службы CNC (ARCHITECTURE.md §5).
"""
from pathlib import Path

import pytest

import core.documents
import unique_answers
from catalog_parser import TECH_SERVICE_ANSWER_PREFIX


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Свои data/knowledge.db и uploads/ на тест — боевые не трогаем."""
    monkeypatch.setattr(core.documents, "DB_PATH", tmp_path / "knowledge.db")
    monkeypatch.setattr(unique_answers, "UPLOAD_DIR", tmp_path / "uploads")
    return tmp_path


def test_parses_category_question_and_answer():
    entries = unique_answers.parse_source(
        "# Уникальные ответы CNC\n"
        "\n"
        "## Воздушные выключатели YCW3\n"
        "\n"
        "### Есть ли у YCW3 мотор-привод?\n"
        "Есть, в стандартной комплектации AC/DC 220 В.\n"
    )
    assert len(entries) == 1
    entry = entries[0]
    assert entry.number == 1
    assert entry.category == "Воздушные выключатели YCW3"
    assert entry.question == "Есть ли у YCW3 мотор-привод?"
    assert entry.context == ""
    assert entry.answer == "Есть, в стандартной комплектации AC/DC 220 В."


def test_answer_keeps_multiple_lines_and_numbering_continues():
    entries = unique_answers.parse_source(
        "## Категория\n"
        "\n"
        "### Первый вопрос?\n"
        "Строка один.\n"
        "Строка два.\n"
        "\n"
        "### Второй вопрос?\n"
        "Ответ.\n"
    )
    assert [entry.number for entry in entries] == [1, 2]
    assert entries[0].answer == "Строка один.\nСтрока два."
    assert entries[1].question == "Второй вопрос?"


def test_context_line_is_separated_from_answer():
    entries = unique_answers.parse_source(
        "## Категория\n"
        "\n"
        "### Вопрос?\n"
        "Контекст: речь про выкатное исполнение.\n"
        "Собственно ответ.\n"
    )
    assert entries[0].context == "Контекст: речь про выкатное исполнение."
    assert entries[0].answer == "Собственно ответ."


def test_category_persists_across_questions_until_next_heading():
    entries = unique_answers.parse_source(
        "## Первая\n"
        "### А?\n"
        "1\n"
        "### Б?\n"
        "2\n"
        "## Вторая\n"
        "### В?\n"
        "3\n"
    )
    assert [entry.category for entry in entries] == ["Первая", "Первая", "Вторая"]


def test_question_without_answer_is_skipped():
    """Заготовка вопроса — это ещё не знание. Индексировать пустую запись
    нельзя: поиск найдёт её по тексту вопроса и покажет пользователю пустоту."""
    entries = unique_answers.parse_source(
        "## Категория\n"
        "### Заготовка, ответа пока нет?\n"
        "\n"
        "### Отвеченный вопрос?\n"
        "Ответ.\n"
    )
    assert [entry.question for entry in entries] == ["Отвеченный вопрос?"]


def test_text_outside_any_question_is_ignored():
    """Преамбула файла и текст под категорией до первого «###» — не ответ."""
    entries = unique_answers.parse_source(
        "# Заголовок файла\n"
        "Пояснение, как вести файл.\n"
        "\n"
        "## Категория\n"
        "Вводный абзац категории.\n"
        "\n"
        "### Вопрос?\n"
        "Ответ.\n"
    )
    assert len(entries) == 1
    assert entries[0].answer == "Ответ."


def test_answer_prefix_is_not_the_tech_service_one():
    """Подписать составленный инженером справочник как «ответ технической
    службы CNC» — значит приписать авторство людям, которые его не писали,
    и поднять его до п.1 приоритета источников (ARCHITECTURE.md §5)."""
    assert unique_answers.ANSWER_PREFIX != TECH_SERVICE_ANSWER_PREFIX
    assert "техническ" not in unique_answers.ANSWER_PREFIX.lower()


def test_rebuild_writes_page_blocks_and_registers_version(isolated_db, monkeypatch, tmp_path):
    source = tmp_path / "unique_answers.md"
    source.write_text(
        "## Категория\n### Вопрос?\nОтвет.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(unique_answers, "SOURCE_PATH", source)

    assert unique_answers.rebuild_unique_answers_document() == 1

    written = list((tmp_path / "uploads").glob("*.md"))
    assert len(written) == 1
    content = written[0].read_text(encoding="utf-8")
    assert "## Страница 1" in content
    assert "### Категория" in content
    assert "Вопрос: Вопрос?" in content
    assert f"{unique_answers.ANSWER_PREFIX} Ответ." in content

    documents = core.documents.list_documents()
    assert [(row["original_name"], row["version"], row["status"]) for row in documents] == [
        (unique_answers.LOGICAL_NAME, 1, "active")
    ]


def test_rebuild_supersedes_previous_version(isolated_db, monkeypatch, tmp_path):
    """Логическое имя фиксировано, поэтому вторая сборка обязана пометить
    первую superseded — иначе knowledge_matrix проиндексирует обе и старый
    ответ будет конкурировать с исправленным."""
    source = tmp_path / "unique_answers.md"
    source.write_text("## К\n### В?\nПервый ответ.\n", encoding="utf-8")
    monkeypatch.setattr(unique_answers, "SOURCE_PATH", source)
    unique_answers.rebuild_unique_answers_document()

    source.write_text("## К\n### В?\nИсправленный ответ.\n", encoding="utf-8")
    unique_answers.rebuild_unique_answers_document()

    statuses = {
        (row["version"], row["status"])
        for row in core.documents.list_documents()
        if row["original_name"] == unique_answers.LOGICAL_NAME
    }
    assert statuses == {(1, "superseded"), (2, "active")}


def test_rebuild_returns_zero_when_source_is_missing(isolated_db, monkeypatch, tmp_path):
    monkeypatch.setattr(unique_answers, "SOURCE_PATH", tmp_path / "нет-такого-файла.md")
    assert unique_answers.rebuild_unique_answers_document() == 0
    assert core.documents.list_documents() == []


def test_shipped_source_file_parses():
    """Файл в репозитории должен разбираться — он и есть смысл модуля."""
    entries = unique_answers.parse_source(
        unique_answers.SOURCE_PATH.read_text(encoding="utf-8")
    )
    assert len(entries) >= 20
    assert all(entry.question and entry.answer for entry in entries)
    assert {"Воздушные выключатели YCW3"} <= {entry.category for entry in entries}
