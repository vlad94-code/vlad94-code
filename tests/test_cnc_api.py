"""cnc_api.CncApi — индекс {артикул: запись} в памяти, поля вместо сырого
JSON в промпте (К-4). Использует настоящий локальный снимок из
data/api_exports/ — если каталог пуст (свежая установка без синка),
тесты пропускаются."""
import json
from pathlib import Path

import pytest

from cnc_api import CncApi

PRODUCTS_PATH = Path("data") / "api_exports" / "products.json"


def _first_vendor_code() -> str | None:
    if not PRODUCTS_PATH.exists():
        return None
    payload = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))
    rows = payload if isinstance(payload, list) else next(
        (v for v in payload.values() if isinstance(v, list)), []
    )
    for row in rows:
        if isinstance(row, dict) and row.get("vendor_code"):
            return str(row["vendor_code"])
    return None


requires_snapshot = pytest.mark.skipif(
    _first_vendor_code() is None, reason="data/api_exports/products.json отсутствует или пуст"
)


@requires_snapshot
def test_exact_article_lookup_hits_the_index():
    api = CncApi()
    code = _first_vendor_code()
    result = api._lookup_exports(code)
    assert code in result


@requires_snapshot
def test_exact_lookup_is_not_a_raw_json_dump():
    """К-4: промпт получает читаемые поля, не json.dumps() всей записи."""
    api = CncApi()
    code = _first_vendor_code()
    result = api._lookup_exports(code)
    assert '"vendor_code"' not in result
    assert '"specification"' not in result


def test_unknown_article_returns_empty_string():
    api = CncApi()
    assert api._lookup_exports("НЕТ-ТАКОГО-АРТИКУЛА-999") == ""


def test_clear_product_cache_does_not_raise():
    CncApi.clear_product_cache()
