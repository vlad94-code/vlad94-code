# tests/test_client_flow.py
"""Пять ступеней лестницы (спека §6). Без Telegram и без сети."""
import asyncio
from unittest.mock import AsyncMock

import pytest

import client_flow
from catalog_search import STOCK_FOR_CLIENT
from core.types import EngineResponse


def answer(question, context=None):
    return asyncio.run(client_flow.answer_for_client(question, context or {}))


def test_commercial_question_goes_to_manager():
    for question in ("Есть на складе?", "Дайте скидку", "Пришлите счёт"):
        assert answer(question).kind == "escalate_manager"


def test_commercial_and_replacement_regexes_do_not_catch_technical_questions():
    # Правка контроллера (FINDING 2): без границ слова "счёт"/"счет" ловили
    # "расчёт" тока КЗ, "опт" ловил "оптический"/"оптический"/"оптимальный",
    # а "аналог" в _REPLACEMENT_RE ловил "аналоговый" (тип сигнала/выхода).
    # Каждый из этих вопросов — реальный технический вопрос по нашей же
    # номенклатуре и не должен уходить ни к менеджеру, ни в подбор замены.
    technical_questions = (
        "Какой расчёт тока короткого замыкания вы используете?",
        "У вас есть оптопара в этом модуле?",
        "Какой оптический датчик используется в реле?",
        "Какой оптимальный ток для этого автомата?",
        "Есть ли у вас аналоговый выход?",
    )
    for question in technical_questions:
        assert client_flow._COMMERCIAL_RE.search(question) is None, question
        assert client_flow._REPLACEMENT_RE.search(question) is None, question


def test_replacement_question_explains_and_offers_support():
    result = answer("Чем заменить ABB S203?")
    assert result.kind == "replacement"
    assert "техническая служба" in result.text.lower()
    assert "не найдено" not in result.text.lower()


def test_unknown_technical_question_goes_to_support():
    result = answer("Какой производитель пластика корпуса у автоматов?")
    assert result.kind == "escalate_support"


def test_client_answer_never_shows_stock():
    # Ужесточено по правке контроллера (FINDING 1): раньше тест пропускал
    # любой ответ со словом "уточните" где угодно в тексте, не проверяя,
    # что реального остатка в ответе при этом нет. Теперь — явная проверка
    # отсутствия признаков сырого остатка ("шт.") и наличия формулировки
    # для клиента (catalog_search.STOCK_FOR_CLIENT).
    result = answer("B030524")
    assert "шт." not in result.text
    assert STOCK_FOR_CLIENT in result.text


def test_catalog_listing_never_shows_stock():
    # Тот же вопрос, что раньше ошибочно ожидал escalate_support (см.
    # отчёт задачи 6): он реально уходит в ProductEngine и возвращает
    # список из нескольких товаров. До правки FINDING 1 каждая строка
    # списка несла настоящий остаток ("170 шт." и т.п.) — критическая
    # утечка складских данных клиенту.
    result = answer("Какая индуктивность у катушки в вашем реле времени?")
    assert result.kind == "catalog"
    assert "шт." not in result.text
    assert STOCK_FOR_CLIENT in result.text


def test_catalog_answer_without_structured_data_escalates_instead_of_leaking():
    # Защитная ветка: если движок отчитался "product", но не оставил в
    # context_update ни sole_article, ни catalog_filters, перерисовать
    # ответ без остатка нечем — safer to escalate than to guess.
    fake = EngineResponse(
        text="Найдено 2 позиций.\n\n• B000001 · авт. · 100,00 р. · 5 шт.",
        handled=True,
        engine_name="product",
        context_update={},
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(client_flow, "route_local", AsyncMock(return_value=fake))
        result = answer("что-нибудь про автоматы")
    assert result.kind == "escalate_support"


def test_no_such_value_answer_passes_through_without_redraw():
    # Правка контроллера, раунд 3 (FINDING 1 и 3): прежний тест собирал
    # заглушку из двух взаимоисключающих кусков _no_such_value (текст
    # ветки "ничего не подходит" вместе с вручную приклеенным
    # "wider_search") — реальный каталог такое не выдаёт. Сценарий ниже —
    # реальный: серия YCB9N-40 (все 14 позиций — 1P+N), затем уточнение
    # "3P", которого в этой выборке нет вовсе, но есть близкая по духу
    # альтернатива. Это тот самый случай, где catalog_search.answer идёт в
    # _no_such_value БЕЗ wider (числовой ветки тут нет), и который ревью
    # воспроизвело как утечку в обычный листинг из 14 позиций.
    ctx = {"catalog_filters": {"series": "YCB9N-40"}}
    result = answer("3P", ctx)
    assert result.kind == "catalog"
    assert "такого в этой выборке нет" in result.text.lower()
    assert "найдено" not in result.text.lower()


def test_greeting_is_not_a_dead_end():
    result = answer("Привет")
    assert result.kind in {"reference", "escalate_support"}
    assert result.text.strip()


def test_greeting_and_empty_input_never_create_a_support_ticket():
    # Ruling S (fix round 1, задача 7): раньше "Привет" и "?" уходили в
    # escalate_support — каждое такое сообщение регистрировалось вопросом
    # №N, будило инженера уведомлением в Telegram и требовало ответа в
    # течение 3 рабочих дней. Приветствие и содержательно пустой ввод —
    # не вопрос, который нужно передавать техслужбе или менеджеру: у бота
    # есть на них собственный самостоятельный ответ, и эскалация здесь
    # была бы чистым мусором в очереди живого человека.
    greetings_and_junk = (
        "Привет", "Здравствуйте", "Здравствуй", "Добрый день", "Доброе утро",
        "Добрый вечер", "Здравия желаю", "hi", "Hello",
        "?", "!", "...", "??", "", "   ",
    )
    for question in greetings_and_junk:
        result = answer(question)
        assert result.kind not in {"escalate_support", "escalate_manager"}, (
            f"«{question}» породило эскалацию: {result.kind}"
        )
        assert result.text.strip(), f"«{question}» осталось без текста"


def test_greeting_prefix_does_not_swallow_the_real_question():
    # Ruling W (fix round 3, задача 7): уточнение Ruling S. Старая версия
    # ловила приветствие ЛЮБЫМ вхождением в сообщение —
    # answer_for_client("Добрый день, у вас есть офис в Москве?")
    # возвращала общую справку о боте и теряла настоящий вопрос, хотя
    # ответ на него в справочнике есть с этого же коммита. В деловой
    # переписке сообщение почти всегда начинается с приветствия, так что
    # это был не край, а типовой случай — и мягкий тупик ровно того
    # сорта, который вся эта задача существует, чтобы закрывать: такой
    # ответ засчитывался как самообслуживание и проходил порог 45, не
    # ответив на вопрос.
    result = answer("Добрый день, у вас есть офис в Москве?")
    assert result.kind == "reference"
    assert "127521" in result.text
    assert "Я бот CNC Electric" not in result.text

    # Приветствие само по себе (без остатка) по-прежнему даёт справку.
    for greeting in ("Привет", "Здравствуйте!"):
        result = answer(greeting)
        assert result.kind == "reference"
        assert "Я бот CNC Electric" in result.text

    # Приветствие перед коммерческим/replacement-вопросом отбрасывается,
    # а не подменяет ступень лестницы.
    result = answer("Здравствуйте, чем заменить ABB S203?")
    assert result.kind == "replacement"
