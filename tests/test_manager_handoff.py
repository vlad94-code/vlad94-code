"""Передача клиента менеджеру: карточка клиенту и уведомление менеджеру.

Без уведомления сделка зависела бы от того, дойдут ли у клиента руки
позвонить по выданному телефону.
"""
import asyncio

import pytest

import bot
import managers


class _Message:
    def __init__(self, text, replies):
        self.text = text
        self.chat = type("Chat", (), {"id": 77})()
        self._replies = replies

    async def reply_text(self, text, **kwargs):
        self._replies.append(text)


class _Query:
    def __init__(self, message, user):
        self.message = message
        self.from_user = user
        self.data = "want_manager"

    async def answer(self, *args, **kwargs):
        pass


class _Chat:
    async def send_action(self, *args, **kwargs):
        pass


class _Bot:
    def __init__(self, sent):
        self.sent = sent

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


class _Context:
    def __init__(self, user_data=None, sent=None):
        self.user_data = {} if user_data is None else user_data
        self.bot = _Bot([] if sent is None else sent)


def _client_update(text, replies):
    update = type("U", (), {})()
    update.message = _Message(text, replies)
    update.effective_user = type("User", (), {"id": 77, "username": "client"})()
    update.callback_query = _Query(update.message, update.effective_user)
    update.effective_chat = _Chat()
    return update


def _context(user_data=None, sent=None):
    return _Context(user_data, sent)


def _async(value):
    async def call(*args, **kwargs):
        return value
    return call


def test_known_city_gets_one_manager_card():
    replies = []
    update = _client_update("Есть на складе YCB9?", replies)
    context = _context(user_data={"city": "Самара"})

    asyncio.run(bot.offer_manager(update, context, "Есть на складе YCB9?"))

    assert "Искорнев" in replies[-1]
    assert "Кузнецов" not in replies[-1]


def test_unknown_city_gets_the_general_address_only():
    replies = []
    update = _client_update("Есть на складе?", replies)
    context = _context(user_data={"city": "Ереван"})

    asyncio.run(bot.offer_manager(update, context, "Есть на складе?"))

    assert "info@cncrussia.com" in replies[-1]
    assert "ЦФО" not in replies[-1]


def test_manager_is_notified(monkeypatch):
    monkeypatch.setattr(managers, "_telegram_ids", lambda: {"is@cncrussia.com": 900003})
    replies, sent = [], []
    update = _client_update("Есть на складе YCB9?", replies)
    context = _context(user_data={"city": "Самара"}, sent=sent)

    asyncio.run(bot.offer_manager(update, context, "Есть на складе YCB9?"))

    assert sent and sent[0][0] == 900003
    assert "Самар" in sent[0][1]


def test_city_is_asked_once_when_unknown():
    replies = []
    update = _client_update("Есть на складе?", replies)
    context = _context(user_data={})

    asyncio.run(bot.offer_manager(update, context, "Есть на складе?"))

    assert "город" in replies[-1].lower()
    assert context.user_data.get("awaiting_city") is True


def test_the_named_city_finishes_the_handoff(monkeypatch):
    """Ответ на «из какого вы города?» — не новый вопрос, а вторая половина передачи."""
    monkeypatch.setattr(bot, "resolve_role", _async(bot.Role.CLIENT))
    monkeypatch.setattr(bot, "log_query", lambda *args, **kwargs: 1)
    replies = []
    update = _client_update("Самара", replies)
    context = _context(user_data={"awaiting_city": True, "pending_question": "Есть на складе YCB9?"})

    asyncio.run(bot.answer(update, context))

    assert context.user_data["city"] == "Самара"
    assert "Искорнев" in replies[-1]


def test_the_button_hands_the_client_over_too():
    replies = []
    update = _client_update("", replies)
    context = _context(user_data={"city": "Самара", "pending_question": "Есть на складе?"})

    asyncio.run(bot.want_manager_callback(update, context))

    assert "Искорнев" in replies[-1]


def test_not_it_is_a_fork_not_an_apology():
    replies = []
    update = _client_update("", replies)

    asyncio.run(bot.not_it_callback(update, _context()))

    assert replies, "кнопка «не то, что нужно» не должна молчать"


@pytest.mark.parametrize("city, surname", [
    ("Москва", "Кузнецов"),
    ("Санкт-Петербург", "Артемьев"),
    ("Краснодар", "Мыц"),
    ("Екатеринбург", "Цыплаков"),
])
def test_each_district_reaches_its_own_manager(city, surname):
    replies = []
    update = _client_update("вопрос", replies)

    asyncio.run(bot.offer_manager(update, _context(user_data={"city": city}), "вопрос"))

    assert surname in replies[-1]
