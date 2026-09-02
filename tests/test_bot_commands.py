"""bot.py — /search, /accessories и подсказка "введите серию" в свободном
тексте (см. bot.py::answer(), флаг await_accessories_series).

Update/Context — не настоящие объекты python-telegram-bot, только то
подмножество атрибутов, которое читает сам bot.py; сеть не трогается."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import bot
import client_flow
import core.db
from core.logging_ import get_review, queue_for_review
from core.roles import ADMIN_IDS, ENGINEER_IDS, MANAGER_IDS


@pytest.fixture(autouse=True)
def _no_media(monkeypatch):
    """Ни один тест этого файла не про фотографии.

    Карточка товара теперь начинается с фото (bot.send_product_photo), а оно
    живёт в таблице номенклатуры и качается с чужого хостинга. Без заглушки
    тесты карточки полезли бы в боевую data/knowledge.db и в сеть. Сами фото
    проверяет tests/test_media_in_bot.py.
    """
    monkeypatch.setattr(bot.media_links, "for_article", lambda article: None)


def _make_update(text: str = "") -> SimpleNamespace:
    admin_id = next(iter(ADMIN_IDS))
    message = SimpleNamespace(text=text, reply_text=AsyncMock(), reply_photo=AsyncMock())
    return SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=admin_id),
        effective_chat=SimpleNamespace(send_action=AsyncMock()),
    )


def _make_context(args: list[str] | None = None) -> SimpleNamespace:
    # application.bot.send_message — уведомление админу о новой записи очереди
    # /unanswered: без него documents_callback/answer падали бы на context.
    return SimpleNamespace(
        args=args or [],
        user_data={},
        application=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock())),
    )


def _admin_pushes(context) -> list[str]:
    """Тексты, ушедшие админам через notify_admins()."""
    return [call.kwargs["text"] for call in context.application.bot.send_message.call_args_list]


def test_search_command_without_args_prompts():
    update = _make_update()
    context = _make_context()
    asyncio.run(bot.search_command(update, context))
    text = update.message.reply_text.call_args[0][0]
    assert "/search" in text


def test_search_command_with_article_replies_with_detail():
    update = _make_update()
    context = _make_context(args=["НЕТ-ТАКОГО-АРТИКУЛА-999"])
    asyncio.run(bot.search_command(update, context))
    text = update.message.reply_text.call_args[0][0]
    assert "не найден" in text.lower()


def test_accessories_command_without_args_sets_flag_and_prompts():
    update = _make_update()
    context = _make_context()
    asyncio.run(bot.accessories_command(update, context))
    assert context.user_data.get("await_accessories_series") is True
    text = update.message.reply_text.call_args[0][0]
    assert "/accessories" in text


def test_accessories_command_with_series_does_not_set_flag():
    update = _make_update()
    context = _make_context(args=["YCW3"])
    asyncio.run(bot.accessories_command(update, context))
    assert "await_accessories_series" not in context.user_data
    update.message.reply_text.assert_awaited()


def test_awaiting_flag_intercepts_next_free_text_message():
    """После /accessories без аргумента следующее свободное сообщение
    должно уйти в подбор аксессуаров, а не в обычный ProductEngine (голая
    серия сама по себе не распознаётся как запрос аксессуаров)."""
    update = _make_update(text="YCW3")
    context = _make_context()
    context.user_data["await_accessories_series"] = True
    asyncio.run(bot.answer(update, context))
    assert "await_accessories_series" not in context.user_data  # one-shot, потреблён
    update.message.reply_text.assert_awaited()
    # Не должен был дойти до route_local()/send_action (typing indicator) —
    # перехват происходит раньше, в самом начале answer().
    update.effective_chat.send_action.assert_not_called()


@pytest.fixture
def isolated_queue(tmp_path, monkeypatch):
    """Своя data/bot.db + заглушки на пересборку индекса: команды проверяем,
    боевую базу знаний не трогаем."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    core.db.init_db()
    rebuilt = {"answers": 0, "matrix": 0}

    def fake_rebuild_answers():
        rebuilt["answers"] += 1
        return 1

    def fake_rebuild_matrix():
        rebuilt["matrix"] += 1
        return (0, 0)

    monkeypatch.setattr(bot, "rebuild_approved_answers_document", fake_rebuild_answers)
    monkeypatch.setattr(bot, "rebuild", fake_rebuild_matrix)
    return rebuilt


def test_review_command_empty_queue(isolated_queue):
    update = _make_update()
    asyncio.run(bot.review_command(update, _make_context()))
    assert "нет" in update.message.reply_text.call_args[0][0].lower()


def test_review_command_lists_pending(isolated_queue):
    queue_for_review("чем YCM3 отличается от YCB9?", "Ответ ИИ про серии.", [], category="контакторы")
    update = _make_update()
    asyncio.run(bot.review_command(update, _make_context()))
    text = update.message.reply_text.call_args[0][0]
    assert "чем YCM3 отличается от YCB9?" in text
    assert "контакторы" in text
    assert "/approve" in text


def test_approve_command_marks_approved_and_rebuilds(isolated_queue):
    review_id = queue_for_review("вопрос?", "ответ", [])
    update = _make_update()
    asyncio.run(bot.approve_command(update, _make_context(args=[str(review_id), "проверено"])))
    assert get_review(review_id)["status"] == "approved"
    assert get_review(review_id)["note"] == "проверено"
    # База знаний пересобирается только после одобрения, не раньше.
    assert isolated_queue["answers"] == 1
    assert isolated_queue["matrix"] == 1


