"""Fixed exchange rates to EUR for the external prices.

A fixed, documented table instead of a live feed: no extra network call, no daily drift in the
stored prices, the same conversion in tests and in production. The rates are the ECB euro
reference rates of ``RATES_DATE`` (units of the currency per 1 EUR); update the table by hand when
they move enough to matter (a few percent). Every stored price records the rate and the table date
it was converted with (``external_prices.match["fx"]``). Any other currency is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

RATES_DATE = "2026-10-07"
RATES_SOURCE = "BCE, tassi di riferimento dell'euro"
PER_EUR: dict[str, Decimal] = {
    "EUR": Decimal("1"),
    "USD": Decimal("1.1177"),
    "GBP": Decimal("0.84645"),
    "CHF": Decimal("0.9309"),
    "PLN": Decimal("4.3825"),
    "SEK": Decimal("11.2240"),
    "DKK": Decimal("7.4745"),
    "CZK": Decimal("24.427"),
}
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Converted:
    price_eur: Decimal
    currency: str
    per_eur: Decimal  # units of ``currency`` per 1 EUR

    def record(self) -> dict[str, Any]:
        """What ``external_prices.match["fx"]`` keeps about the conversion."""
        return {"currency": self.currency, "per_eur": str(self.per_eur), "date": RATES_DATE}


def to_eur(amount: Decimal, currency: str | None) -> Converted | None:
    """``amount`` in EUR (rounded to the cent), or ``None`` for a currency outside the table."""
    code = (currency or "").upper()
    rate = PER_EUR.get(code)
    if rate is None:
        return None
    return Converted((amount / rate).quantize(CENT, rounding=ROUND_HALF_UP), code, rate)
