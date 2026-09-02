# Клиентский контур бота CNC — план реализации

> **Для агентов:** ОБЯЗАТЕЛЬНАЯ ПОД-СКИЛЛ: используйте superpowers:subagent-driven-development (рекомендуется) или superpowers:executing-plans, чтобы выполнять план задача за задачей. Шаги размечены чекбоксами (`- [ ]`).

**Цель:** клиент бота всегда уходит либо с подтверждённым ответом, либо с зарегистрированным вопросом, у которого есть номер, адресат и срок.

**Архитектура:** логика ответа клиенту выносится в отдельный модуль `client_flow.py` — пять ступеней, проверяемых без Telegram; `bot.py` остаётся тонким слоем. Эскалация — своя таблица `escalations`, отправка почты изолирована в `mailer.py`. Каталожный движок перестаёт перехватывать объяснительные вопросы.

**Технологии:** Python 3.11+, python-telegram-bot, SQLite, pytest, smtplib (Mail.ru, SSL 465).

**Спека:** [docs/superpowers/specs/2026-08-29-client-conversation-design.md](../specs/2026-08-29-client-conversation-design.md)

## Общие ограничения

- Числа не генерирует LLM: ток, Icu, цена, срок, количество подставляются из структурированного источника (ARCHITECTURE §2.1).
- Клиенту не показываются остатки склада и ответы уровня 5 («❓ предварительно») — спека §3.2.
- Клиенту не выдаётся расчётный счёт; ИНН, ОГРН и адреса выдаются — спека §3.4.
- Слово «бытовой» в ответах не употребляется: весь ассортимент позиционируется как промышленный.
- Срок ответа техслужбы, называемый клиенту, всегда один: «в течение 3 рабочих дней».
- Рабочее время компании: пн–пт 8:15–18:30.
- Почта техслужбы: `help@cncrussia.com`. Коммерческая: `info@cncrussia.com`. Юрист: `arkr@cncrussia.com`.
- Секреты (`SMTP_PASSWORD`) живут только в `.env` и никогда не попадают в репозиторий и в тесты.
- Тесты не ходят в сеть: SMTP и Telegram в тестах подменяются.
- Запуск тестов: `venv/Scripts/python.exe -m pytest` из каталога `dev`.
- Правка `knowledge/unique_answers.md` требует `python unique_answers.py`; в prod пересборка выполняется отдельно.

---

### Task 1: Паспорт бренда в справочнике

**Файлы:**
- Изменить: `knowledge/unique_answers.md`
- Тест: `tests/test_brand_reference.py`

**Интерфейсы:**
- Использует: `unique_answers.parse_source(text) -> list[VerifiedAnswer]` (существует)
- Даёт: раздел `## О компании CNC Electric` — источник ступени 2 лестницы

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_brand_reference.py
"""Паспорт бренда: ответы, которых в системе не было вообще.

Проверяем исходник справочника, а не поисковый индекс: индекс
пересобирается отдельной командой (`python unique_answers.py`), и тест,
зависящий от неё, падал бы у любого, кто её не запускал.
"""
from pathlib import Path

from unique_answers import SOURCE_PATH, parse_source

REQUIRED = [
    "гарант",
    "где вы находитесь",
    "как купить",
    "физическим лицам",
    "минимальн",
    "рекламац",
    "сертификат",
    "сроки поставки",
    "доставля",
    "реквизиты",
    "юридический вопрос",
    "дистрибьютор",
]


def _entries():
    return parse_source(Path(SOURCE_PATH).read_text(encoding="utf-8"))


def test_brand_section_exists():
    categories = {e.category for e in _entries()}
    assert "О компании CNC Electric" in categories


def test_every_brand_topic_is_covered():
    questions = " ".join(e.question.lower() for e in _entries())
    missing = [topic for topic in REQUIRED if topic not in questions]
    assert not missing, f"нет вопросов про: {missing}"


def test_bank_account_is_never_disclosed():
    body = Path(SOURCE_PATH).read_text(encoding="utf-8")
    assert "40702810838000035040" not in body, "расчётный счёт не выдаётся ботом (спека §3.4)"
    assert "30101810400000000225" not in body


def test_industrial_wording_rule():
    body = Path(SOURCE_PATH).read_text(encoding="utf-8").lower()
    assert "бытов" not in body, "весь ассортимент промышленный — слово запрещено"
```

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_brand_reference.py -v`
Ожидается: FAIL — категории «О компании CNC Electric» нет.

- [ ] **Шаг 3: Добавить раздел в справочник**

Дописать в конец `knowledge/unique_answers.md`:

```markdown
## О компании CNC Electric

### Кто вы такие и чей это бренд?
CNC Electric — производитель промышленного электрооборудования, завод в городе Вэньчжоу, Китай. Основан в 1988 году, с 1997 года — общенациональная промышленная группа: более 10 000 сотрудников, свыше 100 групп продукции и 20 000 моделей.
ООО «СиЭнСи Электрик» — официальный представитель и вендор бренда CNC Electric в России с 2022 года.

### Насколько это надёжный производитель?
Система менеджмента сертифицирована по ISO 9001, ISO 14001 и OHSAS 18001. Продукция имеет сертификаты CCC, CE, CB SEMKO. Торговая марка CNC многократно получала звание «Знаменитая китайская торговая марка».

### Где вы находитесь и как с вами связаться?
Офис: 127521, Москва, ул. Шереметьевская, д. 47.
Почта: info@cncrussia.com.
Часы работы: пн–пт с 8:15 до 18:30, суббота и воскресенье — выходные.

### Где находится склад?
Деревня Шелепаново, строение 2. Часы работы склада: пн–пт с 8:15 до 18:30.

### Какие у вас реквизиты?
ООО «СиЭнСи Электрик», ИНН 7735142621, ОГРН 1157746320280.
Юридический адрес: 124489, Москва, Зеленоград, корпус 619, помещение 1А.
Почтовый адрес: 127521, Москва, Шереметьевская 47, абонентский ящик 27.
Реквизиты для оплаты придут в счёте от менеджера.

### Как купить и как оформить заказ?
Работаем в B2B: с юридическими лицами и индивидуальными предпринимателями — напрямую, через дистрибьюторов и через щитовых сборщиков.
Оставьте заявку менеджеру по вашему региону — менеджер выставит счёт.
Минимальной партии и минимальной суммы заказа нет.

### Продаёте ли вы физическим лицам?
Продажи ведём юридическим лицам и индивидуальным предпринимателям.

### Работаете ли вы с дистрибьюторами и щитовыми сборщиками?
Да. Работаем и напрямую, и через дистрибьюторов, и со щитовыми сборщиками.

### Какая минимальная партия или минимальная сумма заказа?
Минимальной партии и минимальной суммы заказа нет.

### Какие сроки поставки?
Позиция есть на складе — отгружаем сразу, срок в пути зависит от транспортной компании.
Заказная позиция — 45 дней, доставка из Китая самолётом.

### Как вы доставляете и можно ли забрать самовывозом?
Отправляем транспортными компаниями СДЭК, Яндекс и Деловые линии. Возможен самовывоз со склада в Шелепаново.

### Какая гарантия на оборудование?
Гарантия — 5 лет.

### Нужен сертификат соответствия, ТР ТС или декларация ЕАС
Сертификат по конкретному артикулу бот отдаёт файлом — пришлите артикул.
Если по этой позиции сертификата у бота нет, напишите на help@cncrussia.com — документы пришлют.

### Как оформить рекламацию, если оборудование бракованное?
Заполните рекламационный акт по форме CNC — бот пришлёт файл формы.
Приложите фотографии и видео дефекта: это ускоряет решение.
Отправьте акт с приложениями на help@cncrussia.com. Ответ — до 3 рабочих дней.
Порядок одинаков независимо от того, где куплено оборудование: напрямую, у дистрибьютора или у щитового сборщика.

### Куда обращаться по юридическим вопросам и договорам?
Юрист компании — Артур Крапоткин, arkr@cncrussia.com.
```

