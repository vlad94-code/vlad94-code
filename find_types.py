"""Разовый скрипт: показать все уникальные type_item, где встречается слово.

Запуск (из папки telegram-bot, с активным venv):
    python find_types.py контактор
"""
import json
import sys
from pathlib import Path

if len(sys.argv) < 2:
    print("Использование: python find_types.py СЛОВО")
    raise SystemExit(1)

word = sys.argv[1].lower()
path = Path("data/api_exports/products.json")
data = json.loads(path.read_text(encoding="utf-8"))
items = data.get("catalog", data) if isinstance(data, dict) else data

counts: dict[str, int] = {}
for row in items:
    if not isinstance(row, dict):
        continue
    type_item = str(row.get("type_item", ""))
    if word in type_item.lower():
        counts[type_item] = counts.get(type_item, 0) + 1

if not counts:
    print(f"Ничего не нашлось: ни один type_item не содержит «{word}».")
else:
    print(f"Найдено {len(counts)} разных type_item со словом «{word}»:\n")
    for type_item, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"{count:>6} шт.  —  {type_item}")