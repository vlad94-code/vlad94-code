import asyncio

import pytest

import core.db as db
import escalation
import mailer
from core.logging_ import get_escalation


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    db.init_db()


def test_register_notifies_engineer_and_sends_mail(store, monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "send", lambda subject, body, **kw: sent.append(subject) or True)
    monkeypatch.setattr(escalation, "ENGINEER_IDS", frozenset({761316155}))
    bot = FakeBot()

    number = asyncio.run(escalation.register(
        bot, question="Подойдёт ли YCB9RL-63B?", user_id=5, chat_id=5, region="Екатеринбург"))

    assert number == 1
    assert any(f"№{number}" in text for _, text in bot.messages)
    assert any(chat_id == 761316155 for chat_id, _ in bot.messages)
    assert sent and f"№{number}" in sent[0]
    assert get_escalation(number)["mail_status"] == "sent"


def test_failed_mail_does_not_break_registration(store, monkeypatch):
    monkeypatch.setattr(mailer, "send", lambda *a, **kw: False)
    monkeypatch.setattr(escalation, "ENGINEER_IDS", frozenset({761316155}))
    bot = FakeBot()
    number = asyncio.run(escalation.register(bot, question="вопрос", user_id=5, chat_id=5))
    assert get_escalation(number)["mail_status"] == "failed"
    assert bot.messages, "инженер всё равно должен получить уведомление"


def test_deliver_sends_answer_to_the_client_chat(store, monkeypatch):
    monkeypatch.setattr(mailer, "send", lambda *a, **kw: True)
    monkeypatch.setattr(escalation, "ENGINEER_IDS", frozenset({761316155}))
    bot = FakeBot()
    number = asyncio.run(escalation.register(bot, question="вопрос", user_id=5, chat_id=77))
    bot.messages.clear()

    assert asyncio.run(escalation.deliver(bot, number, "Да, подойдёт.", answered_by=761316155))
    assert bot.messages[0][0] == 77
    assert "Да, подойдёт." in bot.messages[0][1]
    assert "технической службы" in bot.messages[0][1]


def test_second_delivery_is_refused(store, monkeypatch):
    monkeypatch.setattr(mailer, "send", lambda *a, **kw: True)
    monkeypatch.setattr(escalation, "ENGINEER_IDS", frozenset({761316155}))
    bot = FakeBot()
    number = asyncio.run(escalation.register(bot, question="вопрос", user_id=5, chat_id=77))
    asyncio.run(escalation.deliver(bot, number, "первый", answered_by=1))
    assert asyncio.run(escalation.deliver(bot, number, "второй", answered_by=1)) is False


def test_client_receipt_promises_three_working_days():
    text = escalation.client_receipt(47)
    assert "№47" in text
    assert "3 рабочих дн" in text


# --- напоминание о просрочке --------------------------------------------------

class FakeApplication:
    def __init__(self):
        self.bot = FakeBot()


def _age(escalation_id, days):
    from datetime import datetime, timedelta, timezone

    import core.db as db

    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with db.get_connection() as conn:
        conn.execute("UPDATE escalations SET ts = ? WHERE id = ?", (old, escalation_id))


def test_a_stale_question_reminds_the_engineer_and_the_client(store, monkeypatch):
    import bot

    monkeypatch.setattr(mailer, "send", lambda *a, **kw: True)
    monkeypatch.setattr(escalation, "ENGINEER_IDS", frozenset({761316155}))
    number = asyncio.run(escalation.register(
        FakeBot(), question="Подойдёт ли YCB9RL-63B?", user_id=5, chat_id=77))
    _age(number, days=10)

    application = FakeApplication()
    assert asyncio.run(bot.remind_about_stale_escalations(application)) == 1

    addressees = [chat_id for chat_id, _ in application.bot.messages]
    assert 761316155 in addressees, "инженер должен получить напоминание"
    assert 77 in addressees, "клиент не должен остаться в тишине"


def test_a_fresh_question_is_not_nagged_about(store, monkeypatch):
    import bot

    monkeypatch.setattr(mailer, "send", lambda *a, **kw: True)
    monkeypatch.setattr(escalation, "ENGINEER_IDS", frozenset({761316155}))
    asyncio.run(escalation.register(FakeBot(), question="вопрос", user_id=5, chat_id=77))

    application = FakeApplication()
    assert asyncio.run(bot.remind_about_stale_escalations(application)) == 0
    assert application.bot.messages == []
