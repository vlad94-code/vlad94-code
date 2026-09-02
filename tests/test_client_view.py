"""Что видит клиент вместо остатков и приходов.

Клиент — подписчик канала, а не сотрудник: цену он видит, а склад нет.
Цифра остатка и дата прихода для него не должны собираться вообще, а не
собираться и потом вырезаться из готового текста."""
import asyncio

import pytest

import catalog_search
import pricelist_store


@pytest.fixture
def snapshot(monkeypatch):
    """Один товар: 12 штук на складе и приход 30 штук к 15.09."""
    row = {
        "vendor_code": "B030524",
        "name": "Воздушный выключатель YCW3 2500/630A",
        "type_item": "Воздушный выключатель",
        "series": "YCW3",
        "specification": [{"name": "Серия", "value": "YCW3"}],
    }
    monkeypatch.setattr(catalog_search, "products", lambda: [row])
    monkeypatch.setattr(catalog_search, "stock_map", lambda: {"B030524": 12})
    monkeypatch.setattr(catalog_search, "transit_map", lambda: {"B030524": [(30.0, "2026-09-15")]})
    monkeypatch.setattr(catalog_search, "price_map", lambda: {"B030524": {"base_price": "185000"}})
    # indexed()/by_vendor_code() кэшируют то, что успели построить по подменённому
    # снимку, и переживают откат monkeypatch: без сброса до и после наш выдуманный
    # выключатель становился каталогом для всех следующих тестов.
    catalog_search.clear_cache()
    yield row
    catalog_search.clear_cache()


def test_staff_still_see_the_stock_figure(snapshot):
    text = catalog_search.card(snapshot)
    assert "12" in text
    assert "185000" in text


def test_client_is_sent_to_the_manager_instead_of_a_figure(snapshot):
    text = catalog_search.card(snapshot, show_stock=False)
    assert "уточните" in text.lower() and "менеджер" in text.lower()
    assert "12 шт" not in text


def test_client_still_sees_the_price(snapshot):
    """Цену клиенту показываем — скрыт только склад."""
    assert "185000" in catalog_search.card(snapshot, show_stock=False)


def test_client_never_sees_an_arrival_date(snapshot):
    """Товара нет на складе, но едет 30 штук к 15.09 — дата не для клиента."""
    monkeypatch_free = dict(snapshot)
    text = catalog_search.card(monkeypatch_free, show_stock=False)
    assert "2026" not in text and "15.09" not in text
    assert "ожидается" not in text


def test_client_list_hides_stock_too(snapshot):
    """Список кандидатов печатает остаток короткой формой — там же дыра."""
    rows = [snapshot, dict(snapshot, vendor_code="B030525")]
    text = catalog_search.result_text(rows, {}, show_stock=False)
    assert "12 шт" not in text
    assert "менеджер" in text.lower()


def test_detail_passes_the_flag_through(snapshot):
    assert "12 шт" not in catalog_search.detail("B030524", show_stock=False)
    assert "12 шт" in catalog_search.detail("B030524")


def test_accessory_list_hides_stock_for_a_client(snapshot):
    """Аксессуары печатаются своим форматтером — он тоже показывает остаток."""
    rows = [{"article": "B030524", "name": "Расцепитель", "size": None}]
    text = pricelist_store.format_accessory_group("YCW3", "Расцепители", rows, show_stock=False)
    assert "12 шт" not in text
    assert "185000" in text


def test_accessory_list_still_shows_stock_to_staff(snapshot):
    rows = [{"article": "B030524", "name": "Расцепитель", "size": None}]
    assert "12 шт" in pricelist_store.format_accessory_group("YCW3", "Расцепители", rows)


# --- первый экран клиента и отказ незнакомцу ---------------------------------

def test_rejection_is_a_business_card():
    """Незнакомец уходит, зная, чем занимается CNC и куда написать."""
    from core.roles import rejection_text

    text = rejection_text(123456789)
    assert "CNC Electric" in text
    assert "help@cncrussia.com" in text
    assert "info@cncrussia.com" in text
    assert "@cncelectric_russia" in text
    assert "123456789" in text


def test_client_start_offers_four_buttons():
    """Кнопка без хендлера — молчащая кнопка, поэтому набор фиксируем тестом."""
    import bot

    data = [b.callback_data for row in bot.client_start_keyboard().inline_keyboard for b in row]
    assert set(data) == {"about", "catalog_menu", "how_to_buy", "ask_support"}


def test_about_text_never_promises_prices_or_stock():
    import bot

    assert "скидк" not in bot.ABOUT_SHORT.lower()
    assert "склад" not in bot.ABOUT_SHORT.lower()


# --- клиентская ветка answer() ------------------------------------------------

class _Message:
    def __init__(self, text, replies):
        self.text = text
        self._replies = replies
        self.documents = []

    async def reply_text(self, text, **kwargs):
        self._replies.append(text)

    async def reply_document(self, document, **kwargs):
        self.documents.append(kwargs.get("caption", ""))

    async def reply_chat_action(self, *args, **kwargs):
        pass


class _Chat:
    async def send_action(self, *args, **kwargs):
        pass


class _User:
    id = 555
    username = "client"


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
    update.effective_chat = _Chat()
    update.effective_user = _User()
    return update


def _context(user_data=None, sent=None):
    return _Context(user_data, sent)


def _async(value):
    async def call(*args, **kwargs):
        return value
    return call


@pytest.fixture
def client_role(monkeypatch):
    """Клиентская ветка answer() без Telegram и без записи в боевую базу."""
    import bot

    monkeypatch.setattr(bot, "resolve_role", _async(bot.Role.CLIENT))
    monkeypatch.setattr(bot, "log_query", lambda *args, **kwargs: 1)
    return bot


def test_client_free_question_is_not_answered_with_send_an_article(client_role):
    replies = []
    update = _client_update("Какая гарантия?", replies)

    asyncio.run(client_role.answer(update, _context()))

    assert replies, "клиент должен получить ответ"
    assert "пришлите точный артикул" not in replies[0].lower()


def test_commercial_question_goes_to_the_manager_step(client_role):
    """Коммерческий вопрос уходит человеку, а не в шаблон про артикул."""
    replies = []
    update = _client_update("Хочу купить, выставьте счёт", replies)

    asyncio.run(client_role.answer(update, _context()))

    assert "город" in replies[-1].lower()


# --- рекламация --------------------------------------------------------------

def test_reclamation_question_attaches_the_form():
    import bot

    assert bot.RECLAMATION_FORM.exists()
    assert bot._is_reclamation("Оборудование бракованное, куда писать?")
    assert bot._is_reclamation("Как оформить рекламацию?")
    assert not bot._is_reclamation("Какая гарантия?")


def test_a_client_complaining_about_a_defect_gets_the_form(client_role):
    """Инструкция без бланка — половина ответа: бланк уходит тем же сообщением."""
    replies = []
    update = _client_update("Автомат сгорел на второй день, как оформить рекламацию?", replies)

    asyncio.run(client_role.answer(update, _context()))

    assert update.message.documents, "форма рекламационного акта не отправлена"
    caption = update.message.documents[-1]
    assert "help@cncrussia.com" in caption