def test_reject_command_does_not_rebuild_knowledge_base(isolated_queue):
    review_id = queue_for_review("вопрос?", "ответ", [])
    update = _make_update()
    asyncio.run(bot.reject_command(update, _make_context(args=[str(review_id), "неточно"])))
    assert get_review(review_id)["status"] == "rejected"
    assert isolated_queue["answers"] == 0
    assert isolated_queue["matrix"] == 0


def test_approve_command_unknown_id_reports_clearly(isolated_queue):
    update = _make_update()
    asyncio.run(bot.approve_command(update, _make_context(args=["99999"])))
    text = update.message.reply_text.call_args[0][0]
    assert "не найден" in text.lower()
    assert isolated_queue["answers"] == 0


def test_approve_command_without_args_shows_format(isolated_queue):
    update = _make_update()
    asyncio.run(bot.approve_command(update, _make_context()))
    assert "/approve" in update.message.reply_text.call_args[0][0]


def test_approve_command_rejects_non_numeric_id(isolated_queue):
    update = _make_update()
    asyncio.run(bot.approve_command(update, _make_context(args=["abc"])))
    assert "целым числом" in update.message.reply_text.call_args[0][0]


def test_approve_twice_is_refused(isolated_queue):
    review_id = queue_for_review("вопрос?", "ответ", [])
    asyncio.run(bot.approve_command(_make_update(), _make_context(args=[str(review_id)])))
    update = _make_update()
    asyncio.run(bot.approve_command(update, _make_context(args=[str(review_id)])))
    assert "не найден" in update.message.reply_text.call_args[0][0].lower()
    assert isolated_queue["answers"] == 1  # второй раз пересборки не было


# --- Лимит длины сообщения Telegram ------------------------------------------

def test_short_text_passes_through_untouched():
    assert bot.clip_for_telegram("короткий ответ") == "короткий ответ"


def test_text_at_the_limit_is_not_touched():
    text = "x" * bot.TELEGRAM_MAX_MESSAGE
    assert bot.clip_for_telegram(text) == text


def test_over_long_text_is_clipped_under_the_limit():
    """Ответ на «CJX2-F» — 9383 символа; Telegram отвергал его
    через BadRequest, и пользователь видел «Не удалось подготовить ответ»."""
    text = "\n".join("• строка %d" % i for i in range(2000))
    out = bot.clip_for_telegram(text)
    assert len(out) <= bot.TELEGRAM_MAX_MESSAGE


def test_clipped_text_says_it_was_clipped():
    out = bot.clip_for_telegram("я" * 9383)
    assert "сокращён" in out


def test_clipping_cuts_on_a_line_boundary():
    """Список товаров не должен обрываться посреди артикула."""
    line = "• C0200134 — Контактор CJX2-FN 150A 4P AC380В"
    text = "\n".join([line] * 300)
    out = bot.clip_for_telegram(text)
    body = out.split("\n…")[0]
    assert all(l == line for l in body.splitlines() if l.strip())


NL = chr(10)


def test_short_text_is_a_single_chunk():
    assert bot.split_for_telegram("коротко") == ["коротко"]


def test_long_listing_is_split_not_truncated():
    """Группа «CJX2-D / Тепловое реле» — 77 позиций, 4236 символов
    при лимите 4096. Обрезать её нельзя: спрошены все аксессуары."""
    lines = ["• C0300%02d · Тепловое реле JR28-36 30-40A · 1597,34 р. · 20 шт." % i for i in range(90)]
    text = NL.join(lines)
    chunks = bot.split_for_telegram(text)
    assert len(chunks) > 1
    assert all(len(c) <= bot.TELEGRAM_MAX_MESSAGE for c in chunks)
    assert NL.join(chunks).splitlines() == lines


def test_splitting_never_breaks_a_line():
    lines = ["• C%05d · товар" % i for i in range(500)]
    for chunk in bot.split_for_telegram(NL.join(lines)):
        for line in chunk.splitlines():
            assert line in lines


def test_a_single_unbreakable_line_is_still_capped():
    """Строка длиннее лимита резать негде — тогда обрезаем."""
    chunks = bot.split_for_telegram("я" * 9000)
    assert all(len(c) <= bot.TELEGRAM_MAX_MESSAGE for c in chunks)


# --- Этап 3: заявки на документы -------------------------------------------
# Ссылок на паспорта/3D/сертификаты ещё нет ни в API (pictures и
# certificates пусты у всех 11942 товаров), ни в прайсе. Пока файл
# по docs/MEDIA_LINKS.md не пришёл, кнопка регистрирует заявку инженеру.

def test_documents_menu_offers_all_three(monkeypatch):
    markup = bot.documents_keyboard("B030001")
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert data == ["doc:B030001:pass", "doc:B030001:3d", "doc:B030001:cert"]


def test_documents_callback_data_fits_telegram_limit():
    """Самый длинный артикул в каталоге — 9 символов."""
    markup = bot.documents_keyboard("E01030501")
    for row in markup.inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode()) <= 64


def test_document_request_is_recorded_for_the_engineer(isolated_queue):
    from core.logging_ import open_unanswered
    bot.record_document_request("B030001", "pass", user_id=1)
    rows = open_unanswered()
    assert len(rows) == 1
    assert "B030001" in rows[0]["question"]
    assert "паспорт" in rows[0]["question"].lower()
    assert rows[0]["reason"] == "document_request"


