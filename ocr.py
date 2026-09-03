"""Распознавание текста (OCR) для PDF, экспортированных как картинки.

Паспорта и часть каталогов CNC сделаны в CorelDRAW и сохранены так, что
текст лежит «в кривых» — для программы это картинка, из неё `pdfplumber`
не вытаскивает ни символа (проверено: 0 символов на всех страницах). Чтобы
такой массив всё же попал в базу знаний, страница рендерится в изображение
и распознаётся Tesseract'ом. Печатный текст в этих файлах чистый, поэтому
распознавание почти безошибочно на прозе (применение, условия, стандарты).

⚠️ ЧИСЛА из OCR-таблиц НЕНАДЁЖНЫ: при распознавании колонки «съезжают», и
ток/мощность могут перепутаться местами. Поэтому OCR-текст идёт в поиск как
СПРАВОЧНАЯ проза, а точные характеристики бот берёт из структурного
источника (1С/каталог), а не отсюда (тот же принцип, что ARCHITECTURE §5:
числа — из структурированного источника, не «распознанные с картинки»).

Всё здесь НЕОБЯЗАТЕЛЬНО: если не установлены `pymupdf`/`pytesseract` или в
системе нет самого Tesseract — `available()` вернёт False, а `ocr_page()`
вернёт пустую строку. Тогда парсер ведёт себя как раньше (без OCR), и ничего
не падает. Установка — см. requirements-ocr.txt и README.
"""
from __future__ import annotations

import io
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Где искать сам бинарь Tesseract, если его нет в PATH. Так пользователю на
# Windows НЕ нужно вручную править переменную PATH: достаточно установить
# Tesseract в папку по умолчанию. Явный путь можно задать в .env через
# TESSERACT_CMD, он проверяется первым.
_TESSERACT_CANDIDATES = (
    os.environ.get("TESSERACT_CMD"),
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
)


def _configure_tesseract(pytesseract) -> None:
    """Указать pytesseract путь к tesseract.exe, если он не в PATH.
    Молча ничего не делает, если бинарь и так в PATH (Linux/mac)."""
    for candidate in _TESSERACT_CANDIDATES:
        if candidate and Path(candidate).exists():
            pytesseract.pytesseract.tesseract_cmd = candidate
            return

# Язык(и) распознавания. Русский + латиница: в паспортах и то и другое
# (обозначения серий, стандарты IEC). Требует установленных языковых пакетов
# Tesseract (tesseract-ocr-rus).
LANG = os.environ.get("OCR_LANG", "rus+eng")

# Разрешение рендера страницы перед распознаванием. 300 dpi — золотая
# середина: на 150 качество заметно падает, на 400+ растёт время и память
# без выигрыша на этих чистых печатных страницах.
DPI = int(os.environ.get("OCR_DPI", "300"))

# Ниже этого числа символов текст страницы считается «пустым» и включается
# OCR. Не ноль: у картиночного PDF pdfplumber иногда выдаёт пару символов
# мусора (номер страницы, артефакт), а осмысленного текста там нет.
MIN_TEXT_CHARS = int(os.environ.get("OCR_MIN_TEXT_CHARS", "24"))

_available: bool | None = None


def available() -> bool:
    """Готов ли OCR: установлены pymupdf, pytesseract и сам бинарь Tesseract.
    Результат кэшируется — проверка бинаря делает системный вызов."""
    global _available
    if _available is not None:
        return _available
    try:
        import pymupdf  # noqa: F401
        import pytesseract
        _configure_tesseract(pytesseract)  # найти tesseract.exe вне PATH (Windows)
        pytesseract.get_tesseract_version()
        _available = True
    except Exception:
        logger.info("ocr: OCR недоступен (нет pymupdf/pytesseract или бинаря Tesseract) — распознавание выключено")
        _available = False
    return _available


def reset_cache() -> None:
    """Сбросить кэш доступности — для тестов, подменяющих окружение."""
    global _available
    _available = None


def ocr_page(pdf_path, page_index: int, *, dpi: int = DPI, lang: str = LANG) -> str:
    """Распознать одну страницу PDF (индекс с нуля). Пустая строка при любой
    неудаче — распознавание не должно ронять загрузку документа."""
    if not available():
        return ""
    try:
        import pymupdf
        import pytesseract
        from PIL import Image

        with pymupdf.open(pdf_path) as document:
            if page_index < 0 or page_index >= document.page_count:
                return ""
            pixmap = document[page_index].get_pixmap(dpi=dpi)
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))
        text = pytesseract.image_to_string(image, lang=lang)
        return text.strip()
    except Exception:
        logger.warning("ocr: не удалось распознать стр. %s файла %s", page_index + 1, pdf_path, exc_info=True)
        return ""


def page_count(pdf_path) -> int:
    """Число страниц по версии pymupdf (тот же движок, что рендерит для OCR).
    0, если OCR недоступен или файл не открылся."""
    if not available():
        return 0
    try:
        import pymupdf

        with pymupdf.open(pdf_path) as document:
            return document.page_count
    except Exception:
        return 0
