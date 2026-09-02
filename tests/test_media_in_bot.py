"""Фото в карточке товара и сертификат по кнопке «Документы».

Фотография — не документ: она не должна ломать ответ. Хостинг «Ориентира»
может не ответить, шару могли отозвать, файл — оказаться в 60 МБ; во всех
этих случаях менеджер обязан получить карточку с характеристиками, как
получал раньше, и ни одного сообщения об ошибке.

Сеть не трогается: share_client подменяется целиком, его собственный разбор
проверяет tests/test_share_client.py.
"""
import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest

import bot
import client_flow
import core.db
import media_links
from core.roles import ADMIN_IDS, Role
from media_links import MediaLinks

_PHOTO = "https://25.lgprk.ru/share?token=20644a7a-1661-48f1-b4cd-c881987105c246c638fa"
_CERT = "https://25.lgprk.ru/share?token=41660046-1e70-4af7-ab97-182ec10a97524990849a"
_MODEL = "https://25.lgprk.ru/share?token=11f73f7c-7419-4868-8d30-1826ebd95d23b223bdcc"

_LINKS = MediaLinks("B05012", _PHOTO, "YCM3YP-100.png", _CERT, "C-00883.jpg",
                    _MODEL, "CJX2-F1154-3D.stp")
_PHOTO_ONLY = MediaLinks("B05012", _PHOTO, "YCM3YP-100.png", None, None)


def _message():
    return SimpleNamespace(
        reply_text=AsyncMock(),
        reply_photo=AsyncMock(return_value=SimpleNamespace(photo=[SimpleNamespace(file_id="AgACnew")])),
        reply_document=AsyncMock(return_value=SimpleNamespace(document=SimpleNamespace(file_id="BQACnew"))),
        reply_chat_action=AsyncMock(),
    )


def _context():
    # application.bot — уведомление админу о заявке в очереди /unanswered.
    return SimpleNamespace(
        args=[],
        user_data={},
        application=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock())),
    )


def _callback_update(data: str):
    message = _message()
    return SimpleNamespace(
        callback_query=SimpleNamespace(data=data, answer=AsyncMock(), message=message),
        message=message,
        effective_user=SimpleNamespace(id=next(iter(ADMIN_IDS))),
    )


@pytest.fixture
def media(monkeypatch):
    """Таблица медиа в памяти: боевой data/knowledge.db тесты не открывают."""
    state = {"links": _LINKS, "cache": {}, "forgotten": []}

    monkeypatch.setattr(bot.media_links, "for_article", lambda article: state["links"])
    monkeypatch.setattr(bot.media_links, "cached_file_id",
                        lambda url, kind: state["cache"].get((url, kind)))
    monkeypatch.setattr(bot.media_links, "remember_file_id",
                        lambda url, kind, file_id: state["cache"].__setitem__((url, kind), file_id))
    monkeypatch.setattr(bot.media_links, "forget_file_id",
                        lambda url, kind: (state["forgotten"].append((url, kind)),
                                           state["cache"].pop((url, kind), None)))
    monkeypatch.setattr(bot, "catalog_by_vendor_code",
                        lambda: {"B05012": {"name": "Выключатель YCM3YP 100A 3P", "vendor_code": "B05012"}})
    return state


def _png(size=(632, 720)) -> bytes:
    """Настоящий PNG: бот проверяет габариты снимка через Pillow, и на
    выдуманных байтах он справедливо решает, что это не изображение."""
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def downloads(monkeypatch):
    """Что «Ориентир» отдаёт по ссылке — задаётся каждым тестом."""
    state = {"result": (_png(), "YCM3YP-100.png"), "calls": []}

    async def fake_fetch(url, size_limit=share_limit_default()):
        state["calls"].append(url)
        return state["result"]

    monkeypatch.setattr(bot.share_client, "fetch", fake_fetch)
    return state


def share_limit_default():
    import share_client
    return share_client.SIZE_LIMIT


# --- Фото в карточке ---------------------------------------------------------

def test_the_photo_is_sent_with_the_article_and_the_product_name(media, downloads):
    message = _message()

    asyncio.run(bot.send_product_photo(message, "B05012"))

    caption = message.reply_photo.call_args.kwargs["caption"]
    assert "B05012" in caption and "YCM3YP" in caption


