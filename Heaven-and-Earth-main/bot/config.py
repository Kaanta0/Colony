"""Bot configuration utilities."""

from __future__ import annotations

import os
from dataclasses import dataclass


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(slots=True)
class BotConfig:
    token: str
    cultivation_tick: int = 25
    cultivation_tick_min: int = 25
    cultivation_tick_max: int = 25
    combat_exp_gain: int = 25
    soul_exp_gain: int = 25
    innate_stat_min: int = 1
    innate_stat_max: int = 20

    @classmethod
    def from_env(cls) -> "BotConfig":
        token = env("DISCORD_TOKEN")
        cultivation_tick = int(os.getenv("CULTIVATION_TICK", "25"))
        cultivation_tick_min = int(
            os.getenv("CULTIVATION_TICK_MIN", str(cultivation_tick))
        )
        cultivation_tick_max = int(
            os.getenv("CULTIVATION_TICK_MAX", str(cultivation_tick))
        )
        combat_exp_gain = int(os.getenv("COMBAT_EXP_GAIN", "25"))
        soul_exp_gain = int(os.getenv("SOUL_EXP_GAIN", str(cultivation_tick)))
        innate_stat_min = int(os.getenv("INNATE_STAT_MIN", "1"))
        innate_stat_max = int(os.getenv("INNATE_STAT_MAX", "20"))
        if cultivation_tick_min > cultivation_tick_max:
            cultivation_tick_min, cultivation_tick_max = (
                cultivation_tick_max,
                cultivation_tick_min,
            )
        cultivation_tick_min = max(0, cultivation_tick_min)
        cultivation_tick_max = max(cultivation_tick_min, cultivation_tick_max)
        if innate_stat_min > innate_stat_max:
            innate_stat_min, innate_stat_max = innate_stat_max, innate_stat_min
        innate_stat_min = max(0, innate_stat_min)

        return cls(
            token=token,
            cultivation_tick=cultivation_tick,
            cultivation_tick_min=cultivation_tick_min,
            cultivation_tick_max=cultivation_tick_max,
            combat_exp_gain=combat_exp_gain,
            soul_exp_gain=soul_exp_gain,
            innate_stat_min=innate_stat_min,
            innate_stat_max=innate_stat_max,
        )


__all__ = ["BotConfig"]
