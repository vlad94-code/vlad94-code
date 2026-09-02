"""Register + index a file already sitting in uploads/, without Telegram.

Mirrors bot.py::upload_document()'s logic minus the Telegram-specific
download step — reusable whenever a document lands in uploads/ by some
other route (manually copied, synced from elsewhere) and needs to become
part of the bot's searchable knowledge base the same way an in-chat upload
would.

Usage:
    python register_local_document.py uploads/<filename>
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from catalog_parser import extract_catalog_facts, parse_generic_document, parse_pdf_catalog
from core.documents import allocate_slot, register_document, replace_document_facts
from knowledge_matrix import rebuild

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Путь к файлу, уже лежащему в uploads/")
    args = parser.parse_args()

    source_path: Path = args.path
    if not source_path.exists():
        sys.exit(f"Файл не найден: {source_path}")
    suffix = source_path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        sys.exit(f"Неподдерживаемое расширение: {suffix}. Поддерживаются: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")

    filename = source_path.name
    slot = allocate_slot(filename)
    upload_dir = source_path.parent
    local_path = upload_dir / slot.stored_name
    if local_path != source_path:
        shutil.copyfile(source_path, local_path)

    parsed_path = upload_dir / f"{local_path.stem}.parsed.md"
    if suffix == ".pdf":
        parse_pdf_catalog(local_path, parsed_path, filename)
    else:
        parse_generic_document(local_path, parsed_path, filename)

    register_document(slot, local_path, parsed_path=parsed_path)
    fact_count = replace_document_facts(slot.stored_name, extract_catalog_facts(parsed_path))
    pages, records = rebuild()

    page_count = parsed_path.read_text(encoding="utf-8", errors="ignore").count("## Страница")
    print(f"«{filename}» зарегистрирован (версия {slot.version}).")
    print(f"Страниц в разобранном файле: {page_count}. Извлечено характеристик: {fact_count}.")
    print(f"Матрица знаний перестроена: страниц каталогов — {pages}, записей API — {records}.")


if __name__ == "__main__":
    main()