def test_an_article_without_a_photo_sends_nothing(media, downloads):
    media["links"] = MediaLinks("B05012", None, None, _CERT, "C-00883.jpg")
    message = _message()

    asyncio.run(bot.send_product_photo(message, "B05012"))

    message.reply_photo.assert_not_called()
    message.reply_text.assert_not_called()
    assert downloads["calls"] == []


def test_an_article_missing_from_the_table_sends_nothing(media, downloads):
    media["links"] = None
    message = _message()

    asyncio.run(bot.send_product_photo(message, "D999999"))

    message.reply_photo.assert_not_called()
    message.reply_text.assert_not_called()


def test_a_photo_the_hosting_will_not_give_is_skipped_without_a_word(media, downloads):
    """Карточка важнее фотографии: жаловаться на чужой хостинг менеджеру
    незачем, он всё равно ничего с этим не сделает."""
    downloads["result"] = None
    message = _message()

    asyncio.run(bot.send_product_photo(message, "B05012"))

    message.reply_photo.assert_not_called()
    message.reply_text.assert_not_called()


def test_what_is_not_an_image_at_all_goes_as_a_document(media, downloads):
    """По ссылке на чужом хостинге однажды окажется страница ошибки или архив.
    Фотографией это не отправить, но и терять файл незачем."""
    downloads["result"] = (b"<!doctype html><html>404</html>", "oops.html")
    message = _message()

    asyncio.run(bot.send_product_photo(message, "B05012"))

    message.reply_photo.assert_not_called()
    assert message.reply_document.call_args.kwargs["filename"] == "oops.html"


# --- Кэш file_id -------------------------------------------------------------

def test_the_first_photo_send_remembers_the_file_id(media, downloads):
    asyncio.run(bot.send_product_photo(_message(), "B05012"))

    assert media["cache"][(_PHOTO, media_links.PHOTO)] == "AgACnew"


def test_a_cached_photo_is_resent_without_downloading(media, downloads):
    """654 фотографии делят 11 552 артикула — второй раз тот же файл качать
    нельзя, Telegram пересылает его по file_id."""
    media["cache"][(_PHOTO, media_links.PHOTO)] = "AgACcached"
    message = _message()

    asyncio.run(bot.send_product_photo(message, "B05012"))

    assert message.reply_photo.call_args.kwargs["photo"] == "AgACcached"
    assert downloads["calls"] == []


def test_a_file_id_telegram_rejects_is_forgotten_and_the_photo_refetched(media, downloads):
    """Сменился токен бота — чужие file_id Telegram не примет. Менеджер об
    этом знать не должен: файл просто скачивается заново."""
    media["cache"][(_PHOTO, media_links.PHOTO)] = "ЧУЖОЙ"
    message = _message()
    message.reply_photo = AsyncMock(side_effect=[
        BadRequest("wrong file identifier"),
        SimpleNamespace(photo=[SimpleNamespace(file_id="AgACnew")]),
    ])

    asyncio.run(bot.send_product_photo(message, "B05012"))

    assert media["forgotten"] == [(_PHOTO, media_links.PHOTO)]
    assert downloads["calls"] == [_PHOTO]
    assert message.reply_photo.call_count == 2


# --- Кнопка «Сертификат» -----------------------------------------------------

