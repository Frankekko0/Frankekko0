"""COND-001: the condition labels vinted.it really prints are understood on every path.

The extension and the server parser both hand the raw label ("Ottime") to the ingestion; it used to read
"unknown" for it, so no real listing could ever be STRONG BUY and the price ignored the condition ladder.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from app.acquisition.vinted_parser import parse_item_html
from app.domain.enums import Condition
from app.identification.engine import IdentificationEngine
from app.identification.taxonomy import DEFAULT_TAXONOMY
from app.ingestion import normalizer
from app.ingestion.catalog import Catalog
from app.ingestion.normalizer import condition_from_text, normalize_condition
from app.ingestion.service import identify_listing, listing_columns

REAL = Path(__file__).resolve().parents[1] / "fixtures" / "vinted" / "real"
NOW = datetime(2026, 10, 7, 23, 0, tzinfo=UTC)

# Labels as printed (item page, card subtitle, other domains' languages). "Ottime" is the one seen on the real
# page; the others follow the same vocabulary.
LABELS = [
    ("Nuovo con cartellino", Condition.NEW_WITH_TAGS),
    ("Nuovo senza cartellino", Condition.NEW_WITHOUT_TAGS),
    ("Ottime", Condition.VERY_GOOD),
    ("Ottime condizioni", Condition.VERY_GOOD),
    ("Buone", Condition.GOOD),
    ("Buone condizioni", Condition.GOOD),
    ("Soddisfacenti", Condition.SATISFACTORY),
    ("Condizioni soddisfacenti", Condition.SATISFACTORY),
    ("Discrete", Condition.SATISFACTORY),
    ("Condizioni: Ottime", Condition.VERY_GOOD),
    ("Très bon état", Condition.VERY_GOOD),
    ("Bon état", Condition.GOOD),
    ("Satisfaisant", Condition.SATISFACTORY),
    ("Neuf avec étiquette", Condition.NEW_WITH_TAGS),
    ("Sehr gut", Condition.VERY_GOOD),
    ("Gut", Condition.GOOD),
    ("Muy bueno", Condition.VERY_GOOD),
    ("Heel goed", Condition.VERY_GOOD),
    ("Goed", Condition.GOOD),
    ("Nieuw zonder kaartje", Condition.NEW_WITHOUT_TAGS),
    ("Very good", Condition.VERY_GOOD),
    ("Good", Condition.GOOD),
    # "bez metki" (without tags) contains "z metk" (with tags): the order of the words is what tells them apart
    ("Nowy z metką", Condition.NEW_WITH_TAGS),
    ("Nowy bez metki", Condition.NEW_WITHOUT_TAGS),
    ("Novo com etiquetas", Condition.NEW_WITH_TAGS),
    ("Novo sem etiquetas", Condition.NEW_WITHOUT_TAGS),
]


@pytest.mark.parametrize(("label", "expected"), LABELS)
def test_every_printed_label_is_understood(label: str, expected: Condition) -> None:
    assert normalize_condition(label) == expected


def test_an_unknown_label_stays_unknown_and_says_so() -> None:
    normalizer._log_unrecognised.cache_clear()
    with capture_logs() as logs:
        assert normalize_condition("Pessime") == Condition.UNKNOWN
        assert normalize_condition("Pessime") == Condition.UNKNOWN  # logged once per label
    events = [e for e in logs if e["event"] == "condition_label_unrecognised"]
    assert len(events) == 1 and events[0]["label"] == "Pessime" and events[0]["reason"]
    # Running text is not a label: nothing is guessed and nothing is logged for it.
    with capture_logs() as logs:
        assert condition_from_text("Prezzo trattabile. Good brand, bella") == Condition.UNKNOWN
        assert condition_from_text("Das ist gut verpackt") == Condition.UNKNOWN
    assert logs == []


def test_a_condition_phrase_in_the_description_is_still_read() -> None:
    assert condition_from_text("Giacca usata ma in ottime condizioni, nessun difetto") == Condition.VERY_GOOD
    assert condition_from_text("Mai indossata, ancora con cartellino") == Condition.NEW_WITH_TAGS


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # the bare cue of newness gives way to a phrase that names a condition
        ("Giacca in buone condizioni, con cartellino", Condition.GOOD),
        ("Mai indossata, ancora con cartellino", Condition.NEW_WITH_TAGS),
        ("Nuovo con cartellino e ottime condizioni", Condition.NEW_WITH_TAGS),
        # a phrase after a negation says nothing
        ("Non usurato, tenuta bene", Condition.UNKNOWN),
        ("Scarpe non da sistemare, ottime condizioni", Condition.VERY_GOOD),
    ],
)
def test_two_phrases_in_the_description_are_read_with_care(text: str, expected: Condition) -> None:
    assert condition_from_text(text) == expected


@pytest.mark.parametrize(
    ("page", "condition"),
    [("item_active_plugins.html", "very_good"), ("item_sold_plugins.html", "new_without_tags")],
)
def test_a_real_page_is_stored_with_its_condition(page: str, condition: str) -> None:
    """Real page -> parser -> provider listing (carrying the raw label) -> the columns that are stored."""
    html = (REAL / page).read_text(encoding="utf-8")
    item = parse_item_html(html, "https://www.vinted.it/items/9000000101-x?referrer=catalog", now=NOW)
    pl = item.to_provider_listing()
    cols = listing_columns(
        pl,
        identify_listing(IdentificationEngine(DEFAULT_TAXONOMY), pl),
        Catalog([], [], DEFAULT_TAXONOMY),
        "vinted",
        NOW,
    )
    assert cols["condition"] == condition
    assert cols["condition_raw"] == pl.condition


def test_the_raw_short_label_the_extension_sends_is_stored_as_very_good() -> None:
    """What ``capturePayload`` sends for the real active page is the label itself ('Ottime')."""
    from app.marketplace.base import ProviderListing

    pl = ProviderListing(
        external_id="1", url="https://www.vinted.it/items/1", title="Giacca", price=20, condition="Ottime"
    )
    cols = listing_columns(
        pl,
        identify_listing(IdentificationEngine(DEFAULT_TAXONOMY), pl),
        Catalog([], [], DEFAULT_TAXONOMY),
        "vinted",
        NOW,
    )
    assert (cols["condition"], cols["condition_raw"]) == ("very_good", "Ottime")
