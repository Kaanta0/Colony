# Soul Land Integration Design

## Canonical Reference Summary

### Martial Soul Categories
- **Beast Spirits** – manifest as the essence of magical beasts and grow alongside the user (e.g., Blue Silver Grass, Clear Sky Hammer).
- **Tool Spirits** – manifest as weapons, armor, instruments, or utility objects (e.g., Seven Treasure Glazed Tile Pagoda, Nine Heart Flowering Apple).

_Note: The core Heaven & Earth ladder now follows traditional Xianxia titles. Refer to [Cultivation Realm Progression](./realm-progression.md) when mapping Soul Land ranks to in-game realms._
- **Body Spirits** – innate bodily augmentations (e.g., external bone manifestations, body part enhancements).
- **Weapon Spirits** – combat-oriented tools, sometimes treated as a high-tier subset of tool spirits.
- **Food & Auxiliary Spirits** – support-focused spirits that provide buffs, healing, or utility rather than raw combat power.

### Spirit Ring Color Progression
| Ring Color | Canonical Age Bracket | Notes |
| --- | --- | --- |
| **White** | 10–99 years | Qi Condensation through Foundation Establishment. |
| **Yellow** | 100–999 years | Core Formation through Soul Formation. |
| **Purple** | 1,000–9,999 years | Soul Transformation through Illusory Yin. |
| **Black** | 10,000–99,999 years | Corporeal Yang through Nirvana Shatterer. |
| **Red** | 100,000–999,999 years | Heaven's Blight through Arcane Void. |
| **Gold** | 1,000,000+ years | Void Tribulant through Heaven Trampling and divine inheritances. |

### Cultivation Ranks & Pathways
1. **Qi Condensation (Ranks 1–5)**
2. **Foundation Establishment (6–10)**
3. **Core Formation (11–15)**
4. **Nascent Soul (16–20)**
5. **Soul Formation (21–25)**
6. **Soul Transformation (26–30)**
7. **Ascendant (31–35)**
8. **Illusory Yin (36–40)**
9. **Corporeal Yang (41–45)**
10. **Nirvana Scryer (46–50)**
11. **Nirvana Cleanser (51–55)**
12. **Nirvana Shatterer (56–60)**
13. **Heaven's Blight (61–65)**
14. **Nirvana Void (66–70)**
15. **Spirit Void (71–75)**
16. **Arcane Void (76–80)**
17. **Void Tribulant (81–85)**
18. **Half-Heaven Trampling (86–90)**
19. **Heaven Trampling (91–100)**

Key progression rules:
- Advancing ten ranks unlocks a spirit ring slot, with most characters capping at nine rings.
- Post-Seagod Island arcs introduce **Spirit Essence Compression** and god trials; keep extensible hooks for divine realms.

### Auxiliary Systems
- **Spirit Bones** – limb, torso, head, and external attachments granting skills; age tiers mirror rings.
- **Domains** – area control effects unlocked by high-quality rings or god trials.
- **Spirit Tool Technology** – optional magi-tech layer; consider as late-game crafting system.
- **Mutations & Variants** – evolved martial souls (e.g., Blue Silver Emperor) through bloodline awakening or external stimuli.

## Martial Soul System Overview

| Soul Land Concept | Current Implementation Touchpoint | Notes |
| --- | --- | --- |
| Martial Soul Affinity | `SpiritualAffinity` enum and martial soul affinities (`bot/models/combat.py`) | Elemental overlap already models attack/resistance. Need new categories for weapon/tool vs. beast flavor tagging. |
| Weapon Resonance | `MartialSoul.favoured_weapons`, `PlayerProgress.martial_soul_damage_multiplier` | Favoured weapon focus amplifies matching skills and scales with spirit ring investment. |
| Martial Soul Quality (Grades) | `PlayerProgress.martial_souls[*].grade` | Map grade 1–9 to ring color tiers; consider clamping Title Douluo requirements to grade ≥6. |
| Spirit Rings | `PlayerProgress.soul_rings` (`SpiritRing` dataclass) | Ring payload tracks age, colour, source event, and attached martial soul. |
| Spirit Bones | `PlayerProgress.soul_bones` (`SpiritBone` dataclass) | Equipment slots distinguished from rings; payload persists limb slot, age, and bonuses. |
| Cultivation Rank | Player progression & cultivation EXP system (`perform_cultivation`, martial-soul multipliers) | Introduce rank ladder aligned with Soul Land stages; modify EXP tables to require ring acquisition before advancing. |
| Support-Type Spirits | Auxiliary affinity tags on `MartialSoul.signature_abilities` | Use to flag buff/healing abilities; integrate with food/auxiliary spirit classification. |

