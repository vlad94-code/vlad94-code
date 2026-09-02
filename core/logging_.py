"""query_log, feedback (👍/👎), unanswered, audit_log.

Заменяет старый engines/analytics.py: та же идея (лог вопросов, эскалация
неотвеченных), но на единой схеме core/db.py вместо отдельного файла/хранилища,
и без движка еженедельной аналитики (ARCHITECTURE.md §15.6 — отложено до
≥500 запросов в query_log). См. ARCHITECTURE.md §10.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from core.db import get_connection

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_query(
    question: str,
    module_used: str,
    answered: bool,
    *,
    user_id: int | None = None,
    role: str | None = None,
    latency_ms: int | None = None,
    tokens: int | None = None,
) -> int:
    """Записать вопрос в query_log. Возвращает id записи — нужен для 👍/👎."""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO query_log (ts, user_id, role, question, module_used, answered, latency_ms, tokens) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), user_id, role, question, module_used, int(answered), latency_ms, tokens),
        )
        return cursor.lastrowid


def record_feedback(query_log_id: int | None, vote: int, *, user_id: int | None = None, comment: str | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO feedback (ts, user_id, query_log_id, vote, comment) VALUES (?, ?, ?, ?, ?)",
            (_now(), user_id, query_log_id, vote, comment),
        )


def record_unanswered(question: str, reason: str, *, user_id: int | None = None) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO unanswered (ts, user_id, question, reason) VALUES (?, ?, ?, ?)",
            (_now(), user_id, question, reason),
        )
        return cursor.lastrowid


def open_unanswered(limit: int = 30) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM unanswered WHERE resolved = 0 ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def resolve_unanswered(question_id: int, note: str = "") -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE unanswered SET resolved = 1, note = ? WHERE id = ? AND resolved = 0",
            (note, question_id),
        )
        return cursor.rowcount > 0


def queue_for_review(
    question: str,
    answer: str,
    sources: list[str] | None = None,
    *,
    query_log_id: int | None = None,
    category: str | None = None,
) -> int:
    """Поставить ответ Claude/RAG в очередь на подтверждение инженером.

    Смысл отдельный от record_unanswered(): там «ответить нечем», здесь
    «ответ есть, надо решить, класть ли его в постоянную базу знаний».
    Автоматически в базу ответы ИИ не попадают: иначе одна ошибка модели
    закрепится как подтверждённый факт, а весь проект держится на
    «только проверенное» (ARCHITECTURE.md §2, §5).
    """
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO rag_review_queue (ts, query_log_id, question, answer, sources, category) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _now(),
                query_log_id,
                question,
                answer,
                json.dumps(sources or [], ensure_ascii=False),
                category,
            ),
        )
        return cursor.lastrowid


def pending_reviews(limit: int = 20) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM rag_review_queue WHERE status = 'pending' ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_review(review_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM rag_review_queue WHERE id = ?", (review_id,)).fetchone()
        return dict(row) if row else None


def resolve_review(review_id: int, status: str, *, reviewed_by: int | None = None, note: str = "") -> bool:
    """Подтвердить (approved) или отклонить (rejected) кандидата.

    Возвращает False, если записи нет или её уже рассмотрели — как
    resolve_unanswered(), чтобы повторная команда не «переоткрывала» решение.
    """
    if status not in {"approved", "rejected"}:
        raise ValueError(f"Недопустимый статус проверки: {status}")
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE rag_review_queue SET status = ?, reviewed_by = ?, reviewed_at = ?, note = ? "
            "WHERE id = ? AND status = 'pending'",
            (status, reviewed_by, _now(), note, review_id),
        )
        return cursor.rowcount > 0


def approved_reviews() -> list[dict]:
    """Все подтверждённые ответы — источник для сборки документа базы знаний
    (verified_answers_queue.rebuild_approved_answers_document())."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM rag_review_queue WHERE status = 'approved' ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]


def record_audit(action: str, *, user_id: int | None = None, role: str | None = None, details: str | dict | None = None) -> None:
    """Обязательно для: загрузки документов, выгрузок счетов, Excel-экспорта, /sync, /reindex (§12)."""
    if isinstance(details, dict):
        details = json.dumps(details, ensure_ascii=False)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO audit_log (ts, user_id, role, action, details) VALUES (?, ?, ?, ?, ?)",
            (_now(), user_id, role, action, details),
        )


def record_escalation(question: str, *, user_id: int, chat_id: int,
                      context: str | None = None, region: str | None = None,
                      email: str | None = None) -> int:
    """Зарегистрировать вопрос клиента. Возвращает номер, который бот назовёт."""
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO escalations (ts, user_id, chat_id, question, context, region, email)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now(), user_id, chat_id, question, context, region, email),
        )
        return int(cursor.lastrowid)


def get_escalation(escalation_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM escalations WHERE id = ?", (escalation_id,)).fetchone()
    return dict(row) if row else None


def open_escalations(limit: int = 30) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM escalations WHERE answered_ts IS NULL ORDER BY id LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def set_escalation_mail_status(escalation_id: int, status: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE escalations SET mail_status = ? WHERE id = ?", (status, escalation_id))


def answer_escalation(escalation_id: int, answer: str, *, answered_by: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE escalations SET answer = ?, answered_by = ?, answered_ts = ?"
            " WHERE id = ? AND answered_ts IS NULL",
            (answer, answered_by, _now(), escalation_id),
        )
        return cursor.rowcount > 0


def stale_escalations(working_days: int = 3) -> list[dict]:
    """Открытые вопросы старше N рабочих дней (спека §7.6).

    Рабочие дни считаются грубо — календарными сутками с поправкой на
    выходные: точность до часа здесь не нужна, нужен сигнал «пора напомнить».

    _now() пишет время в ISO с UTC-зоной (datetime.now(timezone.utc)), поэтому
    порог тоже берём как offset-aware — naive datetime.now() здесь уронит
    сравнение с TypeError (naive vs aware).
    """
    threshold = datetime.now(timezone.utc) - timedelta(days=working_days + 2)
    return [row for row in open_escalations(limit=200)
            if datetime.fromisoformat(row["ts"]) <= threshold]


def stats_last_days(days: int = 7) -> dict:
    window = f"-{int(days)} days"
    with get_connection() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM query_log WHERE ts >= datetime('now', ?)", (window,)
        ).fetchone()["c"]
        answered = conn.execute(
            "SELECT COUNT(*) AS c FROM query_log WHERE answered = 1 AND ts >= datetime('now', ?)", (window,)
        ).fetchone()["c"]
        by_module = conn.execute(
            "SELECT module_used, COUNT(*) AS c FROM query_log WHERE ts >= datetime('now', ?) "
            "GROUP BY module_used ORDER BY c DESC",
            (window,),
        ).fetchall()
        votes = conn.execute(
            "SELECT vote, COUNT(*) AS c FROM feedback WHERE ts >= datetime('now', ?) GROUP BY vote",
            (window,),
        ).fetchall()
    return {
        "total": total,
        "answered": answered,
        "by_module": [(row["module_used"], row["c"]) for row in by_module],
        "votes": {row["vote"]: row["c"] for row in votes},
    }
