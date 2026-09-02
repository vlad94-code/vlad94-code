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
