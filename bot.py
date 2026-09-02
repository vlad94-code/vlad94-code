"""CNC Electric Knowledge Bot — закрытый бот для сотрудников CNC Russia.

Транспорт сменный: логика живёт в модулях, этот файл — тонкий слой Telegram
(ARCHITECTURE.md §2.6). Роли и доступ — core/roles.py. Наблюдаемость (лог
вопросов, 👍/👎, неотвеченные, audit) — core/db.py + core/logging_.py.
"""
import asyncio
import io
import logging
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from dotenv import load_dotenv

# ВАЖНО: .env должен подгружаться до импорта core.roles — тот читает
# ADMIN_USER_IDS/ENGINEER_USER_IDS/MANAGER_USER_IDS из os.environ сразу при
# импорте модуля (один раз, см. core/roles.py). Если load_dotenv() вызвать
# позже — после других "from core... import ..." — роли всегда окажутся
# пустыми, даже если .env заполнен правильно.
load_dotenv()

import httpx
from PIL import Image
from anthropic import AsyncAnthropic, RateLimitError as AnthropicRateLimitError
from telegram import BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from catalog_parser import count_short_indexed_answers_in_file, extract_catalog_facts, parse_generic_document, parse_pdf_catalog
from cnc_api import CncApi
from catalog_search import article_code as catalog_article_code, clear_cache as clear_catalog_cache, detail as catalog_detail, filter_products as catalog_filter_products, by_vendor_code as catalog_by_vendor_code, format_number as catalog_format_number, price_map as catalog_price_map, products as catalog_products, result_text as catalog_result_text, stock_map as catalog_stock_map, transit_map as catalog_transit_map, series as catalog_series
import catalog_links
import client_flow
import escalation
import managers
import media_links
import passport_links
import pricelist_store
import share_client
import unique_answers
import stock_report
from pricelist_parser import parse_workbook as parse_pricelist_workbook
from core.documents import (
    allocate_slot,
    document_id_for,
    list_documents,
    mark_registration_failed,
    register_document,
    register_legacy_document,
    upload_lock,
    replace_document_facts,
    summary,
)
from core.db import backup as backup_db, init_db, prune_old_backups
from core.backups import backup_uploads
from core.logging_ import (
    get_escalation,
    log_query,
    open_unanswered,
    pending_reviews,
    queue_for_review,
    record_audit,
    record_feedback,
    record_unanswered,
    resolve_review,
    resolve_unanswered,
    stale_escalations,
    stats_last_days,
)
from lexicon import resolve_category
from verified_answers_queue import rebuild_approved_answers_document
from core.roles import (
    ADMIN_IDS, CLIENT_CHANNEL, DIRECTOR_TITLES, Role, channel_check, reject_unknown,
    require_role, resolve_role, role_of,
)
from engines.router import route_local
from knowledge_matrix import rebuild, search as search_matrix
from api_sync import (
    OPERATIONAL_SOURCES,
    STATIC_SOURCES,
    get_sync_status,
    sync_in_progress,
    sync_operational,
    sync_static,
)

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
UPLOAD_DIR = Path(os.environ.get("UPLOADS_DIR", "uploads"))
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx"}
SYNC_PRODUCTS_HOUR = int(os.environ.get("SYNC_PRODUCTS_HOUR", "6"))
BACKUP_HOUR = int(os.environ.get("BACKUP_HOUR", "3"))
ESCALATION_REMINDER_HOUR = int(os.environ.get("ESCALATION_REMINDER_HOUR", "10"))
MOSCOW_TZ = timezone(timedelta(hours=3), name="MSK")

# Экран «Что умеет этот бот?»: единственный текст, который видит человек, ещё
# не нажавший «Старт». Заголовок над ним рисует сам Telegram, разметка в
# описании не поддерживается — только простой текст (512 символов, короткое
# описание в профиле — 120).
BOT_DESCRIPTION = (
    "Официальный Telegram-бот компании CNC Electric.\n"
    "Поможет подобрать оборудование среднего и низкого напряжения: "
    "характеристики, цены, наличие и сроки поставки."
)
BOT_SHORT_DESCRIPTION = (
    "Подбор оборудования CNC Electric: характеристики, цены, наличие."
)

INSTRUCTIONS = """Ты — технический помощник CNC Electric для сотрудников компании.
Отвечай на русском, кратко и профессионально. Отвечай ТОЛЬКО на основе найденных
материалов базы знаний и блока «Актуальные данные CNC Russia API», если он дан.
Не выдумывай характеристики, совместимость, наличие,
цены, артикулы, нормы или схемы: числа всегда должны быть скопированы из
предоставленных материалов, а не вычислены или предположены тобой. Если в
материалах нет уверенного ответа, прямо напиши: «В базе знаний нет
подтверждённого ответа» и укажи, что нужно уточнить.
Если в вопросе спрашивают максимум/минимум параметра серии, используй агрегированный
расчёт API, если он есть; иначе найди соответствующую таблицу серии в каталоге и
сообщи значение. В ответе одной короткой фразой объясни путь: «по каталогу» или
«по API». Для вопросов, связанных с безопасностью, монтажом или выбором защитных аппаратов,
напоминай о необходимости сверки с действующей документацией и квалифицированным
проектировщиком. Не указывай сам список источников в конце ответа — это делает код
на основе реально найденных документов, а не ты.
"""

# Меню бота (кнопка «Меню» в Telegram) собирается под роль. Раньше список был
# один на всех: меню обещало /reindex и /stats каждому, а команда потом
# отказывала. Telegram умеет отдавать свой набор на чат (setMyCommands со
# scope), так что обещание и права теперь сходятся.
#
# COMMAND_ROLES — единственный источник правды: и меню, и @require_role берут
# доступ отсюда, поэтому они не могут разойтись.
_BASIC = (Role.ADMIN, Role.DIRECTOR, Role.ENGINEER, Role.MANAGER)
# Клиент — подписчик канала CNC Electric (core/roles.py). Ему открыты три
# вещи: поиск по артикулу, аксессуары и документы. Всё остальное — работа
# сотрудника: склад, выгрузки, обновление данных, свободные вопросы.
_WITH_CLIENT = _BASIC + (Role.CLIENT,)
_ENGINEER_UP = (Role.ADMIN, Role.ENGINEER)
# Руководителю бот нужен как надзор: увидеть, что спрашивают и что осталось
# без ответа. Смотреть — да, чинить — нет: обновление базы, модерация ответов
# ИИ и загрузка документов остаются у инженера и админа.
_OVERSIGHT = (Role.ADMIN, Role.DIRECTOR, Role.ENGINEER)
_ADMIN_ONLY = (Role.ADMIN,)

COMMAND_ROLES: dict[str, tuple[Role, ...]] = {
    "start": _WITH_CLIENT, "search": _WITH_CLIENT, "accessories": _WITH_CLIENT,
    # Каталог — общая для всех вещь: ссылку на скачивание одинаково нужно
    # и менеджеру в переговорах, и подписчику канала.
    "catalog": _WITH_CLIENT,
    "freshness": _BASIC, "stock": _BASIC, "help": _WITH_CLIENT, "whoami": _WITH_CLIENT,
    # /sync тянет каталог на 27,6 МБ и обновляет данные сразу всем — база в
    # боте одна на всех, персональных копий нет. Менеджеру этого не нужно:
    # цены и остатки ему даёт кнопка «🔄 Обновить цены и наличие» (0,9 МБ),
    # а каталог всё равно приезжает сам в 06:00.
    "sync": _ENGINEER_UP,
    "status": _OVERSIGHT,
    "unanswered": _OVERSIGHT, "resolve_question": _ENGINEER_UP,
    "review": _ENGINEER_UP, "approve": _ENGINEER_UP, "reject": _ENGINEER_UP,
    "reindex": _ADMIN_ONLY, "documents": _ADMIN_ONLY, "warm_media": _ADMIN_ONLY,
    "stats": (Role.ADMIN, Role.DIRECTOR),
}

# Порядок в меню — от повседневного к служебному.
COMMAND_TITLES = [
    ("start", "Начать работу с ботом"),
    ("search", "Поиск по артикулу — характеристики, цена, остаток"),
    ("accessories", "Подобрать аксессуары по серии"),
    ("catalog", "📕 Каталоги для скачивания"),
    ("sync", "🔄 Полное обновление, включая каталог товаров"),
    ("freshness", "Актуальность данных"),
    ("stock", "Остатки и приходы в Excel"),
    ("help", "Список команд"),
    ("whoami", "Своя роль и Telegram ID"),
    ("status", "Статус базы"),
    ("unanswered", "Вопросы без подтверждённого ответа"),
    ("review", "Ответы ИИ, ожидающие подтверждения"),
    ("reindex", "Перестроить поисковый индекс документов"),
    ("documents", "Загруженные документы"),
    ("warm_media", "Прогреть кэш фото, сертификатов и 3D-моделей"),
    ("stats", "Запросы и оценки за 7 дней"),
]


# Подсказки по аргументам — только для /help, в меню Telegram они не помещаются.
HELP_ARGS = {
    "search": " АРТИКУЛ",
    "accessories": " СЕРИЯ",
    "approve": " НОМЕР [комментарий]",
    "reject": " НОМЕР [комментарий]",
    "resolve_question": " НОМЕР комментарий",
}


def commands_for(role: Role) -> list[BotCommand]:
    """Меню этой роли. Посторонний не получает ничего: бот пилотный и
    закрытый, ему отвечают отказом с его Telegram ID."""
    return [
        BotCommand(name, title)
        for name, title in COMMAND_TITLES
        if role in COMMAND_ROLES.get(name, ())
    ]


UNANSWERED_REASONS = {
    "local_no_answer": "нет однозначного ответа в локальном каталоге",
    "rag_no_evidence": "в базе нет подтверждённого ответа",
    "rag_unavailable": "ИИ был недоступен",
    "generation_error": "ошибка подготовки ответа",
    "document_request": "запрошен документ — пришлите ссылку через /resolve_question",
}


TELEGRAM_MAX_MESSAGE = 4096


def clip_for_telegram(text: str, limit: int = TELEGRAM_MAX_MESSAGE) -> str:
    """Keep a reply inside Telegram's hard message limit.

    Over the limit, sendMessage fails with BadRequest, and the generic handler
    in answer() turned that into "Не удалось подготовить ответ" — an error that
    looks like a broken search when the search in fact succeeded and merely
    found a lot ("CJX2-F" renders 9383 characters of API snapshot, "LAY5" 8860).

    The snapshot text is built by cnc_api._format_records() as *model context*,
    where 20 rich records is the right size; it only becomes a chat reply when
    Claude is unavailable. Rather than shrink it for both uses, clip it here, on
    the way out — and cut on a line boundary so a product list never breaks in
    the middle of an article number.
    """
    if len(text) <= limit:
        return text
    notice = "\n… ответ сокращён, уточните запрос."
    body = text[: limit - len(notice)]
    boundary = body.rfind("\n")
    if boundary > 0:
        body = body[:boundary]
    return body + notice


def split_for_telegram(text: str, limit: int = TELEGRAM_MAX_MESSAGE) -> list[str]:
    """Break a long listing into whole messages instead of cutting it short.

    clip_for_telegram() is right for a summary, where the tail is more of the
    same. It is wrong for "все аксессуары какие есть": one real group —
    CJX2-D / Тепловое реле, 77 positions — comes to 4236 characters and would
    silently lose its last rows. Splitting happens on line boundaries so no
    accessory is ever cut in half; a single line longer than the limit has no
    boundary to use and falls back to clipping.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        if len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current, size = [], 0
            chunks.append(clip_for_telegram(line, limit))
            continue
        if current and size + 1 + len(line) > limit:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + (1 if size else 0)
    if current:
        chunks.append("\n".join(current))
    return chunks


def source_names(matrix_rows: list[dict]) -> list[str]:
    """Sources for a RAG answer come from the local matrix, not from a hosted
    vector store's citation annotations — retrieval is entirely local now."""
    return sorted({row["source"] for row in matrix_rows if row.get("source")})


def client_start_keyboard() -> InlineKeyboardMarkup:
    """Холодный клиент не знает артикулов и не знает, что можно спросить —
    кнопки и есть готовые вопросы (спека §7.2)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏭 О компании", callback_data="about"),
         InlineKeyboardButton("📚 Каталоги", callback_data="catalog_menu")],
        [InlineKeyboardButton("🛒 Как купить", callback_data="how_to_buy"),
         InlineKeyboardButton("✉️ Спросить техслужбу", callback_data="ask_support")],
    ])


def main_keyboard(role: Role | None = None) -> InlineKeyboardMarkup | None:
    """Показывается на /start и после обновления — там, где человек начинает
    работу. Из-под каждого ответа эти кнопки убраны: они повторялись в каждом
    сообщении, занимали место и почти никогда не были нужны именно там."""
    # Клиенту — не «Обновить»: обновление тянет данные из CNC API сразу для всех
    # пользователей бота, и это работа сотрудника, а не гостя. Ему — первый
    # экран с готовыми вопросами.
    if role is Role.CLIENT:
        return client_start_keyboard()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить цены и наличие", callback_data="refresh_all")],
    ])


def answer_keyboard(query_log_id: int | None, offer: InlineKeyboardMarkup | None = None) -> InlineKeyboardMarkup:
    """Клавиатура под ответом: 👍/👎 (§10) + обычные кнопки обновления снимка.

    `offer` приходит из accessory_offer() и стоит первым — это либо вопрос
    «нужны аксессуары?» под основным товаром, либо кнопки серий под
    принадлежностью. Под списком позиций его нет: предлагать нечего, пока
    товар не выбран.
    """
    rows = []
    if offer is not None:
        rows += list(offer.inline_keyboard)
    if query_log_id is not None:
        rows.append([
            InlineKeyboardButton("👍", callback_data=f"fb:{query_log_id}:1"),
            InlineKeyboardButton("👎", callback_data=f"fb:{query_log_id}:-1"),
        ])
    return InlineKeyboardMarkup(rows)


def accessory_groups_keyboard(series: str, groups: list[tuple[str, list]]) -> InlineKeyboardMarkup:
    """Кнопки подтипов. В callback_data идёт НОМЕР группы, а не её название:
    «Дополнительный контакт и аварийный контакт» — 89 байт в UTF-8 при лимите
    Telegram в 64. Порядок алфавитный и пересчитывается на лету, поэтому
    состояние нигде не хранится и кнопки живут после перезапуска бота."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{name} ({len(rows)})", callback_data=f"acc:{series}:{index}")]
        for index, (name, rows) in enumerate(groups)
    ])