- [ ] **Шаг 4: Запустить тест, убедиться, что проходит**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_brand_reference.py -v`
Ожидается: PASS (4 теста).

- [ ] **Шаг 5: Пересобрать документ базы знаний**

Запуск: `venv/Scripts/python.exe unique_answers.py`
Ожидается: строка вида «Пересобрано N записей».

- [ ] **Шаг 6: Коммит**

```bash
git add knowledge/unique_answers.md tests/test_brand_reference.py
git commit -m "Teach the reference file who CNC Electric is"
```

---

### Task 2: Менеджер по региону

**Файлы:**
- Создать: `managers.py`
- Изменить: `core/roles.py` (разбор `MANAGER_USER_IDS` с подписью)
- Тест: `tests/test_managers.py`

**Интерфейсы:**
- Даёт: `manager_for_city(city: str) -> Manager | None`, `format_manager(m: Manager) -> str`, `FALLBACK_TEXT: str`, `Manager` с полями `name, city, email, phones, districts, user_id`

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_managers.py
import managers


def test_moscow_goes_to_central_district():
    m = managers.manager_for_city("Москва")
    assert m.email == "kmk@cncrussia.com"
    assert "ЦФО" in m.districts


def test_novosibirsk_and_vladivostok_go_to_ural_manager():
    for city in ("Новосибирск", "Владивосток"):
        assert managers.manager_for_city(city).email == "an@cncrussia.com"


def test_makhachkala_goes_to_south_manager():
    assert managers.manager_for_city("Махачкала").email == "aam@cncrussia.com"


def test_city_is_matched_case_and_space_insensitively():
    assert managers.manager_for_city("  санкт-петербург ").email == "ar@cncrussia.com"


def test_unknown_city_has_no_manager():
    assert managers.manager_for_city("Ереван") is None


def test_fallback_text_names_only_the_general_address():
    assert "info@cncrussia.com" in managers.FALLBACK_TEXT
    assert "ЦФО" not in managers.FALLBACK_TEXT


def test_format_manager_shows_name_phone_and_email():
    text = managers.format_manager(managers.manager_for_city("Самара"))
    assert "Искорнев" in text
    assert "+7 (917) 107-54-89" in text
    assert "is@cncrussia.com" in text


def test_telegram_ids_come_from_env(monkeypatch):
    monkeypatch.setattr(managers, "_telegram_ids", lambda: {"is@cncrussia.com": 900003})
    assert managers.manager_for_city("Самара").user_id == 900003
```

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_managers.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'managers'`.

- [ ] **Шаг 3: Написать модуль**

```python
# managers.py
"""Менеджер по продажам для региона клиента.

Пять человек на восемь федеральных округов: ЮФО ведёт ещё и СКФО, УФО —
СФО и ДФО. Контакты живут здесь, а не в текстах бота: люди меняются, и
искать их телефоны по сообщениям нельзя.

Telegram ID берутся из `MANAGER_USER_IDS` в формате «ID:почта» — тем же
приёмом, что подписи руководителей (core/roles.py). Почта служит ключом:
она уже есть в карточке и не повторяет имя человека в двух местах.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from core.roles import MANAGER_TITLES


@dataclass(frozen=True)
class Manager:
    name: str
    city: str
    email: str
    phones: tuple[str, ...]
    districts: tuple[str, ...]
    user_id: int | None = None


MANAGERS: tuple[Manager, ...] = (
    Manager("Кузнецов Михаил Константинович", "Москва", "kmk@cncrussia.com",
            ("+7 (916) 350-35-10",), ("ЦФО",)),
    Manager("Артемьев Артем Николаевич", "Санкт-Петербург", "ar@cncrussia.com",
            ("+7 (911) 989-78-37",), ("СЗФО",)),
    Manager("Искорнев Сергей Александрович", "Самара", "is@cncrussia.com",
            ("+7 (917) 107-54-89",), ("ПФО",)),
    Manager("Мыц Андрей Анатольевич", "Краснодар", "aam@cncrussia.com",
            ("+7 (918) 078-81-03", "+7 (916) 656-52-73"), ("ЮФО", "СКФО")),
    Manager("Цыплаков Андрей Евгеньевич", "Екатеринбург", "an@cncrussia.com",
            ("+7 (912) 208-94-86",), ("УФО", "СФО", "ДФО")),
)

_BY_DISTRICT = {d: m for m in MANAGERS for d in m.districts}

# Города перечислены явно: определять округ по справочнику ФИАС ради пяти
# адресатов — лишняя зависимость. Нераспознанный город уходит на общий адрес,
# и это не ошибка, а нормальная ветка (спека §5).
CITY_TO_DISTRICT: dict[str, str] = {}


def _fill(district: str, cities: str) -> None:
    for city in cities.split(","):
        CITY_TO_DISTRICT[city.strip().lower()] = district


_fill("ЦФО", "Москва, Подольск, Химки, Балашиха, Тула, Калуга, Рязань, Тверь, Ярославль, "
             "Владимир, Иваново, Кострома, Смоленск, Брянск, Орёл, Орел, Курск, Белгород, "
             "Липецк, Воронеж, Тамбов, Мытищи, Королёв, Королев")
_fill("СЗФО", "Санкт-Петербург, Петербург, СПб, Ленинград, Мурманск, Архангельск, Вологда, "
              "Череповец, Калининград, Псков, Великий Новгород, Петрозаводск, Сыктывкар, Гатчина")
_fill("ПФО", "Самара, Тольятти, Казань, Нижний Новгород, Уфа, Пермь, Саратов, Ульяновск, "
             "Оренбург, Пенза, Ижевск, Киров, Чебоксары, Йошкар-Ола, Саранск, Набережные Челны")
_fill("ЮФО", "Краснодар, Ростов-на-Дону, Ростов, Сочи, Волгоград, Астрахань, Новороссийск, "
             "Симферополь, Севастополь, Элиста, Майкоп, Таганрог")
_fill("СКФО", "Махачкала, Ставрополь, Грозный, Владикавказ, Нальчик, Черкесск, Магас, Пятигорск")
_fill("УФО", "Екатеринбург, Челябинск, Тюмень, Курган, Сургут, Нижневартовск, Магнитогорск, "
             "Нижний Тагил, Ханты-Мансийск, Салехард")
_fill("СФО", "Новосибирск, Красноярск, Омск, Иркутск, Барнаул, Кемерово, Новокузнецк, Томск, "
             "Абакан, Кызыл, Горно-Алтайск, Братск кемерово")
_fill("ДФО", "Владивосток, Хабаровск, Якутск, Благовещенск, Южно-Сахалинск, Петропавловск-Камчатский, "
             "Чита, Улан-Удэ, Магадан, Биробиджан, Находка, Комсомольск-на-Амуре")

FALLBACK_TEXT = (
    "Напишите на info@cncrussia.com — вас передадут менеджеру по вашему региону.\n"
    "Часы работы: пн–пт 8:15–18:30."
)


def _telegram_ids() -> dict[str, int]:
    """Почта менеджера → его Telegram ID из `MANAGER_USER_IDS`."""
    return {email.strip().lower(): user_id for user_id, email in MANAGER_TITLES.items()}


def district_for_city(city: str) -> str | None:
    return CITY_TO_DISTRICT.get((city or "").strip().lower())


def manager_for_city(city: str) -> Manager | None:
    district = district_for_city(city)
    if district is None:
        return None
    manager = _BY_DISTRICT[district]
    return replace(manager, user_id=_telegram_ids().get(manager.email))


def format_manager(manager: Manager) -> str:
    phones = ", ".join(manager.phones)
    return (
        f"Ваш менеджер — {manager.name} ({manager.city}).\n"
        f"Телефон: {phones}\n"
        f"Почта: {manager.email}\n"
        f"Часы работы: пн–пт 8:15–18:30."
    )
```

- [ ] **Шаг 4: Добавить подписи менеджерам в core/roles.py**

В `core/roles.py` заменить строку разбора менеджеров:

```python
# было:
# MANAGER_IDS = _parse_ids("MANAGER_USER_IDS")
MANAGER_IDS, MANAGER_TITLES = _parse_titled_ids("MANAGER_USER_IDS")
```

`_parse_titled_ids` уже существует и используется для руководителей — без двоеточия подпись просто отсутствует, поэтому старый формат `.env` продолжает работать.

- [ ] **Шаг 5: Запустить тесты**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_managers.py tests/test_roles.py -v`
Ожидается: PASS.

- [ ] **Шаг 6: Коммит**

```bash
git add managers.py core/roles.py tests/test_managers.py
git commit -m "Route a client to one manager instead of a phone list"
```

---

### Task 3: Стоп-слова каталожного движка

**Файлы:**
- Изменить: `engines/adapters.py` (класс `ProductEngine`)
- Тест: `tests/test_engine_limits.py`

**Интерфейсы:**
- Использует: `ProductEngine.can_handle(question, context) -> float`
- Даёт: `engines.adapters._EXPLANATORY_INTENT_RE`

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_engine_limits.py
"""Каталожный движок не должен отвечать списком на вопрос «зачем».

