"""Собрать одобренные инженером ответы ИИ в документ базы знаний.

Вторая половина механизма подтверждения (первая — core/logging_.py:
queue_for_review/pending_reviews/resolve_review). Смысл разделения: ответ
Claude сам по себе НЕ становится частью базы знаний — иначе одна ошибка
модели закрепится как подтверждённый факт и будет переиспользована в
следующих ответах. В базу попадает только то, что инженер явно одобрил
командой /approve.

Одобренные ответы проходят ровно тот же путь, что и загруженный документ:
рендер в блоки "## Страница N" (catalog_parser.render_verified_answers) →
регистрация версии (core.documents) → knowledge_matrix.rebuild(). Никакого
отдельного конвейера поиска для них нет.
"""
from __future__ import annotations

import json
from pathlib import Path

from catalog_parser import VerifiedAnswer, render_verified_answers
from core.documents import allocate_slot, register_document
from core.logging_ import approved_reviews

UPLOAD_DIR = Path("uploads")

# Фиксированное логическое имя, а не дата/номер в имени: allocate_slot()
# считает версии по original_name, и только при неизменном имени
# register_document() пометит предыдущую версию superseded (тот же приём,
# что и pricelist_store.LOGICAL_NAME).
LOGICAL_NAME = "Одобренные_ответы_ИИ.md"

# Авторство должно оставаться честным: это ответы ИИ, проверенные инженером,
# а не документ технической службы CNC (см. catalog_parser.render_verified_answers).
ANSWER_PREFIX = "Ответ ИИ, проверенный и одобренный инженером CNC:"


def _as_verified_answers(rows: list[dict]) -> list[VerifiedAnswer]:
    entries: list[VerifiedAnswer] = []
    for number, row in enumerate(rows, start=1):
        try:
            sources = json.loads(row.get("sources") or "[]")
        except (TypeError, json.JSONDecodeError):
            sources = []
        context_lines = []
        if sources:
            context_lines.append("Источники исходного ответа: " + ", ".join(str(s) for s in sources))
        if row.get("note"):
            context_lines.append(f"Комментарий инженера: {row['note']}")
        entries.append(VerifiedAnswer(
            number=number,
            category=str(row.get("category") or "Общие вопросы"),
            question=str(row.get("question") or ""),
            context="\n".join(context_lines),
            answer=str(row.get("answer") or ""),
        ))
    return entries


def rebuild_approved_answers_document() -> int:
    """Пересобрать документ из всех одобренных ответов. Возвращает их число.

    Вызывающий отвечает за knowledge_matrix.rebuild() после этого — так же,
    как bot.py делает после upload_document()/upload_pricelist().
    """
    rows = approved_reviews()
    if not rows:
        return 0

    entries = _as_verified_answers(rows)
    content = "\n".join(
        [f"# {LOGICAL_NAME}", ""] + render_verified_answers(entries, answer_prefix=ANSWER_PREFIX)
    )

    UPLOAD_DIR.mkdir(exist_ok=True, parents=True)
    slot = allocate_slot(LOGICAL_NAME)
    path = UPLOAD_DIR / slot.stored_name
    path.write_text(content, encoding="utf-8")
    # Исходника отдельно нет: сгенерированный markdown и есть документ,
    # поэтому он же и "parsed" — register_document() читает path только
    # ради чек-суммы и расширения.
    register_document(slot, path, parsed_path=path)
    return len(entries)