# Как бот называет роль в приветствии. Роль определяет, что человеку доступно,
# и он должен видеть её с первой секунды: иначе половина отсутствующих команд
# выглядит поломкой бота, а не границей прав.
ROLE_TITLES = {
    Role.ADMIN: "Администратор",
    Role.DIRECTOR: "Руководитель",
    Role.ENGINEER: "Инженер",
    Role.MANAGER: "Менеджер",
}

def role_title(role: Role, user_id: int | None = None) -> str | None:
    """Как назвать этого человека. У руководителей роль одна, а подписи
    разные — основатель и технический директор не взаимозаменяемы, — поэтому
    личная подпись из `.env` (DIRECTOR_USER_IDS) старше названия роли."""
    personal = DIRECTOR_TITLES.get(user_id) if role is Role.DIRECTOR else None
    return personal or ROLE_TITLES.get(role)


# Клиента по роли не называют: «Рад приветствовать, Клиент!» звучит как
# обращение к номеру в очереди. Ему — просто приветствие.
CLIENT_GREETING = (
    "Рад приветствовать!\n\n"
    "Я отвечаю на вопросы об оборудовании CNC Electric: характеристики, цена, "
    "документы, аксессуары, условия покупки.\n"
    "Знаете артикул — пришлите его: «B030524».\n"
    "Не знаете, с чего начать — нажмите кнопку ниже."
)

# Приписка под приветствием — только про то, что этой роли разрешено. Любая
# команда, названная здесь, но не разрешённая в COMMAND_ROLES, обернётся
# отказом бота на его же собственное предложение.
ROLE_HINTS = {
    Role.ADMIN: (
        "Можно прислать каталог или паспорт файлом — бот добавит его в базу знаний.\n"
        "Полное обновление, включая каталог товаров, — /sync."
    ),
    Role.DIRECTOR: (
        "Что спрашивают у бота и как оценивают ответы — /stats.\n"
        "Вопросы, оставшиеся без ответа, — /unanswered."
    ),
    Role.ENGINEER: (
        "Вопросы без подтверждённого ответа — /unanswered, ответы ИИ на проверку — /review.\n"
        "Полное обновление, включая каталог товаров, — /sync."
    ),
    Role.MANAGER: "Остатки и ближайшие приходы одним файлом — /stock.",
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # resolve_role, а не role_of: клиент опознаётся по подписке на канал,
    # а это запрос в Telegram (core/roles.py).
    role = await resolve_role(update, context)
    user = update.effective_user
    if role is Role.UNKNOWN:
        await reject_unknown(update)
        return
    context.user_data.pop("catalog_filters", None)
    # Меню ставится на конкретный чат: роль известна только здесь, при старте
    # процесса мы не знаем, кто придёт.
    if update.effective_chat:
        await context.bot.set_my_commands(
            commands_for(role), scope=BotCommandScopeChat(update.effective_chat.id)
        )
    if role is Role.CLIENT:
        await update.message.reply_text(CLIENT_GREETING, reply_markup=main_keyboard(role))
        return
    hint = ROLE_HINTS.get(role, "")
    await update.message.reply_text(
        f"Рад приветствовать, {role_title(role, user.id if user else None) or 'коллега'}!\n\n"
        "Я отвечаю на технические, коммерческие и логистические вопросы.\n"
        "Для цены и остатков укажите точный артикул: «B030524».\n\n"
        "Чтобы работать со свежими данными, нажмите «Обновить» — бот подтянет из CNC API "
        "цены, остатки и товары в пути. Характеристики и состав каталога обновляются сами, "
        f"ежедневно в {SYNC_PRODUCTS_HOUR:02d}:00 МСК."
        + (f"\n\n{hint}" if hint else ""),
        reply_markup=main_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Справка собирается из той же таблицы, что и меню, — раньше это был
    отдельный захардкоженный список, и он успел разойтись с реальными правами."""
    role = await resolve_role(update, context)
    if role is Role.UNKNOWN:
        await reject_unknown(update)
        return
    lines = ["Команды:"]
    for command in commands_for(role):
        lines.append(f"/{command.command}{HELP_ARGS.get(command.command, '')} — {command.description}")
    # Приписка про /sync — только тем, кто может её выполнить. Раньше она была
    # общей на всех, и менеджеру предлагалась команда, на которую бот отвечает
    # отказом: меню собирается из COMMAND_ROLES, а этот текст писался руками.
    forced = " Принудительно — /sync." if role in COMMAND_ROLES["sync"] else ""
    lines += [
        "",
        "🔄 Обновить цены и наличие — тянет из CNC API цены, остатки и товары в пути.",
        f"Характеристики и состав каталога обновляются сами, ежедневно в "
        f"{SYNC_PRODUCTS_HOUR:02d}:00 МСК.{forced}",
    ]
    if role is Role.ADMIN:
        lines.append("\nМожно прислать PDF, DOCX, TXT, MD, CSV или XLSX — бот добавит его в базу знаний.")
    await update.message.reply_text("\n".join(lines), reply_markup=main_keyboard(role))


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    role = await resolve_role(update, context)
    user_id = update.effective_user.id if update.effective_user else "неизвестен"
    if role is Role.UNKNOWN:
        await reject_unknown(update)
        return
    # Роль по-русски: человеку показываем слово, а не значение перечисления —
    # «client» в ответе бота выглядит как техническая утечка.
    await update.message.reply_text(
        f"Ваша роль: {role_title(role, user_id if isinstance(user_id, int) else None) or 'Клиент — подписчик канала CNC Electric'}\n"
        f"Ваш Telegram ID: {user_id}"
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Быстрый доступ к тому, что и так понимает свободный текст (см. §9
    ARCHITECTURE.md) — просто явный вход в меню бота, без новой логики
    поиска: catalog_search.detail() уже отдаёт характеристики+цену+остаток
    одним ответом, отдельных команд под "наличие"/"характеристики" не
    нужно."""
    role = await resolve_role(update, context)
    if role is Role.UNKNOWN:
        await reject_unknown(update)
        return
    article = " ".join(context.args).strip()
    if not article:
        await update.message.reply_text(
            "Введите артикул после команды или следующим сообщением, например: /search YCB9-63-1P-C16"
        )
        return
    await update.message.reply_text(catalog_detail(article, show_stock=role is not Role.CLIENT))


async def accessories_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    role = await resolve_role(update, context)
    if role is Role.UNKNOWN:
        await reject_unknown(update)
        return
    series = " ".join(context.args).strip()
    # Always clear a stale flag from an earlier bare "/accessories" call first —
    # otherwise a prior no-args invocation followed by "/accessories SERIES"
    # leaves the flag set, and the user's next unrelated free-text message
    # gets misrouted into _reply_accessories() at line ~576.
    context.user_data.pop("await_accessories_series", None)
    if not series:
        await update.message.reply_text(
            "Введите серию после команды или следующим сообщением, например: /accessories YCW3"
        )
        # Голая серия без слов "аксессуары"/"совместим" в свободном тексте не
        # распознаётся как запрос аксессуаров (см. engines/adapters.py —
        # AccessoryCompatibilityEngine требует явного слова-маркера) — без
        # этого флага следующее сообщение "YCW3" ушло бы в обычный список
        # товаров серии, а не в подбор аксессуаров.
        context.user_data["await_accessories_series"] = True
        return
    await _reply_accessories(update, series, role)


async def _reply_accessories(update: Update, series: str, role: Role) -> None:
    rows = await asyncio.to_thread(pricelist_store.accessories_for_series, series)
    if not rows:
        # Прайс-лист загружает админ, поэтому и совет про его актуальность —
        # только тому, кто может это сделать. Остальным бот предлагал бы
        # команду, на которую сам же ответит отказом.
        stale = (
            " или актуальность прайс-листа (/upload_pricelist)"
            if role in COMMAND_ROLES["documents"] else ""
        )
        await update.message.reply_text(
            f"По прайс-листу CNC для серии {series} аксессуары не найдены. "
            f"Проверьте написание серии{stale}."
        )
        return
    await update.message.reply_text(pricelist_store.format_accessories(series, rows))


# --- Каталоги для скачивания --------------------------------------------------
# Справочник «название → ссылка» ведётся руками и лежит в
# knowledge/catalog_links.xlsx (catalog_links.py). Бот отдаёт ссылку, а не сам
# файл: каталоги живут на share-хостинге 25.lgprk.ru и весят столько, что в
# лимит Telegram проходят не все.

def catalog_keyboard(catalogs: list[catalog_links.Catalog]) -> InlineKeyboardMarkup:
    """По кнопке на каталог, по одной в ряд: названия длинные, вдвоём в ряд
    они обрезаются до неузнаваемости."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📕 {catalog.title}", callback_data=f"cat:{catalog.key}")]
        for catalog in catalogs
    ])


CATALOGS_UNAVAILABLE = (
    "Справочник каталогов недоступен. Сообщите администратору — "
    "ссылки лежат в отдельном файле, и его, похоже, не выкатили."
)


async def catalog_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    role = await resolve_role(update, context)
    if role is Role.UNKNOWN:
        await reject_unknown(update)
        return
    catalogs = catalog_links.catalogs()
    if not catalogs:
        await update.message.reply_text(CATALOGS_UNAVAILABLE)
        return
    await update.message.reply_text(
        "Каталоги для скачивания — выберите нужный:",
        reply_markup=catalog_keyboard(catalogs),
    )


ABOUT_SHORT = (
    "CNC Electric — производитель промышленного электрооборудования, завод в "
    "городе Вэньчжоу, Китай. Основан в 1988 году, с 1997 года — общенациональная "
    "промышленная группа.\n\n"
    "ООО «СиЭнСи Электрик» — официальный представитель и вендор бренда в России "
    "с 2022 года. Москва, ул. Шереметьевская, 47. info@cncrussia.com, пн–пт 8:15–18:30."
)

ABOUT_FULL = (
    "Более 10 000 сотрудников, 0,25 млн м² производственных площадей, 9 компаний "
    "в составе группы, 9 эксклюзивных представительств за рубежом.\n"
    "Свыше 100 групп продукции и 20 000 моделей: аппараты и ячейки среднего "
    "напряжения, силовые трансформаторы, низковольтная аппаратура.\n\n"
    "Сертификация: ISO 9001, ISO 14001, OHSAS 18001. Продукция — CCC, CE, CB SEMKO. "
    "Торговая марка CNC многократно получала звание «Знаменитая китайская торговая марка»."
)

HOW_TO_BUY = (
    "Работаем в B2B: с юридическими лицами и ИП — напрямую, через дистрибьюторов "
    "и щитовых сборщиков.\n"
    "Минимальной партии и минимальной суммы заказа нет.\n\n"
    "Порядок: заявка менеджеру по вашему региону → менеджер выставляет счёт.\n"
    "Реквизиты для оплаты придут в счёте от менеджера.\n\n"
    "Сроки: позиция на складе — отгрузка сразу, срок в пути зависит от транспортной "
    "компании. Заказная позиция — 45 дней, самолётом из Китая.\n"
    "Доставка: СДЭК, Яндекс, Деловые линии или самовывоз со склада в Шелепаново."
)


async def about_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        ABOUT_SHORT,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Подробнее", callback_data="about_full")],
        ]),
    )


async def about_full_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(ABOUT_FULL)


async def how_to_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        HOW_TO_BUY,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 Связаться с менеджером", callback_data="want_manager")],
        ]),
    )


async def catalog_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Тот же список каталогов, что у команды /catalog — своей копии не заводим."""
    query = update.callback_query
    await query.answer()
    catalogs = catalog_links.catalogs()
    if not catalogs:
        await query.message.reply_text(CATALOGS_UNAVAILABLE)
        return
    await query.message.reply_text(
        "Каталоги CNC Electric:", reply_markup=catalog_keyboard(catalogs),
    )


async def catalog_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    role = await resolve_role(update, context)
    if role is Role.UNKNOWN:
        await reject_unknown(update)
        return
    await query.answer()
    catalog = catalog_links.find(query.data.split(":", 1)[1])
    if catalog is None:
        # Кнопка из старого сообщения, а справочник с тех пор обновили.
        # Промолчать нельзя, отдать соседнюю ссылку — тем более.
        await query.message.reply_text(
            "Этого каталога больше нет в справочнике — откройте /catalog заново."
        )
        return
    # Новым сообщением, а не заменой исходного: за одну команду забирают
    # несколько каталогов, и клавиатура нужна дальше.
    await query.message.reply_text(
        f"📕 {catalog.title}\n{catalog.url}",
        disable_web_page_preview=True,
    )


async def sync_status_text() -> str:
    status = get_sync_status()
    labels = {
        "products.json": "Характеристики/товары",
        "prices.json": "Цены",
        "stock-balances.json": "Остатки",
        "goods-in-transit.json": "Товары в пути",
    }
    lines = ["📊 Актуальность данных:"]
    for filename, label in labels.items():
        item = status.get(filename, {})
        timestamp = item.get("last_success_at") or item.get("last_checked_at")
        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt = dt.astimezone(MOSCOW_TZ)
                value = dt.strftime("%d.%m.%Y %H:%M МСК")
            except (ValueError, TypeError):
                value = timestamp
        else:
            value = "ещё не обновлялись"
        lines.append(f"• {label}: {value}")
    return "\n".join(lines)


# Все четыре источника CNC API за одно действие. Раньше /sync тянул только
# products.json, а кнопка — prices/stock/goods-in-transit, и чтобы получить
# актуальную базу приходилось делать оба, зная разницу между ними.
SYNC_SOURCES = STATIC_SOURCES | OPERATIONAL_SOURCES


async def _reindex_after_sync(application: Application, changed: list) -> tuple[int, int, int]:
    if not changed:
        return 0, 0, 0
    # Снимки прочитаны в память при старте; без сброса кэшей свежие цены и
    # остатки не видны до перезапуска процесса.
    application.bot_data["cnc_api"].clear_product_cache()
    clear_catalog_cache()
    pages, records = await asyncio.to_thread(rebuild)
    return len(changed), pages, records


async def refresh_operational(application: Application) -> tuple[int, int, int]:
    """Цены, остатки, товары в пути — то, ради чего нажимают кнопку. 0,9 МБ.

    Каталог (products.json) сюда намеренно не входит: это 27,6 МБ, которые API
    CNC отдаёт целиком — без сжатия, без пагинации и без фильтра по дате
    изменения (проверено 28.08.2026: gzip, Range, ?limit и ?modified_since
    игнорируются, HEAD отвечает 405). В ночь на 28.08 он качался 14 минут 46
    секунд, и кнопка всё это время выглядела зависшей, хотя честно работала.
    Характеристики товаров меняются редко — их забирает ночная задача в 06:00
    МСК (daily_full_sync), а инженер при необходимости дёргает /sync руками.
    """
    return await _reindex_after_sync(application, await sync_operational(force=True))


async def refresh_everything(application: Application) -> tuple[int, int, int]:
    """Полное обновление, включая каталог: ночная задача и ручной /sync.
    Возвращает (файлов изменилось, страниц каталога, записей API)."""
    changed = await sync_static(force=True)
    changed += await sync_operational(force=True)
    return await _reindex_after_sync(application, changed)


async def refresh_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    role = await resolve_role(update, context)
    if role is Role.UNKNOWN:
        await reject_unknown(update)
        return
    if role is Role.CLIENT:
        # Кнопка тянет данные из CNC API сразу для всех пользователей бота —
        # это действие сотрудника, и клиенту она даже не показывается.
        await query.answer("Обновление данных доступно сотрудникам CNC Electric.", show_alert=True)
        return
    await query.answer()
    if sync_in_progress():
        await query.edit_message_text(
            "🔄 Обновление уже выполняется другим запросом.\nПожалуйста, дождитесь его завершения.",
            reply_markup=main_keyboard(),
        )
        return
    await query.edit_message_text(
        "🔄 Обновляю цены, остатки и товары в пути…\n\n"
        "Характеристики и состав каталога сюда не входят — их бот обновляет сам "
        "каждую ночь в 06:00 МСК."
    )
    try:
        files, pages, records = await refresh_operational(context.application)
        head = (f"✅ Данные обновлены: файлов — {files}, страниц каталогов — {pages}, записей API — {records}."
                if files else "✅ Данные уже актуальны, ничего не изменилось.")
        await query.edit_message_text(head + "\n\n" + await sync_status_text(), reply_markup=main_keyboard())
    except Exception as exc:
        logger.exception("Full API synchronisation failed")
        await query.edit_message_text(
            "❌ Не удалось обновить. Старые сохранённые данные не изменены.\n\n"
            f"Причина: {str(exc).strip() or 'Неизвестная ошибка'}\n\n" + await sync_status_text(),
            reply_markup=main_keyboard(),
        )


@require_role(*COMMAND_ROLES["stock"])
async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Тот же файл, что документооборот рассылает каждое утро, — но собранный
    из API и доступный по требованию, а не раз в день письмом."""
    await update.effective_chat.send_action(ChatAction.UPLOAD_DOCUMENT)
    when = datetime.now(MOSCOW_TZ)
    try:
        payload, rows = await asyncio.to_thread(_build_stock_report, when)
    except Exception:
        logger.exception("Stock report generation failed")
        await update.message.reply_text("Не удалось собрать выгрузку. Сообщите администратору.")
        return
    if not rows:
        await update.message.reply_text(
            "В снимке нет ни одного остатка и ни одного прихода — сначала нажмите «Обновить»."
        )
        return
    await update.message.reply_document(
        document=payload,
        filename=stock_report.report_filename(when),
        caption=(
            f"Остатки и приходы на {when.strftime('%d.%m.%Y %H:%M')} МСК — {rows} позиций.\n"
            "«Доступно» — то, что можно продавать сейчас. Сам резерв не показан: "
            "при нуле стоит посмотреть в 1С — чужой резерв может быть уже неактуален."
        ),
    )


def _build_stock_report(when: datetime) -> tuple[bytes, int]:
    """Синхронная часть — читает снимки и сериализует книгу; вызывается в
    отдельном потоке, чтобы сборка 1600 строк не держала event loop."""
    book = stock_report.build_report(
        catalog_products(),
        {code: row.get("base_price") for code, row in catalog_price_map().items()},
        catalog_stock_map(),
        catalog_transit_map(),
        when,
    )
    buffer = io.BytesIO()
    book.save(buffer)
    # Минус шапка и строка заголовков: считаем только товарные строки.
    return buffer.getvalue(), max(book.active.max_row - 6, 0)


@require_role(*COMMAND_ROLES["freshness"])
async def freshness_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Когда какой источник обновлялся. Раньше висело кнопкой под каждым
    ответом; спрашивают это редко, а место занимало всегда."""
    await update.message.reply_text(await sync_status_text(), reply_markup=main_keyboard())


# Паспорт бот отдаёт из таблицы «Серия → ссылка» (passport_links.py,
# /upload_passports): в API этих данных нет — pictures и certificates пусты у
# всех 11942 товаров, полей паспорта/РЭ/3D в схеме нет вовсе.
#
# Сертификат, фотография и 3D-модель приходят из другой таблицы —
# номенклатурной, по артикулам (media_links.py, /upload_media): паспорт один
# на серию, а сертификат, фотография и модель у исполнений разные.
#
# Модель есть не у всех: 2379 артикулов из 11 552 (версия таблицы
# 31.08.2026). Для остальных кнопка регистрирует заявку — не заглушка, а
# рабочий механизм: потребность не теряется, и по накопленным заявкам видно,
# что наполнять в первую очередь. В эту же ветку падают паспорт, сертификат
# и модель, которых ещё нет в таблицах.
DOCUMENT_KINDS = {
    "pass": "Паспорт",
    "3d": "3D-модель",
    "cert": "Сертификат соответствия",
}


# Telegram принимает от бота файл не больше 50 МБ; паспорта — единицы мегабайт,
# но ссылка ведёт на чужой сайт, и однажды по ней может оказаться что угодно.
PASSPORT_SIZE_LIMIT = 50 * 1024 * 1024
PASSPORT_TIMEOUT = 30.0          # на операцию (соединение, чтение блока)
PASSPORT_TOTAL_TIMEOUT = 180.0   # на всю загрузку, см. fetch_passport()


def passport_filename(url: str) -> str:
    """Имя файла для Telegram — из самой ссылки, а не из серии: клиент,
    которому менеджер перешлёт документ, должен увидеть узнаваемое имя."""
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1]).strip()
    return name or "passport.pdf"


