"""Helpers for injecting special travel encounters such as innate soul mutations."""

from __future__ import annotations

import random
from typing import Iterable

from ..models.map import TravelEvent, TravelEventQueue
from ..models.players import PlayerProgress
from ..models.soul_land import SpiritRing, SpiritRingColor

MUTATION_EVENT_KINDS: frozenset[str] = frozenset({"discovery", "loot", "npc"})


def _supports_mutation(kind: str) -> bool:
    normalized = kind.strip().lower()
    if not normalized:
        return True
    return normalized in MUTATION_EVENT_KINDS


def maybe_enqueue_innate_soul_mutation(
    queue: TravelEventQueue,
    player: PlayerProgress,
    encounter_event: TravelEvent,
    rng: random.Random,
    *,
    now: float | None = None,
    triggers: Iterable[str] | None = None,
) -> None:
    """Enqueue a martial soul resonance event if the encounter qualifies."""

    if not player.martial_souls:
        return
    event_kind = ""
    if encounter_event.data:
        raw_kind = encounter_event.data.get("kind")
        if raw_kind:
            event_kind = str(raw_kind)
    if not _supports_mutation(event_kind):
        return
    if triggers:
        trigger_set = {str(value).strip().lower() for value in triggers if value}
        if event_kind and event_kind.lower() not in trigger_set:
            return
    chance = 0.3
    if rng.random() > chance:
        return
    primary = player.get_active_martial_souls()
    if not primary:
        primary = player.martial_souls
    soul = primary[0] if primary else None
    if soul is None:
        return
    next_index = len(player.soul_rings)
    age = max(10, 10 * (soul.grade + rng.randint(0, soul.grade)))
    if soul.grade >= 9:
        colour = SpiritRingColor.GOLD
    elif soul.grade >= 7:
        colour = SpiritRingColor.BLACK
    elif soul.grade >= 5:
        colour = SpiritRingColor.PURPLE
    elif soul.grade >= 3:
        colour = SpiritRingColor.YELLOW
    else:
        colour = SpiritRingColor.WHITE
    ring = SpiritRing(
        slot_index=next_index,
        color=colour,
        age=age,
        martial_soul=soul.name,
        ability_keys=("resonance-burst",),
        source="encounter",
    )
    queue.push(
        TravelEvent(
            key=f"soul:ring:{player.user_id}:{next_index}",
            description=(
                "The lingering resonance from the encounter coalesces into a "
                "nascent spirit ring."
            ),
            data={
                "ring": ring.to_mapping(),
                "source_event": encounter_event.key,
                "martial_soul": soul.name,
            },
        )
    )


__all__ = ["maybe_enqueue_innate_soul_mutation"]
