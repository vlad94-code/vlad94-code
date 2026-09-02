"""Клиент нажал «Спросить техслужбу»: вопрос получает номер, почта — по желанию."""
import asyncio

import pytest

import bot
import escalation


class _Message:
    def __init__(self, text, replies):
        self.text = text
        self.chat = type("Chat", (), {"id": 77})()
        self._replies = replies

    async def reply_text(self, text, **kwargs):
        self._replies.append(text)

    def get_bot(self):
        return object()


class _Query:
    def __init__(self, message):
        self.message = message

    async def answer(self, *args, **kwargs):
        pass


class _Chat:
    async def send_action(self, *args, **kwargs):
        pass


class _Context:
    def __init__(self, user_data=None):
        self.user_data = {} if user_data is None else user_data


def _client_update(text, replies):
    update = type("U", (), {})()
    update.message = _Message(text, replies)
    update.callback_query = _Query(update.message)
    update.effective_chat = _Chat()
    update.effective_user = type("User", (), {"id": 77, "username": "client"})()
    return update


def _context(user_data=None):
    return _Context(user_data)


def _async(value):
    async def call(*args, **kwargs):
        return value
    return call


@pytest.fixture(autouse=True)
def client_role(monkeypatch):
    """Ветка ожиданий живёт в answer() — роль и лог берём под контроль."""
    monkeypatch.setattr(bot, "resolve_role", _async(bot.Role.CLIENT))
    monkeypatch.setattr(bot, "log_query", lambda *args, **kwargs: 1)


def test_email_step_is_skippable(monkeypatch):
    registered = {}

    async def fake_register(bot_, **kwargs):
        registered.update(kwargs)
        return 47

    monkeypatch.setattr(escalation, "register", fake_register)
    replies = []
    update = _client_update("", replies)
    context = _context(user_data={"pending_question": "Подойдёт ли YCB9RL-63B?"})

    asyncio.run(bot.skip_email_callback(update, context))

    assert registered["email"] is None
    assert registered["question"] == "Подойдёт ли YCB9RL-63B?"
    assert "№47" in replies[-1]


def test_email_is_stored_when_given(monkeypatch):
    registered = {}

    async def fake_register(bot_, **kwargs):
        registered.update(kwargs)
        return 48

    monkeypatch.setattr(escalation, "register", fake_register)
    replies = []
    update = _client_update("client@example.com", replies)
    context = _context(user_data={
        "pending_question": "вопрос", "awaiting_support_email": True,
    })

    asyncio.run(bot.answer(update, context))

    assert registered["email"] == "client@example.com"


def test_junk_instead_of_an_email_does_not_lose_the_question(monkeypatch):
    """Шаг почты необязателен: что бы клиент ни прислал, вопрос уходит."""
    registered = {}

    async def fake_register(bot_, **kwargs):
        registered.update(kwargs)
        return 49

    monkeypatch.setattr(escalation, "register", fake_register)
    replies = []
    update = _client_update("не хочу", replies)
    context = _context(user_data={
        "pending_question": "вопрос", "awaiting_support_email": True,
    })

    asyncio.run(bot.answer(update, context))

    assert registered["email"] is None
    assert registered["question"] == "вопрос"


def test_button_without_a_question_asks_for_one(monkeypatch):
    replies = []
    update = _client_update("", replies)
    context = _context()

    asyncio.run(bot.ask_support_callback(update, context))

    assert context.user_data["awaiting_support_question"] is True
    assert "вопрос" in replies[-1].lower()


def test_the_described_question_reaches_the_email_step():
    replies = []
    update = _client_update("Подойдёт ли YCB9RL-63B к вводу 63 А?", replies)
    context = _context(user_data={"awaiting_support_question": True})

    asyncio.run(bot.answer(update, context))

    assert context.user_data["pending_question"] == "Подойдёт ли YCB9RL-63B к вводу 63 А?"
    assert context.user_data["awaiting_support_email"] is True
    assert "e-mail" in replies[-1].lower()


# --- ответ инженера ----------------------------------------------------------

ENGINEER_ID = 900002  # tests/conftest.py: ENGINEER_USER_IDS


@pytest.fixture
def as_engineer(monkeypatch):
    """Роль берём под контроль, а не через ENGINEER_USER_IDS: tests/test_roles.py
    перечитывает core.roles по ходу набора и обнуляет глобальные наборы ID."""
    monkeypatch.setattr(bot, "role_of", lambda update: bot.Role.ENGINEER)


