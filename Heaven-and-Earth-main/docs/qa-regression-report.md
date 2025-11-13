# QA Regression Report

## Overview
This regression sweep validates the refreshed martial soul schema, affinity handling, and save normalization routines while exercising critical gameplay commands (`/cultivate`, combat resolution, and progression breakthroughs).

## Test Execution Summary
- `pytest tests/test_player_migration.py`
- `pytest tests/test_command_regressions.py`
- `pytest` (full suite)

All suites completed successfully.

## Edge Cases & Notes
- Confirmed that newly created players awaken martial souls with normalized defaults even when no explicit payload is supplied.
- Verified that favoured weapon resonance multiplies spear and elemental techniques when martial soul focus matches equipped gear.
- Validated `InnateSoul.from_mapping` continues to tolerate single-value affinity payloads and drops malformed affinity modifiers.
- Ensured the Soul Land migration backfills sequence fields and normalizes invalid soul power levels without discarding original entries.
- Regression coverage for `/cultivate`, combat, and breakthrough logic uses deterministic randomness to assert exact thresholds and state transitions.
