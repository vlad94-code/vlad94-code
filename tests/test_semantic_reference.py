"""Логика смыслового поиска по справочнику — без реальной модели.

Модель эмбеддингов скачивается из сети и весит сотни мегабайт, поэтому в
тестах её подменяет ДЕТЕРМИНИРОВАННЫЙ фиктивный эмбеддер: он отдаёт заранее
заданные векторы для известных строк. Так проверяется именно логика отбора
(порог, неоднозначность, откат на словарный поиск), а не качество модели.

Реальную модель проверяют не тесты, а прогон на живом справочнике на машине,
где сеть доступна (см. README, раздел про смысловой поиск).
"""
from __future__ import annotations

import pytest

pytest.importorskip("numpy")

import reference_lookup
import semantic_reference
from catalog_parser import VerifiedAnswer


def _entry(number: int, question: str, answer: str, category: str = "Тест") -> VerifiedAnswer:
    return VerifiedAnswer(number=number, category=category, question=question, context="", answer=answer)


# Справочник из трёх записей с ОРТОГОНАЛЬНЫМИ векторами: доставка, YCW3,
# гарантия. Так близость запроса к одной записи не «протекает» на другие.
_ENTRIES = (
    _entry(1, "Какая стоимость доставки?", "Доставка рассчитывается по региону."),
    _entry(2, "Выкатной ли YCW3?", "Да, буква W означает выкатное исполнение."),
    _entry(3, "Какая гарантия на автоматы?", "Гарантия 24 месяца."),
)

# Векторы: записи — базис (взаимно ортогональны). Запросы — под углом к
# «своей» записи (косинус ~0.95, выше порога 0.72) либо в стороне от всех.
_VECTORS = {
    # записи справочника
    "Какая стоимость доставки?": [1.0, 0.0, 0.0],
    "Выкатной ли YCW3?": [0.0, 1.0, 0.0],
    "Какая гарантия на автоматы?": [0.0, 0.0, 1.0],
    # переформулировки без общих слов — близки к «своей» записи
    "во сколько обойдётся доставка": [0.95, 0.31, 0.0],   # ~доставка
    "сколько лет служит гарантийный срок": [0.0, 0.30, 0.95],  # ~гарантия
    # ни на что не похоже — должно молчать (макс. косинус ~0.58 < 0.72)
    "какая сегодня погода на улице": [0.58, 0.58, 0.58],
}


def _fake_embed(texts):
    """Детерминированный эмбеддер: вектор из таблицы, иначе — нули."""
    return [_VECTORS.get(text, [0.0, 0.0, 0.0]) for text in texts]


@pytest.fixture
def semantic_env(monkeypatch):
    """Подменить бэкенд эмбеддингов и справочник, сбросить кэши."""
    monkeypatch.setattr(reference_lookup, "entries", lambda: _ENTRIES)
    monkeypatch.setattr(semantic_reference, "_load_backend", lambda: _fake_embed)
    # На случай реального кэша векторов на диске — не читать и не писать его.
    monkeypatch.setattr(semantic_reference, "_load_disk_cache", lambda fingerprint: None)
    monkeypatch.setattr(semantic_reference, "_save_disk_cache", lambda fingerprint, vectors: None)
    semantic_reference.reset_cache()
    yield
    semantic_reference.reset_cache()


def test_paraphrase_without_shared_words_is_found(semantic_env):
    """«во сколько обойдётся доставка» находит «Какая стоимость доставки?» —
    общих значимых слов нет, совпадение чисто по смыслу."""
    match = semantic_reference.best_match("во сколько обойдётся доставка")
    assert match is not None
    assert match.question == "Какая стоимость доставки?"
    assert match.answer == "Доставка рассчитывается по региону."
    assert match.score >= semantic_reference.THRESHOLD


def test_second_paraphrase_maps_to_its_own_entry(semantic_env):
    match = semantic_reference.best_match("сколько лет служит гарантийный срок")
    assert match is not None
    assert match.question == "Какая гарантия на автоматы?"