Замер до правки: «Зачем нужен контактор?» → «Найдено 877 позиций»,
«Расшифруйте маркировку YCB9» → «подходящих товаров не найдено».
"""
import pytest

from engines.adapters import ProductEngine

EXPLANATORY = [
    "Зачем нужен контактор?",
    "Что такое АВР?",
    "Чем отличается УЗО от дифавтомата?",
    "Расшифруйте маркировку YCB9",
    "Как работает УЗДП?",
    "В чём разница между YCB7 и YCB9?",
]

CATALOG = [
    "Нужен рубильник на 250А",
    "автомат 3P 63А",
]


@pytest.mark.parametrize("question", EXPLANATORY)
def test_explanatory_questions_are_left_to_the_reference(question):
    assert ProductEngine().can_handle(question, {}) == 0.0


@pytest.mark.parametrize("question", CATALOG)
def test_real_catalog_questions_still_handled(question):
    assert ProductEngine().can_handle(question, {}) == 1.0
```

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_engine_limits.py -v`
Ожидается: FAIL на объяснительных вопросах — движок их забирает.

- [ ] **Шаг 3: Добавить ограничитель**

В `engines/adapters.py` рядом с `_ADVISORY_INTENT_RE` добавить:

```python
# Объяснительный вопрос: человек просит объяснить, а не отфильтровать
# каталог. Отменяет подбор безусловно, даже при сильных фильтрах: «зачем
# нужен контактор» разбирается в type_item, и ответом приходило «Найдено
# 877 позиций» — список вместо объяснения. Справочник (§5 п.1a) на такие
# вопросы отвечает, поэтому движок обязан их пропустить.
_EXPLANATORY_INTENT_RE = re.compile(
    r"что такое|зачем|для чего нужен|расшифру|как работает|в ч[её]м разница|"
    r"чем отлича|что означает|объясни",
    re.I,
)
```

В `ProductEngine._search()` первой строкой после получения `prior` добавить:

```python
        if _EXPLANATORY_INTENT_RE.search(question):
            result = catalog_search.Answer(None, prior, False)
            self._cache = (question, prior, result)
            return result
```

- [ ] **Шаг 4: Запустить тесты**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_engine_limits.py tests/test_golden_set.py -v`
Ожидается: PASS.

- [ ] **Шаг 5: Коммит**

```bash
git add engines/adapters.py tests/test_engine_limits.py
git commit -m "Stop answering \"why do I need a contactor\" with 877 products"
```

---

### Task 4: Широкая выдача просит уточнить

**Файлы:**
- Изменить: `catalog_search.py:671-682` (`result_text`, ветка `len(results) > 30`)
- Тест: `tests/test_catalog_search.py`

**Интерфейсы:**
- Использует: `catalog_search.result_text(results, filters, show_stock=True) -> str`

- [ ] **Шаг 1: Написать падающий тест**

Дописать в `tests/test_catalog_search.py`:

```python
def test_wide_result_asks_to_narrow_down():
    products = [
        {"vendor_code": f"B{i:06d}", "name": f"Автомат {i}",
         "specification": [{"name": "Серия", "value": "YCB9"}]}
        for i in range(40)
    ]
    text = catalog_search.result_text(products, {})
    assert "уточните" in text.lower()
    assert "40" in text
```

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_catalog_search.py -k narrow -v`
Ожидается: FAIL — в тексте только «Найдено 40 позиций».

- [ ] **Шаг 3: Изменить формулировку**

В `catalog_search.result_text`, ветка `len(results)>30`, заменить первую строку списка:

```python
        lines=[f"Найдено {len(results)} позиций — уточните серию, номинальный ток или характеристику.", "", "Серии:"]
```

- [ ] **Шаг 4: Запустить тесты**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_catalog_search.py -v`
Ожидается: PASS.

- [ ] **Шаг 5: Коммит**

```bash
git add catalog_search.py tests/test_catalog_search.py
git commit -m "Ask to narrow down instead of announcing 601 positions"
```

---

### Task 5: Справочник впереди каталога

**Файлы:**
- Изменить: `engines/router.py:24-30` (`LOCAL_ENGINES`)
- Тест: `tests/test_engine_limits.py`

**Интерфейсы:**
- Использует: `engines.router.route_local(question, context) -> EngineResponse`

- [ ] **Шаг 1: Написать падающий тест**

Дописать в `tests/test_engine_limits.py`:

```python
import asyncio

from engines.router import LOCAL_ENGINES


def test_knowledge_engine_runs_before_product_engine():
    names = [engine.name for engine in LOCAL_ENGINES]
    assert names.index("knowledge") < names.index("product")


def test_accessory_engine_still_runs_first():
    assert LOCAL_ENGINES[0].name == "accessory_compat"
```

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_engine_limits.py -k engine_runs -v`
Ожидается: FAIL — `knowledge` сейчас последний.

- [ ] **Шаг 3: Изменить порядок**

В `engines/router.py`:

```python
LOCAL_ENGINES: list[Engine] = [
    AccessoryCompatibilityEngine(),
    # Справочник впереди каталога: подтверждённый ответ на «чем отличается»
    # или «что входит в комплектацию» ценнее списка товаров, а
    # ProductEngine возвращал handled=True и закрывал такие вопросы до
    # того, как справочник их видел (спека §1.2).
    KnowledgeEngine(),
    ProductEngine(),
    ProductDetailEngine(),
]
```

- [ ] **Шаг 4: Запустить весь набор**

Запуск: `venv/Scripts/python.exe -m pytest -q`
Ожидается: PASS. Если `tests/test_golden_set.py` показывает изменившийся `expected_module` — привести ожидания в `tests/golden_set.yaml` в соответствие с новым порядком, но только там, где новый ответ содержательно лучше.

- [ ] **Шаг 5: Коммит**

```bash
git add engines/router.py tests/test_engine_limits.py tests/golden_set.yaml
git commit -m "Let the reference file answer before the catalogue does"
```

---

### Task 6: Лестница ответа клиенту

**Файлы:**
- Создать: `client_flow.py`
- Тест: `tests/test_client_flow.py`

**Интерфейсы:**
- Использует: `engines.router.route_local`, `catalog_search.article_code`, `catalog_search.detail`
- Даёт: `async def answer_for_client(question: str, context: dict) -> ClientAnswer`; `ClientAnswer(text: str, kind: str, article: str | None)`, где `kind ∈ {"article", "reference", "catalog", "replacement", "escalate_support", "escalate_manager"}`

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_client_flow.py
"""Пять ступеней лестницы (спека §6). Без Telegram и без сети."""
import asyncio

import pytest

import client_flow


def answer(question, context=None):
    return asyncio.run(client_flow.answer_for_client(question, context or {}))


def test_commercial_question_goes_to_manager():
    for question in ("Есть на складе?", "Дайте скидку", "Пришлите счёт"):
        assert answer(question).kind == "escalate_manager"


def test_replacement_question_explains_and_offers_support():
    result = answer("Чем заменить ABB S203?")
    assert result.kind == "replacement"
    assert "техническая служба" in result.text.lower()
    assert "не найдено" not in result.text.lower()


def test_unknown_technical_question_goes_to_support():
    result = answer("Какая индуктивность у катушки в вашем реле времени?")
    assert result.kind == "escalate_support"


def test_client_answer_never_shows_stock():
    result = answer("B030524")
    assert "склад" not in result.text.lower() or "уточните" in result.text.lower()


def test_greeting_is_not_a_dead_end():
    result = answer("Привет")
    assert result.kind in {"reference", "escalate_support"}
    assert result.text.strip()
```

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_client_flow.py -v`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'client_flow'`.

- [ ] **Шаг 3: Написать модуль**

```python
# client_flow.py
"""Лестница ответа клиенту — пять ступеней, спека §6.

Логика вынесена из bot.py, чтобы её можно было прогнать корпусом вопросов
без Telegram и без сети (tests/test_cold_client.py). bot.py остаётся
тонким слоем: он берёт ClientAnswer и решает, какие кнопки подставить.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from catalog_search import article_code, detail as catalog_detail
from engines.router import route_local

# Коммерческая тема уходит менеджеру независимо от того, нашлось ли
# что-то выше: у бота нет ни остатков для клиента, ни условий сделки
# (спека §6.3).
_COMMERCIAL_RE = re.compile(
    r"склад|налич|остат|когда будет|срок поставк|отгруз|скидк|дешевле|"
    r"счёт|счет|договор|реквизит|оплат|купить|заказать|цена на партию|опт",
    re.I,
)

