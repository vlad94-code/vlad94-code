"""Резервное копирование файлов, которые нельзя пересобрать.

`core/db.py` бэкапит `data/bot.db`. Здесь — `uploads/`: исходные каталоги,
паспорта и прайс-листы, загруженные инженером. Разница принципиальная:
`data/knowledge.db` пересобирается из этих файлов командой `/reindex`, а сами
файлы не восстанавливаются ниоткуда. До появления этого модуля они
существовали в одном экземпляре (ARCHITECTURE.md §16, открытый вопрос №9).

Копирование архивов за пределы машины — задача эксплуатации, не кода.
См. README, раздел «Бэкапы».
"""
from __future__ import annotations

import hashlib
import logging
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _uploads_dir() -> Path:
    return Path(os.environ.get("UPLOADS_DIR", "uploads"))


def _backup_dir() -> Path:
    return Path(os.environ.get("BACKUP_DIR", "backups"))


def fingerprint(src: Path) -> str:
    """Отпечаток состава каталога: путь + размер + mtime каждого файла.

    Содержимое не читаем: 57 МБ хешировать ежедневно ради каталога, который
    меняется раз в месяц, — трата процессорного времени впустую.
    """
    parts: list[str] = []
    for file in sorted(src.rglob("*")):
        if file.is_file():
            stat = file.stat()
            parts.append(f"{file.relative_to(src).as_posix()}|{stat.st_size}|{int(stat.st_mtime)}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def backup_uploads(
    src: Path | None = None,
    backup_dir: Path | None = None,
    keep: int = 5,
) -> Path | None:
    """Архивирует `uploads/` в zip, если состав каталога изменился.

    Возвращает путь к архиву или None, если с прошлого раза ничего не менялось.
    Хранит последние `keep` архивов: файлы крупные, а меняются редко — держать
    их 30 дней, как копии bot.db, смысла нет.
    """
    source = src or _uploads_dir()
    out_dir = backup_dir or _backup_dir()

    if not source.exists():
        logger.warning("Uploads backup skipped: %s does not exist", source)
        return None

    current = fingerprint(source)
    marker = out_dir / "uploads_last.sha256"
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == current:
        logger.info("Uploads unchanged since last backup; nothing to archive")
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = out_dir / f"uploads_{stamp}.zip"
    # Метка времени с точностью до секунды: два вызова подряд (ручной бэкап
    # сразу после ежедневного) дали бы одно имя, и второй архив молча затёр бы
    # первый. Разводим суффиксом вместо потери файла.
    attempt = 2
    while dest.exists():
        dest = out_dir / f"uploads_{stamp}_{attempt}.zip"
        attempt += 1

    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(source.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(source).as_posix())

    marker.write_text(current, encoding="utf-8")
    logger.info("Uploads archived to %s", dest)
    prune_old_upload_archives(keep=keep, backup_dir=out_dir)
    return dest


def prune_old_upload_archives(keep: int = 5, backup_dir: Path | None = None) -> int:
    """Оставляет `keep` самых свежих архивов uploads, остальные удаляет."""
    out_dir = backup_dir or _backup_dir()
    if not out_dir.exists():
        return 0
    archives = sorted(out_dir.glob("uploads_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for stale in archives[keep:]:
        try:
            stale.unlink()
            removed += 1
        except OSError:
            continue
    return removed
