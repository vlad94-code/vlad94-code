"""Очередь подтверждения ответов ИИ: core/logging_.py + verified_answers_queue.py.

Ключевая гарантия, которую проверяют эти тесты: ответ Claude НЕ попадает в
базу знаний сам по себе — только после явного /approve инженером.
"""
import json
from pathlib import Path

import pytest

import core.db
import core.documents
import core.logging_
import verified_answers_queue
from core.logging_ import (
    approved_reviews,
    get_review,
    pending_reviews,
    queue_for_review,
    resolve_review,
)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Своя data/bot.db и data/knowledge.db на тест — боевые не трогаем."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bot.db"))
    monkeypatch.setattr(core.documents, "DB_PATH", tmp_path / "knowledge.db")
    monkeypatch.setattr(verified_answers_queue, "UPLOAD_DIR", tmp_path / "uploads")
    core.db.init_db()
    return tmp_path


def test_queue_and_list_pending(isolated_db):
    review_id = queue_for_review("вопрос?", "ответ", ["каталог"], query_log_id=7, category="контакторы")
    rows = pending_reviews()
    assert [row["id"] for row in rows] == [review_id]
    assert rows[0]["question"] == "вопрос?"
    assert rows[0]["category"] == "контакторы"
    assert rows[0]["query_log_id"] == 7
    assert json.loads(rows[0]["sources"]) == ["каталог"]
    assert rows[0]["status"] == "pending"


def test_approve_removes_from_pending_and_records_reviewer(isolated_db):
    review_id = queue_for_review("вопрос?", "ответ", [])
    assert resolve_review(review_id, "approved", reviewed_by=42, note="ок") is True
    assert pending_reviews() == []
    row = get_review(review_id)
    assert row["status"] == "approved"
    assert row["reviewed_by"] == 42
    assert row["note"] == "ок"
    assert row["reviewed_at"]


def test_resolve_is_one_shot(isolated_db):
    """Повторная команда не должна «переоткрывать» уже принятое решение —
    тот же приём, что у resolve_unanswered (WHERE ... status = 'pending')."""
    review_id = queue_for_review("вопрос?", "ответ", [])
    assert resolve_review(review_id, "approved", reviewed_by=1) is True
    assert resolve_review(review_id, "rejected", reviewed_by=2) is False
    assert get_review(review_id)["status"] == "approved"


def test_resolve_unknown_id_returns_false(isolated_db):
    assert resolve_review(99999, "approved", reviewed_by=1) is False


def test_invalid_status_rejected(isolated_db):
    review_id = queue_for_review("вопрос?", "ответ", [])
    with pytest.raises(ValueError):
        resolve_review(review_id, "maybe", reviewed_by=1)


def test_rejected_answers_never_reach_the_knowledge_base(isolated_db):
    queue_for_review("плохой вопрос?", "сомнительный ответ", [])
    review_id = pending_reviews()[0]["id"]
    resolve_review(review_id, "rejected", reviewed_by=1, note="неточно")
    assert approved_reviews() == []
    assert verified_answers_queue.rebuild_approved_answers_document() == 0


def test_rebuild_with_no_approved_answers_creates_nothing(isolated_db):
    assert verified_answers_queue.rebuild_approved_answers_document() == 0
    assert not (isolated_db / "uploads").exists() or not list((isolated_db / "uploads").glob("*.md"))


def test_approved_answer_becomes_a_searchable_document(isolated_db):
    review_id = queue_for_review(
        "чем YCM3 отличается от YCB9?",
        "YCM3 — промышленная серия в литом корпусе.",
        ["каталог B1"],
        category="выключатели в литом корпусе",
    )
    resolve_review(review_id, "approved", reviewed_by=1, note="проверено инженером")

    count = verified_answers_queue.rebuild_approved_answers_document()
    assert count == 1

    written = list((isolated_db / "uploads").glob("*.md"))
    assert len(written) == 1
    content = written[0].read_text(encoding="utf-8")
    assert "## Страница 1" in content
    assert "чем YCM3 отличается от YCB9?" in content
    assert "YCM3 — промышленная серия в литом корпусе." in content
    assert "### выключатели в литом корпусе" in content
    assert "каталог B1" in content
    assert "проверено инженером" in content


def test_approved_answers_are_not_attributed_to_cnc_tech_service(isolated_db):
    """Ответ ИИ, одобренный инженером, не должен подписываться как ответ
    технической службы CNC — это приписало бы авторство людям, которые его
    не писали, и подняло бы его до п.1 приоритета источников (§5)."""
    review_id = queue_for_review("вопрос?", "ответ ИИ", [])
    resolve_review(review_id, "approved", reviewed_by=1)
    verified_answers_queue.rebuild_approved_answers_document()
    content = list((isolated_db / "uploads").glob("*.md"))[0].read_text(encoding="utf-8")
    assert "технической службы CNC (подтверждено)" not in content
    assert "проверенный и одобренный инженером" in content


def test_rebuild_supersedes_previous_version(isolated_db):
    first = queue_for_review("вопрос 1?", "ответ 1", [])
    resolve_review(first, "approved", reviewed_by=1)
    assert verified_answers_queue.rebuild_approved_answers_document() == 1

    second = queue_for_review("вопрос 2?", "ответ 2", [])
    resolve_review(second, "approved", reviewed_by=1)
    assert verified_answers_queue.rebuild_approved_answers_document() == 2

    # Активной остаётся только последняя версия — иначе поиск нашёл бы оба
    # документа и выдал устаревший набор ответов наравне со свежим.
    active = core.documents.active_parsed_paths(isolated_db / "uploads")
    assert len(active) == 1
    content = next(iter(active)).read_text(encoding="utf-8")
    assert "вопрос 1?" in content and "вопрос 2?" in content
