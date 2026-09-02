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


import asyncio

from engines.router import LOCAL_ENGINES


def test_knowledge_engine_runs_before_product_engine():
    names = [engine.name for engine in LOCAL_ENGINES]
    assert names.index("knowledge") < names.index("product")


def test_accessory_engine_still_runs_first():
    assert LOCAL_ENGINES[0].name == "accessory_compat"