def passport_for_article(article: str) -> passport_links.PassportLink | None:
    """Паспорт по артикулу: таблица ведётся по сериям, а в кнопке — артикул.

    Наименование передаётся вместе с серией не для красоты: у YC7VA-3 и
    YC7VAN-1 серия каталога одна («YC7»), а паспорта разные — см.
    passport_links.link_for()."""
    product = catalog_by_vendor_code().get(article.upper())
    if product is None:
        return None
    return passport_links.link_for(catalog_series(product), str(product.get("name") or ""))


async def _download_passport(url: str) -> tuple[bytes, str] | None:
    async with httpx.AsyncClient(timeout=PASSPORT_TIMEOUT, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
    if len(response.content) > PASSPORT_SIZE_LIMIT:
        logger.warning("Passport too large for Telegram (%d bytes): %s", len(response.content), url)
        return None
    return response.content, passport_filename(url)


async def fetch_passport(url: str) -> tuple[bytes, str] | None:
    """Скачать паспорт по ссылке. None — если не вышло; тогда вызывающий
    отдаёт ссылку текстом, а не молчит.

    Бюджет на всю загрузку общий, потому что timeout httpx считается на
    операцию: сайт, отдающий файл по байту, укладывается в read-timeout
    бесконечно долго, и менеджер остаётся без ответа. Замерено на живом
    cncrussia.com: скачать разом 70 паспортов не удалось и за 12 минут.
    """
    try:
        return await asyncio.wait_for(_download_passport(url), timeout=PASSPORT_TOTAL_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Passport download timed out after %ss: %s", PASSPORT_TOTAL_TIMEOUT, url)
        return None
    except Exception:
        logger.exception("Passport download failed: %s", url)
        return None


async def send_passport(message, article: str, link: passport_links.PassportLink) -> None:
    """Прислать паспорт файлом; если сайт недоступен — хотя бы ссылкой.

    Один раз скачанный паспорт дальше пересылается по file_id: паспорта CNC
    весят 5–13 МБ, а сайт отдаёт их медленно (замер 27.08.2026: 770 КБ за
    54 с), и без кэша каждое нажатие кнопки стоило бы минуту ожидания.
    """
    caption = f"Паспорт серии {link.series} (артикул {article}).\n{link.url}"
    file_id = await asyncio.to_thread(passport_links.cached_file_id, link.url)
    if file_id:
        try:
            await message.reply_document(document=file_id, caption=caption)
            return
        except TelegramError:
            # Чужой file_id (сменился токен бота) — не повод отказать: забываем
            # его и качаем заново, менеджер разницы не замечает.
            logger.warning("Telegram rejected cached file_id for %s", link.url)
            await asyncio.to_thread(passport_links.forget_file_id, link.url)

    await message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
    payload = await fetch_passport(link.url)
    if payload is None:
        await message.reply_text(
            f"Паспорт серии {link.series} (артикул {article}) скачать не удалось — сайт не отдал файл.\n"
            f"Ссылка: {link.url}"
        )
        return
    content, filename = payload
    sent = await message.reply_document(document=io.BytesIO(content), filename=filename, caption=caption)
    sent_document = getattr(sent, "document", None)
    if sent_document is not None:
        await asyncio.to_thread(passport_links.remember_file_id, link.url, sent_document.file_id)


# Фото и сертификат — из таблицы номенклатуры по артикулу (media_links.py).
# Ссылки в ней ведут не на файл, а на страницу шары «Ориентира»; настоящие
# байты достаёт share_client.py.
#
# Предел Telegram на фотографию от бота — 10 МБ (у документа 50). Снимки в
# таблице весят сотни килобайт, но ссылка ведёт на чужой хостинг, и однажды
# по ней может оказаться что угодно: то, что не пролезет фотографией, уходит
# файлом, а не пропадает совсем.
PHOTO_SIZE_LIMIT = 10 * 1024 * 1024

# Второе условие sendPhoto — сумма сторон не больше 10000. Снимки в таблице
# лежат так, как их сняли: 6936×9248 при 13 МБ. Под это не проходят 148 фото
# из 654 (замер 29.08.2026), и Telegram отвечает на них
# Photo_invalid_dimensions. Отдавать их вложенным файлом — не то, что просили:
# фотография должна появляться картинкой, поэтому она ужимается.
PHOTO_MAX_SIDES_SUM = 10000
PHOTO_JPEG_QUALITY = 85


def fit_for_telegram(content: bytes) -> bytes | None:
    """Снимок в габаритах, которые Telegram примет фотографией.

    Возвращает те же байты, если ужимать нечего: пережатие стоит качества, а
    654 снимка из таблицы в основном и так небольшие. None — если это вообще
    не изображение: по ссылке на чужом хостинге однажды окажется что угодно,
    и тогда пусть уходит файлом, а не роняет ответ.
    """
    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except Exception:
        return None

    width, height = image.size
    if width + height <= PHOTO_MAX_SIDES_SUM and len(content) <= PHOTO_SIZE_LIMIT:
        return content

    scale = min(1.0, PHOTO_MAX_SIDES_SUM / (width + height))
    size = (max(1, int(width * scale)), max(1, int(height * scale)))
    if image.mode in ("RGBA", "LA", "P"):
        # JPEG прозрачности не знает, а без подложки она станет чёрной: у
        # снимков товара фон белый, и чёрная рамка выглядела бы браком.
        image = image.convert("RGBA")
        flat = Image.new("RGB", image.size, (255, 255, 255))
        flat.paste(image, mask=image.split()[-1])
        image = flat
    else:
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.resize(size, Image.LANCZOS).save(
        buffer, format="JPEG", quality=PHOTO_JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


async def send_image(message, content: bytes, filename: str, caption: str | None = None):
    """Отправить изображение так, чтобы оно дошло: фотографией, а не выйдет —
    файлом.

    Возвращает `(file_id, sent)`. `file_id` заполнен, только если ушло
    фотографией: идентификатор документа обратно фотографией не отдать, и
    класть его в кэш под видом фото значило бы обречь следующий показ на
    отказ и лишнюю перекачку.
    """
    fitted = fit_for_telegram(content)
    if fitted is not None and len(fitted) <= PHOTO_SIZE_LIMIT:
        try:
            sent = await message.reply_photo(photo=io.BytesIO(fitted), caption=caption)
            # Telegram отдаёт лесенку размеров одного снимка; file_id
            # последнего — самого крупного — и есть идентификатор файла.
            return sent.photo[-1].file_id, sent
        except BadRequest:
            logger.info("Telegram refused %s as a photo, sending it as a file", filename)
    sent = await message.reply_document(
        document=io.BytesIO(content), filename=filename, caption=caption)
    return None, sent


def photo_caption(article: str) -> str:
    """Подпись под фотографией — короткая: полная карточка идёт следующим
    сообщением, а у подписи в Telegram потолок 1024 символа."""
    product = catalog_by_vendor_code().get(article.upper())
    name = str((product or {}).get("name") or "").strip()
    return f"{article} — {name}" if name else article


async def send_product_photo(message, article: str) -> None:
    """Фотография товара перед карточкой характеристик.

    Молчит во всех неудачных случаях — и когда фото нет в таблице, и когда
    хостинг не отдал файл. Фотография не документ: менеджер спрашивал
    характеристики, и остаться без них из-за чужого сервера он не должен, а
    жаловаться ему на «Ориентир» бессмысленно — сделать он с этим ничего не
    может. Причина уходит в лог.
    """
    links = await asyncio.to_thread(media_links.for_article, article)
    if links is None or not links.photo_url:
        return
    url = links.photo_url
    caption = photo_caption(article)

    file_id = await asyncio.to_thread(media_links.cached_file_id, url, media_links.PHOTO)
    if file_id:
        try:
            await message.reply_photo(photo=file_id, caption=caption)
            return
        except TelegramError:
            # Чужой file_id (сменился токен бота) — не повод отказать:
            # забываем его и качаем заново, менеджер разницы не замечает.
            logger.warning("Telegram rejected cached photo file_id for %s", url)
            await asyncio.to_thread(media_links.forget_file_id, url, media_links.PHOTO)

    payload = await share_client.fetch(url)
    if payload is None:
        logger.warning("Product photo unavailable for %s: %s", article, url)
        return
    content, filename = payload
    new_file_id, _ = await send_image(message, content, filename, caption)
    if new_file_id:
        await asyncio.to_thread(media_links.remember_file_id, url, media_links.PHOTO, new_file_id)


async def send_certificate(message, article: str, links: media_links.MediaLinks) -> None:
    """Сертификат соответствия файлом; если хостинг недоступен — ссылкой.

    В отличие от фотографии здесь молчать нельзя: менеджер нажал кнопку и
    ждёт ответа. Отдаётся документом, а не картинкой, — сертификат пересылают
    клиенту, и ему нужны имя файла и несжатый скан.
    """
    url = links.cert_url
    caption = f"Сертификат соответствия — артикул {article}.\n{url}"

    file_id = await asyncio.to_thread(media_links.cached_file_id, url, media_links.CERT)
    if file_id:
        try:
            await message.reply_document(document=file_id, caption=caption)
            return
        except TelegramError:
            logger.warning("Telegram rejected cached certificate file_id for %s", url)
            await asyncio.to_thread(media_links.forget_file_id, url, media_links.CERT)

    await message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
    payload = await share_client.fetch(url)
    if payload is None:
        await message.reply_text(
            f"Сертификат по артикулу {article} скачать не удалось — хранилище не отдало файл.\n"
            f"Ссылка: {url}"
        )
        return
    content, filename = payload
    sent = await message.reply_document(
        document=io.BytesIO(content), filename=filename or links.cert_name or "certificate.pdf",
        caption=caption,
    )
    sent_document = getattr(sent, "document", None)
    if sent_document is not None:
        await asyncio.to_thread(media_links.remember_file_id, url, media_links.CERT, sent_document.file_id)


async def send_model(message, article: str, links: media_links.MediaLinks) -> None:
    """3D-модель файлом и ссылкой сразу.

    Ссылка стоит в подписи всегда, а не только когда файла нет: модель
    пересылают клиенту, и по ссылке он возьмёт её сам, когда пересланное
    сообщение потеряется в переписке. Если хостинг файл не отдал — крупные
    модели в лимит Telegram не проходят, — остаётся одна ссылка: молчать
    нельзя, менеджер нажал кнопку и ждёт ответа.
    """
    url = links.model_url
    caption = f"3D-модель — артикул {article}.\n{url}"

    file_id = await asyncio.to_thread(media_links.cached_file_id, url, media_links.MODEL)
    if file_id:
        try:
            await message.reply_document(document=file_id, caption=caption)
            return
        except TelegramError:
            logger.warning("Telegram rejected cached model file_id for %s", url)
            await asyncio.to_thread(media_links.forget_file_id, url, media_links.MODEL)

    await message.reply_chat_action(ChatAction.UPLOAD_DOCUMENT)
    payload = await share_client.fetch(url)
    if payload is None:
        await message.reply_text(
            f"3D-модель по артикулу {article} скачать не удалось — "
            f"хранилище не отдало файл.\nСсылка: {url}"
        )
        return
    content, filename = payload
    sent = await message.reply_document(
        document=io.BytesIO(content), filename=filename or links.model_name or "model.stp",
        caption=caption,
    )
    sent_document = getattr(sent, "document", None)
    if sent_document is not None:
        await asyncio.to_thread(media_links.remember_file_id, url, media_links.MODEL,
                                sent_document.file_id)


# Прогрев кэша file_id. Первый показ каждого файла стоит скачивания с чужого
# S3 и загрузки в Telegram — несколько секунд ожидания у того менеджера, кто
# спросил артикул первым. Файлов при этом всего 668 на 11 552 артикула, так
# что дешевле прогнать их разом.
WARM_DELAY = 1.0            # пауза между отправками: у Telegram есть предел частоты
WARM_PROGRESS_EVERY = 50    # чтобы прогрев не выглядел зависшим


class WarmReport(NamedTuple):
    """Чем кончился прогрев.

    Две причины неудачи разведены не для красоты: отчёт «148 не отдал
    хостинг» на прогреве prod 29.08.2026 увёл в сторону — файлы хостинг
    отдал, их не принял Telegram, потому что снимки с камеры не проходят по
    габаритам. Причина решает, что делать дальше: ждать хостинг или
    обновлять бота.
    """
    warmed: int         # загружено и запомнено
    skipped: int        # уже было в кэше
    unavailable: int    # хостинг не отдал файл
    refused: int        # Telegram не принял файл (или принял, но не фотографией)


async def warm_media_cache(message, progress_every: int = WARM_PROGRESS_EVERY) -> WarmReport:
    """Прогнать каждый файл через Telegram и запомнить его file_id.

    Греются ссылки, а не товары: один сертификат стоит у 5115 артикулов.

    Служебные сообщения удаляются сразу же — file_id переживает удаление
    сообщения, а 668 картинок в переписке админа не нужны никому. Осечка на
    одном файле не обрывает остальные: отозванная шара — это минус одна
    фотография, а не минус весь прогрев.
    """
    warmed = skipped = unavailable = refused = 0
    plan = [(kind, url)
            for kind in (media_links.PHOTO, media_links.CERT, media_links.MODEL)
            for url in await asyncio.to_thread(media_links.distinct_urls, kind)]
    for done, (kind, url) in enumerate(plan, start=1):
        if await asyncio.to_thread(media_links.cached_file_id, url, kind):
            skipped += 1
            continue
        payload = await share_client.fetch(url)
        if payload is None:
            unavailable += 1
            continue
        content, filename = payload
        try:
            if kind == media_links.PHOTO:
                file_id, sent = await send_image(message, content, filename)
            else:
                sent = await message.reply_document(document=io.BytesIO(content), filename=filename)
                file_id = sent.document.file_id
            await sent.delete()
        except TelegramError:
            logger.warning("Telegram refused a file during warm-up: %s", url, exc_info=True)
            refused += 1
            continue
        if file_id is None:
            # Снимок ушёл файлом — кэшировать нечего, следующий показ снова
            # его скачает. Это не поломка, но и не прогрев.
            refused += 1
            continue
        await asyncio.to_thread(media_links.remember_file_id, url, kind, file_id)
        warmed += 1
        if progress_every and done % progress_every == 0:
            await message.reply_text(f"Прогрев: {done} из {len(plan)}…")
        if WARM_DELAY:
            await asyncio.sleep(WARM_DELAY)
    return WarmReport(warmed, skipped, unavailable, refused)


def documents_keyboard(article: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{icon} {DOCUMENT_KINDS[kind]}", callback_data=f"doc:{article}:{kind}")]
        for kind, icon in (("pass", "📄"), ("3d", "🧊"), ("cert", "📜"))
    ])


def document_request_subject(article: str, kind: str) -> str | None:
    """Как заявка выглядит в очереди и в уведомлении админу — одна строка на
    оба места, чтобы формулировка не разъезжалась."""
    title = DOCUMENT_KINDS.get(kind)
    return f"{title} для артикула {article}" if title else None


def record_document_request(article: str, kind: str, *, user_id: int | None = None) -> int | None:
    """Кладёт заявку в ту же очередь /unanswered, что и вопросы без ответа —
    отдельная таблица не нужна, разбирают их одним списком."""
    subject = document_request_subject(article, kind)
    if subject is None:
        return None
    return record_unanswered(subject, "document_request", user_id=user_id)


async def documents_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    role = await resolve_role(update, context)
    if role is Role.UNKNOWN:
        await reject_unknown(update)
        return
    _, article, kind = query.data.split(":", 2)
    if kind == "?":
        await query.answer()
        await query.message.reply_text(
            f"Документы по {article} — что нужно?",
            reply_markup=documents_keyboard(article),
        )
        return
    if kind == "pass":
        link = await asyncio.to_thread(passport_for_article, article)
        if link is not None:
            await query.answer()
            await send_passport(query.message, article, link)
            return
    if kind in ("cert", "3d"):
        links = await asyncio.to_thread(media_links.for_article, article)
        if links is not None and (links.cert_url if kind == "cert" else links.model_url):
            await query.answer()
            if kind == "cert":
                await send_certificate(query.message, article, links)
            else:
                await send_model(query.message, article, links)
            return
    user_id = update.effective_user.id if update.effective_user else None
    question_id = record_document_request(article, kind, user_id=user_id)
    if question_id is None:
        await query.answer()
        return
    await query.answer("Запрос зарегистрирован")
    await query.message.reply_text(
        f"Запрос на «{DOCUMENT_KINDS[kind]}» по артикулу {article} "
        f"зарегистрирован, номер #{question_id}.\n"
        "Ответим, как только документ появится в базе."
    )
    await notify_admins_unanswered(
        context,
        question_id,
        document_request_subject(article, kind),
        "document_request",
        user_id=user_id,
        role=role,
    )


WIDER_LABELS = {
    "current": "%s А",
    "icu": "%s кА",
    "frame": "типоразмер %s",
}


def wider_keyboard(key: str, value) -> InlineKeyboardMarkup | None:
    """«Искать по всему каталогу» — когда серия упёрлась в потолок.

    Без неё разговор кончается на «в этой серии максимум 80 А»: клиенту нужен
    товар, а не сообщение о том, что его тут нет. Кнопка снимает все прошлые
    фильтры и ищет только запрошенное значение — по другим сериям.
    """
    label = WIDER_LABELS.get(key)
    if not label:
        return None
    shown = catalog_format_number(value) if isinstance(value, float) else str(value)
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🔎 Искать {label % shown} по всему каталогу",
                             callback_data=f"wide:{key}:{shown}")
    ]])


