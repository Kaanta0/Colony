"""Innate soul modelling including mutation tracking utilities."""

from __future__ import annotations

import time
import uuid
from dataclasses import InitVar, dataclass, field
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence, Tuple

from .combat import (
    SpiritualAffinity,
    affinity_relationship_modifier,
    coerce_affinity,
    normalize_affinities,
)


def _computed_affinity_bonus(
    *,
    grade: int,
    overlaps: Iterable[float],
    affinity_count: int,
    flat_modifier: float = 0.0,
) -> float:
    """Return the bonus portion for an affinity overlap calculation."""

    try:
        normalized_grade = int(grade)
    except (TypeError, ValueError):
        normalized_grade = 1
    normalized_grade = max(1, min(9, normalized_grade))

    try:
        affinity_total = int(affinity_count)
    except (TypeError, ValueError):
        affinity_total = 1
    affinity_total = max(1, affinity_total)

    base_bonus = 0.12 + 0.06 * normalized_grade
    focus_exponent = 1.0 + 0.05 * normalized_grade + 0.08 * (affinity_total - 1)
    synergy_bonus = 0.02 * (affinity_total - 1) * (1 + 0.05 * normalized_grade)
    penalty_scale = 0.02 * normalized_grade

    accumulated = 0.0
    applied_synergy = False
    penalty = 0.0
    for overlap in overlaps:
        if overlap <= 0.0:
            penalty += overlap * penalty_scale
            continue
        applied_synergy = True
        accumulated += (overlap ** focus_exponent) * base_bonus

    if applied_synergy and affinity_total > 1:
        accumulated += synergy_bonus

    return accumulated + penalty + flat_modifier