def test_each_document_kind_is_named_in_russian(isolated_queue):
    from core.logging_ import open_unanswered
    for kind in ("pass", "3d", "cert"):
        bot.record_document_request("B030001", kind, user_id=1)
    questions = [r["question"] for r in open_unanswered()]
    assert any("паспорт" in q.lower() for q in questions)
    assert any("3d" in q.lower() for q in questions)
    assert any("сертификат" in q.lower() for q in questions)


def test_unknown_document_kind_is_refused(isolated_queue):
    from core.logging_ import open_unanswered
    assert bot.record_document_request("B030001", "мусор", user_id=1) is None
    assert open_unanswered() == []


def test_unanswered_explains_the_document_reason():
    """Инженер видит в /unanswered понятную причину, а не код."""
    assert "document_request" in bot.UNANSWERED_REASONS


# --- Меню под роль -----------------------------------------------------------
# Раньше список команд был один на всех: меню обещало /reindex и /stats
# каждому, а сама команда потом отказывала. Теперь Telegram получает свой
# список на каждую роль (setMyCommands со scope конкретного чата).

def _names(role):
    return [command.command for command in bot.commands_for(role)]


# ID руководителей заведены в tests/conftest.py: у 900005 подпись своя,
# у 900006 её нет.
FOUNDER_ID = 900005
CTO_ID = 900006


def test_everyone_known_gets_the_working_basics():
    for role in (bot.Role.ADMIN, bot.Role.DIRECTOR, bot.Role.ENGINEER, bot.Role.MANAGER):
        names = _names(role)
        for command in ("start", "search", "accessories", "freshness", "help", "whoami"):
            assert command in names, (role, command)


def _help_text_for(user_id: int) -> str:
    update = _make_update()
    update.effective_user = SimpleNamespace(id=user_id)
    asyncio.run(bot.help_command(update, _make_context()))
    return update.message.reply_text.call_args[0][0]


def test_help_does_not_offer_a_manager_the_command_he_cannot_run():
    """Справка собирается из COMMAND_ROLES, но приписка внизу писалась
    руками — она и разошлась с правами первой."""
    assert "/sync" not in _help_text_for(next(iter(MANAGER_IDS)))


def test_help_still_points_an_engineer_at_sync():
    assert "/sync" in _help_text_for(next(iter(ENGINEER_IDS)))


def _start_text_for(user_id: int) -> str:
    update = _make_update()
    update.effective_user = SimpleNamespace(id=user_id)
    update.effective_chat = SimpleNamespace(id=1, send_action=AsyncMock())
    context = _make_context()
    context.bot = SimpleNamespace(set_my_commands=AsyncMock())
    asyncio.run(bot.start(update, context))
    return update.message.reply_text.call_args[0][0]


def test_start_greets_every_role_by_name():
    """Человек должен сразу видеть, кем его знает бот: перепутанная роль —
    это молчащие команды и непонятно почему пустое меню."""
    assert "Администратор" in _start_text_for(next(iter(ADMIN_IDS)))
    assert "Инженер" in _start_text_for(next(iter(ENGINEER_IDS)))
    assert "Менеджер" in _start_text_for(next(iter(MANAGER_IDS)))


def test_start_shows_a_real_article_as_the_example():
    assert "B030524" in _start_text_for(next(iter(MANAGER_IDS)))


def test_start_does_not_offer_a_manager_what_he_cannot_run():
    text = _start_text_for(next(iter(MANAGER_IDS)))
    assert "/sync" not in text
    assert "/unanswered" not in text


def test_start_points_the_engineer_at_his_queues():
    text = _start_text_for(next(iter(ENGINEER_IDS)))
    assert "/unanswered" in text or "/review" in text


def test_bot_description_fits_what_telegram_accepts():
    """setMyDescription — 512 символов, короткое описание — 120. Текст без
    разметки: заголовок «Что умеет этот бот?» рисует сам Telegram."""
    assert 0 < len(bot.BOT_DESCRIPTION) <= 512
    assert 0 < len(bot.BOT_SHORT_DESCRIPTION) <= 120
    assert "*" not in bot.BOT_DESCRIPTION and "<b>" not in bot.BOT_DESCRIPTION


def test_start_does_not_promise_that_the_button_pulls_the_catalogue():
    """Кнопка тянет цены, остатки и транзит. Обещание «товары» осталось от
    прежнего поведения и вводило в заблуждение."""
    update = _make_update()
    update.effective_chat = SimpleNamespace(id=1, send_action=AsyncMock())
    context = _make_context()
    context.bot = SimpleNamespace(set_my_commands=AsyncMock())
    asyncio.run(bot.start(update, context))
    text = update.message.reply_text.call_args[0][0]
    assert "товары, цены, остатки" not in text


def test_manager_cannot_pull_the_catalogue():
    """Менеджеру нужны цены и остатки, а это кнопка. /sync тянет каталог на
    27,6 МБ и обновляет базу сразу всем — случайное нажатие в рабочий день
    стоит минуты ожидания каждому, кто в это время спросит цену."""
    assert "sync" not in _names(bot.Role.MANAGER)
    assert "sync" in _names(bot.Role.ENGINEER)
    assert "sync" in _names(bot.Role.ADMIN)


def test_manager_has_no_engineer_commands():
    """Менеджер — как инженер, но без разбора очередей."""
    names = _names(bot.Role.MANAGER)
    for command in ("unanswered", "review", "reindex", "documents", "stats"):
        assert command not in names, command


def test_engineer_has_queues_but_not_the_admin_tools():
    names = _names(bot.Role.ENGINEER)
    assert "unanswered" in names and "review" in names
    for command in ("reindex", "documents", "stats"):
        assert command not in names, command


