"""Analysis records: what makes two analyses "the same", why a new one exists, JSON-safe blocks (pure)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.opportunities.analysis_record import (
    classify_trigger,
    inputs_hash,
    jsonable,
    result_hash,
)

BASE = {
    "price": Decimal("20.00"),
    "currency": "EUR",
    "status": "active",
    "photos": ["a", "b"],
    "title": "t",
    "description": "d",
}


def test_money_and_times_are_stored_exactly_and_are_json_safe() -> None:
    out = jsonable(
        {
            "p": Decimal("20.50"),
            "whole": Decimal("18.00"),
            "t": datetime(2026, 10, 9, 12, 0, tzinfo=UTC),
            "id": uuid.UUID(int=1),
            "nested": [Decimal("0.4000")],
        }
    )
    assert out["p"] == "20.50" and out["whole"] == "18" and out["nested"] == ["0.4000"]
    assert out["t"] == "2026-10-09T12:00:00+00:00" and out["id"].endswith("0001")


def test_same_inputs_same_hash_and_any_change_changes_it() -> None:
    assert inputs_hash(BASE) == inputs_hash(dict(reversed(list(BASE.items()))))
    assert inputs_hash(BASE) != inputs_hash({**BASE, "price": Decimal("19.00")})
    assert inputs_hash(BASE) != inputs_hash({**BASE, "photos": ["a", "c"]})


def test_results_ignore_the_clock() -> None:
    values = {"flip_score": 80, "expected_profit": Decimal("12.00"), "analyzed_at": 1, "updated_at": 1}
    assert result_hash(values) == result_hash({**values, "analyzed_at": 999, "updated_at": 999})
    assert result_hash(values) != result_hash({**values, "flip_score": 79})


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({}, "recompute"),
        ({"price": Decimal("18.00")}, "price_change"),
        ({"photos": ["a", "b", "c"]}, "photos"),
        ({"photos": ["b", "a"]}, "photos"),
        ({"status": "reserved"}, "status_change"),
        ({"description": "other"}, "data_changed"),
        # The most specific cause wins when several changed.
        ({"price": Decimal("18.00"), "photos": ["x"]}, "price_change"),
    ],
)
def test_trigger_names_the_cause(change, expected) -> None:
    assert classify_trigger(jsonable(BASE), {**BASE, **change}) == expected


def test_first_and_migrated_and_explicit_triggers() -> None:
    assert classify_trigger(None, BASE) == "new"
    assert classify_trigger({"migrated": True}, BASE) == "recompute"
    assert classify_trigger(jsonable(BASE), BASE, "manual") == "manual"
    with pytest.raises(ValueError, match="unknown analysis trigger"):
        classify_trigger(None, BASE, "whatever")