@dataclass(slots=True)
class InnateSoul:
    """Represents a cultivator's elemental talent."""

    name: str
    grade: int
    affinities: Tuple[SpiritualAffinity, ...] = field(default_factory=tuple)
    affinity_modifiers: Mapping[SpiritualAffinity, float] = field(default_factory=dict)
    affinity: InitVar[SpiritualAffinity | str | None] = None

    def __post_init__(self, affinity: SpiritualAffinity | str | None = None) -> None:
        affinities: list[SpiritualAffinity] = []
        for value in self.affinities:
            affinities.append(coerce_affinity(value))
        if affinity is not None:
            try:
                affinities.append(coerce_affinity(affinity))
            except ValueError:
                pass
        if not affinities:
            affinities.append(SpiritualAffinity.FIRE)
        unique: dict[SpiritualAffinity, None] = {aff: None for aff in affinities}
        self.affinities = tuple(unique.keys())

        modifiers: dict[SpiritualAffinity, float] = {}
        for key, value in dict(self.affinity_modifiers).items():
            try:
                coerced = coerce_affinity(key)
            except ValueError:
                continue
            try:
                modifiers[coerced] = float(value)
            except (TypeError, ValueError):
                continue
        self.affinity_modifiers = modifiers

    @property
    def primary_affinity(self) -> SpiritualAffinity | None:
        return self.affinities[0] if self.affinities else None

    @property
    def affinity(self) -> SpiritualAffinity | None:
        return self.primary_affinity

    def matches(self, affinity: Optional[SpiritualAffinity]) -> bool:
        return affinity is not None and affinity in self.affinities

    def damage_multiplier(
        self, affinity: Optional[SpiritualAffinity | Sequence[SpiritualAffinity]]
    ) -> float:
        affinities = normalize_affinities(affinity)
        if not affinities:
            return 1.0
        overlaps = (
            max(
                affinity_relationship_modifier(owned, candidate)
                for candidate in affinities
            )
            for owned in self.affinities
        )
        bonus = _computed_affinity_bonus(
            grade=self.grade,
            overlaps=overlaps,
            affinity_count=len(self.affinities),
            flat_modifier=max(
                (self.affinity_modifiers.get(entry, 0.0) for entry in affinities),
                default=0.0,
            ),
        )
        return max(0.1, 1.0 + bonus)

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "grade": int(self.grade),
            "affinities": [affinity.value for affinity in self.affinities],
        }
        if self.affinity_modifiers:
            payload["affinity_modifiers"] = {
                affinity.value: bonus
                for affinity, bonus in self.affinity_modifiers.items()
            }
        return payload

    @staticmethod
    def default() -> "InnateSoul":
        affinity = SpiritualAffinity.FIRE
        return InnateSoul(
            name=f"Dormant {affinity.display_name} Soul",
            grade=1,
            affinities=(affinity,),
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "InnateSoul":
        name = str(data.get("name", "Unknown Soul"))
        grade = int(data.get("grade", 1))
        raw_affinities = data.get("affinities")
        affinities: list[SpiritualAffinity] = []
        if isinstance(raw_affinities, (list, tuple, set)):
            for entry in raw_affinities:
                try:
                    affinities.append(coerce_affinity(entry))
                except ValueError:
                    continue
        elif raw_affinities is not None:
            try:
                affinities.append(coerce_affinity(raw_affinities))
            except ValueError:
                pass
        affinity_value = data.get("affinity")
        if not affinities and affinity_value is not None:
            try:
                affinities.append(coerce_affinity(affinity_value))
            except ValueError:
                pass
        raw_modifiers = data.get("affinity_modifiers", {})
        modifiers: dict[SpiritualAffinity, float] = {}
        if isinstance(raw_modifiers, Mapping):
            for key, value in raw_modifiers.items():
                try:
                    affinity_key = coerce_affinity(key)
                except ValueError:
                    continue
                try:
                    modifiers[affinity_key] = float(value)
                except (TypeError, ValueError):
                    continue
        return cls(
            name=name,
            grade=grade,
            affinities=tuple(affinities),
            affinity_modifiers=modifiers,
        )


@dataclass(frozen=True, slots=True)
class InnateSoulMutation:
    """Represents an applied mutation to a cultivator's souls."""

    variant: InnateSoul
    hybridized: bool = False
    source: str | None = None
    applied_at: float | None = None
    trigger: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "variant": self.variant.to_mapping(),
            "hybridized": self.hybridized,
        }
        if self.source is not None:
            payload["source"] = self.source
        if self.applied_at is not None:
            payload["applied_at"] = float(self.applied_at)
        if self.trigger is not None:
            payload["trigger"] = self.trigger
        return payload

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "InnateSoulMutation":
        variant_data = data.get("variant", data)
        if isinstance(variant_data, InnateSoul):
            variant = variant_data
        else:
            variant = InnateSoul.from_mapping(variant_data)  # type: ignore[arg-type]
        hybridized = bool(data.get("hybridized", False))
        source = data.get("source")
        trigger = data.get("trigger")
        applied_at_raw = data.get("applied_at")
        applied_at = None
        if applied_at_raw is not None:
            try:
                applied_at = float(applied_at_raw)
            except (TypeError, ValueError):
                applied_at = None
        return cls(
            variant=variant,
            hybridized=hybridized,
            source=str(source).strip() or None if source is not None else None,
            applied_at=applied_at,
            trigger=str(trigger).strip() or None if trigger is not None else None,
        )