async def wider_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    role = await resolve_role(update, context)
    if role is Role.UNKNOWN:
        await reject_unknown(update)
        return
    _, key, raw = query.data.split(":", 2)
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        await query.answer()
        return
    filters = {key: value}
    results = await asyncio.to_thread(catalog_filter_products, filters)
    context.user_data["catalog_filters"] = filters
    await query.answer()
    for chunk in split_for_telegram(
            catalog_result_text(results, filters, show_stock=role is not Role.CLIENT)):
        await query.message.reply_text(chunk)


async def series_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«📦 Товары серии X» под карточкой принадлежности.

    Кнопка, а не подсказка «наберите CJX2S»: набранное руками имя серии легко
    испортить — кириллическая «С» вместо латинской, лишний дефис — и поиск
    молча ничего не найдёт. Нажатие ещё и делает эту серию текущим контекстом
    разговора, так что следующее «3р» уточнит именно её.
    """
    query = update.callback_query
    if not query or not query.data:
        return
    role = await resolve_role(update, context)
    if role is Role.UNKNOWN:
        await reject_unknown(update)
        return
    series = query.data.split(":", 1)[1]
    filters = {"series": series.upper()}
    results = await asyncio.to_thread(catalog_filter_products, filters)
    context.user_data["catalog_filters"] = filters
    await query.answer()
    for chunk in split_for_telegram(
            catalog_result_text(results, filters, show_stock=role is not Role.CLIENT)):
        await query.message.reply_text(chunk)


async def accessory_offer(article: str | None, series: str | None) -> tuple[str, InlineKeyboardMarkup | None]:
    """Что предложить под карточкой одной позиции: (приписка к тексту, кнопки).

    Направление зависит от того, чем товар является сам. Аксессуару предлагать
    аксессуары бессмысленно — у C000213 (доп. контакты F4-DN22) это выглядело
    как «подберите аксессуары к аксессуару». Для него осмыслен обратный вопрос:
    к каким сериям он подходит.

    Когда предложить нечего, молчать нельзя: отсутствие кнопки не отличить от
    «забыли добавить», поэтому пишем словами.
    """
    if not article:
        return "", None
    docs = [InlineKeyboardButton("📄 Документы", callback_data=f"doc:{article}:?")]
    if await asyncio.to_thread(pricelist_store.is_accessory, article):
        fits = await asyncio.to_thread(pricelist_store.series_for_accessory, article)
        if not fits:
            return "\n\nК каким товарам подходит — в прайс-листе не указано.", InlineKeyboardMarkup([docs])
        return "\n\nЭто принадлежность. Подходит к сериям — выберите, чтобы посмотреть товары:", InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"📦 Товары серии {name}", callback_data=f"srs:{name}")] for name in fits] + [docs]
        )
    product = catalog_by_vendor_code().get(article.upper())
    if product is None:
        return "", InlineKeyboardMarkup([docs])
    rows = await asyncio.to_thread(pricelist_store.accessories_for_product, product)
    if not rows:
        # Серия может нести аксессуары, а к этому типоразмеру/полюсам не
        # подходить ни один — тогда это тоже «нет», и сказать надо словами.
        return "\n\nПодходящих аксессуаров для этого товара нет.", InlineKeyboardMarkup([docs])
    return "", InlineKeyboardMarkup([
        [InlineKeyboardButton("🔧 Нужны аксессуары? Да", callback_data=f"acc:{article}:?"),
         InlineKeyboardButton("Нет", callback_data="acc:-:x")],
        docs,
    ])


async def accessory_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«Нужны аксессуары?» → подтипы → список позиций подтипа."""
    query = update.callback_query
    if not query or not query.data:
        return
    role = await resolve_role(update, context)
    if role is Role.UNKNOWN:
        await reject_unknown(update)
        return
    # В callback_data идёт АРТИКУЛ, а не серия: подбор сужается по типоразмеру,
    # полюсам, току и типу расцепителя конкретного аппарата, иначе к YCM3 100 3P
    # предлагались все шесть выкатных корзин серии, включая 400/630 и 4P.
    _, article, choice = query.data.split(":", 2)
    if choice == "x":
        await query.answer()
        await query.message.reply_text("Хорошо. Если понадобятся — просто спросите «аксессуары к <серия>».")
        return
    product = catalog_by_vendor_code().get(article.upper())
    if product is None:
        await query.answer("Товар не найден, откройте карточку заново.")
        return
    rows = await asyncio.to_thread(pricelist_store.accessories_for_product, product)
    groups = await asyncio.to_thread(pricelist_store.group_accessories, rows)
    if not groups:
        await query.answer()
        await query.message.reply_text(f"Подходящих аксессуаров для {article} в прайс-листе нет.")
        return
    if choice == "?":
        await query.answer()
        await query.message.reply_text(
            f"Аксессуары для {article} — {len(rows)} поз. Выберите тип:",
            reply_markup=accessory_groups_keyboard(article, groups),
        )
        return
    try:
        index = int(choice)
        name, group_rows = groups[index]
    except (ValueError, IndexError):
        # Индексы пересчитываются из прайса; после его обновления старая
        # кнопка может указывать в пустоту — это не ошибка, а устаревшее
        # сообщение.
        await query.answer("Список аксессуаров обновился, откройте заново.")
        return
    await query.answer()
    for chunk in split_for_telegram(pricelist_store.format_accessory_group(
            article, name, group_rows, show_stock=role is not Role.CLIENT)):
        await query.message.reply_text(chunk)


