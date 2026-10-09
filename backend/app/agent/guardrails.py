"""Guardrails of the agent, enforced in code, out of the model's reach.

* The text of a listing is **data, never instructions**: it is shown to the model only between
  delimiters, stripped of anything that could close them, shortened, and checked for wording that
  tries to give orders.
* The model can **lower** a verdict (skip, watch), never raise it: a pick is accepted only when the
  decision engine's own verdict allows the action. Vetoes of the decision engine therefore bind
  the agent too.
* The agent only touches opportunities of its run (its *scope*), notifies within a daily cap and
  never about something the decision engine rejected.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

OPEN, CLOSE = "<annuncio_non_fidato>", "</annuncio_non_fidato>"
MAX_TEXT = 700

# Wording that addresses the reader of the text rather than describing the item. A hit does not
# block anything (a seller may write "ignore" innocently): it is reported in the trace and the
# event log so a person can look, and the text stays inert data either way.
INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignor[ae]\w*\s+(?:(?:all|any|the|your|every|tutt\w+|le|gli|le tue|queste)\s+)*(?:previous|prior|above|precedent\w*|istruzion\w*|instructions?)",
        r"disregard\s+(?:the\s+|all\s+|your\s+)?(?:above|previous|instructions?)",
        r"\b(?:system|developer)\s*(?:prompt|message)\b",
        r"\byou\s+are\s+now\b|\bsei\s+ora\b|\bact\s+as\b|\bfai\s+finta\b",
        r"\b(?:new|nuove?)\s+(?:instructions?|istruzion\w+)\b",
        r"\b(?:submit_result|notify_user|allocate_capital|finance_calc)\b",
        r"\b(?:rispondi|answer|respond)\s+(?:con|with|only|solo)\b",
        r"\bSTRONG[ _]BUY\b.*\b(?:always|sempre|must|devi)\b",
    )
]


def injection_suspected(text: str) -> bool:
    return any(p.search(text) for p in INJECTION_PATTERNS)


def neutralise(text: str) -> str:
    """Make ``text`` inert as data: no control characters, no way to forge or close a delimiter."""
    text = "".join(ch if ch in "\n\t" or ch >= " " else " " for ch in text)
    text = text.replace("<", "‹").replace(">", "›")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def wrap_untrusted(text: str | None, limit: int = MAX_TEXT) -> str:
    """The listing text as the model sees it: delimited, shortened, harmless."""
    clean = neutralise(text or "")
    if len(clean) > limit:
        clean = clean[:limit].rstrip() + " […]"
    return f"{OPEN}{clean}{CLOSE}"


# ------------------------------------------------------------------ what a pick may say
ACTIONS = ("buy", "negotiate", "watch", "skip")
# Verdicts (decision engine) that allow each action. "skip" is always allowed: lowering is free.
ALLOWED: dict[str, frozenset[str]] = {
    "buy": frozenset({"STRONG_BUY", "BUY"}),
    "negotiate": frozenset({"STRONG_BUY", "BUY", "NEGOTIATE"}),
    "watch": frozenset({"STRONG_BUY", "BUY", "NEGOTIATE", "WATCHLIST"}),
    "skip": frozenset({"STRONG_BUY", "BUY", "NEGOTIATE", "WATCHLIST", "PASS", "INSUFFICIENT_EVIDENCE"}),
}
NOTIFIABLE = frozenset({"STRONG_BUY", "BUY", "NEGOTIATE"})


@dataclass(frozen=True)
class PickCheck:
    accepted: list[dict[str, object]]
    rejected: list[dict[str, object]]


def validate_picks(
    picks: Iterable[dict[str, object]], verdicts: dict[str, str], active: set[str]
) -> PickCheck:
    """Keep the picks the decision engine allows; say why for the others."""
    accepted: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    seen: set[str] = set()
    for pick in picks:
        oid, action = str(pick.get("opportunity_id")), str(pick.get("action"))
        why: str | None = None
        if oid not in verdicts:
            why = "fuori dal perimetro di questa esecuzione"
        elif oid in seen:
            why = "indicata due volte"
        elif action not in ACTIONS:
            why = f"azione sconosciuta: {action}"
        elif oid not in active and action != "skip":
            why = "annuncio non più attivo"
        elif verdicts[oid] not in ALLOWED[action]:
            why = f"il motore decisionale dice {verdicts[oid].replace('_', ' ')}: «{action}» non è consentito"
        if why:
            rejected.append({**pick, "rejected_because": why})
        else:
            seen.add(oid)
            accepted.append(pick)
    return PickCheck(accepted, rejected)
