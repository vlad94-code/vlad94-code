"""Роль «клиент»: доступ по подписке на канал CNC Electric.

Штатные роли (админ, инженер, менеджер) заданы списками ID в .env и от канала
не зависят — иначе сотрудник потерял бы бота вместе с отпиской. Все остальные
проходят проверку у Telegram: подписан на канал — клиент, нет — отказ.

Сеть здесь не трогается: `bot.get_chat_member` подменяется заглушкой, как это
делает сам Telegram-транспорт в остальных тестах."""
import asyncio
import importlib
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest


@pytest.fixture
def roles():
    """Свежий core.roles: списки ID читаются один раз при импорте.

    Окружение и модуль возвращаются на место после теста. Без этого 111/222/333
    оставались в core.roles навсегда, и всякий тестовый файл, который идёт
    после этого по алфавиту, терял админа из tests/conftest.py: роль admin
    900001 переставала распознаваться, а падало это только при запуске всего
    набора — поодиночке файл проходил.
    """
    import core.roles as module

    saved = {name: os.environ.get(name)
             for name in ("ADMIN_USER_IDS", "ENGINEER_USER_IDS", "MANAGER_USER_IDS")}
    os.environ["ADMIN_USER_IDS"] = "111"
    os.environ["ENGINEER_USER_IDS"] = "222"
    os.environ["MANAGER_USER_IDS"] = "333"

    module = importlib.reload(module)
    module.forget_memberships()
    yield module

    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    importlib.reload(module).forget_memberships()


def _update(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        message=SimpleNamespace(reply_text=AsyncMock()),
        callback_query=None,
        effective_user=SimpleNamespace(id=user_id),
    )


def _context(status: str | None = "member", *, error: Exception | None = None) -> SimpleNamespace:
    """Контекст с ботом, отвечающим на getChatMember заданным статусом."""
    if error is not None:
        member = AsyncMock(side_effect=error)
    else:
        member = AsyncMock(return_value=SimpleNamespace(status=status, is_member=True))
    return SimpleNamespace(bot=SimpleNamespace(get_chat_member=member))


def test_subscriber_becomes_a_client(roles):
    role = asyncio.run(roles.resolve_role(_update(999), _context("member")))
    assert role is roles.Role.CLIENT


@pytest.mark.parametrize("status", ["creator", "administrator", "member"])
def test_every_kind_of_subscription_counts(roles, status):
    assert asyncio.run(roles.resolve_role(_update(999), _context(status))) is roles.Role.CLIENT


@pytest.mark.parametrize("status", ["left", "kicked"])
def test_a_non_subscriber_stays_unknown(roles, status):
    assert asyncio.run(roles.resolve_role(_update(999), _context(status))) is roles.Role.UNKNOWN


def test_staff_do_not_depend_on_the_channel(roles):
    """У сотрудника канал даже не спрашивается: отписка не должна отбирать
    у инженера его очереди."""
    context = _context(error=AssertionError("канал спрашивать не должны"))
    assert asyncio.run(roles.resolve_role(_update(111), context)) is roles.Role.ADMIN
    assert asyncio.run(roles.resolve_role(_update(222), context)) is roles.Role.ENGINEER
    assert asyncio.run(roles.resolve_role(_update(333), context)) is roles.Role.MANAGER


def test_a_bot_that_is_not_channel_admin_denies_rather_than_crashes(roles):
    """Пока бота не сделали администратором канала, Telegram отвечает ошибкой.
    Это не повод пускать всех и не повод падать — доступа нет, в логе видно."""
    context = _context(error=BadRequest("member list is inaccessible"))
    assert asyncio.run(roles.resolve_role(_update(999), context)) is roles.Role.UNKNOWN


def test_membership_is_asked_once_and_then_remembered(roles):
    """Иначе на каждое сообщение уходит запрос в Telegram."""
    context = _context("member")
    for _ in range(3):
        asyncio.run(roles.resolve_role(_update(999), context))
    assert context.bot.get_chat_member.await_count == 1


def test_a_stale_membership_is_asked_again(roles, monkeypatch):
    context = _context("member")
    asyncio.run(roles.resolve_role(_update(999), context))
    monkeypatch.setattr(roles, "MEMBERSHIP_TTL", -1)
    asyncio.run(roles.resolve_role(_update(999), context))
    assert context.bot.get_chat_member.await_count == 2


def test_refusal_invites_to_the_channel(roles):
    text = roles.rejection_text(999)
    assert "cncelectric_russia" in text


# --- require_role -------------------------------------------------------------

def _handler_calls(roles, allowed, user_id, context):
    seen = []

    @roles.require_role(*allowed)
    async def handler(update, ctx):
        seen.append(ctx)

    asyncio.run(handler(_update(user_id), context))
    return seen


def test_a_subscribed_client_is_let_into_a_client_command(roles):
    context = _context("member")
    assert _handler_calls(roles, (roles.Role.CLIENT, roles.Role.MANAGER), 999, context)


def test_a_client_is_refused_an_engineer_command(roles):
    context = _context("member")
    update = _update(999)

    @roles.require_role(roles.Role.ADMIN, roles.Role.ENGINEER)
    async def handler(u, c):
        raise AssertionError("клиента сюда пускать нельзя")

    asyncio.run(handler(update, context))
    assert "недоступна" in update.message.reply_text.call_args[0][0].lower()


def test_a_non_subscriber_is_told_where_to_subscribe(roles):
    context = _context("left")
    update = _update(999)

    @roles.require_role(roles.Role.CLIENT)
    async def handler(u, c):
        raise AssertionError("не подписан — не пускаем")

    asyncio.run(handler(update, context))
    assert "cncelectric_russia" in update.message.reply_text.call_args[0][0]


# --- Проверка канала при запуске ---------------------------------------------
# Если бота не сделали администратором канала, ни один клиент внутрь не попадёт,
# а выглядеть это будет как «бот сломался». Пусть он скажет об этом сам.

def test_startup_check_passes_when_the_bot_is_channel_admin(roles):
    context = _context("administrator")
    assert asyncio.run(roles.channel_check(context.bot, bot_id=42)) is True


def test_startup_check_fails_when_the_bot_is_only_a_member(roles):
    """Обычному участнику Telegram не даёт смотреть других участников."""
    assert asyncio.run(roles.channel_check(_context("member").bot, bot_id=42)) is False


def test_startup_check_fails_when_the_channel_is_unreachable(roles):
    context = _context(error=BadRequest("chat not found"))
    assert asyncio.run(roles.channel_check(context.bot, bot_id=42)) is False
