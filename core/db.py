"""SQLite: схема, миграции, бэкап. Единственная база — `data/bot.db`.

См. ARCHITECTURE.md §6 (схема), §11 (бэкап). 7 пользователей — SQLite
постоянное решение, не переходный этап (§0).
"""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


def _db_path() -> Path:
    return Path(os.environ.get("DB_PATH", "data/bot.db"))


def _backup_dir() -> Path:
    return Path(os.environ.get("BACKUP_DIR", "backups"))


SCHEMA = """
-- Счета из 1С (перезаписываются целиком при каждой загрузке XLSX)
CREATE TABLE IF NOT EXISTS invoices (
    number TEXT PRIMARY KEY,
    date TEXT,
    counterparty TEXT,
    counterparty_code TEXT,
    amount_total REAL,
    amount_paid REAL,
    payment_status TEXT,   -- not_paid | partial | paid
    shipment_status TEXT,  -- not_shipped | partial | shipped
    ship_date TEXT,
    snapshot_at TEXT
);

CREATE TABLE IF NOT EXISTS invoice_lines (
    invoice_number TEXT,
    line_no INTEGER,
    vendor_code TEXT,
    name TEXT,
    qty REAL,
    price REAL,
    amount REAL,
    qty_shipped REAL,
    FOREIGN KEY(invoice_number) REFERENCES invoices(number)
);

-- Качество и наблюдаемость
CREATE TABLE IF NOT EXISTS query_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    user_id INTEGER,
    role TEXT,
    question TEXT,
    module_used TEXT,
    answered INTEGER,
    latency_ms INTEGER,
    tokens INTEGER
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    user_id INTEGER,
    query_log_id INTEGER,
    vote INTEGER,          -- +1 / -1
    comment TEXT
);

CREATE TABLE IF NOT EXISTS unanswered (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    user_id INTEGER,
    question TEXT,
    reason TEXT,
    resolved INTEGER NOT NULL DEFAULT 0,
    note TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    user_id INTEGER,
    role TEXT,
    action TEXT,
    details TEXT
);

-- В БД, а не в context.user_data: контекст фильтров должен переживать рестарт.
CREATE TABLE IF NOT EXISTS dialog_context (
    user_id INTEGER PRIMARY KEY,
    filters_json TEXT,
    updated_at TEXT
);

-- Ответы Claude/RAG, ожидающие подтверждения инженером перед тем, как стать
-- частью базы знаний. Смысл отдельный от unanswered: там «ответить нечем»,
-- здесь «ответ есть, решаем, класть ли его в постоянную базу» — бот не
-- учит сам себя, иначе одна ошибка ИИ закрепляется как подтверждённый факт.
CREATE TABLE IF NOT EXISTS rag_review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    query_log_id INTEGER,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    sources TEXT,                            -- JSON-список, как EngineResponse.sources
    category TEXT,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    reviewed_by INTEGER,
    reviewed_at TEXT,
    note TEXT
);

-- last_sync_*, snapshot_at и другие разовые значения.
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Вопрос клиента, на который бот не смог ответить сам. Отличается от
-- unanswered тем, что несёт обратный адрес: чат, куда вернуть ответ, и
-- необязательную почту клиента. Номер строки — это номер вопроса,
-- который клиент видит и называет.
CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    context TEXT,
    region TEXT,
    email TEXT,
    mail_status TEXT,
    answered_ts TEXT,
    answer TEXT,
    answered_by INTEGER
);

CREATE INDEX IF NOT EXISTS idx_query_log_ts ON query_log(ts);
CREATE INDEX IF NOT EXISTS idx_unanswered_resolved ON unanswered(resolved);
CREATE INDEX IF NOT EXISTS idx_invoice_lines_invoice ON invoice_lines(invoice_number);
CREATE INDEX IF NOT EXISTS idx_invoice_lines_vendor_code ON invoice_lines(vendor_code);
CREATE INDEX IF NOT EXISTS idx_rag_review_queue_status ON rag_review_queue(status);
CREATE INDEX IF NOT EXISTS idx_escalations_open ON escalations(answered_ts);
"""


def init_db(path: Path | None = None) -> None:
    """Создать таблицы, если их ещё нет. Безопасно вызывать при каждом старте."""
    db_path = path or _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.commit()
    logger.info("SQLite schema ready at %s", db_path)


@contextmanager
def get_connection(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    db_path = path or _db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def backup(path: Path | None = None, backup_dir: Path | None = None) -> Path:
    """Согласованный бэкап через `sqlite3 .backup` — безопасно на «живой» БД.

    Кладёт копию в BACKUP_DIR. Синхронизация BACKUP_DIR за пределы сервера —
    задача эксплуатации (второй диск / облако), не кода. См. ARCHITECTURE.md §11:
    непроверенный бэкап не считается бэкапом — раз в месяц восстанавливайте и
    открывайте копию.
    """
    db_path = path or _db_path()
    out_dir = backup_dir or _backup_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = out_dir / f"bot_{stamp}.db"

    source = sqlite3.connect(db_path)
    target = sqlite3.connect(dest)
    try:
        with target:
            source.backup(target)
    finally:
        source.close()
        target.close()
    logger.info("Backup written to %s", dest)
    return dest


def prune_old_backups(keep_days: int = 30, backup_dir: Path | None = None) -> int:
    out_dir = backup_dir or _backup_dir()
    if not out_dir.exists():
        return 0
    cutoff = datetime.now(timezone.utc).timestamp() - keep_days * 86400
    removed = 0
    for file in out_dir.glob("bot_*.db"):
        try:
            if file.stat().st_mtime < cutoff:
                file.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def set_meta(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_meta(key: str) -> str | None:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
