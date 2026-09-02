"""Логика смыслового поиска по документам — на временной базе, без модели.

Модель эмбеддингов подменяется детерминированным фиктивным эмбеддером, база
знаний — временной sqlite с несколькими кусками. Проверяется отбор абзацев,
исключение структурных строк товаров (kind='api') и graceful-поведение.
"""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("numpy")

import reference_lookup  # noqa: F401  (semantic_reference тянет его лениво)
import semantic_documents
import semantic_reference


# Абзацы, как они получатся ПОСЛЕ _split_passages (схлопнутые пробелы).
_P1 = "Контактор предназначен для управления двигателями переменного тока."
_P2 = "Условия эксплуатации: температура окружающей среды до плюс семидесяти."

_VECTORS = {
    _P1: [1.0, 0.0, 0.0],
    _P2: [0.0, 1.0, 0.0],
    "чем управляет контактор": [0.95, 0.31, 0.0],   # ~ _P1
    "какая рабочая температура": [0.05, 0.998, 0.0],  # ~ _P2
    "как настроить телевизор": [0.30, 0.30, 0.90],    # чужое — косинус ~0.30, ниже порога
}


def _fake_embed(texts):
    return [_VECTORS.get(t, [0.0, 0.0, 0.0]) for t in texts]


@pytest.fixture
def docs_db(tmp_path, monkeypatch):
    db = tmp_path / "knowledge.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE chunks (source, kind, article, page, text)")
    connection.execute(
        "INSERT INTO chunks VALUES (?,?,?,?,?)",
        ("Паспорт CJX2-D", "catalog", "", "4", f"## Страница 4\n\n{_P1}\n\n{_P2}"),
    )
    # Строка товара из 1С — НЕ должна попасть в смысловой индекс.
    connection.execute(
        "INSERT INTO chunks VALUES (?,?,?,?,?)",
        ("products.json", "api", "CJX2-D-25", "", '{"vendor_code": "CJX2-D-25", "current": 25}'),
    )
    connection.commit()
    connection.close()

    monkeypatch.setattr(semantic_documents, "DB_PATH", db)
    monkeypatch.setattr(semantic_documents, "_load_disk_cache", lambda fingerprint: None)
    monkeypatch.setattr(semantic_documents, "_save_disk_cache", lambda fingerprint, vectors: None)
    monkeypatch.setattr(semantic_reference, "_load_backend", lambda: _fake_embed)
    semantic_reference.reset_cache()
    semantic_documents.reset_cache()
    yield
    semantic_reference.reset_cache()
    semantic_documents.reset_cache()


def test_finds_relevant_passage(docs_db):
    match = semantic_documents.best_passage("чем управляет контактор")
    assert match is not None
    assert match.text == _P1
    assert match.source == "Паспорт CJX2-D"
    assert match.page == "4"
    assert match.score >= semantic_documents.THRESHOLD


def test_second_query_maps_to_second_passage(docs_db):
    match = semantic_documents.best_passage("какая рабочая температура")
    assert match is not None
    assert match.text == _P2


def test_api_rows_are_excluded(docs_db):
    """Смысловой индекс строится только по документам (kind='catalog'):
    структурная строка товара в кандидаты не попадает."""
    passages = semantic_documents._load_passages()
    texts = [p[2] for p in passages]
    assert _P1 in texts and _P2 in texts
    assert all("vendor_code" not in t for t in texts)


def test_unrelated_question_below_threshold_is_silent(docs_db):
    assert semantic_documents.best_passage("как настроить телевизор") is None


def test_disabled_backend_returns_none(monkeypatch, tmp_path):
    db = tmp_path / "knowledge.db"
    sqlite3.connect(db).close()
    monkeypatch.setattr(semantic_documents, "DB_PATH", db)
    monkeypatch.setattr(semantic_reference, "_load_backend", lambda: None)
    semantic_reference.reset_cache()
    semantic_documents.reset_cache()
    try:
        assert semantic_documents.best_passage("чем управляет контактор") is None
    finally:
        semantic_reference.reset_cache()
        semantic_documents.reset_cache()


def test_empty_index_returns_none(monkeypatch, tmp_path):
    """Модель есть, но документов ещё не загружали → молчим, не падаем."""
    db = tmp_path / "knowledge.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE chunks (source, kind, article, page, text)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(semantic_documents, "DB_PATH", db)
    monkeypatch.setattr(semantic_reference, "_load_backend", lambda: _fake_embed)
    semantic_reference.reset_cache()
    semantic_documents.reset_cache()
    try:
        assert semantic_documents.best_passage("чем управляет контактор") is None
    finally:
        semantic_reference.reset_cache()
        semantic_documents.reset_cache()


def test_engine_answers_with_source_and_disclaimer(docs_db):
    """DocumentSemanticEngine отдаёт дословный абзац, источник и оговорку."""
    import asyncio

    from engines.adapters import DocumentSemanticEngine

    engine = DocumentSemanticEngine()
    assert engine.can_handle("чем управляет контактор", {}) >= semantic_documents.THRESHOLD
    response = asyncio.run(engine.answer("чем управляет контактор", {}))
    assert response.handled
    assert _P1 in response.text
    assert "сверьте" in response.text.lower()
    assert response.sources == ["Паспорт CJX2-D, стр. 4"]
