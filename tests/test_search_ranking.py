"""Ранжирование в knowledge_matrix.search(): редкий артикул важнее общих слов.

Тесты идут по синтетическому индексу во временном файле, а не по боевому
data/knowledge.db: ранжирование должно проверяться на известном содержимом,
иначе тест начнёт падать от каждой новой загрузки каталога.
"""
import pytest

import knowledge_matrix
from knowledge_matrix import search


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    """Проиндексировать заданные страницы во временную матрицу."""
    monkeypatch.setattr(knowledge_matrix, "DB_PATH", tmp_path / "knowledge.db")

    def _index(pages: list[tuple[str, str]], api_noise: str = "") -> None:
        with knowledge_matrix._connect() as connection:
            connection.execute("DELETE FROM chunks")
            for number, (source, text) in enumerate(pages, start=1):
                connection.execute(
                    "INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                    (source, "catalog", "", str(number), text),
                )
            # Боевой индекс — это ~500 страниц каталогов и ~25 000 записей API.
            # Артикул из вопроса встречается в сотнях товарных записей, из-за
            # чего его IDF падает, и общие слова вопроса начинают перевешивать.
            # Без этого шума синтетический индекс слишком «чистый» и сбой
            # ранжирования на нём не воспроизводится.
            for index in range(300):
                connection.execute(
                    "INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                    ("products.json", "api", "B%05d" % index, "",
                     '{"name": "Выключатель в литом корпусе %s 250 3P %dA"}' % (api_noise, index % 600)),
                )

    return _index


def test_identifier_outranks_pages_matching_only_common_words(indexed):
    """«какие регулировки у YCM3E» обязано приводить к YCM3E, а не к страницам,
    совпавшим по словам «какие регулировки у». Это и был реальный симптом:
    в топ выходили YCM8T/A и YCM8RT, а страница YCM3E — нет.

    Ключ к воспроизведению: нужная страница сформулирована ИНАЧЕ и совпадает
    с вопросом только по артикулу, а страницы-конкуренты дословно содержат
    «какие регулировки у». При OR-запросе три совпавших слова перевешивают
    один артикул — хотя различает ответ именно артикул.
    """
    indexed([
        ("справочник.md", "Вопрос: Какие регулировки у YCM8T/A? Плавным тумблером."),
        ("справочник.md", "Вопрос: Какие регулировки у YCM8RT? Плавным тумблером."),
        ("справочник.md", "Вопрос: Какие регулировки у YCM3Y? Кнопками, LCD дисплей."),
        ("справочник.md",
         "Вопрос: Чем YCM3E отличается от YCM3Y? Ir диапазон In (0,4-1,0), шаг 1,0; "
         "Isd диапазон In (1,5-10); Ii диапазон In (2-15); всё десятипозиционным переключателем."),
    ], api_noise="YCM3E")
    rows = search("какие регулировки у YCM3E?")
    assert rows, "поиск обязан что-то вернуть"
    assert "YCM3E" in rows[0]["text"]


def test_all_identifiers_are_required_together(indexed):
    """Если в вопросе два артикула, страница с обоими важнее страницы с одним."""
    indexed([
        ("каталог.md", "YCM3 премиум линейка, ротоактивное размыкание."),
        ("каталог.md", "YCM1 стандартная линейка для базовых задач."),
        ("справочник.md",
         "Чем отличается YCM3 от YCM1: корпус PC против DMC, классы отключающей "
         "способности N/H против L/M, ротоактивное размыкание против обычного, "
         "три типа расцепителя против двух, и премиум-позиционирование против "
         "базовых задач. Подробное сравнение линеек приведено ниже по тексту."),
    ])
    rows = search("чем YCM3 отличается от YCM1?")
    assert rows
    assert "против" in rows[0]["text"], "страница с обоими артикулами должна быть первой"


def test_falls_back_to_or_when_identifiers_match_nothing(indexed):
    """Незнакомый артикул не должен обнулять выдачу — иначе опечатка в коде
    серии оставит пользователя вообще без ответа."""
    indexed([
        ("справочник.md", "Вопрос: Какие регулировки у YCM3E? Ir In (0,4-1,0)."),
    ])
    rows = search("какие регулировки у YCM9Z?")
    assert rows, "при неизвестном артикуле поиск обязан откатиться к обычному OR"


def test_question_without_identifiers_still_searches(indexed):
    indexed([
        ("справочник.md", "Только у серии YCHGLB прозрачный корпус, который даёт видимый разрыв."),
        ("каталог.md", "Технические характеристики контакторов."),
    ])
    rows = search("у какой серии есть видимый разрыв?")
    assert rows
    assert "разрыв" in rows[0]["text"]


def test_common_question_words_do_not_drive_the_result(indexed):
    """Служебные слова вопроса не должны перевешивать содержательные."""
    indexed([
        ("шум.md", "Что это такое и как это в на от для при через над под между."),
        ("нужное.md", "Мотор-привод устанавливается в стандартной комплектации AC/DC 220 В."),
    ])
    rows = search("что такое мотор-привод и как это работает?")
    assert rows
    assert rows[0]["source"] == "нужное.md"


def test_empty_question_returns_nothing(indexed):
    indexed([("справочник.md", "любой текст")])
    assert search("") == []


def test_stopwords_only_question_returns_nothing(indexed):
    """Вопрос из одних служебных слов нечем искать — пустая выдача честнее,
    чем случайная страница."""
    indexed([("справочник.md", "любой текст")])
    assert search("а что если и как же") == []


def test_comparison_question_collects_material_for_both_products(indexed):
    """Сравнение двух аппаратов требует материала с РАЗНЫХ страниц: описание
    YCM1 и описание YCM3 нигде не лежат вместе. Строгий AND по обоим артикулам
    оставлял одну страницу вместо четырёх — выдача обязана добираться."""
    indexed([
        ("справочник.md", "YCM3 премиум линейка, корпус PC, ротоактивное размыкание."),
        ("справочник.md", "YCM1 стандартная линейка, корпус DMC, для базовых задач."),
        ("каталог.md", "Прочая страница без нужных артикулов."),
    ])
    rows = search("чем YCM3 отличается от YCM1?")
    texts = " ".join(row["text"] for row in rows)
    assert "YCM3" in texts and "YCM1" in texts, "нужны обе стороны сравнения"


def test_results_are_not_duplicated_across_variants(indexed):
    """Один и тот же фрагмент не должен занимать два слота из четырёх."""
    indexed([
        ("справочник.md", "Вопрос: Какие регулировки у YCM3E? Ir диапазон In (0,4-1,0)."),
    ])
    rows = search("какие регулировки у YCM3E?")
    identities = [(row["source"], row["page"], row["text"]) for row in rows]
    assert len(identities) == len(set(identities))
