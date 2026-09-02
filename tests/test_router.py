"""Smoke tests for engines/router.py — Phase 1 stabilization."""
import asyncio

import pytest

import pricelist_store
from engines.router import route_local


def test_product_engine_category():
    """Product Engine handles category-based questions."""
    question = "воздушный выключатель на 1600А"
    context = {"catalog_filters": {}}
    response = asyncio.run(route_local(question, context))
    assert response.handled, "Product Engine should handle category question"
    assert response.engine_name == "product"


def test_product_detail_engine_article():
    """ProductDetailEngine handles exact article lookup."""
    # Use a realistic article pattern; actual existence depends on API snapshot.
    # Note: ProductEngine may handle first if article matches series pattern.
    question = "YCW3-2000/3"
    context = {}
    response = asyncio.run(route_local(question, context))
    # If article exists in snapshot, handled=True; otherwise falls through.
    # This test verifies the engine attempts lookup without crashing.
    assert response.handled or response.engine_name == "router"


def test_knowledge_engine_max_current():
    """Knowledge Engine handles max nominal current queries when PDF data exists.
    
    Note: This engine returns None if the series page is not in parsed catalogs.
    The test verifies routing works correctly even when Knowledge Engine declines.
    """
    question = "максимальный ток серии YCM3"
    context = {}
    response = asyncio.run(route_local(question, context))
    # Product Engine may handle first (series filter), or router falls through.
    # Knowledge Engine would need the specific PDF page to answer.
    assert response.engine_name in ("product", "knowledge", "router")


def test_unhandled_question():
    """Router returns not_handled for unknown questions."""
    question = "какая погода в Москве"
    context = {}
    response = asyncio.run(route_local(question, context))
    assert not response.handled
    assert response.engine_name == "router"


def test_accessory_engine_wins_over_product_for_compatibility_question():
    """AccessoryCompatibilityEngine must run before ProductEngine — both can
    extract a bare series filter from an accessory question, and ProductEngine
    would otherwise silently answer with a plain product list instead."""
    if not pricelist_store.accessories_for_series("YCW3"):
        pytest.skip("прайс-лист ещё не загружен в data/knowledge.db (см. /upload_pricelist)")
    response = asyncio.run(route_local("какие аксессуары подходят к YCW3?", {"catalog_filters": {}}))
    assert response.handled
    assert response.engine_name == "accessory_compat"


def test_product_engine_unaffected_by_new_accessory_engine():
    """Regression: a plain product question must still route to ProductEngine."""
    response = asyncio.run(route_local("выключатель YCW3 3P 50кА", {"catalog_filters": {}}))
    assert response.engine_name in ("product", "router")


def test_product_engine_declines_technical_questions_with_bare_series():
    """A technical/comparison question that only mentions a series (no
    type/current/poles/icu) must NOT be claimed by ProductEngine — it can't
    answer "можно ли/входит ли/чем отличается" and would silently return a
    plain product listing instead. Verified on real paraphrased questions:
    without this check 5 of 8 such questions were claimed here."""
    for question in (
        "какие узо селективные у ycb7rl-100",
        "можно ли использовать ycb9rl-63b вместо type f schneider",
        "входит ли корзина в выкатной автомат ycw3",
        "что входит в базовую комплектацию ycw3",
    ):
        response = asyncio.run(route_local(question, {"catalog_filters": {}}))
        assert response.engine_name != "product", question


def test_product_engine_still_handles_plain_catalog_questions():
    """Regression for the check above: real browse/filter questions must
    still work, even when they mention a bare series."""
    for question, expected_engine in (
        ("воздушник YCW3", "product"),
        ("выключатель YCW3 3P 50кА", "product"),
        ("воздушный выключатель на 1600А", "product"),
    ):
        response = asyncio.run(route_local(question, {"catalog_filters": {}}))
        assert response.engine_name == expected_engine, question


if __name__ == "__main__":
    test_product_engine_category()
    print("✓ test_product_engine_category")
    test_product_detail_engine_article()
    print("✓ test_product_detail_engine_article")
    test_knowledge_engine_max_current()
    print("✓ test_knowledge_engine_max_current")
    test_unhandled_question()
    print("✓ test_unhandled_question")
    print("\nAll router smoke tests passed.")


@pytest.mark.parametrize("question", [
    "чем отличается электронный расцепитель от термомагнитного",
    "подходит ли DC автомат для солнечных панелей",
    "можно ли поставить электронный расцепитель в YCM3",
    "можно ли заменить YCB9RL-63B на YCB9-80M",
])
def test_technical_questions_are_not_claimed_by_product_engine(question):
    """Тип расцепителя и род тока — такие же слабые признаки, как серия:
    они описывают класс товаров, а не конкретную позицию. На вопрос «чем
    отличается / можно ли / подходит ли» список товаров не ответ."""
    response = asyncio.run(route_local(question, {"catalog_filters": {}}))
    assert response.engine_name != "product"


def test_plain_catalog_query_with_release_type_is_still_claimed():
    """Без технического намерения это обычный подбор по каталогу."""
    response = asyncio.run(route_local("YCM3 электронный 3P", {"catalog_filters": {}}))
    assert response.handled and response.engine_name == "product"


@pytest.mark.parametrize("question", [
    "какие регулировки у YCM3E",
    "что представляет собой линейка YCM3",
    "какая серия выключателей в литом корпусе лучше",
    "какую серию в литом корпусе выбрать",
    "что лучше YCM1 или YCM3",
    "посоветуйте выключатель в литом корпусе",
])
def test_advisory_and_reference_questions_are_not_claimed_by_product_engine(question):
    """Список товаров — не ответ на «какая лучше», «что выбрать» и «какие
    регулировки». Причём «какая серия ... в литом корпусе лучше» парсится в
    СИЛЬНЫЙ фильтр type_item, поэтому проверки на слабые фильтры тут мало:
    консультационное намерение должно отменять подбор и по категории тоже."""
    response = asyncio.run(route_local(question, {"catalog_filters": {}}))
    assert response.engine_name != "product", question


@pytest.mark.parametrize("question", [
    "выключатель в литом корпусе 250А 3P",
    "воздушный выключатель на 1600А",
    "YCM3 электронный 3P",
])
def test_shopping_questions_are_still_claimed(question):
    """Регрессия: обычный подбор по каталогу трогать нельзя."""
    response = asyncio.run(route_local(question, {"catalog_filters": {}}))
    assert response.engine_name == "product", question


def test_advisory_question_with_concrete_parameters_is_still_claimed():
    """Если названы конкретные параметры (ток, полюса), пользователь уже
    подбирает, а не советуется — список с фасетами ему полезен."""
    response = asyncio.run(route_local("какой выключатель на 250А 3P лучше", {"catalog_filters": {}}))
    assert response.engine_name == "product"
