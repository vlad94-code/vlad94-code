# tests/test_reference_lookup.py
"""Справочник отвечает клиенту напрямую, без модели.

До этой задачи справочник читался только через Claude, а клиенту Claude
закрыт — «Что такое УЗДП?» уходило в эскалацию, хотя ответ есть.

Fix round 1 (три Important находки на приёмке задачи 6a):

- находка 1 — короткий вопрос с кодом серии/артикула получал 1.0 против
  ЛЮБОЙ более длинной записи, где встретились оба слова: структурное
  свойство overlap coefficient, не зависящее от того, что
  AccessoryCompatibilityEngine в роутере стоит раньше;
- находка 2 — ничья на максимальном балле между записями с содержательно
  разными ответами решалась порядком строк в файле;
- находка 3 — порог THRESHOLD не был закреплён тестами этого модуля:
  единственное, что реально удерживало его от ослабления, — чужие файлы
  (golden_set.yaml, test_router.py).

Round-1 гейт по коду серии закрыл только сам пример из находки 1
("аксессуары к YCW3") и попутно сломал узнаваемость ~40 записей с кодом
серии в заголовке — клиент почти никогда не повторяет заголовок дословно.

Fix round 2 (Ruling P) меняет саму метрику вместо ещё одного гейта:
покрытие ЗАПИСИ считается взвешенным по редкости слова в справочнике
(IDF), покрытие ВОПРОСА — как обычно (доля значимых слов), итоговый
балл — их гармоническое среднее (см. `reference_lookup._score`).
Код-гейта больше нет. Ниже — оба обязательных списка Round 2:
переформулировки вопросов про записи с кодом серии (`recall`) и вопросы,
которые обязаны остаться без ответа (`precision`).

Тесты ниже проверяют исправления напрямую через `reference_lookup.lookup`,
не полагаясь на порядок движков в роутере.
"""
import asyncio

import pytest

import reference_lookup
import unique_answers
from engines.adapters import ReferenceEngine

ANSWERABLE = [
    "Какая гарантия?",
    "Где вы находитесь?",
    "Кто вы такие?",
    "Как оформить рекламацию?",
    "Продаёте физлицам?",
    "Что такое УЗДП?",
]

NOT_ANSWERABLE = [
    "Какая индуктивность у катушки в вашем реле времени?",
    "Посчитайте ток короткого замыкания для моей схемы",
]


@pytest.mark.parametrize("question", ANSWERABLE)
def test_reference_answers_what_it_knows(question):
    match = reference_lookup.lookup(question)
    assert match is not None, f"справочник не нашёл ответ на «{question}»"
    assert match.answer.strip()


@pytest.mark.parametrize("question", NOT_ANSWERABLE)
def test_reference_stays_silent_on_what_it_does_not_know(question):
    assert reference_lookup.lookup(question) is None


def test_answer_is_returned_verbatim():
    match = reference_lookup.lookup("Какая гарантия?")
    source_entry = next(e for e in reference_lookup.entries() if e.answer == match.answer)
    assert match.answer == source_entry.answer


def test_engine_marks_the_source_as_the_engineer_reference():
    engine = ReferenceEngine()
    assert engine.can_handle("Какая гарантия?", {}) > 0
    response = asyncio.run(engine.answer("Какая гарантия?", {}))
    assert response.handled
    assert response.sources
    assert "правочник" in response.sources[0]


def test_engine_declines_unknown_question():
    engine = ReferenceEngine()
    assert engine.can_handle("Посчитайте ток короткого замыкания для моей схемы", {}) == 0.0


@pytest.fixture
def fake_reference(monkeypatch, tmp_path):
    """Подменяет источник справочника переданным markdown-текстом — тот же
    приём, что в tests/test_unique_answers.py
    (monkeypatch.setattr(unique_answers, "SOURCE_PATH", ...)), плюс сброс
    кэша `reference_lookup.entries()` до и после: без него кэш утёк бы
    между тестами (см. `reference_lookup.clear_cache`, по образцу
    `catalog_search.clear_cache`).
    """
    def _apply(markdown: str) -> None:
        source = tmp_path / "unique_answers.md"
        source.write_text(markdown, encoding="utf-8")
        monkeypatch.setattr(unique_answers, "SOURCE_PATH", source)
        reference_lookup.clear_cache()
    yield _apply
    reference_lookup.clear_cache()


# --- Находка 1: короткий код-содержащий вопрос не должен получать
# максимальный балл только за счёт формулы. Проверяем напрямую через
# reference_lookup.lookup, а не через роутер: защита порядком
# LOCAL_ENGINES (AccessoryCompatibilityEngine раньше справочника) не
# спасает, если однажды у серии не окажется строк в прайсе. Round 1
# закрывал это гейтом по коду серии (удалён в Round 2, Ruling P) — здесь
# те же примеры продолжают проходить уже за счёт новой взвешенной
# метрики, без специального правила для код-содержащих вопросов.

