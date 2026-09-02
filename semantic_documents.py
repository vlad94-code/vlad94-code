"""Поиск по СМЫСЛУ по всему массиву документов (паспорта, каталоги).

`semantic_reference.py` ищет по 89 кураторским вопросам справочника. Этот
модуль распространяет тот же смысловой поиск на ВЕСЬ документный индекс
(`knowledge_matrix`, таблица `chunks`) — сотни паспортов и каталогов, в том
числе распознанных OCR. Именно он заменяет убранный внешний ИИ: на свободный
вопрос бот находит самый близкий по смыслу АБЗАЦ из документа и отдаёт его
дословно, с указанием источника — ничего не выдумывая.

Отличия от semantic_reference:
- источник — не 89 вопросов, а тысячи абзацев документов; поэтому индекс
  крупнее, строится дольше и кэшируется на диск (data/document_embeddings.npz);
- берутся только куски kind='catalog' (документы). Записи kind='api' — это
  ~25 000 структурных строк товаров из 1С; их обслуживает каталожный движок,
  а в смысловой индекс они бы только шумели и раздували память;
- модель эмбеддингов ПЕРЕИСПОЛЬЗУЕТСЯ из semantic_reference (_embedder) —
  одна копия на процесс, важно на слабом железе (4 ГБ ОЗУ).

Порог (REFERENCE_DOC_THRESHOLD) отдельный от справочника: абзацы длиннее и
«шумнее» вопросов, шкала близости другая. Подбирается на реальных данных.

⚠️ Абзац может содержать OCR-таблицу с числами, а числа из OCR ненадёжны
(колонки съезжают). Поэтому ответ всегда идёт с оговоркой «сверьте
критичные числа с паспортом», а точные характеристики — из 1С (ARCHITECTURE §5).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("data") / "knowledge.db"
_CACHE_PATH = Path("data") / "document_embeddings.npz"

# Порог косинусной близости для документов. По умолчанию мягче справочного
# (0.72): абзац редко совпадает с вопросом так же тесно, как кураторский
# вопрос. Настраивается на реальных данных (см. semantic_probe.py --docs).
THRESHOLD = float(os.environ.get("REFERENCE_DOC_THRESHOLD", "0.55"))

# Границы длины абзаца-кандидата. Слишком короткие («1.2 Условия») ничего не
# несут и шумят; слишком длинные разбавляют вектор — режем на куски.
_MIN_CHARS = int(os.environ.get("REFERENCE_DOC_MIN_CHARS", "40"))
_MAX_CHARS = int(os.environ.get("REFERENCE_DOC_MAX_CHARS", "600"))


@dataclass(frozen=True)
class DocumentMatch:
    source: str
    page: str
    text: str
    score: float


def _split_passages(page_text: str) -> list[str]:
    """Разбить текст страницы на абзацы-кандидаты для поиска.

    По пустым строкам, со схлопыванием пробелов; заголовок «## Страница N»
    выбрасывается; слишком длинные абзацы режутся по _MAX_CHARS, слишком
    короткие пропускаются.
    """
    body = re.sub(r"^## Страница \d+\s*$", "", page_text, flags=re.M).strip()
    passages: list[str] = []
    for block in re.split(r"\n\s*\n", body):
        text = " ".join(block.split())
        if len(text) < _MIN_CHARS:
            continue
        while len(text) > _MAX_CHARS:
            passages.append(text[:_MAX_CHARS])
            text = text[_MAX_CHARS:]
        if len(text) >= _MIN_CHARS:
            passages.append(text)
    return passages


def _load_passages() -> list[tuple[str, str, str]]:
    """(источник, страница, текст абзаца) по всем документам индекса.
    Только kind='catalog' — структурные строки товаров (kind='api') сюда не
    берём. Пусто, если базы ещё нет."""
    if not DB_PATH.exists():
        return []
    rows: list[tuple[str, str, str]] = []
    try:
        connection = sqlite3.connect(DB_PATH)
        try:
            cursor = connection.execute(
                "SELECT source, page, text FROM chunks WHERE kind = 'catalog'"
            )
            for source, page, text in cursor.fetchall():
                for passage in _split_passages(text or ""):
                    rows.append((source, str(page or ""), passage))
        finally:
            connection.close()
    except sqlite3.OperationalError:
        # Таблицы ещё нет (индекс не строился) — не ошибка, просто пусто.
        return []
    return rows


def _fingerprint(passages: list[tuple[str, str, str]], model_name: str) -> str:
    hasher = hashlib.sha256()
    hasher.update(model_name.encode("utf-8"))
    for source, page, text in passages:
        hasher.update(source.encode("utf-8"))
        hasher.update(page.encode("utf-8"))
        hasher.update(text.encode("utf-8"))
    return hasher.hexdigest()


_index: tuple[str, list[tuple[str, str, str]], object] | None = None


def reset_cache() -> None:
    global _index
    _index = None


def _load_disk_cache(fingerprint: str):
    if not _CACHE_PATH.exists():
        return None
    try:
        import numpy as np

        data = np.load(_CACHE_PATH, allow_pickle=True)
        if str(data["fingerprint"]) != fingerprint:
            return None
        return data["vectors"]
    except Exception:
        return None


def _save_disk_cache(fingerprint: str, vectors) -> None:
    try:
        import numpy as np

        _CACHE_PATH.parent.mkdir(exist_ok=True, parents=True)
        np.savez(_CACHE_PATH, fingerprint=fingerprint, vectors=vectors)
    except Exception:
        logger.debug("semantic_documents: не удалось сохранить кэш векторов", exc_info=True)


def _build_index():
    """Собрать (или взять из кэша) матрицу векторов абзацев документов.
    Возвращает (passages, нормированная_матрица) или None."""
    global _index
    import semantic_reference

    embed = semantic_reference._embedder()  # общая модель, одна копия на процесс
    if embed is None:
        return None

    passages = _load_passages()
    if not passages:
        return None

    fingerprint = _fingerprint(passages, semantic_reference.MODEL_NAME)
    if _index is not None and _index[0] == fingerprint:
        return _index[1], _index[2]

    import numpy as np

    vectors = _load_disk_cache(fingerprint)
    if vectors is None:
        logger.info("semantic_documents: строю эмбеддинги для %d абзацев (разово)", len(passages))
        vectors = np.array(embed([text for _s, _p, text in passages]), dtype="float32")
        _save_disk_cache(fingerprint, vectors)
    matrix = np.asarray(vectors, dtype="float32")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix = matrix / norms
    _index = (fingerprint, passages, matrix)
    return passages, matrix


def best_passage(question: str, threshold: float = THRESHOLD) -> DocumentMatch | None:
    """Самый близкий по смыслу абзац документа к вопросу — или None.
    None: поиск выключен/нет модели, индекс пуст, или ничего не дотянуло до
    порога (тогда вопрос честно уходит человеку)."""
    question = (question or "").strip()
    if not question:
        return None
    built = _build_index()
    if built is None:
        return None
    passages, matrix = built

    import semantic_reference

    embed = semantic_reference._embedder()
    if embed is None:
        return None
    import numpy as np

    query = np.asarray(embed([question]), dtype="float32")[0]
    norm = np.linalg.norm(query)
    if norm == 0:
        return None
    query = query / norm

    scores = matrix @ query
    best = int(np.argmax(scores))
    best_score = float(scores[best])
    if best_score < threshold:
        return None
    source, page, text = passages[best]
    return DocumentMatch(source=source, page=page, text=text, score=best_score)


def is_available() -> bool:
    """Готов ли смысловой поиск по документам (есть модель и непустой индекс)."""
    import semantic_reference

    return semantic_reference.is_available() and bool(_load_passages())
