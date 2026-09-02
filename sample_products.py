"""Показать несколько реальных артикулов из вашего каталога — чтобы было
чем проверять поиск, вместо примеров из документации.

Запуск: python sample_products.py
"""
import json
import random
from pathlib import Path

path = Path("data/api_exports/products.json")
data = json.loads(path.read_text(encoding="utf-8"))
items = data.get("catalog", data) if isinstance(data, dict) else data
items = [row for row in items if isinstance(row, dict) and row.get("vendor_code")]

print(f"Всего товаров в снимке: {len(items)}\n")
print("10 случайных реальных артикулов для проверки бота:\n")
for row in random.sample(items, min(10, len(items))):
    print(f"  {row.get('vendor_code')} — {row.get('type_item')} — {row.get('name')}")