def test_director_sees_what_the_bot_is_asked_and_how_it_answers():
    """Руководителю бот нужен как надзор: что спрашивают, что осталось без
    ответа, свежа ли база."""
    names = _names(bot.Role.DIRECTOR)
    for command in ("stats", "status", "unanswered", "stock"):
        assert command in names, command


def test_director_does_not_get_the_service_tools():
    """Надзор — это чтение. Обновление базы, модерация ответов ИИ и загрузка
    документов остаются у инженера и админа."""
    names = _names(bot.Role.DIRECTOR)
    for command in ("sync", "review", "reindex", "documents"):
        assert command not in names, command


def test_director_title_comes_from_env_not_from_the_role():
    """Основатель и техдиректор — одна роль, но подписи разные."""
    assert bot.role_title(bot.Role.DIRECTOR, FOUNDER_ID) == "Руководитель CNC Electric"


def test_director_without_a_personal_title_is_just_a_director():
    assert bot.role_title(bot.Role.DIRECTOR, CTO_ID) == "Руководитель"


def test_start_greets_the_founder_by_his_own_title():
    assert "Руководитель CNC Electric" in _start_text_for(FOUNDER_ID)


def test_start_points_the_director_at_the_numbers_not_at_the_queues():
    text = _start_text_for(FOUNDER_ID)
    assert "/stats" in text
    assert "/sync" not in text and "/review" not in text


def test_admin_has_everything():
    admin = set(_names(bot.Role.ADMIN))
    assert admin >= set(_names(bot.Role.ENGINEER))
    assert {"reindex", "documents", "stats"} <= admin


def test_unknown_is_offered_nothing_but_the_door():
    """Бот пилотный и закрытый: посторонний получает отказ со своим ID."""
    assert _names(bot.Role.UNKNOWN) == []


def test_menu_never_advertises_a_command_the_role_cannot_run():
    """Меню и права должны сходиться, иначе бот обещает и отказывает."""
    for role in (bot.Role.ADMIN, bot.Role.DIRECTOR, bot.Role.ENGINEER, bot.Role.MANAGER):
        for command in bot.commands_for(role):
            allowed = bot.COMMAND_ROLES.get(command.command)
            assert allowed is None or role in allowed, (role, command.command)


# --- Одна кнопка на все четыре источника --------------------------------------

def test_refresh_covers_every_api_source():
    """«Обновить» тянет и статику, и оперативные данные: /sync обновлял только
    products.json, а кнопка — три остальных, и человеку приходилось делать
    оба действия, чтобы получить актуальную базу."""
    import api_sync

    assert bot.SYNC_SOURCES == api_sync.STATIC_SOURCES | api_sync.OPERATIONAL_SOURCES
    assert len(bot.SYNC_SOURCES) == 4


def test_answer_keyboard_is_only_feedback_now():
    """Кнопки обновления ушли из-под каждого ответа в меню."""
    markup = bot.answer_keyboard(42)
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert data == ["fb:42:1", "fb:42:-1"]


def test_answer_keyboard_still_carries_a_product_offer():
    offer = bot.InlineKeyboardMarkup([[bot.InlineKeyboardButton("x", callback_data="doc:B1:?")]])
    markup = bot.answer_keyboard(42, offer)
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert data == ["doc:B1:?", "fb:42:1", "fb:42:-1"]


# --- Паспорт по кнопке «📄 Документы» ----------------------------------------
# Ссылки берутся из загруженной таблицы «Серия → паспорт» (passport_links.py).
# Ветка «ссылки нет» обязана сохранить прежнее поведение — заявку инженеру,
# иначе потребность в недостающем паспорте перестанет попадать в /unanswered.

def _make_callback_update(data: str) -> SimpleNamespace:
    admin_id = next(iter(ADMIN_IDS))
    message = SimpleNamespace(
        reply_text=AsyncMock(),
        reply_document=AsyncMock(return_value=SimpleNamespace(document=None)),
        reply_chat_action=AsyncMock(),
    )
    query = SimpleNamespace(data=data, answer=AsyncMock(), message=message)
    return SimpleNamespace(
        callback_query=query,
        message=message,
        effective_user=SimpleNamespace(id=admin_id),
    )


_LINK = "https://cncrussia.com/uploads/passports/ycw3_pasport.pdf"


def test_passport_button_sends_the_document(isolated_queue, monkeypatch):
    from core.logging_ import open_unanswered
    from passport_links import PassportLink

    monkeypatch.setattr(bot, "passport_for_article", lambda article: PassportLink("YCW3", _LINK, "ок"))
    monkeypatch.setattr(bot.passport_links, "cached_file_id", lambda url: None)
    monkeypatch.setattr(bot, "fetch_passport", AsyncMock(return_value=(b"%PDF-1.4", "ycw3_pasport.pdf")))
    update = _make_callback_update("doc:B030001:pass")

    asyncio.run(bot.documents_callback(update, _make_context()))

    call = update.callback_query.message.reply_document.call_args
    assert call.kwargs["filename"] == "ycw3_pasport.pdf"
    assert "YCW3" in call.kwargs["caption"]
    assert open_unanswered() == []


def test_passport_button_falls_back_to_the_link_when_the_download_fails(isolated_queue, monkeypatch):
    from passport_links import PassportLink

    monkeypatch.setattr(bot, "passport_for_article", lambda article: PassportLink("YCW3", _LINK, "ок"))
    monkeypatch.setattr(bot.passport_links, "cached_file_id", lambda url: None)
    monkeypatch.setattr(bot, "fetch_passport", AsyncMock(return_value=None))
    update = _make_callback_update("doc:B030001:pass")

    asyncio.run(bot.documents_callback(update, _make_context()))

    update.callback_query.message.reply_document.assert_not_called()
    assert _LINK in update.callback_query.message.reply_text.call_args[0][0]


