"""Определение роли и доступ.

Сотрудники — только из `.env`: никакой БД пользователей, никаких приглашений.
Клиент — любой, кто подписан на канал CNC Electric; подписку спрашиваем у
самого Telegram, поэтому и списка клиентов вести не нужно.
Приоритет: admin > director > engineer > manager > client > unknown.

См. ARCHITECTURE.md §3. Права проверяются здесь, на границе доступа к
данным/командам — не в системном промпте LLM (§2.3 «Промпт — не механизм
безопасности»).
"""
from __future__ import annotations

import functools
import logging
import os
import time
from enum import Enum
from typing import Awaitable, Callable

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


class Role(str, Enum):
    ADMIN = "admin"
    DIRECTOR = "director"
    ENGINEER = "engineer"
    MANAGER = "manager"
    CLIENT = "client"
    UNKNOWN = "unknown"


def _parse_ids(env_var: str) -> frozenset[int]:
    raw = os.environ.get(env_var, "")
    ids: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError:
            logger.warning("Ignoring non-numeric ID %r in %s", chunk, env_var)
    return frozenset(ids)


def _parse_titled_ids(env_var: str) -> tuple[frozenset[int], dict[int, str]]:
    """`123:Руководитель CNC Electric, 456` → ID и подписи к ним.

    Руководителей двое, роль у них одна, а называться они должны по-разному:
    основатель — не то же самое, что технический директор. Подпись стоит
    рядом с ID, поэтому имена людей не попадают в код и не разъезжаются со
    списком доступа. Двоеточия нет — берётся общее название роли.
    """
    ids: set[int] = set()
    titles: dict[int, str] = {}
    for chunk in os.environ.get(env_var, "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        raw_id, _, title = chunk.partition(":")
        try:
            user_id = int(raw_id.strip())
        except ValueError:
            logger.warning("Ignoring non-numeric ID %r in %s", raw_id, env_var)
            continue
        ids.add(user_id)
        if title.strip():
            titles[user_id] = title.strip()
    return frozenset(ids), titles


# Читаются один раз при импорте. Подключение нового сотрудника = строка в
# .env + рестарт процесса (ARCHITECTURE.md §3), поэтому перечитывать на
# каждое сообщение не нужно.
ADMIN_IDS = _parse_ids("ADMIN_USER_IDS")
DIRECTOR_IDS, DIRECTOR_TITLES = _parse_titled_ids("DIRECTOR_USER_IDS")
ENGINEER_IDS = _parse_ids("ENGINEER_USER_IDS")
MANAGER_IDS, MANAGER_TITLES = _parse_titled_ids("MANAGER_USER_IDS")

ALL_ROLES = (Role.ADMIN, Role.DIRECTOR, Role.ENGINEER, Role.MANAGER)


def get_role(user_id: int | None) -> Role:
    """Приоритет сверху вниз: если ID указан в нескольких списках — высшая роль."""
    if user_id is None:
        return Role.UNKNOWN
    if user_id in ADMIN_IDS:
        return Role.ADMIN
    if user_id in DIRECTOR_IDS:
        return Role.DIRECTOR
    if user_id in ENGINEER_IDS:
        return Role.ENGINEER
    if user_id in MANAGER_IDS:
        return Role.MANAGER
    return Role.UNKNOWN


def role_of(update: Update) -> Role:
    """Роль сотрудника — синхронно, по спискам из .env.

    Клиента здесь не бывает: его роль зависит от подписки на канал, а это
    запрос в Telegram. Для полного ответа есть resolve_role() ниже."""
    user = update.effective_user
    return get_role(user.id if user else None)


# --- Клиент: доступ по подписке на канал -------------------------------------

# Канал, подписка на который и делает человека клиентом. Списка клиентов у
# бота нет и не будет: держать его вручную для сотен подписчиков невозможно,
# а Telegram и так знает, кто подписан.
CLIENT_CHANNEL = os.environ.get("CLIENT_CHANNEL", "@cncelectric_russia")

# Сколько помнить ответ Telegram. Без кэша запрос getChatMember уходил бы на
# каждое сообщение каждого клиента. Плата за это — отписавшийся сохраняет
# доступ до конца интервала.
MEMBERSHIP_TTL = 600.0

# user_id -> (когда спросили, подписан ли)
_memberships: dict[int, tuple[float, bool]] = {}

# Статусы, при которых человек реально состоит в канале. "restricted" бывает и
# у вышедших, поэтому у него отдельно проверяется is_member.
_MEMBER_STATUSES = {"creator", "administrator", "member", "owner"}


def forget_memberships() -> None:
    """Сбросить кэш подписок (тесты, смена канала, ручная проверка)."""
    _memberships.clear()


async def is_channel_member(bot, user_id: int) -> bool:
    cached = _memberships.get(user_id)
    if cached and time.monotonic() - cached[0] < MEMBERSHIP_TTL:
        return cached[1]
    try:
        member = await bot.get_chat_member(CLIENT_CHANNEL, user_id)
    except TelegramError:
        # Самая частая причина — бот не администратор канала, и тогда список
        # участников ему недоступен. Пускать всех в этом случае нельзя, падать
        # тоже: отказываем и оставляем след в логе, иначе причина «клиентов не
        # пускает» будет искаться вслепую.
        logger.warning(
            "Не удалось проверить подписку на %s (бот должен быть администратором канала)",
            CLIENT_CHANNEL, exc_info=True,
        )
        return False
    status = str(getattr(member, "status", ""))
    subscribed = status in _MEMBER_STATUSES or (
        status == "restricted" and bool(getattr(member, "is_member", False))
    )
    _memberships[user_id] = (time.monotonic(), subscribed)
    return subscribed


async def channel_check(bot, bot_id: int) -> bool:
    """Может ли бот вообще проверять подписку — то есть администратор ли он
    канала. Вызывается при запуске: без этого права ни один клиент внутрь не
    попадёт, а со стороны это выглядит как сломавшийся бот, а не как забытая
    галочка в настройках канала."""
    try:
        member = await bot.get_chat_member(CLIENT_CHANNEL, bot_id)
    except TelegramError:
        logger.warning("Канал %s недоступен боту", CLIENT_CHANNEL, exc_info=True)
        return False
    return str(getattr(member, "status", "")) in {"administrator", "creator", "owner"}


async def resolve_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Role:
    """Полная роль, включая клиента. Сотрудника канал не спрашивают: отписка
    от канала не должна отбирать у инженера его очереди."""
    role = role_of(update)
    if role is not Role.UNKNOWN:
        return role
    user = update.effective_user
    if user is None:
        return Role.UNKNOWN
    return Role.CLIENT if await is_channel_member(context.bot, user.id) else Role.UNKNOWN


# Отказ — не закрытая дверь, а визитка (спека §7.1): незнакомец уходит, зная,
# чем занимается CNC Electric и куда написать, даже если подписываться на
# канал не собирается.
REJECTION = (
    "CNC Electric — промышленное электрооборудование: автоматические выключатели, "
    "УЗО и дифавтоматы, контакторы, рубильники, АВР, оборудование постоянного тока, "
    "аппараты среднего напряжения. Завод в Китае с 1988 года, в России — "
    "официальное представительство с 2022 года.\n\n"
    "Бот показывает характеристики, цену и документы и передаёт вопросы инженерам CNC. "
    "Он работает для подписчиков канала {channel} — подпишитесь и нажмите /start.\n\n"
    "Не хотите подписываться: технические вопросы — help@cncrussia.com, "
    "коммерческие — info@cncrussia.com, пн–пт 8:15–18:30.\n\n"
    "Ваш Telegram ID: {user_id} — передайте администратору, если вы сотрудник CNC."
)


def rejection_text(user_id: int | str | None) -> str:
    return REJECTION.format(
        channel=CLIENT_CHANNEL,
        user_id=user_id if user_id is not None else "неизвестен",
    )


async def _deny(update: Update, text: str) -> None:
    if update.message:
        await update.message.reply_text(text)
    elif update.callback_query:
        await update.callback_query.answer(text, show_alert=True)


async def reject_unknown(update: Update) -> None:
    user_id = update.effective_user.id if update.effective_user else None
    await _deny(update, rejection_text(user_id))


Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def require_role(*allowed: Role) -> Callable[[Handler], Handler]:
    """Декоратор для хендлеров python-telegram-bot.

    Незнакомцу — вежливый отказ с его Telegram ID (§3). Знакомому без нужной
    роли — короткое «недоступно». Права всегда проверяются здесь, до вызова
    хендлера, а не внутри него.
    """

    def decorator(handler: Handler) -> Handler:
        @functools.wraps(handler)
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            # Именно resolve_role, а не role_of: иначе подписчик канала везде
            # выглядел бы посторонним. Ответ Telegram кэшируется, так что на
            # команду сотрудника лишнего запроса нет вовсе, а на клиента — не
            # чаще раза в MEMBERSHIP_TTL.
            role = await resolve_role(update, context)
            if role is Role.UNKNOWN:
                await reject_unknown(update)
                return
            if role not in allowed:
                await _deny(update, "Эта команда недоступна для вашей роли.")
                return
            await handler(update, context)

        return wrapped

    return decorator


def manager_sees_all_invoices() -> bool:
    return os.environ.get("MANAGER_SEES_ALL_INVOICES", "true").strip().lower() not in {"0", "false", "no"}