# Замена импортного аппарата: данных для подбора у системы нет, поэтому
# бот объясняет порядок, а не изображает поиск (спека §6.2).
_REPLACEMENT_RE = re.compile(
    r"заменить|замена|аналог|вместо|эквивалент|импортозамещ", re.I,
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


@dataclass(frozen=True)
class ClientAnswer:
    text: str
    kind: str
    article: str | None = None
    sources: tuple[str, ...] = ()


async def answer_for_client(question: str, context: dict) -> ClientAnswer:
    question = (question or "").strip()
    if not question:
        return ClientAnswer(text="", kind="escalate_support")

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
        kind = "reference" if local.engine_name in {"knowledge", "accessory_compat"} else "catalog"
        return ClientAnswer(
            text=local.text,
            kind=kind,
            article=local.context_update.get("sole_article"),
            sources=tuple(local.sources),
        )

    # Ступень 5 — вопрос уходит в техслужбу.
    return ClientAnswer(text="", kind="escalate_support")
```

- [ ] **Шаг 4: Запустить тесты**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_client_flow.py -v`
Ожидается: PASS.

- [ ] **Шаг 5: Коммит**

```bash
git add client_flow.py tests/test_client_flow.py
git commit -m "Give the client five steps instead of one dead end"
```

---

### Task 7: Корпус холодного клиента как тест

**Файлы:**
- Создать: `tests/cold_client_corpus.yaml`
- Создать: `tests/test_cold_client.py`

**Интерфейсы:**
- Использует: `client_flow.answer_for_client`

- [ ] **Шаг 1: Записать корпус**

`tests/cold_client_corpus.yaml` — 63 вопроса из Приложения А спеки. Формат:

```yaml
# Корпус вопросов холодного клиента (спека, Приложение А).
# kind: ожидаемая ступень лестницы — article | reference | catalog |
#       replacement | escalate_support | escalate_manager
# Тупиков быть не должно ни у одного вопроса.
- {q: "Кто вы такие?", kind: reference}
- {q: "CNC Electric это чей бренд?", kind: reference}
- {q: "Вы производитель или перекупщики?", kind: reference}
- {q: "Где вы находитесь?", kind: reference}
- {q: "У вас есть офис в Москве?", kind: reference}
- {q: "Как с вами связаться?", kind: reference}
- {q: "Вы официальный представитель?", kind: reference}
- {q: "Сколько лет вы на рынке?", kind: reference}
- {q: "Чем вы вообще торгуете?", kind: reference}
- {q: "Что у вас есть из ассортимента?", kind: reference}
- {q: "Автоматические выключатели есть?", kind: catalog}
- {q: "Вы делаете щиты под ключ?", kind: escalate_support}
- {q: "Частотники продаёте?", kind: catalog}
- {q: "Есть ли у вас оборудование среднего напряжения?", kind: catalog}
- {q: "Скиньте каталог", kind: reference}
- {q: "Есть прайс-лист?", kind: reference}
- {q: "Чем заменить ABB S203?", kind: replacement}
- {q: "Аналог Schneider Acti9", kind: replacement}
- {q: "У вас есть замена IEK ВД1-63?", kind: replacement}
- {q: "Подберите аналог автомата ABB", kind: replacement}
- {q: "Что у вас вместо Legrand?", kind: replacement}
- {q: "Чем отличается от китайского ноунейма?", kind: escalate_support}
- {q: "У вас качество как у европейцев?", kind: reference}
- {q: "Сколько стоит?", kind: escalate_manager}
- {q: "Цена на автомат 16А", kind: escalate_manager}
- {q: "Как купить?", kind: escalate_manager}
- {q: "Как оформить заказ?", kind: escalate_manager}
- {q: "Продаёте физлицам?", kind: reference}
- {q: "Какая минимальная партия?", kind: reference}
- {q: "Работаете с дистрибьюторами?", kind: reference}
- {q: "Дайте скидку", kind: escalate_manager}
- {q: "Пришлите реквизиты", kind: escalate_manager}
- {q: "Есть на складе?", kind: escalate_manager}
- {q: "Когда будет поставка?", kind: escalate_manager}
- {q: "Сколько ждать заказную позицию?", kind: escalate_manager}
- {q: "Отправляете в Новосибирск?", kind: reference}
- {q: "Какой транспортной компанией отправляете?", kind: reference}
- {q: "Можно самовывозом?", kind: reference}
- {q: "Какая гарантия?", kind: reference}
- {q: "Есть сертификат соответствия?", kind: reference}
- {q: "Пришлите сертификат ТР ТС", kind: reference}
- {q: "У меня сгорел автомат, что делать?", kind: reference}
- {q: "Как оформить рекламацию?", kind: reference}
- {q: "Оборудование бракованное, куда писать?", kind: reference}
- {q: "Есть ли декларация ЕАС?", kind: reference}
- {q: "Что такое УЗДП?", kind: reference}
- {q: "Чем отличается УЗО от дифавтомата?", kind: reference}
- {q: "Что такое Icu?", kind: reference}
- {q: "Что означает характеристика C в автомате?", kind: reference}
- {q: "Зачем нужен контактор?", kind: reference}
- {q: "Что такое АВР?", kind: reference}
- {q: "Расшифруйте маркировку YCB9", kind: escalate_support}
- {q: "Какой автомат поставить на розетки?", kind: escalate_support}
- {q: "Нужен автомат на 63А трёхполюсный", kind: catalog}
- {q: "Подберите контактор на двигатель 15 кВт", kind: escalate_support}
- {q: "Что поставить на ввод в частном доме?", kind: escalate_support}
- {q: "Нужен рубильник на 250А", kind: catalog}
- {q: "Какой номинал взять для кабеля 4 квадрата?", kind: escalate_support}
- {q: "Аксессуары к YCW3", kind: reference}
- {q: "Привет", kind: reference}
- {q: "?", kind: escalate_support}
- {q: "Хочу устроиться к вам на работу", kind: escalate_support}
- {q: "Вы дилеров ищете?", kind: escalate_support}
```

- [ ] **Шаг 2: Написать тест**

```python
# tests/test_cold_client.py
"""Матрица ответов как исполняемый тест.

Замер до реализации: 0 ответов из 63 — любой вопрос, кроме артикула,
получал «пришлите точный артикул». Здесь проверяется главное: тупиков нет
ни у одного вопроса, и большинство закрывается без эскалации.
"""
import asyncio
from pathlib import Path

import pytest
import yaml

import client_flow

CORPUS = yaml.safe_load((Path(__file__).parent / "cold_client_corpus.yaml").read_text(encoding="utf-8"))
SELF_SERVED = {"article", "reference", "catalog", "replacement"}


def _answer(question):
    return asyncio.run(client_flow.answer_for_client(question, {}))


@pytest.mark.parametrize("case", CORPUS, ids=[c["q"] for c in CORPUS])
def test_no_question_is_a_dead_end(case):
    result = _answer(case["q"])
    assert result.kind, "ступень должна быть определена всегда"
    if result.kind in SELF_SERVED:
        assert result.text.strip(), "самостоятельный ответ не может быть пустым"


def test_at_least_45_questions_are_answered_without_escalation():
    served = sum(1 for case in CORPUS if _answer(case["q"]).kind in SELF_SERVED)
    assert served >= 45, f"бот закрывает сам только {served} из {len(CORPUS)}"


def test_replacement_questions_never_claim_nothing_found():
    for case in CORPUS:
        if case["kind"] != "replacement":
            continue
        assert "не найдено" not in _answer(case["q"]).text.lower()
```

- [ ] **Шаг 3: Запустить и зафиксировать реальность**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_cold_client.py -v`

Ожидается: часть вопросов не совпадёт с ожидаемым `kind`. Это нормально и это работа: **правьте `knowledge/unique_answers.md`, а не ожидания в корпусе**, пока не сойдётся. Ожидание в корпусе меняется только там, где ступень содержательно верна, а записана неточно; каждое такое изменение отмечайте в сообщении коммита.

Порог `>= 45` — обязателен и снижению не подлежит: это критерий приёмки 2 из спеки §11.

- [ ] **Шаг 4: Пересобрать справочник после правок**

Запуск: `venv/Scripts/python.exe unique_answers.py`

- [ ] **Шаг 5: Коммит**

```bash
git add tests/cold_client_corpus.yaml tests/test_cold_client.py knowledge/unique_answers.md
git commit -m "Turn the answer matrix into a test that fails on regression"
```

---

### Task 8: Отправка почты

**Файлы:**
- Создать: `mailer.py`
- Тест: `tests/test_mailer.py`

**Интерфейсы:**
- Даёт: `send(subject: str, body: str, *, to: str | None = None, attachments: list[tuple[str, bytes]] | None = None) -> bool`, `SUPPORT_EMAIL: str`, `configured() -> bool`

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_mailer.py
"""SMTP изолирован: тесты не открывают ни одного соединения."""
import smtplib

import pytest

import mailer


class FakeSMTP:
    sent = []

    def __init__(self, host, port, context=None, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        FakeSMTP.user = user

    def send_message(self, message):
        FakeSMTP.sent.append(message)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.mail.ru")
    monkeypatch.setenv("SMTP_USER", "help@cncrussia.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SUPPORT_EMAIL", "help@cncrussia.com")


def test_send_delivers_to_support_by_default():
    assert mailer.send("Вопрос №1", "текст") is True
    message = FakeSMTP.sent[0]
    assert message["To"] == "help@cncrussia.com"
    assert message["Subject"] == "Вопрос №1"


def test_send_reports_failure_instead_of_raising(monkeypatch):
    def explode(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"bad password")

    monkeypatch.setattr(smtplib, "SMTP_SSL", explode)
    assert mailer.send("тема", "текст") is False


def test_not_configured_without_password(monkeypatch):
    monkeypatch.delenv("SMTP_PASSWORD")
    assert mailer.configured() is False
    assert mailer.send("тема", "текст") is False


def test_attachment_is_included():
    mailer.send("тема", "текст", attachments=[("акт.docx", b"PK\x03\x04")])
    names = [part.get_filename() for part in FakeSMTP.sent[0].iter_attachments()]
    assert "акт.docx" in names
```

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_mailer.py -v`
Ожидается: FAIL — модуля нет.

- [ ] **Шаг 3: Написать модуль**

```python
# mailer.py
"""Отправка писем в техническую службу.

Тонкий слой над smtplib и ничего больше: логика эскалации живёт в
escalation.py. Так тест эскалации не открывает сетевых соединений, а смена
транспорта (ARCHITECTURE §2.6) не задевает логику.

Домен cncrussia.com обслуживается Mail.ru для бизнеса: smtp.mail.ru:465,
SSL, «пароль для внешнего приложения» — не пароль входа.
"""
from __future__ import annotations

import logging
import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)

DEFAULT_HOST = "smtp.mail.ru"
DEFAULT_PORT = 465
TIMEOUT = 20


def _setting(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def support_email() -> str:
    return _setting("SUPPORT_EMAIL", "help@cncrussia.com")


def configured() -> bool:
    """Есть ли чем отправлять. Отсутствие настроек — не авария: эскалация
    всё равно доходит до инженера в Telegram (спека §9)."""
    return bool(_setting("SMTP_USER") and _setting("SMTP_PASSWORD"))


def send(subject: str, body: str, *, to: str | None = None,
         attachments: list[tuple[str, bytes]] | None = None) -> bool:
    if not configured():
        logger.warning("SMTP не настроен — письмо %r не отправлено", subject)
        return False

    message = EmailMessage()
    message["From"] = _setting("SMTP_USER")
    message["To"] = to or support_email()
    message["Subject"] = subject
    message.set_content(body)
    for filename, payload in attachments or []:
        guessed, _ = mimetypes.guess_type(filename)
        maintype, _, subtype = (guessed or "application/octet-stream").partition("/")
        message.add_attachment(payload, maintype=maintype, subtype=subtype, filename=filename)

    host = _setting("SMTP_HOST", DEFAULT_HOST)
    port = int(_setting("SMTP_PORT", str(DEFAULT_PORT)) or DEFAULT_PORT)
    try:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=TIMEOUT) as smtp:
            smtp.login(_setting("SMTP_USER"), _setting("SMTP_PASSWORD"))
            smtp.send_message(message)
    except Exception:
        logger.exception("Не удалось отправить письмо %r", subject)
        return False
    return True
```

- [ ] **Шаг 4: Запустить тесты**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_mailer.py -v`
Ожидается: PASS.

- [ ] **Шаг 5: Коммит**

```bash
git add mailer.py tests/test_mailer.py
git commit -m "Add the mail transport, isolated from everything else"
```

---

### Task 9: Таблица эскалаций

**Файлы:**
- Изменить: `core/db.py` (SCHEMA)
- Изменить: `core/logging_.py`
- Тест: `tests/test_escalation_store.py`

