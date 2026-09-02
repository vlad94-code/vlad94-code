"""Поиск по СМЫСЛУ поверх справочника инженера (`knowledge/unique_answers.md`).

`reference_lookup.py` сопоставляет вопрос клиента с записью справочника по
СЛОВАМ (совпадение токенов, взвешенное по редкости). Это точно ловит
переформулировки, где сохранились редкие слова («YCW3», «гарантия»), но
беспомощно там, где человек сказал то же самое ДРУГИМИ словами:
«во сколько обойдётся доставка» против «Какая стоимость доставки?» — общих
значимых слов нет, и словарный поиск молчит.

Этот модуль добавляет второй, смысловой проход: вопрос и записи справочника
переводятся в числовые векторы («смысловые отпечатки») небольшой локальной
моделью, а близость считается косинусом между векторами. Синонимы и иные
формулировки одной мысли дают близкие векторы, поэтому находятся даже без
общих слов.

Три свойства, важные для этого проекта:

1. **Полностью локально.** Модель работает офлайн, на нашем железе; вопрос
   клиента никуда не отправляется (ARCHITECTURE §12). Внешний ИИ не нужен.
2. **Необязательно.** Если библиотека `fastembed` не установлена или модель
   не скачана (например, в CI без сети), модуль тихо отключается и
   `best_match()` возвращает None. Тогда работает прежний словарный поиск —
   ничего не ломается. Так набор тестов остаётся зелёным без модели.
3. **Консервативно, как и словарный поиск.** Отдаём совпадение только выше
   порога уверенности (`THRESHOLD`) и молчим при неоднозначности: ложный
   «подтверждённый инженером» ответ хуже, чем передать вопрос человеку
   (тот же принцип, что в `reference_lookup`).

Кэш: 89 записей справочника переводятся в векторы один раз и складываются в
`data/reference_embeddings.npz`. Пересчёт запускается сам, если изменился
текст справочника или имя модели (ключ кэша — их хеш) — как и веса слов в
`reference_lookup._word_weights`, считать это на каждый вопрос незачем.
"""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

logger = logging.getLogger(__name__)

