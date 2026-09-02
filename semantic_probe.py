"""«Измеритель» смыслового поиска: показать баллы близости для вопросов.

Зачем. Порог `REFERENCE_SEMANTIC_THRESHOLD` (по умолчанию 0.72) решает,
принять смысловое совпадение или промолчать. Слишком высокий — бот не
узнаёт переформулировки; слишком низкий — отдаёт чужой ответ под меткой
«подтверждено инженером» (худшая ошибка). Подбирать порог наугад нельзя —
нужно ВИДЕТЬ реальные баллы. Этот скрипт их и печатает: для каждого вопроса
показывает три ближайшие записи справочника с их косинусной близостью.

Запуск (из папки бота, с активным venv, где установлен fastembed):

    python semantic_probe.py
        — прогнать встроенный тестовый набор;

    python semantic_probe.py "а YCW3 из корзины можно выкатить?" "ещё вопрос"
        — прогнать свои вопросы по СПРАВОЧНИКУ (89 записей);

    python semantic_probe.py --docs "вопрос про паспорт" "ещё вопрос"
        — прогнать по ДОКУМЕНТАМ (паспорта/каталоги, semantic_documents):
          показывает три ближайших АБЗАЦА с баллами, чтобы подобрать
          REFERENCE_DOC_THRESHOLD (по умолчанию 0.55).

Как читать: если у «правильной» записи балл, например, 0.66, а у всех
чужих — ниже 0.55, то порог 0.62–0.64 примет нужное и отсечёт лишнее.
Ставится он в .env строкой REFERENCE_SEMANTIC_THRESHOLD=0.63 (справочник)
или REFERENCE_DOC_THRESHOLD=... (документы).
"""
from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv()

# Встроенный набор: смесь настоящих переформулировок и заведомо чужого
# вопроса (погода) — чтобы сразу видеть и «своё», и «чужое» на одной шкале.
DEFAULT_QUESTIONS = [
    "а YCW3 из корзины можно выкатить?",
    "выкатной ли YCW3",
    "что значит вкачено и выкачено",
    "во сколько встанет доставка",
    "сколько ждать заказную позицию",
    "какая гарантия на автоматы",
    "какая сегодня погода на улице",  # чужой — должен быть НИЗКИЙ балл у всех
]


def _probe_documents(questions) -> None:
    """Прогон вопросов по документному индексу (паспорта/каталоги)."""
    import numpy as np

    import semantic_documents
    import semantic_reference

    if not semantic_reference.is_available():
        print("❌ Поиск по смыслу выключен: модель эмбеддингов недоступна "
              "(pip install -r requirements-semantic.txt).")
        return
    built = semantic_documents._build_index()
    if built is None:
        print("❌ Документный индекс пуст: сначала загрузите паспорта/каталоги "
              "в бота (и, для картиночных PDF, установите OCR — requirements-ocr.txt).")
        return
    passages, matrix = built
    embed = semantic_reference._embedder()
    threshold = semantic_documents.THRESHOLD
    print(f"Абзацев в индексе: {len(passages)}. Порог REFERENCE_DOC_THRESHOLD = {threshold}\n")
    for question in questions:
        query = np.asarray(embed([question]), dtype="float32")[0]
        query = query / (np.linalg.norm(query) or 1.0)
        scores = matrix @ query
        order = np.argsort(-scores)[:3]
        print(f"❓ {question!r}")
        for rank, idx in enumerate(order, 1):
            score = float(scores[idx])
            mark = "✅ примет" if rank == 1 and score >= threshold else "  "
            source, page, text = passages[int(idx)]
            print(f"   {rank}. {score:.3f} {mark}  [{source}, стр.{page}] {text[:90]}")
        print()


def main() -> None:
    import numpy as np

    import reference_lookup
    import semantic_reference

    args = sys.argv[1:]
    if args and args[0] == "--docs":
        _probe_documents(args[1:] or DEFAULT_QUESTIONS)
        return

    if not semantic_reference.is_available():
        print("❌ Поиск по смыслу выключен: модель эмбеддингов недоступна.")
        print("   Установите:  pip install -r requirements-semantic.txt")
        print("   и дайте боту один раз скачать модель (нужен интернет).")
        return

    built = semantic_reference._build_index()
    if built is None:
        print("❌ Справочник пуст или индекс не собрался.")
        return
    entries, matrix = built
    embed = semantic_reference._embedder()

    questions = sys.argv[1:] or DEFAULT_QUESTIONS
    threshold = semantic_reference.THRESHOLD
    print(f"Текущий порог REFERENCE_SEMANTIC_THRESHOLD = {threshold}\n")

    for question in questions:
        query = np.asarray(embed([question]), dtype="float32")[0]
        norm = np.linalg.norm(query)
        query = query / (norm or 1.0)
        scores = matrix @ query
        order = np.argsort(-scores)[:3]
        print(f"❓ {question!r}")
        for rank, idx in enumerate(order, 1):
            score = float(scores[idx])
            mark = "✅ примет" if rank == 1 and score >= threshold else "  "
            print(f"   {rank}. {score:.3f} {mark}  → {entries[int(idx)].question!r}")
        print()


if __name__ == "__main__":
    main()
