"""Energy system helpers and configuration."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

# Baseline energy cap used when a realm does not provide an explicit mapping.
DEFAULT_REALM_ENERGY_CAP = 60.0

# Increment applied per realm order when deriving a fallback maximum.
REALM_ENERGY_CAP_STEP = 20.0

# Number of seconds before a player's energy fully refills automatically.
ENERGY_DAILY_REFILL_SECONDS = 24 * 60 * 60


_BASE_REALM_CAPS: Mapping[str, float] = {
    "mortal": DEFAULT_REALM_ENERGY_CAP,
    "qi condensation": 80.0,
    "foundation establishment": 100.0,
    "core formation": 120.0,
    "nascent soul": 140.0,
    "soul formation": 160.0,
    "soul transformation": 180.0,
    "ascendant": 200.0,
    "illusory yin": 220.0,
    "corporeal yang": 240.0,
    "nirvana scryer": 260.0,
    "nirvana cleanser": 280.0,
    "nirvana shatterer": 300.0,
    "heaven's blight": 320.0,
    "nirvana void": 340.0,
    "spirit void": 360.0,
    "arcane void": 380.0,
    "void tribulant": 400.0,
    "half-heaven trampling": 420.0,
    "heaven trampling": 440.0,
    "body tempering": 90.0,
    "soul awakening": 90.0,
}

REALM_ENERGY_CAPS: Mapping[str, float] = MappingProxyType(dict(_BASE_REALM_CAPS))


def _normalize_realm_name(realm: str | None) -> str | None:
    if not realm:
        return None
    normalized = realm.strip().lower()
    return normalized or None


def energy_cap_for_realm(
    realm: str | None,
    *,
    realm_order: int | None = None,
    step: int | None = None,
) -> float:
    """Return the maximum energy for a cultivation realm."""

    normalized = _normalize_realm_name(realm)
    if normalized and normalized in REALM_ENERGY_CAPS:
        return REALM_ENERGY_CAPS[normalized]

    base = DEFAULT_REALM_ENERGY_CAP
    if realm_order is not None:
        base += max(0, int(realm_order)) * REALM_ENERGY_CAP_STEP
    if step is not None and step > 1:
        base += max(0, int(step) - 1) * (REALM_ENERGY_CAP_STEP * 0.5)
    return float(base)
