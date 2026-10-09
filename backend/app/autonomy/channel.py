"""Execution channels: how a decided action reaches the world.

Two exist on purpose. ``DryRunChannel`` records what would have been done. ``AssistedChannel`` prepares the
action (text, price, link) as a task for the user to carry out on the marketplace: FlipFinder does not act
on Vinted by itself. A channel that is refused or fails stops that action, it is recorded, and the rest
continues: protections are never worked around.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.autonomy.policy import Kind


class ChannelRefused(Exception):
    """The platform or the channel refused the action. It is recorded, never retried around."""


class UnsupportedPlatform(ValueError):
    pass


@dataclass(frozen=True)
class ExecutionResult:
    status: str  # dry_run | pending_user | done | failed
    detail: dict[str, Any] = field(default_factory=dict)


class ExecutionChannel(ABC):
    name = "abstract"
    marketplace = "vinted"

    @abstractmethod
    async def execute(self, kind: Kind, payload: dict[str, Any]) -> ExecutionResult: ...


class DryRunChannel(ExecutionChannel):
    name = "dry_run"

    async def execute(self, kind: Kind, payload: dict[str, Any]) -> ExecutionResult:
        return ExecutionResult("dry_run", {"would": kind.value, **payload})


class AssistedChannel(ExecutionChannel):
    """A task for the user: what to do, with the text or the price ready. Nothing touches the marketplace."""

    name = "assisted"

    async def execute(self, kind: Kind, payload: dict[str, Any]) -> ExecutionResult:
        steps = {
            Kind.BUY: "Apri l'annuncio su Vinted e acquista al prezzo indicato, se ancora conviene.",
            Kind.OFFER: "Invia l'offerta indicata al venditore dalla pagina dell'annuncio.",
            Kind.MESSAGE: "Invia il messaggio proposto dalla chat dell'annuncio.",
            Kind.LIST: "Pubblica l'articolo su Vinted con il titolo, la descrizione e il prezzo preparati.",
            Kind.REPRICE: "Aggiorna il prezzo dell'annuncio su Vinted al valore indicato.",
        }
        return ExecutionResult("pending_user", {"todo": steps[kind], **payload})


CHANNELS: dict[tuple[str, str], type[ExecutionChannel]] = {
    ("vinted", "dry_run"): DryRunChannel,
    ("vinted", "assisted"): AssistedChannel,
}


def get_channel(marketplace: str, mode: str) -> ExecutionChannel:
    """The channel for a marketplace. There is no automatic channel for Vinted, and none can be added here
    without changing the guardian tests: only an official API or an allowed service could be."""
    try:
        return CHANNELS[(marketplace, mode)]()
    except KeyError as exc:
        raise UnsupportedPlatform(f"nessun canale lecito per {marketplace}/{mode}") from exc
