"""The stages of an analysis and which of them a change makes necessary.

    S0 ingest · S1 triage · S2 light photo check · S3 deep analysis · S4 decision · S5 notify/monitor

Work is only repeated where its inputs changed. A new price changes the numbers (S4) and may be
worth telling about (S5); it does not change what the photos show, so the photo stages are not run
again. New photos are the opposite. The mapping is the single answer to "what do we redo?", used by
the workers when they queue follow-up work, so a price change never costs a model call.
"""

from __future__ import annotations

from enum import StrEnum


class Stage(StrEnum):
    S0_INGEST = "S0"
    S1_TRIAGE = "S1"
    S2_VISION_LIGHT = "S2"
    S3_DEEP = "S3"
    S4_DECISION = "S4"
    S5_NOTIFY = "S5"


ALL = frozenset(Stage)
_DECIDE_AND_NOTIFY = frozenset({Stage.S4_DECISION, Stage.S5_NOTIFY})

# Analysis trigger (see ``app.opportunities.analysis_record.TRIGGERS``) -> stages to run again.
AFTER_CHANGE: dict[str, frozenset[Stage]] = {
    "new": ALL,
    "price_change": _DECIDE_AND_NOTIFY,
    "status_change": _DECIDE_AND_NOTIFY,
    "recompute": _DECIDE_AND_NOTIFY,  # the market moved or the algorithm changed
    "photos": frozenset({Stage.S2_VISION_LIGHT, Stage.S3_DEEP, *_DECIDE_AND_NOTIFY}),
    "data_changed": frozenset({Stage.S1_TRIAGE, *_DECIDE_AND_NOTIFY}),  # title, description, fields
    "manual": ALL - {Stage.S0_INGEST},
    "migrated": frozenset({Stage.S4_DECISION}),
}


def stages_after(trigger: str | None) -> frozenset[Stage]:
    """Stages a change of this kind makes necessary; an unknown cause redoes everything but ingest."""
    if trigger is None:
        return AFTER_CHANGE["manual"]
    return AFTER_CHANGE.get(trigger, AFTER_CHANGE["manual"])


def needs_photo_check(trigger: str | None, *, vision_done: bool) -> bool:
    """Photos are (re)checked when the change touches them, or when they were never checked."""
    return Stage.S2_VISION_LIGHT in stages_after(trigger) and (not vision_done or trigger == "photos")