@pytest.fixture
def isolated_queue(tmp_path, monkeypatch):
    """Своя data/bot.db: очередь заявок инженеру боевую базу не трогает."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    core.db.init_db()
    yield


def test_the_certificate_button_sends_the_document(media, downloads, isolated_queue):
    from core.logging_ import open_unanswered

    downloads["result"] = (b"\xff\xd8\xff jpeg", "C-00883 с водяным знаком_Страница_1.jpg")
    update = _callback_update("doc:B05012:cert")

    asyncio.run(bot.documents_callback(update, _context()))

    call = update.callback_query.message.reply_document.call_args
    assert call.kwargs["filename"] == "C-00883 с водяным знаком_Страница_1.jpg"
    assert "B05012" in call.kwargs["caption"]
    assert open_unanswered() == []


def test_the_certificate_send_remembers_its_own_file_id(media, downloads, isolated_queue):
    """Сертификат уходит документом, фото — фотографией; Telegram выдаёт на
    один файл разные file_id, и путать их нельзя."""
    downloads["result"] = (b"\xff\xd8\xff jpeg", "C-00883.jpg")

    asyncio.run(bot.documents_callback(_callback_update("doc:B05012:cert"),
                                       _context()))

    assert media["cache"][(_CERT, media_links.CERT)] == "BQACnew"
    assert (_CERT, media_links.PHOTO) not in media["cache"]


def test_a_certificate_the_hosting_will_not_give_falls_back_to_the_link(media, downloads, isolated_queue):
    """В отличие от фото, здесь менеджер нажал кнопку и ждёт ответа —
    молчать нельзя, отдаём хотя бы ссылку."""
    downloads["result"] = None
    update = _callback_update("doc:B05012:cert")

    asyncio.run(bot.documents_callback(update, _context()))

    update.callback_query.message.reply_document.assert_not_called()
    assert _CERT in update.callback_query.message.reply_text.call_args[0][0]


def test_an_article_without_a_certificate_still_asks_the_engineer(media, downloads, isolated_queue):
    from core.logging_ import open_unanswered

    media["links"] = _PHOTO_ONLY
    update = _callback_update("doc:B05012:cert")

    asyncio.run(bot.documents_callback(update, _context()))

    update.callback_query.message.reply_document.assert_not_called()
    rows = open_unanswered()
    assert len(rows) == 1 and rows[0]["reason"] == "document_request"


# --- Кнопка «3D-модель» ------------------------------------------------------

def test_the_3d_button_sends_the_model_as_a_file(media, downloads, isolated_queue):
    from core.logging_ import open_unanswered

    downloads["result"] = (b"ISO-10303-21;", "CJX2-F1154-3D.stp")
    update = _callback_update("doc:B05012:3d")

    asyncio.run(bot.documents_callback(update, _context()))

    call = update.callback_query.message.reply_document.call_args
    assert call.kwargs["filename"] == "CJX2-F1154-3D.stp"
    assert open_unanswered() == []


def test_the_model_caption_carries_the_link_next_to_the_file(media, downloads, isolated_queue):
    """Модель нужна и файлом, и ссылкой: файл пересылают клиенту, а по ссылке
    он скачает свежую версию, когда пересланное сообщение потеряется."""
    downloads["result"] = (b"ISO-10303-21;", "CJX2-F1154-3D.stp")
    update = _callback_update("doc:B05012:3d")

    asyncio.run(bot.documents_callback(update, _context()))

    caption = update.callback_query.message.reply_document.call_args.kwargs["caption"]
    assert "B05012" in caption and _MODEL in caption


def test_the_model_send_remembers_its_own_file_id(media, downloads, isolated_queue):
    downloads["result"] = (b"ISO-10303-21;", "CJX2-F1154-3D.stp")

    asyncio.run(bot.documents_callback(_callback_update("doc:B05012:3d"), _context()))

    assert media["cache"][(_MODEL, media_links.MODEL)] == "BQACnew"


def test_a_model_too_large_for_telegram_still_arrives_as_a_link(media, downloads, isolated_queue):
    """share_client не отдаёт файл, который не влезет в 50 МБ. Ссылка ведёт на
    ту же модель, и менеджеру она годится — в отличие от молчания."""
    downloads["result"] = None
    update = _callback_update("doc:B05012:3d")

    asyncio.run(bot.documents_callback(update, _context()))

    update.callback_query.message.reply_document.assert_not_called()
    assert _MODEL in update.callback_query.message.reply_text.call_args[0][0]


def test_an_article_without_a_3d_model_still_asks_the_engineer(media, downloads, isolated_queue):
    """Модель есть у 2379 артикулов из 11 552; для остальных кнопка копит
    заявки, по которым видно, что наполнять в первую очередь."""
    from core.logging_ import open_unanswered

    media["links"] = _PHOTO_ONLY
    update = _callback_update("doc:B05012:3d")

    asyncio.run(bot.documents_callback(update, _context()))

    update.callback_query.message.reply_document.assert_not_called()
    rows = open_unanswered()
    assert len(rows) == 1 and rows[0]["reason"] == "document_request"


# --- Фото появляется в самой карточке ----------------------------------------

def _answer_update(text: str):
    message = _message()
    message.text = text
    return SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=next(iter(ADMIN_IDS))),
        effective_chat=SimpleNamespace(send_action=AsyncMock()),
    )


def test_a_client_asking_for_an_article_gets_the_photo_before_the_card(media, downloads, isolated_queue, monkeypatch):
    """Фото видят все роли, клиента включая — он и приходит в бот за
    артикулом (ARCHITECTURE.md §3)."""
    monkeypatch.setattr(bot, "resolve_role", AsyncMock(return_value=Role.CLIENT))
    monkeypatch.setattr(client_flow, "article_code", lambda question: "B05012")
    monkeypatch.setattr(client_flow, "catalog_detail",
                        lambda code, show_stock=True: "Выключатель YCM3YP 100A")
    update = _answer_update("B05012")

    asyncio.run(bot.answer(update, _context()))

    update.message.reply_photo.assert_called_once()
    assert "YCM3YP" in update.message.reply_text.call_args[0][0]


def test_a_manager_asking_for_an_article_gets_the_photo_before_the_card(media, downloads, isolated_queue, monkeypatch):
    monkeypatch.setattr(bot, "resolve_role", AsyncMock(return_value=Role.MANAGER))
    monkeypatch.setattr(bot, "route_local", AsyncMock(return_value=SimpleNamespace(
        handled=True, text="Выключатель YCM3YP 100A", sources=[], engine_name="catalog",
        context_update={"sole_article": "B05012"},
    )))
    monkeypatch.setattr(bot, "accessory_offer", AsyncMock(return_value=("", None)))
    update = _answer_update("B05012")

    asyncio.run(bot.answer(update, _context()))

    update.message.reply_photo.assert_called_once()
    assert "YCM3YP" in update.message.reply_text.call_args[0][0]


def test_a_list_of_results_carries_no_photo(media, downloads, isolated_queue, monkeypatch):
    """Фотография — у карточки одной позиции. В списке из сорока автоматов
    сорок картинок сделали бы ответ нечитаемым."""
    monkeypatch.setattr(bot, "resolve_role", AsyncMock(return_value=Role.MANAGER))
    monkeypatch.setattr(bot, "route_local", AsyncMock(return_value=SimpleNamespace(
        handled=True, text="Найдено 40 позиций", sources=[], engine_name="catalog",
        context_update={},
    )))
    monkeypatch.setattr(bot, "accessory_offer", AsyncMock(return_value=("", None)))
    update = _answer_update("YCM3 100А")

    asyncio.run(bot.answer(update, _context()))

    update.message.reply_photo.assert_not_called()


# --- Прогрев кэша ------------------------------------------------------------
# Первый показ каждого файла стоит скачивания с S3 и загрузки в Telegram.
# Файлов всего 668 на 11 552 артикула, поэтому дешевле прогнать их разом, чем
# заставлять ждать того менеджера, кто первым спросит артикул.

@pytest.fixture
def warm(media, downloads, monkeypatch):
    monkeypatch.setattr(bot, "WARM_DELAY", 0)
    urls = {media_links.PHOTO: [_PHOTO], media_links.CERT: [_CERT],
            media_links.MODEL: [_MODEL]}
    monkeypatch.setattr(bot.media_links, "distinct_urls", lambda kind: urls[kind])
    return urls


def _warm_message():
    message = _message()
    message.reply_photo.return_value = SimpleNamespace(
        photo=[SimpleNamespace(file_id="AgACnew")], delete=AsyncMock())
    message.reply_document.return_value = SimpleNamespace(
        document=SimpleNamespace(file_id="BQACnew"), delete=AsyncMock())
    return message


def test_warming_stores_a_file_id_for_every_kind(warm, media):
    message = _warm_message()

    assert asyncio.run(bot.warm_media_cache(message)) == (3, 0, 0, 0)
    assert media["cache"][(_PHOTO, media_links.PHOTO)] == "AgACnew"
    assert media["cache"][(_CERT, media_links.CERT)] == "BQACnew"
    assert media["cache"][(_MODEL, media_links.MODEL)] == "BQACnew"


def test_warming_removes_its_service_messages(warm, media):
    """Прогрев шлёт файлы в чат админа только чтобы получить file_id —
    оставлять 668 сообщений в переписке незачем, а file_id переживает
    удаление сообщения."""
    message = _warm_message()

    asyncio.run(bot.warm_media_cache(message))

    # Одно фото и два документа — сертификат и 3D-модель; мок у документов общий.
    assert message.reply_photo.return_value.delete.await_count == 1
    assert message.reply_document.return_value.delete.await_count == 2


def test_warming_skips_what_is_already_cached(warm, media, downloads):
    media["cache"][(_PHOTO, media_links.PHOTO)] = "AgACcached"
    media["cache"][(_CERT, media_links.CERT)] = "BQACcached"
    media["cache"][(_MODEL, media_links.MODEL)] = "BQACcached"

    assert asyncio.run(bot.warm_media_cache(_warm_message())) == (0, 3, 0, 0)
    assert downloads["calls"] == []


def test_warming_counts_a_file_the_hosting_will_not_give_and_goes_on(warm, media, downloads):
    """Одна отозванная шара не должна обрывать прогрев остальных 667 файлов."""
    downloads["result"] = None

    assert asyncio.run(bot.warm_media_cache(_warm_message())) == (0, 0, 3, 0)


# --- Загрузка таблицы --------------------------------------------------------

def test_upload_media_refuses_a_non_xlsx_file():
    update = _answer_update("")
    update.message.document = SimpleNamespace(file_name="номенклатура.pdf")
    asyncio.run(bot.upload_media(update, _context()))
    assert "xlsx" in update.message.reply_text.call_args[0][0].lower()


def test_warm_media_without_a_table_asks_for_the_file_first(media, isolated_queue, monkeypatch):
    """Греть нечего, пока таблица не загружена — и сказать надо об этом, а не
    отчитаться «0 загружено» так, будто всё в порядке."""
    monkeypatch.setattr(bot.media_links, "distinct_urls", lambda kind: [])
    monkeypatch.setattr(bot, "warm_media_cache", AsyncMock(side_effect=AssertionError("греть нечего")))
    update = _answer_update("/warm_media")

    asyncio.run(bot.warm_media_command(update, _context()))

    assert "/upload_media" in update.message.reply_text.call_args[0][0]


def test_warm_media_does_not_promise_a_duration_it_cannot_know(warm, media, isolated_queue, monkeypatch):
    """Обещание «примерно 11 мин» бралось из паузы между отправками, а время
    съедает скачивание: замер 29.08.2026 — 8,4 с на файл при размере 0,9–6,7 МБ,
    то есть все 668 файлов идут около полутора часов. Врать про минуты нельзя."""
    monkeypatch.setattr(bot, "warm_media_cache", AsyncMock(return_value=bot.WarmReport(2, 0, 0, 0)))
    update = _answer_update("/warm_media")

    asyncio.run(bot.warm_media_command(update, _context()))

    announced = update.message.reply_text.call_args_list[0][0][0]
    assert "мин" not in announced


# --- Время на загрузку файла -------------------------------------------------

def test_the_bot_is_built_with_room_to_upload_a_heavy_file():
    """python-telegram-bot по умолчанию даёт на запись 5 секунд, и send_photo
    этого не переопределяет. Замер 29.08.2026: на прогреве 215 файлов из 668
    отвалились с TimedOut — снимки весят до 6,7 МБ, паспорта до 13, и в пять
    секунд такая заливка не укладывается. Менеджеру это выглядело бы как
    «фото просто не приходит», молча и каждый раз.
    """
    app = bot.build_application("123:ABC")
    assert app.bot.request._client.timeout.write >= 60


# --- Снимки, которые Telegram не берёт фотографией ---------------------------
# Файлы в таблице лежат как с камеры: 6936×9248 при 13 МБ. sendPhoto требует
# сумму сторон не больше 10000 и отвечает Photo_invalid_dimensions — на этом
# встали 148 из 654 фотографий.

@pytest.fixture(scope="module")
def camera_original():
    """Снимок, не проходящий по габаритам: 6000 + 4500 больше 10000."""
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (6000, 4500), (200, 120, 60)).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def test_a_photo_within_the_limits_is_passed_through_untouched():
    """Пережимать то, что Telegram и так примет, незачем — качество дороже."""
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (632, 720), (10, 20, 30)).save(buffer, format="JPEG")
    original = buffer.getvalue()

    assert bot.fit_for_telegram(original) is original


def test_a_camera_original_is_shrunk_until_telegram_will_take_it(camera_original):
    from PIL import Image
    fitted = bot.fit_for_telegram(camera_original)
    width, height = Image.open(io.BytesIO(fitted)).size

    assert width + height <= bot.PHOTO_MAX_SIDES_SUM


def test_the_shrunk_photo_also_fits_the_size_limit(camera_original):
    assert len(bot.fit_for_telegram(camera_original)) <= bot.PHOTO_SIZE_LIMIT


def test_something_that_is_not_an_image_is_not_a_photo():
    """По ссылке на чужом хостинге однажды окажется что угодно — тогда пусть
    уходит файлом, а не роняет ответ."""
    assert bot.fit_for_telegram(b"<!doctype html><html>404</html>") is None


def test_a_camera_original_still_arrives_as_a_photo(media, downloads, camera_original):
    """Вы просили, чтобы фото появлялось. Вложенный файл вместо картинки —
    это не «появилось»."""
    downloads["result"] = (camera_original, "C020057.jpg")
    message = _message()

    asyncio.run(bot.send_product_photo(message, "B05012"))

    message.reply_photo.assert_called_once()
    message.reply_document.assert_not_called()


def test_a_photo_telegram_refuses_anyway_goes_as_a_file_without_breaking_the_card(media, downloads):
    """Отказ на свежей загрузке не был обёрнут вовсе: BadRequest улетал в
    answer() и менеджер оставался без карточки целиком, а не без фотографии."""
    message = _message()
    message.reply_photo = AsyncMock(side_effect=BadRequest("Photo_invalid_dimensions"))

    asyncio.run(bot.send_product_photo(message, "B05012"))

    assert message.reply_document.call_args.kwargs["filename"] == "YCM3YP-100.png"


def test_a_photo_that_went_as_a_file_is_not_cached_as_a_photo(media, downloads):
    """file_id документа обратно фотографией не отдать — класть его в кэш
    под видом фото значит обречь следующий показ на отказ и перекачку."""
    message = _message()
    message.reply_photo = AsyncMock(side_effect=BadRequest("Photo_invalid_dimensions"))

    asyncio.run(bot.send_product_photo(message, "B05012"))

    assert media["cache"] == {}


def test_warming_tells_a_silent_host_apart_from_a_refusing_telegram(warm, media, downloads):
    """Отчёт «148 не отдал хостинг» увёл в сторону: файлы хостинг отдал, их
    не принял Telegram — снимки с камеры не проходят по габаритам. Причина
    решает, что делать дальше, и путать их нельзя."""
    message = _warm_message()
    message.reply_photo = AsyncMock(side_effect=BadRequest("Photo_invalid_dimensions"))

    warmed, skipped, unavailable, refused = asyncio.run(bot.warm_media_cache(message))

    assert (unavailable, refused) == (0, 1)


def test_the_warm_report_names_the_reason_it_actually_saw(warm, media, monkeypatch):
    monkeypatch.setattr(bot, "warm_media_cache", AsyncMock(return_value=bot.WarmReport(500, 0, 0, 148)))
    update = _answer_update("/warm_media")

    asyncio.run(bot.warm_media_command(update, _context()))

    report = update.message.reply_text.call_args_list[-1][0][0]
    assert "Telegram" in report and "хостинг" not in report


def test_a_clean_warm_report_mentions_no_trouble_at_all(warm, media, monkeypatch):
    monkeypatch.setattr(bot, "warm_media_cache", AsyncMock(return_value=bot.WarmReport(668, 0, 0, 0)))
    update = _answer_update("/warm_media")

    asyncio.run(bot.warm_media_command(update, _context()))

    report = update.message.reply_text.call_args_list[-1][0][0]
    assert "не " not in report
