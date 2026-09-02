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
print("1. Проверка Claude (Anthropic) — свободные вопросы")
print("=" * 60)

key = os.environ.get("ANTHROPIC_API_KEY", "")
model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

if not key:
    print("❌ ANTHROPIC_API_KEY пустой в .env — свободные вопросы ('есть на")
    print("   постоянный ток' и т.п.) работать не будут, пока не заполните.")
else:
    print(f"Ключ найден (начинается с {key[:12]}...), модель: {model}")
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model=model,
            max_tokens=10,
            messages=[{"role": "user", "content": "ping"}],
        )
        print("✅ Claude ответил, ключ и баланс в порядке:", response.content[0].text)
    except Exception as error:
        print("❌ Claude не ответил. Настоящая причина ниже — это и есть то,")
        print("   что нужно показать мне или проверить в Anthropic Console:")
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