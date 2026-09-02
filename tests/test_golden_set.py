"""Прогон tests/golden_set.yaml через настоящий router.route_local() (Г-2).

expected_module "router" означает: ни один локальный движок не должен
обработать вопрос (route_local() возвращает handled=False, engine_name
"router") — дальше в bot.py он ушёл бы в Claude/RAG, что этот тест не
проверяет (нужен живой ANTHROPIC_API_KEY, см. golden_set.yaml).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from engines.router import route_local

GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.yaml"
PRODUCTS_SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "api_exports" / "products.json"


def _snapshot_present() -> bool:
    try:
        return PRODUCTS_SNAPSHOT.stat().st_size > 0
    except OSError:
        return False


# Golden set — измерительный прибор Гейта §14 («ошибки в числах: 0»). Без снимка
# каталога он проверяет только маршрутизацию: числа брать неоткуда, и часть
# expected_contains заведомо не сойдётся. Пропускаем набор ЦЕЛИКОМ, а не по
# одному кейсу: частичный прогон, показанный как зелёный (например в CI, где
# data/ нет), создавал бы ложную уверенность, что гейт пройден.
pytestmark = pytest.mark.skipif(
    not _snapshot_present(),
    reason=(
        "data/api_exports/products.json отсутствует: golden set не является "
        "доказательством Гейта §14 без снимка каталога (см. README, «Два уровня проверки»)"
    ),
)


def _load_cases() -> list[dict]:
    with GOLDEN_SET_PATH.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream) or []


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["question"])
def test_golden_case(case: dict) -> None:
    response = asyncio.run(route_local(case["question"], {"catalog_filters": {}}))
    expected_module = case["expected_module"]

    if expected_module == "router":
        assert not response.handled, (
            f"{case['question']!r}: ожидали, что ни один локальный движок не возьмётся, "
            f"но ответил {response.engine_name!r}"
        )
    else:
        assert response.handled, f"{case['question']!r}: ожидали ответ модуля {expected_module!r}, но вопрос не обработан"
        assert response.engine_name == expected_module, (
            f"{case['question']!r}: ожидали модуль {expected_module!r}, получили {response.engine_name!r}"
        )

    for substring in case.get("expected_contains") or []:
        assert substring in response.text, f"{case['question']!r}: в ответе нет {substring!r}"