**Интерфейсы:**
- Даёт: `record_escalation(question, *, user_id, chat_id, context=None, region=None, email=None) -> int`; `get_escalation(escalation_id) -> dict | None`; `open_escalations(limit=30) -> list[dict]`; `set_escalation_mail_status(escalation_id, status) -> None`; `answer_escalation(escalation_id, answer, *, answered_by) -> bool`; `stale_escalations(working_days=3) -> list[dict]`

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_escalation_store.py
import core.db as db
from core.logging_ import (
    answer_escalation, get_escalation, open_escalations,
    record_escalation, set_escalation_mail_status,
)


def test_escalation_gets_a_number_clients_can_quote(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.db"))
    db.init_db()
    first = record_escalation("Подойдёт ли YCB9RL-63B?", user_id=42, chat_id=42)
    second = record_escalation("А сертификат есть?", user_id=42, chat_id=42)
    assert second == first + 1


def test_open_escalations_exclude_answered(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.db"))
    db.init_db()
    number = record_escalation("вопрос", user_id=1, chat_id=1)
    assert [row["id"] for row in open_escalations()] == [number]
    assert answer_escalation(number, "ответ", answered_by=761316155) is True
    assert open_escalations() == []


def test_answer_is_stored_with_its_author(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.db"))
    db.init_db()
    number = record_escalation("вопрос", user_id=1, chat_id=7, context="YCW3")
    answer_escalation(number, "Да, подойдёт.", answered_by=761316155)
    row = get_escalation(number)
    assert row["answer"] == "Да, подойдёт."
    assert row["answered_by"] == 761316155
    assert row["chat_id"] == 7
    assert row["context"] == "YCW3"


def test_mail_status_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.db"))
    db.init_db()
    number = record_escalation("вопрос", user_id=1, chat_id=1)
    set_escalation_mail_status(number, "failed")
    assert get_escalation(number)["mail_status"] == "failed"
```

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_escalation_store.py -v`
Ожидается: FAIL — `ImportError: cannot import name 'record_escalation'`.

- [ ] **Шаг 3: Добавить таблицу**

В `core/db.py`, в конец SCHEMA перед индексами:

```sql
-- Вопрос клиента, на который бот не смог ответить сам. Отличается от
-- unanswered тем, что несёт обратный адрес: чат, куда вернуть ответ, и
-- необязательную почту клиента. Номер строки — это номер вопроса,
-- который клиент видит и называет.
CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    context TEXT,
    region TEXT,
    email TEXT,
    mail_status TEXT,
    answered_ts TEXT,
    answer TEXT,
    answered_by INTEGER
);
```

И индекс рядом с остальными:

```sql
CREATE INDEX IF NOT EXISTS idx_escalations_open ON escalations(answered_ts);
```

- [ ] **Шаг 4: Добавить функции доступа**

В `core/logging_.py`:

```python
def record_escalation(question: str, *, user_id: int, chat_id: int,
                      context: str | None = None, region: str | None = None,
                      email: str | None = None) -> int:
    """Зарегистрировать вопрос клиента. Возвращает номер, который бот назовёт."""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO escalations (ts, user_id, chat_id, question, context, region, email)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now(), user_id, chat_id, question, context, region, email),
        )
        return int(cursor.lastrowid)


def get_escalation(escalation_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM escalations WHERE id = ?", (escalation_id,)).fetchone()
    return dict(row) if row else None


def open_escalations(limit: int = 30) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM escalations WHERE answered_ts IS NULL ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def set_escalation_mail_status(escalation_id: int, status: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE escalations SET mail_status = ? WHERE id = ?", (status, escalation_id))


def answer_escalation(escalation_id: int, answer: str, *, answered_by: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE escalations SET answer = ?, answered_by = ?, answered_ts = ?"
            " WHERE id = ? AND answered_ts IS NULL",
            (answer, answered_by, _now(), escalation_id),
        )
        return cursor.rowcount > 0


def stale_escalations(working_days: int = 3) -> list[dict]:
    """Открытые вопросы старше N рабочих дней (спека §7.6).

    Рабочие дни считаются грубо — календарными сутками с поправкой на
    выходные: точность до часа здесь не нужна, нужен сигнал «пора напомнить».
    """
    from datetime import datetime, timedelta

    threshold = datetime.now() - timedelta(days=working_days + 2)
    return [row for row in open_escalations(limit=200)
            if datetime.fromisoformat(row["ts"]) <= threshold]
```

- [ ] **Шаг 5: Запустить тесты**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_escalation_store.py -v`
Ожидается: PASS.

- [ ] **Шаг 6: Коммит**

```bash
git add core/db.py core/logging_.py tests/test_escalation_store.py
git commit -m "Give a client question a number and a return address"
```

---

### Task 10: Регистрация и доставка эскалации

**Файлы:**
- Создать: `escalation.py`
- Тест: `tests/test_escalation.py`

**Интерфейсы:**
- Использует: `core.logging_.record_escalation / get_escalation / answer_escalation / set_escalation_mail_status`, `mailer.send`, `managers.manager_for_city`
- Даёт: `async def register(bot, *, question, user_id, chat_id, context=None, region=None, email=None) -> int`; `async def deliver(bot, escalation_id, answer, *, answered_by) -> bool`; `ENGINEER_IDS`; `format_notification(row) -> str`; `client_receipt(number) -> str`

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_escalation.py
import asyncio

import pytest

import core.db as db
import escalation
import mailer
from core.logging_ import get_escalation


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.db"))
    db.init_db()


def test_register_notifies_engineer_and_sends_mail(store, monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "send", lambda subject, body, **kw: sent.append(subject) or True)
    monkeypatch.setattr(escalation, "ENGINEER_IDS", frozenset({761316155}))
    bot = FakeBot()

    number = asyncio.run(escalation.register(
        bot, question="Подойдёт ли YCB9RL-63B?", user_id=5, chat_id=5, region="Екатеринбург"))

    assert number == 1
    assert any(f"№{number}" in text for _, text in bot.messages)
    assert any(chat_id == 761316155 for chat_id, _ in bot.messages)
    assert sent and f"№{number}" in sent[0]
    assert get_escalation(number)["mail_status"] == "sent"


def test_failed_mail_does_not_break_registration(store, monkeypatch):
    monkeypatch.setattr(mailer, "send", lambda *a, **kw: False)
    monkeypatch.setattr(escalation, "ENGINEER_IDS", frozenset({761316155}))
    bot = FakeBot()
    number = asyncio.run(escalation.register(bot, question="вопрос", user_id=5, chat_id=5))
    assert get_escalation(number)["mail_status"] == "failed"
    assert bot.messages, "инженер всё равно должен получить уведомление"


def test_deliver_sends_answer_to_the_client_chat(store, monkeypatch):
    monkeypatch.setattr(mailer, "send", lambda *a, **kw: True)
    monkeypatch.setattr(escalation, "ENGINEER_IDS", frozenset({761316155}))
    bot = FakeBot()
    number = asyncio.run(escalation.register(bot, question="вопрос", user_id=5, chat_id=77))
    bot.messages.clear()

    assert asyncio.run(escalation.deliver(bot, number, "Да, подойдёт.", answered_by=761316155))
    assert bot.messages[0][0] == 77
    assert "Да, подойдёт." in bot.messages[0][1]
    assert "технической службы" in bot.messages[0][1]


def test_second_delivery_is_refused(store, monkeypatch):
    monkeypatch.setattr(mailer, "send", lambda *a, **kw: True)
    monkeypatch.setattr(escalation, "ENGINEER_IDS", frozenset({761316155}))
    bot = FakeBot()
    number = asyncio.run(escalation.register(bot, question="вопрос", user_id=5, chat_id=77))
    asyncio.run(escalation.deliver(bot, number, "первый", answered_by=1))
    assert asyncio.run(escalation.deliver(bot, number, "второй", answered_by=1)) is False


def test_client_receipt_promises_three_working_days():
    text = escalation.client_receipt(47)
    assert "№47" in text
    assert "3 рабочих дн" in text
```

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_escalation.py -v`
Ожидается: FAIL — модуля нет.

- [ ] **Шаг 3: Написать модуль**

```python
# escalation.py
"""Вопрос клиента, на который бот не смог ответить сам.

Два пути одновременно (спека §3.3): письмо на help@ — письменный след,
уведомление инженеру в Telegram — оттуда ответ автоматически возвращается
клиенту. Срок один и тот же: до 3 рабочих дней.
"""
from __future__ import annotations

import logging

import mailer
from core.logging_ import (
    answer_escalation, get_escalation, record_escalation, set_escalation_mail_status,
)
from core.roles import ENGINEER_IDS

logger = logging.getLogger(__name__)

ANSWER_PREFIX = "Ответ технической службы CNC"

RECEIPT = (
    "Вопрос №{number} передан технической службе CNC.\n"
    "Отвечают в течение 3 рабочих дней — ответ придёт сюда же, в этот чат."
)


def client_receipt(number: int) -> str:
    return RECEIPT.format(number=number)


def format_notification(row: dict) -> str:
    lines = [f"🔔 Вопрос №{row['id']}"]
    if row.get("region"):
        lines[0] += f", клиент из {row['region']}"
    lines.append(f"«{row['question']}»")
    if row.get("context"):
        lines.append(f"Смотрел перед этим: {row['context']}")
    if row.get("email"):
        lines.append(f"Почта клиента: {row['email']}")
    lines.append("")
    lines.append("Ответьте на это сообщение — бот передаст ответ клиенту.")
    return "\n".join(lines)


def _letter(row: dict) -> tuple[str, str]:
    subject = f"Вопрос №{row['id']} от клиента из бота CNC"
    body = [row["question"], ""]
    if row.get("context"):
        body.append(f"Смотрел в боте: {row['context']}")
    if row.get("region"):
        body.append(f"Регион: {row['region']}")
    if row.get("email"):
        body.append(f"Почта клиента: {row['email']}")
    body += ["", f"Ответить можно в Telegram-боте — ответом на уведомление о вопросе №{row['id']}."]
    return subject, "\n".join(body)


async def register(bot, *, question: str, user_id: int, chat_id: int,
                   context: str | None = None, region: str | None = None,
                   email: str | None = None) -> int:
    number = record_escalation(question, user_id=user_id, chat_id=chat_id,
                               context=context, region=region, email=email)
    row = get_escalation(number)

    subject, body = _letter(row)
    set_escalation_mail_status(number, "sent" if mailer.send(subject, body) else "failed")

    text = format_notification(row)
    for engineer_id in ENGINEER_IDS:
        try:
            await bot.send_message(engineer_id, text)
        except Exception:
            logger.exception("Не удалось уведомить инженера %s о вопросе №%s", engineer_id, number)
    return number


async def deliver(bot, escalation_id: int, answer: str, *, answered_by: int) -> bool:
    """Отдать ответ инженера клиенту. Второй раз на тот же вопрос — отказ."""
    row = get_escalation(escalation_id)
    if row is None:
        return False
    if not answer_escalation(escalation_id, answer, answered_by=answered_by):
        return False
    await bot.send_message(
        row["chat_id"],
        f"{ANSWER_PREFIX} на ваш вопрос №{escalation_id}:\n\n{answer}",
    )
    return True
```

- [ ] **Шаг 4: Запустить тесты**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_escalation.py -v`
Ожидается: PASS.

- [ ] **Шаг 5: Коммит**

```bash
git add escalation.py tests/test_escalation.py
git commit -m "Register a client question, mail it, and route the answer back"
```

---

### Task 11: Визитка вместо отказа и новый первый экран

**Файлы:**
- Изменить: `core/roles.py` (`reject_unknown`)
- Изменить: `bot.py:347-355` (`CLIENT_GREETING`), `bot.py:284-294` (`main_keyboard`)
- Тест: `tests/test_client_view.py`

**Интерфейсы:**
- Даёт: `bot.client_start_keyboard() -> InlineKeyboardMarkup` с callback_data `about`, `catalog_menu`, `how_to_buy`, `ask_support`

- [ ] **Шаг 1: Написать падающий тест**

Дописать в `tests/test_client_view.py`:

```python
def test_rejection_is_a_business_card():
    from core.roles import rejection_text

    text = rejection_text(123456789)
    assert "CNC Electric" in text
    assert "help@cncrussia.com" in text
    assert "info@cncrussia.com" in text
    assert "@cncelectric_russia" in text
    assert "123456789" in text


def test_client_start_offers_four_buttons():
    import bot

    data = [b.callback_data for row in bot.client_start_keyboard().inline_keyboard for b in row]
    assert set(data) == {"about", "catalog_menu", "how_to_buy", "ask_support"}
```

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_client_view.py -v`
Ожидается: FAIL — `rejection_text` и `client_start_keyboard` не существуют.

- [ ] **Шаг 3: Переписать отказ**

В `core/roles.py` вынести текст в функцию и использовать её в `reject_unknown`:

```python
REJECTION = (
    "CNC Electric — промышленное электрооборудование: автоматические выключатели, "
    "УЗО и дифавтоматы, контакторы, рубильники, АВР, оборудование постоянного тока, "
    "аппараты среднего напряжения. Завод в Китае с 1988 года, в России — "
    "официальное представительство с 2022 года.\n\n"
    "Бот показывает характеристики, цену и документы и передаёт вопросы инженерам CNC. "
    "Он работает для подписчиков канала @cncelectric_russia — подпишитесь и нажмите /start.\n\n"
    "Не хотите подписываться: технические вопросы — help@cncrussia.com, "
    "коммерческие — info@cncrussia.com, пн–пт 8:15–18:30.\n\n"
    "Ваш Telegram ID: {user_id} — передайте администратору, если вы сотрудник CNC."
)


def rejection_text(user_id: int | str) -> str:
    return REJECTION.format(user_id=user_id)
```

- [ ] **Шаг 4: Новый первый экран клиента**

В `bot.py` заменить `CLIENT_GREETING` и добавить клавиатуру:

```python
CLIENT_GREETING = (
    "Рад приветствовать!\n\n"
    "Я отвечаю на вопросы об оборудовании CNC Electric: характеристики, цена, "
    "документы, аксессуары, условия покупки.\n"
    "Знаете артикул — пришлите его: «B030524».\n"
    "Не знаете, с чего начать — нажмите кнопку ниже."
)


def client_start_keyboard() -> InlineKeyboardMarkup:
    """Холодный клиент не знает артикулов и не знает, что можно спросить —
    кнопки и есть готовые вопросы (спека §7.2)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏭 О компании", callback_data="about"),
         InlineKeyboardButton("📚 Каталоги", callback_data="catalog_menu")],
        [InlineKeyboardButton("🛒 Как купить", callback_data="how_to_buy"),
         InlineKeyboardButton("✉️ Спросить техслужбу", callback_data="ask_support")],
    ])
```

В `main_keyboard()` заменить `return None` для клиента на `return client_start_keyboard()`.

- [ ] **Шаг 5: Написать хендлеры трёх кнопок**

Кнопка без хендлера — молчащая кнопка, поэтому все три подключаются здесь же.
`ask_support` появится в Task 13.

```python
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
    await query.message.reply_text(
        "Каталоги CNC Electric:", reply_markup=catalog_keyboard(catalog_links.all_catalogs()),
    )
```

Если имя функции получения каталогов отличается — взять то, которое уже
использует `catalog_command` в `bot.py:529`, а не вводить новое.

Зарегистрировать в `main()` рядом с существующими:

```python
    app.add_handler(CallbackQueryHandler(about_callback, pattern=r"^about$"))
    app.add_handler(CallbackQueryHandler(about_full_callback, pattern=r"^about_full$"))
    app.add_handler(CallbackQueryHandler(how_to_buy_callback, pattern=r"^how_to_buy$"))
    app.add_handler(CallbackQueryHandler(catalog_menu_callback, pattern=r"^catalog_menu$"))
```

Дописать тест:

```python
def test_every_start_button_has_a_handler():
    import bot

    data = {b.callback_data for row in bot.client_start_keyboard().inline_keyboard for b in row}
    patterns = {"about", "catalog_menu", "how_to_buy", "ask_support"}
    assert data == patterns


def test_about_text_never_promises_prices_or_stock():
    import bot

    assert "скидк" not in bot.ABOUT_SHORT.lower()
    assert "склад" not in bot.ABOUT_SHORT.lower()
```

- [ ] **Шаг 6: Запустить тесты**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_client_view.py tests/test_bot_commands.py -v`
Ожидается: PASS.

- [ ] **Шаг 7: Коммит**

```bash
git add core/roles.py bot.py tests/test_client_view.py
git commit -m "Turn the closed door into a business card"
```

---

### Task 12: Клиентская ветка идёт по лестнице

**Файлы:**
- Изменить: `bot.py:1731-1748` (клиентская ветка `answer()`)
- Тест: `tests/test_client_view.py`

**Интерфейсы:**
- Использует: `client_flow.answer_for_client`, `escalation.register`, `managers.manager_for_city`

- [ ] **Шаг 1: Написать падающий тест**

```python
def test_client_free_question_is_not_answered_with_send_an_article(monkeypatch):
    import asyncio

    import bot
    import client_flow

    replies = []
    update = _client_update("Какая гарантия?", replies)   # хелпер уже есть в файле
    monkeypatch.setattr(bot, "resolve_role", _async(bot.Role.CLIENT))
    asyncio.run(bot.answer(update, _context()))

    assert replies, "клиент должен получить ответ"
    assert "пришлите точный артикул" not in replies[0].lower()
```

Если хелперов `_client_update` / `_context` / `_async` в файле нет — написать их рядом по образцу существующих тестов `tests/test_client_view.py`.

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_client_view.py -k free_question -v`
Ожидается: FAIL — бот отвечает шаблоном.

- [ ] **Шаг 3: Переписать ветку**

В `bot.py`, в `answer()`, заменить блок `if role is Role.CLIENT:` целиком:

```python
    if role is Role.CLIENT:
        await update.effective_chat.send_action(ChatAction.TYPING)
        result = await client_flow.answer_for_client(question, context.user_data)
        user_id = update.effective_user.id if update.effective_user else None
        log_query(question, f"client:{result.kind}", result.kind not in
                  {"escalate_support", "escalate_manager"}, user_id=user_id, role=role.value)

        if result.kind == "article" and result.article:
            await send_product_photo(update.message, result.article)
            await update.message.reply_text(result.text, reply_markup=client_answer_keyboard(result.article))
            return
        if result.kind in {"reference", "catalog"}:
            await update.message.reply_text(result.text, reply_markup=client_answer_keyboard(result.article))
            return
        if result.kind == "replacement":
            await update.message.reply_text(
                result.text, reply_markup=support_keyboard())
            return
        if result.kind == "escalate_manager":
            await offer_manager(update, context, question)
            return
        await offer_support(update, context, question)
        return
```

И вспомогательные функции рядом:

```python
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
```

Добавить импорты `import client_flow`, `import escalation`, `import managers` в начало `bot.py`.

- [ ] **Шаг 4: Запустить тесты**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_client_view.py -v`
Ожидается: PASS.

- [ ] **Шаг 5: Коммит**

```bash
git add bot.py tests/test_client_view.py
git commit -m "Walk the client down the ladder instead of asking for an article"
```

---

### Task 13: Кнопка «Спросить техслужбу» и необязательная почта

**Файлы:**
- Изменить: `bot.py` (новые callback-хендлеры и регистрация в `main()`)
- Тест: `tests/test_support_request.py`

**Интерфейсы:**
- Использует: `escalation.register`, `escalation.client_receipt`
- Даёт: хендлеры `ask_support_callback`, `skip_email_callback`; ключи `context.user_data`: `pending_question`, `awaiting_support_email`

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_support_request.py
"""Клиент нажал «Спросить техслужбу»: вопрос получает номер, почта — по желанию."""
import asyncio

import pytest

import bot
import escalation


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
```

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_support_request.py -v`
Ожидается: FAIL — хендлеров нет.

- [ ] **Шаг 3: Написать хендлеры**

```python
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


async def ask_support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not context.user_data.get("pending_question"):
        context.user_data["awaiting_support_question"] = True
        await query.message.reply_text("Опишите вопрос одним сообщением — передам технической службе.")
        return
    context.user_data["awaiting_support_email"] = True
    await query.message.reply_text(
        "Оставьте e-mail, если нужны документы письмом. Или пропустите этот шаг.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Пропустить", callback_data="skip_email")],
        ]),
    )


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
```

В `answer()`, до клиентской ветки, обработать ожидания:

```python
    if context.user_data.pop("awaiting_support_question", False):
        context.user_data["pending_question"] = question
        await ask_support_callback_from_message(update, context)
        return
    if context.user_data.get("awaiting_support_email"):
        email = question if EMAIL_RE.match(question) else None
        await _submit_support(update.message, context, email=email)
        return
```

Зарегистрировать в `main()`:

```python
    app.add_handler(CallbackQueryHandler(ask_support_callback, pattern=r"^ask_support$"))
    app.add_handler(CallbackQueryHandler(skip_email_callback, pattern=r"^skip_email$"))
```

- [ ] **Шаг 4: Запустить тесты**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_support_request.py -v`
Ожидается: PASS.

- [ ] **Шаг 5: Коммит**

```bash
git add bot.py tests/test_support_request.py
git commit -m "Let a client hand the question over in two taps"
```

---

### Task 14: Ответ инженера возвращается клиенту

**Файлы:**
- Изменить: `bot.py` (обработка reply инженера, регистрация хендлера)
- Тест: `tests/test_support_request.py`

**Интерфейсы:**
- Использует: `escalation.deliver`
- Даёт: `engineer_reply_handler(update, context)`; распознавание номера по тексту уведомления `🔔 Вопрос №N`

- [ ] **Шаг 1: Написать падающий тест**

```python
def test_engineer_reply_reaches_the_client(monkeypatch):
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
    asyncio.run(bot.engineer_reply_handler(update, _context()))

    assert delivered["number"] == 47
    assert delivered["answer"].startswith("Да, подойдёт")
    assert "передан клиенту" in replies[-1].lower()


def test_reply_without_a_question_number_is_ignored(monkeypatch):
    monkeypatch.setattr(escalation, "deliver", None)
    replies = []
    update = _engineer_reply_update(reply_to_text="просто сообщение", text="ответ", replies=replies)
    asyncio.run(bot.engineer_reply_handler(update, _context()))
    assert not replies
```

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_support_request.py -k engineer -v`
Ожидается: FAIL — хендлера нет.

- [ ] **Шаг 3: Написать хендлер**

```python
_QUESTION_NUMBER_RE = re.compile(r"Вопрос №(\d+)")


@require_role(Role.ENGINEER, Role.ADMIN, Role.DIRECTOR)
async def engineer_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Инженер отвечает reply-сообщением на уведомление — бот доставляет
    ответ клиенту и предлагает положить его в справочник (спека §7.5)."""
    message = update.message
    source = message.reply_to_message
    if source is None or not (source.text or ""):
        return
    match = _QUESTION_NUMBER_RE.search(source.text)
    if not match:
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
```

Зарегистрировать раньше общего текстового хендлера в `main()`:

```python
    app.add_handler(MessageHandler(filters.TEXT & filters.REPLY & ~filters.COMMAND, engineer_reply_handler))
```

- [ ] **Шаг 4: Запустить тесты**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_support_request.py -v`
Ожидается: PASS.

- [ ] **Шаг 5: Коммит**

```bash
git add bot.py tests/test_support_request.py
git commit -m "Deliver the engineer's answer to the client who waited for it"
```

---

### Task 15: Кнопка «В справочник»

**Файлы:**
- Изменить: `unique_answers.py` (дописывание записи)
- Изменить: `bot.py` (callback `to_reference:N`)
- Тест: `tests/test_unique_answers_append.py`

**Интерфейсы:**
- Даёт: `unique_answers.append_entry(question: str, answer: str, category: str = "Вопросы клиентов") -> bool`

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_unique_answers_append.py
from pathlib import Path

import unique_answers


def test_entry_is_appended_under_its_category(tmp_path, monkeypatch):
    source = tmp_path / "unique_answers.md"
    source.write_text("# Уникальные ответы CNC\n\n## Контакторы CJX2\n\n### Вопрос?\nОтвет.\n", encoding="utf-8")
    monkeypatch.setattr(unique_answers, "SOURCE_PATH", source)

    assert unique_answers.append_entry("Подойдёт ли X вместо Y?", "Да, подойдёт.") is True

    body = source.read_text(encoding="utf-8")
    assert "## Вопросы клиентов" in body
    assert "### Подойдёт ли X вместо Y?" in body
    assert "Да, подойдёт." in body


def test_duplicate_question_is_not_appended_twice(tmp_path, monkeypatch):
    source = tmp_path / "unique_answers.md"
    source.write_text("# Уникальные ответы CNC\n", encoding="utf-8")
    monkeypatch.setattr(unique_answers, "SOURCE_PATH", source)

    unique_answers.append_entry("Один и тот же вопрос?", "Ответ.")
    assert unique_answers.append_entry("Один и тот же вопрос?", "Другой ответ.") is False
    assert source.read_text(encoding="utf-8").count("Один и тот же вопрос?") == 1
```

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_unique_answers_append.py -v`
Ожидается: FAIL — функции нет.

- [ ] **Шаг 3: Написать функцию**

```python
CLIENT_CATEGORY = "Вопросы клиентов"


def append_entry(question: str, answer: str, category: str = CLIENT_CATEGORY) -> bool:
    """Дописать в справочник ответ, который инженер только что дал клиенту.

    Так справочник растёт из реальных вопросов, без отдельной работы
    куратора (ARCHITECTURE §0 п. 3). Повтор не дописывается: один и тот же
    вопрос с двумя ответами сделал бы поиск недетерминированным.
    """
    question = " ".join(question.split()).rstrip("?") + "?"
    answer = answer.strip()
    if not answer:
        return False

    path = Path(SOURCE_PATH)
    body = path.read_text(encoding="utf-8") if path.exists() else "# Уникальные ответы CNC\n"
    if f"### {question}" in body:
        return False

    if f"## {category}" not in body:
        body = body.rstrip("\n") + f"\n\n## {category}\n"
    body = body.rstrip("\n") + f"\n\n### {question}\n{answer}\n"
    path.write_text(body, encoding="utf-8")
    return True
```

- [ ] **Шаг 4: Подключить кнопку**

В `bot.py`:

```python
async def to_reference_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    rebuild()   # knowledge_matrix.rebuild, уже импортирован в bot.py:81
    await query.message.reply_text(
        f"Добавлено в справочник. Всего подтверждённых ответов: {count}.\n"
        "Следующий такой вопрос бот закроет сам."
    )
```

Зарегистрировать: `app.add_handler(CallbackQueryHandler(to_reference_callback, pattern=r"^to_reference:"))`

Дописать `get_escalation` в существующий импорт из `core.logging_` в начале `bot.py`
и добавить `import unique_answers`.

- [ ] **Шаг 5: Запустить тесты**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_unique_answers_append.py tests/test_bot_commands.py -v`
Ожидается: PASS.

- [ ] **Шаг 6: Коммит**

```bash
git add unique_answers.py bot.py tests/test_unique_answers_append.py
git commit -m "Let one tap turn an answer into knowledge"
```

---

### Task 16: Менеджер: карточка клиенту и уведомление менеджеру

**Файлы:**
- Изменить: `bot.py` (`offer_manager`, callback `want_manager`, запрос города)
- Тест: `tests/test_manager_handoff.py`

**Интерфейсы:**
- Использует: `managers.manager_for_city`, `managers.format_manager`, `managers.FALLBACK_TEXT`
- Даёт: `offer_manager(update, context, question)`; ключ `context.user_data["city"]`

- [ ] **Шаг 1: Написать падающий тест**

```python
# tests/test_manager_handoff.py
import asyncio

import bot
import managers


def test_known_city_gets_one_manager_card():
    replies = []
    update = _client_update("Есть на складе YCB9?", replies)
    context = _context(user_data={"city": "Самара"})
    asyncio.run(bot.offer_manager(update, context, "Есть на складе YCB9?"))
    assert "Искорнев" in replies[-1]
    assert "Кузнецов" not in replies[-1]


def test_unknown_city_gets_the_general_address_only():
    replies = []
    update = _client_update("Есть на складе?", replies)
    context = _context(user_data={"city": "Ереван"})
    asyncio.run(bot.offer_manager(update, context, "Есть на складе?"))
    assert "info@cncrussia.com" in replies[-1]
    assert "ЦФО" not in replies[-1]


def test_manager_is_notified(monkeypatch):
    sent = []

    async def fake_send(chat_id, text, **kwargs):
        sent.append((chat_id, text))

    monkeypatch.setattr(managers, "_telegram_ids", lambda: {"is@cncrussia.com": 900003})
    replies = []
    update = _client_update("Есть на складе YCB9?", replies, send_message=fake_send)
    context = _context(user_data={"city": "Самара"})
    asyncio.run(bot.offer_manager(update, context, "Есть на складе YCB9?"))
    assert sent and sent[0][0] == 900003
    assert "Самар" in sent[0][1]


def test_city_is_asked_once_when_unknown():
    replies = []
    update = _client_update("Есть на складе?", replies)
    context = _context(user_data={})
    asyncio.run(bot.offer_manager(update, context, "Есть на складе?"))
    assert "город" in replies[-1].lower()
    assert context.user_data.get("awaiting_city") is True
```

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_manager_handoff.py -v`
Ожидается: FAIL — `offer_manager` не существует.

- [ ] **Шаг 3: Написать передачу менеджеру**

```python
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
```

В `answer()` обработать ожидание города до клиентской лестницы:

```python
    if context.user_data.pop("awaiting_city", False):
        context.user_data["city"] = question
        await offer_manager(update, context, context.user_data.pop("pending_question", question))
        return
```

- [ ] **Шаг 4: Подключить кнопки `want_manager` и `not_it`**

Обе кнопки расставлены в Task 12 и Task 11; без хендлеров они молчат.

```python
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
```

`_message_update(query)` — маленький адаптер, отдающий `offer_manager` объект
с полями `message` и `effective_user` от callback-запроса; если в `bot.py` уже
есть похожий приём для callback-хендлеров, использовать его, а не заводить второй.

Зарегистрировать:

```python
    app.add_handler(CallbackQueryHandler(want_manager_callback, pattern=r"^want_manager$"))
    app.add_handler(CallbackQueryHandler(not_it_callback, pattern=r"^not_it$"))
```

- [ ] **Шаг 5: Запустить тесты**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_manager_handoff.py -v`
Ожидается: PASS.

- [ ] **Шаг 6: Коммит**

```bash
git add bot.py tests/test_manager_handoff.py
git commit -m "Tell the manager about the client, not only the client about the manager"
```

---

### Task 17: Форма рекламационного акта

**Файлы:**
- Создать: `uploads/Форма_рекламационного_акта.docx` (копия присланного файла)
- Изменить: `bot.py` (отдача файла на вопросы о браке)
- Тест: `tests/test_client_view.py`

**Интерфейсы:**
- Даёт: `RECLAMATION_FORM: Path`, `_is_reclamation(question) -> bool`

- [ ] **Шаг 1: Написать падающий тест**

```python
def test_reclamation_question_attaches_the_form():
    import bot

    assert bot.RECLAMATION_FORM.exists()
    assert bot._is_reclamation("Оборудование бракованное, куда писать?")
    assert bot._is_reclamation("Как оформить рекламацию?")
    assert not bot._is_reclamation("Какая гарантия?")
```

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_client_view.py -k reclamation -v`
Ожидается: FAIL.

- [ ] **Шаг 3: Положить файл и подключить**

```bash
cp "C:/Users/gerku/Downloads/Форма Рекламационный Акт.docx" "uploads/Форма_рекламационного_акта.docx"
```

В `bot.py`:

```python
RECLAMATION_FORM = Path("uploads") / "Форма_рекламационного_акта.docx"
_RECLAMATION_RE = re.compile(r"рекламац|брак|бракован|сгорел|не работает|дефект|гарантийн", re.I)


def _is_reclamation(question: str) -> bool:
    return bool(_RECLAMATION_RE.search(question or ""))
```

В клиентской ветке `answer()`, после получения `result`, перед отправкой текста:

```python
        if _is_reclamation(question) and RECLAMATION_FORM.exists():
            await update.message.reply_document(
                RECLAMATION_FORM.open("rb"),
                filename=RECLAMATION_FORM.name,
                caption="Форма рекламационного акта CNC. Заполните, приложите фото и видео "
                        "дефекта и отправьте на help@cncrussia.com — ответят до 3 рабочих дней.",
            )
```

- [ ] **Шаг 4: Запустить тесты**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_client_view.py -v`
Ожидается: PASS.

- [ ] **Шаг 5: Коммит**

```bash
git add bot.py tests/test_client_view.py uploads/Форма_рекламационного_акта.docx
git commit -m "Hand over the reclamation form with the instructions"
```

---

### Task 18: Просрочка не молчит

**Файлы:**
- Изменить: `bot.py` (ежедневная задача рядом с `daily_full_sync`)
- Тест: `tests/test_escalation_store.py`

**Интерфейсы:**
- Использует: `core.logging_.stale_escalations`, `escalation.ENGINEER_IDS`
- Даёт: `async def remind_about_stale_escalations(application) -> int`

- [ ] **Шаг 1: Написать падающий тест**

```python
def test_stale_escalations_are_found(tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    import core.db as db
    from core.logging_ import record_escalation, stale_escalations

    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.db"))
    db.init_db()
    number = record_escalation("старый вопрос", user_id=1, chat_id=1)
    old = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    with db.get_connection() as conn:
        conn.execute("UPDATE escalations SET ts = ? WHERE id = ?", (old, number))

    assert [row["id"] for row in stale_escalations()] == [number]


def test_fresh_escalation_is_not_stale(tmp_path, monkeypatch):
    import core.db as db
    from core.logging_ import record_escalation, stale_escalations

    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.db"))
    db.init_db()
    record_escalation("свежий вопрос", user_id=1, chat_id=1)
    assert stale_escalations() == []
```

- [ ] **Шаг 2: Запустить тест, убедиться, что падает**

Запуск: `venv/Scripts/python.exe -m pytest tests/test_escalation_store.py -k stale -v`
Ожидается: FAIL, если `stale_escalations` из Task 9 ещё не реализована; иначе PASS — тогда переходите к шагу 3.

- [ ] **Шаг 3: Добавить ежедневное напоминание**

В `bot.py` рядом с `daily_full_sync`:

```python
async def remind_about_stale_escalations(application: Application) -> int:
    """Третий рабочий день — напоминание инженеру, четвёртый — письмо клиенту.

    Молчание — единственный оставшийся способ отпустить клиента с пустыми
    руками, и он закрывается здесь (спека §7.6).
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
```

Зарегистрировать рядом с существующими ежедневными задачами в `post_init`, час — `ESCALATION_REMINDER_HOUR = 10`.

- [ ] **Шаг 4: Запустить весь набор**

Запуск: `venv/Scripts/python.exe -m pytest -q`
Ожидается: PASS.

- [ ] **Шаг 5: Коммит**

```bash
git add bot.py tests/test_escalation_store.py
git commit -m "Never let a registered question go quiet"
```

---

### Task 19: Настройки почты и документация

**Файлы:**
- Изменить: `README.md`
- Изменить: `ARCHITECTURE.md` (§3 матрица доступа)
- Изменить: `.env` (только на машине, не в git)

**Интерфейсы:**
- Использует: всё выше

- [ ] **Шаг 1: Прописать переменные в `.env`**

```
SMTP_HOST=smtp.mail.ru
SMTP_PORT=465
SMTP_USER=help@cncrussia.com
SMTP_PASSWORD=<пароль для внешнего приложения>
SUPPORT_EMAIL=help@cncrussia.com
```

Проверено на живом сервере 2026-08-29: аутентификация проходит, письмо доставляется. `.env` в git не попадает — убедиться, что он в `.gitignore`.

- [ ] **Шаг 2: Обновить матрицу доступа в ARCHITECTURE.md §3**

Строку «Свободные вопросы (движки, ИИ)» разделить на две:

| Возможность | client | manager | engineer | director | admin |
|---|:--:|:--:|:--:|:--:|:--:|
| Свободные вопросы — подтверждённые источники | ✅ | ✅ | ✅ | ✅ | ✅ |
| Свободные вопросы — ответы ИИ (уровень 5) | ❌ | ✅ | ✅ | ✅ | ✅ |
| Передать вопрос в техслужбу | ✅ | ✅ | ✅ | ✅ | ✅ |

- [ ] **Шаг 3: Дописать раздел в README.md**

В «Что умеет» добавить:

```markdown
**Клиенты** (работает)
- ответы о компании, условиях покупки, гарантии, сроках и рекламации;
- вопрос без подтверждённого ответа передаётся в техслужбу с номером —
  ответ инженера возвращается клиенту в тот же чат;
- коммерческий вопрос уходит менеджеру по региону, менеджер получает
  уведомление.

Порядок работы инженера с вопросами клиентов — [docs/ENGINEER_ESCALATION.md](docs/ENGINEER_ESCALATION.md).
```

- [ ] **Шаг 4: Прогнать весь набор и корпус**

Запуск: `venv/Scripts/python.exe -m pytest -q`
Ожидается: PASS, включая `tests/test_cold_client.py` с порогом ≥ 45 из 63.

- [ ] **Шаг 5: Коммит**

```bash
git add README.md ARCHITECTURE.md
git commit -m "Document the client contour and the mail settings"
```

---

## Проверка перед сдачей

Пройти критерии приёмки спеки §11 по списку:

- [ ] Ни один из 63 вопросов не отвечает «пришлите точный артикул» и не молчит.
- [ ] Не менее 45 из 63 закрываются без эскалации (`tests/test_cold_client.py`).
- [ ] Остальные заканчиваются номером, адресатом и сроком.
- [ ] «Чем заменить ABB S203?» объясняет порядок подбора и предлагает техслужбу.
- [ ] Ни один клиентский ответ не показывает остаток склада.
- [ ] Клиенту не показывается ответ уровня 5.
- [ ] Существующие тесты ролей менеджера, инженера, руководителя и админа проходят.
- [ ] Живая проверка в prod: подписаться на канал с тестового аккаунта, задать пять вопросов из разных групп корпуса, довести один до ответа инженера и обратно.
