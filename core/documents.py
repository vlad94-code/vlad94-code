"""Registry of uploaded knowledge-base documents.

The registry is deliberately local: it records which document version is active
for retrieval without copying either the source file or its contents into SQLite.
"""
from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

DB_PATH = Path("data") / "knowledge.db"

# allocate_slot() reads MAX(version) and closes its connection before the
# caller (bot.py, verified_answers_queue.py) does download/parse work and
# calls register_document() to INSERT — an unguarded read-then-write. Two
# uploads racing for the same original_name (fixed LOGICAL_NAME for
# pricelist/verified-answers, so this isn't even rare for those two) can
# read the same MAX(version) and collide on the UNIQUE(original_name,
# version) constraint. The bot is a single process on one machine (see
# ARCHITECTURE.md) — one process-wide lock held by the caller across the
# whole allocate_slot -> register_document span is enough to close the race.
upload_lock = asyncio.Lock()


@dataclass(frozen=True)
class DocumentSlot:
    """A unique local filename allocated for a new uploaded version."""

    original_name: str
    stored_name: str
    version: int


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL UNIQUE,
            parsed_name TEXT,
            extension TEXT NOT NULL,
            version INTEGER NOT NULL,
            checksum_sha256 TEXT NOT NULL,
            source TEXT NOT NULL,
            uploaded_by INTEGER,
            uploaded_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active', 'superseded', 'failed')),
            vector_file_id TEXT,
            UNIQUE(original_name, version)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS document_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            entity TEXT NOT NULL,
            attribute TEXT NOT NULL,
            value TEXT NOT NULL,
            page INTEGER NOT NULL,
            evidence TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('current', 'conflict', 'rejected')),
            extracted_at TEXT NOT NULL,
            FOREIGN KEY(document_id) REFERENCES documents(id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_facts_lookup ON document_facts(entity, attribute)"
    )
    return connection