async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """👍/👎 под ответом — самый дешёвый сигнал качества (ARCHITECTURE.md §10)."""
    query = update.callback_query
    if not query or not query.data:
        return
    role = await resolve_role(update, context)
    if role is Role.UNKNOWN:
        await reject_unknown(update)
        return
    try:
        _, query_log_id, vote = query.data.split(":")
        query_log_id = int(query_log_id)
        vote = int(vote)
    except ValueError:
        await query.answer()
        return
    user_id = update.effective_user.id if update.effective_user else None
    record_feedback(query_log_id, vote, user_id=user_id)
    await query.answer("Спасибо, учтено!")


@require_role(*COMMAND_ROLES["status"])
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    documents, newest = summary()
    newest_text = newest.replace("T", " ").replace("+00:00", " UTC") if newest else "ещё нет"
    ai_status = "подключена" if context.application.bot_data.get("anthropic") else "не настроена (нет ANTHROPIC_API_KEY)"
    await update.message.reply_text(
        f"Модель: {MODEL} ({ai_status})\nАктивных документов: {documents}\nПоследняя загрузка: {newest_text}"
    )


@require_role(*COMMAND_ROLES["reindex"])
async def reindex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Обновляю матрицу знаний из каталогов и API-файлов…")
    pages, records = await asyncio.to_thread(rebuild)
    await update.message.reply_text(f"Матрица знаний обновлена: страниц каталогов — {pages}, записей API — {records}.")


@require_role(*COMMAND_ROLES["sync"])
async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Полное обновление, включая каталог (27,6 МБ). Кнопка каталог не трогает —
    он обновляется ночью в 06:00; эта команда нужна, когда каталог понадобился
    днём, сразу после правок в 1С."""
    if sync_in_progress():
        await update.message.reply_text("Обновление уже выполняется, дождитесь его завершения.")
        return
    await update.message.reply_text("Обновляю товары, цены, остатки и товары в пути из CNC API…")
    try:
        files, pages, records = await refresh_everything(context.application)
        await update.message.reply_text(
            f"Готово: обновлено файлов — {files}, страниц каталогов — {pages}, записей API — {records}."
            if files else "Готово: данные уже актуальны, ничего не изменилось.",
            reply_markup=main_keyboard(),
        )
    except Exception as exc:
        logger.exception("Manual full sync failed")
        await update.message.reply_text(f"Не удалось синхронизировать: {exc}")


@require_role(*COMMAND_ROLES["unanswered"])
async def unanswered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = open_unanswered()
    if not rows:
        await update.message.reply_text("Открытых вопросов без подтверждённого ответа нет.")
        return
    reasons = UNANSWERED_REASONS
    lines = ["📝 Вопросы без подтверждённого ответа:"]
    for row in rows:
        lines.append(
            f"• #{row['id']}: {row['question']}\n"
            f"  Причина: {reasons.get(str(row['reason']), str(row['reason']))}"
        )
    lines.append("\nЗакрыть после пополнения базы: /resolve_question НОМЕР комментарий")
    await update.message.reply_text("\n".join(lines)[:4096])


@require_role(*COMMAND_ROLES["resolve_question"])
async def resolve_question_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Формат: /resolve_question НОМЕР комментарий")
        return
    try:
        question_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Номер вопроса должен быть целым числом.")
        return
    if not resolve_unanswered(question_id, " ".join(context.args[1:])):
        await update.message.reply_text("Открытый вопрос с таким номером не найден.")
        return
    await update.message.reply_text(f"Вопрос #{question_id} закрыт.")


@require_role(*COMMAND_ROLES["stats"])
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = stats_last_days(7)
    total = data["total"]
    answered = data["answered"]
    rate = round(answered / total * 100) if total else 0
    lines = [
        "📈 За последние 7 дней:",
        f"• Запросов: {total}",
        f"• Отвечено: {answered} ({rate}%)",
    ]
    if data["by_module"]:
        lines.append("• По модулям: " + ", ".join(f"{name or '—'} — {count}" for name, count in data["by_module"]))
    votes = data["votes"]
    up, down = votes.get(1, 0), votes.get(-1, 0)
    lines.append(f"• 👍 {up} / 👎 {down}")
    await update.message.reply_text("\n".join(lines))


@require_role(*COMMAND_ROLES["documents"])
async def documents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = list_documents()
    if not rows:
        await update.message.reply_text("В реестре пока нет документов.")
        return
    status_labels = {"active": "активен", "superseded": "заменён", "failed": "ошибка"}
    lines = ["📚 Последние версии документов:"]
    for row in rows:
        timestamp = str(row["uploaded_at"]).replace("T", " ").replace("+00:00", " UTC")
        lines.append(
            f"• #{row['id']} {row['original_name']} v{row['version']} — "
            f"{status_labels.get(str(row['status']), row['status'])}; фактов: {row['facts']}; {timestamp}"
        )
    await update.message.reply_text("\n".join(lines)[:4096])


@require_role(*COMMAND_ROLES["documents"])
async def upload_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    document = update.message.document
    filename = document.file_name or "document"
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        await update.message.reply_text("Поддерживаются: PDF, DOCX, TXT, MD, CSV, XLSX.")
        return
    UPLOAD_DIR.mkdir(exist_ok=True, parents=True)
    # Held across allocate_slot -> register_document (including download/
    # parse in between) so two concurrent uploads never race on the same
    # (original_name, version) — see core.documents.upload_lock.
    async with upload_lock:
        slot = allocate_slot(filename)
        local_path = UPLOAD_DIR / slot.stored_name
        try:
            telegram_file = await document.get_file()
            await telegram_file.download_to_drive(local_path)
        except NetworkError:
            logger.exception("Document download failed (network)")
            await update.message.reply_text(
                "Не удалось скачать файл — оборвалось соединение с серверами Telegram "
                "(нестабильный интернет/VPN). Отправьте файл ещё раз."
            )
            return
        await update.message.reply_text(f"Добавляю «{filename}» в базу знаний…")
        user_id = update.effective_user.id if update.effective_user else None
        role = role_of(update)
        try:
            # Полностью локально: внешнего хранилища для поиска по документам
            # больше нет, индекс — knowledge_matrix.py (core/db.py не при чём).
            parsed_path = UPLOAD_DIR / f"{local_path.stem}.parsed.md"
            if suffix == ".pdf":
                parse_pdf_catalog(local_path, parsed_path, filename)
            else:
                parse_generic_document(local_path, parsed_path, filename)
            register_document(slot, local_path, parsed_path=parsed_path, uploaded_by=user_id)
            fact_count = replace_document_facts(slot.stored_name, extract_catalog_facts(parsed_path))
            await asyncio.to_thread(rebuild)
            record_audit(
                "document_upload",
                user_id=user_id,
                role=role.value,
                details={"filename": filename, "version": slot.version, "facts": fact_count},
            )
            # Placeholder detection in catalog_parser._is_placeholder_answer()
            # only recognizes today's exact CNC template wording — a suspiciously
            # short indexed answer is a cheap nudge for the uploader to glance at
            # /documents in case a reworded "ещё не отвечено" slipped through as
            # a confirmed fact (see catalog_parser.count_short_indexed_answers()).
            short_answers = await asyncio.to_thread(count_short_indexed_answers_in_file, local_path)
            await update.message.reply_text(
                f"«{filename}» принят (версия {slot.version}). Текст и таблицы преобразованы в поисковый формат. "
                + (f"Извлечено подтверждённых характеристик: {fact_count}. " if fact_count else "")
                + "Уже доступен в ответах."
                + (
                    f"\n⚠️ {short_answers} коротких ответов попали в базу как подтверждённые — "
                    "проверьте /documents, не заглушка ли это с новой формулировкой."
                    if short_answers
                    else ""
                )
            )
        except Exception:
            logger.exception("Document upload failed")
            await update.message.reply_text("Не удалось добавить документ. Проверьте формат файла и повторите попытку.")


@require_role(*COMMAND_ROLES["documents"])
async def upload_pricelist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Структурированная загрузка прайс-листа CNC (совместимость аксессуаров,
    типоразмер — см. ARCHITECTURE.md §5, строка 3a). Отдельно от
    upload_document(): тот делает полнотекстовый дамп xlsx, здесь нужны
    настоящие столбцы, не текст для угадывания.
    """
    document = update.message.document
    filename = document.file_name or "pricelist.xlsx"
    if Path(filename).suffix.lower() != ".xlsx":
        await update.message.reply_text("Прайс-лист должен быть в формате .xlsx.")
        return
    UPLOAD_DIR.mkdir(exist_ok=True, parents=True)
    # Логическое имя, не настоящее: настоящее содержит версию CNC в самом
    # имени файла (например "8865_248_...V3.8.2.xlsx"), и allocate_slot()
    # с ним каждый раз завёл бы новую цепочку original_name — старая версия
    # никогда не помечалась бы superseded. Настоящее имя всё равно попадает
    # в audit_log ниже.
    # Held across allocate_slot -> register_document — see upload_document()
    # above and core.documents.upload_lock.
    async with upload_lock:
        slot = allocate_slot(pricelist_store.LOGICAL_NAME)
        local_path = UPLOAD_DIR / slot.stored_name
        try:
            telegram_file = await document.get_file()
            await telegram_file.download_to_drive(local_path)
        except NetworkError:
            logger.exception("Price-list download failed (network)")
            await update.message.reply_text(
                "Не удалось скачать файл — оборвалось соединение с серверами Telegram "
                "(нестабильный интернет/VPN). Отправьте файл ещё раз."
            )
            return
        await update.message.reply_text(f"Разбираю прайс-лист «{filename}»… это может занять до минуты.")
        user_id = update.effective_user.id if update.effective_user else None
        role = role_of(update)
        # register_document() must run before parsing (import_items() needs
        # the document_id it creates) — so a parse/import failure here would
        # otherwise leave the new (empty) version wrongly 'active' and the
        # still-serving previous version wrongly 'superseded'. Scoped to just
        # register+parse+import so a later failure (e.g. record_audit,
        # reply_text) after a successful import never triggers a rollback.
        try:
            register_document(slot, local_path, uploaded_by=user_id)
            document_id = document_id_for(slot.stored_name)
            items = await asyncio.to_thread(parse_pricelist_workbook, local_path)
            count = await asyncio.to_thread(pricelist_store.import_items, document_id, items)
        except Exception:
            logger.exception("Price-list upload failed")
            mark_registration_failed(slot)
            await update.message.reply_text("Не удалось разобрать прайс-лист. Проверьте формат файла и повторите попытку.")
            return
        record_audit(
            "pricelist_upload",
            user_id=user_id,
            role=role.value,
            details={"filename": filename, "version": slot.version, "items": count},
        )
        await update.message.reply_text(
            f"Прайс-лист принят (версия {slot.version}): {count} позиций. "
            "Совместимость аксессуаров и типоразмер уже доступны в ответах."
        )