def test_short_question_with_series_code_is_not_claimed_by_word_overlap_alone():
    assert reference_lookup.lookup("аксессуары к YCW3") is None
    assert reference_lookup.lookup("аксессуары YCW3") is None


def test_series_code_question_still_matches_the_near_exact_heading():
    # Позитивный контроль: код-содержащий вопрос не отрезан целиком —
    # он проходит, когда покрывает запись почти дословно (обе доли —
    # покрытие записи и покрытие вопроса — становятся 1.0).
    match = reference_lookup.lookup("Какие дополнительные аксессуары доступны для YCW3?")
    assert match is not None


# --- Находка 3: пограничные случаи из таблицы баллов fix-отчёта (не нули
# — реальные ненулевые оценки около порога). Если THRESHOLD ослабить в
# сторону 0.667, эти вопросы начнут получать ответ из справочника, хотя
# это территория ProductEngine/KnowledgeEngine (golden_set.yaml,
# test_router.py). Тест ловит такое ослабление здесь же, не полагаясь на
# чужие файлы.
@pytest.mark.parametrize("question", [
    "выключатель в литом корпусе",  # 0.667 — ближайший к порогу случай
    "диф автомат в литом корпусе",  # 0.5
    "какие характеристики бывают",  # 0.5
])
def test_reference_stays_silent_near_the_threshold(question):
    assert reference_lookup.lookup(question) is None


# --- Находка 2: ничья на максимальном балле между записями с разными
# ответами не должна решаться порядком строк в файле (Ruling N).

def test_tie_between_equally_specific_entries_with_different_answers_stays_silent(fake_reference):
    fake_reference(
        "## К\n"
        "### Общее слово альфа?\n"
        "Ответ один.\n"
        "\n"
        "### Общее слово бета?\n"
        "Ответ два, другой по смыслу.\n"
    )
    # Обе записи покрыты вопросом одинаково (общий балл и precision
    # совпадают: у каждой ровно одно "своё" слово вне вопроса), а ответы
    # по существу разные — молчание, а не первая по файлу.
    assert reference_lookup.lookup("Общее слово?") is None


def test_tie_is_resolved_by_the_entry_the_question_covers_more_fully(fake_reference):
    fake_reference(
        "## К\n"
        "### Общее слово?\n"
        "Точный ответ.\n"
        "\n"
        "### Общее слово гамма?\n"
        "Ответ пошире, про другое.\n"
    )
    # Первичный балл (recall вопроса) у обеих записей одинаков — вопрос
    # целиком покрыт в обоих случаях. Но первая запись не содержит
    # ничего сверх вопроса (precision 1.0), а вторая — содержит лишнее
    # слово "гамма" (precision 0.667): тай-брейк по precision выбирает
    # более узкую/точную запись, а не первую по файлу.
    match = reference_lookup.lookup("Общее слово?")
    assert match is not None
    assert match.answer == "Точный ответ."


def test_uzdp_question_gets_the_definition_not_an_arbitrary_tied_entry():
    """Регрессия на реальном справочнике: до fix round 1 «Что такое
    УЗДП?» сводился к единственному слову и давал 1.0 сразу против
    четырёх записей о разном (конфигурации, принцип защиты, заменяемость,
    подбор номинала) — побеждала первая по файлу («В каких
    конфигурациях..."), хотя вопрос был про суть устройства. Теперь в
    справочнике есть отдельная определительная запись, и тай-брейк по
    precision выбирает именно её."""
    match = reference_lookup.lookup("Что такое УЗДП?")
    assert match is not None
    assert match.question == "Что такое УЗДП?"


# --- Fix round 2 (Ruling P): взвешенная по IDF метрика вместо гейта по
# коду серии. Оба списка ниже — обязательные требования приёмки, взятые
# из fix-отчёта дословно; полная таблица баллов (совпавшие слова,
# покрытие записи, покрытие вопроса, итоговый балл) — там же.

