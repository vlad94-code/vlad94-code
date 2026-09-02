"""Доступ к файлам, выложенным в «Ориентире» (25.lgprk.ru).

Таблица номенклатуры несёт ссылки вида
`https://25.lgprk.ru/share?token=…`, и это не файл: по ней отдаётся
SPA-страница с кнопкой «Скачать файл». Telegram по такой ссылке покажет
HTML, а менеджер — не фотографию. Настоящий файл достаётся в два шага
(снято с живого сервиса 29.08.2026):

    POST https://api.25.lgprk.ru/api/share/verify  {"token": "…"}
    → {"access_granted": true,
       "entity_data": {"name": "YCM3YP-100.png", "byteSize": 592210,
                       "downloadUrl": "https://s3.lgprk.ru/attachments/…"}}

`downloadUrl` — presigned-ссылка на S3 со сроком жизни в час, поэтому её
нельзя ни сохранить в базу, ни отдать менеджеру: качать надо сразу.

Модуль отделён от media_links.py намеренно: это протокол чужого сервиса, а
не предметная область бота. Завтра файлы переедут на другой хостинг —
поменяется только этот файл.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.25.lgprk.ru"
SHARE_PATH = "/share"

# Как у паспортов (bot.py): таймаут на операцию не ограничивает загрузку
# целиком — сервис, отдающий файл по байту, укладывается в read-timeout
# бесконечно долго. Поэтому есть ещё общий бюджет на весь fetch().
TIMEOUT = 30.0
TOTAL_TIMEOUT = 180.0

# Потолок по умолчанию — предел Telegram на файл от бота.
SIZE_LIMIT = 50 * 1024 * 1024


@dataclass(frozen=True)
class SharedFile:
    download_url: str
    name: str
    size: int


def _client(timeout=None) -> httpx.AsyncClient:
    """Единая точка создания клиента: и таймауты, и подмена транспорта в тестах."""
    return httpx.AsyncClient(timeout=timeout, follow_redirects=True)


def token_of(url: str) -> str | None:
    """Токен шары или None, если ссылка ведёт прямо на файл."""
    if not url:
        return None
    parts = urlsplit(url)
    if parts.path.rstrip("/") != SHARE_PATH:
        return None
    token = parse_qs(parts.query).get("token", [""])[0].strip()
    return token or None


def filename_of(url: str) -> str:
    """Имя файла из самой ссылки — запасной вариант, когда настоящее имя
    взять неоткуда."""
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1]).strip()


async def resolve(share_url: str) -> SharedFile | None:
    """Presigned-ссылка и настоящее имя файла за страницей шары.

    None — если шару отозвали, закрыли паролем или сервис недоступен: это не
    ошибка бота, и вызывающий просто обходится без файла.
    """
    token = token_of(share_url)
    if token is None:
        return None
    try:
        async with _client(timeout=TIMEOUT) as client:
            response = await client.post(f"{API_BASE}/api/share/verify", json={"token": token})
            response.raise_for_status()
            payload = response.json()
    except Exception:
        logger.warning("Share verify failed: %s", share_url, exc_info=True)
        return None

    if not payload.get("access_granted"):
        logger.warning("Share is closed: %s", share_url)
        return None
    entity = payload.get("entity_data") or {}
    download_url = entity.get("downloadUrl")
    if not download_url:
        logger.warning("Share carries no downloadUrl: %s", share_url)
        return None
    name = str(entity.get("name") or "").strip() or filename_of(share_url) or "file"
    return SharedFile(download_url=download_url, name=name, size=int(entity.get("byteSize") or 0))


async def _fetch(url: str, size_limit: int) -> tuple[bytes, str] | None:
    token = token_of(url)
    if token is None:
        # Прямая ссылка: промежуточный шаг не нужен. Так контракт из
        # docs/MEDIA_LINKS.md («прямая ссылка на изображение») продолжит
        # работать, если следующая версия таблицы придёт без «Ориентира».
        target, name = url, filename_of(url) or "file"
    else:
        resolved = await resolve(url)
        if resolved is None:
            return None
        if resolved.size and resolved.size > size_limit:
            # Размер известен ещё из verify — качать файл, который всё равно
            # не влезет в Telegram, незачем.
            logger.warning("Shared file too large (%d bytes): %s", resolved.size, url)
            return None
        target, name = resolved.download_url, resolved.name

    async with _client(timeout=TIMEOUT) as client:
        response = await client.get(target)
        response.raise_for_status()
    if len(response.content) > size_limit:
        logger.warning("Downloaded file too large (%d bytes): %s", len(response.content), url)
        return None
    return response.content, name


async def fetch(url: str, size_limit: int = SIZE_LIMIT) -> tuple[bytes, str] | None:
    """Скачать файл по ссылке из таблицы. None — если не вышло."""
    try:
        return await asyncio.wait_for(_fetch(url, size_limit), timeout=TOTAL_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Share download timed out after %ss: %s", TOTAL_TIMEOUT, url)
        return None
    except Exception:
        logger.warning("Share download failed: %s", url, exc_info=True)
        return None