def test_unrelated_question_stays_silent(semantic_env):
    """Вопрос не по теме справочника не должен получить ложный ответ."""
    assert semantic_reference.best_match("какая сегодня погода на улице") is None


def test_below_threshold_returns_none(semantic_env):
    """Тот же запрос при завышенном пороге тоже молчит — порог работает."""
    assert semantic_reference.best_match("во сколько обойдётся доставка", threshold=0.99) is None


def test_ambiguous_match_between_different_answers_is_silent(monkeypatch):
    """Две записи с РАЗНЫМИ ответами одинаково близки к запросу — молчим,
    а не отдаём одну наугад под меткой «подтверждено инженером»."""
    entries = (
        _entry(1, "Вопрос А", "Ответ А"),
        _entry(2, "Вопрос Б", "Ответ Б"),
    )
    vectors = {"Вопрос А": [1.0, 0.0], "Вопрос Б": [1.0, 0.0], "запрос": [1.0, 0.0]}
    monkeypatch.setattr(reference_lookup, "entries", lambda: entries)
    monkeypatch.setattr(semantic_reference, "_load_backend",
                        lambda: (lambda texts: [vectors.get(t, [0.0, 0.0]) for t in texts]))
    monkeypatch.setattr(semantic_reference, "_load_disk_cache", lambda fingerprint: None)
    monkeypatch.setattr(semantic_reference, "_save_disk_cache", lambda fingerprint, vectors: None)
    semantic_reference.reset_cache()
    try:
        assert semantic_reference.best_match("запрос") is None
    finally:
        semantic_reference.reset_cache()


def test_disabled_backend_returns_none(monkeypatch):
    """Модель недоступна (fastembed нет или не скачана) → None, тихо."""
    monkeypatch.setattr(reference_lookup, "entries", lambda: _ENTRIES)
    monkeypatch.setattr(semantic_reference, "_load_backend", lambda: None)
    semantic_reference.reset_cache()
    try:
        assert semantic_reference.best_match("во сколько обойдётся доставка") is None
        assert semantic_reference.is_available() is False
    finally:
        semantic_reference.reset_cache()


def test_lookup_combined_falls_back_to_keyword(monkeypatch):
    """Когда смысловой поиск выключен, lookup_combined ведёт себя как lookup:
    отдаёт словарное совпадение и не падает без модели."""
    sentinel = reference_lookup.Match(question="Q", answer="A", category="C", score=0.9)
    monkeypatch.setattr(reference_lookup, "lookup", lambda q, threshold=reference_lookup.THRESHOLD: sentinel)
    # Даже если семантику дёрнут — она выключена; результат должен быть словарным.
    monkeypatch.setattr(semantic_reference, "_load_backend", lambda: None)
    semantic_reference.reset_cache()
    try:
        assert reference_lookup.lookup_combined("любой вопрос") is sentinel
    finally:
        semantic_reference.reset_cache()


def test_lookup_combined_uses_semantic_when_keyword_misses(monkeypatch, semantic_env):
    """Словарный поиск промахнулся (None) → включается смысловой и находит."""
    monkeypatch.setattr(reference_lookup, "lookup", lambda q, threshold=reference_lookup.THRESHOLD: None)
    match = reference_lookup.lookup_combined("во сколько обойдётся доставка")
    assert match is not None
    assert match.question == "Какая стоимость доставки?"
    assert match.answer == "Доставка рассчитывается по региону."


def test_keyword_result_wins_and_semantic_not_consulted(monkeypatch):
    """Если словарный поиск нашёл — смысловой не вызывается вовсе
    (доверяем точному оттюнингованному совпадению)."""
    sentinel = reference_lookup.Match(question="Q", answer="A", category="C", score=0.8)
    monkeypatch.setattr(reference_lookup, "lookup", lambda q, threshold=reference_lookup.THRESHOLD: sentinel)

    def _boom(*args, **kwargs):
        raise AssertionError("semantic_reference.best_match не должен вызываться")

    monkeypatch.setattr(semantic_reference, "best_match", _boom)
    assert reference_lookup.lookup_combined("вопрос") is sentinel