@require_role(*COMMAND_ROLES["documents"])
async def upload_passports(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Загрузка таблицы «Серия → ссылка на паспорт» (docs/MEDIA_LINKS.md).

    Устроена как upload_pricelist(): нужны настоящие столбцы, а не
    полнотекстовый дамп, который сделал бы общий upload_document().
    """
    document = update.message.document
    filename = document.file_name or "passports.xlsx"
    if Path(filename).suffix.lower() != ".xlsx":
        await update.message.reply_text("Таблица паспортов должна быть в формате .xlsx.")
        return
    UPLOAD_DIR.mkdir(exist_ok=True, parents=True)
    # Логическое имя вместо настоящего — как у прайс-листа: файл присылают под
    # разными именами («Новая таблица.xlsx»), и по ним supersede-логика
    # core/documents.py никогда бы не сработала. Настоящее имя уходит в audit.
    async with upload_lock:
        slot = allocate_slot(passport_links.LOGICAL_NAME)
        local_path = UPLOAD_DIR / slot.stored_name
        try:
            telegram_file = await document.get_file()
            await telegram_file.download_to_drive(local_path)
        except NetworkError:
            logger.exception("Passport table download failed (network)")
            await update.message.reply_text(
                "Не удалось скачать файл — оборвалось соединение с серверами Telegram "
                "(нестабильный интернет/VPN). Отправьте файл ещё раз."
            )
            return
        user_id = update.effective_user.id if update.effective_user else None
        role = role_of(update)
        # register_document() обязан отработать до разбора (import_links()
        # нужен document_id) — поэтому при ошибке разбора новая версия
        # откатывается, иначе она осталась бы «active» пустой, а работающая
        # предыдущая — «superseded». Как в upload_pricelist().
        try:
            register_document(slot, local_path, uploaded_by=user_id)
            document_id = document_id_for(slot.stored_name)
            links = await asyncio.to_thread(passport_links.parse_workbook, local_path)
            await asyncio.to_thread(passport_links.import_links, document_id, links)
            # Считаем не строки файла, а серии с настоящей ссылкой: строки со
            # статусом «сделать» тоже хранятся (они запрещают выдать паспорт
            # соседней серии), но паспортами не являются.
            count = await asyncio.to_thread(passport_links.count)
        except ValueError as error:
            logger.warning("Passport table rejected: %s", error)
            mark_registration_failed(slot)
            await update.message.reply_text(f"Файл не разобран. {error}")
            return
        except Exception:
            logger.exception("Passport table upload failed")
            mark_registration_failed(slot)
            await update.message.reply_text("Не удалось разобрать таблицу паспортов. Проверьте формат файла.")
            return
        record_audit(
            "passports_upload",
            user_id=user_id,
            role=role.value,
            details={"filename": filename, "version": slot.version, "links": count},
        )
        await update.message.reply_text(
            f"Таблица паспортов принята (версия {slot.version}): {count} серий со ссылкой. "
            "Кнопка «📄 Документы → Паспорт» уже отдаёт эти паспорта файлом."
        )


@require_role(*COMMAND_ROLES["documents"])
async def upload_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Загрузка таблицы номенклатуры «Артикул → фото и сертификат».

    Устроена как upload_passports(): нужны настоящие столбцы, а не
    полнотекстовый дамп, который сделал бы общий upload_document().
    """
    document = update.message.document
    filename = document.file_name or "nomenclature.xlsx"
    if Path(filename).suffix.lower() != ".xlsx":
        await update.message.reply_text("Таблица номенклатуры должна быть в формате .xlsx.")
        return
    UPLOAD_DIR.mkdir(exist_ok=True, parents=True)
    async with upload_lock:
        slot = allocate_slot(media_links.LOGICAL_NAME)
        local_path = UPLOAD_DIR / slot.stored_name
        try:
            telegram_file = await document.get_file()
            await telegram_file.download_to_drive(local_path)
        except NetworkError:
            logger.exception("Nomenclature table download failed (network)")
            await update.message.reply_text(
                "Не удалось скачать файл — оборвалось соединение с серверами Telegram "
                "(нестабильный интернет/VPN). Отправьте файл ещё раз."
            )
            return
        user_id = update.effective_user.id if update.effective_user else None
        role = role_of(update)
        # register_document() обязан отработать до разбора (import_links()
        # нужен document_id) — поэтому при ошибке разбора новая версия
        # откатывается, иначе она осталась бы «active» пустой, а работающая
        # предыдущая — «superseded». Как в upload_passports().
        try:
            register_document(slot, local_path, uploaded_by=user_id)
            document_id = document_id_for(slot.stored_name)
            links = await asyncio.to_thread(media_links.parse_workbook, local_path)
            await asyncio.to_thread(media_links.import_links, document_id, links)
            photos, certificates, models = await asyncio.to_thread(media_links.counts)
        except ValueError as error:
            logger.warning("Nomenclature table rejected: %s", error)
            mark_registration_failed(slot)
            await update.message.reply_text(f"Файл не разобран. {error}")
            return
        except Exception:
            logger.exception("Nomenclature table upload failed")
            mark_registration_failed(slot)
            await update.message.reply_text("Не удалось разобрать таблицу номенклатуры. Проверьте формат файла.")
            return
        record_audit(
            "media_upload",
            user_id=user_id,
            role=role.value,
            details={"filename": filename, "version": slot.version,
                     "photos": photos, "certificates": certificates,
                     "models": models},
        )
        await update.message.reply_text(
            f"Таблица номенклатуры принята (версия {slot.version}): "
            f"{photos} артикулов с фото, {certificates} с сертификатом, "
            f"{models} с 3D-моделью.\n"
            "Фото показывается в карточке товара, сертификат и модель — по кнопкам "
            "«📄 Документы → 📜 Сертификат соответствия» и «📄 Документы → 🧊 3D-модель».\n"
            "Первый показ каждого файла занимает несколько секунд; /warm_media прогреет кэш заранее."
        )


@require_role(*COMMAND_ROLES["warm_media"])
async def warm_media_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Прогреть кэш file_id по всем фото, сертификатам и 3D-моделям таблицы."""
    photos = await asyncio.to_thread(media_links.distinct_urls, media_links.PHOTO)
    certificates = await asyncio.to_thread(media_links.distinct_urls, media_links.CERT)
    models = await asyncio.to_thread(media_links.distinct_urls, media_links.MODEL)
    total = len(photos) + len(certificates) + len(models)
    if not total:
        await update.message.reply_text(
            "Прогревать нечего: таблица номенклатуры не загружена. "
            "Пришлите .xlsx с подписью /upload_media."
        )
        return
    # Срок не называется намеренно. Прежняя оценка бралась из WARM_DELAY и
    # обещала 11 минут, тогда как время съедает не пауза, а перекачка: замер
    # 29.08.2026 — 8,4 с на файл при размере 0,9–6,7 МБ, около полутора часов
    # на все 668. Сколько выйдет на другом канале, бот знать не может, а
    # обещание, которому нельзя верить, хуже отсутствия обещания.
    await update.message.reply_text(
        f"Прогреваю кэш: {len(photos)} фото, {len(certificates)} сертификатов "
        f"и {len(models)} 3D-моделей. "
        "Каждый файл качается из хранилища и загружается в Telegram, так что это надолго — "
        f"буду отчитываться каждые {WARM_PROGRESS_EVERY}. Служебные сообщения удалятся сами."
    )
    report = await warm_media_cache(update.message)
    lines = [f"Прогрев закончен: {report.warmed} загружено, {report.skipped} уже было в кэше."]
    # Про беду пишем, только если она была: строка «0 не отдал хостинг» под
    # удачным прогревом заставляет искать проблему там, где её нет.
    if report.unavailable:
        lines.append(f"{report.unavailable} файлов не отдал хостинг — попробуйте /warm_media ещё раз позже.")
    if report.refused:
        lines.append(
            f"{report.refused} файлов не принял Telegram. Так выглядят снимки, которые бот не смог "
            "ужать до его габаритов; если бота недавно обновляли — повторите /warm_media, "
            "а если нет, сообщите разработчику."
        )
    await update.message.reply_text("\n".join(lines))


# Бланк лежит в knowledge/, а не в uploads/: uploads — рабочее состояние
# бота и в git не хранится, а этот файл должен приезжать в прод вместе с кодом.
RECLAMATION_FORM = Path("knowledge") / "Форма_рекламационного_акта.docx"
_RECLAMATION_RE = re.compile(r"рекламац|брак|бракован|сгорел|не работает|дефект|гарантийн", re.I)


def _is_reclamation(question: str) -> bool:
    return bool(_RECLAMATION_RE.search(question or ""))


def client_answer_keyboard(article: str | None = None) -> InlineKeyboardMarkup:
    """Выход под каждым ответом: даже удачный ответ имеет продолжение.

    Кнопки документов не изобретаются заново — берутся из существующей
    documents_keyboard(), у которой уже есть свой хендлер на `doc:`.
    """
    rows = list(documents_keyboard(article).inline_keyboard) if article else []
    rows.append([InlineKeyboardButton("🤷 Не то, что нужно", callback_data="not_it")])
    return InlineKeyboardMarkup(rows)


def support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✉️ Спросить техслужбу", callback_data="ask_support")],
    ])


async def offer_support(update: Update, context: ContextTypes.DEFAULT_TYPE, question: str) -> None:
    """Ступень 5: подтверждённого ответа нет — но это не тупик."""
    context.user_data["pending_question"] = question
    await update.message.reply_text(
        "Подтверждённого ответа на этот вопрос у меня нет — отвечать наугад не буду.\n"
        "Передать вопрос технической службе CNC? Отвечают в течение 3 рабочих дней, "
        "ответ придёт сюда же.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✉️ Передать в техслужбу", callback_data="ask_support")],
            [InlineKeyboardButton("👤 Связаться с менеджером", callback_data="want_manager")],
        ]),
    )


async def offer_manager(update: Update, context: ContextTypes.DEFAULT_TYPE, question: str) -> None:
    """Коммерческий вопрос: карточка одного менеджера + уведомление ему же.

    Без уведомления сделка зависела бы от того, дойдут ли у клиента руки
    позвонить по выданному телефону (спека §5.1 п. 4).
    """
    city = context.user_data.get("city")
    if not city:
        context.user_data["awaiting_city"] = True
        context.user_data["pending_question"] = question
        await update.message.reply_text(
            "Из какого вы города? Подскажу вашего менеджера — у него актуальные "
            "остатки, сроки и условия."
        )
        return

    manager = managers.manager_for_city(city)
    if manager is None:
        await update.message.reply_text(
            f"{client_flow.MANAGER_INTRO}\n\n{managers.FALLBACK_TEXT}")
        return

    await update.message.reply_text(
        f"{client_flow.MANAGER_INTRO}\n\n{managers.format_manager(manager)}")

    if manager.user_id:
        try:
            await context.bot.send_message(
                manager.user_id,
                f"📩 Клиент из города {city} спрашивает:\n«{question}»\n"
                f"Telegram: @{update.effective_user.username or update.effective_user.id}",
            )
        except Exception:
            logger.exception("Не удалось уведомить менеджера %s", manager.user_id)


# Почта нужна только чтобы прислать документы письмом, и обязательной её
# делать нельзя: вопрос уходит в техслужбу в любом случае (спека §3.3).
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


async def _ask_support(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка и текстовый путь ведут в одно место: сначала вопрос, потом почта."""
    if not context.user_data.get("pending_question"):
        context.user_data["awaiting_support_question"] = True
        await message.reply_text("Опишите вопрос одним сообщением — передам технической службе.")
        return
    context.user_data["awaiting_support_email"] = True
    await message.reply_text(
        "Оставьте e-mail, если нужны документы письмом. Или пропустите этот шаг.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Пропустить", callback_data="skip_email")],
        ]),
    )


async def ask_support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await _ask_support(query.message, context)


async def _submit_support(message, context: ContextTypes.DEFAULT_TYPE, *, email: str | None) -> None:
    question = context.user_data.pop("pending_question", "")
    context.user_data.pop("awaiting_support_email", None)
    number = await escalation.register(
        message.get_bot(),
        question=question,
        user_id=message.chat.id,
        chat_id=message.chat.id,
        context=context.user_data.get("last_article"),
        region=context.user_data.get("city"),
        email=email,
    )
    await message.reply_text(escalation.client_receipt(number))


async def skip_email_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is not None:
        await query.answer()
        message = query.message
    else:
        message = update.message
    await _submit_support(message, context, email=None)


_QUESTION_NUMBER_RE = re.compile(r"Вопрос №(\d+)")

_STAFF_ANSWERING = (Role.ENGINEER, Role.ADMIN, Role.DIRECTOR)


class _QuestionReplyFilter(filters.MessageFilter):
    """Ответ на уведомление «🔔 Вопрос №N» — и только он.

    Фильтр, а не проверка внутри хендлера: общий текстовый хендлер стоит в
    той же группе, и всё, что этот фильтр пропустит, до answer() уже не
    дойдёт. Чужие reply-сообщения должны идти обычным путём.
    """

    def filter(self, message) -> bool:
        source = message.reply_to_message
        return bool(source and source.text and _QUESTION_NUMBER_RE.search(source.text))


QUESTION_REPLY = _QuestionReplyFilter()


async def engineer_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Инженер отвечает reply-сообщением на уведомление — бот доставляет
    ответ клиенту и предлагает положить его в справочник (спека §7.5).

    Роль проверяется молча, без @require_role: отказ в ответ на обычный
    reply случайного человека был бы шумом, а не защитой.
    """
    message = update.message
    source = message.reply_to_message
    if source is None or not (source.text or ""):
        return
    match = _QUESTION_NUMBER_RE.search(source.text)
    if not match:
        return
    if role_of(update) not in _STAFF_ANSWERING:
        return
    number = int(match.group(1))
    answered_by = update.effective_user.id if update.effective_user else 0
    if not await escalation.deliver(context.bot, number, message.text or "", answered_by=answered_by):
        await message.reply_text(f"Вопрос №{number} уже закрыт или не найден.")
        return
    await message.reply_text(
        f"Ответ на вопрос №{number} передан клиенту.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ В справочник", callback_data=f"to_reference:{number}")],
        ]),
    )


async def to_reference_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ответ, уже отданный клиенту, кладём в справочник — следующий такой
    вопрос бот закроет сам (спека §7.5)."""
    query = update.callback_query
    await query.answer()
    number = int(query.data.split(":", 1)[1])
    row = get_escalation(number)
    if row is None or not row.get("answer"):
        await query.message.reply_text("Ответа по этому вопросу нет.")
        return
    if not unique_answers.append_entry(row["question"], row["answer"]):
        await query.message.reply_text("Такой вопрос в справочнике уже есть.")
        return
    count = unique_answers.rebuild_unique_answers_document()
    rebuild()
    await query.message.reply_text(
        f"Добавлено в справочник. Всего подтверждённых ответов: {count}.\n"
        "Следующий такой вопрос бот закроет сам."
    )


