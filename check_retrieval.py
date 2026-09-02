"""Проверка поиска по документам БЕЗ Claude/API — что реально найдёт бот
в загруженных каталогах/письмах/ГОСТах для данного вопроса.

Показывает то же самое, что попало бы в контекст Claude, если бы API был
подключён — но без единого запроса наружу, полностью локально.

Запуск (из папки telegram-bot, с активным venv):
    python check_retrieval.py "что говорит ГОСТ про сечение кабеля для DC"
    python check_retrieval.py                       # спросит вопрос в консоли
    python check_retrieval.py --batch questions.txt  # по одному вопросу на строку
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from knowledge_matrix import search  # noqa: E402


def check_one(question: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"Вопрос: {question}")
    print("=" * 70)
    rows = search(question)
    if not rows:
        print("❌ НИЧЕГО не найдено. Либо в базе нет такого документа, либо")
        print("   вопрос сформулирован не теми словами, что в тексте документа —")
        print("   попробуйте более простую формулировку (ключевые слова из документа).")
        return
    print(f"✅ Найдено {len(rows)} фрагмент(ов) — вот что увидел бы Claude:\n")
    for i, row in enumerate(rows, 1):
        print(f"--- Фрагмент {i} ---")
        print(f"Источник: {row['source']} (стр. {row['page'] or '-'})")
        print(row["text"])
        print()


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--batch":
        if len(args) < 2:
            print("Использование: python check_retrieval.py --batch файл_с_вопросами.txt")
            raise SystemExit(1)
        questions = [line.strip() for line in Path(args[1]).read_text(encoding="utf-8").splitlines() if line.strip()]
        for q in questions:
            check_one(q)
        return

    if args:
        check_one(" ".join(args))
        return

    print("Вводите вопросы по одному (Ctrl+C или пустая строка — выход):")
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            break
        check_one(question)


if __name__ == "__main__":
    main()