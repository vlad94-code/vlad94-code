from datetime import datetime, timedelta, timezone

import core.db as db
from core.logging_ import (
    answer_escalation, get_escalation, open_escalations,
    record_escalation, set_escalation_mail_status, stale_escalations,
)


def test_escalation_gets_a_number_clients_can_quote(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    db.init_db()
    first = record_escalation("Подойдёт ли YCB9RL-63B?", user_id=42, chat_id=42)
    second = record_escalation("А сертификат есть?", user_id=42, chat_id=42)
    assert second == first + 1


def test_open_escalations_exclude_answered(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    db.init_db()
    number = record_escalation("вопрос", user_id=1, chat_id=1)
    assert [row["id"] for row in open_escalations()] == [number]
    assert answer_escalation(number, "ответ", answered_by=761316155) is True
    assert open_escalations() == []


def test_answer_is_stored_with_its_author(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    db.init_db()
    number = record_escalation("вопрос", user_id=1, chat_id=7, context="YCW3")
    answer_escalation(number, "Да, подойдёт.", answered_by=761316155)
    row = get_escalation(number)
    assert row["answer"] == "Да, подойдёт."
    assert row["answered_by"] == 761316155
    assert row["chat_id"] == 7
    assert row["context"] == "YCW3"


def test_mail_status_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    db.init_db()
    number = record_escalation("вопрос", user_id=1, chat_id=1)
    set_escalation_mail_status(number, "failed")
    assert get_escalation(number)["mail_status"] == "failed"


def test_stale_escalations_compares_offset_aware_timestamps(tmp_path, monkeypatch):
    """Ruling A: _now() пишет ISO-строки с зоной (offset-aware). Сравнение
    с наивным datetime.now() падает с TypeError — stale_escalations должна
    сравнивать offset-aware со offset-aware и без ошибок находить только
    действительно старые записи."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    db.init_db()
    old_number = record_escalation("старый вопрос", user_id=1, chat_id=1)
    fresh_number = record_escalation("свежий вопрос", user_id=1, chat_id=1)

    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    with db.get_connection() as conn:
        conn.execute("UPDATE escalations SET ts = ? WHERE id = ?", (old_ts, old_number))

    stale_ids = [row["id"] for row in stale_escalations()]
    assert stale_ids == [old_number]
    assert fresh_number not in stale_ids


def _age(escalation_id, days):
    """Состарить вопрос: ждать реальные сутки в тесте невозможно.

    Время пишем с зоной, как _now() в core/logging_: naive-строка уронила бы
    сравнение с порогом.
    """
    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with db.get_connection() as conn:
        conn.execute("UPDATE escalations SET ts = ? WHERE id = ?", (old, escalation_id))


def test_stale_escalations_are_found(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    db.init_db()
    number = record_escalation("старый вопрос", user_id=1, chat_id=1)
    _age(number, days=10)

    assert [row["id"] for row in stale_escalations()] == [number]


def test_fresh_escalation_is_not_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    db.init_db()
    record_escalation("свежий вопрос", user_id=1, chat_id=1)

    assert stale_escalations() == []


def test_an_answered_question_is_never_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    db.init_db()
    number = record_escalation("старый вопрос", user_id=1, chat_id=1)
    _age(number, days=10)
    answer_escalation(number, "ответ", answered_by=1)

    assert stale_escalations() == []