def test_passport_button_without_a_link_still_asks_the_engineer(isolated_queue, monkeypatch):
    from core.logging_ import open_unanswered

    monkeypatch.setattr(bot, "passport_for_article", lambda article: None)
    update = _make_callback_update("doc:B030001:pass")

    asyncio.run(bot.documents_callback(update, _make_context()))

    update.callback_query.message.reply_document.assert_not_called()
    rows = open_unanswered()
    assert len(rows) == 1
    assert rows[0]["reason"] == "document_request"


def test_3d_and_a_certificate_outside_the_table_are_requests_only(isolated_queue, monkeypatch):
    """3D-модели нет ни в одном источнике, а сертификат есть не у каждого
    артикула — в обоих случаях кнопка копит заявку инженеру. Сертификат из
    таблицы проверяет tests/test_media_in_bot.py."""
    from core.logging_ import open_unanswered

    monkeypatch.setattr(bot, "fetch_passport", AsyncMock(side_effect=AssertionError("не должно скачиваться")))
    monkeypatch.setattr(bot.media_links, "for_article", lambda article: None)
    for kind in ("3d", "cert"):
        asyncio.run(bot.documents_callback(_make_callback_update(f"doc:B030001:{kind}"), _make_context()))
    assert len(open_unanswered()) == 2


def test_passport_filename_is_taken_from_the_url():
    assert bot.passport_filename(_LINK) == "ycw3_pasport.pdf"
    assert bot.passport_filename("https://cncrussia.com/uploads/passports/") == "passport.pdf"


def test_upload_passports_refuses_a_non_xlsx_file():
    update = _make_update()
    update.message.document = SimpleNamespace(file_name="паспорта.pdf")
    asyncio.run(bot.upload_passports(update, _make_context()))
    assert "xlsx" in update.message.reply_text.call_args[0][0].lower()


def test_passport_download_gives_up_on_a_deadline(monkeypatch):
    """timeout httpx считается на операцию: сайт, отдающий файл по байту,
    держал бы обработчик бесконечно. Бюджет на всю загрузку — общий."""
    async def never_ends(url):
        await asyncio.sleep(5)
        return b"", "late.pdf"

    monkeypatch.setattr(bot, "_download_passport", never_ends)
    monkeypatch.setattr(bot, "PASSPORT_TOTAL_TIMEOUT", 0.05)
    assert asyncio.run(bot.fetch_passport("https://cncrussia.com/uploads/passports/slow.pdf")) is None


# --- Кэш file_id -------------------------------------------------------------
# Паспорта весят 5–13 МБ, сайт CNC отдаёт их медленно; второй раз тот же файл
# качать нельзя — Telegram пересылает уже загруженный документ по file_id.

def _sent_message(file_id: str = "BQACAgIAAxkBAAI"):
    return SimpleNamespace(document=SimpleNamespace(file_id=file_id))


def test_a_cached_passport_is_resent_without_downloading(monkeypatch):
    from passport_links import PassportLink

    monkeypatch.setattr(bot.passport_links, "cached_file_id", lambda url: "BQACAgIAAxkBAAI")
    monkeypatch.setattr(bot, "fetch_passport", AsyncMock(side_effect=AssertionError("качать не нужно")))
    message = SimpleNamespace(
        reply_text=AsyncMock(), reply_document=AsyncMock(return_value=_sent_message()),
        reply_chat_action=AsyncMock(),
    )

    asyncio.run(bot.send_passport(message, "B030001", PassportLink("YCW3", _LINK, "ок")))

    assert message.reply_document.call_args.kwargs["document"] == "BQACAgIAAxkBAAI"


def test_the_first_send_remembers_the_file_id(monkeypatch):
    from passport_links import PassportLink

    remembered = {}
    monkeypatch.setattr(bot.passport_links, "cached_file_id", lambda url: None)
    monkeypatch.setattr(bot.passport_links, "remember_file_id", lambda url, file_id: remembered.update({url: file_id}))
    monkeypatch.setattr(bot, "fetch_passport", AsyncMock(return_value=(b"%PDF-1.4", "ycw3.pdf")))
    message = SimpleNamespace(
        reply_text=AsyncMock(), reply_document=AsyncMock(return_value=_sent_message("НОВЫЙ")),
        reply_chat_action=AsyncMock(),
    )

    asyncio.run(bot.send_passport(message, "B030001", PassportLink("YCW3", _LINK, "ок")))

    assert remembered == {_LINK: "НОВЫЙ"}


def test_a_file_id_telegram_no_longer_accepts_is_forgotten_and_refetched(monkeypatch):
    """Сменился токен бота — чужие file_id Telegram не примет. Менеджер об
    этом знать не должен: файл просто скачивается заново."""
    from telegram.error import BadRequest
    from passport_links import PassportLink

    forgotten = []
    monkeypatch.setattr(bot.passport_links, "cached_file_id", lambda url: "ЧУЖОЙ")
    monkeypatch.setattr(bot.passport_links, "forget_file_id", forgotten.append)
    monkeypatch.setattr(bot.passport_links, "remember_file_id", lambda url, file_id: None)
    monkeypatch.setattr(bot, "fetch_passport", AsyncMock(return_value=(b"%PDF-1.4", "ycw3.pdf")))
    message = SimpleNamespace(
        reply_text=AsyncMock(),
        reply_document=AsyncMock(side_effect=[BadRequest("wrong file identifier"), _sent_message()]),
        reply_chat_action=AsyncMock(),
    )

    asyncio.run(bot.send_passport(message, "B030001", PassportLink("YCW3", _LINK, "ок")))

    assert forgotten == [_LINK]
    assert message.reply_document.call_count == 2
    assert message.reply_document.call_args.kwargs["filename"] == "ycw3.pdf"