class _CallbackUpdate(NamedTuple):
    """Callback-запрос в том виде, в каком его ждут хендлеры сообщений.

    offer_manager отвечает в чат и подписывает уведомление менеджеру именем
    клиента — ему нужны ровно эти два поля, а не весь Update.
    """
    message: object
    effective_user: object


def _message_update(query) -> _CallbackUpdate:
    return _CallbackUpdate(query.message, query.from_user)


async def want_manager_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    question = context.user_data.get("pending_question", "Вопрос по оборудованию CNC")
    await offer_manager(_message_update(query), context, question)


async def not_it_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«Не то, что нужно» — развилка, а не извинение."""
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "Уточните вопрос — или передам его человеку.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✉️ Спросить техслужбу", callback_data="ask_support")],
            [InlineKeyboardButton("👤 Связаться с менеджером", callback_data="want_manager")],
        ]),
    )


async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    role = await resolve_role(update, context)
    if role is Role.UNKNOWN:
        await reject_unknown(update)
        return
    question = (update.message.text or "").strip()
    if not question:
        return
    # Ответ на вопрос бота «какая серия?» разбирается раньше проверки роли:
    # для клиента это не свободный вопрос, а вторая половина /accessories,
    # которая ему разрешена.
    if context.user_data.pop("await_accessories_series", False):
        await _reply_accessories(update, question, role)
        return
    # Название города — не новый вопрос, а вторая половина передачи менеджеру.
    if context.user_data.pop("awaiting_city", False):
        context.user_data["city"] = question
        await offer_manager(update, context, context.user_data.pop("pending_question", question))
        return
    # Клиент описывает вопрос для техслужбы или оставляет почту — это не
    # свободный вопрос к боту, а вторая половина кнопки «Спросить техслужбу».
    if context.user_data.pop("awaiting_support_question", False):
        context.user_data["pending_question"] = question
        await _ask_support(update.message, context)
        return
    if context.user_data.get("awaiting_support_email"):
        email = question if EMAIL_RE.match(question) else None
        await _submit_support(update.message, context, email=email)
        return
    if role is Role.CLIENT:
        # Лестница ответа живёт в client_flow: здесь только доставка того,
        # что она вернула, и выход на человека, если ответа нет (спека §5.1).
        await update.effective_chat.send_action(ChatAction.TYPING)
        result = await client_flow.answer_for_client(question, context.user_data)
        user_id = update.effective_user.id if update.effective_user else None
        log_query(question, f"client:{result.kind}", result.kind not in
                  {"escalate_support", "escalate_manager"}, user_id=user_id, role=role.value)

        # Инструкция без бланка — половина ответа: бланк идёт первым, до
        # любого текста лестницы.
        if _is_reclamation(question) and RECLAMATION_FORM.exists():
            with RECLAMATION_FORM.open("rb") as form:
                await update.message.reply_document(
                    form,
                    filename=RECLAMATION_FORM.name,
                    caption="Форма рекламационного акта CNC. Заполните, приложите фото и видео "
                            "дефекта и отправьте на help@cncrussia.com — ответят до 3 рабочих дней.",
                )

        if result.kind == "article" and result.article:
            await send_product_photo(update.message, result.article)
            await update.message.reply_text(
                result.text, reply_markup=client_answer_keyboard(result.article))
            return
        if result.kind in {"reference", "catalog"}:
            await update.message.reply_text(
                result.text, reply_markup=client_answer_keyboard(result.article))
            return
        if result.kind == "replacement":
            await update.message.reply_text(result.text, reply_markup=support_keyboard())
            return
        if result.kind == "escalate_manager":
            await offer_manager(update, context, question)
            return
        await offer_support(update, context, question)
        return
    await update.effective_chat.send_action(ChatAction.TYPING)
    user_id = update.effective_user.id if update.effective_user else None
    try:
        route_context = {"catalog_filters": context.user_data.get("catalog_filters", {})}
        local = await route_local(question, route_context)
        if "catalog_filters" in local.context_update:
            context.user_data["catalog_filters"] = local.context_update["catalog_filters"]
        if local.handled:
            source_line = "\n\nИсточник: " + ", ".join(local.sources) if local.sources else ""
            sole_article = local.context_update.get("sole_article")
            note, offer = await accessory_offer(
                sole_article,
                local.context_update.get("accessory_series"),
            )
            wider = local.context_update.get("wider_search")
            if wider:
                offer = wider_keyboard(*wider) or offer
            qid = log_query(question, local.engine_name, True, user_id=user_id, role=role.value)
            # Фотография — только у карточки одной позиции: в списке из сорока
            # автоматов сорок картинок сделали бы ответ нечитаемым.
            if sole_article:
                await send_product_photo(update.message, sole_article)
            await update.message.reply_text(
                clip_for_telegram(local.text + note + source_line),
                reply_markup=answer_keyboard(qid, offer),
            )
            return

        # Точный артикул из локальных снимков — не требует сети.
        live_data = context.application.bot_data["cnc_api"].lookup_local(question)
        client: AsyncAnthropic | None = context.application.bot_data.get("anthropic")
        if client is None:
            # cnc_api reads only the local API snapshot — no network/LLM
            # involved — so it must still answer when Claude is down
            # (ARCHITECTURE.md §9: "шаги 1-4 работают полностью"). This used to
            # discard an already-found result and always claim "нет ответа".
            #
            # Rendered through catalog_search, not through lookup_local()'s own
            # string: that one is written for the model — up to 20 records with
            # every specification field inline — and in a chat window it came
            # out as an unreadable wall (and, before clipping, one Telegram
            # rejected outright). Same records, the one product layout the bot
            # uses everywhere else.
            live_records = context.application.bot_data["cnc_api"].lookup_records(question)
            if live_records:
                qid = log_query(question, "cnc_api_local", True, user_id=user_id, role=role.value)
                await update.message.reply_text(
                    clip_for_telegram(
                        catalog_result_text(live_records, {})
                        + "\n\n⚠️ Поиск по документам (Claude) временно недоступен — показан только снимок API."
                    ),
                    reply_markup=answer_keyboard(qid),
                )
                return
            await update.message.reply_text(
                "Поиск по документам временно недоступен, каталог и счета работают.\n"
                "В локальном каталоге нет однозначного ответа. Уточните тип оборудования, серию, ток, полюса или артикул."
            )
            qid = log_query(question, "none", False, user_id=user_id, role=role.value)
            question_id = record_unanswered(question, "local_no_answer", user_id=user_id)
            await notify_admins_unanswered(
                context, question_id, question, "local_no_answer", user_id=user_id, role=role)
            return

        live_context = f"\n\nАктуальные данные CNC Russia API:\n{live_data}" if live_data else ""
        matrix_rows = search_matrix(question)
        matrix_context = "\n".join(
            f"[{row['kind']}; {row['source']}; стр. {row['page'] or '-'}] {row['text']}"
            for row in matrix_rows
        )
        if matrix_context:
            matrix_context = "\n\nЛокальная матрица знаний (релевантные фрагменты):\n" + matrix_context
        message = await client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=INSTRUCTIONS,
            messages=[{"role": "user", "content": question + live_context + matrix_context}],
        )
        text = "".join(block.text for block in message.content if getattr(block, "type", None) == "text").strip()
        text = text or "❓ В базе знаний нет подтверждённого ответа, сверьтесь с паспортом."
        sources = source_names(matrix_rows)
        if sources and "источники:" not in text.lower():
            text += "\n\nИсточники: " + ", ".join(sources)
        # log_query's "answered" must reflect whether we actually gave a
        # confirmed answer, not just that the rag branch ran — otherwise
        # /stats' "Отвечено" counts every "нет подтверждённого ответа" reply
        # as a success (В-4).
        answered = "в базе знаний нет подтверждённого ответа" not in text.lower()
        qid = log_query(question, "rag", answered, user_id=user_id, role=role.value)
        # clip_for_telegram, not a bare text[:4096]: the old slice cut mid-word
        # and left no sign that the answer had been truncated at all.
        await update.message.reply_text(clip_for_telegram(text), reply_markup=answer_keyboard(qid))
        if answered:
            # Ответ ИИ — кандидат в базу знаний, но не её часть: пока инженер
            # не подтвердил через /approve, он никуда не индексируется.
            # Автоматическое самообучение здесь недопустимо — одна ошибка
            # модели закрепилась бы как подтверждённый факт (ARCHITECTURE.md §5).
            # Категорию берём тем же лексиконом, что и каталожный поиск, —
            # инженеру при подтверждении ничего вводить не нужно.
            category = (resolve_category(question) or {}).get("label") or "Общие вопросы"
            queue_for_review(question, text, sources, query_log_id=qid, category=category)
        else:
            question_id = record_unanswered(question, "rag_no_evidence", user_id=user_id)
            await notify_admins_unanswered(
                context, question_id, question, "rag_no_evidence", user_id=user_id, role=role)
    except AnthropicRateLimitError:
        await update.message.reply_text(
            "Каталожный поиск работает без ИИ. Для свободных технических вопросов "
            "Claude сейчас недоступен из-за лимита API. Уточните артикул, серию или параметры товара."
        )
        log_query(question, "rag_rate_limited", False, user_id=user_id, role=role.value)
        question_id = record_unanswered(question, "rag_unavailable", user_id=user_id)
        await notify_admins_unanswered(
            context, question_id, question, "rag_unavailable", user_id=user_id, role=role)
    except Exception:
        logger.exception("Answer generation failed")
        await update.message.reply_text("Не удалось подготовить ответ. Попробуйте ещё раз или сообщите администратору.")
        log_query(question, "error", False, user_id=user_id, role=role.value)
        question_id = record_unanswered(question, "generation_error", user_id=user_id)
        await notify_admins_unanswered(
            context, question_id, question, "generation_error", user_id=user_id, role=role)


@require_role(*COMMAND_ROLES["review"])
async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Очередь ответов ИИ, ожидающих подтверждения (§10 — тот же принцип, что
    /unanswered, но предмет другой: не «ответить нечем», а «ответ есть, решаем,
    класть ли его в постоянную базу знаний»)."""
    rows = pending_reviews()
    if not rows:
        await update.message.reply_text("Ответов ИИ, ожидающих подтверждения, нет.")
        return
    lines = ["🧠 Ответы ИИ на подтверждение:"]
    for row in rows:
        answer = " ".join(str(row["answer"]).split())
        if len(answer) > 300:
            answer = answer[:300] + "…"
        lines.append("")
        lines.append(f"• #{row['id']} [{row['category'] or 'Общие вопросы'}]")
        lines.append(f"  Вопрос: {row['question']}")
        lines.append(f"  Ответ: {answer}")
    lines.append("")
    lines.append("/approve НОМЕР [комментарий] — в базу знаний")
    lines.append("/reject НОМЕР [комментарий] — отклонить")
    await update.message.reply_text("\n".join(lines)[:4096])


async def _resolve_review_command(update: Update, context: ContextTypes.DEFAULT_TYPE, status: str) -> None:
    command = "approve" if status == "approved" else "reject"
    if not context.args:
        await update.message.reply_text(f"Формат: /{command} НОМЕР [комментарий]")
        return
    try:
        review_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Номер должен быть целым числом.")
        return
    note = " ".join(context.args[1:])
    user_id = update.effective_user.id if update.effective_user else None
    role = role_of(update)
    if not resolve_review(review_id, status, reviewed_by=user_id, note=note):
        await update.message.reply_text(
            f"Ответ #{review_id} не найден или уже рассмотрен. Актуальный список: /review"
        )
        return
    record_audit(
        f"rag_review_{status}",
        user_id=user_id,
        role=role.value,
        details={"review_id": review_id, "note": note},
    )
    if status == "rejected":
        await update.message.reply_text(f"Ответ #{review_id} отклонён, в базу знаний не попадёт.")
        return
    # rebuild_approved_answers_document() runs allocate_slot -> register_document
    # in a worker thread; the lock is asyncio-only, so it's held here in the
    # calling coroutine rather than inside the threaded function.
    async with upload_lock:
        count = await asyncio.to_thread(rebuild_approved_answers_document)
    await asyncio.to_thread(rebuild)
    await update.message.reply_text(
        f"Ответ #{review_id} одобрен. Одобренных ответов в базе знаний: {count}. Уже доступен в поиске."
    )


@require_role(*COMMAND_ROLES["approve"])
async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _resolve_review_command(update, context, "approved")


@require_role(*COMMAND_ROLES["reject"])
async def reject_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _resolve_review_command(update, context, "rejected")


async def notify_admins(application: Application, text: str) -> None:
    for admin_id in ADMIN_IDS:
        try:
            await application.bot.send_message(chat_id=admin_id, text=text)
        except Exception:
            logger.exception("Failed to notify admin %s", admin_id)


