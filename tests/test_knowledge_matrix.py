"""knowledge_matrix.search() — ORDER BY rank (К-1). Использует настоящий
data/knowledge.db; если его ещё нет (свежая установка без /reindex), тесты
пропускаются."""
from pathlib import Path

import pytest

from knowledge_matrix import DB_PATH, search

requires_matrix = pytest.mark.skipif(not Path(DB_PATH).exists(), reason="data/knowledge.db ещё не построен (см. /reindex)")


@requires_matrix
def test_search_returns_rows_without_crashing():
    rows = search("выключатель")
    assert isinstance(rows, list)


@requires_matrix
def test_search_empty_question_returns_nothing():
    assert search("") == []
