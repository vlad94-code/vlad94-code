"""CNC Electric Knowledge Bot — закрытый RAG-бот для технической поддержки."""
import asyncio
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from openai import AsyncOpenAI, RateLimitError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)
from catalog_parser import extract_catalog_facts, parse_pdf_catalog
from cnc_api import CncApi
from catalog_search import clear_cache as clear_catalog_cache
from core.documents import (
    allocate_slot,
    list_documents,
    register_document,
    register_legacy_document,
    replace_document_facts,
    summary,
)
from core.conflicts import detect_conflicts, open_conflicts, resolve_conflict, warning_for_question
from core.lexicon_overrides import add_override, list_overrides, remove_override
from engines.analytics import (
    log_query,
    open_unanswered,
    record_unanswered,
    resolve_unanswered,
    weekly_report,
)
from engines.router import route_local
from knowledge_matrix import rebuild, search as search_matrix
from api_sync import get_sync_status, sync_in_progress, sync_operational, sync_static

load_dotenv()
logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
ADMINS = {int(value.strip()) for value in os.environ.get("ADMIN_USER_IDS", "").split(",") if value.strip()}
DATA_DIR = Path("data")
UPLOAD_DIR = Path("uploads")
STATE_FILE = DATA_DIR / "state.json"
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx"}

INSTRUCTIONS = """Ты — технический помощник CNC Electric для сотрудников компании.
Отвечай на русском, кратко и профессионально. Отвечай ТОЛЬКО на основе найденных
материалов базы знаний и блока «Актуальные данные CNC Russia API», если он дан.
Не выдумывай характеристики, совместимость, наличие,
цены, артикулы, нормы или схемы. Если в материалах нет уверенного ответа, прямо
напиши: «В базе знаний нет подтверждённого ответа» и укажи, что нужно уточнить.
Если в вопросе спрашивают максимум/минимум параметра серии, используй агрегированный
расчёт API, если он есть; иначе найди соответствующую таблицу серии в каталоге и
сообщи значение. В ответе одной короткой фразой объясни путь: «по каталогу» или
«по API». Для вопросов, связанных с безопасностью, монтажом или выбором защитных аппаратов,
напоминай о необходимости сверки с действующей документацией и квалифицированным
проектировщиком. В конце перечисли использованные файлы в строке «Источники:».
"""


def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id in ADMINS)


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    configured_id = os.environ.get("OPENAI_VECTOR_STORE_ID")
    return {"vector_store_id": configured_id} if configured_id else {}


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


async def vector_store_id(context: ContextTypes.DEFAULT_TYPE) -> str:
    state = load_state()
    if state.get("vector_store_id"):
        return state["vector_store_id"]
    store = await context.application.bot_data["openai"].vector_stores.create(name="CNC Electric knowledge base")
    state["vector_store_id"] = store.id
    save_state(state)
    return store.id