def _engineer_reply_update(*, reply_to_text, text, replies):
    update = type("U", (), {})()
    update.message = _Message(text, replies)
    update.message.reply_to_message = _Message(reply_to_text, [])
    update.callback_query = None
    update.effective_chat = _Chat()
    update.effective_user = type("User", (), {"id": ENGINEER_ID, "username": "eng"})()
    return update


class _BotContext(_Context):
    def __init__(self, user_data=None):
        super().__init__(user_data)
        self.bot = object()


def test_engineer_reply_reaches_the_client(monkeypatch, as_engineer):
    delivered = {}

    async def fake_deliver(bot_, number, answer, *, answered_by):
        delivered.update(number=number, answer=answer, answered_by=answered_by)
        return True

    monkeypatch.setattr(escalation, "deliver", fake_deliver)
    replies = []
    update = _engineer_reply_update(
        reply_to_text="🔔 Вопрос №47, клиент из Екатеринбурга\n«вопрос»",
        text="Да, подойдёт при токе КЗ до 10 кА.",
        replies=replies,
    )

    asyncio.run(bot.engineer_reply_handler(update, _BotContext()))

    assert delivered["number"] == 47
    assert delivered["answer"].startswith("Да, подойдёт")
    assert delivered["answered_by"] == ENGINEER_ID
    assert "передан клиенту" in replies[-1].lower()


def test_reply_without_a_question_number_is_ignored(as_engineer):
    replies = []
    update = _engineer_reply_update(reply_to_text="просто сообщение", text="ответ", replies=replies)

    asyncio.run(bot.engineer_reply_handler(update, _BotContext()))

    assert not replies


def test_a_closed_question_is_not_answered_twice(monkeypatch, as_engineer):
    async def fake_deliver(bot_, number, answer, *, answered_by):
        return False

    monkeypatch.setattr(escalation, "deliver", fake_deliver)
    replies = []
    update = _engineer_reply_update(
        reply_to_text="🔔 Вопрос №47", text="второй ответ", replies=replies)

    asyncio.run(bot.engineer_reply_handler(update, _BotContext()))

    assert "уже закрыт" in replies[-1]


def test_a_client_reply_is_not_taken_for_an_engineer_answer(monkeypatch):
    """Хендлер стоит перед общим текстовым — чужой ответ он трогать не должен."""
    called = []

    async def fake_deliver(*args, **kwargs):
        called.append(args)
        return True

    monkeypatch.setattr(escalation, "deliver", fake_deliver)
    replies = []
    monkeypatch.setattr(bot, "role_of", lambda update: bot.Role.CLIENT)
    update = _engineer_reply_update(
        reply_to_text="🔔 Вопрос №47", text="ответ", replies=replies)

    asyncio.run(bot.engineer_reply_handler(update, _BotContext()))

    assert not called
    assert not replies


# --- кнопка «В справочник» ---------------------------------------------------

def test_an_answer_becomes_knowledge_in_one_tap(monkeypatch):
    appended = {}
    replies = []
    update = _client_update("", replies)
    update.callback_query.data = "to_reference:47"

    monkeypatch.setattr(bot, "get_escalation",
                        lambda number: {"question": "Какая гарантия?", "answer": "12 месяцев."})
    monkeypatch.setattr(bot.unique_answers, "append_entry",
                        lambda question, answer: appended.update(q=question, a=answer) or True)
    monkeypatch.setattr(bot.unique_answers, "rebuild_unique_answers_document", lambda: 118)
    monkeypatch.setattr(bot, "rebuild", lambda *args, **kwargs: None)

    asyncio.run(bot.to_reference_callback(update, _context()))

    assert appended == {"q": "Какая гарантия?", "a": "12 месяцев."}
    assert "118" in replies[-1]


def test_a_question_without_an_answer_is_not_filed(monkeypatch):
    replies = []
    update = _client_update("", replies)
    update.callback_query.data = "to_reference:47"
    monkeypatch.setattr(bot, "get_escalation", lambda number: {"question": "вопрос", "answer": None})

    asyncio.run(bot.to_reference_callback(update, _context()))

    assert "Ответа по этому вопросу нет." in replies[-1]
