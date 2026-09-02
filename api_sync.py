"""CNC API snapshot synchronisation.

Static product data is refreshed by the daily 06:00 Moscow job.
Volatile commercial/logistics data is refreshed only when a user explicitly
presses the refresh button (or an administrator invokes the corresponding
command in code).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

API_DIR = Path("data") / "api_exports"
STATE_FILE = Path("data") / "api_sync_state.json"
SOURCES = {
    "products.json": "https://api.cncrussia.com/products/",
    "prices.json": "https://api.cncrussia.com/prices/",
    "stock-balances.json": "https://api.cncrussia.com/stock-balances/",
    "goods-in-transit.json": "https://api.cncrussia.com/GoodsInTransit/",
}

STATIC_SOURCES = {"products.json"}
OPERATIONAL_SOURCES = {"prices.json", "stock-balances.json", "goods-in-transit.json"}

# A single process-wide lock prevents two Telegram users (or the daily task and
# a user) from downloading/replacing the same snapshots simultaneously.
SYNC_LOCK = asyncio.Lock()
logger = logging.getLogger(__name__)


def _state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _migrate_legacy_timestamps(state: dict[str, Any]) -> dict[str, Any]:
    """Backfill timestamps for snapshots created by the previous bot version."""
    changed = False
    for filename in SOURCES:
        item = state.get(filename)
        path = API_DIR / filename
        if not isinstance(item, dict) or not path.exists():
            continue
        if not item.get("last_success_at"):
            try:
                timestamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
                item["last_success_at"] = timestamp
                item["last_checked_at"] = item.get("last_checked_at") or timestamp
                changed = True
            except OSError:
                pass
    if changed:
        _save_state(state)
    return state


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(STATE_FILE.suffix + ".new")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)


def _auth() -> httpx.BasicAuth | None:
    username = os.environ.get("CNC_API_USERNAME", "")
    password = os.environ.get("CNC_API_PASSWORD", "")
    return httpx.BasicAuth(username, password) if username and password else None


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _sync_sources(source_names: set[str], *, force: bool = False) -> list[Path]:
    """Download requested snapshots, validate all of them, then replace atomically as a batch."""
    if not os.environ.get("CNC_API_USERNAME") or not os.environ.get("CNC_API_PASSWORD"):
        raise RuntimeError(
            "Не заданы CNC_API_USERNAME и CNC_API_PASSWORD в .env. "
            "Без учётных данных CNC API обновление невозможно."
        )

    API_DIR.mkdir(parents=True, exist_ok=True)
    state = _migrate_legacy_timestamps(_state())
    changed: list[Path] = []
    staged: list[tuple[Path, Path, bytes, dict[str, Any]]] = []

    try:
        timeout = httpx.Timeout(connect=10.0, read=45.0, write=45.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout, auth=_auth(), follow_redirects=True) as client:
            for filename in SOURCES:
                if filename not in source_names:
                    continue

                url = SOURCES[filename]
                old = state.get(filename, {})
                logger.info("CNC API sync: downloading %s", filename)
                headers = {"Accept": "application/json"}
                if not force and old.get("etag"):
                    headers["If-None-Match"] = old["etag"]
                if not force and old.get("last_modified"):
                    headers["If-Modified-Since"] = old["last_modified"]

                try:
                    response = await client.get(url, headers=headers)
                except httpx.TimeoutException as exc:
                    raise RuntimeError(f"Тайм-аут при обновлении {filename}. API CNC не ответил вовремя.") from exc
                except httpx.RequestError as exc:
                    raise RuntimeError(f"Ошибка соединения при обновлении {filename}: {exc}") from exc
                if response.status_code == 401:
                    raise RuntimeError(f"CNC API отклонил авторизацию при обновлении {filename} (HTTP 401). Проверьте CNC_API_USERNAME/CNC_API_PASSWORD.")
                if response.status_code == 403:
                    raise RuntimeError(f"CNC API запретил доступ при обновлении {filename} (HTTP 403). Проверьте права учётной записи.")
                if response.status_code == 429:
                    raise RuntimeError(f"CNC API временно ограничил запросы при обновлении {filename} (HTTP 429). Попробуйте позже.")
                if response.status_code == 304:
                    state[filename] = {**old, "last_checked_at": _timestamp()}
                    continue

                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise RuntimeError(f"CNC API вернул HTTP {response.status_code} при обновлении {filename}.") from exc
                content = response.content
                payload = json.loads(content.decode("utf-8"))
                if payload is None:
                    raise ValueError(f"API returned empty JSON for {filename}")

                digest = hashlib.sha256(content).hexdigest()
                destination = API_DIR / filename
                metadata = {
                    "etag": response.headers.get("etag", ""),
                    "last_modified": response.headers.get("last-modified", ""),
                    "sha256": digest,
                    "last_checked_at": _timestamp(),
                    "last_success_at": _timestamp(),
                }
                state[filename] = metadata

                if old.get("sha256") == digest and destination.exists():
                    continue

                temporary = destination.with_suffix(destination.suffix + ".new")
                temporary.write_bytes(content)
                staged.append((destination, temporary, content, metadata))

        # No file is replaced until every requested response has downloaded and
        # passed JSON validation. If any request above fails, old snapshots stay intact.
        for destination, temporary, _content, _metadata in staged:
            temporary.replace(destination)
            changed.append(destination)

        _save_state(state)
        return changed
    except Exception:
        for _destination, temporary, _content, _metadata in staged:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise


async def sync_static(*, force: bool = False) -> list[Path]:
    """Refresh stable product/technical data (normally once every morning)."""
    async with SYNC_LOCK:
        return await _sync_sources(STATIC_SOURCES, force=force)


async def sync_operational(*, force: bool = False) -> list[Path]:
    """Refresh prices, stock and goods-in-transit after an explicit request."""
    async with SYNC_LOCK:
        return await _sync_sources(OPERATIONAL_SOURCES, force=force)


async def sync_if_changed(force: bool = False) -> list[Path]:
    """Backward-compatible helper: refresh every source explicitly."""
    async with SYNC_LOCK:
        return await _sync_sources(set(SOURCES), force=force)


def sync_in_progress() -> bool:
    """Whether another API synchronization is currently running."""
    return SYNC_LOCK.locked()


def get_sync_status() -> dict[str, dict[str, Any]]:
    """Return per-source synchronization metadata for display in the bot."""
    state = _migrate_legacy_timestamps(_state())
    return {filename: dict(state.get(filename, {})) for filename in SOURCES}