@dataclass(slots=True)
class InnateSoulMutationOpportunity:
    """Represents a pending chance for a player to mutate their souls."""

    identifier: str
    variant: InnateSoul
    hybridized: bool
    created_at: float
    expires_at: float | None = None
    source: str | None = None
    description: str | None = None

    def to_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.identifier,
            "variant": self.variant.to_mapping(),
            "hybridized": self.hybridized,
            "created_at": float(self.created_at),
        }
        if self.expires_at is not None:
            payload["expires_at"] = float(self.expires_at)
        if self.source is not None:
            payload["source"] = self.source
        if self.description is not None:
            payload["description"] = self.description
        return payload

    def to_mutation(self, *, applied_at: float | None = None) -> InnateSoulMutation:
        return InnateSoulMutation(
            variant=self.variant,
            hybridized=self.hybridized,
            source=self.source,
            applied_at=applied_at,
            trigger=self.description,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "InnateSoulMutationOpportunity":
        identifier = str(data.get("id") or data.get("identifier") or uuid.uuid4().hex)
        variant_data = data.get("variant", {})
        if isinstance(variant_data, InnateSoul):
            variant = variant_data
        else:
            variant = InnateSoul.from_mapping(variant_data)  # type: ignore[arg-type]
        hybridized = bool(data.get("hybridized", False))
        created_raw = data.get("created_at", time.time())
        try:
            created_at = float(created_raw)
        except (TypeError, ValueError):
            created_at = time.time()
        expires_raw = data.get("expires_at")
        expires_at = None
        if expires_raw is not None:
            try:
                expires_at = float(expires_raw)
            except (TypeError, ValueError):
                expires_at = None
        source = data.get("source")
        description = data.get("description")
        return cls(
            identifier=identifier,
            variant=variant,
            hybridized=hybridized,
            created_at=created_at,
            expires_at=expires_at,
            source=str(source).strip() or None if source is not None else None,
            description=str(description).strip() or None if description is not None else None,
        )


class InnateSoulSet:
    """Collection wrapper that behaves like a spiritual soul aggregate."""

    __slots__ = ("_souls", "_bonus_affinities", "_mutations", "_hybridized")

    def __init__(
        self,
        souls: Sequence[InnateSoul] | None = None,
        bonus_affinities: Sequence[SpiritualAffinity] | None = None,
        mutations: Sequence[InnateSoulMutation | Mapping[str, Any]] | None = None,
        hybridized: bool | None = None,
    ) -> None:
        self._souls = tuple(souls or ())
        bonus: list[SpiritualAffinity] = []
        for entry in bonus_affinities or ():
            try:
                bonus.append(coerce_affinity(entry))
            except ValueError:
                continue
        self._bonus_affinities = tuple(bonus)
        parsed_mutations: list[InnateSoulMutation] = []
        for entry in mutations or ():
            if isinstance(entry, InnateSoulMutation):
                parsed_mutations.append(entry)
            elif isinstance(entry, Mapping):
                parsed_mutations.append(InnateSoulMutation.from_mapping(entry))
        self._mutations = tuple(parsed_mutations)
        self._hybridized = bool(hybridized) or any(
            mutation.hybridized for mutation in self._mutations
        )

    def __iter__(self) -> Iterator[InnateSoul]:
        return iter(self._souls)

    def __bool__(self) -> bool:
        return bool(self._souls or self._bonus_affinities or self._mutations)

    @property
    def souls(self) -> Tuple[InnateSoul, ...]:
        return self._souls

    @property
    def bases(self) -> Tuple[InnateSoul, ...]:
        """Backward compatible alias for :attr:`souls`.

        Older call sites referenced ``bases`` when dealing with innate soul
        collections.  The cultivation flow still expects that attribute, so we
        provide this alias to avoid breaking those consumers while continuing to
        expose the canonical :attr:`souls` API.
        """

        return self._souls

    @property
    def bonus_affinities(self) -> Tuple[SpiritualAffinity, ...]:
        return self._bonus_affinities

    @property
    def mutations(self) -> Tuple[InnateSoulMutation, ...]:
        return self._mutations

    @property
    def hybridized(self) -> bool:
        return self._hybridized

    @property
    def mutation_affinities(self) -> Tuple[SpiritualAffinity, ...]:
        ordered: list[SpiritualAffinity] = []
        seen: set[SpiritualAffinity] = set()
        for mutation in self._mutations:
            for affinity in mutation.variant.affinities:
                if affinity in seen:
                    continue
                ordered.append(affinity)
                seen.add(affinity)
        return tuple(ordered)

    @property
    def affinities(self) -> Tuple[SpiritualAffinity, ...]:
        ordered: list[SpiritualAffinity] = []
        seen: set[SpiritualAffinity] = set()
        for soul in self._souls:
            for affinity in soul.affinities:
                if affinity in seen:
                    continue
                ordered.append(affinity)
                seen.add(affinity)
        for mutation in self._mutations:
            for affinity in mutation.variant.affinities:
                if affinity in seen:
                    continue
                ordered.append(affinity)
                seen.add(affinity)
        for affinity in self._bonus_affinities:
            if affinity in seen:
                continue
            ordered.append(affinity)
            seen.add(affinity)
        return tuple(ordered)

    @property
    def affinity(self) -> SpiritualAffinity | None:
        affinities = self.affinities
        return affinities[0] if affinities else None

    @property
    def name(self) -> str:
        if not self._souls:
            return ""
        return " & ".join(soul.name for soul in self._souls)

    @property
    def grade(self) -> int:
        """Return the highest grade represented within the set.

        The calculation considers the grades of all innate souls and any active
        mutations. When no grade-bearing souls are present a baseline grade of
        1 is returned so downstream consumers have a sensible default.
        """

        grades: list[int] = []
        for soul in self._souls:
            try:
                grades.append(int(soul.grade))
            except (TypeError, ValueError):
                continue
        for mutation in self._mutations:
            try:
                grades.append(int(mutation.variant.grade))
            except (TypeError, ValueError):
                continue
        if grades:
            return max(1, max(grades))
        return 1

    def matches(self, affinity: Optional[SpiritualAffinity]) -> bool:
        if affinity is None:
            return False
        if any(soul.matches(affinity) for soul in self._souls):
            return True
        if any(mutation.variant.matches(affinity) for mutation in self._mutations):
            return True
        return affinity in self._bonus_affinities

    def damage_multiplier(
        self, affinity: Optional[SpiritualAffinity | Sequence[SpiritualAffinity]]
    ) -> float:
        affinities = normalize_affinities(affinity)
        if not affinities:
            return 1.0
        best = max(
            (soul.damage_multiplier(affinities) for soul in self._souls),
            default=1.0,
        )
        for mutation in self._mutations:
            best = max(best, mutation.variant.damage_multiplier(affinities))
        for bonus in self._bonus_affinities:
            overlap = max(
                affinity_relationship_modifier(bonus, candidate)
                for candidate in affinities
            )
            bonus_multiplier = max(
                0.1,
                1.0
                + _computed_affinity_bonus(
                    grade=self.grade,
                    overlaps=(overlap,),
                    affinity_count=1,
                ),
            )
            best = max(best, bonus_multiplier)
        return best

    def with_bonus_affinities(
        self, affinities: Sequence[SpiritualAffinity | str]
    ) -> "InnateSoulSet":
        extras: list[SpiritualAffinity] = list(self._bonus_affinities)
        for affinity in affinities:
            try:
                extras.append(coerce_affinity(affinity))
            except ValueError:
                continue
        return InnateSoulSet(
            self._souls,
            extras,
            mutations=self._mutations,
            hybridized=self._hybridized,
        )

    def with_mutation(self, mutation: InnateSoulMutation) -> "InnateSoulSet":
        mutations = list(self._mutations)
        mutations.append(mutation)
        return InnateSoulSet(
            self._souls,
            self._bonus_affinities,
            mutations=mutations,
            hybridized=self._hybridized or mutation.hybridized,
        )


def generate_mutation_opportunity(
    soul_set: InnateSoulSet,
    *,
    rng,
    now: float | None = None,
    source: str | None = None,
    description: str | None = None,
) -> InnateSoulMutationOpportunity | None:
    """Create a mutation opportunity if new affinities are available."""

    if not soul_set.souls:
        return None
    available = [
        affinity
        for affinity in SpiritualAffinity
        if affinity not in soul_set.affinities
    ]
    if not available:
        return None
    highest = max(soul_set.souls, key=lambda entry: entry.grade)
    chosen = rng.choice(available)
    affinities = tuple(list(highest.affinities) + [chosen])
    variant = InnateSoul(
        name=f"{highest.name} ({chosen.display_name} Mutation)",
        grade=min(9, highest.grade + 1),
        affinities=affinities,
    )
    created_at = now or time.time()
    expires_at = created_at + 3600.0
    identifier = uuid.uuid4().hex
    return InnateSoulMutationOpportunity(
        identifier=identifier,
        variant=variant,
        hybridized=len(affinities) > len(highest.affinities),
        created_at=created_at,
        expires_at=expires_at,
        source=source,
        description=description,
    )


__all__ = [
    "InnateSoulMutation",
    "InnateSoulMutationOpportunity",
    "InnateSoul",
    "InnateSoulSet",
    "generate_mutation_opportunity",
]
