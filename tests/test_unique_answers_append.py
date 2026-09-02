"""Ответ инженера, дописанный в справочник одной кнопкой.

Справочник растёт из реальных вопросов клиентов, без отдельной работы
куратора: инженер уже ответил — остаётся не потерять этот ответ.
"""
import unique_answers


def test_entry_is_appended_under_its_category(tmp_path, monkeypatch):
    source = tmp_path / "unique_answers.md"
    source.write_text("# Уникальные ответы CNC\n\n## Контакторы CJX2\n\n### Вопрос?\nОтвет.\n",
                      encoding="utf-8")
    monkeypatch.setattr(unique_answers, "SOURCE_PATH", source)

    assert unique_answers.append_entry("Подойдёт ли X вместо Y?", "Да, подойдёт.") is True

    body = source.read_text(encoding="utf-8")
    assert "## Вопросы клиентов" in body
    assert "### Подойдёт ли X вместо Y?" in body
    assert "Да, подойдёт." in body


def test_duplicate_question_is_not_appended_twice(tmp_path, monkeypatch):
    source = tmp_path / "unique_answers.md"
    source.write_text("# Уникальные ответы CNC\n", encoding="utf-8")
    monkeypatch.setattr(unique_answers, "SOURCE_PATH", source)

    unique_answers.append_entry("Один и тот же вопрос?", "Ответ.")
    assert unique_answers.append_entry("Один и тот же вопрос?", "Другой ответ.") is False
    assert source.read_text(encoding="utf-8").count("Один и тот же вопрос?") == 1


def test_an_empty_answer_is_not_knowledge(tmp_path, monkeypatch):
    source = tmp_path / "unique_answers.md"
    source.write_text("# Уникальные ответы CNC\n", encoding="utf-8")
    monkeypatch.setattr(unique_answers, "SOURCE_PATH", source)

    assert unique_answers.append_entry("Вопрос?", "   ") is False
    assert "Вопрос?" not in source.read_text(encoding="utf-8")


def test_the_appended_entry_is_readable_back(tmp_path, monkeypatch):
    """Дописанное должно попасть в документ базы знаний, а не просто в файл."""
    source = tmp_path / "unique_answers.md"
    source.write_text("# Уникальные ответы CNC\n", encoding="utf-8")
    monkeypatch.setattr(unique_answers, "SOURCE_PATH", source)

    unique_answers.append_entry("Какая гарантия на автоматы?", "12 месяцев с даты отгрузки.")

    entries = unique_answers.parse_source(source.read_text(encoding="utf-8"))
    assert [entry.question for entry in entries] == ["Какая гарантия на автоматы?"]
    assert entries[0].category == "Вопросы клиентов"
    assert entries[0].answer == "12 месяцев с даты отгрузки."
