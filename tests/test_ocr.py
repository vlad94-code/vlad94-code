"""OCR-конвейер: graceful-поведение и запасной путь в парсере PDF.

Реальный Tesseract в тестах не нужен (в CI его нет): проверяется, что без
OCR ничего не падает, а с OCR парсер зовёт распознавание ровно для пустых
страниц. Само качество распознавания проверяется вручную на живых паспортах.
"""
from __future__ import annotations

import catalog_parser
import ocr


def test_ocr_page_empty_when_unavailable(monkeypatch):
    """Нет Tesseract → ocr_page возвращает пустую строку, не падает."""
    monkeypatch.setattr(ocr, "available", lambda: False)
    assert ocr.ocr_page("любой.pdf", 0) == ""


def test_available_is_cached_and_resettable(monkeypatch):
    monkeypatch.setattr(ocr, "_available", None)
    # Подменяем внутреннюю проверку через отсутствие модулей нельзя, поэтому
    # просто убеждаемся, что available() возвращает bool и кэширует результат.
    first = ocr.available()
    assert isinstance(first, bool)
    assert ocr._available is first
    ocr.reset_cache()
    assert ocr._available is None


class _FakePage:
    def __init__(self, text: str = "", tables=None):
        self._text = text
        self._tables = tables or []

    def extract_text(self, **_kwargs):
        return self._text

    def extract_tables(self):
        return self._tables


class _FakePDF:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _patch_pdf(monkeypatch, pages):
    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda _path: _FakePDF(pages))


def test_parser_calls_ocr_for_image_pages(monkeypatch, tmp_path):
    """Страница без текста и таблиц (картиночный PDF) → парсер распознаёт её
    и кладёт распознанный текст в разметку «## Страница N»."""
    _patch_pdf(monkeypatch, [_FakePage(text=""), _FakePage(text="")])
    monkeypatch.setattr(ocr, "MIN_TEXT_CHARS", 24)
    monkeypatch.setattr(ocr, "ocr_page", lambda _path, index, **_k: f"РАСПОЗНАНО-{index + 1}")

    out = tmp_path / "parsed.md"
    catalog_parser.parse_pdf_catalog(tmp_path / "image.pdf", out, "Паспорт")
    markdown = out.read_text(encoding="utf-8")
    assert "## Страница 1" in markdown and "РАСПОЗНАНО-1" in markdown
    assert "РАСПОЗНАНО-2" in markdown


def test_parser_skips_ocr_when_text_present(monkeypatch, tmp_path):
    """Есть настоящий текст → OCR не вызывается (не тратим время зря)."""
    _patch_pdf(monkeypatch, [_FakePage(text="Полноценный текст страницы паспорта про применение.")])

    def _boom(*_a, **_k):
        raise AssertionError("OCR не должен вызываться, когда текст уже есть")

    monkeypatch.setattr(ocr, "ocr_page", _boom)
    out = tmp_path / "parsed.md"
    catalog_parser.parse_pdf_catalog(tmp_path / "text.pdf", out, "Паспорт")
    assert "Полноценный текст страницы" in out.read_text(encoding="utf-8")


def test_parser_skips_ocr_when_tables_present(monkeypatch, tmp_path):
    """Короткий текст, но есть таблица (нормальный PDF со структурой) → это не
    картиночная страница, OCR не нужен."""
    _patch_pdf(monkeypatch, [_FakePage(text="", tables=[[["A", "B"], ["1", "2"]]])])

    def _boom(*_a, **_k):
        raise AssertionError("OCR не должен вызываться при наличии таблиц")

    monkeypatch.setattr(ocr, "ocr_page", _boom)
    out = tmp_path / "parsed.md"
    catalog_parser.parse_pdf_catalog(tmp_path / "table.pdf", out, "Каталог")
    assert "| A | B |" in out.read_text(encoding="utf-8")