## Proposed Mechanics & API Changes

### 1. Martial Soul Classification Layer
- Extend martial soul data with `category: MartialSoulType` (`beast`, `tool`, `weapon`, `body`, `auxiliary`).
- Persist classification on `MartialSoul` and propagate through serialization (`to_mapping`, `from_mapping`).
- Add helpers for flavor text and mechanical hooks (e.g., beast souls gaining passive stats, tool souls improving weapon skills via favoured weapon focus).

### 2. Spirit Ring Slots & Metadata
- `SpiritRing` dataclass captures `color`, `age`, `slot_index`, attached martial soul, and granted ability keys.
- Track rings on the player via `soul_rings: list[SpiritRing]` with max size tied to rank.
- Travel events surface resonance opportunities that emit serialized ring payloads; legacy innate soul mutations convert into starter rings during migration.

### 3. Rank Progression Integration
- Define rank thresholds aligned with canonical titles; integrate into cultivation flow (`perform_cultivation` consumers) by checking `player.rank` against `CultivationEXP` milestones.
- Require ring attachment before crossing each 10-rank boundary; surfaces UI hints when missing rings.
- Provide conversion utility from existing EXP to nearest Soul Land rank.

### 4. Auxiliary Systems Support
- Model spirit bones as separate inventory (`SpiritBone` dataclass) referencing affected limb, age tier, and active skill.
- Extend combat calculations to factor ring/bone bonuses via `StatMultipliers` and passive effects.
- Add domain hooks by tagging high-age rings with `domain_effect` payload consumed by encounter resolvers.

### 5. Terminology & UI Updates
- Update player profile views to surface martial soul type, ring colors, and rank title (`bot/cogs/player.py` rendering helpers).
- Provide localization strings for new terms (e.g., “Spirit Douluo”, “Spirit Bone”).
- Ensure travel events referencing innate soul mutations adapt to ring terminology.

## Data Structures

```python
@dataclass
class SpiritRing:
    color: Literal["white", "yellow", "purple", "black", "red", "gold"]
    age: int
    slot_index: int
    martial_soul: str | None
    ability_keys: tuple[str, ...] = ()
    domain_effect: str | None = None
```

```python
@dataclass
class SpiritBone:
    slot: Literal["head", "torso", "left_arm", "right_arm", "left_leg", "right_leg", "external"]
    age: int
    abilities: tuple[str, ...]
    passive_bonuses: StatMultipliers | None = None
```

- `PlayerProgress` persists `martial_souls`, `soul_rings`, and `soul_bones` with normalization mirroring other collections.
- Migration `0005_martial_schema` converts legacy base payloads and populates default martial souls when missing.

## API/Schema Impact
- **Storage**: `bot/storage.py` serializers must round-trip new structures; version existing payloads.
- **Commands**: Player inspection commands should expose `spirit_rings` and `spirit_bones`, respecting permissions.
- **Config**: Guild-level overrides for base grade weights expand to allow ring rarity adjustments.

## Open Questions & Lore Ambiguities
1. **Multiple Martial Souls** – Canon supports dual martial souls; decide whether to allow multiple active souls versus merged affinities.
2. **God-Level Rings** – Determine if gold rings are unlockable at launch or reserved for future divine systems.
3. **Ring Ability Sourcing** – Clarify how ability keys map to canonical skills (e.g., Ten Thousand Year Blue Silver Entangling Vines) versus procedurally generated abilities.
4. **Spirit Bone Drop Rates** – Need drop tables balancing lore rarity (extremely low) with gameplay accessibility.
5. **Technology Timeline** – Later arcs introduce soul tools/engineers; confirm era settings to avoid contradictions.
6. **Bloodline Awakening Mechanics** – Decide if mutations cover legendary evolutions (Blue Silver Emperor) or if separate awakening events are required.
7. **Stat Scaling** – Finalize how ring age translates to numerical bonuses relative to existing base grade multipliers.

## Next Steps
- Validate classification schema against current data exports.
- Prototype serialization changes behind a feature flag.
- Draft migration strategy for live player profiles.