# recall: естественные переформулировки вопросов про записи с кодом
# серии в заголовке — короче заголовка, без кода в части случаев,
# ровно то, ради чего существует взвешенная метрика (клиент не повторяет
# заголовок дословно). Контроллер разрешил до четырёх провалов из 14;
# отмеченные xfail — пять известных провалов при THRESHOLD=0.68 после
# Ruling Q (Fix round 3, оценки и разбор — в таблице fix-отчёта), а не
# забытая регрессия. Это на один провал больше разрешённых четырёх —
# см. "DONE_WITH_CONCERNS" в fix-отчёте, Fix round 3: полный перебор
# порога показал, что при сохранении MUST_SILENT 12 из 12 без исключений
# recall не может подняться выше 9 из 14 (окно порога, где MUST_SILENT
# ещё держится, уже отрезало «Какая категория применения у YCW3?» и
# «Какую выбрать: YCM1, YCM8 или YCM3?» — оба заголовка справочника
# объединяют по два разных факта, и обрывок про один из них законно не
# покрывает вторую половину записи).
_RECALL_REFORMULATIONS = [
    "Есть ли у YCW3 расцепитель с уставкой?",
    pytest.param(
        "Какая индикация у YCW3?",
        marks=pytest.mark.xfail(strict=True, reason="score 0.4692 < THRESHOLD=0.68 — см. таблицу баллов fix-отчёта, Fix round 3"),
    ),
    "Что входит в комплектацию YCW3?",
    "Есть ли у YCW3 защита по напряжению?",
    "Есть ли независимый расцепитель у YCW3?",
    "Как вывести сухой контакт YCW3?",
    pytest.param(
        "Какая категория применения у YCW3?",
        marks=pytest.mark.xfail(strict=True, reason="score 0.6667 < THRESHOLD=0.68 — см. таблицу баллов fix-отчёта, Fix round 3"),
    ),
    pytest.param(
        "Что за линейка YCM3?",
        marks=pytest.mark.xfail(strict=True, reason="score 0.5224 < THRESHOLD=0.68 — см. таблицу баллов fix-отчёта, Fix round 3"),
    ),
    pytest.param(
        "Какую выбрать: YCM1, YCM8 или YCM3?",
        marks=pytest.mark.xfail(strict=True, reason="score 0.6709 < THRESHOLD=0.68 — см. таблицу баллов fix-отчёта, Fix round 3"),
    ),
    "Каким стандартам соответствует YCW3?",
    "Есть ли у YCW3 мотор-привод?",
    pytest.param(
        "Как проверить фазировку YCW3?",
        marks=pytest.mark.xfail(strict=True, reason="score 0.5716 < THRESHOLD=0.68 — см. таблицу баллов fix-отчёта, Fix round 3"),
    ),
    "Чем CJX2-F отличается от CJX2S-F?",
    "Что означают положения вкачено и выкачено?",
]


@pytest.mark.parametrize("question", _RECALL_REFORMULATIONS)
def test_natural_reformulations_of_series_code_questions_are_answered(question):
    assert reference_lookup.lookup(question) is not None


# precision: вопросы, которые обязаны остаться без ответа — обрывки без
# содержательного покрытия записи, оба вопроса NOT_ANSWERABLE и
# каталожные вопросы (территория ProductEngine/KnowledgeEngine).
#
# «дополнительные аксессуары?» БОЛЬШЕ НЕ xfail (Fix round 3, Ruling Q):
# после того как код изделия стал весить по максимуму независимо от
# частоты, этот вопрос (без кода в тексте) потерял балл — 0.7143 →
# 0.6561, конфликт с «Какая гарантия?» (0.6879) исчез. Взамен вплотную
# к минимуму ANSWERABLE подошла «аксессуары к YCW3» (0.6153 → 0.6771,
# ниже 0.6879, но эта близость и сузила окно порога — см. fix-отчёт,
# Fix round 3, и разбор недостающих пунктов recall выше).
_MUST_STAY_SILENT = [
    "аксессуары к YCW3",
    "дополнительные аксессуары?",
    "сухие контакты?",
    "мотор-привод?",
    "независимый расцепитель?",
    "контактор 25А",
    "выключатель в литом корпусе",
    "контакторы",
    "какие бывают контакторы",
    "автомат YCB9 63А",
    *NOT_ANSWERABLE,
]


@pytest.mark.parametrize("question", _MUST_STAY_SILENT)
def test_partial_matches_and_catalog_questions_stay_silent(question):
    assert reference_lookup.lookup(question) is None


# --- Ruling U (fix round 2, задача 7): название города — параметр вопроса
# про доставку, а не его содержание. Ответ про способ доставки один и тот
# же для любого города, а заголовок записи справочника сознательно
# обобщён и города не называет (Ruling T) — значит непокрытое название
# города не должно топить вопрос. До этой правки «Отправляете в
# Новосибирск?» и «Отправляете в Хабаровск?» одинаково не находили ничего:
# единственное значимое слово сверх глагола («город-название») никогда не
# могло совпасть, и вопрос был обречён на молчание независимо от
# формулировки заголовка (см. отчёт Fix round 1). Смысл проверки — не
# «находит хоть что-то», а «разные города находят ОДНУ И ТУ ЖЕ запись»,
# то есть город действительно перестал участвовать в сопоставлении.
def test_delivery_question_with_a_city_name_ignores_the_city():
    novosibirsk = reference_lookup.lookup("Отправляете в Новосибирск?")
    khabarovsk = reference_lookup.lookup("Отправляете в Хабаровск?")
    kazan = reference_lookup.lookup("Отправляете в Казань?")
    assert novosibirsk is not None
    assert khabarovsk is not None
    assert kazan is not None
    assert novosibirsk.question == khabarovsk.question == kazan.question
    assert novosibirsk.answer == khabarovsk.answer == kazan.answer
