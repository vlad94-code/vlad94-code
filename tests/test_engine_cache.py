"""engines/adapters.py — can_handle()/answer() больше не пересчитывают
одинаковый поиск дважды (В-2). Проверяем, что кеш на один вопрос не
подсовывает чужой результат следующему вопросу в той же сессии роутера."""
import asyncio

from engines.adapters import KnowledgeEngine, ProductEngine


def test_product_engine_cache_does_not_leak_between_questions():
    engine = ProductEngine()
    context = {"catalog_filters": {}}

    confidence = engine.can_handle("контакторы", context)
    assert confidence > 0
    response = asyncio.run(engine.answer("контакторы", context))
    assert response.handled
    first_text = response.text

    # A different question right after must not reuse the first one's cache.
    confidence2 = engine.can_handle("какая погода в Москве", context)
    assert confidence2 == 0
    response2 = asyncio.run(engine.answer("какая погода в Москве", context))
    assert not response2.handled
    assert response2.text != first_text


def test_knowledge_engine_cache_does_not_leak_between_questions():
    engine = KnowledgeEngine()
    context: dict = {}

    assert engine.can_handle("что такое характеристика C", context) > 0
    response = asyncio.run(engine.answer("что такое характеристика C", context))
    assert response.handled
    assert "Характеристика C" in response.text

    assert engine.can_handle("что такое характеристика D", context) > 0
    response2 = asyncio.run(engine.answer("что такое характеристика D", context))
    assert response2.handled
    assert "Характеристика D" in response2.text