# --- Кнопка «Обновить» не тянет каталог --------------------------------------
# products.json — 27,6 МБ, и API CNC отдаёт его без сжатия, без пагинации и без
# фильтра по дате (проверено 28.08.2026: Range, ?limit, ?modified_since и gzip
# игнорируются, HEAD отвечает 405). Ночью 28.08 он качался 14 мин 46 с и кнопка
# выглядела зависшей. Каталог обновляет ночная задача в 06:00 МСК, кнопке
# остаются цены, остатки и транзит — 0,9 МБ.

def _fake_application():
    return SimpleNamespace(bot_data={"cnc_api": SimpleNamespace(clear_product_cache=lambda: None)})


def test_the_refresh_button_does_not_pull_the_catalogue(monkeypatch):
    called = []

    async def fake_static(**kwargs):
        called.append("static")
        return []

    async def fake_operational(**kwargs):
        called.append("operational")
        return []

    monkeypatch.setattr(bot, "sync_static", fake_static)
    monkeypatch.setattr(bot, "sync_operational", fake_operational)

    asyncio.run(bot.refresh_operational(_fake_application()))

    assert called == ["operational"]


def test_sync_command_still_refreshes_the_catalogue(monkeypatch):
    """Ручная команда инженера — единственный способ дёрнуть каталог днём."""
    called = []

    async def fake_static(**kwargs):
        called.append("static")
        return []

    async def fake_operational(**kwargs):
        called.append("operational")
        return []

    monkeypatch.setattr(bot, "sync_static", fake_static)
    monkeypatch.setattr(bot, "sync_operational", fake_operational)

    asyncio.run(bot.refresh_everything(_fake_application()))

    assert called == ["static", "operational"]

# --- Клиент: подписчик канала, а не сотрудник --------------------------------
# Ему доступны три вещи: артикулы, аксессуары, документы. Цену он видит, склад
# и приходы — нет. Свободные вопросы не для него: на них отвечает менеджер.

CLIENT_ID = 555


@pytest.fixture
def subscriber(monkeypatch):
    """Контекст, в котором Telegram отвечает «подписан», и чистый кэш подписок."""
    import core.roles

    core.roles.forget_memberships()
    yield
    core.roles.forget_memberships()


def _client_update() -> SimpleNamespace:
    update = _make_update()
    update.effective_user = SimpleNamespace(id=CLIENT_ID)
    return update


def _client_context(args: list[str] | None = None) -> SimpleNamespace:
    context = _make_context(args)
    context.bot = SimpleNamespace(
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member", is_member=True)),
        set_my_commands=AsyncMock(),
    )
    return context


def test_client_menu_is_articles_accessories_and_documents(subscriber):
    assert _names(bot.Role.CLIENT) == ["start", "search", "accessories", "catalog", "help", "whoami"]


def test_client_has_no_refresh_button(subscriber):
    """Кнопка обновляет данные всем сразу — это работа сотрудника.

    У клиента на её месте свой первый экран, и «Обновить» в нём быть не должно."""
    markup = bot.main_keyboard(bot.Role.CLIENT)
    data = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "refresh_all" not in data


def test_staff_still_have_the_refresh_button(subscriber):
    data = [b.callback_data for row in bot.main_keyboard(bot.Role.MANAGER).inline_keyboard for b in row]
    assert data == ["refresh_all"]


def test_search_hides_the_warehouse_from_a_client(subscriber, monkeypatch):
    seen = {}

    def fake_detail(article, show_stock=True):
        seen["show_stock"] = show_stock
        return "карточка"

    monkeypatch.setattr(bot, "catalog_detail", fake_detail)
    asyncio.run(bot.search_command(_client_update(), _client_context(["B030524"])))
    assert seen["show_stock"] is False


def test_search_still_shows_the_warehouse_to_staff(subscriber, monkeypatch):
    seen = {}

    def fake_detail(article, show_stock=True):
        seen["show_stock"] = show_stock
        return "карточка"

    monkeypatch.setattr(bot, "catalog_detail", fake_detail)
    asyncio.run(bot.search_command(_make_update(), _client_context(["B030524"])))
    assert seen["show_stock"] is True


def test_a_client_gets_the_card_for_the_article_the_bot_asked_for(subscriber, monkeypatch):
    """Бот сам просит «пришлите точный артикул» — и обязан на него ответить.
    Клиент присылал артикул и получал ту же просьбу в ответ, по кругу."""
    seen = {}

    def fake_detail(code, show_stock=True):
        seen["code"], seen["show_stock"] = code, show_stock
        return f"Артикул: {code}\nТарифная цена: 5621,00 р."

    monkeypatch.setattr(client_flow, "catalog_detail", fake_detail)
    update = _client_update()
    update.message.text = "B000001"
    asyncio.run(bot.answer(update, _client_context()))
    assert seen["code"] == "B000001"
    assert "5621" in update.message.reply_text.call_args[0][0]