def source_names(response) -> list[str]:
    names: set[str] = set()
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            for annotation in getattr(content, "annotations", []) or []:
                filename = getattr(annotation, "filename", None)
                if filename:
                    names.add(filename)
    return sorted(names)


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить цены и наличие", callback_data="refresh_operational")],
        [InlineKeyboardButton("ℹ️ Актуальность данных", callback_data="sync_status")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("catalog_filters", None)
    admin_hint = "\nВы администратор: пришлите каталог/заметку файлом для загрузки." if is_admin(update) else ""
    await update.message.reply_text(
        "Я отвечаю на технические вопросы по оборудованию CNC Electric по загруженной базе знаний.\n"
        "Просто напишите вопрос. Для цены и остатков укажите точный артикул: «Артикул: ...».\n\n"
        "Цена, остатки и товары в пути обновляются по кнопке ниже. Характеристики и каталог обновляются автоматически каждый день в 06:00 МСК."
        + admin_hint,
        reply_markup=main_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Команды:\n/start — начать\n/help — справка\n/status — статус базы (администратор)\n"
        "/conflicts — расхождения каталогов и API (администратор)\n"
        "/resolve_conflict — подтвердить источник (администратор)\n"
        "/unanswered — вопросы без ответа (администратор)\n"
        "/report — недельный отчёт (администратор)\n"
        "/documents — версии документов (администратор)\n"
        "/synonyms — добавленные синонимы (администратор)\n"
        "/forget — удалить локальную привязку к базе (администратор)\n\n"
        "🔄 Обновить цены и наличие — загрузить свежие цены, остатки и товары в пути.\n"
        "Характеристики и каталог автоматически обновляются ежедневно в 06:00 МСК.\n\n"
        "Администратор может отправить PDF, DOCX, TXT, MD, CSV или XLSX — бот добавит его в базу.",
        reply_markup=main_keyboard(),
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
                # Moscow time is UTC+3 year-round. Using a fixed offset avoids
                # the Windows/Python zoneinfo dependency on the tzdata package.
                moscow_tz = timezone(timedelta(hours=3), name="MSK")
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt = dt.astimezone(moscow_tz)
                value = dt.strftime("%d.%m.%Y %H:%M МСК")
            except (ValueError, TypeError):
                value = timestamp
        else:
            value = "ещё не обновлялись"
        lines.append(f"• {label}: {value}")
    return "\n".join(lines)


async def refresh_operational_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
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
        "Это может занять до нескольких минут, пока CNC API отдаёт три набора данных."
    )
    try:
        changed = await sync_operational()
        if changed:
            pages, records = rebuild()
            logger.info("Operational API sync changed %s files; matrix rebuilt: pages=%s records=%s", len(changed), pages, records)
        await query.edit_message_text(
            "✅ Оперативные данные обновлены.\n\n" + await sync_status_text(),
            reply_markup=main_keyboard(),
        )
    except Exception as exc:
        logger.exception("Operational API synchronisation failed")
        message = str(exc).strip() or "Неизвестная ошибка"
        await query.edit_message_text(
            "❌ Не удалось обновить оперативные данные. Старые сохранённые данные не изменены.\n\n"
            f"Причина: {message}\n\n"
            + await sync_status_text(),
            reply_markup=main_keyboard(),
        )


async def sync_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    await query.edit_message_text(await sync_status_text(), reply_markup=main_keyboard())


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    store_id = load_state().get("vector_store_id", "ещё не создано")
    documents, newest = summary()
    newest_text = newest.replace("T", " ").replace("+00:00", " UTC") if newest else "ещё нет"
    await update.message.reply_text(
        f"Vector Store: {store_id}\nМодель: {MODEL}\nАктивных документов: {documents}\nПоследняя загрузка: {newest_text}"
    )


async def forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    # This does not delete documents in OpenAI; it only prevents this bot from using the store.
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    await update.message.reply_text("Локальная привязка к базе удалена. Документы в OpenAI не удалялись.")


async def reindex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Обновлять матрицу знаний может только администратор.")
        return
    await update.message.reply_text("Обновляю матрицу знаний из каталогов и API-файлов…")
    pages, records = rebuild()
    conflicts = detect_conflicts()
    await update.message.reply_text(
        f"Матрица знаний обновлена: страниц каталогов — {pages}, записей API — {records}.\n"
        f"Открытых расхождений: {conflicts}."
    )


async def conflicts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    rows = open_conflicts()
    if not rows:
        await update.message.reply_text("Открытых расхождений между каталогами и API нет.")
        return
    labels = {
        "nominal_current": "номинальный ток",
        "nominal_voltage": "номинальное напряжение",
        "breaking_capacity": "отключающая способность",
        "pole_count": "количество полюсов",
    }
    lines = ["⚠️ Открытые расхождения (каталог → API):"]
    for row in rows:
        lines.append(
            f"• #{row['id']} {row['entity']}, {labels.get(row['attribute'], row['attribute'])}: "
            f"{row['document_value']} → {row['api_value']}"
        )
    lines.append("\nПодтвердить: /resolve_conflict НОМЕР catalog|api комментарий")
    await update.message.reply_text("\n".join(lines))


async def resolve_conflict_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    arguments = context.args
    if len(arguments) < 2 or arguments[1] not in {"catalog", "api"}:
        await update.message.reply_text(
            "Формат: /resolve_conflict НОМЕР catalog|api комментарий\n"
            "catalog — оставить значение каталога; api — исключить спорный факт каталога."
        )
        return
    try:
        conflict_id = int(arguments[0])
    except ValueError:
        await update.message.reply_text("Номер расхождения должен быть целым числом.")
        return
    decision = arguments[1]
    note = " ".join(arguments[2:])
    if not resolve_conflict(conflict_id, decision, note):
        await update.message.reply_text("Открытое расхождение с таким номером не найдено.")
        return
    label = "каталог" if decision == "catalog" else "API"
    await update.message.reply_text(f"Расхождение #{conflict_id} подтверждено: источником выбран {label}.")


async def unanswered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    rows = open_unanswered()
    if not rows:
        await update.message.reply_text("Открытых вопросов без ответа нет.")
        return
    reasons = {
        "local_no_answer": "нет однозначного ответа в локальном каталоге",
        "rag_no_evidence": "в базе нет подтверждённого ответа",
        "rag_unavailable": "ИИ был недоступен",
        "generation_error": "ошибка подготовки ответа",
    }
    lines = ["📝 Вопросы без подтверждённого ответа:"]
    for row in rows:
        lines.append(
            f"• #{row['id']} ×{row['occurrences']}: {row['question']}\n"
            f"  Причина: {reasons.get(str(row['reason']), str(row['reason']))}"
        )
    lines.append("\nЗакрыть после пополнения базы: /resolve_question НОМЕР комментарий")
    await update.message.reply_text("\n".join(lines)[:4096])


async def resolve_question_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
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


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    data = weekly_report()
    total = data["total"]
    handled = data["handled"]
    rate = round(handled / total * 100) if total else 0
    lines = [
        "📈 Отчёт за последние 7 дней:",
        f"• Запросов: {total}",
        f"• Обработано: {handled} ({rate}%)",
    ]
    engines = data["engines"]
    if engines:
        lines.append("• По движкам: " + ", ".join(f"{name} — {count}" for name, count in engines))
    unanswered_rows = data["unanswered"]
    lines.append(f"• Открытых вопросов без ответа: {len(unanswered_rows)}")
    for row in unanswered_rows[:3]:
        lines.append(f"  #{row['id']} ×{row['occurrences']}: {row['question']}")
    await update.message.reply_text("\n".join(lines)[:4096])


async def documents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
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


async def synonyms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    rows = list_overrides()
    if not rows:
        await update.message.reply_text(
            "Дополнительных синонимов пока нет.\n"
            "Добавить: /add_synonym ФРАЗА | ТИП_ИЗ_API | НАЗВАНИЕ"
        )
        return
    lines = ["🗂 Добавленные синонимы:"]
    for row in rows:
        lines.append(f"• #{row['id']} «{row['phrase']}» → {row['label']} ({row['type_item']})")
    lines.append("\nДобавить: /add_synonym ФРАЗА | ТИП_ИЗ_API | НАЗВАНИЕ")
    lines.append("Удалить: /remove_synonym НОМЕР")
    await update.message.reply_text("\n".join(lines)[:4096])


async def add_synonym(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    parts = [part.strip() for part in " ".join(context.args).split("|")]
    if len(parts) != 3 or not all(parts):
        await update.message.reply_text("Формат: /add_synonym ФРАЗА | ТИП_ИЗ_API | НАЗВАНИЕ")
        return
    try:
        add_override(parts[0], parts[1], parts[2], update.effective_user.id if update.effective_user else None)
    except ValueError:
        await update.message.reply_text("Фраза, тип и название должны быть заполнены.")
        return
    await update.message.reply_text(f"Синоним «{parts[0]}» добавлен.")


async def remove_synonym(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return
    try:
        override_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Формат: /remove_synonym НОМЕР")
        return
    if not remove_override(override_id):
        await update.message.reply_text("Синоним с таким номером не найден.")
        return
    await update.message.reply_text(f"Синоним #{override_id} удалён.")


async def upload_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Загружать материалы может только администратор.")
        return
    document = update.message.document
    filename = document.file_name or "document"
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        await update.message.reply_text("Поддерживаются: PDF, DOCX, TXT, MD, CSV, XLSX.")
        return
    UPLOAD_DIR.mkdir(exist_ok=True)
    slot = allocate_slot(filename)
    local_path = UPLOAD_DIR / slot.stored_name
    await (await document.get_file()).download_to_drive(local_path)
    await update.message.reply_text(f"Загружаю «{filename}» в базу знаний…")
    try:
        client: AsyncOpenAI = context.application.bot_data["openai"]
        upload_path = local_path
        if suffix == ".pdf":
            parsed_path = UPLOAD_DIR / f"{local_path.stem}.parsed.md"
            parse_pdf_catalog(local_path, parsed_path, filename)
            upload_path = parsed_path
        with upload_path.open("rb") as file_handle:
            uploaded = await client.files.create(file=file_handle, purpose="user_data")
        await client.vector_stores.files.create(vector_store_id=await vector_store_id(context), file_id=uploaded.id)
        register_document(
            slot,
            local_path,
            parsed_path=parsed_path if suffix == ".pdf" else None,
            uploaded_by=update.effective_user.id if update.effective_user else None,
            vector_file_id=uploaded.id,
        )
        fact_count = 0
        if suffix == ".pdf":
            fact_count = replace_document_facts(slot.stored_name, extract_catalog_facts(parsed_path))
        conflict_count = detect_conflicts()
        rebuild()
        await update.message.reply_text(
            f"«{filename}» принят (версия {slot.version}). "
            + ("Текст и таблицы каталога преобразованы в поисковый формат. " if suffix == ".pdf" else "")
            + (f"Извлечено подтверждённых характеристик: {fact_count}. " if suffix == ".pdf" else "")
            + (f"Обнаружено расхождений с API: {conflict_count}. " if conflict_count else "")
            + "Индексация может занять несколько минут; затем информация появится в ответах."
        )
    except Exception:
        logger.exception("Document upload failed")
        await update.message.reply_text("Не удалось добавить документ. Проверьте API-ключ и повторите попытку.")


async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    question = (update.message.text or "").strip()
    if not question:
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
            conflict_warning = warning_for_question(question)
            await update.message.reply_text(
                local.text + source_line + ("\n\n" + conflict_warning if conflict_warning else ""),
                reply_markup=main_keyboard(),
            )
            log_query(question, local.engine_name, True, user_id=user_id)
            return

        # Volatile data / legacy exact-article route from local snapshots.
        live_data = context.application.bot_data["cnc_api"].lookup_local(question)
        client: AsyncOpenAI | None = context.application.bot_data.get("openai")
        if client is None:
            await update.message.reply_text(
                "В локальном каталоге нет однозначного ответа. Уточните тип оборудования, серию, ток, полюса или артикул."
            )
            log_query(question, "none", False, user_id=user_id)
            record_unanswered(question, "local_no_answer")
            return

        live_context = f"\n\nАктуальные данные CNC Russia API:\n{live_data}" if live_data else ""
        matrix_rows = search_matrix(question)
        matrix_context = "\n".join(
            f"[{row['kind']}; {row['source']}; стр. {row['page'] or '-'}] {row['text']}"
            for row in matrix_rows
        )
        if matrix_context:
            matrix_context = "\n\nЛокальная матрица знаний (релевантные фрагменты):\n" + matrix_context
        response = await client.responses.create(
            model=MODEL,
            instructions=INSTRUCTIONS,
            input=question + live_context + matrix_context,
            tools=[{"type": "file_search", "vector_store_ids": [await vector_store_id(context)]}],
            store=False,
        )
        text = response.output_text.strip() or "В базе знаний нет подтверждённого ответа."
        sources = source_names(response)
        if sources and "источники:" not in text.lower():
            text += "\n\nИсточники: " + ", ".join(sources)
        conflict_warning = warning_for_question(question)
        if conflict_warning:
            text += "\n\n" + conflict_warning
        await update.message.reply_text(text[:4096])
        log_query(question, "rag", True, user_id=user_id)
        if "в базе знаний нет подтверждённого ответа" in text.lower():
            record_unanswered(question, "rag_no_evidence")
    except RateLimitError:
        await update.message.reply_text(
            "Каталожный поиск работает без OpenAI. Для свободных технических вопросов "
            "ИИ сейчас недоступен из-за лимита API. Уточните артикул, серию или параметры товара."
        )
        log_query(question, "rag_rate_limited", False, user_id=user_id)
        record_unanswered(question, "rag_unavailable")
    except Exception:
        logger.exception("Answer generation failed")
        await update.message.reply_text("Не удалось подготовить ответ. Попробуйте ещё раз или сообщите администратору.")
        record_unanswered(question, "generation_error")


async def daily_full_sync(application: Application) -> None:
    """Refresh stable product/technical API data every day at 06:00 Moscow time."""
    # Moscow is UTC+3 year-round; a fixed offset works on Windows without
    # requiring the external tzdata package.
    moscow_tz = timezone(timedelta(hours=3), name="MSK")
    while True:
        now = datetime.now(moscow_tz)
        target = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            changed = await sync_static(force=True)
            if changed:
                application.bot_data["cnc_api"].clear_product_cache()
                clear_catalog_cache()
                pages, records = rebuild()
                conflicts = detect_conflicts()
                logger.info(
                    "Daily static API sync changed %s files; matrix rebuilt: pages=%s records=%s, conflicts=%s",
                    len(changed), pages, records, conflicts,
                )
            else:
                logger.info("Daily static API sync completed; products snapshot unchanged")
        except Exception:
            logger.exception("Daily static API synchronisation failed; previous data remain in place")


async def post_init(application: Application) -> None:
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
    conflict_count = detect_conflicts()
    if conflict_count:
        logger.warning("Detected %s open catalogue/API conflicts", conflict_count)
    # post_init runs before Application is marked as running. Using
    # Application.create_task here produces PTBUserWarning, so create a normal
    # asyncio task and keep a reference for clean shutdown.
    task = asyncio.create_task(daily_full_sync(application), name="daily-api-full-sync")
    application.bot_data["daily_sync_task"] = task


async def post_shutdown(application: Application) -> None:
    task = application.bot_data.pop("daily_sync_task", None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled Telegram update error", exc_info=context.error)


def main() -> None:
    if not TOKEN or not ADMINS:
        raise RuntimeError("Заполните TELEGRAM_BOT_TOKEN и ADMIN_USER_IDS в .env")
    app = (Application.builder().token(TOKEN).post_init(post_init).post_shutdown(post_shutdown).build())
    app.bot_data["openai"] = AsyncOpenAI() if os.environ.get("OPENAI_API_KEY") else None
    app.bot_data["cnc_api"] = CncApi()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("forget", forget))
    app.add_handler(CommandHandler("reindex", reindex))
    app.add_handler(CommandHandler("conflicts", conflicts))
    app.add_handler(CommandHandler("resolve_conflict", resolve_conflict_command))
    app.add_handler(CommandHandler("unanswered", unanswered))
    app.add_handler(CommandHandler("resolve_question", resolve_question_command))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("documents", documents))
    app.add_handler(CommandHandler("synonyms", synonyms))
    app.add_handler(CommandHandler("add_synonym", add_synonym))
    app.add_handler(CommandHandler("remove_synonym", remove_synonym))
    app.add_handler(CallbackQueryHandler(refresh_operational_callback, pattern=r"^refresh_operational$"))
    app.add_handler(CallbackQueryHandler(sync_status_callback, pattern=r"^sync_status$"))
    app.add_handler(MessageHandler(filters.Document.ALL, upload_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, answer))
    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