async def notify_admins_unanswered(
    context: ContextTypes.DEFAULT_TYPE,
    question_id: int,
    subject: str,
    reason: str,
    *,
    user_id: int | None = None,
    role: Role | None = None,
) -> None:
    """Новая запись очереди /unanswered — сразу админу в личку.

    Раньше бот отвечал «передан инженеру», но не отправлял ничего и никому:
    заявка ложилась в таблицу unanswered, и увидеть её можно было, только
    самому набрав /unanswered. Никто из ENGINEER_USER_IDS этого не делал —
    записи висели открытыми неделями. Пока поток небольшой, вопросы разбирает
    и раздаёт админ; раскладка по категории оборудования на конкретного
    инженера — следующий шаг, когда заявок станет много.

    На ответ пользователю доставка не влияет: notify_admins() гасит ошибки
    отправки в лог, а сама заявка к этому моменту уже в базе.
    """
    who = f"ID {user_id}" if user_id else "отправитель неизвестен"
    title = role_title(role, user_id) if role is not None else None
    if title:
        who += f", {title}"
    headline = "📄 Заявка на документ" if reason == "document_request" else "📝 Вопрос без ответа"
    await notify_admins(
        context.application,
        f"{headline} #{question_id}: {subject}\n"
        f"Причина: {UNANSWERED_REASONS.get(reason, reason)}\n"
        f"Кто спрашивал: {who}\n"
        f"Закрыть: /resolve_question {question_id} комментарий",
    )


async def _run_daily(application: Application, hour: int, task_name: str, job) -> None:
    """Общий планировщик: раз в сутки в заданный час по МСК выполнить job()."""
    while True:
        now = datetime.now(MOSCOW_TZ)
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            await job()
        except Exception:
            logger.exception("%s failed", task_name)
            await notify_admins(application, f"⚠️ {task_name}: сбой, см. логи. Предыдущие данные не изменены.")


async def daily_full_sync(application: Application) -> None:
    """Товары/характеристики: ежедневно в SYNC_PRODUCTS_HOUR (ARCHITECTURE.md §6)."""

    async def job() -> None:
        changed = await sync_static(force=True)
        if changed:
            application.bot_data["cnc_api"].clear_product_cache()
            clear_catalog_cache()
            pages, records = await asyncio.to_thread(rebuild)
            logger.info(
                "Daily static API sync changed %s files; matrix rebuilt: pages=%s records=%s",
                len(changed), pages, records,
            )
        else:
            logger.info("Daily static API sync completed; products snapshot unchanged")

    await _run_daily(application, SYNC_PRODUCTS_HOUR, "Ежедневная синхронизация товаров", job)


async def daily_backup(application: Application) -> None:
    """`bot.db`: ежедневный бэкап в BACKUP_HOUR, хранить 30 дней (ARCHITECTURE.md §11).

    Копирование BACKUP_DIR за пределы сервера (второй диск/облако) — задача
    эксплуатации, не этого процесса.
    """

    async def job() -> None:
        dest = backup_db()
        removed = prune_old_backups(keep_days=30)
        logger.info("Backup complete: %s (pruned %s old backups)", dest, removed)
        # uploads/ — исходные каталоги и паспорта: knowledge.db из них
        # пересобирается (/reindex), они сами — ниоткуда. Архивирование в
        # отдельном потоке: zip на 57 МБ заблокировал бы цикл событий и бот
        # перестал бы отвечать на время бэкапа.
        archive = await asyncio.to_thread(backup_uploads)
        if archive:
            logger.info("Uploads archived: %s", archive)

    await _run_daily(application, BACKUP_HOUR, "Ежедневный бэкап bot.db", job)


async def remind_about_stale_escalations(application: Application) -> int:
    """Вопрос, который третий рабочий день без ответа, напоминает о себе сам.

    Молчание — единственный оставшийся способ отпустить клиента с пустыми
    руками, и он закрывается здесь (спека §7.6). Возвращает число вопросов,
    о которых напомнили.
    """
    rows = stale_escalations()
    for row in rows:
        for engineer_id in escalation.ENGINEER_IDS:
            await application.bot.send_message(
                engineer_id,
                f"⏰ Вопрос №{row['id']} без ответа: «{row['question']}»",
            )
        await application.bot.send_message(
            row["chat_id"],
            f"Ваш вопрос №{row['id']} ещё в работе у технической службы. "
            "Если нужно быстрее — напишите менеджеру: /start → «Связаться с менеджером».",
        )
    return len(rows)


async def daily_escalation_reminders(application: Application) -> None:
    """Ежедневно в ESCALATION_REMINDER_HOUR — обход просроченных вопросов."""

    async def job() -> None:
        reminded = await remind_about_stale_escalations(application)
        logger.info("Stale escalation reminders sent: %s", reminded)

    await _run_daily(application, ESCALATION_REMINDER_HOUR, "Напоминание о просроченных вопросах", job)


async def post_init(application: Application) -> None:
    # По умолчанию — пустое меню: бот закрытый, и постороннему нечего
    # предлагать. Свой набор каждый получает на /start, когда роль известна.
    await application.bot.set_my_commands([])
    # Экран «Что умеет этот бот?» — то, что человек видит ДО кнопки «Старт».
    # Это не сообщение, а описание бота в Telegram, поэтому ставится здесь, при
    # запуске процесса, а не в ответ на чьё-то действие. Сбой описания не повод
    # не поднимать бота: без него он работает, просто первый экран пустой.
    try:
        await application.bot.set_my_description(BOT_DESCRIPTION)
        await application.bot.set_my_short_description(BOT_SHORT_DESCRIPTION)
    except TelegramError:
        logger.exception("Не удалось обновить описание бота в Telegram")
    # Клиент опознаётся по подписке на канал, а спрашивать участников Telegram
    # разрешает только администратору канала. Забытая галочка = ни одного
    # клиента внутри, и со стороны это неотличимо от поломки бота.
    if not await channel_check(application.bot, application.bot.id):
        logger.warning(
            "Бот не администратор канала %s — клиенты войти не смогут", CLIENT_CHANNEL
        )
        await notify_admins(
            application,
            f"⚠️ Бот не администратор канала {CLIENT_CHANNEL}: проверить подписку клиентов "
            "невозможно, и все они получат отказ. Добавьте бота администратором канала.",
        )
    init_db()
    migrated = facts = 0
    for parsed_path in UPLOAD_DIR.glob("*.parsed.md"):
        pdf_path = UPLOAD_DIR / f"{parsed_path.name.removesuffix('.parsed.md')}.pdf"
        if not pdf_path.exists():
            continue
        slot = register_legacy_document(pdf_path, parsed_path)
        if slot is None:
            continue
        migrated += 1
        facts += replace_document_facts(slot.stored_name, extract_catalog_facts(parsed_path))
    if migrated:
        logger.info("Migrated %s legacy catalogues into document registry; extracted %s facts", migrated, facts)
    # post_init runs before Application is marked as running. Using
    # Application.create_task here produces PTBUserWarning, so create normal
    # asyncio tasks and keep references for clean shutdown.
    application.bot_data["daily_sync_task"] = asyncio.create_task(daily_full_sync(application), name="daily-api-full-sync")
    application.bot_data["daily_backup_task"] = asyncio.create_task(daily_backup(application), name="daily-backup")
    application.bot_data["daily_escalation_task"] = asyncio.create_task(
        daily_escalation_reminders(application), name="daily-escalation-reminders")


async def post_shutdown(application: Application) -> None:
    for key in ("daily_sync_task", "daily_backup_task", "daily_escalation_task"):
        task = application.bot_data.pop(key, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled Telegram update error", exc_info=context.error)


def preflight() -> None:
    """Проверки перед стартом: лучше упасть громко, чем молча отвечать «не найдено».

    Главный риск при выносе бота на постоянный хост — запуск с чужой рабочей
    директорией. Пути к данным в проекте относительные (`data/`, `uploads/`),
    поднимется штатно, создаст пустой `data/` в системном каталоге и будет отвечать
    поднимется штатно, создаст пустой `data/` на новом месте и будет отвечать
    «ничего не найдено» на каждый вопрос. Процесс жив, логи чистые, ошибок нет —
    понять это можно только по жалобам менеджеров. Поэтому проверяем явно.
    """
    if not Path("bot.py").exists() or not Path("core").is_dir():
        raise RuntimeError(
            f"Неверная рабочая директория: {Path.cwd()}. "
            "Бот ищет data/ и uploads/ относительно текущего каталога. "
            "Запускайте из папки проекта, а для службы задайте рабочий каталог "
            "явно (WorkingDirectory в systemd, «Рабочая папка» в планировщике Windows)."
        )

    for name in ("data", "backups"):
        directory = Path(name)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise RuntimeError(f"Нет прав на запись в {directory.resolve()}: {exc}") from exc

    snapshot = Path("data/api_exports/products.json")
    if not snapshot.exists() or snapshot.stat().st_size == 0:
        logger.warning(
            "Снимок каталога отсутствует (%s). Цены, остатки и поиск по каталогу "
            "работать не будут, пока не пройдёт синхронизация — нажмите "
            "«Обновить цены и наличие» или выполните /sync.", snapshot,
        )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.warning(
            "ANTHROPIC_API_KEY не задан: свободные технические вопросы "
            "(«чем YCM3 отличается от YCB9?») останутся без ответа. Каталог, "
            "цены и остатки от этого не зависят и работают."
        )


# Сколько ждать заливку файла в Telegram. python-telegram-bot по умолчанию
# даёт на запись 5 секунд, и send_photo/send_document этого не переопределяют.
# Замер 29.08.2026 на прогреве кэша: 215 файлов из 668 отвалились с TimedOut —
# снимки в таблице весят до 6,7 МБ, паспорта CNC до 13, и в пять секунд такая
# заливка не укладывается ни при каком канале. Менеджер видел бы это как «фото
# просто не приходит», молча и каждый раз.
MEDIA_WRITE_TIMEOUT = 300.0
MEDIA_READ_TIMEOUT = 60.0


def build_application(token: str) -> Application:
    """Собрать приложение. Отдельной функцией — чтобы таймауты на заливку
    файлов можно было проверить тестом, не поднимая бота."""
    return (
        Application.builder()
        .token(token)
        .write_timeout(MEDIA_WRITE_TIMEOUT)
        .read_timeout(MEDIA_READ_TIMEOUT)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )


def main() -> None:
    if not TOKEN or not ADMIN_IDS:
        raise RuntimeError("Заполните TELEGRAM_BOT_TOKEN и ADMIN_USER_IDS в .env")
    preflight()
    app = build_application(TOKEN)
    app.bot_data["anthropic"] = AsyncAnthropic() if os.environ.get("ANTHROPIC_API_KEY") else None
    app.bot_data["cnc_api"] = CncApi()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("accessories", accessories_command))
    app.add_handler(CommandHandler("catalog", catalog_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("reindex", reindex))
    app.add_handler(CommandHandler("sync", sync_command))
    app.add_handler(CommandHandler("freshness", freshness_command))
    app.add_handler(CommandHandler("stock", stock_command))
    app.add_handler(CommandHandler("unanswered", unanswered))
    app.add_handler(CommandHandler("resolve_question", resolve_question_command))
    app.add_handler(CommandHandler("review", review_command))
    app.add_handler(CommandHandler("approve", approve_command))
    app.add_handler(CommandHandler("reject", reject_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("documents", documents))
    app.add_handler(CommandHandler("warm_media", warm_media_command))
    app.add_handler(CallbackQueryHandler(refresh_all_callback, pattern=r"^refresh_all$"))
    app.add_handler(CallbackQueryHandler(feedback_callback, pattern=r"^fb:"))
    app.add_handler(CallbackQueryHandler(accessory_callback, pattern=r"^acc:"))
    app.add_handler(CallbackQueryHandler(series_callback, pattern=r"^srs:"))
    app.add_handler(CallbackQueryHandler(documents_callback, pattern=r"^doc:"))
    app.add_handler(CallbackQueryHandler(catalog_callback, pattern=r"^cat:"))
    app.add_handler(CallbackQueryHandler(about_callback, pattern=r"^about$"))
    app.add_handler(CallbackQueryHandler(about_full_callback, pattern=r"^about_full$"))
    app.add_handler(CallbackQueryHandler(how_to_buy_callback, pattern=r"^how_to_buy$"))
    app.add_handler(CallbackQueryHandler(catalog_menu_callback, pattern=r"^catalog_menu$"))
    app.add_handler(CallbackQueryHandler(ask_support_callback, pattern=r"^ask_support$"))
    app.add_handler(CallbackQueryHandler(skip_email_callback, pattern=r"^skip_email$"))
    app.add_handler(CallbackQueryHandler(to_reference_callback, pattern=r"^to_reference:"))
    app.add_handler(CallbackQueryHandler(want_manager_callback, pattern=r"^want_manager$"))
    app.add_handler(CallbackQueryHandler(not_it_callback, pattern=r"^not_it$"))
    app.add_handler(CallbackQueryHandler(wider_callback, pattern=r"^wide:"))
    # Должен идти раньше общего upload_document — иначе прайс-лист попадёт
    # в общий (полнотекстовый) xlsx-конвейер вместо структурированного.
    app.add_handler(MessageHandler(
        filters.Document.ALL & filters.CaptionRegex(r"(?i)^/upload_pricelist"),
        upload_pricelist,
    ))
    # Тоже раньше общего upload_document — иначе таблица паспортов уедет в
    # полнотекстовый xlsx-конвейер вместо структурированного разбора.
    app.add_handler(MessageHandler(
        filters.Document.ALL & filters.CaptionRegex(r"(?i)^/upload_passports"),
        upload_passports,
    ))
    # И эта — раньше общего upload_document, по той же причине.
    app.add_handler(MessageHandler(
        filters.Document.ALL & filters.CaptionRegex(r"(?i)^/upload_media"),
        upload_media,
    ))
    app.add_handler(MessageHandler(filters.Document.ALL, upload_document))
    # Раньше общего текстового хендлера: ответ инженера на уведомление —
    # не вопрос боту, а доставка ответа клиенту.
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & QUESTION_REPLY, engineer_reply_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, answer))
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()