"""The labelled set: 150 realistic listings (tests/fixtures/extraction/cases.py, written before the work), a
hold-out of 52 written after it (holdout.py) and 54 fresh ones written in the continuation session (fresh.py), scored
per attribute exactly like ``tests/bench/measure_extraction.py``.

The floors are what the engine reaches today, a little below, so a change that makes it worse fails here. They are
precisions: of the answers the engine gives, how many are right (a wrong answer is worse than none, because the
comparables are chosen with it).
"""

import pytest

from tests.bench import measure_extraction as M

FLOORS = {
    "brand": (0.99, 0.93),
    "model": (0.96, 0.90),
    "category": (0.98, 0.95),
    "gender": (0.95, 0.95),
    "size": (0.97, 0.95),
    "color": (0.95, 0.95),
    "material": (0.97, 0.95),
    "team": (1.0, 1.0),
    "suspicious": (0.97, 0.97),
}


# The fresh table has two honest misses (a model the catalogue has no line for, a sneaker line it does not know).
FRESH_FLOORS = {**FLOORS, "model": (0.95, 0.80), "category": (0.98, 0.95), "size": (0.95, 0.95)}


@pytest.mark.parametrize("table", ["labelled_cases", "holdout_cases", "fresh_cases"])
def test_precision_and_recall_per_attribute_hold(table: str) -> None:
    result = getattr(M, table)()
    assert "error" not in result, result
    for attribute, (precision, recall) in (FRESH_FLOORS if table == "fresh_cases" else FLOORS).items():
        got = result[attribute]
        assert got["precision"] >= precision, (table, attribute, got)
        assert got["strict_recall"] >= recall, (table, attribute, got)
    # A guess is never "certain", and a wrong core attribute never goes with a high confidence.
    assert result["_certain_but_wrong_category"] == 0
    assert result["_wrong_core_with_confidence_ge_70"] <= 1


def test_the_two_tables_are_big_enough_to_mean_something() -> None:
    assert len(M.CASES) >= 100 and len(M.HOLDOUT) >= 50 and len(M.FRESH) >= 50
    ids = [c["id"] for c in (*M.CASES, *M.HOLDOUT, *M.FRESH)]
    assert len(ids) == len(set(ids))