def test_a_client_sees_the_card_without_the_warehouse(subscriber, monkeypatch):
    """Остаток — не клиентское поле (ARCHITECTURE.md §3), как и в /search."""
    seen = {}

    def fake_detail(code, show_stock=True):
        seen["show_stock"] = show_stock
        return f"Артикул: {code}"

    monkeypatch.setattr(client_flow, "catalog_detail", fake_detail)
    update = _client_update()
    update.message.text = "B000001"
    asyncio.run(bot.answer(update, _client_context()))
    assert seen["show_stock"] is False


def test_an_unknown_article_still_offers_a_way_out(subscriber, monkeypatch):
    """Артикула нет — но это не тупик: под ответом остаётся кнопка к человеку.

    Раньше выход был в самом тексте («поможет менеджер»). Теперь текст
    приходит с лестницы client_flow, а продолжение живёт в клавиатуре.
    """
    monkeypatch.setattr(client_flow, "catalog_detail",
                        lambda code, show_stock=True: "Товар с таким артикулом не найден.")
    update = _client_update()
    update.message.text = "B999999"
    asyncio.run(bot.answer(update, _client_context()))
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    data = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "not_it" in data


def test_free_text_from_a_client_never_reaches_the_ai(subscriber):
    """Свободный вопрос клиента идёт по локальной лестнице client_flow и
    останавливается до Claude: неподтверждённые ответы ИИ клиенту не отдаются.

    Раньше здесь запрещались и локальные движки — просьба «пришлите артикул»
    и была единственным ответом. Теперь движки клиенту открыты, закрыт ИИ:
    context.application выставлен в None, и обращение к нему уронило бы тест.
    """
    context = _client_context()
    context.application = None
    update = _client_update()
    update.message.text = "чем YCM3 отличается от YCB9?"
    asyncio.run(bot.answer(update, context))
    assert update.message.reply_text.call_args[0][0]


def test_a_wrong_series_does_not_send_a_client_to_an_admin_command(subscriber):
    """/upload_pricelist доступен только админу: предлагать его клиенту —
    то же самое расхождение «меню обещает, команда отказывает»."""
    update = _client_update()
    asyncio.run(bot.accessories_command(update, _client_context(["YCW33"])))
    text = update.message.reply_text.call_args[0][0]
    assert "не найдены" in text
    assert "/upload_pricelist" not in text


def test_a_wrong_series_still_points_the_admin_at_the_pricelist():
    update = _make_update()
    asyncio.run(bot.accessories_command(update, _make_context(args=["YCW33"])))
    assert "/upload_pricelist" in update.message.reply_text.call_args[0][0]


def test_a_client_can_ask_for_a_passport(subscriber, monkeypatch):
    from passport_links import PassportLink

    monkeypatch.setattr(bot, "passport_for_article", lambda article: PassportLink("YCW3", _LINK, "ок"))
    monkeypatch.setattr(bot.passport_links, "cached_file_id", lambda url: None)
    monkeypatch.setattr(bot, "fetch_passport", AsyncMock(return_value=(b"%PDF-1.4", "ycw3.pdf")))
    update = _make_callback_update("doc:B030001:pass")
    update.effective_user = SimpleNamespace(id=CLIENT_ID)
    asyncio.run(bot.documents_callback(update, _client_context()))
    update.callback_query.message.reply_document.assert_awaited()


def test_a_client_cannot_refresh_the_data_for_everyone(subscriber):
    update = _make_callback_update("refresh_all")
    update.effective_user = SimpleNamespace(id=CLIENT_ID)
    update.callback_query.edit_message_text = AsyncMock()
    asyncio.run(bot.refresh_all_callback(update, _client_context()))
    update.callback_query.edit_message_text.assert_not_called()


def test_client_greeting_points_at_the_article_and_the_manager(subscriber):
    update = _client_update()
    update.effective_chat = SimpleNamespace(id=1, send_action=AsyncMock())
    asyncio.run(bot.start(update, _client_context()))
    text = update.message.reply_text.call_args[0][0]
    assert "Рад приветствовать" in text
    assert "B030524" in text
    assert "/stock" not in text and "/sync" not in text


def test_a_client_who_was_asked_for_a_series_still_gets_accessories(subscriber, monkeypatch):
    """/accessories без аргумента спрашивает серию следующим сообщением. Для
    клиента это сообщение — не «свободный вопрос», а ответ на вопрос бота."""
    replies = []
    monkeypatch.setattr(bot, "_reply_accessories", AsyncMock(side_effect=lambda u, s, role: replies.append(s)))
    update = _client_update()
    update.message.text = "YCW3"
    context = _client_context()
    context.user_data["await_accessories_series"] = True

    asyncio.run(bot.answer(update, context))

    assert replies == ["YCW3"]


def test_a_client_sees_his_role_in_russian(subscriber):
    update = _client_update()
    asyncio.run(bot.whoami(update, _client_context()))
    text = update.message.reply_text.call_args[0][0]
    assert "client" not in text.lower()


# --- Куда уходит заявка ------------------------------------------------------
# Бот отвечал «передан инженеру», не отправляя ничего и никому: заявка молча
# ложилась в unanswered, а ENGINEER_USER_IDS её не видели, пока сами не наберут
# /unanswered. Теперь ответ называет номер, а разбор идёт админу.

def test_document_request_reply_gives_the_registration_number(isolated_queue, monkeypatch):
    from core.logging_ import open_unanswered

    monkeypatch.setattr(bot, "passport_for_article", lambda article: None)
    update = _make_callback_update("doc:B030001:pass")
    context = _make_context()

    asyncio.run(bot.documents_callback(update, context))

    text = update.callback_query.message.reply_text.call_args[0][0]
    assert "инженер" not in text.lower()
    assert "зарегистрирован, номер #" in text
    assert f"#{open_unanswered()[0]['id']}" in text


