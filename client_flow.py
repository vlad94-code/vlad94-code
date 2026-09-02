# client_flow.py
"""Лестница ответа клиенту — пять ступеней, спека §6.

Логика вынесена из bot.py, чтобы её можно было прогнать корпусом вопросов
без Telegram и без сети (tests/test_cold_client.py). bot.py остаётся
тонким слоем: он берёт ClientAnswer и решает, какие кнопки подставить.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from catalog_search import (
    article_code,
    detail as catalog_detail,
    filter_products as catalog_filter_products,
    result_text as catalog_result_text,
)
from engines.router import route_local

# Приветствие в начале сообщения отбрасывается, а остаток идёт по
# лестнице как обычный вопрос (Ruling W, fix round 3 задачи 7 — уточнение
# собственного Ruling S). Старая версия ловила приветствие ЛЮБЫМ
# вхождением в сообщение: «Добрый день, у вас есть офис в Москве?»
# целиком проглатывался общей справкой о боте и терял реальный вопрос,
# хотя ответ на него в справочнике есть. В деловой переписке сообщение
# почти всегда начинается с приветствия — это был не край, а типовой
# случай, и мягкий тупик того же рода, ради закрытия которого существует
# вся эта задача.
#
# Правило теперь: приветствие даёт самостоятельный ответ (CLIENT_GREETING_TEXT,
# kind=reference) только если после его отбрасывания в сообщении не
# осталось ничего содержательного — сообщение было только приветствием
# или содержательно пустым вводом («?», «!», «...», пустая строка). Если
# приветствие стоит в начале, а дальше есть текст — приветствие
# отбрасывается молча, а остаток идёт по лестнице как обычно: артикул,
# коммерческий гейт, замена импорта, справочник — в этом порядке.
_GREETING_PHRASES = (
    "привет", "здравствуйте", "здравствуй", "добрый день", "доброе утро",
    "добрый вечер", "здравия желаю", "hi", "hello",
)
_GREETING_PREFIX_RE = re.compile(
    r"^\s*(?:" + "|".join(_GREETING_PHRASES) + r")\b"
    r"(?:[\s,.!:;-]+(?:" + "|".join(_GREETING_PHRASES) + r")\b)*"
    r"[\s,.!:;-]*",
    re.I,
)
_LETTER_OR_DIGIT_RE = re.compile(r"[a-zа-яё0-9]", re.I)


def _strip_leading_greeting(question: str) -> str:
    """Убрать приветствие из начала сообщения, если оно там есть, и
    вернуть остаток без ведущих пробелов/запятых. Приветствия в начале
    нет — сообщение возвращается без изменений."""
    return _GREETING_PREFIX_RE.sub("", question, count=1)


def _is_content_empty(text: str) -> bool:
    """Ввод без содержания: без единой буквы или цифры (например «?»,
    «!», «...», пустая строка) — в нём физически нет содержания, которое
    можно передать технической службе."""
    return not _LETTER_OR_DIGIT_RE.search(text)


# Тот же смысл, что у CLIENT_GREETING в bot.py (первый экран клиента), но
# не импортируется оттуда — bot.py переписывает задача 12, и модуль
# client_flow.py должен прогоняться корпусом вопросов независимо от него.
# Это не факт о компании, а описание самого бота: что он умеет и что можно
# спросить, поэтому источник не требует подтверждения инженером.
CLIENT_GREETING_TEXT = (
    "Здравствуйте! Я бот CNC Electric.\n\n"
    "Помогу подобрать оборудование и найти документы на него — пришлите "
    "точный артикул, покажу характеристики и цену: «B030524».\n"
    "Отвечу и на вопросы о компании, доставке, гарантии и сертификатах.\n\n"
    "Если готового ответа не найду — передам вопрос технической службе или "
    "менеджеру."
)

# Коммерческая тема уходит менеджеру независимо от того, нашлось ли
# что-то выше: у бота нет ни остатков для клиента, ни условий сделки
# (спека §6.3).
#
# Границы слов и отрицательные предпросмотры ниже чинят реальные ложные
# срабатывания внутри технических терминов того же каталога: "остаточная
# отключающая способность" (спека автомата) содержит "остат", "счётчик"
# (измерительное устройство — реальная категория товара) содержит "счет",
# а без границы "счёт"/"счет" срабатывали и внутри "расчёт" тока КЗ.
# "опт" без разбора ловил "оптический", "оптимальный", "оптопара",
# "оптрон" — тоже электронную лексику, а не намерение купить оптом.
_COMMERCIAL_RE = re.compile(
    r"склад|налич|\bостат(?!очн)|когда будет|срок поставк|отгруз|скидк|дешевле|"
    r"\bсчёт(?!чик)|\bсчет(?!чик)|договор|реквизит|оплат|купить|заказать|"
    r"цена на партию|\bопт(?!ическ|имальн|имизац|опар|рон|оэлектрон|оволокн)",
    re.I,
)

# Замена импортного аппарата: данных для подбора у системы нет, поэтому
# бот объясняет порядок, а не изображает поиск (спека §6.2).
#
# \bаналог(?!ов) — не просто граница слова: "аналоговый"/"аналоговое"/
# "аналоговым" (реальный термин про тип сигнала/выхода) всегда продолжается
# именно "аналогов", а не другой буквой, так что одного отрицательного
# предпросмотра хватает, чтобы не путать вопрос про тип выхода с просьбой
# подобрать замену.
_REPLACEMENT_RE = re.compile(
    r"заменить|замена|\bаналог(?!ов)|вместо|эквивалент|импортозамещ", re.I,
)
_FOREIGN_BRAND_RE = re.compile(
    r"\bABB\b|schneider|шнайдер|\bIEK\b|\bИЭК\b|legrand|леград|hager|siemens|"
    r"сименс|acti9|\bEKF\b|\bDEKraft\b|chint|\bTDM\b",
    re.I,
)

REPLACEMENT_TEXT = (
    "Подбор аналога импортного аппарата выполняет техническая служба CNC — "
    "по исходной модели и условиям применения.\n\n"
    "Наши модульные автоматы — серии YCB7 и YCB9, каталоги доступны по команде /catalog.\n\n"
    "Передать вопрос в техслужбу? Ответят в течение 3 рабочих дней."
)

MANAGER_INTRO = (
    "Наличие на складе, сроки, условия поставки и цену по вашему объёму "
    "подскажет менеджер."
)

# Ступень 5 — вопрос без ответа в локальных источниках. Пустой текст здесь
# был бы ровно тем тупиком, из-за которого корпус холодного клиента дал
# 0 ответов из 63 (см. брифинг задачи): «эскалация» обязана нести
# собственный текст, а не полагаться на то, что bot.py его придумает.
SUPPORT_ESCALATION_TEXT = (
    "Не нашёл точного ответа в базе знаний CNC. Передаю вопрос технической "
    "службе — ответят в течение 3 рабочих дней."
)


@dataclass(frozen=True)
class ClientAnswer:
    text: str
    kind: str
    article: str | None = None
    sources: tuple[str, ...] = ()


async def answer_for_client(question: str, context: dict) -> ClientAnswer:
    question = (question or "").strip()

    # Приветствие в начале отбрасываем ДО всего остального (Ruling W).
    # Если после отбрасывания ничего содержательного не осталось —
    # сообщение было только приветствием или пустым вводом, и это готовый
    # самостоятельный ответ. Если что-то осталось — это и есть настоящий
    # вопрос клиента: дальше он идёт по лестнице как обычно, начиная со
    # ступени 1 (артикул важнее того, что клиент написал перед ним).
    question = _strip_leading_greeting(question)
    if _is_content_empty(question):
        return ClientAnswer(text=CLIENT_GREETING_TEXT, kind="reference")

    # Ступень 1 — точный артикул.
    code = article_code(question)
    if code:
        card = catalog_detail(code, show_stock=False)
        if "не найден" not in card.lower():
            return ClientAnswer(text=card, kind="article", article=code)

    # Коммерческая тема — сразу к менеджеру, выше по лестнице не идём.
    if _COMMERCIAL_RE.search(question):
        return ClientAnswer(text=MANAGER_INTRO, kind="escalate_manager")

    # Замена импорта — честное объяснение вместо ложного «не найдено».
    if _REPLACEMENT_RE.search(question) and _FOREIGN_BRAND_RE.search(question):
        return ClientAnswer(text=REPLACEMENT_TEXT, kind="replacement")

    # Ступени 2–4: справочник, документы, каталог. Уровень 5 (ответы ИИ)
    # клиенту не отдаётся — route_local работает только на локальных
    # движках и до Claude не доходит по определению.
    local = await route_local(question, {"catalog_filters": context.get("catalog_filters", {})})
    if local.handled and local.text.strip():
        if local.engine_name in {"reference", "knowledge", "accessory_compat"}:
            return ClientAnswer(
                text=local.text,
                kind="reference",
                article=local.context_update.get("sole_article"),
                sources=tuple(local.sources),
            )

        # ProductEngine / ProductDetailEngine рисуют текст с show_stock=True
        # по умолчанию — это для сотрудников, которым остаток положен.
        # Клиенту эту строку отдавать нельзя (правило «остаток не
        # показываем никогда» шире буквы про catalog_search.detail). Ответ
        # перерисовываем заново из тех же структурированных данных с
        # show_stock=False — числа берутся из того же снимка каталога,
        # только с другим флагом отображения, а не правкой строки постфактум.
        #
        # Исключение — ветка "в этой выборке такого нет" (catalog_search
        # answer -> _no_such_value): она уже отвечает по существу (какой
        # характеристики не хватает, какой в выборке потолок, что взять
        # взамен) и остатка не печатает вовсе — result_text() пересобрал бы
        # её в обычный листинг по накопленным фильтрам, и клиент вместо
        # ответа получил бы список. "wider_search" эту ветку НЕ опознаёт:
        # оно ставится только когда в выборке не подходит вообще ничего, а
        # _no_such_value отвечает так же и на несовпадение по нечисловому
        # ключу, и на числовой ключ с найденным близким значением — оба
        # случая раньше молча перерисовывались в листинг. Опознаём явным
        # флагом от самого каталога: Answer.explains -> ProductEngine кладёт
        # "explains_selection" в context_update ровно и только для этой
        # ветки (engines/adapters.py: `if found.explains: update[...] = True`).
        if "explains_selection" in local.context_update:
            return ClientAnswer(
                text=local.text,
                kind="catalog",
                article=local.context_update.get("sole_article"),
                sources=tuple(local.sources),
            )

        article = local.context_update.get("sole_article")
        filters = local.context_update.get("catalog_filters")
        if article:
            catalog_text = catalog_detail(article, show_stock=False)
        elif filters:
            catalog_text = catalog_result_text(catalog_filter_products(filters), filters, show_stock=False)
        else:
            # Ни точного товара, ни фильтров — безопасно перерисовать
            # ответ нечем. Лучше отдать вопрос человеку, чем рискнуть
            # показать клиенту цифру склада из непроверенного текста.
            return ClientAnswer(text=SUPPORT_ESCALATION_TEXT, kind="escalate_support")
        return ClientAnswer(
            text=catalog_text,
            kind="catalog",
            article=article,
            sources=tuple(local.sources),
        )

    # Ступень 5 — вопрос уходит в техслужбу.
    return ClientAnswer(text=SUPPORT_ESCALATION_TEXT, kind="escalate_support")