def allocate_slot(filename: str) -> DocumentSlot:
    """Allocate the next versioned filename without replacing an older upload."""
    original_name = Path(filename).name or "document"
    path = Path(original_name)
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM documents WHERE original_name = ?",
            (original_name,),
        ).fetchone()
    finally:
        connection.close()
    version = int(row[0]) + 1
    stored_name = f"{path.stem}.v{version}{path.suffix.lower()}"
    return DocumentSlot(original_name=original_name, stored_name=stored_name, version=version)


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def register_document(
    slot: DocumentSlot,
    path: Path,
    *,
    parsed_path: Path | None = None,
    uploaded_by: int | None = None,
    vector_file_id: str | None = None,
) -> None:
    """Make an uploaded document the active version of its original filename."""
    timestamp = datetime.now(timezone.utc).isoformat()
    connection = _connect()
    try:
        connection.execute(
            "UPDATE documents SET status = 'superseded' WHERE original_name = ? AND status = 'active'",
            (slot.original_name,),
        )
        connection.execute(
            """
            INSERT INTO documents (
                original_name, stored_name, parsed_name, extension, version,
                checksum_sha256, source, uploaded_by, uploaded_at, status, vector_file_id
            ) VALUES (?, ?, ?, ?, ?, ?, 'telegram', ?, ?, 'active', ?)
            """,
            (
                slot.original_name,
                slot.stored_name,
                parsed_path.name if parsed_path else None,
                path.suffix.lower(),
                slot.version,
                file_checksum(path),
                uploaded_by,
                timestamp,
                vector_file_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def mark_registration_failed(slot: DocumentSlot) -> None:
    """Roll back a register_document() call whose post-processing (parsing,
    structured import — e.g. upload_pricelist()'s import_items()) failed
    afterward. register_document() must run before that work for callers that
    need a real document_id FK (see document_id_for()), so by the time a
    parse failure is caught, the new version is already wrongly 'active' and
    the previous one wrongly 'superseded' even though it's still the version
    actually serving data. Marks the new version 'failed' and restores the
    immediately preceding version — the one register_document() just flipped
    — back to 'active'.
    """
    connection = _connect()
    try:
        connection.execute(
            "UPDATE documents SET status = 'failed' WHERE original_name = ? AND version = ?",
            (slot.original_name, slot.version),
        )
        connection.execute(
            "UPDATE documents SET status = 'active' WHERE original_name = ? AND version = ?",
            (slot.original_name, slot.version - 1),
        )
        connection.commit()
    finally:
        connection.close()


def register_legacy_document(path: Path, parsed_path: Path) -> DocumentSlot | None:
    """Register a catalogue that existed before the document registry.

    Returns ``None`` when this source file has already been migrated.
    """
    connection = _connect()
    try:
        exists = connection.execute(
            "SELECT 1 FROM documents WHERE stored_name = ?", (path.name,)
        ).fetchone()
        if exists:
            return None
        original_name = path.name
        version = connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM documents WHERE original_name = ?",
            (original_name,),
        ).fetchone()[0]
        has_active = connection.execute(
            "SELECT 1 FROM documents WHERE original_name = ? AND status = 'active'",
            (original_name,),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO documents (
                original_name, stored_name, parsed_name, extension, version,
                checksum_sha256, source, uploaded_by, uploaded_at, status, vector_file_id
            ) VALUES (?, ?, ?, ?, ?, ?, 'legacy_local', NULL, ?, ?, NULL)
            """,
            (
                original_name,
                path.name,
                parsed_path.name,
                path.suffix.lower(),
                version,
                file_checksum(path),
                datetime.now(timezone.utc).isoformat(),
                'superseded' if has_active else 'active',
            ),
        )
        connection.commit()
        return DocumentSlot(original_name=original_name, stored_name=path.name, version=version)
    finally:
        connection.close()


def document_id_for(stored_name: str) -> int | None:
    """id зарегистрированного документа по его stored_name.

    register_document() ничего не возвращает (вызывающие раньше работали
    только через stored_name/parsed_name), но модулям, которым нужен
    настоящий FK на documents.id (например pricelist_store.py), нужно
    как-то получить id только что зарегистрированной версии.
    """
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT id FROM documents WHERE stored_name = ?", (stored_name,)
        ).fetchone()
    finally:
        connection.close()
    return row[0] if row else None


def registered_parsed_names() -> set[str]:
    """Return parsed filenames of every registered version, including obsolete ones."""
    connection = _connect()
    try:
        rows = connection.execute(
            "SELECT parsed_name FROM documents WHERE parsed_name IS NOT NULL"
        ).fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def active_parsed_paths(upload_dir: Path) -> set[Path]:
    """Return parsed catalogues of active registered versions.

    Empty results preserve compatibility with catalogues uploaded before the
    registry was introduced.
    """
    connection = _connect()
    try:
        rows = connection.execute(
            "SELECT parsed_name FROM documents WHERE status = 'active' AND parsed_name IS NOT NULL"
        ).fetchall()
    finally:
        connection.close()
    return {upload_dir / row[0] for row in rows if (upload_dir / row[0]).exists()}


def summary() -> tuple[int, str | None]:
    """Return active document count and the newest upload timestamp."""
    connection = _connect()
    try:
        count, newest = connection.execute(
            "SELECT COUNT(*), MAX(uploaded_at) FROM documents WHERE status = 'active'"
        ).fetchone()
    finally:
        connection.close()
    return int(count), newest


def list_documents(limit: int = 20) -> list[dict[str, object]]:
    """Return recent document versions with their extraction status."""
    connection = _connect()
    try:
        rows = connection.execute(
            """
            SELECT documents.id, documents.original_name, documents.version, documents.status,
                   documents.uploaded_at, COUNT(document_facts.id) AS facts
            FROM documents
            LEFT JOIN document_facts ON document_facts.document_id = documents.id
            GROUP BY documents.id
            ORDER BY documents.uploaded_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        connection.close()
    keys = ("id", "original_name", "version", "status", "uploaded_at", "facts")
    return [dict(zip(keys, row)) for row in rows]


def replace_document_facts(stored_name: str, facts: Iterable[object]) -> int:
    """Replace machine-extracted facts for one registered document.

    Facts are kept separately from full-text search so a later quality stage can
    compare them with API values and passports without reparsing PDFs.
    """
    connection = _connect()
    try:
        document = connection.execute(
            "SELECT id FROM documents WHERE stored_name = ?", (stored_name,)
        ).fetchone()
        if document is None:
            raise ValueError(f"Document is not registered: {stored_name}")
        document_id = document[0]
        connection.execute("DELETE FROM document_facts WHERE document_id = ?", (document_id,))
        for fact in facts:
            connection.execute(
                """
                INSERT INTO document_facts (document_id, entity, attribute, value, page, evidence, status, extracted_at)
                VALUES (?, ?, ?, ?, ?, ?, 'current', ?)
                """,
                (
                    document_id,
                    fact.entity,
                    fact.attribute,
                    fact.value,
                    fact.page,
                    fact.evidence,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        connection.commit()
        return connection.execute(
            "SELECT COUNT(*) FROM document_facts WHERE document_id = ?", (document_id,)
        ).fetchone()[0]
    finally:
        connection.close()