def test_document_request_is_pushed_to_the_admin(isolated_queue, monkeypatch):
    from core.logging_ import open_unanswered

    monkeypatch.setattr(bot, "passport_for_article", lambda article: None)
    update = _make_callback_update("doc:B030001:pass")
    context = _make_context()

    asyncio.run(bot.documents_callback(update, context))

    push = _admin_pushes(context)
    assert len(push) == len(ADMIN_IDS) == 1
    assert "B030001" in push[0] and "аспорт" in push[0]
    assert f"/resolve_question {open_unanswered()[0]['id']}" in push[0]


def test_the_admin_push_names_the_asker_and_the_reason(isolated_queue):
    context = _make_context()

    asyncio.run(bot.notify_admins_unanswered(
        context, 7, "какой ток у YCW3?", "rag_no_evidence",
        user_id=next(iter(ADMIN_IDS)), role=bot.Role.ADMIN))

    push = _admin_pushes(context)[0]
    assert "#7" in push and "какой ток у YCW3?" in push
    assert bot.UNANSWERED_REASONS["rag_no_evidence"] in push
    assert str(next(iter(ADMIN_IDS))) in push and "Администратор" in push


def test_a_failed_push_does_not_break_the_reply(isolated_queue, monkeypatch):
    """Телеграм может не доставить сообщение админу — заявка уже в базе,
    и пользователь обязан получить свой номер в любом случае."""
    from core.logging_ import open_unanswered

    monkeypatch.setattr(bot, "passport_for_article", lambda article: None)
    update = _make_callback_update("doc:B030001:pass")
    context = _make_context()
    context.application.bot.send_message = AsyncMock(side_effect=RuntimeError("bot was blocked"))

    asyncio.run(bot.documents_callback(update, context))

    assert "зарегистрирован, номер #" in update.callback_query.message.reply_text.call_args[0][0]
    assert len(open_unanswered()) == 1


# --- /catalog: каталоги для скачивания ----------------------------------------
# Справочник — knowledge/catalog_links.xlsx, десяток строк «название → ссылка».
# Здесь он подменяется: тесты про сам разбор файла — в test_catalog_links.py.

_CATALOGS = [
    bot.catalog_links.Catalog("каталог трансформаторы", "https://25.lgprk.ru/share?token=aaa"),
    bot.catalog_links.Catalog("каталог воздушные выключатели", "https://25.lgprk.ru/share?token=bbb"),
]


@pytest.fixture
def two_catalogs(monkeypatch):
    monkeypatch.setattr(bot.catalog_links, "catalogs", lambda: _CATALOGS)
    monkeypatch.setattr(
        bot.catalog_links, "find",
        lambda key: next((c for c in _CATALOGS if c.key == key), None),
    )
    return _CATALOGS


def test_catalog_command_shows_a_button_per_catalog(two_catalogs):
    update, context = _make_update(), _make_context()

    asyncio.run(bot.catalog_command(update, context))

    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert [b.text for b in buttons] == ["📕 Каталог трансформаторы", "📕 Каталог воздушные выключатели"]
    assert [b.callback_data for b in buttons] == [f"cat:{c.key}" for c in two_catalogs]


def test_catalog_button_replies_with_the_link(two_catalogs):
    update = _make_callback_update(f"cat:{two_catalogs[0].key}")

    asyncio.run(bot.catalog_callback(update, _make_context()))

    text = update.callback_query.message.reply_text.call_args[0][0]
    assert "Каталог трансформаторы" in text
    assert "https://25.lgprk.ru/share?token=aaa" in text


def test_catalog_button_leaves_the_keyboard_in_place(two_catalogs):
    """Ответ приходит новым сообщением: за одну команду забирают несколько
    каталогов, и клавиатура нужна дальше."""
    update = _make_callback_update(f"cat:{two_catalogs[0].key}")
    update.callback_query.edit_message_text = AsyncMock()

    asyncio.run(bot.catalog_callback(update, _make_context()))

    update.callback_query.edit_message_text.assert_not_called()


def test_catalog_button_from_an_old_message_says_the_catalog_is_gone(two_catalogs):
    """Справочник обновили, каталога в нём больше нет — кнопка в истории чата
    не должна ни падать, ни отдавать чужую ссылку."""
    update = _make_callback_update("cat:deadbeef")

    asyncio.run(bot.catalog_callback(update, _make_context()))

    text = update.callback_query.message.reply_text.call_args[0][0]
    assert "больше нет" in text
    assert "http" not in text


def test_catalog_command_without_the_reference_file_says_so(monkeypatch):
    """Файл забыли выкатить — бот честно сообщает об этом, а не показывает
    пустую клавиатуру."""
    monkeypatch.setattr(bot.catalog_links, "catalogs", list)
    update = _make_update()

    asyncio.run(bot.catalog_command(update, _make_context()))

    assert update.message.reply_text.call_args.kwargs.get("reply_markup") is None
    assert "недоступен" in update.message.reply_text.call_args[0][0]


def test_catalog_is_offered_to_every_role_including_the_client():
    """Каталог — то немногое, что нужно всем: и менеджеру, и подписчику канала."""
    for role in (bot.Role.ADMIN, bot.Role.DIRECTOR, bot.Role.ENGINEER,
                 bot.Role.MANAGER, bot.Role.CLIENT):
        assert "catalog" in _names(role), role