# Модель по умолчанию: многоязычная MiniLM, ~220 МБ, размерность 384.
# Выбрана под слабое железо (ноутбук с 4 ГБ ОЗУ): на порядок легче
# multilingual-e5-large (2.24 ГБ) и уверенно понимает русский. Сменить —
# переменной окружения, не правкой кода.
MODEL_NAME = os.environ.get(
    "REFERENCE_SEMANTIC_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)

# Порог косинусной близости, ниже которого совпадение не принимается.
# Для этой модели настоящие переформулировки дают ~0.6–0.9, а разные по
# смыслу вопросы — ~0.1–0.4, поэтому 0.72 отсекает чужое, сохраняя своё.
# Значение подбирается на реальном справочнике (см. scripts ниже) и
# переопределяется окружением — оно зависит и от модели, и от того, как
# сформулированы вопросы в конкретном `unique_answers.md`.
THRESHOLD = float(os.environ.get("REFERENCE_SEMANTIC_THRESHOLD", "0.72"))

# Ширина «куста» вокруг лучшего балла: записи в пределах этого разрыва от
# лидера считаются одинаково близкими и разбираются тай-брейком (см.
# best_match). У вопросов про одно изделие несколько записей справочника
# закономерно стоят вплотную (например все про YCW3 — 0.829/0.828/0.825),
# поэтому «куст» здесь ожидаем и сам по себе НЕ повод молчать.
_AMBIGUITY_MARGIN = float(os.environ.get("REFERENCE_SEMANTIC_MARGIN", "0.03"))

# Уровень уверенности, выше которого лучший кандидат берётся даже когда
# тай-брейк по словам не развёл «куст»: балл заведомо высокий, а порог уже
# гарантировал, что тема правильная — молчать здесь хуже, чем отдать лучший
# по смыслу ответ. Ниже этого уровня неразрешённая ничья между РАЗНЫМИ
# ответами по-прежнему приводит к молчанию (безопаснее промолчать, чем
# выдать чужой ответ под меткой «подтверждено инженером»).
_HIGH_CONFIDENCE = float(os.environ.get("REFERENCE_SEMANTIC_CONFIDENT", "0.80"))

# Выключатель на случай, если семантику надо отключить целиком, не удаляя
# библиотеку (например, чтобы сравнить поведение или сэкономить память).
_ENABLED = os.environ.get("REFERENCE_SEMANTIC", "1").strip().lower() not in {"0", "false", "no", ""}

_CACHE_PATH = Path("data") / "reference_embeddings.npz"


# Тип бэкенда: функция, превращающая список строк в список векторов
# (каждый вектор — список float одинаковой длины). Ровно этот шов
# подменяют тесты фиктивным детерминированным эмбеддером, чтобы проверить
# ЛОГИКУ отбора без скачивания модели.
Embedder = Callable[[Sequence[str]], "list"]

_backend: Embedder | None = None
_backend_loaded = False


def _load_backend() -> Embedder | None:
    """Собрать эмбеддер на базе fastembed (ONNX, без тяжёлого PyTorch).

    Возвращает None, если библиотека не установлена или модель недоступна —
    тогда весь модуль работает как «выключенный», и вызывающий откатывается
    на словарный поиск. Именно эту функцию подменяют тесты.
    """
    if not _ENABLED:
        return None
    try:
        from fastembed import TextEmbedding  # type: ignore
    except Exception:
        logger.info("semantic_reference: fastembed не установлен — смысловой поиск выключен")
        return None
    try:
        model = TextEmbedding(model_name=MODEL_NAME)
    except Exception:
        # Самая частая причина здесь — модель ещё не скачана, а сеть недоступна
        # (CI, закрытый контур). Не ошибка конфигурации: просто нет модели.
        logger.info("semantic_reference: модель %s недоступна — смысловой поиск выключен", MODEL_NAME)
        return None

    def embed(texts: Sequence[str]) -> list:
        return [list(vector) for vector in model.embed(list(texts))]

    return embed


def _embedder() -> Embedder | None:
    global _backend, _backend_loaded
    if not _backend_loaded:
        _backend = _load_backend()
        _backend_loaded = True
    return _backend


def reset_cache() -> None:
    """Сбросить загруженный бэкенд и векторы — для тестов, подменяющих
    эмбеддер или справочник (аналог reference_lookup.clear_cache)."""
    global _backend, _backend_loaded, _index
    _backend = None
    _backend_loaded = False
    _index = None


@dataclass(frozen=True)
class SemanticMatch:
    question: str
    answer: str
    category: str
    score: float


# Кэш собранного индекса в памяти на время жизни процесса: (fingerprint,
# entries, матрица векторов). Пересобирается, когда меняется отпечаток
# справочника или имя модели.
_index: tuple[str, tuple, object] | None = None


def _fingerprint(questions: Sequence[str]) -> str:
    payload = (MODEL_NAME + "\n" + "\n".join(questions)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalize(matrix):
    import numpy as np

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


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
        # Битый или несовместимый кэш — просто пересчитаем, не падаем.
        return None


def _save_disk_cache(fingerprint: str, vectors) -> None:
    try:
        import numpy as np

        _CACHE_PATH.parent.mkdir(exist_ok=True, parents=True)
        np.savez(_CACHE_PATH, fingerprint=fingerprint, vectors=vectors)
    except Exception:
        logger.debug("semantic_reference: не удалось сохранить кэш векторов", exc_info=True)


def _build_index():
    """Собрать (или взять из кэша) матрицу векторов вопросов справочника.

    Возвращает (entries, нормированная_матрица) или None, если эмбеддер
    недоступен либо справочник пуст.
    """
    global _index
    embed = _embedder()
    if embed is None:
        return None

    import reference_lookup  # локальный импорт: избегаем кольца импортов

    entries = reference_lookup.entries()
    questions = [entry.question for entry in entries]
    if not questions:
        return None

    fingerprint = _fingerprint(questions)
    if _index is not None and _index[0] == fingerprint:
        return _index[1], _index[2]

    import numpy as np

    vectors = _load_disk_cache(fingerprint)
    if vectors is None:
        vectors = np.array(embed(questions), dtype="float32")
        _save_disk_cache(fingerprint, vectors)
    matrix = _normalize(np.asarray(vectors, dtype="float32"))
    _index = (fingerprint, entries, matrix)
    return entries, matrix


def best_match(question: str, threshold: float = THRESHOLD) -> SemanticMatch | None:
    """Лучшая по смыслу запись справочника для вопроса — или None.

    None означает одно из трёх: смысловой поиск выключен/модель недоступна;
    ближайшая запись не дотянула до порога; либо две записи с РАЗНЫМИ
    ответами оказались одинаково близки (неоднозначность — молчим).
    """
    question = (question or "").strip()
    if not question:
        return None
    built = _build_index()
    if built is None:
        return None
    entries, matrix = built

    embed = _embedder()
    if embed is None:
        return None
    import numpy as np

    query = np.asarray(embed([question]), dtype="float32")[0]
    norm = np.linalg.norm(query)
    if norm == 0:
        return None
    query = query / norm

    scores = matrix @ query  # косинус: обе стороны уже нормированы
    order = [int(i) for i in np.argsort(-scores)]
    best = order[0]
    best_score = float(scores[best])
    if best_score < threshold:
        return None

    # «Куст» — записи в пределах _AMBIGUITY_MARGIN от лидера. Если у них у всех
    # один и тот же ответ (или куст из одной записи) — неоднозначности нет,
    # берём лидера.
    cluster = [i for i in order if best_score - float(scores[i]) <= _AMBIGUITY_MARGIN]
    chosen = best
    if len({entries[i].answer for i in cluster}) > 1:
        chosen = _resolve_cluster(question, entries, scores, cluster, best, best_score)
        if chosen is None:
            return None

    entry = entries[chosen]
    return SemanticMatch(
        question=entry.question,
        answer=entry.answer,
        category=entry.category,
        score=float(scores[chosen]),
    )


def _resolve_cluster(question, entries, scores, cluster, best, best_score):
    """Разрешить «куст» одинаково близких записей с РАЗНЫМИ ответами.

    Три исхода по возрастанию осторожности:
      1) тай-брейк по СЛОВАМ — если ровно одна запись куста делит с вопросом
         больше всего значимых слов, берём её (тот же словарный сигнал, что и
         в reference_lookup, только для разведения смыслового куста);
      2) слова не развели, но балл заведомо высокий (>= _HIGH_CONFIDENCE) —
         тема точно верная, отдаём лучший по смыслу, а не молчим;
      3) иначе (ничья и невысокий балл) — молчим: чужой ответ под меткой
         «подтверждено инженером» хуже, чем передать вопрос человеку.
    Возвращает индекс выбранной записи или None (молчание).
    """
    import reference_lookup  # тот же разбор значимых слов, что и в словарном поиске

    question_words = reference_lookup._meaningful_words(question)

    def overlap(index: int) -> int:
        entry_words = reference_lookup._meaningful_words(entries[index].question)
        matched_q, _ = reference_lookup._matched_words(question_words, entry_words)
        return len(matched_q)

    overlaps = {index: overlap(index) for index in cluster}
    top_overlap = max(overlaps.values())
    leaders = [index for index in cluster if overlaps[index] == top_overlap]
    if top_overlap > 0 and len(leaders) == 1:
        return leaders[0]
    if best_score >= _HIGH_CONFIDENCE:
        return best
    return None


def is_available() -> bool:
    """Готов ли смысловой поиск (установлена библиотека и загрузилась модель).
    Удобно для диагностики (`/status`, diagnose.py) — показать сотруднику,
    работает ли поиск по смыслу или бот на словарном поиске."""
    return _embedder() is not None
