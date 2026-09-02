"""Массовая загрузка папки документов в базу знаний бота (сотни паспортов).

Грузить сотни файлов по одному через Telegram — мучение. Этот скрипт берёт
целую папку, разбирает каждый файл (PDF-картинки распознаёт OCR'ом, если он
установлен), регистрирует как документ и в КОНЦЕ один раз пересобирает
индекс и эмбеддинги. Это то же, что делает загрузка через бота
(register_local_document.py), только пакетно и с устойчивостью к сбоям:
битый файл не роняет весь прогон.

Запуск (из папки бота, с активным venv):

    python bulk_ingest.py "C:\\путь\\к\\папке\\с\\паспортами"

Рекурсивно берёт .pdf/.docx/.txt/.md/.csv/.xlsx. Для распознавания
паспортов-картинок нужен OCR (см. requirements-ocr.txt) — без него такие PDF
попадут в базу пустыми (скрипт это предупредит).

Скорость: OCR тяжёлый (~3–5 сек/страница). Сотни паспортов считаются часы —
это разовая операция; лучше гнать на VPS, а не на слабом ноутбуке.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from catalog_parser import extract_catalog_facts, parse_generic_document, parse_pdf_catalog
from core.documents import allocate_slot, register_document, replace_document_facts

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx"}
UPLOAD_DIR = Path("uploads")


def _ingest_one(source_path: Path) -> tuple[int, int, bool]:
    """Разобрать и зарегистрировать один файл. Возвращает (страниц, фактов,
    похоже_на_пустой_PDF). Индекс здесь НЕ пересобирается — это делает main()
    один раз в конце."""
    filename = source_path.name
    slot = allocate_slot(filename)
    UPLOAD_DIR.mkdir(exist_ok=True, parents=True)
    local_path = UPLOAD_DIR / slot.stored_name
    shutil.copyfile(source_path, local_path)

    parsed_path = UPLOAD_DIR / f"{local_path.stem}.parsed.md"
    if source_path.suffix.lower() == ".pdf":
        parse_pdf_catalog(local_path, parsed_path, filename)
    else:
        parse_generic_document(local_path, parsed_path, filename)

    register_document(slot, local_path, parsed_path=parsed_path)
    facts = replace_document_facts(slot.stored_name, extract_catalog_facts(parsed_path))
    parsed_text = parsed_path.read_text(encoding="utf-8", errors="ignore")
    pages = parsed_text.count("## Страница")
    # Признак «пустого» PDF: на страницу приходится меньше ~80 символов —
    # значит остались одни заголовки «## Страница N», текста нет. Обычно это
    # картиночный PDF, который распознал бы OCR, но OCR не установлен.
    chars_per_page = len(parsed_text.strip()) / max(pages, 1)
    empty_pdf = source_path.suffix.lower() == ".pdf" and pages > 0 and chars_per_page < 80
    return pages, facts, empty_pdf


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Папка с документами (обходится рекурсивно)")
    args = parser.parse_args()

    folder: Path = args.folder
    if not folder.is_dir():
        sys.exit(f"Не папка: {folder}")

    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in SUPPORTED_EXTENSIONS)
    if not files:
        sys.exit(f"В «{folder}» нет поддерживаемых файлов ({', '.join(sorted(SUPPORTED_EXTENSIONS))}).")

    import ocr
    if ocr.available():
        print("OCR: включён (картиночные PDF будут распознаны).")
    else:
        print("⚠️  OCR ВЫКЛЮЧЕН: PDF-картинки (паспорта из CorelDRAW) попадут в базу пустыми.")
        print("    Поставьте requirements-ocr.txt и Tesseract, чтобы их распознать.")

    print(f"Найдено файлов: {len(files)}. Начинаю…\n")
    started = time.time()
    ok = failed = empty = 0
    for index, source_path in enumerate(files, start=1):
        tag = f"[{index}/{len(files)}] {source_path.name}"
        try:
            t0 = time.time()
            pages, facts, empty_pdf = _ingest_one(source_path)
            note = "  ⚠️ похоже на пустой PDF (нужен OCR)" if empty_pdf else ""
            if empty_pdf:
                empty += 1
            print(f"{tag}: страниц {pages}, характеристик {facts}, {time.time() - t0:.0f}с{note}")
            ok += 1
        except Exception as error:  # один битый файл не должен рушить весь прогон
            failed += 1
            print(f"{tag}: ОШИБКА — {type(error).__name__}: {error}")

    print("\nПересобираю индекс и эмбеддинги (один раз)…")
    from knowledge_matrix import rebuild
    pages, records = rebuild()
    # Прогреть документные эмбеддинги сразу, чтобы первый вопрос не ждал.
    try:
        import semantic_documents
        built = semantic_documents._build_index()
        embedded = len(built[0]) if built else 0
    except Exception:
        embedded = 0

    minutes = (time.time() - started) / 60
    print("\n" + "=" * 60)
    print(f"Готово за {minutes:.1f} мин. Загружено: {ok}, ошибок: {failed}, пустых PDF: {empty}.")
    print(f"Индекс: страниц-документов {pages}, записей API {records}.")
    if embedded:
        print(f"Смысловой индекс документов: {embedded} абзацев готовы к поиску.")
    elif ok:
        print("Смысловой поиск по документам не прогрет (нет модели — "
              "поставьте requirements-semantic.txt).")


if __name__ == "__main__":
    main()
