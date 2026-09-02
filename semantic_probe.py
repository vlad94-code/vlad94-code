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
        — прогнать свои вопросы.

Как читать: если у «правильной» записи балл, например, 0.66, а у всех
чужих — ниже 0.55, то порог 0.62–0.64 примет нужное и отсечёт лишнее.
Ставится он в .env строкой REFERENCE_SEMANTIC_THRESHOLD=0.63.
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


def main() -> None:
    import numpy as np

    import reference_lookup
    import semantic_reference

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
