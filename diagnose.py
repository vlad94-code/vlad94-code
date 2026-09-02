"""Разовая диагностика: почему бот не отвечает по вопросу и по артикулу.

Запуск (из папки telegram-bot, с активным venv):
    python diagnose.py
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

print("=" * 60)
print("1. Проверка поиска по смыслу (локально, без интернета)")
print("=" * 60)

# Внешний ИИ (Claude) отключён: бот работает только на локальных данных.
# Свободные переформулированные вопросы обслуживает локальный поиск по
# смыслу поверх справочника (semantic_reference.py). Здесь проверяем, что он
# доступен, и на паре фраз показываем, что модель отличает близкое от далёкого.
try:
    import semantic_reference

    if not semantic_reference.is_available():
        print("❌ Поиск по смыслу выключен: модель эмбеддингов недоступна.")
        print("   Установите зависимости и дайте боту один раз скачать модель:")
        print("       pip install -r requirements-semantic.txt")
        print("   Без этого работает поиск по словам (переформулировки ловятся хуже).")
    else:
        print("✅ Поиск по смыслу включён. Пробую сопоставить переформулировку…")
        match = semantic_reference.best_match("во сколько обойдётся доставка")
        if match:
            print(f"   Нашёл запись справочника: «{match.question}» (близость {match.score:.2f})")
        else:
            print("   Модель работает, но в справочнике нет близкой записи на пробный вопрос —")
            print("   это нормально, если там нет вопроса про доставку.")
except Exception as error:
    print("❌ Не удалось проверить поиск по смыслу:")
    print(f"   {type(error).__name__}: {error}")

print()
print("=" * 60)
print("2. Проверка артикула YCB9 в products.json")
print("=" * 60)

path = Path("data/api_exports/products.json")
if not path.exists():
    print("❌ Файла data/api_exports/products.json нет вообще — /sync не запускался.")
else:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("catalog", data) if isinstance(data, dict) else data
    matches = [row for row in items if isinstance(row, dict) and str(row.get("vendor_code", "")).upper().startswith("YCB9")]
    print(f"Товаров с артикулом, начинающимся на YCB9: {len(matches)}")
    if matches:
        sample = matches[0]
        print("Пример записи:")
        print(f"  vendor_code: {sample.get('vendor_code')}")
        print(f"  series (поле): {sample.get('series')!r}")
        spec_seria = None
        for s in sample.get("specification", []) or []:
            if isinstance(s, dict) and s.get("name") == "Серия":
                spec_seria = s.get("value")
        print(f"  Серия (из specification): {spec_seria!r}")
        if not sample.get("series") and not spec_seria:
            print()
            print("⚠️ Поле 'series' пустое, и в specification тоже нет 'Серия'.")
            print("   Вот почему поиск по серии не находит YCB9: боту негде")
            print("   прочитать, что это вообще серия YCB9 — кроме самого")
            print("   текста артикула (vendor_code), который поиск по серии")
            print("   сейчас не смотрит.")
    else:
        print("Такого артикула в снимке нет вообще — возможно, другая серия")
        print("или другое написание в реальном каталоге CNC.")