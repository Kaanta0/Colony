from __future__ import annotations

import asyncio
import copy
import logging
import textwrap
import random
import re
import time
from functools import lru_cache
from heapq import nlargest
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from collections import defaultdict
from collections.abc import Mapping
from typing import (
    Any,
    Callable,
    Iterable,
    Literal,
    Optional,
    Sequence,
    TYPE_CHECKING,
    TypeVar,
    cast,
)
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import discord
from discord import InteractionType, app_commands
from discord.ext import commands

from ..config import BotConfig
from ..constants import (
    DEFAULT_CULTIVATION_COOLDOWN,
    CURRENCY_EMOJI_TEXT,
    WANDER_TRAVEL_EMOJI,
    WANDER_TRAVEL_EMOJI_TEXT,
)
from ..game import (
    add_item_to_inventory,
    attempt_breakthrough,
    award_loot,
    complete_bond_mission,
    can_evolve_skill,
    cultivation_ready,
    gain_combat_exp,
    gain_soul_exp,
    inventory_capacity,
    inventory_load,
    perform_cultivation,
    roll_talent_stats,
)
from ..models.combat import (
    DamageType,
    Skill,
    SkillCategory,
    SpiritualAffinity,
    Stats,
    WeaponType,
    PLAYER_STAT_NAMES,
    AFFINITY_RELATIONSHIPS,
    skill_grade_number,
)
from ..models.players import (
    BondProfile,
    PlayerProgress,
    PronounSet,
    add_equipped_item,
    active_weapon_types,
    equipped_items_for_player,
    equipment_slot_usage,
    grant_legacy_to_heir,
    remove_equipped_item,
)
from ..models.progression import (
    CultivationPath,
    CultivationTechnique,
    CultivationStage,
    CultivationPhase,
    EquipmentSlot,
    EQUIPMENT_SLOT_ORDER,
    Race,
    SpecialTrait,
    InnateSoul,
    InnateSoulSet,
    TechniqueStatMode,
    Title,
    TitlePosition,
    DEFAULT_STAGE_BASE_STAT,
    STAGE_STAT_TARGETS,
    MORTAL_REALM_KEY,
    equipment_slot_capacity,
)
from ..models.soul_land import MartialSoul, MartialSoulType
from ..models.world import (
    Currency,
    Item,
    Location,
    LocationNPC,
    LocationNPCType,
)
from ..models.map import (
    TravelPath,
    TravelEvent,
    TileCoordinate,
    CampAction,
    EncounterBucket,
    TileCategory,
)
from ..travel import MiniMapRenderer
from ..views import (
    CallbackButton,
    CallbackSelect,
    CultivationActionView,
    EncounterDecisionView,
    OwnedView,
    ProfileRefreshButton,
    ProfileView,
    ReturnButtonView,
    SafeZoneActionView,
    ShopEntry,
    ShopPurchaseResult,
    ShopView,
    SimpleCallback,
    TradeBuilderView,
    TradeConfirmationView,
    TradeNegotiationView,
    TradePartnerOption,
    TradeProposal,
    TravelNavigatorView,
    WanderExpeditionView,
)
from ..data.realm_success_flashes import REALM_SUCCESS_FLASH_VARIATIONS
from ..utils import build_travel_narrative, format_number
from .base import HeavenCog
from .combat import CombatSetupError, AFFINITY_IMAGERY
from .admin import require_admin

if TYPE_CHECKING:
    from .combat import CombatCog
    from .economy import EconomyCog


CONCEAL_CLEAR_SENTINEL = "__CLEAR_OVERRIDE__"


def _format_stage_display(realm_display: str, phase_display: str) -> str:
    """Combine realm and phase labels into a single cultivation stage string."""

    realm = (realm_display or "").strip()
    phase = (phase_display or "").strip()

    if not realm:
        return phase or "Unknown"
    if not phase:
        return realm
    return f"{realm} — {phase}"


CULTIVATION_AFFINITY_METADATA: dict[SpiritualAffinity | None, dict[str, str]] = {
    None: {
        "title": "Primordial Qi",
        "noun_singular": "current",
        "noun_plural": "currents",
    },
    SpiritualAffinity.FIRE: {
        "title": "Scarlet Flame",
        "noun_singular": "ember",
        "noun_plural": "embers",
    },
    SpiritualAffinity.WATER: {
        "title": "Azure Tide",
        "noun_singular": "torrent",
        "noun_plural": "torrents",
    },
    SpiritualAffinity.WIND: {
        "title": "Soaring Gale",
        "noun_singular": "gust",
        "noun_plural": "gusts",
    },
    SpiritualAffinity.EARTH: {
        "title": "Stoneheart Earth",
        "noun_singular": "earth mote",
        "noun_plural": "earth motes",
    },
    SpiritualAffinity.METAL: {
        "title": "Auric Steel",
        "noun_singular": "metal shard",
        "noun_plural": "metal shards",
    },
    SpiritualAffinity.ICE: {
        "title": "Frostbound Glaze",
        "noun_singular": "ice crystal",
        "noun_plural": "ice crystals",
    },
    SpiritualAffinity.LIGHTNING: {
        "title": "Thunderclap Spark",
        "noun_singular": "spark",
        "noun_plural": "sparks",
    },
    SpiritualAffinity.LIGHT: {
        "title": "Radiant Lumen",
        "noun_singular": "ray",
        "noun_plural": "rays",
    },
    SpiritualAffinity.DARKNESS: {
        "title": "Abyssal Gloom",
        "noun_singular": "shadow",
        "noun_plural": "shadows",
    },
    SpiritualAffinity.LIFE: {
        "title": "Verdant Bloom",
        "noun_singular": "sprout",
        "noun_plural": "sprouts",
    },
    SpiritualAffinity.DEATH: {
        "title": "Nether Requiem",
        "noun_singular": "wisp",
        "noun_plural": "wisps",
    },
    SpiritualAffinity.SAMSARA: {
        "title": "Samsara Aegis",
        "noun_singular": "samsara mote",
        "noun_plural": "samsara motes",
    },
    SpiritualAffinity.SPACE: {
        "title": "Void Horizon",
        "noun_singular": "rift",
        "noun_plural": "rifts",
    },
    SpiritualAffinity.TIME: {
        "title": "Chrono Flow",
        "noun_singular": "chronal mote",
        "noun_plural": "chronal motes",
    },
    SpiritualAffinity.GRAVITY: {
        "title": "Starbound Weight",
        "noun_singular": "gravitas thread",
        "noun_plural": "gravitas threads",
    },
    SpiritualAffinity.POISON: {
        "title": "Viridian Venom",
        "noun_singular": "toxin bead",
        "noun_plural": "toxin beads",
    },
    SpiritualAffinity.MUD: {
        "title": "Loamy Mire",
        "noun_singular": "mud mote",
        "noun_plural": "mud motes",
    },
    SpiritualAffinity.TEMPERATURE: {
        "title": "Dual Polarity",
        "noun_singular": "thermal pulse",
        "noun_plural": "thermal pulses",
    },
    SpiritualAffinity.LAVA: {
        "title": "Magma Torrent",
        "noun_singular": "magma bead",
        "noun_plural": "magma beads",
    },
    SpiritualAffinity.TWILIGHT: {
        "title": "Duskwoven Glow",
        "noun_singular": "twilight thread",
        "noun_plural": "twilight threads",
    },
    SpiritualAffinity.PERMAFROST: {
        "title": "Permafrost Bastion",
        "noun_singular": "permafrost shard",
        "noun_plural": "permafrost shards",
    },
    SpiritualAffinity.DUSTSTORM: {
        "title": "Desert Tempest",
        "noun_singular": "sand gale",
        "noun_plural": "sand gales",
    },
    SpiritualAffinity.PLASMA: {
        "title": "Starfire Surge",
        "noun_singular": "plasma spark",
        "noun_plural": "plasma sparks",
    },
    SpiritualAffinity.STEAM: {
        "title": "Geyser Shroud",
        "noun_singular": "steam coil",
        "noun_plural": "steam coils",
    },
    SpiritualAffinity.INFERNO: {
        "title": "Inferno Gale",
        "noun_singular": "inferno plume",
        "noun_plural": "inferno plumes",
    },
    SpiritualAffinity.FLASHFROST: {
        "title": "Flashfrost Rime",
        "noun_singular": "flashfrost bolt",
        "noun_plural": "flashfrost bolts",
    },
    SpiritualAffinity.FROSTFLOW: {
        "title": "Glacial Current",
        "noun_singular": "frostflow eddy",
        "noun_plural": "frostflow eddies",
    },
    SpiritualAffinity.BLIZZARD: {
        "title": "Whiteout Tempest",
        "noun_singular": "blizzard flake",
        "noun_plural": "blizzard flakes",
    },
    SpiritualAffinity.TEMPEST: {
        "title": "Stormcrown Tempest",
        "noun_singular": "tempest gale",
        "noun_plural": "tempest gales",
    },
    SpiritualAffinity.MIST: {
        "title": "Veiled Mistshroud",
        "noun_singular": "mist veil",
        "noun_plural": "mist veils",
    },
    SpiritualAffinity.ENTROPY: {
        "title": "Void Entropy",
        "noun_singular": "entropy mote",
        "noun_plural": "entropy motes",
    },
}

CULTIVATION_AFFINITY_IMAGERY: dict[SpiritualAffinity | None, dict[str, tuple[str, ...]]] = {
    affinity: {
        "manifestations": tuple(data.get("manifestations", [])),
        "effects": tuple(data.get("effects", [])),
    }
    for affinity, data in AFFINITY_IMAGERY.items()
}

DEFAULT_CULTIVATION_IMAGERY = CULTIVATION_AFFINITY_IMAGERY.get(
    None,
    {
        "manifestations": ("untamed qi currents",),
        "effects": ("surge in layered crescendos",),
    },
)


def _realm_key_from_stage(stage: CultivationStage | None) -> str:
    """Derive the base realm key for a cultivation stage."""

    if stage is None:
        return MORTAL_REALM_KEY
    key = str(stage.key or "").strip().lower().replace("_", "-")
    if key:
        for phase in CultivationPhase:
            suffix = f"-{phase.value}"
            if key.endswith(suffix) and len(key) > len(suffix):
                key = key[: -len(suffix)]
                break
        if key:
            return key
    realm_name = str(stage.realm or stage.name or "").strip().lower()
    if realm_name:
        return realm_name.replace(" ", "-").replace("_", "-")
    return MORTAL_REALM_KEY


@dataclass(frozen=True)
class RealmTribulationProfile:
    key: str
    rank: int
    title: str
    epithet: str
    power_comparison: str
    scope: str
    tribulation_title: str
    success_flash: str
    failure_echo: str
    readiness_hint: str
    resonance: str
    success_flash_variants: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        variants = REALM_SUCCESS_FLASH_VARIATIONS.get(self.key)
        if not variants:
            slug = _slugify_realm_identifier(self.title)
            variants = REALM_SUCCESS_FLASH_VARIATIONS.get(slug)
        if not variants:
            variants = (self.success_flash,)
        object.__setattr__(self, "success_flash_variants", tuple(variants))

    def random_success_flash(self) -> str:
        if self.success_flash_variants:
            return random.choice(self.success_flash_variants)
        return self.success_flash


_REALM_PROFILE_DATA: Sequence[tuple[str, dict[str, str]]] = [
    (
        MORTAL_REALM_KEY,
        {
            "title": "Mortal",
            "epithet": "Fledgling Breath Seeker",
            "power_comparison": "Barely beyond mundane soldiers—the first sparks of qi simply steady your stance.",
            "scope": "You outrun militias and shrug off blows that would bruise ordinary warriors.",
            "tribulation_title": "BREATHING RHYTHM TRIAL",
            "success_flash": "Steady breaths align with qi threads, coaxing awareness into sleeping meridians.",
            "failure_echo": "Your rhythm falters; the qi you sought disperses back into the air.",
            "readiness_hint": "Perfect foundational forms and calm the heart before courting the rhythm again.",
            "resonance": "\n".join([
                "• You outrun militias and shrug off blows that would bruise ordinary warriors.",
                "• Meditation draws faint motes of power toward your dantian.",
            ]),
        },
    ),
    (
        "qi-condensation",
        {
            "title": "Qi Condensation",
            "epithet": "Nebula Heart Initiate",
            "power_comparison": "Tenfold beyond mortals—condensed qi can shatter fortress walls with a gesture.",
            "scope": "Your aura blankets training grounds; lesser cultivators struggle to breathe nearby.",
            "tribulation_title": "NEBULA CONDENSATION STORM",
            "success_flash": "Misty qi collapses into a brilliant core that pulses with disciplined rhythm.",
            "failure_echo": "The nascent nebula unravels, lashing your channels with shards of cold essence.",
            "readiness_hint": "Cycle breath until the condensate no longer trembles, then brave the storm again.",
            "resonance": "\n".join([
                "• Your aura blankets training grounds; lesser cultivators struggle to breathe nearby.",
                "• Techniques consume a fraction of the qi they once devoured.",
            ]),
        },
    ),
    (
        "foundation-establishment",
        {
            "title": "Foundation Establishment",
            "epithet": "Immutable Pillar Architect",
            "power_comparison": "Hundreds of times beyond condensation adepts—pillars of dao anchor every technique.",
            "scope": "Your foundation steadies provinces; ley lines bend to reinforce your will.",
            "tribulation_title": "IMMUTABLE PILLAR ORDEAL",
            "success_flash": "Law pillars lock through each meridian, fusing body and soul into bedrock.",
            "failure_echo": "Uneven pillars collapse, rattling bones with reverberating backlash.",
            "readiness_hint": "Scour cracks from your dao heart before daring to raise the pillars anew.",
            "resonance": "\n".join([
                "• Your foundation steadies provinces; ley lines bend to reinforce your will.",
                "• Companions bask in the calm bastion radiating from your core.",
            ]),
        },
    ),
    (
        "core-formation",
        {
            "title": "Core Formation",
            "epithet": "Stellar Core Forgemaster",
            "power_comparison": "Thousands of times beyond foundation—an inner star answers every command.",
            "scope": "Your radiance illuminates battlefields; foes wilt beneath stellar heat.",
            "tribulation_title": "STARFORGE CORE TEMPERING",
            "success_flash": "A molten core hardens into a steady sun, spinning within your dantian.",
            "failure_echo": "The core fractures, spilling unstable plasma that sears your channels.",
            "readiness_hint": "Purge impurities and synchronise the core’s rotation before reopening the forge.",
            "resonance": "\n".join([
                "• Your radiance illuminates battlefields; foes wilt beneath stellar heat.",
                "• Allies draw courage from the dawn blazing behind your eyes.",
            ]),
        },
    ),
    (
        "nascent-soul",
        {
            "title": "Nascent Soul",
            "epithet": "Twin-Soul Commander",
            "power_comparison": "Millions of times beyond core sovereigns—a nascent avatar mirrors every intention.",
            "scope": "Two bodies of will act in concert, directing wars and forging destiny.",
            "tribulation_title": "SPIRIT INFANT MANIFESTATION",
            "success_flash": "A radiant infant steps from your core, wielding dao light alongside you.",
            "failure_echo": "The infant splinters, screaming discord that scars your consciousness.",
            "readiness_hint": "Still every stray thought; only perfect unity sustains the nascent self.",
            "resonance": "\n".join([
                "• Two bodies of will act in concert, directing wars and forging destiny.",
                "• Companions trust your soul avatar to guard them from afar.",
            ]),
        },
    ),
    (
        "soul-formation",
        {
            "title": "Soul Formation",
            "epithet": "Rivers of Intent Weaver",
            "power_comparison": "You eclipse nascent soul prodigies—consciousness engraves living dao glyphs.",
            "scope": "Soul rivers course through inner worlds, sculpting fate beneath your palm.",
            "tribulation_title": "SOUL RIVER WEAVING",
            "success_flash": "Runes blaze across your sea of consciousness, knitting rivers of intent together.",
            "failure_echo": "The rivers flood uncontrolled, eroding the shores of your psyche.",
            "readiness_hint": "Refine thought after thought until even dreams obey your cadence.",
            "resonance": "\n".join([
                "• Soul rivers course through inner worlds, sculpting fate beneath your palm.",
                "• Allies feel their spirits soothed by the tides you command.",
            ]),
        },
    ),
    (
        "soul-transformation",
        {
            "title": "Soul Transformation",
            "epithet": "Soulflame Chrysalis Regent",
            "power_comparison": "Soul formation sages pale—you molt into incandescent flame that redraws destiny.",
            "scope": "Soulflame avatars stride across continents refining karma with every step.",
            "tribulation_title": "SOULFLAME METAMORPHOSIS",
            "success_flash": "Luminous cocoons ignite, birthing a renewed spirit woven from pure flame.",
            "failure_echo": "Half-formed chrysalis shards tear through your meridians and mind.",
            "readiness_hint": "Temper lingering mortal intent before surrendering to metamorphosis.",
            "resonance": "\n".join([
                "• Soulflame avatars stride across continents refining karma with every step.",
                "• Companions feel their spirits steadied by your tempered blaze.",
            ]),
        },
    ),
    (
        "ascendant",
        {
            "title": "Ascendant",
            "epithet": "Sky-Piercing Seeker",
            "power_comparison": "Heavens tremble as you pry open hidden gates—few below dare whisper your name.",
            "scope": "Ascension light surges through sects, uplifting armies with a single command.",
            "tribulation_title": "ASCENDANT SKY CLASH",
            "success_flash": "Cloud seas part and stairways of light coil around you toward higher heavens.",
            "failure_echo": "The heavens slam shut, casting you down amid thunderous refusal.",
            "readiness_hint": "Stabilise dao heart and soulflame before forcing the sky to yield.",
            "resonance": "\n".join([
                "• Ascension light surges through sects, uplifting armies with a single command.",
                "• Followers glimpse distant heavens through your triumphant gaze.",
            ]),
        },
    ),
    (
        "illusory-yin",
        {
            "title": "Illusory Yin",
            "epithet": "Twilight Mirage Matron",
            "power_comparison": "Yin profundities obey; even great clans lose themselves within your mirages.",
            "scope": "Boundless twilight layers cloak realms, hiding allies within endless night.",
            "tribulation_title": "TWILIGHT VEIL TRIAL",
            "success_flash": "Spectral moons align, weaving veils that answer your whispered command.",
            "failure_echo": "The veil tears, releasing hungry phantoms that gnaw at your spirit.",
            "readiness_hint": "Harmonise yin calm and ruthless precision before deepening the veil.",
            "resonance": "\n".join([
                "• Boundless twilight layers cloak realms, hiding allies within endless night.",
                "• Enemies strike shadows only to meet your mirrored forms.",
            ]),
        },
    ),
    (
        "corporeal-yang",
        {
            "title": "Corporeal Yang",
            "epithet": "Solar Furnace Imperator",
            "power_comparison": "Yang furnaces roar—your flesh becomes a blazing crucible of annihilation.",
            "scope": "Every motion trails sunfire; legions ignite beneath your relentless advance.",
            "tribulation_title": "SOLAR FURNACE HAMMERING",
            "success_flash": "Suns condense into marrow, tempering sinew into radiant steel.",
            "failure_echo": "The furnace flares wild, threatening to incinerate body and soul alike.",
            "readiness_hint": "Balance ferocity with precision so the furnace hammers rather than consumes.",
            "resonance": "\n".join([
                "• Every motion trails sunfire; legions ignite beneath your relentless advance.",
                "• Allies bask in invigorating warmth that mends wounds mid-battle.",
            ]),
        },
    ),
    (
        "nirvana-scryer",
        {
            "title": "Nirvana Scryer",
            "epithet": "Karmic Mirror Oracle",
            "power_comparison": "Second step begins—karma itself unfurls scrolls at your slightest glance.",
            "scope": "Destinies of clans and nations lie exposed within your mirror lake.",
            "tribulation_title": "KARMIC MIRROR VIGIL",
            "success_flash": "Mirror waters settle, revealing branching futures ready for your decree.",
            "failure_echo": "Ripples distort, splintering sight into agonising fragments.",
            "readiness_hint": "Cleanse karmic debts lest the mirror echo flaws back into your heart.",
            "resonance": "\n".join([
                "• Destinies of clans and nations lie exposed within your mirror lake.",
                "• Allies march with certainty guided by glimpses you share.",
            ]),
        },
    ),
    (
        "nirvana-cleanser",
        {
            "title": "Nirvana Cleanser",
            "epithet": "Purifying Flame Sovereign",
            "power_comparison": "Cleansing flames strip away imperfection; heavens recognise your refined dao.",
            "scope": "Whole sects bathe in nirvana fire that burns corruption yet spares devotion.",
            "tribulation_title": "NIRVANA PURIFICATION PYRE",
            "success_flash": "Azure fire scours every flaw, leaving crystalline patterns in its wake.",
            "failure_echo": "Flames sputter, coating meridians in acrid soot and remorse.",
            "readiness_hint": "Confess buried regrets so the fire purifies instead of devouring.",
            "resonance": "\n".join([
                "• Whole sects bathe in nirvana fire that burns corruption yet spares devotion.",
                "• Companions feel old wounds evaporate in cleansing heat.",
            ]),
        },
    ),
    (
        "nirvana-shatterer",
        {
            "title": "Nirvana Shatterer",
            "epithet": "Fate-Sundering Herald",
            "power_comparison": "Karmic chains snap at your command—fate itself is clay within your grasp.",
            "scope": "Entire timelines tremble as you decide which threads survive the shattering.",
            "tribulation_title": "FATE SHATTERING CALAMITY",
            "success_flash": "Shards of destiny spin around you, reforming into obedient constellations.",
            "failure_echo": "The shards cut deep, bleeding memories you hoped to preserve.",
            "readiness_hint": "Accept the cost of severed destinies before raising your hand to fate.",
            "resonance": "\n".join([
                "• Entire timelines tremble as you decide which threads survive the shattering.",
                "• Allies rely on you to cleave paths through inevitable doom.",
            ]),
        },
    ),
    (
        "heavens-blight",
        {
            "title": "Heaven's Blight",
            "epithet": "Tribulation Venom Warden",
            "power_comparison": "You weaponise tribulation poison—hostile heavens recoil from your stain.",
            "scope": "Calamity clouds curdle at your approach, their fury refined into obedient toxin.",
            "tribulation_title": "BLIGHTED HEAVEN TEMPERING",
            "success_flash": "Lightning venom settles into your veins, purring like a loyal serpent.",
            "failure_echo": "The venom rebels, scorching meridians and dimming your dao star.",
            "readiness_hint": "Embrace suffering without resentment; only then does blight kneel.",
            "resonance": "\n".join([
                "• Calamity clouds curdle at your approach, their fury refined into obedient toxin.",
                "• Enemies feel tribulation echoes gnawing at their courage.",
            ]),
        },
    ),
    (
        "nirvana-void",
        {
            "title": "Nirvana Void",
            "epithet": "Void Tide Monarch",
            "power_comparison": "Void tides bend; absence itself becomes brush and ink for your dao.",
            "scope": "World seams ripple as you weave rivers of nothingness into obedient shapes.",
            "tribulation_title": "VOID NIRVANA SUBLIMATION",
            "success_flash": "Silent tides of void curl around you, forming an unending horizon.",
            "failure_echo": "The void yawns hungry, threatening to erase your imprint entirely.",
            "readiness_hint": "Anchor identity within paradox before surrendering to the tide.",
            "resonance": "\n".join([
                "• World seams ripple as you weave rivers of nothingness into obedient shapes.",
                "• Companions walk hidden corridors between breaths of reality.",
            ]),
        },
    ),
    (
        "spirit-void",
        {
            "title": "Spirit Void",
            "epithet": "Void-Walking Emissary",
            "power_comparison": "Soul intent roams freely; your avatars strike from horizons unseen.",
            "scope": "Armies witness phantom generals leading them while you remain elsewhere.",
            "tribulation_title": "SOUL VOID PILGRIMAGE",
            "success_flash": "Your spirit threads through endless emptiness, returning crowned in starlight.",
            "failure_echo": "The pilgrimage loses you—echoes wander soulless across the void.",
            "readiness_hint": "Map anchors for every avatar lest one return bearing madness.",
            "resonance": "\n".join([
                "• Armies witness phantom generals leading them while you remain elsewhere.",
                "• Allies coordinate with effortless clarity across impossible distances.",
            ]),
        },
    ),
    (
        "arcane-void",
        {
            "title": "Arcane Void",
            "epithet": "Paradox Cipher Sage",
            "power_comparison": "Esoteric glyphs etch the void; contradictions resolve at your command.",
            "scope": "Philosophies manifest as weapons; realities rewrite to match your logic.",
            "tribulation_title": "ARCANE VOID CIPHER",
            "success_flash": "Runes spiral across the emptiness, locking paradoxes into obedient lattices.",
            "failure_echo": "Symbols collide chaotically, splintering minds that glimpse them.",
            "readiness_hint": "Resolve personal paradoxes before daring to script those of heaven.",
            "resonance": "\n".join([
                "• Philosophies manifest as weapons; realities rewrite to match your logic.",
                "• Companions wield arcane boons born from your inscriptions.",
            ]),
        },
    ),
    (
        "void-tribulant",
        {
            "title": "Void Tribulant",
            "epithet": "Stormcrowned Tribulation Lord",
            "power_comparison": "Calamities feed you—tribulation lightning crowns your every stride.",
            "scope": "Heavenly disasters become weapons, marching beside you as obedient hosts.",
            "tribulation_title": "VOID TRIBULATION STORM",
            "success_flash": "Stormcrowns ignite above your head, channeling fury into serene resolve.",
            "failure_echo": "The storm rebels, scattering your essence across screaming heavens.",
            "readiness_hint": "Welcome each strike as tempering rather than punishment.",
            "resonance": "\n".join([
                "• Heavenly disasters become weapons, marching beside you as obedient hosts.",
                "• Allies advance beneath storm shields woven from your will.",
            ]),
        },
    ),
    (
        "half-heaven-trampling",
        {
            "title": "Half-Heaven Trampling",
            "epithet": "Hemisphere Sovereign",
            "power_comparison": "One foot anchors mortal realms while the other grinds heavenly gates ajar.",
            "scope": "You bridge worlds effortlessly; realms knit together beneath your stride.",
            "tribulation_title": "HEMI-HEAVEN CRUSHING",
            "success_flash": "Half the heavens buckle, forming a throne that links earth and sky.",
            "failure_echo": "The gate snaps shut, crushing limbs caught between duty and ascension.",
            "readiness_hint": "Balance compassion for mortals with hunger for the heavens before stepping through.",
            "resonance": "\n".join([
                "• You bridge worlds effortlessly; realms knit together beneath your stride.",
                "• Allies travel beside you, sheltered from celestial backlash.",
            ]),
        },
    ),
    (
        "heaven-trampling",
        {
            "title": "Heaven Trampling",
            "epithet": "Firmament-Subduing Monarch",
            "power_comparison": "The firmament cracks beneath each decree—heaven and earth bow unwillingly.",
            "scope": "Your dao rewrites cosmic law; stars realign to announce your dominion.",
            "tribulation_title": "FIRMAMENT DOMINION DECREE",
            "success_flash": "Heaven’s vault shatters into radiant shards that swirl into your crown.",
            "failure_echo": "Heaven retaliates, birthing chains of light that try to bind your wrists.",
            "readiness_hint": "Carry the burden of worlds; trampling heaven demands a heart vast enough to hold them.",
            "resonance": "\n".join([
                "• Your dao rewrites cosmic law; stars realign to announce your dominion.",
                "• Companions fight as living legends beneath the shadow of your decree.",
            ]),
        },
    ),
]


REALM_TRIBULATION_PROFILES: tuple[RealmTribulationProfile, ...] = tuple(
    RealmTribulationProfile(key=key, rank=index, **details)
    for index, (key, details) in enumerate(_REALM_PROFILE_DATA)
)


def _slugify_realm_identifier(value: str) -> str:
    value = value.strip().lower()
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def _build_realm_profile_index(
    profiles: Iterable[RealmTribulationProfile],
) -> dict[str, RealmTribulationProfile]:
    index: dict[str, RealmTribulationProfile] = {}
    for profile in profiles:
        variants = {
            profile.key,
            profile.key.replace("-", "_"),
            profile.key.replace("_", "-"),
            profile.key.replace("-", ""),
            _slugify_realm_identifier(profile.title),
            profile.title.lower(),
            profile.title.lower().replace(" ", "-"),
        }
        expanded: set[str] = set()
        for variant in variants:
            if not variant:
                continue
            expanded.add(variant)
            expanded.add(variant.replace("_", ""))
            expanded.add(variant.replace("-", ""))
        for variant in expanded:
            index.setdefault(variant, profile)
    return index


REALM_TRIBULATION_PROFILE_INDEX = _build_realm_profile_index(
    REALM_TRIBULATION_PROFILES
)


def _extract_realm_key(stage: CultivationStage) -> str:
    key = str(getattr(stage, "key", "")).strip().lower()
    for phase in CultivationPhase:
        suffix = f"-{phase.value}"
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key


def _resolve_realm_profile(stage: CultivationStage) -> RealmTribulationProfile:
    base_key = _extract_realm_key(stage)
    search_keys = [
        base_key,
        base_key.replace("_", "-"),
        base_key.replace("-", "_"),
        base_key.replace("-", ""),
    ]
    realm_label = str(getattr(stage, "realm", "")).strip()
    if realm_label:
        slug = _slugify_realm_identifier(realm_label)
        search_keys.extend(
            [
                slug,
                slug.replace("-", "_"),
                slug.replace("-", ""),
                realm_label.lower(),
                realm_label.lower().replace(" ", "-"),
            ]
        )
    for key in search_keys:
        if not key:
            continue
        profile = REALM_TRIBULATION_PROFILE_INDEX.get(key)
        if profile:
            return profile
    order = getattr(stage, "realm_order", 0)
    if 0 <= order < len(REALM_TRIBULATION_PROFILES):
        return REALM_TRIBULATION_PROFILES[order]
    if order < 0:
        return REALM_TRIBULATION_PROFILES[0]
    return REALM_TRIBULATION_PROFILES[-1]


def _compose_realm_context(
    current_stage: CultivationStage, next_stage: CultivationStage
) -> dict[str, str]:
    current_profile = _resolve_realm_profile(current_stage)
    next_profile = _resolve_realm_profile(next_stage)
    context = {
        "current_stage": current_stage.combined_name,
        "next_stage": next_stage.combined_name,
        "current_realm_title": current_profile.title,
        "next_realm_title": next_profile.title,
        "current_realm_title_caps": current_profile.title.upper(),
        "next_realm_title_caps": next_profile.title.upper(),
        "current_realm_epithet": current_profile.epithet,
        "next_realm_epithet": next_profile.epithet,
        "current_realm_power": current_profile.power_comparison,
        "next_realm_power": next_profile.power_comparison,
        "current_realm_scope": current_profile.scope,
        "next_realm_scope": next_profile.scope,
        "current_realm_success": current_profile.random_success_flash(),
        "next_realm_success": next_profile.random_success_flash(),
        "current_realm_failure": current_profile.failure_echo,
        "next_realm_failure": next_profile.failure_echo,
        "current_realm_readiness": current_profile.readiness_hint,
        "next_realm_readiness": next_profile.readiness_hint,
        "current_realm_resonance": current_profile.resonance,
        "next_realm_resonance": next_profile.resonance,
        "current_realm_tribulation": current_profile.tribulation_title,
        "next_realm_tribulation": next_profile.tribulation_title,
        "current_realm_rank": str(current_profile.rank),
        "next_realm_rank": str(next_profile.rank),
        "realm_rank_delta": str(max(0, next_profile.rank - current_profile.rank)),
    }
    return context


class _SafeFormatDict(dict[str, str]):
    def __missing__(self, key: str) -> str:  # pragma: no cover - defensive default
        return ""

MORTAL_CULTIVATION_TEMPLATES: list[str] = [
    "You sit cross-legged on the packed earth, counting breaths until a thread of qi stirs.",
    "You rest your hands upon your knees, guiding a timid current of qi through sluggish meridians.",
    "You listen to the rustle of leaves outside the training hall, letting the quiet settle into your dantian.",
    "You steady your heartbeat, coaxing a faint glow of qi to gather without forcing it.",
    "You trace a simple breathing pattern, feeling the world's energy seep in a trickle at a time.",
]

CULTIVATION_TEMPLATES: list[str] = [
    "You seal your fingers together, drawing {manifestation} to {effect} around your core.",
    "You inhale slowly as {manifestation} settle within your meridians, echoing the cadence of the {affinity_title}.",
    "You etch drifting sigils of {affinity_noun_plural} above your lap until {manifestation} begin to {effect}.",
    "You kneel beside a spirit spring, guiding {affinity_noun_plural} into your dantian while {manifestation} {effect} overhead.",
    "You spin jade prayer beads, each rotation coaxing {manifestation} to {effect} in harmony with the {affinity_title}.",
    "You balance on one palm as {manifestation} orbit you, refining every {affinity_noun_singular} you command.",
    "You set incense burning; the smoke curls into {manifestation} that soon {effect}.",
    "You weave a formation of {affinity_noun_plural}, inviting {manifestation} to {effect} through the pattern.",
    "You steady your heartbeat until it mirrors the {affinity_title}, and {manifestation} answer by {effect}.",
    "You chant an ancient mantra; syllables rise like {affinity_noun_plural} while {manifestation} {effect}.",
    "You circulate qi through hidden meridians, letting {manifestation} {effect} with each cycle.",
    "You project consciousness into the heavens, tugging {manifestation} down until they {effect}.",
    "You rest upon a jade disc, inscribing it with {affinity_noun_plural} as {manifestation} {effect} around you.",
    "You meditate within a starlit array, shaping {manifestation} to {effect} according to the {affinity_title}.",
    "You suspend a drop of essence between your palms; it splits into {affinity_noun_plural} while {manifestation} {effect}.",
    "You trace rings in the air, each one snaring {manifestation} before they {effect} along your spine.",
    "You recite the scriptures of the {affinity_title}, and {manifestation} obediently {effect}.",
    "You raise a barrier of {affinity_noun_plural}, shepherding {manifestation} so they {effect} within it.",
    "You exhale a thin stream of qi that crystallises into {manifestation}, soon to {effect}.",
    "You let your thoughts drift blank, trusting {manifestation} to {effect} of their own accord.",
    "You align your posture with celestial ley lines, inviting {manifestation} to {effect} across your meridians.",
    "You murmur vows to an ancestral spirit; {manifestation} reply by {effect}.",
    "You grip a talisman etched with {affinity_noun_plural}, commanding {manifestation} to {effect}.",
    "You spin your sword slowly, its edge guiding {manifestation} that {effect} around the blade.",
    "You float inches above the ground as {manifestation} {effect}, sustaining the {affinity_title} aura.",
    "You tap acupoints in sequence, prompting {manifestation} to {effect} through every channel.",
    "You drink a draught of spirit dew; within, {manifestation} {effect} like captive dragons.",
    "You align your breath with distant thunder so {manifestation} {effect} in cadence.",
    "You circulate qi through bone and marrow, forging {affinity_noun_plural} while {manifestation} {effect}.",
    "You let your shadow stretch long, swallowing {affinity_noun_plural} as {manifestation} {effect}.",
    "You cradle your nascent core, feeding it {affinity_noun_plural} until {manifestation} {effect} outward.",
    "You compose silent mudras, each stroke bending {manifestation} to {effect}.",
    "You flip through a jade scripture; inked characters bloom into {manifestation} that {effect}.",
    "You attune to the sect's grand array so {manifestation} {effect} along the lattice.",
    "You step through slow martial forms, tracing arcs where {manifestation} {effect}.",
    "You scatter talismans that dissolve into {affinity_noun_plural}, guiding {manifestation} to {effect}.",
    "You open the palace of your mind, welcoming {manifestation} to {effect} without resistance.",
    "You soak in a spirit bath; rising vapours twist into {manifestation} that {effect}.",
    "You stretch awareness beneath your feet, coaxing {manifestation} to {effect} from the ley lines below.",
    "You polish a river pebble until {manifestation} {effect} within its grain.",
    "You carve runes into the air, each rune birthing {manifestation} that {effect}.",
    "You listen to the silence between breaths; in that stillness {manifestation} {effect}.",
    "You bind {affinity_noun_plural} around your wrists, letting {manifestation} {effect} along the threads.",
    "You place a crystal atop your head, channeling {manifestation} to {effect} through it.",
    "You watch moonlight fall upon {affinity_noun_plural}; reflected, {manifestation} {effect}.",
    "You stand amid swirling petals; each petal becomes {manifestation} that {effect}.",
    "You clasp a beast core; its aura releases {manifestation} that {effect}.",
    "You cast your senses deep underground, awakening {manifestation} to {effect}.",
    "You harmonise heartbeat and breath until {manifestation} {effect} in perfect rhythm.",
    "You open the gates of your dantian, inviting {affinity_noun_plural} while {manifestation} {effect} like a royal procession.",
]


def select_cultivation_flavour(
    affinity: SpiritualAffinity | None, *, mortal: bool = False
) -> str:
    if mortal:
        return random.choice(MORTAL_CULTIVATION_TEMPLATES)
    metadata = CULTIVATION_AFFINITY_METADATA.get(
        affinity, CULTIVATION_AFFINITY_METADATA[None]
    )
    imagery = CULTIVATION_AFFINITY_IMAGERY.get(affinity, DEFAULT_CULTIVATION_IMAGERY)
    manifestations = imagery.get("manifestations") or DEFAULT_CULTIVATION_IMAGERY["manifestations"]
    effects = imagery.get("effects") or DEFAULT_CULTIVATION_IMAGERY["effects"]
    template = random.choice(CULTIVATION_TEMPLATES)
    return template.format(
        manifestation=random.choice(manifestations),
        effect=random.choice(effects),
        affinity_title=metadata["title"],
        affinity_noun_singular=metadata["noun_singular"],
        affinity_noun_plural=metadata["noun_plural"],
    )

TRAINING_VARIATIONS: list[str] = [
    "You hammer a training post until splinters leap away from your fists.",
    "You sprint along the sect walls, leaving afterimages in your wake.",
    "You lift stone cauldrons overhead, tendons creaking with focused strength.",
    "You spar with shadow clones, matching their blows one by one.",
    "You plunge your arms into hot sand, tempering muscle and bone.",
    "You wrestle with a weighted chain, dragging it across the courtyard.",
    "You vault between boulders, honing footwork and balance.",
    "You practice spear thrusts against a waterfall, cleaving the torrent in two.",
    "You clash wooden swords until sparks form from sheer momentum.",
    "You brace beneath a falling log, turning impact into refined force.",
    "You endure icy winds on the peak, letting cold temper your frame.",
    "You stomp rhythmic patterns, shaking dust from the training grounds.",
    "You trade palm strikes with a senior puppet, matching its mechanical ferocity.",
    "You hang upside down from a tree branch, crunching to strengthen core qi.",
    "You run laps with ankle weights forged from spiritual iron.",
    "You channel qi into your knuckles, carving grooves into stone.",
    "You rotate your waist like a grinding millstone, testing resilience.",
    "You stretch atop bamboo poles, body bending like a willow.",
    "You practice kicks in waist-deep water, sending waves crashing ashore.",
    "You shadowbox under noon sun, sweat sizzling as it hits the ground.",
    "You roll across gravel, hardening skin against abrasive pain.",
    "You press your palms against a boulder, pushing until it trembles.",
    "You leap through a ring of blades, trusting instinct and agility.",
    "You exchange blows with a flame puppet, ignoring the heat around you.",
    "You squat with mountains of bricks balanced on your shoulders.",
    "You weave around spear traps, refining reflexes to razor sharpness.",
    "You slam your elbows into wooden pegs, splintering them one by one.",
    "You channel qi into your spine, each vertebra clicking into alignment.",
    "You sprint up a waterfall, legs pumping against relentless current.",
    "You brace in horse stance as spiritual lightning crawls across your skin.",
    "You whirl twin chains overhead, striking at imagined foes.",
    "You meditate mid-strike, turning each punch into a perfectly timed explosion.",
    "You drag a sled of ore, every step pounding qi into your legs.",
    "You grip heated iron bars, letting blisters forge a tougher grip.",
    "You dive through mist-laden hoops, training eyes and breath as one.",
    "You pound the ground with iron boots until shockwaves ripple outward.",
    "You somersault through a forest of spears, weaving without a nick.",
    "You balance jars of spirit wine on your shoulders, refusing to spill a drop.",
    "You clash with a roaring beast puppet, redirecting its savage momentum.",
    "You punch through suspended slabs of ice, shards chiming like bells.",
    "You run handstands along the training hall rafters, defying gravity.",
    "You strike a bronze bell with bare palms, letting resonance temper your bones.",
    "You leap rooftop to rooftop, harnessing qi to soften every landing.",
    "You spar blindfolded atop a narrow log, trusting senses beyond sight.",
    "You submerge in a freezing lake, forcing breath to steady beneath the surface.",
    "You wrestle a wind-forged rope, muscles burning as it thrashes.",
    "You hurl weighted talismans skyward, catching them before they fall.",
    "You clash gauntlets with a senior, exchanging thunderous blows.",
    "You run the sect's perilous cliff path, lungs burning with determination.",
    "You whirl a meteor hammer in tight arcs, letting centrifugal force temper your frame.",
]

SOUL_TEMPERING_VARIATIONS: list[str] = [
    "You float a lantern within your sea of consciousness, keeping its flame steady.",
    "You walk through a memory palace, polishing each thought until it gleams.",
    "You weave strands of soul light into a protective cocoon.",
    "You listen to echoing heartbeats, letting them wash away stray emotions.",
    "You confront a phantom fear, dissolving it with calm intent.",
    "You sculpt your spirit into a blade, sharpening resolve.",
    "You drift through dreamscape mountains, absorbing their silent wisdom.",
    "You trace sigils across your soul, anchoring clarity in every stroke.",
    "You sit within a mental storm, redirecting lightning with a gesture.",
    "You bathe your soul in warm starlight, knitting frayed edges.",
    "You disentangle threads of past karma, smoothing their knots.",
    "You invite a phantom teacher to spar, crossing wills without bodies.",
    "You ring invisible bells, letting each tone steady your mind.",
    "You scatter worries like petals, watching them fade into mist.",
    "You forge chains of intent, binding distractions beyond reach.",
    "You sip nectar of thought, sweet clarity infusing your spirit.",
    "You align your inner cosmos, stars settling into their rightful paths.",
    "You walk a bridge of moonlight, each step strengthening resolve.",
    "You breathe in dreams, exhaling fatigue from the deepest recesses.",
    "You polish a mirror of intent, reflecting only unwavering purpose.",
    "You seed your consciousness with lotus blooms, letting them anchor serenity.",
    "You duel a nightmare beast, conquering it with will alone.",
    "You let chants reverberate within, sweeping away lingering gloom.",
    "You stir embers of ambition, tempering them into steady glow.",
    "You commune with ancestral echoes, accepting their guidance.",
    "You cradle your nascent soul core, feeding it gentle warmth.",
    "You unravel tangled memories, weaving them into a coherent tapestry.",
    "You trace constellations behind closed eyes, mapping paths through eternity.",
    "You balance atop a thought-thread, neither wavering nor falling.",
    "You bottle stray sentiments, sealing them for later contemplation.",
    "You extend consciousness beyond your body, tasting distant breezes.",
    "You extinguish flickers of doubt, leaving only luminous calm.",
    "You negotiate with an inner demon, transforming hatred into steel.",
    "You meditate beside an illusory abyss, refusing to be drawn in.",
    "You gather motes of soul light, shaping them into a radiant lotus.",
    "You weigh future choices on mental scales, discarding reckless paths.",
    "You wander a forest of memories, pruning branches that no longer serve.",
    "You braid threads of empathy, strengthening bonds with unseen allies.",
    "You swallow a draught of dream dew, letting it clarify every thought.",
    "You carve mantras on your heart, each stroke resonating with courage.",
    "You trace a labyrinth of intention, arriving at stillness in the centre.",
    "You negotiate with past lives, gleaning lessons from their triumphs and failures.",
    "You guide a stream of moonfire through your mind, cauterising lingering doubts.",
    "You ignite a sun within your chest, its light banishing inner shadows.",
    "You orchestrate a silent symphony, harmonising every stray emotion.",
    "You spin prayer wheels of light, each revolution calming restless thoughts.",
    "You forge a pact with your dao heart, promising never to stray.",
    "You cultivate empathy toward foes, softening grudges into understanding.",
    "You weave a mantle of serenity, draping it over every anxious corner.",
    "You let cosmic rain fall within, each drop soothing the soul-sea.",
]


def _format_stat_value(value: float) -> str:
    decimal_value = Decimal(str(value))
    rounded = decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if rounded < 0 else ""
    rounded = abs(rounded)
    text = format(rounded, ".2f")
    integer_part, dot, fractional_part = text.partition(".")
    formatted_integer = format_number(int(integer_part or "0"))
    return f"{sign}{formatted_integer}{dot}{fractional_part}"


def _format_decimal(value: float) -> str:
    decimal_value = Decimal(str(value))
    rounded = decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if rounded < 0 else ""
    rounded = abs(rounded)
    text = format(rounded, ".2f")
    integer_part, dot, fractional_part = text.partition(".")
    formatted_integer = format_number(int(integer_part or "0"))
    return f"{sign}{formatted_integer}{dot}{fractional_part}"


def _format_percentage(ratio: float) -> str:
    return f"{_format_decimal(ratio * 100)}%"


def _format_percentage_precise(ratio: float, *, places: int = 3) -> str:
    multiplier = Decimal(str(ratio)) * Decimal("100")
    if places <= 0:
        rounded = multiplier.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    else:
        quantiser = Decimal("1." + ("0" * places))
        rounded = multiplier.quantize(quantiser, rounding=ROUND_HALF_UP)

    sign = "-" if rounded < 0 else ""
    rounded = abs(rounded)

    if places <= 0:
        integer_text = format_number(int(rounded))
        return f"{sign}{integer_text}%"

    formatted = format(rounded, f".{places}f")
    integer_part, dot, fractional_part = formatted.partition(".")
    integer_text = format_number(int(integer_part or "0"))
    fractional_part = fractional_part.rstrip("0")
    if fractional_part:
        return f"{sign}{integer_text}{dot}{fractional_part}%"
    return f"{sign}{integer_text}%"


def _format_signed(value: float) -> str:
    prefix = "+" if value >= 0 else "-"
    return f"{prefix}{_format_decimal(abs(value))}"


def _technique_effect_details(
    technique: CultivationTechnique,
    path: CultivationPath,
) -> tuple[str, list[str]]:
    """Summarise the additive/multiplicative effects of a cultivation technique."""

    addition, multiplier = technique.experience_adjustments(path)
    effect_parts: list[str] = []
    if abs(addition) > 1e-6:
        effect_parts.append(f"{_format_signed(addition)} EXP")
    if abs(multiplier) > 1e-6:
        effect_parts.append(f"{_format_signed(multiplier * 100)}%")
    effect_text = ", ".join(effect_parts) if effect_parts else "No bonus"

    stat_details: list[str] = []
    for stat_name, raw_value in technique.stats.items():
        if abs(raw_value) <= 1e-6:
            continue
        label = stat_name.replace("_", " ").title()
        if technique.stat_mode is TechniqueStatMode.ADDITION:
            value_text = f"{_format_signed(raw_value)} EXP"
        else:
            value_text = f"{_format_signed(raw_value * 100)}%"
        stat_details.append(f"{label} {value_text}")

    return effect_text, stat_details


PRIMARY_STAT_DISPLAY_ORDER: tuple[str, ...] = (
    "strength",
    "physique",
    "agility",
)

SECONDARY_STAT_DISPLAY_ORDER: tuple[str, ...] = (
    "attacks",
    "health_points",
    "defense",
    "dodges",
    "hit_points",
)

STAT_LABEL_OVERRIDES: Mapping[str, str] = {
    "strength": "Strength",
    "physique": "Physique",
    "agility": "Agility",
    "attacks": "Attack",
    "health_points": "Health Point",
    "defense": "Defense",
    "dodges": "Dodge",
    "hit_points": "Accuracy",
}


STAT_ANSI_COLOURS: Mapping[str, str] = {
    "strength": "\u001b[31m",
    "attacks": "\u001b[31m",
    "physique": "\u001b[32m",
    "health_points": "\u001b[32m",
    "defense": "\u001b[32m",
    "agility": "\u001b[34m",
    "dodges": "\u001b[34m",
    "hit_points": "\u001b[34m",
}


def format_stats_block(
    stats: Stats,
    *,
    emphasize: bool = False,
    max_value: int | None = None,
    min_value: int | None = None,
    include_descriptions: bool = False,
    breakdown: Mapping[str, str] | None = None,
    show_quality_labels: bool = False,
    descriptions: Mapping[str, str] | None = None,
    stat_names: Sequence[str] | None = None,
) -> str:
    names = stat_names or PRIMARY_STAT_DISPLAY_ORDER
    lines: list[str] = []
    for stat in names:
        label = STAT_LABEL_OVERRIDES.get(stat, stat.replace("_", " ").title())
        raw_value = getattr(stats, stat)
        value = _format_stat_value(raw_value)
        if (
            show_quality_labels
            and min_value is not None
            and max_value is not None
            and max_value > 0
        ):
            try:
                numeric_value = int(value)
            except ValueError:
                numeric_value = int(
                    Decimal(str(raw_value)).quantize(
                        Decimal("1"), rounding=ROUND_HALF_UP
                    )
                )
            ratio = numeric_value / max_value
            if ratio >= 0.75:
                quality = "genius"
            elif ratio < 0.5:
                quality = "trash"
            else:
                quality = "average"
            value = f"{value} ({quality})"
        elif min_value is not None and max_value is not None:
            value = f"{value} ({min_value}-{max_value})"
        elif max_value is not None:
            value = f"{value}/{max_value}"
        elif min_value is not None:
            value = f"{value} (min {min_value})"
        value_display = coloured(value, bold=emphasize)
        lines.append(f"**{label}:** {value_display}")
        if breakdown:
            details = breakdown.get(stat)
            if details:
                lines.append(f"    {coloured(details, dim=True)}")
        if include_descriptions and descriptions:
            description = descriptions.get(stat)
            if description:
                lines.append(f"    {coloured(description, dim=True)}")
    return "\n".join(lines)


def format_coloured_stats_block(
    stats: Stats,
    *,
    stat_names: Sequence[str],
) -> str:
    lines: list[str] = []
    for stat in stat_names:
        label = STAT_LABEL_OVERRIDES.get(stat, stat.replace("_", " ").title())
        value = _format_stat_value(getattr(stats, stat))
        colour_code = STAT_ANSI_COLOURS.get(stat)
        if colour_code:
            lines.append(f"{colour_code}{label}{ANSI_RESET}: {value}")
        else:
            lines.append(f"{label}: {value}")
    return "```ansi\n" + "\n".join(lines) + "\n```"


def _build_stat_gain_lines(
    before: Stats,
    after: Stats,
    *,
    stat_names: Sequence[str] = PRIMARY_STAT_DISPLAY_ORDER,
) -> list[str]:
    stat_lines: list[str] = []
    for stat in stat_names:
        previous_value = getattr(before, stat, 0.0)
        current_value = getattr(after, stat, 0.0)
        delta = current_value - previous_value
        if abs(delta) <= 1e-6:
            continue

        label = stat.replace("_", " ").title()
        previous_text = _format_stat_value(previous_value)
        current_text = _format_stat_value(current_value)
        delta_text = _format_signed(delta)
        colour = STAT_COLOURS.get(stat, "white")
        stat_lines.append(
            ansi_colour(
                f"{label}: {previous_text} → {current_text} ({delta_text})",
                colour,
            )
        )

    if not stat_lines:
        return []

    header = ansi_colour("STATS", "yellow", bold=True)
    return [ansi_block([header, *stat_lines])]


TALENT_STAT_EXPLANATIONS: dict[str, str] = {
    "strength": "Increases the attacks stat (4 attack points per strength).",
    "physique": "Improves health points (3 HP per physique) and defense (2 per point).",
    "agility": "Boosts dodge (4 per point) and hit points (8 per point).",
}


def build_stat_breakdown(
    player: PlayerProgress,
    qi_stage: CultivationStage,
    body_stage: CultivationStage | None,
    soul_stage: CultivationStage | None,
    race: Race | None,
    traits: list[SpecialTrait],
    equipped_items: list[Item],
    passive_bonus: Stats,
) -> dict[str, str]:
    eps = 1e-6
    base_values = {stat: getattr(player.stats, stat) for stat in PLAYER_STAT_NAMES}
    innate_values = {stat: getattr(player.innate_stats, stat) for stat in PLAYER_STAT_NAMES}
    stage_totals = {stat: 0.0 for stat in PLAYER_STAT_NAMES}
    stage_details: dict[str, list[str]] = defaultdict(list)

    for stage in (qi_stage, body_stage, soul_stage):
        if not stage:
            continue
        label = stage.combined_name
        bonuses = stage.stat_bonuses
        for stat in PLAYER_STAT_NAMES:
            value = getattr(bonuses, stat)
            if abs(value) <= eps:
                continue
            stage_totals[stat] += value
            stage_details[stat].append(f"{label} {_format_signed(value)}")

    race_multipliers: dict[str, tuple[str, float]] = {}
    if race:
        multipliers = race.stat_multipliers
        for stat in PLAYER_STAT_NAMES:
            value = getattr(multipliers, stat)
            if abs(value - 1.0) > eps:
                race_multipliers[stat] = (race.name, value)

    trait_multiplier_values: dict[str, float] = {}
    trait_multiplier_names: dict[str, list[str]] = defaultdict(list)
    for trait in traits:
        multipliers = trait.stat_multipliers
        for stat in PLAYER_STAT_NAMES:
            value = getattr(multipliers, stat)
            if abs(value - 1.0) <= eps:
                continue
            trait_multiplier_values[stat] = trait_multiplier_values.get(stat, 1.0) * value
            trait_multiplier_names[stat].append(trait.name)

    equipment_bonus = Stats()
    for item in equipped_items:
        equipment_bonus.add_in_place(item.stat_modifiers)

    stage_context: dict[str, tuple[CultivationStage | None, CultivationPath | None]] = {}
    for path, stats in STAGE_STAT_TARGETS.items():
        if path is CultivationPath.QI:
            stage_obj = qi_stage
        elif path is CultivationPath.BODY:
            stage_obj = body_stage
        else:
            stage_obj = soul_stage
        for stat in stats:
            stage_context[stat] = (stage_obj, path)

    path_labels = {
        CultivationPath.QI: "Cultivation Soul",
        CultivationPath.BODY: "Body Soul",
        CultivationPath.SOUL: "Soul Soul",
    }

    breakdown: dict[str, str] = {}
    for stat in PLAYER_STAT_NAMES:
        base_value = base_values[stat]
        stage_total = stage_totals[stat]
        parts: list[str] = []

        innate_value = innate_values[stat]
        stage_obj, path = stage_context.get(stat, (None, None))
        if stage_obj:
            stage_base = float(stage_obj.base_stat)
            if DEFAULT_STAGE_BASE_STAT:
                stage_base /= float(DEFAULT_STAGE_BASE_STAT)
            base_from_innate = innate_value * stage_base
            base_components = (
                f"{_format_decimal(innate_value)} × {_format_decimal(stage_base)}"
            )
        else:
            stage_base = DEFAULT_STAGE_BASE_STAT
            base_from_innate = innate_value
            base_components = _format_decimal(innate_value)
        display_label = path_labels.get(path, "Talent")
        base_text = f"Talent ({base_components})"
        if stage_obj:
            base_text = f"{base_text} [{stage_obj.name}]"
        if abs(base_from_innate - base_value) > eps:
            base_text = f"{base_text} → {_format_decimal(base_from_innate)}"
        else:
            base_text = f"{base_text} → {_format_decimal(base_value)}"
        base_text = f"{display_label}: {base_text}"
        if stage_details.get(stat):
            stage_text = " + ".join(stage_details[stat])
            base_text = f"{base_text}; Stage Bonuses: {stage_text}"
        elif abs(stage_total) > eps:
            base_text = f"{base_text}; Stage Bonuses: {_format_signed(stage_total)}"
        parts.append(base_text)

        multiplier_entries: list[tuple[str, float]] = []
        if stat in race_multipliers:
            race_name, value = race_multipliers[stat]
            multiplier_entries.append((f"Race ({race_name})", value))
        if stat in trait_multiplier_values:
            names = trait_multiplier_names.get(stat, [])
            if names:
                label = (
                    f"Traits ({', '.join(names)})"
                    if len(names) > 1
                    else f"Trait ({names[0]})"
                )
            else:
                label = "Traits"
            multiplier_entries.append((label, trait_multiplier_values[stat]))

        if multiplier_entries:
            multiplier_text = " · ".join(
                f"{label} ×{_format_decimal(value)}" for label, value in multiplier_entries
            )
            parts.append(f"Multipliers: {multiplier_text}")

        additions: list[str] = []
        gear_value = getattr(equipment_bonus, stat)
        if abs(gear_value) > eps:
            additions.append(f"Gear {_format_signed(gear_value)}")
        passive_value = getattr(passive_bonus, stat)
        if abs(passive_value) > eps:
            additions.append(f"Passives {_format_signed(passive_value)}")
        if additions:
            parts.append(f"Additions: {', '.join(additions)}")

        breakdown[stat] = "; ".join(parts)

    return breakdown


def build_skill_damage_breakdown(
    skill: Skill,
    base_label: str,
    base_value: float,
    proficiency: int,
    innate_soul_multiplier: float,
) -> str:
    step = skill.proficiency_max // 2 or 1
    if step <= 0:
        step = 1
    bonus_steps = min(proficiency // step, 2)
    proficiency_bonus = 0.1 * bonus_steps
    base_percent = _format_percentage(skill.damage_ratio)
    multiplier_terms = [f"{base_percent} base"]
    if bonus_steps:
        multiplier_terms.append(f"{_format_percentage(proficiency_bonus)} proficiency")
    else:
        multiplier_terms.append("0% proficiency")
    breakdown_text = (
        f"{base_label} {_format_stat_value(base_value)} × "
        f"({' + '.join(multiplier_terms)})"
    )
    total_multiplier = skill.damage_ratio + proficiency_bonus
    if abs(innate_soul_multiplier - 1.0) > 1e-6:
        breakdown_text += f" × Soul {_format_percentage(innate_soul_multiplier)}"
    estimated = base_value * total_multiplier * innate_soul_multiplier
    breakdown_text += f" ≈ {_format_stat_value(estimated)} base damage"
    breakdown_text += " (before variance/resistances)"
    return breakdown_text


COLOUR_CODES: dict[str, str] = {
    "red": "31",
    "crimson": "31",
    "rose": "91",
    "orange": "33",
    "amber": "33",
    "yellow": "33",
    "green": "32",
    "cyan": "36",
    "teal": "36",
    "blue": "34",
    "purple": "35",
    "magenta": "35",
    "violet": "95",
    "white": "37",
    "gray": "90",
    "black": "30",
}

SECTION_COLOURS: dict[str, str] = {
    "stage": "teal",
    "progress": "yellow",
    "talent": "purple",
    "innate_soul": "green",
    "titles": "purple",
    "traits": "purple",
    "stats": "blue",
    "race": "cyan",
    "location": "teal",
    "inventory": "green",
    "currency": "yellow",
    "combat": "red",
    "cultivation": "magenta",
    "soul": "purple",
    "martial_soul": "orange",
    "spirit_ring": "violet",
    "affinity": "cyan",
    "guide": "cyan",
    "warning": "yellow",
    "error": "red",
    "profile": "magenta",
    "welcome": "teal",
    "bond": "green",
}

STAT_COLOURS: dict[str, str] = {
    "strength": "orange",
    "physique": "crimson",
    "agility": "teal",
    "attacks": "yellow",
    "health_points": "red",
    "defense": "blue",
    "dodges": "green",
    "hit_points": "purple",
}

MALE_STAGE_EMOJI = "<:heaven_stage_m:1433134912126324909>"
FEMALE_STAGE_EMOJI = "<:heaven_stage_f:1433135683152773150>"


SECTION_EMOJIS: dict[str, str] = {
    "stage": MALE_STAGE_EMOJI,
    "progress": "📈",
    "talent": "<:dna:1433155212423467038>",
    "innate_soul": "<:heaven_spirit_base:1433148572181860514>",
    "titles": "🎖️",
    "traits": "✨",
    "stats": "<:stats:1433194251461595297>",
    "race": "<:dna:1433155212423467038>",
    "location": WANDER_TRAVEL_EMOJI_TEXT,
    "currency": CURRENCY_EMOJI_TEXT,
    "trade": "🤝",
    "combat": "⚔️",
    "cultivation": "<:heaven_cultivation:1433142350590378086>",
    "soul": "🧠",
    "guide": WANDER_TRAVEL_EMOJI_TEXT,
    "warning": "⚠️",
    "error": "❌",
    "profile": "🪪",
    "welcome": "🎉",
}


CURRENCY_ICON_ALIASES: dict[str, str] = {
    "spirit-stone": "💎",
    "spirit stone": "💎",
    "spiritstone": "💎",
    "tael": CURRENCY_EMOJI_TEXT,
}


def _stage_emoji_for_gender(gender: str | None) -> str:
    normalized = (gender or "").strip().lower()
    if normalized == "female":
        return FEMALE_STAGE_EMOJI
    return MALE_STAGE_EMOJI


def _section_emoji(
    section: str, *, player: PlayerProgress | None = None
) -> str | None:
    if section == "stage":
        return _stage_emoji_for_gender(getattr(player, "gender", None))
    return SECTION_EMOJIS.get(section)

INCORRECT_ICON = "❌"

AFFINITY_COLOURS: dict[SpiritualAffinity, str] = {
    SpiritualAffinity.FIRE: "red",
    SpiritualAffinity.WATER: "blue",
    SpiritualAffinity.WIND: "teal",
    SpiritualAffinity.EARTH: "yellow",
    SpiritualAffinity.METAL: "gray",
    SpiritualAffinity.ICE: "cyan",
    SpiritualAffinity.LIGHTNING: "yellow",
    SpiritualAffinity.LIGHT: "white",
    SpiritualAffinity.DARKNESS: "purple",
    SpiritualAffinity.LIFE: "green",
    SpiritualAffinity.DEATH: "red",
    SpiritualAffinity.SPACE: "purple",
    SpiritualAffinity.TIME: "orange",
    SpiritualAffinity.GRAVITY: "blue",
    SpiritualAffinity.SAMSARA: "magenta",
    SpiritualAffinity.POISON: "green",
    SpiritualAffinity.MUD: "amber",
    SpiritualAffinity.TEMPERATURE: "cyan",
    SpiritualAffinity.LAVA: "red",
    SpiritualAffinity.TWILIGHT: "violet",
    SpiritualAffinity.ENTROPY: "black",
    SpiritualAffinity.PERMAFROST: "powderblue",
    SpiritualAffinity.DUSTSTORM: "tan",
    SpiritualAffinity.PLASMA: "magenta",
    SpiritualAffinity.STEAM: "lavender",
    SpiritualAffinity.INFERNO: "crimson",
    SpiritualAffinity.FLASHFROST: "deepskyblue",
    SpiritualAffinity.FROSTFLOW: "lightcyan",
    SpiritualAffinity.BLIZZARD: "snow",
    SpiritualAffinity.TEMPEST: "navy",
    SpiritualAffinity.MIST: "silver",
}


def _ansi_affinity_name(
    affinity: SpiritualAffinity, *, bold: bool = True
) -> str:
    if affinity.is_mixed:
        return ansi_colour(affinity.display_name, "white", bold=bold)
    segments: list[str] = []
    for text, component in affinity.name_segments():
        colour_name = AFFINITY_COLOURS.get(component, "white")
        segments.append(ansi_colour(text, colour_name, bold=bold))
    return "".join(segments) or ansi_colour(affinity.display_name, "white", bold=bold)


DAMAGE_TYPE_COLOURS: dict[str, str] = {
    DamageType.PHYSICAL.value: "orange",
    DamageType.QI.value: "magenta",
    DamageType.SOUL.value: "purple",
    DamageType.TRUE.value: "yellow",
}

ITEM_TYPE_COLOURS: dict[str, str] = {
    "equipment": "teal",
    "material": "yellow",
    "quest": "purple",
}


ANSI_RESET = "\u001b[0m"
ANSI_DIM = "\u001b[2m"


def _wrap(text: str, prefix: str, suffix: str | None = None) -> str:
    suffix = suffix if suffix is not None else prefix
    if text.startswith(prefix) and text.endswith(suffix):
        return text
    return f"{prefix}{text}{suffix}"


def coloured(text: str, *, colour: str = "white", bold: bool = False, dim: bool = False) -> str:
    formatted = text
    if bold:
        formatted = _wrap(formatted, "**")
    if dim:
        formatted = _wrap(formatted, "*")
    return formatted


def ansi_colour(
    text: str,
    colour: str | None,
    *,
    bold: bool = False,
    dim: bool = False,
    italic: bool = False,
) -> str:
    prefixes: list[str] = []
    if bold:
        prefixes.append("1")
    if dim:
        prefixes.append("2")
    if italic:
        prefixes.append("3")
    if colour is not None:
        code = COLOUR_CODES.get(colour, "37")
        prefixes.append(code)
    if not prefixes:
        return text
    sequence = f"\u001b[{';'.join(prefixes)}m"
    return f"{sequence}{text}{ANSI_RESET}"


def coloured_block(lines: list[str]) -> str:
    cleaned = [line for line in lines if line]
    return "\n".join(cleaned)


def ansi_block(lines: Sequence[str]) -> str:
    cleaned = [line.rstrip() for line in lines if line]
    if not cleaned:
        cleaned = [f"{ANSI_DIM}No data available{ANSI_RESET}"]
    return "```ansi\n" + "\n".join(cleaned) + "\n```"


def _resource_value(value: float | int | None, maximum: float) -> float:
    if value is None:
        return maximum
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return maximum


def _status_block(
    player: PlayerProgress,
    *,
    effective_stats: Stats,
    active_path: CultivationPath,
    has_passive_bonus: bool,
) -> str:
    max_hp, max_soul = player.max_health_caps(effective_stats)
    current_hp = _resource_value(player.current_hp, max_hp)
    current_soul = _resource_value(player.current_soul_hp, max_soul)

    battle_lines = [
        ansi_coloured_pair(
            "Vitality",
            f"{_format_stat_value(current_hp)} / {_format_stat_value(max_hp)}",
            label_colour=SECTION_COLOURS["cultivation"],
            value_colour="white",
            bold_value=True,
        ),
        progress_bar(int(round(current_hp)), int(round(max_hp))),
    ]

    if active_path is CultivationPath.SOUL:
        battle_lines.extend(
            [
                ansi_coloured_pair(
                    "Soul Qi",
                    (
                        f"{_format_stat_value(current_soul)} / "
                        f"{_format_stat_value(max_soul)}"
                    ),
                    label_colour=SECTION_COLOURS["soul"],
                    value_colour="white",
                    bold_value=True,
                ),
                progress_bar(int(round(current_soul)), int(round(max_soul))),
            ]
        )

    if has_passive_bonus:
        battle_lines.append(
            ansi_colour(
                "Passive skills are currently empowering your stats.",
                SECTION_COLOURS["cultivation"],
                dim=True,
            )
        )

    return ansi_block(battle_lines)


def _martial_soul_block(
    player: PlayerProgress,
    *,
    mortal_stage: bool,
) -> str:
    soul_lines: list[str] = []
    if mortal_stage:
        soul_lines.append(
            ansi_colour(
                "Martial souls remain sealed until you reach Qi Condensation.",
                "gray",
                dim=True,
            )
        )
    else:
        martial_souls = list(player.martial_souls)
        active_names = {soul.name for soul in player.get_active_martial_souls()}
        if martial_souls:
            for soul in martial_souls:
                status = "Active" if soul.name in active_names else "Dormant"
                block_lines = _format_martial_soul_block(soul, status=status)
                if soul_lines:
                    soul_lines.append("")
                soul_lines.extend(block_lines)
        else:
            soul_lines.append(
                ansi_colour("No martial souls awakened yet.", "gray", dim=True)
            )

    return ansi_block(soul_lines)


def progress_bar(current: int, required: int, *, width: int = 18) -> str:
    if required <= 0:
        return ansi_colour("No experience recorded yet.", "gray", dim=True)

    ratio = max(0.0, current / float(required))
    clamped = min(1.0, ratio)
    filled = int(clamped * width)
    if filled == 0 and current > 0:
        filled = 1
    empty = max(0, width - filled)

    if ratio >= 1.0:
        fill_colour = "green"
    elif ratio >= 0.66:
        fill_colour = "yellow"
    else:
        fill_colour = "orange"

    bar_segments: list[str] = []
    if filled:
        bar_segments.append(ansi_colour("█" * filled, fill_colour, bold=True))
    if empty:
        bar_segments.append(ansi_colour("░" * empty, "gray", dim=True))

    percent_value = min(999.9, max(0.0, ratio * 100.0))
    percent_text = f"{percent_value:5.1f}%"
    bar_segments.append(
        ansi_colour(
            percent_text,
            SECTION_COLOURS["progress"],
            bold=True,
        )
    )
    return " ".join(bar_segments)


def _player_in_mortal_realm(
    player: PlayerProgress, *, stage: CultivationStage | None = None
) -> bool:
    """Determine whether the player should be treated as mortal for display."""

    stage_value = getattr(player, "cultivation_stage", None)
    if isinstance(stage_value, CultivationStage):
        return stage_value.is_mortal

    normalized = str(stage_value or "").strip().lower()
    if normalized:
        token = normalized.replace("_", "-")
        if token == MORTAL_REALM_KEY:
            return True
        if token.startswith(MORTAL_REALM_KEY):
            return True
        return False

    if stage is not None:
        return stage.is_mortal

    return False


def talent_field_entries(
    player: PlayerProgress,
    *,
    _innate_min: int | None,
    innate_max: int | None,
    effective_base: InnateSoulSet | None = None,
    qi_stage: CultivationStage | None = None,
) -> list[tuple[str, str, bool]]:
    """Build embed field entries describing a player's innate talents."""

    quality_colours = {"genius": "green", "average": "yellow", "trash": "red"}

    player_is_mortal = _player_in_mortal_realm(player, stage=qi_stage)
    mask_innate = player_is_mortal

    fields: list[tuple[str, str, bool]] = []

    if mask_innate:
        fields.append(
            (
                _label_with_icon("talent", "Talent"),
                ansi_block(["Talent remains sealed until you reach Qi Condensation."]),
                False,
            )
        )
    else:
        talent_entries: list[list[str]] = []
        for stat_key, _description in TALENT_STAT_EXPLANATIONS.items():
            label = stat_key.replace("_", " ").title()
            raw_value = getattr(player.innate_stats, stat_key)
            display_value = 1 if mask_innate else raw_value
            value_display = _format_stat_value(display_value)
            quality: str | None = None

            if innate_max is not None and innate_max > 0:
                try:
                    numeric_value = int(value_display)
                except ValueError:
                    numeric_value = int(
                        Decimal(str(display_value)).quantize(
                            Decimal("1"), rounding=ROUND_HALF_UP
                        )
                    )
                ratio = numeric_value / innate_max
                if ratio >= 0.75:
                    quality = "genius"
                elif ratio < 0.5:
                    quality = "trash"
                else:
                    quality = "average"

            value_line = ansi_coloured_pair(
                label,
                value_display,
                label_colour="cyan",
                value_colour="white",
                bold_value=True,
            )
            extras: list[str] = []
            if quality:
                colour_key = quality_colours.get(quality, "yellow")
                extras.append(ansi_colour(quality.title(), colour_key, bold=True))
            if extras:
                value_line = f"{value_line} {' '.join(extras)}"
            talent_entries.append([value_line])

        column_count = 2
        columns: list[list[str]] = [[] for _ in range(column_count)]
        for index, entry in enumerate(talent_entries):
            column = columns[index % column_count]
            column.extend(entry)
            column.append(" ")

        for index, column_lines in enumerate(columns):
            cleaned = [line for line in column_lines if line]
            if not cleaned:
                continue
            field_name = _label_with_icon("talent", "Talent") if index == 0 else "\u200b"
            fields.append((field_name, ansi_block(cleaned), True))

    martial_lines: list[str] = []
    if mask_innate:
        martial_lines.append(
            ansi_colour(
                "Martial souls remain sealed until you reach Qi Condensation.",
                "gray",
                dim=True,
            )
        )
    else:
        martial_souls = list(player.martial_souls)
        active_names = {soul.name for soul in player.get_active_martial_souls()}
        if martial_souls:
            for soul in martial_souls:
                status = "Active" if soul.name in active_names else "Dormant"
                block_lines = _format_martial_soul_block(soul, status=status)
                if martial_lines:
                    martial_lines.append("")
                martial_lines.extend(block_lines)
        else:
            martial_lines.append(
                ansi_colour("No martial souls awakened yet.", "gray", dim=True)
            )
    fields.append(
        (
            _label_with_icon("martial_soul", "Martial Souls"),
            ansi_block(martial_lines),
            False,
        )
    )

    if effective_base and effective_base.bonus_affinities:
        bonus_lines = [
            ansi_colour(
                "Traits grant additional affinities:",
                SECTION_COLOURS["traits"],
                bold=True,
            )
        ]
        bonus_lines.extend(
            ansi_colour(
                "• "
                + affinity_description(
                    affinity,
                    include_relationships=True,
                    relationship_style="stacked",
                ),
                "magenta",
                bold=True,
            )
            for affinity in effective_base.bonus_affinities
        )
        fields.append(
            (
                _label_with_icon("traits", "Trait Resonance"),
                ansi_block(bonus_lines),
                True,
            )
        )

    return fields


def coloured_pair(
    label: str,
    value: str,
    *,
    label_colour: str = "teal",
    value_colour: str = "white",
    bold_value: bool = False,
    dim_value: bool = False,
) -> str:
    return (
        f"{coloured(label, colour=label_colour, bold=True)}: "
        f"{coloured(value, colour=value_colour, bold=bold_value, dim=dim_value)}"
    )


def ansi_coloured_pair(
    label: str,
    value: str,
    *,
    label_colour: str = "teal",
    value_colour: str = "white",
    bold_value: bool = False,
    dim_value: bool = False,
) -> str:
    label_text = ansi_colour(label, label_colour, bold=True)
    value_text = ansi_colour(value, value_colour, bold=bold_value, dim=dim_value)
    return f"{label_text}: {value_text}"


def profile_pair(
    label: str,
    value: str,
    *,
    label_key: str = "profile",
    bold_value: bool = False,
    dim_value: bool = False,
    bullet: bool = True,
) -> str:
    prefix = "• " if bullet else ""
    label_colour = SECTION_COLOURS.get(label_key, "teal")
    label_text = coloured(label, colour=label_colour, bold=True)
    value_text = coloured(value, colour="white", bold=bold_value, dim=dim_value)
    return f"{prefix}{label_text}: {value_text}"


def breakthrough_ready_indicator(current: int, required: int) -> str:
    if required <= 0:
        return ""
    if current >= 2 * required:
        return "Overprepared ! breakthrough chance doubled !"
    return ""


def section_token(
    key: str, text: str | None = None, *, bold: bool = True, dim: bool = False
) -> str:
    label = text or key.replace("_", " ").title()
    return coloured(label, bold=bold, dim=dim)


def stat_token(stat: str) -> str:
    return coloured(stat.replace("_", " ").title(), bold=True)


def affinity_relationship_details(
    affinity: SpiritualAffinity, *, relationship_style: Literal["inline", "stacked"] = "inline"
) -> str:
    return ""


def affinity_description(
    affinity: SpiritualAffinity,
    *,
    include_relationships: bool = False,
    relationship_style: Literal["inline", "stacked"] = "inline",
) -> str:
    label = affinity.display_name
    if affinity.is_mixed:
        components = " + ".join(component.display_name for component in affinity.components)
        label = f"{label} ({components})"
    if include_relationships:
        details = affinity_relationship_details(
            affinity, relationship_style=relationship_style
        )
        if details:
            if relationship_style == "stacked":
                label = f"{label}\n{details}"
            else:
                label = f"{label} — {details}"
    return label


def affinity_token(
    affinity: SpiritualAffinity | None,
    *,
    text: str | None = None,
    include_relationships: bool = False,
    relationship_style: Literal["inline", "stacked"] = "inline",
) -> str:
    if affinity is None:
        return coloured("Unaligned", dim=True)
    if text is None:
        label = affinity_description(
            affinity,
            include_relationships=include_relationships,
            relationship_style=relationship_style,
        )
    else:
        label = text
        if include_relationships:
            details = affinity_relationship_details(
                affinity, relationship_style=relationship_style
            )
            if details:
                if relationship_style == "stacked":
                    label = f"{label}\n{details}"
                else:
                    label = f"{label} — {details}"
    return coloured(label, bold=True)


def damage_token(damage_type: DamageType | str) -> str:
    value = damage_type.value if isinstance(damage_type, DamageType) else str(damage_type)
    return coloured(value.title(), bold=True)


def item_type_token(item_type: str) -> str:
    key = item_type.lower()
    label = key.title()
    return coloured(label, bold=True)


SECTION_ICONS: dict[str, str] = {key: section_token(key) for key in SECTION_COLOURS}
STAT_ICONS: dict[str, str] = {
    **{stat: stat_token(stat) for stat in PLAYER_STAT_NAMES},
    "attacks": stat_token("attacks"),
    "health_points": stat_token("health_points"),
    "defense": stat_token("defense"),
    "dodges": stat_token("dodges"),
    "hit_points": stat_token("hit_points"),
}
AFFINITY_ICONS: dict[SpiritualAffinity, str] = {
    affinity: affinity_token(affinity) for affinity in SpiritualAffinity
}
DAMAGE_TYPE_ICONS: dict[str, str] = {
    key: damage_token(key) for key in DAMAGE_TYPE_COLOURS.keys()
}
ITEM_TYPE_ICONS: dict[str, str] = {
    key: item_type_token(key) for key in ITEM_TYPE_COLOURS.keys()
}

EQUIPMENT_SLOT_LABELS: dict[EquipmentSlot, str] = {
    EquipmentSlot.NECKLACE: "Necklace",
    EquipmentSlot.ARMOR: "Armor",
    EquipmentSlot.BELT: "Belt",
    EquipmentSlot.BOOTS: "Boots",
    EquipmentSlot.ACCESSORY: "Accessories",
    EquipmentSlot.WEAPON: "Weapon",
}

WEAPON_TYPE_LABELS: dict[WeaponType, str] = {
    WeaponType.BARE_HAND: "Bare-Handed",
    WeaponType.SWORD: "Sword",
    WeaponType.SPEAR: "Spear",
    WeaponType.BOW: "Bow",
    WeaponType.INSTRUMENT: "Instrument",
    WeaponType.BRUSH: "Brush",
    WeaponType.WHIP: "Whip",
    WeaponType.FAN: "Fan",
}


def _build_equipment_view_cache(
    player: PlayerProgress,
    items: Mapping[str, Item],
) -> tuple[
    dict[EquipmentSlot, dict[str, Any]],
    dict[EquipmentSlot, list[dict[str, Any]]],
    dict[EquipmentSlot, dict[str, tuple[Item | None, int]]],
]:
    slot_cache: dict[EquipmentSlot, dict[str, Any]] = {}
    inventory_candidates: dict[EquipmentSlot, list[dict[str, Any]]] = {
        slot: [] for slot in EQUIPMENT_SLOT_ORDER
    }
    inventory_lookup: dict[EquipmentSlot, dict[str, tuple[Item | None, int]]] = {
        slot: {} for slot in EQUIPMENT_SLOT_ORDER
    }

    for slot in EQUIPMENT_SLOT_ORDER:
        capacity = equipment_slot_capacity(slot)
        usage = equipment_slot_usage(player, slot, items)
        usage_text = f"{format_number(usage)}/{format_number(capacity)}"
        free_text = format_number(max(0, capacity - usage))
        option_description = (
            f"{usage_text} slots used" if capacity else "No slot limit"
        )
        keys = list(player.equipment.get(slot.value, []))
        detail_items: list[dict[str, Any]] = []
        equipped_entries: list[dict[str, Any]] = []
        overview_names: list[str] = []

        for index, key in enumerate(keys):
            item = items.get(key)
            base_label = item.name if item else key
            slots_required = getattr(item, "slots_required", 1) or 1
            slots_text = format_number(slots_required)
            detail_label = base_label
            if (
                slot is EquipmentSlot.WEAPON
                and item is not None
                and slots_required > 1
            ):
                detail_label = f"{base_label} ({slots_text} slots)"

            detail_items.append({"key": key, "item": item, "label": detail_label})

            entry_details: list[str] = []
            if slots_required > 1:
                entry_details.append(f"Uses {slots_text} slots")
            if item and item.weapon_type:
                weapon_label = WEAPON_TYPE_LABELS.get(
                    item.weapon_type, item.weapon_type.value.title()
                )
                entry_details.append(weapon_label)

            equipped_entries.append(
                {
                    "key": key,
                    "item": item,
                    "label": base_label,
                    "details": entry_details,
                    "details_text": " • ".join(entry_details) or None,
                    "value": f"{key}::{index}",
                }
            )
            overview_names.append(detail_label)

        if not overview_names:
            if slot is EquipmentSlot.WEAPON:
                overview_names = [coloured("Bare-handed", dim=True)]
            else:
                overview_names = [coloured("Empty", dim=True)]

        if len(overview_names) > 5:
            display_names = overview_names[:5] + ["…"]
        else:
            display_names = overview_names

        label = EQUIPMENT_SLOT_LABELS.get(slot, slot.value.title())
        overview_line = f"{label} [{usage_text}]: {', '.join(display_names)}"

        slot_cache[slot] = {
            "capacity": capacity,
            "usage": usage,
            "usage_text": usage_text,
            "free_text": free_text,
            "option_description": option_description,
            "label": label,
            "detail_items": detail_items,
            "equipped_entries": equipped_entries,
            "overview_line": overview_line,
        }

    for key, amount in sorted(player.inventory.items()):
        if amount <= 0:
            continue
        item = items.get(key)
        if not item or item.item_type != "equipment":
            continue

        slot_value: EquipmentSlot | str | None = item.equipment_slot or EquipmentSlot.ACCESSORY
        if not isinstance(slot_value, EquipmentSlot):
            try:
                slot_value = EquipmentSlot.from_value(
                    slot_value, default=EquipmentSlot.ACCESSORY
                )
            except ValueError:
                slot_value = EquipmentSlot.ACCESSORY

        info_bits: list[str] = [f"Owned ×{format_number(amount)}"]
        slots_required = getattr(item, "slots_required", 1) or 1
        slots_text = format_number(slots_required)
        if slots_required > 1:
            info_bits.append(f"Cost {slots_text} slots")
        if item.weapon_type:
            weapon_label = WEAPON_TYPE_LABELS.get(
                item.weapon_type, item.weapon_type.value.title()
            )
            info_bits.append(weapon_label)
        inventory_bonus = getattr(item, "inventory_space_bonus", 0)
        if inventory_bonus:
            info_bits.append(
                f"+{format_number(inventory_bonus)} inventory"
            )

        description_text = " • ".join(info_bits)
        summary_text = f"{item.name}: {', '.join(info_bits)}"

        entry = {
            "key": key,
            "item": item,
            "amount": amount,
            "details": info_bits,
            "details_text": description_text,
            "summary_text": summary_text,
            "option_label": item.name[:100],
            "option_description": description_text,
        }

        inventory_candidates.setdefault(slot_value, []).append(entry)
        slot_lookup = inventory_lookup.setdefault(slot_value, {})
        slot_lookup[key] = (item, amount)

    return slot_cache, inventory_candidates, inventory_lookup


def _label_with_icon(
    section: str,
    label: str,
    *,
    player: PlayerProgress | None = None,
    include_icon: bool = False,
) -> str:
    text = label.upper()
    if include_icon:
        emoji = _section_emoji(section, player=player)
        if emoji:
            text = f"{emoji} {text}"
    return f"__**{text}**__"


def _format_weapon_focus(weapons: Sequence[WeaponType]) -> str | None:
    labels = [
        weapon.value.replace("-", " ").title()
        for weapon in weapons
        if isinstance(weapon, WeaponType)
    ]
    if not labels:
        return None
    return ", ".join(labels)


def _format_martial_soul_block(
    soul: MartialSoul,
    *,
    status: str,
    rings: Sequence[Any] | None = None,
) -> list[str]:
    try:
        star_count = int(soul.grade)
    except (TypeError, ValueError):
        star_count = 1
    stars = "⭐" * max(1, star_count)
    grade_label = ansi_colour(
        f"{stars}({format_number(soul.grade)})",
        SECTION_COLOURS["martial_soul"],
        bold=True,
    )

    cleaned_status = status.strip()
    status_segment = ""
    if cleaned_status and cleaned_status.lower() not in {"active", "dormant"}:
        status_segment = ansi_colour(f"{cleaned_status} ", "white", bold=True)

    name_segment = ansi_colour(soul.name, "white", bold=True)
    name_line = f"{status_segment}{name_segment}" if status_segment else name_segment

    category_label = soul.category.value.title()
    type_line = f"Type: {category_label}"

    lines = [name_line, grade_label, type_line]

    affinity_segments: list[str] = []
    for affinity in soul.affinities:
        if not isinstance(affinity, SpiritualAffinity):
            continue
        if affinity.is_mixed:
            main_label = ansi_colour(affinity.display_name, "white")
            component_names = " + ".join(
                _ansi_affinity_name(component, bold=False)
                for component in affinity.components
            )
            display_name = (
                f"{main_label} ({component_names})"
                if component_names
                else main_label
            )
        else:
            display_name = _ansi_affinity_name(affinity)
        affinity_segments.append(display_name)

    if affinity_segments:
        affinity_label = ansi_colour("Affinity:", "white")
        lines.append(affinity_label)
        lines.extend(affinity_segments)

    detail_bits: list[str] = []
    if rings:
        detail_bits.append(
            f"{len(rings)} ring{'s' if len(rings) != 1 else ''} bound"
        )

    if detail_bits:
        lines.extend(f"  {bit}" for bit in detail_bits)

    summary = (soul.description or "").strip()
    if summary:
        if lines and lines[-1].strip():
            lines.append(" ")
        lines.append(
            ansi_colour(
                summary,
                "gray",
                italic=True,
            )
        )

    return lines


def _currency_icon_for_display(
    key: str,
    currency: Currency | None,
) -> str | None:
    candidates = {
        key.lower(),
        key.replace("-", " ").lower(),
        key.replace("_", " ").lower(),
        key.replace("-", "").replace("_", "").lower(),
    }
    if currency:
        name = currency.name.strip().lower()
        if name:
            candidates.update(
                {
                    name,
                    name.replace("-", " "),
                    name.replace("-", "").replace(" ", ""),
                }
            )
    for candidate in candidates:
        icon = CURRENCY_ICON_ALIASES.get(candidate)
        if icon:
            return icon
    return None


def _make_embed(
    section: str,
    title: str,
    description: str,
    colour: discord.Colour,
    *,
    include_icon: bool = False,
) -> discord.Embed:
    embed = discord.Embed(
        title=_label_with_icon(section, title, include_icon=include_icon),
        colour=colour,
    )
    cleaned = description.strip("\n")
    if cleaned:
        embed.description = cleaned
    return embed


COMMON_AFFINITIES = [
    SpiritualAffinity.FIRE,
    SpiritualAffinity.WATER,
    SpiritualAffinity.WIND,
    SpiritualAffinity.EARTH,
]
UNCOMMON_AFFINITIES = [
    SpiritualAffinity.METAL,
    SpiritualAffinity.ICE,
    SpiritualAffinity.LIGHTNING,
]
RARE_AFFINITIES = [SpiritualAffinity.LIGHT, SpiritualAffinity.DARKNESS]
EXTREMELY_RARE_AFFINITIES = [
    SpiritualAffinity.LIFE,
    SpiritualAffinity.DEATH,
    SpiritualAffinity.SPACE,
    SpiritualAffinity.TIME,
]
MIXED_AFFINITIES = [
    SpiritualAffinity.POISON,
    SpiritualAffinity.MUD,
    SpiritualAffinity.TEMPERATURE,
    SpiritualAffinity.LAVA,
    SpiritualAffinity.TWILIGHT,
    SpiritualAffinity.GRAVITY,
    SpiritualAffinity.SAMSARA,
    SpiritualAffinity.PERMAFROST,
    SpiritualAffinity.DUSTSTORM,
    SpiritualAffinity.PLASMA,
    SpiritualAffinity.STEAM,
    SpiritualAffinity.INFERNO,
    SpiritualAffinity.FLASHFROST,
    SpiritualAffinity.FROSTFLOW,
    SpiritualAffinity.BLIZZARD,
    SpiritualAffinity.TEMPEST,
    SpiritualAffinity.MIST,
]
UNFATHOMABLY_RARE_AFFINITIES = [SpiritualAffinity.ENTROPY]

AFFINITY_WEIGHT_TABLE: list[tuple[list[SpiritualAffinity], int]] = [
    (COMMON_AFFINITIES, 60),
    (UNCOMMON_AFFINITIES, 25),
    (RARE_AFFINITIES, 10),
    (EXTREMELY_RARE_AFFINITIES, 5),
    (MIXED_AFFINITIES, 3),
    (UNFATHOMABLY_RARE_AFFINITIES, 1),
]

T = TypeVar("T")


def _default_affinity_weights() -> dict[SpiritualAffinity, float]:
    weights: dict[SpiritualAffinity, float] = {}
    for pool, weight in AFFINITY_WEIGHT_TABLE:
        if not pool or weight <= 0:
            continue
        share = weight / len(pool)
        for affinity in pool:
            weights[affinity] = share
    return weights


def _default_innate_soul_grade_weights() -> dict[int, float]:
    return {
        1: 1.0,
        2: 2.0,
        3: 3.0,
        4: 4.0,
        5: 4.0,
        6: 2.0,
        7: 2.0,
        8: 2.0,
        9: 1.0,
    }


def _default_innate_soul_count_weights() -> dict[int, float]:
    return {1: 1.0, 2: 0.05}


def _weighted_choice(options: Mapping[T, float]) -> T:
    items = [(key, max(0.0, float(weight))) for key, weight in options.items()]
    if not items:
        raise ValueError("Weighted choice requires at least one option")
    positive = [(key, weight) for key, weight in items if weight > 0]
    pool = positive or [(key, 1.0) for key, _ in items]
    total = sum(weight for _, weight in pool)
    roll = random.random() * total
    cumulative = 0.0
    for key, weight in pool:
        cumulative += weight
        if roll <= cumulative:
            return key
    return pool[-1][0]

INNATE_SOUL_TITLES = {
    1: "Dormant",
    2: "Flickering",
    3: "Unsteady",
    4: "Steady",
    5: "Resonant",
    6: "Vibrant",
    7: "Mythic",
    8: "Transcendent",
    9: "Celestial",
}


def roll_martial_souls(
    *,
    grade_weights: Mapping[int, float] | None = None,
    count_weights: Mapping[int, float] | None = None,
    category: MartialSoulType | str | None = "any",
    rng: random.Random | None = None,
) -> list[MartialSoul]:
    """Generate martial soul presets directly without rolling affinities."""

    grade_pool = dict(_default_innate_soul_grade_weights())
    if grade_weights:
        for grade, weight in grade_weights.items():
            try:
                normalized = int(grade)
            except (TypeError, ValueError):
                continue
            if normalized < 1 or normalized > 9:
                continue
            try:
                cast_weight = float(weight)
            except (TypeError, ValueError):
                continue
            grade_pool[normalized] = cast_weight

    count_pool = dict(_default_innate_soul_count_weights())
    if count_weights:
        for amount, weight in count_weights.items():
            try:
                total = int(amount)
            except (TypeError, ValueError):
                continue
            if total < 1:
                continue
            try:
                cast_weight = float(weight)
            except (TypeError, ValueError):
                continue
            if cast_weight < 0:
                continue
            count_pool[total] = cast_weight

    soul_count = max(1, int(_weighted_choice(count_pool)))
    souls: list[MartialSoul] = []
    for _ in range(soul_count):
        grade = int(_weighted_choice(grade_pool))
        soul = MartialSoul.default(
            category="any" if category is None else category,
            grade=grade,
            rng=rng,
        )
        souls.append(soul)

    return souls


def roll_innate_souls(
    affinity_weights: Mapping[SpiritualAffinity, float] | None = None,
    grade_weights: Mapping[int, float] | None = None,
    count_weights: Mapping[int, float] | None = None,
) -> list[InnateSoul]:
    affinity_pool = dict(_default_affinity_weights())
    if affinity_weights:
        for affinity, weight in affinity_weights.items():
            if isinstance(affinity, SpiritualAffinity):
                key = affinity
            else:
                try:
                    key = SpiritualAffinity(str(affinity))
                except ValueError:
                    continue
            affinity_pool[key] = float(weight)
    grade_pool = dict(_default_innate_soul_grade_weights())
    if grade_weights:
        for grade, weight in grade_weights.items():
            try:
                key = int(grade)
            except (TypeError, ValueError):
                continue
            if key < 1 or key > 9:
                continue
            grade_pool[key] = float(weight)
    count_pool = dict(_default_innate_soul_count_weights())
    if count_weights:
        for amount, weight in count_weights.items():
            try:
                total = int(amount)
            except (TypeError, ValueError):
                continue
            if total < 1:
                continue
            try:
                cast_weight = float(weight)
            except (TypeError, ValueError):
                continue
            if cast_weight < 0:
                continue
            count_pool[total] = cast_weight

    count = max(1, int(_weighted_choice(count_pool)))
    souls: list[InnateSoul] = []
    for _ in range(count):
        grade = int(_weighted_choice(grade_pool))
        affinity = _weighted_choice(affinity_pool)
        title = INNATE_SOUL_TITLES.get(grade, "Mysterious")
        name = f"{title} {affinity.display_name} Soul"
        souls.append(InnateSoul(name=name, grade=grade, affinities=(affinity,)))
    return souls


def roll_innate_soul(
    affinity_weights: Mapping[SpiritualAffinity, float] | None = None,
    grade_weights: Mapping[int, float] | None = None,
    count_weights: Mapping[int, float] | None = None,
) -> InnateSoul:
    return roll_innate_souls(
        affinity_weights=affinity_weights,
        grade_weights=grade_weights,
        count_weights=count_weights,
    )[0]


def _colour_affinity_tokens(
    text: str,
    affinities: Sequence[SpiritualAffinity],
    *,
    use_ansi: bool,
) -> str:
    if not text:
        return text

    if not use_ansi:
        return coloured(text, bold=True)

    affinity_list = [aff for aff in affinities if isinstance(aff, SpiritualAffinity)]
    if not affinity_list:
        return ansi_colour(text, "white", bold=True)

    replacements: dict[str, str] = {}
    for affinity in affinity_list:
        name = affinity.display_name
        replacements[name] = _ansi_affinity_name(affinity)

    # Sort by length so longer affinity names take precedence.
    sorted_names = sorted(replacements.keys(), key=len, reverse=True)
    pattern = "|".join(re.escape(name) for name in sorted_names)
    if not pattern:
        return ansi_colour(text, "white", bold=True)
    regex = re.compile(pattern)

    segments: list[str] = []
    last_index = 0
    for match in regex.finditer(text):
        start, end = match.span()
        if start > last_index:
            plain_text = text[last_index:start]
            segments.append(ansi_colour(plain_text, "white", bold=True))
        matched = match.group(0)
        coloured = replacements.get(matched)
        if coloured is None:
            coloured = ansi_colour(matched, "white", bold=True)
        segments.append(coloured)
        last_index = end

    if last_index < len(text):
        remaining = text[last_index:]
        segments.append(ansi_colour(remaining, "white", bold=True))

    return "".join(segments)


def _innate_soul_signature(
    base: InnateSoul | InnateSoulSet | Sequence[InnateSoul] | None,
) -> tuple[tuple[tuple[str, int, tuple[str, ...]], ...], tuple[str, ...]] | None:
    if base is None:
        return None

    bases: list[InnateSoul] = []
    bonus_source: Sequence[SpiritualAffinity] = ()
    if isinstance(base, InnateSoulSet):
        bases.extend(base.bases)
        bonus_source = base.bonus_affinities
    elif isinstance(base, InnateSoul):
        bases.append(base)
    else:
        try:
            for entry in base:
                if isinstance(entry, InnateSoul):
                    bases.append(entry)
        except TypeError:
            return None

    base_signature: list[tuple[str, int, tuple[str, ...]]] = []
    for entry in bases:
        name = str(getattr(entry, "name", ""))
        try:
            grade_value = int(getattr(entry, "grade", 0))
        except (TypeError, ValueError):
            grade_value = 0
        affinity_names: list[str] = []
        for affinity in getattr(entry, "affinities", ()):  # type: ignore[attr-defined]
            if isinstance(affinity, SpiritualAffinity):
                affinity_names.append(affinity.name)
        base_signature.append((name, grade_value, tuple(affinity_names)))

    bonus_names = tuple(
        affinity.name for affinity in bonus_source if isinstance(affinity, SpiritualAffinity)
    )

    if not base_signature and not bonus_names:
        return None

    return tuple(base_signature), bonus_names


def _affinity_from_name(name: str) -> SpiritualAffinity | None:
    if not name:
        return None
    try:
        return SpiritualAffinity[name]
    except KeyError:
        try:
            return SpiritualAffinity(name)
        except ValueError:
            return None


def _render_innate_soul_inner(
    signature: tuple[tuple[tuple[str, int, tuple[str, ...]], ...], tuple[str, ...]],
    use_ansi: bool,
    gain_resolver: Callable[[int], tuple[int, int] | None] | None,
) -> str:
    base_entries, bonus_names = signature

    entry_data: list[dict[str, Any]] = []
    for name, grade_value, affinity_names in base_entries:
        star_count = grade_value if grade_value > 0 else 1
        display_value = grade_value if grade_value > 0 else 0
        stars = f"{'⭐' * star_count}({display_value})"
        affinities: list[SpiritualAffinity] = []
        component_notes: list[str] = []
        for affinity_name in affinity_names:
            affinity = _affinity_from_name(affinity_name)
            if affinity is None:
                continue
            affinities.append(affinity)
            if affinity.is_mixed:
                components = " + ".join(
                    component.display_name for component in affinity.components
                )
                component_notes.append(
                    f"{affinity.display_name} channels {components} at 12.5% each (25% total)."
                )
        entry_data.append(
            {
                "stars": stars,
                "name": name,
                "grade": grade_value,
                "affinities": affinities,
                "component_notes": component_notes,
            }
        )

    bonus_affinities = [
        affinity
        for name in bonus_names
        if (affinity := _affinity_from_name(name)) is not None
    ]

    if use_ansi:
        lines: list[str] = []
        for data in entry_data:
            lines.append(ansi_colour(data["stars"], "yellow", bold=True))
            lines.append(
                _colour_affinity_tokens(str(data["name"]), data["affinities"], use_ansi=True)
            )
            if gain_resolver is not None:
                gain_range = gain_resolver(max(1, int(data["grade"] or 0)))
                if gain_range:
                    minimum, maximum = gain_range
                    if maximum > 0:
                        if minimum == maximum:
                            gain_text = (
                                f"Yields {format_number(minimum)} Qi per cultivation."
                            )
                        else:
                            gain_text = (
                                "Yields "
                                f"{format_number(minimum)}–{format_number(maximum)} Qi per cultivation."
                            )
                        lines.append(
                            ansi_colour(gain_text, SECTION_COLOURS["cultivation"], bold=True)
                        )
            for note in data["component_notes"]:
                lines.append(ansi_colour(note, "gray", dim=True))
            lines.append(" ")
        if bonus_affinities:
            bonus_label = ", ".join(
                affinity_description(affinity, include_relationships=True)
                for affinity in bonus_affinities
            )
            lines.append(ansi_colour(f"Trait Affinities: {bonus_label}", "magenta", dim=True))
        cleaned = [line for line in lines if line]
        if cleaned and cleaned[-1] == " ":
            cleaned = cleaned[:-1]
        return ansi_block(cleaned)

    parts: list[str] = []
    for data in entry_data:
        entry_lines = [
            coloured(data["stars"], colour="yellow", bold=True),
            _colour_affinity_tokens(str(data["name"]), data["affinities"], use_ansi=False),
        ]
        if gain_resolver is not None:
            gain_range = gain_resolver(max(1, int(data["grade"] or 0)))
            if gain_range:
                minimum, maximum = gain_range
                if maximum > 0:
                    if minimum == maximum:
                        gain_text = (
                            f"Yields {format_number(minimum)} Qi per cultivation."
                        )
                    else:
                        gain_text = (
                            "Yields "
                            f"{format_number(minimum)}–{format_number(maximum)} Qi per cultivation."
                        )
                    entry_lines.append(
                        coloured(
                            gain_text,
                            colour=SECTION_COLOURS["cultivation"],
                            bold=True,
                        )
                    )
        for note in data["component_notes"]:
            entry_lines.append(coloured(note, dim=True))
        parts.append("\n".join(entry_lines))

    if bonus_affinities:
        bonus_label = ", ".join(
            affinity_token(aff, include_relationships=True) for aff in bonus_affinities
        )
        parts.append(coloured(f"Trait Affinities: {bonus_label}", dim=True))

    return "\n\n".join(parts)


@lru_cache(maxsize=256)
def _render_innate_soul_cached(
    signature: tuple[tuple[tuple[str, int, tuple[str, ...]], ...], tuple[str, ...]],
    use_ansi: bool,
) -> str:
    return _render_innate_soul_inner(signature, use_ansi, None)


def _render_innate_soul(
    signature: tuple[tuple[tuple[str, int, tuple[str, ...]], ...], tuple[str, ...]],
    use_ansi: bool,
    gain_resolver: Callable[[int], tuple[int, int] | None] | None = None,
) -> str:
    if gain_resolver is None:
        return _render_innate_soul_cached(signature, use_ansi)
    return _render_innate_soul_inner(signature, use_ansi, gain_resolver)


def describe_innate_soul(
    base: InnateSoul | InnateSoulSet | Sequence[InnateSoul] | None,
    *,
    use_ansi: bool = False,
    gain_resolver: Callable[[int], tuple[int, int] | None] | None = None,
) -> str:
    signature = _innate_soul_signature(base)
    if signature is None:
        if use_ansi:
            return ansi_block([ansi_colour("No innate soul discovered.", "gray", dim=True)])
        return coloured("No innate soul discovered.", dim=True)

    return _render_innate_soul(signature, use_ansi, gain_resolver)


def _ordinal_step_label(step: int) -> str:
    step_map = {
        0: "Mortal Realm",
        1: "First Step",
        2: "Second Step",
        3: "Third Step",
        4: "Fourth Step",
        5: "Fifth Step",
        6: "Sixth Step",
        7: "Seventh Step",
        8: "Eighth Step",
        9: "Ninth Step",
        10: "Tenth Step",
    }
    try:
        normalized = max(0, int(step))
    except (TypeError, ValueError):
        normalized = 0
    if normalized in step_map:
        return step_map[normalized]
    if 10 <= normalized % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(normalized % 10, "th")
    return f"{normalized}{suffix} Step"


class EquipmentManagerView(OwnedView):
    """Interactive menu for equipping and unequipping items."""

    _EMPTY_OPTION_VALUE = "__EMPTY__"

    class EquipmentSlotSelect(discord.ui.Select["EquipmentManagerView"]):
        def __init__(self, view: "EquipmentManagerView") -> None:
            super().__init__(
                placeholder="Select a gear slot",
                options=[
                    discord.SelectOption(
                        label=EQUIPMENT_SLOT_LABELS.get(
                            EQUIPMENT_SLOT_ORDER[0],
                            EQUIPMENT_SLOT_ORDER[0].value.title(),
                        ),
                        value=EQUIPMENT_SLOT_ORDER[0].value,
                    )
                ],
                min_values=1,
                max_values=1,
                row=0,
            )
            self._manager = view

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            view = self._manager
            if interaction.user.id != view.owner_id:
                await interaction.response.send_message(
                    "Only the cultivator who opened this menu may use it.",
                    ephemeral=True,
                )
                return
            raw_value = self.values[0]
            try:
                selected = EquipmentSlot.from_value(
                    raw_value, default=view.selected_slot
                )
            except ValueError:
                selected = view.selected_slot
            view.selected_slot = selected
            view.selected_inventory_key = None
            view.selected_equipped_option = None
            slot_label = EQUIPMENT_SLOT_LABELS.get(
                selected, selected.value.title()
            ).lower()
            await view.refresh(
                interaction,
                status=view._status_info(f"Viewing the {slot_label} slot."),
            )

    class EquippedItemSelect(discord.ui.Select["EquipmentManagerView"]):
        def __init__(self, view: "EquipmentManagerView") -> None:
            super().__init__(
                placeholder="Inspect equipped gear",
                options=[
                    discord.SelectOption(
                        label="No gear equipped",
                        value=EquipmentManagerView._EMPTY_OPTION_VALUE,
                    )
                ],
                min_values=1,
                max_values=1,
                row=1,
            )
            self._manager = view

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            view = self._manager
            if interaction.user.id != view.owner_id:
                await interaction.response.send_message(
                    "Only the cultivator who opened this menu may use it.",
                    ephemeral=True,
                )
                return
            value = self.values[0]
            if value == EquipmentManagerView._EMPTY_OPTION_VALUE:
                await interaction.response.defer()
                return
            view.selected_equipped_option = value
            await view.refresh(interaction)

    class InventoryItemSelect(discord.ui.Select["EquipmentManagerView"]):
        def __init__(self, view: "EquipmentManagerView") -> None:
            super().__init__(
                placeholder="Select inventory equipment",
                options=[
                    discord.SelectOption(
                        label="No equipment available",
                        value=EquipmentManagerView._EMPTY_OPTION_VALUE,
                    )
                ],
                min_values=1,
                max_values=1,
                row=2,
            )
            self._manager = view

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            view = self._manager
            if interaction.user.id != view.owner_id:
                await interaction.response.send_message(
                    "Only the cultivator who opened this menu may use it.",
                    ephemeral=True,
                )
                return
            value = self.values[0]
            if value == EquipmentManagerView._EMPTY_OPTION_VALUE:
                await interaction.response.defer()
                return
            view.selected_inventory_key = value
            await view.refresh(interaction)

    class EquipButton(discord.ui.Button["EquipmentManagerView"]):
        def __init__(self) -> None:
            super().__init__(
                label="Equip Selected",
                style=discord.ButtonStyle.success,
                emoji="🛡",
                row=3,
            )

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            view = self.view
            if not isinstance(view, EquipmentManagerView):
                await interaction.response.send_message(
                    "This equipment menu is no longer available.",
                    ephemeral=True,
                )
                return
            await view.handle_equip(interaction)

    class UnequipButton(discord.ui.Button["EquipmentManagerView"]):
        def __init__(self) -> None:
            super().__init__(
                label="Unequip Selected",
                style=discord.ButtonStyle.danger,
                emoji="📤",
                row=3,
            )

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            view = self.view
            if not isinstance(view, EquipmentManagerView):
                await interaction.response.send_message(
                    "This equipment menu is no longer available.",
                    ephemeral=True,
                )
                return
            await view.handle_unequip(interaction)

    class CloseButton(discord.ui.Button["EquipmentManagerView"]):
        def __init__(self) -> None:
            super().__init__(
                label="Close",
                style=discord.ButtonStyle.secondary,
                emoji="✖",
                row=3,
            )

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            view = self.view
            if not isinstance(view, EquipmentManagerView):
                await interaction.response.send_message(
                    "This equipment menu is no longer available.",
                    ephemeral=True,
                )
                return
            await view.close(interaction)

    def __init__(
        self,
        cog: "PlayerCog",
        guild_id: int,
        owner_id: int,
        *,
        avatar_url: str | None,
        initial_player: PlayerProgress,
        focus: str = "equip",
        initial_slot: EquipmentSlot | None = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(owner_id, timeout=timeout)
        self.cog = cog
        self.guild_id = guild_id
        self.avatar_url = avatar_url
        self.focus_mode = focus
        self.selected_slot = self._determine_initial_slot(initial_player, initial_slot)
        self.selected_inventory_key: str | None = None
        self.selected_equipped_option: str | None = None
        self.focused_equipped_key: str | None = None
        self.focused_inventory_key: str | None = None
        self.status_message = self._default_status()
        self._equipped_option_lookup: dict[str, tuple[str, EquipmentSlot]] = {}
        self._inventory_lookup: dict[str, tuple[Item, int]] = {}
        self._first_sync = True
        self._last_player_snapshot = initial_player
        self._reload_requested = False
        self.message: Optional[discord.Message] = None
        self._slot_cache: dict[EquipmentSlot, dict[str, Any]] = {}
        self._inventory_candidates_cache: dict[
            EquipmentSlot, list[dict[str, Any]]
        ] = {}
        self._inventory_lookup_by_slot: dict[
            EquipmentSlot, dict[str, tuple[Item | None, int]]
        ] = {}

        self.slot_select = EquipmentManagerView.EquipmentSlotSelect(self)
        self.equipped_select = EquipmentManagerView.EquippedItemSelect(self)
        self.inventory_select = EquipmentManagerView.InventoryItemSelect(self)
        self.equip_button = EquipmentManagerView.EquipButton()
        self.unequip_button = EquipmentManagerView.UnequipButton()
        self.close_button = EquipmentManagerView.CloseButton()

        self.add_item(self.slot_select)
        self.add_item(self.equipped_select)
        self.add_item(self.inventory_select)
        self.add_item(self.equip_button)
        self.add_item(self.unequip_button)
        self.add_item(self.close_button)

        self._sync_from_player(initial_player)

    def _default_status(self) -> str:
        if self.focus_mode == "unequip":
            return self._status_info(
                "Choose equipped gear to inspect or remove it."
            )
        if self.focus_mode == "equip":
            return self._status_info(
                "Browse your inventory below and press Equip to wear an item."
            )
        return self._status_info(
            "Use the controls below to manage your equipment."
        )

    def _status_info(self, message: str) -> str:
        return coloured(
            f"ℹ️ {message}",
            colour=SECTION_COLOURS["inventory"],
            bold=True,
        )

    def _status_success(self, message: str) -> str:
        return coloured(f"✅ {message}", colour="green", bold=True)

    def _status_warning(self, message: str) -> str:
        return coloured(
            f"⚠️ {message}", colour=SECTION_COLOURS["warning"], bold=True
        )

    def _status_error(self, message: str) -> str:
        return coloured(
            f"❌ {message}", colour=SECTION_COLOURS["error"], bold=True
        )

    def _determine_initial_slot(
        self, player: PlayerProgress, provided: EquipmentSlot | None
    ) -> EquipmentSlot:
        slot: EquipmentSlot | None = None
        if provided is not None:
            slot = provided
        else:
            if self.focus_mode == "equip":
                for candidate in EQUIPMENT_SLOT_ORDER:
                    if self._has_inventory_for_slot(player, candidate):
                        slot = candidate
                        break
                if slot is None:
                    for candidate in EQUIPMENT_SLOT_ORDER:
                        if player.equipment.get(candidate.value):
                            slot = candidate
                            break
            else:
                for candidate in EQUIPMENT_SLOT_ORDER:
                    if player.equipment.get(candidate.value):
                        slot = candidate
                        break
                if slot is None:
                    for candidate in EQUIPMENT_SLOT_ORDER:
                        if self._has_inventory_for_slot(player, candidate):
                            slot = candidate
                            break
        if slot is None:
            slot = EQUIPMENT_SLOT_ORDER[0]
        return slot

    def _has_inventory_for_slot(
        self, player: PlayerProgress, slot: EquipmentSlot
    ) -> bool:
        for key, amount in player.inventory.items():
            if amount <= 0:
                continue
            item = self.cog.state.items.get(key)
            if not item or item.item_type != "equipment":
                continue
            item_slot = item.equipment_slot or EquipmentSlot.ACCESSORY
            if not isinstance(item_slot, EquipmentSlot):
                try:
                    item_slot = EquipmentSlot.from_value(
                        item_slot, default=EquipmentSlot.ACCESSORY
                    )
                except ValueError:
                    item_slot = EquipmentSlot.ACCESSORY
            if item_slot is slot:
                return True
        return False

    def _sync_from_player(self, player: PlayerProgress) -> None:
        self._last_player_snapshot = player
        try:
            self.selected_slot = EquipmentSlot.from_value(
                self.selected_slot, default=EQUIPMENT_SLOT_ORDER[0]
            )
        except ValueError:
            self.selected_slot = EQUIPMENT_SLOT_ORDER[0]

        (
            self._slot_cache,
            self._inventory_candidates_cache,
            self._inventory_lookup_by_slot,
        ) = _build_equipment_view_cache(player, self.cog.state.items)

        slot_options = self._build_slot_options()
        self.slot_select.options = slot_options
        slot_label = EQUIPMENT_SLOT_LABELS.get(
            self.selected_slot, self.selected_slot.value.title()
        )
        self.slot_select.placeholder = f"Slot: {slot_label}"

        selected_slot_cache = self._slot_cache.get(self.selected_slot, {})
        equipped_entries = selected_slot_cache.get("equipped_entries", [])
        if self._first_sync and equipped_entries and self.focus_mode == "unequip":
            self.selected_equipped_option = equipped_entries[0]["value"]

        if (
            self.selected_equipped_option
            and self.selected_equipped_option
            not in {entry["value"] for entry in equipped_entries}
        ):
            self.selected_equipped_option = None

        equipped_options = self._build_equipped_options(equipped_entries)
        self.equipped_select.options = equipped_options
        self.equipped_select.disabled = not equipped_entries
        self.equipped_select.placeholder = (
            "Inspect equipped gear"
            if equipped_entries
            else "No gear equipped in this slot"
        )

        inventory_candidates = self._inventory_candidates_cache.get(
            self.selected_slot, []
        )
        if (
            self._first_sync
            and inventory_candidates
            and self.focus_mode == "equip"
        ):
            self.selected_inventory_key = inventory_candidates[0]["key"]

        if (
            self.selected_inventory_key
            and self.selected_inventory_key
            not in {entry["key"] for entry in inventory_candidates}
        ):
            self.selected_inventory_key = None

        inventory_options = self._build_inventory_options(inventory_candidates)
        self.inventory_select.options = inventory_options
        self.inventory_select.disabled = not inventory_candidates
        self.inventory_select.placeholder = (
            "Select inventory equipment"
            if inventory_candidates
            else "No matching gear in your inventory"
        )

        slot_lookup = self._inventory_lookup_by_slot.get(self.selected_slot, {})
        self._inventory_lookup = {
            key: (item, amount)
            for key, (item, amount) in slot_lookup.items()
            if item is not None
        }

        self.focused_equipped_key = None
        if self.selected_equipped_option:
            lookup = self._equipped_option_lookup.get(self.selected_equipped_option)
            if lookup is not None:
                self.focused_equipped_key = lookup[0]

        self.focused_inventory_key = self.selected_inventory_key

        self.equip_button.disabled = not inventory_candidates
        self.unequip_button.disabled = not equipped_entries

        self._first_sync = False

    def request_reload(self) -> None:
        """Force the next refresh to re-fetch the player from the datastore."""

        self._reload_requested = True

    def _build_slot_options(self) -> list[discord.SelectOption]:
        options: list[discord.SelectOption] = []
        for slot in EQUIPMENT_SLOT_ORDER:
            snapshot = self._slot_cache.get(slot)
            if snapshot is None:
                label = EQUIPMENT_SLOT_LABELS.get(slot, slot.value.title())
                description = "No slot limit"
            else:
                label = snapshot["label"]
                description = snapshot["option_description"]
            options.append(
                discord.SelectOption(
                    label=label,
                    value=slot.value,
                    description=description,
                    default=(slot is self.selected_slot),
                )
            )
        return options

    def _equipped_entries(
        self, player: PlayerProgress
    ) -> list[dict[str, Any]]:
        snapshot = self._slot_cache.get(self.selected_slot)
        if not snapshot:
            return []
        return snapshot["equipped_entries"]

    def _build_equipped_options(
        self, entries: list[dict[str, Any]]
    ) -> list[discord.SelectOption]:
        options: list[discord.SelectOption] = []
        self._equipped_option_lookup = {}
        for entry in entries:
            option = discord.SelectOption(
                label=entry["label"][:100],
                value=entry["value"],
                description=entry.get("details_text"),
                default=(entry["value"] == self.selected_equipped_option),
            )
            options.append(option)
            self._equipped_option_lookup[entry["value"]] = (
                entry["key"],
                self.selected_slot,
            )
        if not options:
            self.selected_equipped_option = None
        if options and self.selected_equipped_option is None:
            options[0].default = True
            self.selected_equipped_option = options[0].value
            self.focused_equipped_key = self._equipped_option_lookup[
                options[0].value
            ][0]
        return (
            options
            if options
            else [
                discord.SelectOption(
                    label="No gear equipped",
                    value=self._EMPTY_OPTION_VALUE,
                    description="Equip items to manage them here.",
                )
            ]
        )

    def _inventory_candidates(
        self, player: PlayerProgress
    ) -> list[dict[str, Any]]:
        return self._inventory_candidates_cache.get(self.selected_slot, [])

    def _build_inventory_options(
        self, candidates: list[dict[str, Any]]
    ) -> list[discord.SelectOption]:
        options: list[discord.SelectOption] = []
        for entry in candidates:
            option = discord.SelectOption(
                label=entry["option_label"],
                value=entry["key"],
                description=entry.get("option_description"),
                default=(entry["key"] == self.selected_inventory_key),
            )
            options.append(option)
        if (
            options
            and self.selected_inventory_key is None
            and self.focus_mode == "equip"
        ):
            options[0].default = True
            self.selected_inventory_key = options[0].value
            self.focused_inventory_key = self.selected_inventory_key
        return (
            options
            if options
            else [
                discord.SelectOption(
                    label="No equipment available",
                    value=self._EMPTY_OPTION_VALUE,
                    description="Loot or purchase gear to equip it.",
                )
            ]
        )

    def initial_embed(self) -> discord.Embed:
        return self._build_embed(self._last_player_snapshot)

    def _build_embed(self, player: PlayerProgress) -> discord.Embed:
        embed = discord.Embed(
            title=_label_with_icon(
                "inventory", f"{player.name}'s Equipment"
            ),
            colour=discord.Colour.blurple(),
        )
        if self.avatar_url:
            self.cog._apply_avatar_portrait(embed, self.avatar_url)

        embed.description = self.status_message or self._default_status()

        overview_lines = self._slot_overview_lines(player)
        embed.add_field(
            name=_label_with_icon("inventory", "Slot Overview"),
            value=coloured_block([f"• {line}" for line in overview_lines])
            if overview_lines
            else coloured("No equipment configured.", dim=True),
            inline=False,
        )

        slot_label = EQUIPMENT_SLOT_LABELS.get(
            self.selected_slot, self.selected_slot.value.title()
        )
        slot_detail_lines = self._slot_detail_lines(player, self.selected_slot)
        embed.add_field(
            name=_label_with_icon(
                "inventory", f"{slot_label} Slot Details"
            ),
            value=coloured_block(slot_detail_lines),
            inline=False,
        )

        inventory_lines = self._inventory_summary_lines()
        embed.add_field(
            name=_label_with_icon("inventory", "Inventory Choices"),
            value=coloured_block(inventory_lines),
            inline=False,
        )

        equipped_focus = self._equipped_focus_lines()
        if equipped_focus:
            embed.add_field(
                name=_label_with_icon("inventory", "Equipped Focus"),
                value=coloured_block(equipped_focus),
                inline=False,
            )

        inventory_focus = self._inventory_focus_lines()
        if inventory_focus:
            embed.add_field(
                name=_label_with_icon("inventory", "Inventory Focus"),
                value=coloured_block(inventory_focus),
                inline=False,
            )

        return embed

    def _slot_state(
        self, player: PlayerProgress, slot: EquipmentSlot
    ) -> dict[str, Any]:
        snapshot = self._slot_cache.get(slot)
        if snapshot is None:
            capacity = equipment_slot_capacity(slot)
            usage = equipment_slot_usage(player, slot, self.cog.state.items)
            return {"capacity": capacity, "usage": usage, "items": []}
        return {
            "capacity": snapshot["capacity"],
            "usage": snapshot["usage"],
            "items": snapshot["detail_items"],
        }

    def _slot_overview_lines(self, player: PlayerProgress) -> list[str]:
        lines: list[str] = []
        for slot in EQUIPMENT_SLOT_ORDER:
            snapshot = self._slot_cache.get(slot)
            if snapshot is None:
                label = EQUIPMENT_SLOT_LABELS.get(slot, slot.value.title())
                line = f"{label}: {coloured('No data', dim=True)}"
            else:
                line = snapshot["overview_line"]
            if slot is self.selected_slot:
                line = coloured(line, colour=SECTION_COLOURS["inventory"], bold=True)
            lines.append(line)
        return lines

    def _slot_detail_lines(
        self, player: PlayerProgress, slot: EquipmentSlot
    ) -> list[str]:
        snapshot = self._slot_cache.get(slot)
        if snapshot is None:
            return [coloured("No slot data available.", dim=True)]

        lines = [
            coloured_pair(
                "Slots Used",
                snapshot["usage_text"],
                label_colour=SECTION_COLOURS["inventory"],
                value_colour="white",
                bold_value=True,
            )
        ]
        lines.append(
            coloured_pair(
                "Free Slots",
                snapshot["free_text"],
                label_colour=SECTION_COLOURS["inventory"],
                value_colour="white",
                bold_value=True,
            )
        )
        detail_items = snapshot["detail_items"]
        if detail_items:
            for entry in detail_items:
                text = entry["label"]
                if entry["key"] == self.focused_equipped_key:
                    text = coloured(text, colour=SECTION_COLOURS["inventory"], bold=True)
                lines.append(f"• {text}")
        else:
            lines.append(coloured("• Nothing equipped.", dim=True))
        return lines

    def _inventory_summary_lines(self) -> list[str]:
        candidates = self._inventory_candidates_cache.get(self.selected_slot, [])
        if not candidates:
            return [coloured("• No matching items in your inventory.", dim=True)]
        lines: list[str] = []
        for entry in candidates:
            text = entry["summary_text"]
            if entry["key"] == self.focused_inventory_key:
                text = coloured(
                    text, colour=SECTION_COLOURS["inventory"], bold=True
                )
            lines.append(f"• {text}")
        return lines

    def _equipped_focus_lines(self) -> list[str]:
        if not self.focused_equipped_key:
            return []
        item = self.cog.state.items.get(self.focused_equipped_key)
        if not item:
            return [coloured("That item configuration no longer exists.", dim=True)]
        return self._describe_item(item)

    def _inventory_focus_lines(self) -> list[str]:
        if not self.focused_inventory_key:
            return []
        item, _ = self._inventory_lookup.get(self.focused_inventory_key, (None, 0))
        if not item:
            item = self.cog.state.items.get(self.focused_inventory_key)
        if not item:
            return [coloured("That item can no longer be equipped.", dim=True)]
        return self._describe_item(item)

    def _describe_item(self, item: Item) -> list[str]:
        lines: list[str] = []
        description = item.description.strip()
        if description:
            lines.append(coloured(description, dim=True))
        stats = item.stat_modifiers
        stat_lines: list[str] = []
        for stat_name in PLAYER_STAT_NAMES:
            value = getattr(stats, stat_name)
            if not value:
                continue
            icon = STAT_ICONS.get(stat_name, "•")
            label = stat_name.replace("_", " ").title()
            stat_lines.append(
                f"{icon} {label}: +{_format_stat_value(float(value))}"
            )
        if stat_lines:
            lines.extend(stat_lines)
        slots_required = getattr(item, "slots_required", 1) or 1
        lines.append(
            coloured_pair(
                "Slot Cost",
                format_number(slots_required),
                label_colour=SECTION_COLOURS["inventory"],
                value_colour="white",
                bold_value=True,
            )
        )
        if item.weapon_type:
            lines.append(
                coloured_pair(
                    "Weapon Type",
                    WEAPON_TYPE_LABELS.get(
                        item.weapon_type, item.weapon_type.value.title()
                    ),
                    label_colour=SECTION_COLOURS["inventory"],
                    value_colour="white",
                    bold_value=False,
                )
            )
        if getattr(item, "inventory_space_bonus", 0):
            lines.append(
                coloured_pair(
                    "Inventory Bonus",
                    f"+{format_number(item.inventory_space_bonus)} slots",
                    label_colour=SECTION_COLOURS["inventory"],
                    value_colour="white",
                    bold_value=False,
                )
            )
        if not lines:
            lines.append(coloured("No notable effects.", dim=True))
        return lines

    async def refresh(
        self,
        interaction: discord.Interaction,
        *,
        status: str | None = None,
        force_reload: bool = False,
    ) -> None:
        should_fetch = (
            force_reload
            or self._reload_requested
            or self._last_player_snapshot is None
            or interaction.user.id != self.owner_id
        )
        player: PlayerProgress | None
        if should_fetch:
            self.cog.log.debug(
                "EquipmentManagerView refresh: fetching player (force=%s, requested=%s)",
                force_reload,
                self._reload_requested,
            )
            player = await self.cog._fetch_player(self.guild_id, interaction.user.id)
            self._reload_requested = False
        else:
            player = self._last_player_snapshot
            self.cog.log.debug(
                "EquipmentManagerView refresh: using cached player snapshot for %s",
                interaction.user.id,
            )
        if not player:
            await interaction.response.edit_message(
                embed=_make_embed(
                    "error",
                    "No Cultivation Record",
                    "Use /register first.",
                    discord.Colour.red(),
                ),
                view=None,
            )
            self.stop()
            return
        if status is not None:
            self.status_message = status
        self._sync_from_player(player)
        try:
            await interaction.response.edit_message(
                embed=self._build_embed(player), view=self
            )
        except discord.HTTPException:
            pass

    async def handle_equip(self, interaction: discord.Interaction) -> None:
        if not self.selected_inventory_key:
            await self.refresh(
                interaction,
                status=self._status_warning(
                    "Select an item from your inventory to equip."
                ),
            )
            return
        player = await self.cog._fetch_player(self.guild_id, interaction.user.id)
        if not player:
            await interaction.response.edit_message(
                embed=_make_embed(
                    "error",
                    "No Cultivation Record",
                    "Use /register first.",
                    discord.Colour.red(),
                ),
                view=None,
            )
            self.stop()
            return
        self._last_player_snapshot = player
        key = self.selected_inventory_key
        item = self.cog.state.items.get(key)
        if not item or item.item_type != "equipment":
            await self.refresh(
                interaction,
                status=self._status_error(
                    "That item can no longer be equipped."
                ),
            )
            return
        slot_value = item.equipment_slot or EquipmentSlot.ACCESSORY
        if not isinstance(slot_value, EquipmentSlot):
            try:
                slot_value = EquipmentSlot.from_value(
                    slot_value, default=EquipmentSlot.ACCESSORY
                )
            except ValueError:
                slot_value = EquipmentSlot.ACCESSORY
        slots_required = getattr(item, "slots_required", 1) or 1
        capacity = equipment_slot_capacity(slot_value)
        usage = equipment_slot_usage(player, slot_value, self.cog.state.items)
        if capacity > 0 and usage + slots_required > capacity:
            slot_label = EQUIPMENT_SLOT_LABELS.get(
                slot_value, slot_value.value.title()
            )
            await self.refresh(
                interaction,
                status=self._status_warning(
                    f"Your {slot_label.lower()} slot has no free space."
                ),
            )
            return
        quantity = player.inventory.get(key, 0)
        if quantity <= 0:
            await self.refresh(
                interaction,
                status=self._status_warning(
                    "You no longer possess that item."
                ),
            )
            return
        player.inventory[key] = quantity - 1
        if player.inventory[key] <= 0:
            player.inventory.pop(key)
        add_equipped_item(player, slot_value, key)
        self._last_player_snapshot = player
        try:
            await self.cog._save_player(self.guild_id, player)
        except Exception:
            self.cog.log.exception(
                "EquipmentManagerView equip failed to persist; forcing reload"
            )
            self.request_reload()
            await self.refresh(
                interaction,
                status=self._status_error(
                    "A storage error prevented equipping that item."
                ),
                force_reload=True,
            )
            return
        self.selected_slot = slot_value
        self.selected_inventory_key = None
        self.selected_equipped_option = None
        slot_label = EQUIPMENT_SLOT_LABELS.get(
            slot_value, slot_value.value.title()
        )
        slot_phrase = slot_label.lower()
        if slot_value is EquipmentSlot.WEAPON:
            slot_phrase = "weapon slots"
        elif slot_value is EquipmentSlot.ACCESSORY:
            slot_phrase = "accessory slots"
        await self.refresh(
            interaction,
            status=self._status_success(
                f"Equipped **{item.name}** to your {slot_phrase}."
            ),
        )

    async def handle_unequip(self, interaction: discord.Interaction) -> None:
        lookup = (
            self._equipped_option_lookup.get(self.selected_equipped_option or "")
        )
        if not lookup:
            await self.refresh(
                interaction,
                status=self._status_warning(
                    "Select equipped gear to remove first."
                ),
            )
            return
        item_key, slot_hint = lookup
        player = await self.cog._fetch_player(self.guild_id, interaction.user.id)
        if not player:
            await interaction.response.edit_message(
                embed=_make_embed(
                    "error",
                    "No Cultivation Record",
                    "Use /register first.",
                    discord.Colour.red(),
                ),
                view=None,
            )
            self.stop()
            return
        self._last_player_snapshot = player
        if item_key not in player.equipped_item_keys():
            await self.refresh(
                interaction,
                status=self._status_warning(
                    "That item is no longer equipped."
                ),
            )
            return
        slot_for_item: EquipmentSlot | None = None
        for slot_key, values in player.equipment.items():
            if item_key in values:
                try:
                    slot_for_item = EquipmentSlot.from_value(
                        slot_key, default=slot_hint
                    )
                except ValueError:
                    slot_for_item = slot_hint
                break
        item_obj = self.cog.state.items.get(item_key)
        bonus = 0
        if item_obj is not None:
            try:
                bonus = int(getattr(item_obj, "inventory_space_bonus", 0))
            except (TypeError, ValueError):
                bonus = 0
        bonus = max(0, bonus)
        load_before = inventory_load(player)
        capacity_before = inventory_capacity(player, self.cog.state.items)
        capacity_after = max(0, capacity_before - bonus)
        if load_before + 1 > capacity_after:
            await self.refresh(
                interaction,
                status=self._status_warning(
                    "Your packs are full. Free space before unequipping this item."
                ),
            )
            return
        if not remove_equipped_item(player, item_key):
            await self.refresh(
                interaction,
                status=self._status_error(
                    "Something prevented that item from being removed."
                ),
            )
            return
        added = add_item_to_inventory(player, item_key, 1, self.cog.state.items)
        if added <= 0:
            if slot_for_item is not None:
                add_equipped_item(player, slot_for_item, item_key)
            await self.refresh(
                interaction,
                status=self._status_error(
                    "Your inventory overflowed before the item could be stored."
                ),
            )
            return
        self._last_player_snapshot = player
        try:
            await self.cog._save_player(self.guild_id, player)
        except Exception:
            self.cog.log.exception(
                "EquipmentManagerView unequip failed to persist; forcing reload"
            )
            self.request_reload()
            await self.refresh(
                interaction,
                status=self._status_error(
                    "A storage error prevented unequipping that item."
                ),
                force_reload=True,
            )
            return
        item_name = item_obj.name if item_obj else item_key
        self.selected_inventory_key = None
        self.selected_equipped_option = None
        if slot_for_item is not None:
            self.selected_slot = slot_for_item
        await self.refresh(
            interaction,
            status=self._status_success(
                f"Stored **{item_name}** back into your inventory."
            ),
        )

    async def close(self, interaction: discord.Interaction) -> None:
        for child in self.children:
            child.disabled = True
        self.status_message = self._status_info(
            "Equipment manager closed. Reopen with /equip to continue."
        )
        try:
            await interaction.response.edit_message(
                embed=self._build_embed(self._last_player_snapshot), view=self
            )
        except discord.HTTPException:
            pass
        self.stop()

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class ProfileEquipmentPanel:
    """Interactive equipment controls embedded within the profile view."""

    _EMPTY_OPTION_VALUE = "__EMPTY__"

    class EquipmentSlotSelect(discord.ui.Select["ProfileView"]):
        def __init__(self, panel: "ProfileEquipmentPanel") -> None:
            super().__init__(
                placeholder="Choose a gear slot to inspect",
                options=[
                    discord.SelectOption(
                        label=EQUIPMENT_SLOT_LABELS.get(
                            EQUIPMENT_SLOT_ORDER[0],
                            EQUIPMENT_SLOT_ORDER[0].value.title(),
                        ),
                        value=EQUIPMENT_SLOT_ORDER[0].value,
                    )
                ],
                min_values=1,
                max_values=1,
                row=1,
            )
            self._panel = panel

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            panel = self._panel
            if not await panel.ensure_owner(interaction):
                return
            raw_value = self.values[0]
            try:
                selected = EquipmentSlot.from_value(raw_value, default=panel.selected_slot)
            except ValueError:
                selected = panel.selected_slot
            panel.selected_slot = selected
            panel.selected_inventory_key = None
            panel.selected_equipped_option = None
            panel.focus_mode = "equip"
            slot_label = EQUIPMENT_SLOT_LABELS.get(selected, selected.value.title()).lower()
            panel.status_message = panel._status_info(
                f"Viewing the {slot_label} slot."
            )
            await panel.refresh(interaction)

    class EquippedSelect(discord.ui.Select["ProfileView"]):
        def __init__(self, panel: "ProfileEquipmentPanel") -> None:
            super().__init__(
                placeholder="Inspect equipped gear",
                options=[
                    discord.SelectOption(
                        label="No gear equipped",
                        value=ProfileEquipmentPanel._EMPTY_OPTION_VALUE,
                    )
                ],
                min_values=1,
                max_values=1,
                row=2,
            )
            self._panel = panel

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            panel = self._panel
            if not await panel.ensure_owner(interaction):
                return
            value = self.values[0]
            if value == ProfileEquipmentPanel._EMPTY_OPTION_VALUE:
                if not interaction.response.is_done():
                    await interaction.response.defer()
                return
            panel.selected_equipped_option = value
            panel.focus_mode = "unequip"
            lookup = panel._equipped_option_lookup.get(value)
            if lookup:
                item_key, _ = lookup
                item_obj = panel.cog.state.items.get(item_key)
                if item_obj:
                    panel.status_message = panel._status_info(
                        f"Inspecting **{item_obj.name}**."
                    )
                else:
                    panel.status_message = panel._status_info(
                        "Inspecting equipped gear."
                    )
            else:
                panel.status_message = panel._status_info(
                    "Inspecting equipped gear."
                )
            await panel.refresh(interaction)

    class InventorySelect(discord.ui.Select["ProfileView"]):
        def __init__(self, panel: "ProfileEquipmentPanel") -> None:
            super().__init__(
                placeholder="Select inventory equipment",
                options=[
                    discord.SelectOption(
                        label="No equipment available",
                        value=ProfileEquipmentPanel._EMPTY_OPTION_VALUE,
                    )
                ],
                min_values=1,
                max_values=1,
                row=3,
            )
            self._panel = panel

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            panel = self._panel
            if not await panel.ensure_owner(interaction):
                return
            value = self.values[0]
            if value == ProfileEquipmentPanel._EMPTY_OPTION_VALUE:
                if not interaction.response.is_done():
                    await interaction.response.defer()
                return
            panel.selected_inventory_key = value
            panel.focus_mode = "equip"
            item, _ = panel._inventory_lookup.get(value, (None, 0))
            if item:
                panel.status_message = panel._status_info(
                    f"Preparing to equip **{item.name}**."
                )
            else:
                panel.status_message = panel._status_info(
                    "Select equipment from your inventory to inspect it."
                )
            await panel.refresh(interaction)

    class EquipButton(discord.ui.Button["ProfileView"]):
        def __init__(self, panel: "ProfileEquipmentPanel") -> None:
            super().__init__(
                label="Equip Selected",
                style=discord.ButtonStyle.success,
                emoji="🛡",
                row=4,
            )
            self._panel = panel

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            panel = self._panel
            if not await panel.ensure_owner(interaction):
                return
            await panel.handle_equip(interaction)

    class UnequipButton(discord.ui.Button["ProfileView"]):
        def __init__(self, panel: "ProfileEquipmentPanel") -> None:
            super().__init__(
                label="Unequip",
                style=discord.ButtonStyle.secondary,
                emoji="📤",
                row=4,
            )
            self._panel = panel

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            panel = self._panel
            if not await panel.ensure_owner(interaction):
                return
            await panel.handle_unequip(interaction)

    class OpenManagerButton(discord.ui.Button["ProfileView"]):
        def __init__(self, panel: "ProfileEquipmentPanel") -> None:
            super().__init__(
                label="Open Full Manager",
                style=discord.ButtonStyle.primary,
                emoji="🧰",
                row=4,
            )
            self._panel = panel

        async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
            panel = self._panel
            if not await panel.ensure_owner(interaction):
                return
            await panel.open_manager(interaction)

    def __init__(
        self,
        cog: "PlayerCog",
        guild_id: int,
        owner_id: int,
        *,
        avatar_url: str | None,
        initial_player: PlayerProgress,
    ) -> None:
        self.cog = cog
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.avatar_url = avatar_url
        self.selected_slot = self._determine_initial_slot(initial_player, None)
        self.selected_inventory_key: str | None = None
        self.selected_equipped_option: str | None = None
        self.focused_equipped_key: str | None = None
        self.focused_inventory_key: str | None = None
        self.focus_mode: str = "equip"
        self.status_message: str | None = None
        self._inventory_lookup: dict[str, tuple[Item, int]] = {}
        self._equipped_option_lookup: dict[str, tuple[str, EquipmentSlot]] = {}
        self._last_player_snapshot: PlayerProgress = initial_player
        self._first_sync = True
        self._view: ProfileView | None = None
        self._slot_cache: dict[EquipmentSlot, dict[str, Any]] = {}
        self._inventory_candidates_cache: dict[
            EquipmentSlot, list[dict[str, Any]]
        ] = {}
        self._inventory_lookup_by_slot: dict[
            EquipmentSlot, dict[str, tuple[Item | None, int]]
        ] = {}

        self.slot_select = ProfileEquipmentPanel.EquipmentSlotSelect(self)
        self.equipped_select = ProfileEquipmentPanel.EquippedSelect(self)
        self.inventory_select = ProfileEquipmentPanel.InventorySelect(self)
        self.equip_button = ProfileEquipmentPanel.EquipButton(self)
        self.unequip_button = ProfileEquipmentPanel.UnequipButton(self)
        self.open_manager_button = ProfileEquipmentPanel.OpenManagerButton(self)

        self.sync_from_player(initial_player)

    async def ensure_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        if interaction.response.is_done():
            await interaction.followup.send(
                "Only the cultivator viewing this profile may use these controls.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Only the cultivator viewing this profile may use these controls.",
                ephemeral=True,
            )
        return False

    def attach(self, view: ProfileView) -> None:
        self._view = view

    def controls(self) -> list[discord.ui.Item[ProfileView]]:
        return [
            self.slot_select,
            self.equipped_select,
            self.inventory_select,
            self.equip_button,
            self.unequip_button,
            self.open_manager_button,
        ]

    def build_embed(self) -> discord.Embed:
        return self._build_embed(self._last_player_snapshot)

    def _default_status(self) -> str:
        if self.focus_mode == "unequip":
            return self._status_info("Select equipped gear to inspect or remove it.")
        if self.focus_mode == "equip":
            return self._status_info(
                "Choose equipment from your inventory below and press Equip."
            )
        return self._status_info(
            "Use the controls below to review and adjust your loadout."
        )

    def _status_info(self, message: str) -> str:
        return coloured(
            f"ℹ️ {message}",
            colour=SECTION_COLOURS["inventory"],
            bold=True,
        )

    def _status_success(self, message: str) -> str:
        return coloured(f"✅ {message}", colour="green", bold=True)

    def _status_warning(self, message: str) -> str:
        return coloured(
            f"⚠️ {message}", colour=SECTION_COLOURS["warning"], bold=True
        )

    def _status_error(self, message: str) -> str:
        return coloured(
            f"❌ {message}", colour=SECTION_COLOURS["error"], bold=True
        )

    def _determine_initial_slot(
        self, player: PlayerProgress, provided: EquipmentSlot | None
    ) -> EquipmentSlot:
        slot: EquipmentSlot | None = None
        if provided is not None:
            slot = provided
        if slot is None:
            for candidate in EQUIPMENT_SLOT_ORDER:
                if player.equipment.get(candidate.value):
                    slot = candidate
                    break
        if slot is None:
            slot = EQUIPMENT_SLOT_ORDER[0]
        return slot

    def sync_from_player(self, player: PlayerProgress) -> None:
        self._last_player_snapshot = player
        try:
            self.selected_slot = EquipmentSlot.from_value(
                self.selected_slot, default=EQUIPMENT_SLOT_ORDER[0]
            )
        except ValueError:
            self.selected_slot = EQUIPMENT_SLOT_ORDER[0]

        (
            self._slot_cache,
            self._inventory_candidates_cache,
            self._inventory_lookup_by_slot,
        ) = _build_equipment_view_cache(player, self.cog.state.items)

        slot_options = self._build_slot_options()
        self.slot_select.options = slot_options
        slot_label = EQUIPMENT_SLOT_LABELS.get(
            self.selected_slot, self.selected_slot.value.title()
        )
        self.slot_select.placeholder = f"Slot: {slot_label}"

        equipped_entries = self._equipped_entries(player)
        if self._first_sync and equipped_entries:
            self.selected_equipped_option = equipped_entries[0]["value"]

        if (
            self.selected_equipped_option
            and self.selected_equipped_option
            not in {entry["value"] for entry in equipped_entries}
        ):
            self.selected_equipped_option = None

        equipped_options = self._build_equipped_options(equipped_entries)
        self.equipped_select.options = equipped_options
        self.equipped_select.disabled = not equipped_entries
        self.equipped_select.placeholder = (
            "Inspect equipped gear"
            if equipped_entries
            else "No gear equipped in this slot"
        )

        inventory_candidates = self._inventory_candidates(player)
        if self._first_sync and inventory_candidates:
            self.selected_inventory_key = inventory_candidates[0]["key"]

        if (
            self.selected_inventory_key
            and self.selected_inventory_key
            not in {entry["key"] for entry in inventory_candidates}
        ):
            self.selected_inventory_key = None

        inventory_options = self._build_inventory_options(inventory_candidates)
        self.inventory_select.options = inventory_options
        self.inventory_select.disabled = not inventory_candidates
        self.inventory_select.placeholder = (
            "Select inventory equipment"
            if inventory_candidates
            else "No matching gear in your inventory"
        )

        slot_lookup = self._inventory_lookup_by_slot.get(self.selected_slot, {})
        self._inventory_lookup = {
            key: (item, amount)
            for key, (item, amount) in slot_lookup.items()
            if item is not None
        }

        self.focused_equipped_key = None
        if self.selected_equipped_option:
            lookup = self._equipped_option_lookup.get(self.selected_equipped_option)
            if lookup is not None:
                self.focused_equipped_key = lookup[0]

        self.focused_inventory_key = self.selected_inventory_key

        self.equip_button.disabled = not inventory_candidates
        self.unequip_button.disabled = not equipped_entries

        if self.status_message is None:
            self.status_message = self._default_status()

        self._first_sync = False

    def _build_slot_options(self) -> list[discord.SelectOption]:
        options: list[discord.SelectOption] = []
        for slot in EQUIPMENT_SLOT_ORDER:
            snapshot = self._slot_cache.get(slot)
            if snapshot is None:
                label = EQUIPMENT_SLOT_LABELS.get(slot, slot.value.title())
                description = "No slot limit"
            else:
                label = snapshot["label"]
                description = snapshot["option_description"]
            options.append(
                discord.SelectOption(
                    label=label,
                    value=slot.value,
                    description=description,
                    default=(slot is self.selected_slot),
                )
            )
        return options

    def _equipped_entries(
        self, player: PlayerProgress
    ) -> list[dict[str, Any]]:
        snapshot = self._slot_cache.get(self.selected_slot)
        if not snapshot:
            return []
        return snapshot["equipped_entries"]

    def _build_equipped_options(
        self, entries: list[dict[str, Any]]
    ) -> list[discord.SelectOption]:
        options: list[discord.SelectOption] = []
        self._equipped_option_lookup = {}
        for entry in entries:
            option = discord.SelectOption(
                label=entry["label"][:100],
                value=entry["value"],
                description=entry.get("details_text"),
                default=(entry["value"] == self.selected_equipped_option),
            )
            options.append(option)
            self._equipped_option_lookup[entry["value"]] = (
                entry["key"],
                self.selected_slot,
            )
        if not options:
            self.selected_equipped_option = None
        if options and self.selected_equipped_option is None:
            options[0].default = True
            self.selected_equipped_option = options[0].value
            self.focused_equipped_key = self._equipped_option_lookup[
                options[0].value
            ][0]
        return (
            options
            if options
            else [
                discord.SelectOption(
                    label="No gear equipped",
                    value=self._EMPTY_OPTION_VALUE,
                    description="Equip items to manage them here.",
                )
            ]
        )

    def _inventory_candidates(
        self, player: PlayerProgress
    ) -> list[dict[str, Any]]:
        return self._inventory_candidates_cache.get(self.selected_slot, [])

    def _build_inventory_options(
        self, candidates: list[dict[str, Any]]
    ) -> list[discord.SelectOption]:
        options: list[discord.SelectOption] = []
        for entry in candidates:
            option = discord.SelectOption(
                label=entry["item"].name[:100],
                value=entry["key"],
                description=(" • ".join(entry["details"]) or None),
                default=(entry["key"] == self.selected_inventory_key),
            )
            options.append(option)
        if (
            options
            and self.selected_inventory_key is None
            and self.focus_mode == "equip"
        ):
            options[0].default = True
            self.selected_inventory_key = options[0].value
            self.focused_inventory_key = self.selected_inventory_key
        if options:
            return options

        self.selected_inventory_key = None
        self.focused_inventory_key = None
        return [
            discord.SelectOption(
                label="No equipment available",
                value=self._EMPTY_OPTION_VALUE,
                description="You have no matching gear in your inventory.",
            )
        ]

    def _slot_state(
        self, player: PlayerProgress, slot: EquipmentSlot
    ) -> dict[str, Any]:
        snapshot = self._slot_cache.get(slot)
        if snapshot is None:
            capacity = equipment_slot_capacity(slot)
            usage = equipment_slot_usage(player, slot, self.cog.state.items)
            return {"capacity": capacity, "usage": usage, "items": []}
        return {
            "capacity": snapshot["capacity"],
            "usage": snapshot["usage"],
            "items": snapshot["detail_items"],
        }

    def _slot_overview_lines(self, player: PlayerProgress) -> list[str]:
        lines: list[str] = []
        for slot in EQUIPMENT_SLOT_ORDER:
            snapshot = self._slot_cache.get(slot)
            if snapshot is None:
                label = EQUIPMENT_SLOT_LABELS.get(slot, slot.value.title())
                line = f"{label}: {coloured('No data', dim=True)}"
            else:
                line = snapshot["overview_line"]
            if slot is self.selected_slot:
                line = coloured(line, colour=SECTION_COLOURS["inventory"], bold=True)
            lines.append(line)
        return lines

    def _slot_detail_lines(
        self, player: PlayerProgress, slot: EquipmentSlot
    ) -> list[str]:
        snapshot = self._slot_cache.get(slot)
        if snapshot is None:
            return [coloured("No slot data available.", dim=True)]

        lines = [
            coloured_pair(
                "Slots Used",
                snapshot["usage_text"],
                label_colour=SECTION_COLOURS["inventory"],
                value_colour="white",
                bold_value=True,
            )
        ]
        lines.append(
            coloured_pair(
                "Free Slots",
                snapshot["free_text"],
                label_colour=SECTION_COLOURS["inventory"],
                value_colour="white",
                bold_value=True,
            )
        )
        detail_items = snapshot["detail_items"]
        if detail_items:
            for entry in detail_items:
                text = entry["label"]
                if entry["key"] == self.focused_equipped_key:
                    text = coloured(text, colour=SECTION_COLOURS["inventory"], bold=True)
                lines.append(f"• {text}")
        else:
            lines.append(coloured("• Nothing equipped.", dim=True))
        return lines

    def _inventory_summary_lines(self) -> list[str]:
        candidates = self._inventory_candidates_cache.get(self.selected_slot, [])
        if not candidates:
            return [coloured("• No matching items in your inventory.", dim=True)]
        lines: list[str] = []
        for entry in candidates:
            text = entry["summary_text"]
            if entry["key"] == self.focused_inventory_key:
                text = coloured(
                    text, colour=SECTION_COLOURS["inventory"], bold=True
                )
            lines.append(f"• {text}")
        return lines

    def _equipped_focus_lines(self) -> list[str]:
        if not self.focused_equipped_key:
            return []
        item = self.cog.state.items.get(self.focused_equipped_key)
        if not item:
            return [coloured("That item configuration no longer exists.", dim=True)]
        return self._describe_item(item)

    def _inventory_focus_lines(self) -> list[str]:
        if not self.focused_inventory_key:
            return []
        item, _ = self._inventory_lookup.get(self.focused_inventory_key, (None, 0))
        if not item:
            item = self.cog.state.items.get(self.focused_inventory_key)
        if not item:
            return [coloured("That item can no longer be equipped.", dim=True)]
        return self._describe_item(item)

    def _describe_item(self, item: Item) -> list[str]:
        lines: list[str] = []
        description = item.description.strip()
        if description:
            lines.append(coloured(description, dim=True))
        stats = item.stat_modifiers
        stat_lines: list[str] = []
        for stat_name in PLAYER_STAT_NAMES:
            value = getattr(stats, stat_name)
            if not value:
                continue
            icon = STAT_ICONS.get(stat_name, "•")
            label = stat_name.replace("_", " ").title()
            stat_lines.append(
                f"{icon} {label}: +{_format_stat_value(float(value))}"
            )
        if stat_lines:
            lines.extend(stat_lines)
        slots_required = getattr(item, "slots_required", 1) or 1
        lines.append(
            coloured_pair(
                "Slot Cost",
                format_number(slots_required),
                label_colour=SECTION_COLOURS["inventory"],
                value_colour="white",
                bold_value=True,
            )
        )
        if item.weapon_type:
            lines.append(
                coloured_pair(
                    "Weapon Type",
                    WEAPON_TYPE_LABELS.get(
                        item.weapon_type, item.weapon_type.value.title()
                    ),
                    label_colour=SECTION_COLOURS["inventory"],
                    value_colour="white",
                    bold_value=False,
                )
            )
        if getattr(item, "inventory_space_bonus", 0):
            lines.append(
                coloured_pair(
                    "Inventory Bonus",
                    f"+{format_number(item.inventory_space_bonus)} slots",
                    label_colour=SECTION_COLOURS["inventory"],
                    value_colour="white",
                    bold_value=False,
                )
            )
        if not lines:
            lines.append(coloured("No notable effects.", dim=True))
        return lines

    def _build_embed(self, player: PlayerProgress) -> discord.Embed:
        embed = discord.Embed(
            title=_label_with_icon("inventory", f"{player.name}'s Equipment"),
            colour=discord.Colour.blurple(),
        )
        if self.avatar_url:
            self.cog._apply_avatar_portrait(embed, self.avatar_url)

        embed.description = self.status_message or self._default_status()

        overview_lines = self._slot_overview_lines(player)
        embed.add_field(
            name=_label_with_icon("inventory", "Slot Overview"),
            value=coloured_block([f"• {line}" for line in overview_lines])
            if overview_lines
            else coloured("No equipment configured.", dim=True),
            inline=False,
        )

        slot_label = EQUIPMENT_SLOT_LABELS.get(
            self.selected_slot, self.selected_slot.value.title()
        )
        slot_detail_lines = self._slot_detail_lines(player, self.selected_slot)
        embed.add_field(
            name=_label_with_icon(
                "inventory", f"{slot_label} Slot Details"
            ),
            value=coloured_block(slot_detail_lines),
            inline=False,
        )

        inventory_lines = self._inventory_summary_lines()
        embed.add_field(
            name=_label_with_icon("inventory", "Inventory Choices"),
            value=coloured_block(inventory_lines),
            inline=False,
        )

        equipped_focus = self._equipped_focus_lines()
        if equipped_focus:
            embed.add_field(
                name=_label_with_icon("inventory", "Equipped Focus"),
                value=coloured_block(equipped_focus),
                inline=False,
            )

        inventory_focus = self._inventory_focus_lines()
        if inventory_focus:
            embed.add_field(
                name=_label_with_icon("inventory", "Inventory Focus"),
                value=coloured_block(inventory_focus),
                inline=False,
            )

        return embed

    async def refresh(
        self, interaction: discord.Interaction, *, status: str | None = None
    ) -> None:
        if status is not None:
            self.status_message = status
        player = await self.cog._fetch_player(self.guild_id, interaction.user.id)
        if not player:
            message = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=message, ephemeral=True)
            else:
                await interaction.response.send_message(embed=message, ephemeral=True)
            return
        self._last_player_snapshot = player
        if status is None:
            self.status_message = self.status_message or self._default_status()
        self.sync_from_player(player)
        view = self._view
        if view is None:
            if not interaction.response.is_done():
                await interaction.response.defer()
            return
        if not interaction.response.is_done():
            await interaction.response.defer()
        await view.refresh_current_page()

    async def handle_equip(self, interaction: discord.Interaction) -> None:
        key = self.selected_inventory_key
        if not key:
            await self.refresh(
                interaction,
                status=self._status_warning(
                    "Choose an item from your inventory to equip first."
                ),
            )
            return
        player = await self.cog._fetch_player(self.guild_id, interaction.user.id)
        if not player:
            message = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=message, ephemeral=True)
            else:
                await interaction.response.send_message(embed=message, ephemeral=True)
            return
        item = self.cog.state.items.get(key)
        if not item or item.item_type != "equipment":
            await self.refresh(
                interaction,
                status=self._status_error(
                    "That item can no longer be equipped."
                ),
            )
            return
        slot_value = item.equipment_slot or EquipmentSlot.ACCESSORY
        if not isinstance(slot_value, EquipmentSlot):
            try:
                slot_value = EquipmentSlot.from_value(
                    slot_value, default=EquipmentSlot.ACCESSORY
                )
            except ValueError:
                slot_value = EquipmentSlot.ACCESSORY
        slots_required = getattr(item, "slots_required", 1) or 1
        capacity = equipment_slot_capacity(slot_value)
        usage = equipment_slot_usage(player, slot_value, self.cog.state.items)
        if capacity > 0 and usage + slots_required > capacity:
            slot_label = EQUIPMENT_SLOT_LABELS.get(
                slot_value, slot_value.value.title()
            )
            await self.refresh(
                interaction,
                status=self._status_warning(
                    f"Your {slot_label.lower()} slot has no free space."
                ),
            )
            return
        quantity = player.inventory.get(key, 0)
        if quantity <= 0:
            await self.refresh(
                interaction,
                status=self._status_warning(
                    "You no longer possess that item."
                ),
            )
            return
        player.inventory[key] = quantity - 1
        if player.inventory[key] <= 0:
            player.inventory.pop(key)
        add_equipped_item(player, slot_value, key)
        await self.cog._save_player(self.guild_id, player)
        self.selected_slot = slot_value
        self.selected_inventory_key = None
        self.selected_equipped_option = None
        slot_label = EQUIPMENT_SLOT_LABELS.get(
            slot_value, slot_value.value.title()
        )
        slot_phrase = slot_label.lower()
        if slot_value is EquipmentSlot.WEAPON:
            slot_phrase = "weapon slots"
        elif slot_value is EquipmentSlot.ACCESSORY:
            slot_phrase = "accessory slots"
        await self.refresh(
            interaction,
            status=self._status_success(
                f"Equipped **{item.name}** to your {slot_phrase}."
            ),
        )

    async def handle_unequip(self, interaction: discord.Interaction) -> None:
        lookup = self._equipped_option_lookup.get(self.selected_equipped_option or "")
        if not lookup:
            await self.refresh(
                interaction,
                status=self._status_warning(
                    "Select equipped gear to remove first."
                ),
            )
            return
        item_key, slot_hint = lookup
        player = await self.cog._fetch_player(self.guild_id, interaction.user.id)
        if not player:
            message = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=message, ephemeral=True)
            else:
                await interaction.response.send_message(embed=message, ephemeral=True)
            return
        if item_key not in player.equipped_item_keys():
            await self.refresh(
                interaction,
                status=self._status_warning(
                    "That item is no longer equipped."
                ),
            )
            return
        slot_for_item: EquipmentSlot | None = None
        for slot_key, values in player.equipment.items():
            if item_key in values:
                try:
                    slot_for_item = EquipmentSlot.from_value(
                        slot_key, default=slot_hint
                    )
                except ValueError:
                    slot_for_item = slot_hint
                break
        item_obj = self.cog.state.items.get(item_key)
        bonus = 0
        if item_obj is not None:
            try:
                bonus = int(getattr(item_obj, "inventory_space_bonus", 0))
            except (TypeError, ValueError):
                bonus = 0
        bonus = max(0, bonus)
        load_before = inventory_load(player)
        capacity_before = inventory_capacity(player, self.cog.state.items)
        capacity_after = max(0, capacity_before - bonus)
        if load_before + 1 > capacity_after:
            await self.refresh(
                interaction,
                status=self._status_warning(
                    "Your packs are full. Free space before unequipping this item."
                ),
            )
            return
        if not remove_equipped_item(player, item_key):
            await self.refresh(
                interaction,
                status=self._status_error(
                    "Something prevented that item from being removed."
                ),
            )
            return
        added = add_item_to_inventory(player, item_key, 1, self.cog.state.items)
        if added <= 0:
            if slot_for_item is not None:
                add_equipped_item(player, slot_for_item, item_key)
            await self.refresh(
                interaction,
                status=self._status_error(
                    "Your inventory overflowed before the item could be stored."
                ),
            )
            return
        await self.cog._save_player(self.guild_id, player)
        item_name = item_obj.name if item_obj else item_key
        self.selected_inventory_key = None
        self.selected_equipped_option = None
        if slot_for_item is not None:
            self.selected_slot = slot_for_item
        await self.refresh(
            interaction,
            status=self._status_success(
                f"Stored **{item_name}** back into your inventory."
            ),
        )

    async def open_manager(self, interaction: discord.Interaction) -> None:
        await self.cog._open_equipment_manager(interaction, focus="equip")


class PlayerCog(HeavenCog):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.config: BotConfig = bot.config  # type: ignore[assignment]
        self.log = logging.getLogger(__name__)
        self._rarity_config_loaded = False
        self._affinity_weight_overrides: dict[SpiritualAffinity, float] = {}
        self._innate_soul_grade_weight_overrides: dict[int, float] = {}
        self._innate_soul_count_weight_overrides: dict[int, float] = {}
        self._affinity_config_present = False
        self._innate_soul_grade_config_present = False
        self._birth_trait_config_loaded = False
        self._birth_trait_chance = 0.0
        self._birth_trait_weights: dict[str, float] = {}
        self._birth_trait_config_present = False
        self._birth_race_config_loaded = False
        self._birth_race_chance = 0.0
        self._birth_race_weights: dict[str, float] = {}
        self._birth_race_config_present = False
        self._cooldown_config_loaded: set[int] = set()
        self._guild_cooldown_defaults: dict[int, int] = {}
        self._guild_cooldown_overrides: dict[int, dict[int, int]] = {}
        self._player_cache: dict[
            tuple[int, int], tuple[float, float, PlayerProgress]
        ] = {}
        self._player_cache_ttl = 45.0
        self._player_cache_capacity = 256
        self._trade_sessions: dict[int, TradeNegotiationView] = {}

    async def _respond_interaction(
        self,
        interaction: discord.Interaction,
        *,
        content: str | None = None,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        ephemeral: bool = True,
    ) -> None:
        payload: dict[str, object] = {}
        if content is not None:
            payload["content"] = content
        if embed is not None:
            payload["embed"] = embed
        if view is not None:
            payload["view"] = view
        if interaction.type is InteractionType.component:
            if interaction.response.is_done():
                await interaction.edit_original_response(**payload)
            else:
                await interaction.response.edit_message(**payload)
            return
        if interaction.response.is_done():
            await interaction.followup.send(**payload, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(**payload, ephemeral=ephemeral)

    def _get_cached_player(
        self, guild_id: int, user_id: int, revision: float
    ) -> PlayerProgress | None:
        key = (guild_id, user_id)
        cached = self._player_cache.get(key)
        if not cached:
            return None
        cached_at, cached_revision, snapshot = cached
        now = time.monotonic()
        if now - cached_at > self._player_cache_ttl:
            self._player_cache.pop(key, None)
            return None
        if revision > 0 and cached_revision > 0 and revision != cached_revision:
            self._player_cache.pop(key, None)
            return None
        self._player_cache[key] = (now, cached_revision, snapshot)
        return copy.deepcopy(snapshot)

    def _cache_player(
        self, guild_id: int, player: PlayerProgress, revision: float
    ) -> None:
        key = (guild_id, player.user_id)
        if key not in self._player_cache and len(self._player_cache) >= self._player_cache_capacity:
            oldest_key: tuple[int, int] | None = None
            oldest_time = float("inf")
            for existing_key, (cached_at, _, _) in self._player_cache.items():
                if cached_at < oldest_time:
                    oldest_time = cached_at
                    oldest_key = existing_key
            if oldest_key is not None:
                self._player_cache.pop(oldest_key, None)
        self._player_cache[key] = (
            time.monotonic(),
            revision,
            copy.deepcopy(player),
        )

    def _format_realm_section(
        self,
        header: str,
        qi_stage: CultivationStage | None,
        body_stage: CultivationStage | None,
        soul_stage: CultivationStage | None,
        *,
        active_path: CultivationPath | str = CultivationPath.QI,
    ) -> str:
        lines = [f"**{header}**"]
        path_value = CultivationPath.from_value(active_path)
        label_map = {
            CultivationPath.QI: ("Qi Realm", qi_stage),
            CultivationPath.BODY: ("Body Realm", body_stage),
            CultivationPath.SOUL: ("Soul Realm", soul_stage),
        }
        label, stage = label_map[path_value]
        if stage is None:
            lines.append(f"{label}: Unrefined")
        else:
            realm_name = stage.realm or stage.name
            phase = stage.phase_display
            lines.append(f"{label}: {realm_name} ({phase})")
        return "\n".join(lines)

    def invalidate_rarity_cache(self) -> None:
        self._rarity_config_loaded = False
        self._affinity_weight_overrides.clear()
        self._innate_soul_grade_weight_overrides.clear()
        self._innate_soul_count_weight_overrides.clear()
        self._affinity_config_present = False
        self._innate_soul_grade_config_present = False

    async def refresh_rarity_config(self, guild_id: int) -> None:
        self.invalidate_rarity_cache()
        await self._load_rarity_config(guild_id)

    def invalidate_birth_trait_config(self) -> None:
        self._birth_trait_config_loaded = False
        self._birth_trait_chance = 0.0
        self._birth_trait_weights.clear()
        self._birth_trait_config_present = False

    async def refresh_birth_trait_config(self, guild_id: int) -> None:
        self.invalidate_birth_trait_config()
        await self._load_birth_trait_config(guild_id)

    def invalidate_birth_race_config(self) -> None:
        self._birth_race_config_loaded = False
        self._birth_race_chance = 0.0
        self._birth_race_weights.clear()
        self._birth_race_config_present = False

    async def refresh_birth_race_config(self, guild_id: int) -> None:
        self.invalidate_birth_race_config()
        await self._load_birth_race_config(guild_id)

    def invalidate_cultivation_cooldown_config(
        self, guild_id: int | None = None
    ) -> None:
        if guild_id is None:
            self._cooldown_config_loaded.clear()
            self._guild_cooldown_defaults.clear()
            self._guild_cooldown_overrides.clear()
            return
        self._cooldown_config_loaded.discard(guild_id)
        self._guild_cooldown_defaults.pop(guild_id, None)
        self._guild_cooldown_overrides.pop(guild_id, None)

    def set_cultivation_cooldown_config(
        self, guild_id: int, default: int, overrides: Mapping[int, int]
    ) -> None:
        self._guild_cooldown_defaults[guild_id] = max(0, int(default))
        cleaned: dict[int, int] = {}
        for user_id, value in overrides.items():
            try:
                cooldown = max(0, int(value))
            except (TypeError, ValueError):
                continue
            if cooldown <= 0:
                continue
            cleaned[int(user_id)] = cooldown
        self._guild_cooldown_overrides[guild_id] = cleaned
        self._cooldown_config_loaded.add(guild_id)

    async def _ensure_cultivation_cooldown_config(self, guild_id: int) -> None:
        if guild_id in self._cooldown_config_loaded:
            return
        bucket = await self.store.get(guild_id, "config")
        payload = bucket.get("cultivation_cooldown", {})
        default_value = DEFAULT_CULTIVATION_COOLDOWN
        overrides: dict[int, int] = {}
        if isinstance(payload, Mapping):
            try:
                default_value = int(payload.get("default", default_value))
            except (TypeError, ValueError):
                default_value = DEFAULT_CULTIVATION_COOLDOWN
            override_payload = payload.get("overrides", {})
            if isinstance(override_payload, Mapping):
                for raw_user, raw_value in override_payload.items():
                    try:
                        user_id = int(raw_user)
                        cooldown = max(0, int(raw_value))
                    except (TypeError, ValueError):
                        continue
                    if cooldown <= 0:
                        continue
                    overrides[user_id] = cooldown
        self.set_cultivation_cooldown_config(guild_id, default_value, overrides)

    def _resolve_cultivation_cooldown(
        self, guild_id: int, player: PlayerProgress
    ) -> int:
        overrides = self._guild_cooldown_overrides.get(guild_id, {})
        override_value = overrides.get(player.user_id)
        if override_value is not None and override_value > 0:
            return override_value
        return max(0, self._guild_cooldown_defaults.get(guild_id, DEFAULT_CULTIVATION_COOLDOWN))

    async def get_affinity_weights(
        self, guild_id: int
    ) -> dict[SpiritualAffinity, float]:
        await self._load_rarity_config(guild_id)
        return self._current_affinity_weights()

    async def get_innate_soul_grade_weights(self, guild_id: int) -> dict[int, float]:
        await self._load_rarity_config(guild_id)
        return self._current_innate_soul_grade_weights()

    async def get_innate_soul_count_weights(self, guild_id: int) -> dict[int, float]:
        await self._load_rarity_config(guild_id)
        return self._current_innate_soul_count_weights()

    async def _load_rarity_config(self, guild_id: int) -> None:
        if self._rarity_config_loaded:
            return
        bucket = await self.store.get(guild_id, "config")
        affinity_payload = bucket.get("affinity_weights", {})
        base_payload = bucket.get("innate_soul_grade_weights", {})
        count_payload = bucket.get("innate_soul_count_weights", {})

        affinity_overrides: dict[SpiritualAffinity, float] = {}
        if isinstance(affinity_payload, Mapping):
            for key, value in affinity_payload.items():
                try:
                    affinity = SpiritualAffinity(str(key))
                except ValueError:
                    continue
                try:
                    weight = float(value)
                except (TypeError, ValueError):
                    continue
                if weight < 0:
                    continue
                affinity_overrides[affinity] = weight

        base_overrides: dict[int, float] = {}
        if isinstance(base_payload, Mapping):
            for key, value in base_payload.items():
                try:
                    grade = int(key)
                except (TypeError, ValueError):
                    continue
                if grade < 1 or grade > 9:
                    continue
                try:
                    weight = float(value)
                except (TypeError, ValueError):
                    continue
                if weight < 0:
                    continue
                base_overrides[grade] = weight

        count_overrides: dict[int, float] = {}
        if isinstance(count_payload, Mapping):
            for key, value in count_payload.items():
                try:
                    count = int(key)
                except (TypeError, ValueError):
                    continue
                if count < 1:
                    continue
                try:
                    weight = float(value)
                except (TypeError, ValueError):
                    continue
                if weight < 0:
                    continue
                count_overrides[count] = weight

        self._affinity_weight_overrides = affinity_overrides
        self._innate_soul_grade_weight_overrides = base_overrides
        self._innate_soul_count_weight_overrides = count_overrides
        self._affinity_config_present = bool(affinity_overrides)
        self._innate_soul_grade_config_present = bool(base_overrides)
        self._rarity_config_loaded = True

    def _current_affinity_weights(self) -> dict[SpiritualAffinity, float]:
        weights = _default_affinity_weights()
        for affinity, weight in self._affinity_weight_overrides.items():
            weights[affinity] = weight
        return weights

    def _current_innate_soul_grade_weights(self) -> dict[int, float]:
        weights = _default_innate_soul_grade_weights()
        for grade, weight in self._innate_soul_grade_weight_overrides.items():
            weights[grade] = weight
        return weights

    def _current_innate_soul_count_weights(self) -> dict[int, float]:
        weights = _default_innate_soul_count_weights()
        for count, weight in self._innate_soul_count_weight_overrides.items():
            weights[count] = weight
        return weights

    async def _load_birth_trait_config(self, guild_id: int) -> None:
        if self._birth_trait_config_loaded:
            return
        bucket = await self.store.get(guild_id, "config")
        payload = bucket.get("birth_trait", {})
        chance = 0.0
        weights: dict[str, float] = {}
        if isinstance(payload, Mapping):
            chance_value = payload.get("chance")
            try:
                chance = float(chance_value)
            except (TypeError, ValueError):
                chance = 0.0
            weights_payload = payload.get("weights", {})
            if isinstance(weights_payload, Mapping):
                for key, value in weights_payload.items():
                    try:
                        weight = float(value)
                    except (TypeError, ValueError):
                        continue
                    if weight <= 0:
                        continue
                    weights[str(key)] = weight
        self._birth_trait_chance = max(0.0, chance)
        self._birth_trait_weights = weights
        self._birth_trait_config_present = bool(weights)
        self._birth_trait_config_loaded = True

    def _roll_birth_trait_key(self) -> Optional[str]:
        if not self._birth_trait_config_present or not self._birth_trait_weights:
            return None
        chance = max(0.0, min(100.0, self._birth_trait_chance))
        threshold = chance / 100.0
        if random.random() > threshold:
            return None
        try:
            return _weighted_choice(self._birth_trait_weights)
        except ValueError:
            return None

    async def _load_birth_race_config(self, guild_id: int) -> None:
        if self._birth_race_config_loaded:
            return
        bucket = await self.store.get(guild_id, "config")
        payload = bucket.get("birth_race", {})
        chance = 0.0
        weights: dict[str, float] = {}
        if isinstance(payload, Mapping):
            chance_value = payload.get("chance")
            try:
                chance = float(chance_value)
            except (TypeError, ValueError):
                chance = 0.0
            weights_payload = payload.get("weights", {})
            if isinstance(weights_payload, Mapping):
                for key, value in weights_payload.items():
                    try:
                        weight = float(value)
                    except (TypeError, ValueError):
                        continue
                    if weight <= 0:
                        continue
                    weights[str(key)] = weight
        self._birth_race_chance = max(0.0, chance)
        self._birth_race_weights = weights
        self._birth_race_config_present = bool(weights)
        self._birth_race_config_loaded = True

    def _roll_birth_race_key(self) -> Optional[str]:
        if not self._birth_race_config_present or not self._birth_race_weights:
            return None
        chance = max(0.0, min(100.0, self._birth_race_chance))
        threshold = chance / 100.0
        if random.random() > threshold:
            return None
        try:
            return _weighted_choice(self._birth_race_weights)
        except ValueError:
            return None

    async def _get_stage_key(
        self, *, path: str = CultivationPath.QI.value
    ) -> Optional[str]:
        path_value = CultivationPath.from_value(path)
        if path_value is CultivationPath.BODY:
            stages = list(self.state.body_cultivation_stages.values())
        elif path_value is CultivationPath.SOUL:
            stages = list(self.state.soul_cultivation_stages.values())
        else:
            stages = list(self.state.qi_cultivation_stages.values())
        if not stages:
            return None
        stages.sort(key=lambda stage: stage.ordering_tuple)
        if path_value is CultivationPath.QI:
            for stage in stages:
                if stage.is_mortal:
                    return stage.key
        return stages[0].key

    def _title_positions(self, title_keys: Sequence[str]) -> dict[str, TitlePosition]:
        positions: dict[str, TitlePosition] = {}
        for key in title_keys:
            title = self.state.titles.get(key)
            if title:
                positions[key] = title.position
        return positions

    def _auto_assign_titles(self, player: PlayerProgress, title_keys: Sequence[str]) -> None:
        for key in title_keys:
            title = self.state.titles.get(key)
            if title:
                player.auto_equip_title(title)

    def _format_title_name(self, title_key: str | None) -> str:
        if not title_key:
            return coloured("None equipped", dim=True)
        title = self.state.titles.get(title_key)
        return title.name if title else title_key

    def _trait_objects(self, player: PlayerProgress) -> list[SpecialTrait]:
        return [
            self.state.traits[key]
            for key in player.trait_keys
            if key in self.state.traits
        ]

    def _technique_exp_bonus(
        self,
        player: PlayerProgress,
        path: CultivationPath,
        *,
        innate_soul_set: InnateSoulSet | None = None,
    ) -> tuple[float, float, list[str]]:
        addition_total = 0.0
        multiplier_total = 0.0
        contributing: list[str] = []
        for key in player.cultivation_technique_keys:
            technique = self.state.cultivation_techniques.get(key)
            if technique is None:
                continue
            if innate_soul_set is not None and not technique.is_affinity_compatible(innate_soul_set):
                continue
            addition, multiplier = technique.experience_adjustments(path)
            if abs(addition) <= 1e-6 and abs(multiplier) <= 1e-6:
                continue
            addition_total += addition
            multiplier_total += multiplier
            contributing.append(technique.name or technique.key)
        return addition_total, multiplier_total, contributing

    def _apply_cultivation_technique_bonus(
        self,
        player: PlayerProgress,
        *,
        base_gain: int,
        path: CultivationPath,
        combined_base: InnateSoulSet | None,
    ) -> tuple[int, int, list[str]]:
        addition_bonus, multiplier_bonus, bonus_sources = self._technique_exp_bonus(
            player,
            path,
            innate_soul_set=combined_base,
        )
        adjusted_total = base_gain
        technique_bonus = 0
        if addition_bonus or multiplier_bonus:
            adjusted = max(0.0, base_gain + addition_bonus)
            if multiplier_bonus:
                adjusted *= 1 + multiplier_bonus
            adjusted_total = int(round(adjusted))
            if adjusted_total < base_gain:
                adjusted_total = base_gain
            technique_bonus = adjusted_total - base_gain
            if technique_bonus > 0:
                player.cultivation_exp += technique_bonus
        return adjusted_total, technique_bonus, bonus_sources

    def _time_until_next_cultivation_action(
        self, guild_id: int, player: PlayerProgress
    ) -> int:
        timestamps = [
            ts
            for ts in (
                player.last_cultivate_ts,
                player.last_train_ts,
                player.last_temper_ts,
            )
            if ts
        ]
        if not timestamps:
            return 0
        cooldown = self._resolve_cultivation_cooldown(guild_id, player)
        if cooldown <= 0:
            return 0
        elapsed = time.time() - max(timestamps)
        if elapsed >= cooldown:
            return 0
        return int(max(0, cooldown - elapsed))

    async def _resolve_player_context(
        self,
        guild: discord.Guild,
        user_id: int,
        *,
        player: PlayerProgress | None = None,
    ) -> tuple[PlayerProgress | None, list[SpecialTrait], InnateSoulSet | None]:
        if player is None:
            player = await self._fetch_player(guild.id, user_id)
        if not player:
            return None, [], None
        trait_list = self._trait_objects(player)
        combined = player.combined_innate_soul(trait_list)
        return player, trait_list, combined

    def _skill_grade_stars(self, grade: str | None) -> str:
        value = skill_grade_number(grade)
        star_count = value if value else 1
        display_value = value if value else 0
        return f"{'⭐' * star_count}({display_value})"

    def _skill_display_name(self, skill: Skill) -> str:
        grade_value = skill_grade_number(skill.grade)
        if grade_value:
            grade_label = f"Grade {grade_value}"
        else:
            grade_label = skill.grade.title() if skill.grade else "Unranked"
        return f"{skill.name} ({grade_label})"

    def _skill_entries(
        self,
        player: PlayerProgress,
        base: InnateSoulSet | None = None,
        *,
        category: SkillCategory = SkillCategory.ACTIVE,
        stats: Stats | None = None,
    ) -> list[tuple[Skill, str, str]]:
        qi_stage = self.state.get_stage(player.cultivation_stage, CultivationPath.QI)
        if _player_in_mortal_realm(player, stage=qi_stage):
            return []

        if not player.skill_proficiency:
            return []

        trait_objects = [
            self.state.traits[key]
            for key in player.trait_keys
            if key in self.state.traits
        ]
        effective_stats = stats
        effective_base = base
        equipped_weapon_types = active_weapon_types(player, self.state.items)
        if effective_stats is None or effective_base is None:
            body_stage = (
                self.state.get_stage(player.body_cultivation_stage, CultivationPath.BODY)
                if player.body_cultivation_stage
                else None
            )
            soul_stage = (
                self.state.get_stage(player.soul_cultivation_stage, CultivationPath.SOUL)
                if player.soul_cultivation_stage
                else None
            )
            race = self.state.races.get(player.race_key) if player.race_key else None
            equipped_items = equipped_items_for_player(player, self.state.items)
            if effective_stats is None and qi_stage is not None:
                computed = player.effective_stats(
                    qi_stage,
                    body_stage,
                    soul_stage,
                    race,
                    trait_objects,
                    equipped_items,
                )
                passive_bonus_map = self.passive_skill_bonuses(player)
                total_passive_bonus = Stats()
                for bonus in passive_bonus_map.values():
                    total_passive_bonus.add_in_place(bonus)
                computed.add_in_place(total_passive_bonus)
                effective_stats = computed
        if effective_stats is None:
            effective_stats = player.stats.copy()
        if effective_base is None:
            effective_base = player.combined_innate_soul(trait_objects)
        base = effective_base

        def sort_key(item: tuple[str, int]) -> tuple[str, str]:
            key, _ = item
            skill = self.state.skills.get(key)
            name = skill.name if skill else key
            return (name.lower(), key)

        entries: list[tuple[Skill, str, str]] = []
        for key, prof in sorted(player.skill_proficiency.items(), key=sort_key):
            skill = self.state.skills.get(key)
            if not skill or skill.category is not category:
                continue
            affinity_entries = skill.elements or (
                (skill.element,) if skill.element else ()
            )
            element_tokens: list[str] = []
            if affinity_entries:
                for entry in affinity_entries:
                    if entry.is_mixed:
                        main_label = ansi_colour(entry.display_name, "white", bold=True)
                        component_names = " + ".join(
                            _ansi_affinity_name(component, bold=False)
                            for component in entry.components
                        )
                        label = (
                            f"{main_label} ({component_names})"
                            if component_names
                            else main_label
                        )
                        element_tokens.append(label)
                        continue
                    element_tokens.append(_ansi_affinity_name(entry))
            else:
                element_tokens.append(ansi_colour("Neutral", "gray", dim=True))

            skill_damage_type = skill.skill_type
            if isinstance(skill_damage_type, DamageType):
                damage_label = skill_damage_type.value.title()
            else:
                damage_label = str(skill_damage_type).replace("_", " ").title()
            damage_text = _format_percentage_precise(skill.damage_ratio)
            display_name = self._skill_display_name(skill)
            field_name = "\u200b"
            title_line = ansi_colour(display_name, "white", bold=True)
            stars = self._skill_grade_stars(skill.grade)
            grade_line = ansi_colour(stars, "yellow", bold=True)
            weapon_type = skill.weapon or WeaponType.BARE_HAND
            if not isinstance(weapon_type, WeaponType):
                try:
                    weapon_type = WeaponType.from_value(weapon_type, default=WeaponType.BARE_HAND)
                except ValueError:
                    weapon_type = WeaponType.BARE_HAND
            weapon_label = WEAPON_TYPE_LABELS.get(weapon_type, weapon_type.value.title())
            weapon_colour = SECTION_COLOURS["inventory"]
            if weapon_type not in equipped_weapon_types:
                weapon_label = f"{weapon_label} {INCORRECT_ICON}"
                weapon_colour = SECTION_COLOURS["error"]
            proficiency_ceiling = max(1, skill.proficiency_max)
            proficiency_ratio = prof / proficiency_ceiling
            proficiency_cap = skill.proficiency_max or proficiency_ceiling
            proficiency_value = (
                f"{format_number(prof)}/{format_number(proficiency_cap)} "
                f"({_format_percentage(proficiency_ratio)})"
            )
            description_line = (
                ansi_colour(
                    skill.description.strip(), "gray", dim=True, italic=True
                )
                if skill.description and skill.description.strip()
                else None
            )
            value_lines: list[str] = [title_line, grade_line]
            if description_line:
                value_lines.append(description_line)
            value_lines.append(
                ansi_coloured_pair(
                    "Type",
                    damage_label,
                    label_colour=SECTION_COLOURS["combat"],
                    value_colour=SECTION_COLOURS["combat"],
                    bold_value=True,
                )
            )
            value_lines.append(
                ansi_coloured_pair(
                    "Damage",
                    damage_text,
                    label_colour=SECTION_COLOURS["combat"],
                    value_colour=SECTION_COLOURS["progress"],
                    bold_value=True,
                )
            )
            element_heading = ansi_colour(
                "Element", SECTION_COLOURS["guide"], bold=True
            )
            element_value = " / ".join(element_tokens)
            value_lines.append(f"{element_heading}: {element_value}")
            value_lines.append(
                ansi_coloured_pair(
                    "Weapon",
                    weapon_label,
                    label_colour=SECTION_COLOURS["inventory"],
                    value_colour=weapon_colour,
                    bold_value=True,
                    dim_value=False,
                )
            )
            value_lines.append(
                ansi_coloured_pair(
                    "Proficiency",
                    proficiency_value,
                    label_colour=SECTION_COLOURS["progress"],
                    value_colour=SECTION_COLOURS["progress"],
                    bold_value=True,
                )
            )
            entries.append((skill, field_name, ansi_block(value_lines)))
        return entries

    def _skill_summary_blocks(
        self,
        player: PlayerProgress,
        base: InnateSoulSet | None,
        *,
        entries: Sequence[tuple[Skill, str, str]] | None = None,
    ) -> list[tuple[str, str]]:
        if entries is None:
            entries = self._skill_entries(
                player, base, category=SkillCategory.ACTIVE
            )
        if not entries:
            return [("\u200b", coloured("No combat techniques learned.", dim=True))]

        def grade_sort_key(entry: tuple[Skill, str, str]) -> tuple[int, str, str]:
            skill, _, _ = entry
            grade_value = skill_grade_number(skill.grade)
            sort_grade = grade_value if grade_value is not None else -1
            return (-sort_grade, (skill.grade or "").lower(), skill.name.lower())

        sorted_entries = sorted(entries, key=grade_sort_key)

        entry_texts: list[str] = []
        for _, heading, body in sorted_entries:
            if heading == "\u200b":
                entry_texts.append(body)
            else:
                entry_texts.append(f"{heading}\n{body}")
        chunks: list[str] = []
        current_parts: list[str] = []
        current_length = 0
        for entry_text in entry_texts:
            entry_length = len(entry_text)
            spacer = 2 if current_parts else 0
            if current_parts and current_length + spacer + entry_length > 1024:
                chunks.append("\n\n".join(current_parts))
                current_parts = [entry_text]
                current_length = len(entry_text)
                continue
            if current_parts:
                current_length += spacer
            current_parts.append(entry_text)
            current_length += entry_length
        if current_parts:
            chunks.append("\n\n".join(current_parts))

        blocks: list[tuple[str, str]] = []
        base_heading = "\u200b"
        total_chunks = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            chunk_value = chunk
            if total_chunks > 1:
                page_label = ansi_colour(
                    f"Page {index}/{total_chunks}",
                    SECTION_COLOURS["guide"],
                    bold=True,
                )
                chunk_value = f"{page_label}\n\n{chunk}"
            blocks.append((base_heading, chunk_value))
        return blocks

    def _technique_entry_blocks(
        self, player: PlayerProgress, trait_objects: Sequence[SpecialTrait]
    ) -> list[tuple[str, list[str]]]:
        if not player.cultivation_technique_keys:
            return []

        base = player.combined_innate_soul(trait_objects)
        entries: list[tuple[str, list[str]]] = []
        for key in player.cultivation_technique_keys:
            technique = self.state.cultivation_techniques.get(key)
            if not technique:
                heading = f"{key} (unconfigured)"
                entries.append(
                    (heading, [coloured("Technique data missing.", dim=True)])
                )
                continue

            path_value = CultivationPath.from_value(technique.path)
            effect_text, stat_details = _technique_effect_details(technique, path_value)
            status_active = technique.is_affinity_compatible(base)
            affinity_label: str | None = None
            skill_names = [
                self.state.skills.get(skill_key).name
                if skill_key in self.state.skills
                else skill_key
                for skill_key in technique.skills
            ]
            stars = self._skill_grade_stars(technique.grade)
            grade_line = ansi_colour(stars, "yellow", bold=True)
            heading = technique.name
            description_line = (
                ansi_colour(
                    technique.description.strip(), "gray", dim=True, italic=True
                )
                if technique.description and technique.description.strip()
                else None
            )

            if technique.affinity:
                affinity_label = (
                    technique.affinity.display_name
                    if isinstance(technique.affinity, SpiritualAffinity)
                    else str(technique.affinity).replace("_", " ").title()
                )

            details: list[str] = [grade_line]
            if description_line:
                details.append(description_line)
            details.append(
                ansi_coloured_pair(
                    "Effect",
                    effect_text,
                    label_colour=SECTION_COLOURS["cultivation"],
                    value_colour="white",
                )
            )
            if stat_details:
                details.append(
                    ansi_coloured_pair(
                        "Stats",
                        ", ".join(stat_details),
                        label_colour=SECTION_COLOURS["stats"],
                        value_colour="white",
                    )
                )
            if affinity_label:
                details.append(
                    ansi_coloured_pair(
                        "Affinity",
                        affinity_label,
                        label_colour=SECTION_COLOURS["cultivation"],
                        value_colour="white",
                    )
                )
            if skill_names:
                details.append(
                    ansi_coloured_pair(
                        "Grants",
                        ", ".join(skill_names),
                        label_colour=SECTION_COLOURS["combat"],
                        value_colour="white",
                    )
                )
            if status_active:
                status_text = "Active"
                status_colour = "green"
            else:
                status_text = (
                    f"Requires {affinity_label} affinity"
                    if affinity_label
                    else "Unavailable"
                )
                status_colour = "red"
            details.append(
                ansi_coloured_pair(
                    "Status",
                    status_text,
                    label_colour=SECTION_COLOURS["cultivation"],
                    value_colour=status_colour,
                    bold_value=True,
                )
            )
            entries.append((heading, details))
        return entries

    def _technique_summary_lines(
        self, player: PlayerProgress, trait_objects: Sequence[SpecialTrait]
    ) -> list[str]:
        entries = self._technique_entry_blocks(player, trait_objects)
        if not entries:
            return [coloured("No cultivation techniques mastered.", dim=True)]

        lines: list[str] = []
        for heading, detail_lines in entries:
            block_lines = [f"• {heading}"]
            block_lines.extend(f"  {line}" for line in detail_lines)
            lines.append(coloured_block(block_lines))
        return lines

    def _equipment_summary_lines(self, player: PlayerProgress) -> list[str]:
        lines: list[str] = []
        display_slots = [
            EquipmentSlot.NECKLACE,
            EquipmentSlot.ARMOR,
            EquipmentSlot.BELT,
            EquipmentSlot.BOOTS,
            EquipmentSlot.ACCESSORY,
            EquipmentSlot.WEAPON,
        ]
        for slot in display_slots:
            capacity = equipment_slot_capacity(slot)
            usage = equipment_slot_usage(player, slot, self.state.items)
            keys = player.equipment.get(slot.value, [])
            names: list[str] = []
            for key in keys:
                item = self.state.items.get(key)
                label = item.name if item else key
                if (
                    slot is EquipmentSlot.WEAPON
                    and item is not None
                    and getattr(item, "slots_required", 1) > 1
                ):
                    label = f"{label} ({format_number(item.slots_required)}-slot)"
                names.append(label)
            if not names:
                if slot is EquipmentSlot.WEAPON:
                    names.append(coloured("Bare-handed", dim=True))
                else:
                    names.append(coloured("Empty", dim=True))
            label = EQUIPMENT_SLOT_LABELS.get(slot, slot.value.title())
            if capacity:
                prefix = (
                    f"{label} [{format_number(usage)}/{format_number(capacity)}]"
                )
            else:
                prefix = label
            lines.append(f"{prefix}: {', '.join(names)}")
        return lines

    def _stage_display(self, stage: CultivationStage | None) -> str:
        if stage is None:
            return "Unconfigured"
        return stage.combined_name

    async def _fetch_player(self, guild_id: int, user_id: int) -> Optional[PlayerProgress]:
        await self.ensure_guild_loaded(guild_id)
        revision = await self.store.get_player_revision(guild_id, user_id)
        cached = self._get_cached_player(guild_id, user_id, revision)
        if cached is not None:
            return cached
        data = await self.store.get_player(guild_id, user_id)
        if not data:
            self._player_cache.pop((guild_id, user_id), None)
            return None
        player = PlayerProgress(**data)
        self.state.register_player(player)
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(user_id) if guild else None
        if member and member.display_name:
            display_name = member.display_name
            if display_name != player.name:
                player.name = display_name
                await self._save_player(guild_id, player)
                return copy.deepcopy(player)
        revision = await self.store.get_player_revision(guild_id, user_id)
        self._cache_player(guild_id, player, revision)
        return player

    async def _save_player(self, guild_id: int, player: PlayerProgress) -> None:
        await self.store.upsert_player(guild_id, asdict(player))
        revision = await self.store.get_player_revision(guild_id, player.user_id)
        self._cache_player(guild_id, player, revision)

    def _safe_zone_service_lines(self, location: Location) -> list[str]:
        lines: list[str] = []
        for npc in location.npcs:
            if npc.npc_type is LocationNPCType.SHOP:
                prefix = "[Shop]"
            elif npc.npc_type is LocationNPCType.ENEMY:
                prefix = "[Enemy]"
            else:
                prefix = "[Dialogue]"
            label = coloured(npc.name, bold=True)
            detail = npc.description or "Available for a brief interaction."
            if npc.npc_type is LocationNPCType.SHOP and npc.shop_items:
                detail = (
                    npc.description
                    or f"Offers {len(npc.shop_items)} curated wares."
                )
            elif npc.npc_type is LocationNPCType.DIALOG and npc.dialogue:
                detail = npc.description or npc.dialogue[:120]
            lines.append(f"{prefix} {label} — {coloured(detail, dim=True)}")
        return lines

    async def _open_safe_zone_shop(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        npc: LocationNPC,
        *,
        player_id: int,
        menu_view: SafeZoneActionView | None = None,
        menu_embed_builder: Callable[[], discord.Embed] | None = None,
    ) -> None:
        economy_cog = self.bot.get_cog("EconomyCog")
        if economy_cog is None:
            embed = _make_embed(
                "error",
                f"{npc.name}'s Shop",
                "The marketplace is currently closed.",
                discord.Colour.red(),
            )
            if menu_view is not None:
                await interaction.response.edit_message(embed=embed, view=menu_view)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        player = await self._fetch_player(guild_id, player_id)
        if not player:
            embed = _make_embed(
                "warning",
                f"{npc.name}'s Shop",
                "Register first with /register before visiting the shop.",
                discord.Colour.gold(),
            )
            if menu_view is not None:
                await interaction.response.edit_message(embed=embed, view=menu_view)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        entries: dict[str, ShopEntry] = {}
        for item_key in npc.shop_items:
            shop_item = self.state.shop_items.get(item_key)
            if not shop_item:
                continue
            item = self.state.items.get(shop_item.item_key)
            currency = self.state.currencies.get(shop_item.currency_key)
            if not item or not currency:
                continue
            entries[item_key] = ShopEntry(
                key=item_key,
                name=item.name,
                description=item.description or "No description provided.",
                price=shop_item.price,
                currency_key=shop_item.currency_key,
                currency_name=currency.name,
            )
        if not entries:
            embed = _make_embed(
                "warning",
                f"{npc.name}'s Shop",
                f"{npc.name} has no wares configured yet.",
                discord.Colour.gold(),
            )
            if menu_view is not None:
                await interaction.response.edit_message(embed=embed, view=menu_view)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        async def handle_purchase(
            purchase_interaction: discord.Interaction,
            view: ShopView,
            selected_key: str,
            amount: int,
        ) -> ShopPurchaseResult:
            refreshed_cog = self.bot.get_cog("EconomyCog")
            if refreshed_cog is None:
                return ShopPurchaseResult(False, "The marketplace is currently closed.")
            if not hasattr(refreshed_cog, "_execute_purchase"):
                return ShopPurchaseResult(False, "Purchasing is not available.")
            if view.player is None:
                return ShopPurchaseResult(False, "Register first with /register.")
            exec_fn = getattr(refreshed_cog, "_execute_purchase")
            return await exec_fn(guild_id, view.player, selected_key, amount)

        view = ShopView(entries, player=player, purchase_callback=handle_purchase)
        embed = view.build_embed()
        embed.title = f"{npc.name}'s Shop"
        if menu_view is not None:
            menu_view.message = None
            if menu_embed_builder is not None:

                async def restore_menu(return_interaction: discord.Interaction) -> None:
                    if return_interaction.user.id != player_id:
                        await return_interaction.response.send_message(
                            "Only the wandering cultivator may interact with this menu.",
                            ephemeral=True,
                        )
                        return
                    menu_embed = menu_embed_builder()
                    view.message = None
                    view.set_return_callback(None)
                    menu_view.message = return_interaction.message
                    await return_interaction.response.edit_message(
                        embed=menu_embed,
                        view=menu_view,
                    )

                view.set_return_callback(restore_menu)
            await interaction.response.edit_message(embed=embed, view=view)
            view.message = interaction.message
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            try:
                view.message = await interaction.original_response()
            except discord.HTTPException:
                view.message = None

    async def _speak_with_safe_zone_npc(
        self,
        interaction: discord.Interaction,
        npc: LocationNPC,
        *,
        view: SafeZoneActionView | None = None,
    ) -> None:
        dialogue = npc.dialogue or npc.description or "They have nothing to say right now."
        embed = _make_embed(
            "guide",
            f"Conversation — {npc.name}",
            dialogue,
            discord.Colour.blurple(),
        )
        if view is not None:
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _handle_safe_zone_hostile(
        self,
        interaction: discord.Interaction,
        npc: LocationNPC,
        *,
        view: SafeZoneActionView | None = None,
    ) -> None:
        detail = npc.description or "Combats are forbidden within the sanctuary."
        if view is not None:
            embed = _make_embed(
                "warning",
                f"Challenge — {npc.name}",
                f"{npc.name} cannot be challenged here. {detail}",
                discord.Colour.orange(),
            )
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            await interaction.response.send_message(
                f"{npc.name} cannot be challenged here. {detail}", ephemeral=True
            )

    async def _handle_safe_zone_wander(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        player: PlayerProgress,
        location: Location,
        *,
        return_callback: SimpleCallback | None = None,
    ) -> None:
        previous_hp = player.current_hp
        previous_soul = player.current_soul_hp
        previous_safe_zone = player.last_safe_zone
        player.last_safe_zone = location.location_id
        player.restore_full_health()

        def build_safe_zone_embed() -> discord.Embed:
            description_lines: list[str] = []
            if location.description:
                description_lines.append(location.description)
            description_lines.append(
                "No hostile encounters occur within this sanctuary. Choose an activity below."
            )
            base_embed = _make_embed(
                "guide",
                f"Safe Haven — {location.name}",
                "\n".join(description_lines),
                discord.Colour.from_str("#5865f2"),
            )
            if location.npcs:
                services = self._safe_zone_service_lines(location)
                base_embed.add_field(
                    name=_label_with_icon("guide", "Available Services"),
                    value=coloured_block(services),
                    inline=False,
                )
            else:
                base_embed.add_field(
                    name=_label_with_icon("guide", "Available Services"),
                    value=coloured("No services configured yet.", dim=True),
                    inline=False,
                )
            return base_embed

        embed = build_safe_zone_embed()
        if (
            previous_hp != player.current_hp
            or previous_soul != player.current_soul_hp
            or previous_safe_zone != player.last_safe_zone
        ):
            embed.add_field(
                name=_label_with_icon("talent", "Rejuvenation"),
                value=(
                    "Your wounds knit together and your spirit steadies within the sanctuary's wards."
                ),
                inline=False,
            )
            await self._save_player(guild.id, player)
        if location.npcs:
            view = SafeZoneActionView(
                location.npcs,
                return_callback=return_callback,
            )

            async def handle_selection(
                component_interaction: discord.Interaction, selected_npc: LocationNPC
            ) -> None:
                if component_interaction.user.id != player.user_id:
                    await component_interaction.response.send_message(
                        "Only the wandering cultivator may interact with this menu.",
                        ephemeral=True,
                    )
                    return
                if selected_npc.npc_type is LocationNPCType.SHOP:
                    await self._open_safe_zone_shop(
                        component_interaction,
                        guild.id,
                        selected_npc,
                        player_id=player.user_id,
                        menu_view=view,
                        menu_embed_builder=build_safe_zone_embed,
                    )
                elif selected_npc.npc_type is LocationNPCType.DIALOG:
                    await self._speak_with_safe_zone_npc(
                        component_interaction, selected_npc, view=view
                    )
                else:
                    await self._handle_safe_zone_hostile(
                        component_interaction, selected_npc, view=view
                    )

            async def handle_close(close_interaction: discord.Interaction) -> None:
                if close_interaction.response.is_done():
                    return
                await close_interaction.response.send_message(
                    "You step away from the sanctuary amenities.", ephemeral=True
                )

            view.set_callback(handle_selection)
            view.set_close_callback(handle_close)
            await self._respond_interaction(
                interaction,
                embed=embed,
                view=view,
            )
            try:
                if interaction.type is InteractionType.component:
                    view.message = interaction.message
                else:
                    view.message = await interaction.original_response()
            except discord.HTTPException:
                view.message = None
            return

        await self._respond_interaction(
            interaction,
            embed=embed,
            view=self._build_return_view(interaction, return_callback),
        )

    async def _resolve_member(self, guild: discord.Guild, user: discord.abc.User) -> discord.Member:
        if isinstance(user, discord.Member):
            return user
        return await guild.fetch_member(user.id)

    def _choose_race_for_member(
        self, member: discord.Member, races: Sequence[Race]
    ) -> tuple[Race | None, str | None, str]:
        if not races:
            return None, "No races have been configured yet.", "missing"
        race_roles = [race for race in races if race.role_id]
        matching_roles: list[Race] = []
        member_role_ids = {role.id for role in getattr(member, "roles", [])}
        for race in race_roles:
            if race.role_id in member_role_ids:
                matching_roles.append(race)
        if matching_roles:
            if len(matching_roles) == 1:
                return matching_roles[0], None, "role-match"
            names = ", ".join(race.name or race.key for race in matching_roles)
            return (
                None,
                (
                    "Multiple race roles detected. Remove extra race roles before "
                    f"registering: {names}."
                ),
                "multiple-role",
            )
        open_races = [race for race in races if not race.role_id]
        if len(open_races) == 1:
            return open_races[0], None, "open-single"
        if len(open_races) > 1:
            return (
                None,
                (
                    "Multiple races are available. Ask an administrator to assign a "
                    "race role before registering."
                ),
                "ambiguous",
            )
        return (
            None,
            "No race role is assigned to you yet. Ask an administrator for assistance.",
            "unassigned",
        )

    async def _sync_race_roles(
        self, guild: discord.Guild, member: discord.Member, target: Race | None
    ) -> None:
        configured_ids = {
            race.role_id for race in self.state.races.values() if race.role_id
        }
        target_role = guild.get_role(target.role_id) if target and target.role_id else None
        updates: list[tuple[str, discord.Role]] = []
        if target_role and target_role not in member.roles:
            updates.append(("add", target_role))
        for role_id in configured_ids:
            if target and target.role_id == role_id:
                continue
            role = guild.get_role(role_id)
            if role and role in member.roles:
                updates.append(("remove", role))
        if not updates:
            return
        for action, role in updates:
            try:
                if action == "add":
                    await member.add_roles(role, reason="Race alignment update")
                else:
                    await member.remove_roles(role, reason="Race alignment update")
            except discord.Forbidden:
                self.log.warning(
                    "Missing permissions to adjust race role %s in guild %s",
                    role.id,
                    guild.id,
                )
            except discord.HTTPException as exc:
                self.log.warning(
                    "Failed to adjust race role %s in guild %s: %s",
                    role.id,
                    guild.id,
                    exc,
                )

    async def _sync_trait_roles(
        self, guild: discord.Guild, member: discord.Member, trait_keys: Sequence[str]
    ) -> None:
        configured_ids = {
            trait.role_id
            for trait in self.state.traits.values()
            if getattr(trait, "role_id", None)
        }
        if not configured_ids:
            return
        target_ids = {
            self.state.traits[key].role_id
            for key in trait_keys
            if key in self.state.traits and self.state.traits[key].role_id
        }
        updates: list[tuple[str, discord.Role]] = []
        for role_id in target_ids:
            if role_id is None:
                continue
            role = guild.get_role(role_id)
            if role and role not in member.roles:
                updates.append(("add", role))
        for role_id in configured_ids:
            if role_id is None or role_id in target_ids:
                continue
            role = guild.get_role(role_id)
            if role and role in member.roles:
                updates.append(("remove", role))
        if not updates:
            return
        for action, role in updates:
            try:
                if action == "add":
                    await member.add_roles(role, reason="Trait alignment update")
                else:
                    await member.remove_roles(role, reason="Trait alignment update")
            except discord.Forbidden:
                self.log.warning(
                    "Missing permissions to adjust trait role %s in guild %s",
                    role.id,
                    guild.id,
                )
            except discord.HTTPException as exc:
                self.log.warning(
                    "Failed to adjust trait role %s in guild %s: %s",
                    role.id,
                    guild.id,
                    exc,
                )

    def _stages_for_path(self, path: CultivationPath) -> Sequence[CultivationStage]:
        if path is CultivationPath.BODY:
            return list(self.state.body_cultivation_stages.values())
        if path is CultivationPath.SOUL:
            return list(self.state.soul_cultivation_stages.values())
        return list(self.state.qi_cultivation_stages.values())

    def _resolve_display_stage(
        self,
        player: PlayerProgress,
        path: CultivationPath,
        actual_stage: CultivationStage | None,
    ) -> CultivationStage | None:
        overrides = player.path_role_overrides
        override_key = overrides.get(path.value) if overrides else None
        if override_key and actual_stage is not None:
            candidate = self.state.get_stage(override_key, path)
            if candidate and candidate.ordering_tuple <= actual_stage.ordering_tuple:
                return candidate
        return actual_stage

    async def _sync_player_path_roles(
        self,
        guild: discord.Guild,
        member: discord.Member,
        player: PlayerProgress,
    ) -> None:
        active_path = CultivationPath.from_value(player.active_path)
        for path in CultivationPath:
            stage_key = player.stage_key_for_path(path) if path is active_path else None
            if stage_key:
                target_stage = self.state.get_stage(stage_key, path)
            else:
                target_stage = None
            display_stage = self._resolve_display_stage(player, path, target_stage)
            await self._apply_display_stage_role(guild, member, path, display_stage)

    async def _apply_display_stage_role(
        self,
        guild: discord.Guild,
        member: discord.Member,
        path: CultivationPath,
        stage: CultivationStage | None,
    ) -> None:
        target_role_id = stage.role_id if stage and stage.role_id else None
        for candidate in self._stages_for_path(path):
            role_id = getattr(candidate, "role_id", None)
            if not role_id:
                continue
            role = guild.get_role(role_id)
            if role is None:
                continue
            try:
                if role_id == target_role_id and role not in member.roles:
                    await member.add_roles(role, reason="Cultivation path display update")
                elif role_id != target_role_id and role in member.roles:
                    await member.remove_roles(role, reason="Cultivation path display update")
            except discord.Forbidden:
                self.log.warning(
                    "Missing permissions to adjust stage role %s in guild %s", role.id, guild.id
                )
            except discord.HTTPException as exc:
                self.log.warning(
                    "Failed to adjust stage role %s in guild %s: %s", role.id, guild.id, exc
                )

    async def _handle_cultivation_interaction(
        self,
        interaction: discord.Interaction,
        *,
        ephemeral: bool,
        player: PlayerProgress | None = None,
        trait_objects: Sequence[SpecialTrait] | None = None,
        combined_base: InnateSoulSet | None = None,
        return_callback: SimpleCallback | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed("error",
                "Action Not Available",
                "This action must be performed within a server.",
                discord.Colour.red(),
            )
            await self._respond_interaction(
                interaction, embed=embed, ephemeral=ephemeral
            )
            return

        if player is None:
            player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed("warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(
                interaction, embed=embed, ephemeral=ephemeral
            )
            return

        if player.active_path != CultivationPath.QI.value:
            embed = _make_embed(
                "warning",
                "Path Not Active",
                "Align with the Qi Path before attempting to cultivate.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(
                interaction, embed=embed, ephemeral=ephemeral
            )
            return

        if trait_objects is None:
            trait_list = self._trait_objects(player)
        else:
            trait_list = list(trait_objects)
        if combined_base is None:
            combined_base = player.combined_innate_soul(trait_list)

        qi_stage = self.state.get_stage(player.cultivation_stage, CultivationPath.QI)
        if qi_stage is None:
            await self._respond_interaction(
                interaction,
                content="Your current cultivation stage is no longer configured.",
                ephemeral=True,
            )
            return
        body_stage = self.state.get_stage(
            player.body_cultivation_stage, CultivationPath.BODY
        )
        soul_stage = self.state.get_stage(
            player.soul_cultivation_stage, CultivationPath.SOUL
        )
        race = self.state.races.get(player.race_key) if player.race_key else None
        equipped_items = equipped_items_for_player(player, self.state.items)
        effective_stats = player.effective_stats(
            qi_stage,
            body_stage,
            soul_stage,
            race,
            trait_list,
            equipped_items,
        )
        effective_stats.add_in_place(self.passive_skill_bonus(player))
        effective_base = combined_base or player.combined_innate_soul(trait_list)
        active_martial_souls = player.get_active_martial_souls()
        mortal_stage = _player_in_mortal_realm(player, stage=qi_stage)

        async def _repeat_cultivation(
            inter: discord.Interaction,
        ) -> None:
            refreshed, resolved_traits, resolved_base = await self._resolve_player_context(
                guild,
                inter.user.id,
            )
            if not refreshed:
                await self._respond_interaction(
                    inter, content="Use `/register` first.", ephemeral=True
                )
                return
            await self._handle_cultivation_interaction(
                inter,
                ephemeral=True,
                player=refreshed,
                trait_objects=resolved_traits,
                combined_base=resolved_base,
                return_callback=return_callback,
            )

        async def _attempt_breakthrough(inter: discord.Interaction) -> None:
            await self._handle_breakthrough_flow(
                inter,
                selected_path=CultivationPath.QI,
                ephemeral=True,
            )

        await self._run_cultivation_session(
            interaction,
            ephemeral=ephemeral,
            player=player,
            guild_id=guild.id,
            combined_base=combined_base,
            active_martial_souls=active_martial_souls,
            qi_stage=qi_stage,
            mortal_stage=mortal_stage,
            effective_base=effective_base,
            return_callback=return_callback,
            repeat_callback=_repeat_cultivation,
            breakthrough_callback=_attempt_breakthrough,
        )
        return

    async def _run_cultivation_session(
        self,
        interaction: discord.Interaction,
        *,
        ephemeral: bool,
        player: PlayerProgress,
        guild_id: int,
        combined_base: InnateSoulSet | None,
        active_martial_souls: Sequence[MartialSoul],
        qi_stage: CultivationStage,
        mortal_stage: bool,
        effective_base: InnateSoulSet | None,
        return_callback: SimpleCallback | None,
        repeat_callback: SimpleCallback,
        breakthrough_callback: SimpleCallback | None,
    ) -> None:
        tick_interval = 1
        session_duration = 60
        total_ticks = max(1, session_duration // tick_interval)
        tick_min = getattr(self.config, "cultivation_tick_min", self.config.cultivation_tick)
        tick_max = getattr(self.config, "cultivation_tick_max", self.config.cultivation_tick)
        if tick_min > tick_max:
            tick_min, tick_max = tick_max, tick_min
        tick_min = max(0, int(tick_min))
        tick_max = max(tick_min, int(tick_max))

        realm_display = self._stage_display(qi_stage)
        phase_display = ""
        if qi_stage is not None:
            realm_candidate = qi_stage.realm or qi_stage.name or realm_display
            realm_display = (realm_candidate or realm_display).strip() or realm_display
            phase_display = qi_stage.phase_display
        stage_title = _format_stage_display(realm_display, phase_display)

        affinity_for_flavour = None
        for soul in active_martial_souls:
            if soul.affinities:
                affinity_for_flavour = soul.affinities[0]
                break
        if affinity_for_flavour is None and effective_base:
            affinity_for_flavour = effective_base.affinity
        flavour_line = select_cultivation_flavour(
            affinity_for_flavour, mortal=mortal_stage
        )
        description_line = ansi_colour(
            flavour_line,
            "gray",
            dim=True,
            italic=True,
        )

        before_exp = player.cultivation_exp
        base_total = 0
        session_start = time.monotonic()
        session_end = session_start + session_duration
        thumbnail_url = self._player_avatar_thumbnail_url(interaction, player)

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral, thinking=False)

        cancel_event = asyncio.Event()

        class CultivationSessionView(OwnedView):
            message: discord.Message | None

            def __init__(self) -> None:
                super().__init__(interaction.user.id, timeout=session_duration + 10)
                self.message = None
                self.add_item(_StopCultivationButton())

            def bind_message(self, message: discord.Message) -> None:
                self.message = message

            async def refresh(self) -> None:
                if self.message is None:
                    return
                try:
                    await self.message.edit(view=self)
                except discord.HTTPException:
                    pass

        class _StopCultivationButton(discord.ui.Button["CultivationSessionView"]):
            def __init__(self) -> None:
                super().__init__(
                    style=discord.ButtonStyle.danger,
                    label="Stop Cultivating",
                    emoji="🛑",
                )

            async def callback(self, inter: discord.Interaction) -> None:  # type: ignore[override]
                view = self.view
                if not isinstance(view, CultivationSessionView):
                    await inter.response.send_message(
                        "The cultivation session has already ended.",
                        ephemeral=True,
                    )
                    return
                if cancel_event.is_set():
                    await inter.response.send_message(
                        "The cultivation session is already ending.",
                        ephemeral=True,
                    )
                    return
                cancel_event.set()
                for child in view.children:
                    child.disabled = True
                if not inter.response.is_done():
                    try:
                        await inter.response.defer(ephemeral=True)
                    except discord.HTTPException:
                        pass
                await view.refresh()

        def build_progress_embed(
            ticks_completed: int, last_gain: int, stopped: bool
        ) -> discord.Embed:
            remaining_seconds = max(0, int(round(session_end - time.monotonic())))
            minutes, seconds = divmod(remaining_seconds, 60)

            if stopped:
                timer_line = "Session halted"
            elif ticks_completed >= total_ticks:
                timer_line = "Session complete"
            else:
                timer_line = f"{minutes:02d}:{seconds:02d} remaining"

            summary_lines: list[str] = [timer_line]
            summary_lines.append(
                f"Progress: {format_number(player.cultivation_exp)}/{format_number(player.cultivation_exp_required)}"
            )
            summary_lines.append(
                progress_bar(player.cultivation_exp, player.cultivation_exp_required)
            )

            indicator_text = breakthrough_ready_indicator(
                player.cultivation_exp, player.cultivation_exp_required
            )
            if indicator_text:
                summary_lines.append(
                    ansi_colour(
                        indicator_text,
                        SECTION_COLOURS["progress"],
                        bold=True,
                    )
                )

            if ticks_completed > 0:
                total_text = f"+{format_number(last_gain)} EXP"
                total_text = f"{total_text} (+{format_number(base_total)} EXP in total)"
                summary_lines.append("")
                summary_lines.append(total_text)
                summary_lines.append("")

            summary_lines.append(description_line)

            embed = _make_embed(
                "cultivation",
                "CULTIVATING...",
                ansi_block(summary_lines),
                discord.Colour.purple(),
            )
            self._apply_avatar_portrait(embed, thumbnail_url)
            return embed

        session_view = CultivationSessionView()

        try:
            await interaction.edit_original_response(
                embed=build_progress_embed(0, 0, False), view=session_view
            )
            try:
                session_view.bind_message(await interaction.original_response())
            except (discord.HTTPException, discord.NotFound):
                pass
        except discord.HTTPException as exc:
            self.log.debug(
                "Failed to push initial cultivation session state for %s: %s",
                player.user_id,
                exc,
            )

        multiplier_ranges = self.state.innate_soul_exp_multiplier_ranges
        ticks_completed = 0
        last_gain = 0
        session_cancelled = False
        for second in range(1, session_duration + 1):
            target_time = session_start + second
            sleep_for = max(0.0, target_time - time.monotonic())
            if sleep_for:
                await asyncio.sleep(sleep_for)
            session_cancelled = cancel_event.is_set()
            if not session_cancelled and second % tick_interval == 0:
                before_tick = player.cultivation_exp
                gain_seed = random.randint(tick_min, tick_max)
                perform_cultivation(
                    player,
                    gain_seed,
                    multiplier_ranges=multiplier_ranges,
                )
                tick_gain = player.cultivation_exp - before_tick
                base_total += tick_gain
                last_gain = tick_gain
                ticks_completed += 1
            try:
                await interaction.edit_original_response(
                    embed=build_progress_embed(ticks_completed, last_gain, session_cancelled),
                    view=session_view,
                )
            except discord.HTTPException as exc:
                self.log.debug(
                    "Failed to update cultivation session tick for %s: %s",
                    player.user_id,
                    exc,
                )
            if session_cancelled:
                break

        session_cancelled = session_cancelled or cancel_event.is_set()
        session_view.stop()

        technique_bonus = 0
        bonus_sources: list[str] = []
        if mortal_stage:
            gained = max(base_total, 10)
            if gained != base_total:
                player.cultivation_exp = before_exp + gained
                base_total = gained
        else:
            gained, technique_bonus, bonus_sources = self._apply_cultivation_technique_bonus(
                player,
                base_gain=base_total,
                path=CultivationPath.QI,
                combined_base=combined_base,
            )

        await self._save_player(guild_id, player)

        indicator_text = breakthrough_ready_indicator(
            player.cultivation_exp, player.cultivation_exp_required
        )

        summary_lines: list[str] = []
        if session_cancelled:
            summary_lines.append(f"Realm: {realm_display}")
        else:
            summary_lines.append(f"Realm: {realm_display}")
            if phase_display:
                summary_lines.append(f"Stage: {phase_display}")
            session_descriptor = f"{ticks_completed}/{total_ticks} ticks × {tick_interval}s"
            if ticks_completed == total_ticks:
                session_descriptor = f"{total_ticks} ticks × {tick_interval}s"
            range_descriptor = f"{format_number(tick_min)}–{format_number(tick_max)} base"
            summary_lines.append(f"Session: {session_descriptor} ({range_descriptor})")

        summary_lines.append(
            f"Progress: {format_number(player.cultivation_exp)}/{format_number(player.cultivation_exp_required)}"
        )
        summary_lines.append(
            progress_bar(player.cultivation_exp, player.cultivation_exp_required)
        )
        if indicator_text:
            summary_lines.append(
                ansi_colour(
                    indicator_text,
                    SECTION_COLOURS["progress"],
                    bold=True,
                )
            )

        if session_cancelled:
            summary_lines.append(
                f"+{format_number(base_total)} EXP (+{format_number(gained)} EXP in Total)"
            )
        else:
            summary_lines.append(f"Base Yield: +{format_number(base_total)} EXP")
            if technique_bonus > 0:
                source_text = ", ".join(bonus_sources) if bonus_sources else "Techniques"
                summary_lines.append(
                    f"Technique Bonus: +{format_number(technique_bonus)} EXP ({source_text})"
                )
            if gained > 0:
                summary_lines.append(f"+{format_number(gained)} Cultivation EXP")

        summary_lines.append("")
        summary_lines.append(description_line)

        final_title = "Cultivation Halted" if session_cancelled else "Cultivation Session Complete"
        final_embed = _make_embed(
            "cultivation",
            final_title,
            ansi_block(summary_lines),
            discord.Colour.purple(),
        )
        self._apply_avatar_portrait(final_embed, thumbnail_url)

        ready_for_breakthrough = (
            player.cultivation_exp_required > 0
            and player.cultivation_exp >= player.cultivation_exp_required
        )

        view = CultivationActionView(
            interaction.user.id,
            return_callback=return_callback,
            on_cultivate_qi=repeat_callback,
            on_breakthrough=(
                breakthrough_callback if ready_for_breakthrough else None
            ),
        )

        try:
            await interaction.edit_original_response(embed=final_embed, view=view)
        except discord.HTTPException as exc:
            self.log.warning(
                "Failed to finalise cultivation session for %s: %s",
                player.user_id,
                exc,
            )

    async def _handle_cooperative_cultivation(
        self,
        interaction: discord.Interaction,
        *,
        ephemeral: bool,
        player: PlayerProgress,
        partner: PlayerProgress,
        player_traits: Sequence[SpecialTrait] | None,
        partner_traits: Sequence[SpecialTrait] | None,
        player_base: InnateSoulSet | None,
        partner_base: InnateSoulSet | None,
        bond: BondProfile,
        return_callback: SimpleCallback | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed(
                "error",
                "Action Not Available",
                "This action must be performed within a server.",
                discord.Colour.red(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=ephemeral)
            return

        if not self.state.bond_conditions_met(player, partner, bond):
            embed = _make_embed(
                "warning",
                "Bond Requirements Not Met",
                (
                    "Both cultivators must share the bond, active path, "
                    "and minimum realm before harmonising their sessions."
                ),
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=ephemeral)
            return

        if player_traits is None:
            player_traits = self._trait_objects(player)
        if partner_traits is None:
            partner_traits = self._trait_objects(partner)
        if player_base is None:
            player_base = player.combined_innate_soul(player_traits)
        if partner_base is None:
            partner_base = partner.combined_innate_soul(partner_traits)

        shared_now = time.time()
        before_player = player.cultivation_exp
        before_partner = partner.cultivation_exp
        perform_cultivation(
            player,
            self.config.cultivation_tick,
            multiplier_ranges=self.state.innate_soul_exp_multiplier_ranges,
            now=shared_now,
        )
        perform_cultivation(
            partner,
            self.config.cultivation_tick,
            multiplier_ranges=self.state.innate_soul_exp_multiplier_ranges,
            now=shared_now,
        )

        base_gain_player = player.cultivation_exp - before_player
        base_gain_partner = partner.cultivation_exp - before_partner

        final_gain_player, technique_bonus_player, technique_sources_player = (
            self._apply_cultivation_technique_bonus(
                player,
                base_gain=base_gain_player,
                path=CultivationPath.QI,
                combined_base=player_base,
            )
        )
        final_gain_partner, technique_bonus_partner, technique_sources_partner = (
            self._apply_cultivation_technique_bonus(
                partner,
                base_gain=base_gain_partner,
                path=CultivationPath.QI,
                combined_base=partner_base,
            )
        )

        qi_stage_player = self.state.get_stage(
            player.cultivation_stage, CultivationPath.QI
        )
        qi_stage_partner = self.state.get_stage(
            partner.cultivation_stage, CultivationPath.QI
        )
        player_mortal = qi_stage_player.is_mortal if qi_stage_player else False
        partner_mortal = qi_stage_partner.is_mortal if qi_stage_partner else False

        player_bond_bonus, partner_bond_bonus = self.state.bond_bonuses(
            bond, player, partner, final_gain_player, final_gain_partner
        )
        player_total = final_gain_player
        partner_total = final_gain_partner
        if player_bond_bonus > 0 and not player_mortal:
            player.cultivation_exp += player_bond_bonus
            player_total += player_bond_bonus
        else:
            player_bond_bonus = 0
        if partner_bond_bonus > 0 and not partner_mortal:
            partner.cultivation_exp += partner_bond_bonus
            partner_total += partner_bond_bonus
        else:
            partner_bond_bonus = 0

        if player_mortal:
            player_target = 10
            player.cultivation_exp = before_player + player_target
            player_total = player_target
            final_gain_player = player_target
            technique_bonus_player = 0
        if partner_mortal:
            partner_target = 10
            partner.cultivation_exp = before_partner + partner_target
            partner_total = partner_target
            final_gain_partner = partner_target
            technique_bonus_partner = 0

        await self._save_player(guild.id, player)
        await self._save_player(guild.id, partner)

        stage_label_player = self._stage_display(qi_stage_player)
        stage_label_partner = self._stage_display(qi_stage_partner)

        def _build_summary(
            target: PlayerProgress,
            stage_label: str,
            total_gain: int,
            technique_bonus: int,
            technique_sources: Sequence[str],
            bond_bonus: int,
        ) -> str:
            progress_pct = (
                (target.cultivation_exp / target.cultivation_exp_required) * 100
                if target.cultivation_exp_required
                else 0
            )
            indicator = breakthrough_ready_indicator(
                target.cultivation_exp, target.cultivation_exp_required
            )
            lines = [
                ansi_coloured_pair(
                    "Stage",
                    stage_label,
                    label_colour=SECTION_COLOURS["stage"],
                    value_colour="white",
                    bold_value=True,
                ),
                ansi_coloured_pair(
                    f"{SECTION_ICONS['cultivation']} Gains",
                    f"+{format_number(total_gain)} Cultivation EXP",
                    label_colour=SECTION_COLOURS["cultivation"],
                    value_colour="white",
                    bold_value=True,
                ),
            ]
            if technique_bonus > 0:
                source_text = ", ".join(technique_sources) if technique_sources else "Techniques"
                lines.append(
                    ansi_coloured_pair(
                        f"{SECTION_ICONS['cultivation']} Technique Bonus",
                        f"+{format_number(technique_bonus)} EXP ({source_text})",
                        label_colour=SECTION_COLOURS["cultivation"],
                        value_colour="white",
                    )
                )
            if bond_bonus > 0:
                lines.append(
                    ansi_coloured_pair(
                        f"{SECTION_ICONS['bond']} Bond Bonus",
                        f"+{format_number(bond_bonus)} EXP ({bond.name})",
                        label_colour=SECTION_COLOURS["bond"],
                        value_colour="white",
                    )
                )
            lines.append(
                ansi_coloured_pair(
                    "Progress",
                    f"{format_number(target.cultivation_exp)}/{format_number(target.cultivation_exp_required)}",
                    label_colour=SECTION_COLOURS["progress"],
                    value_colour="white",
                    bold_value=True,
                )
            )
            lines.append(
                progress_bar(target.cultivation_exp, target.cultivation_exp_required)
            )
            if indicator:
                lines.append(
                    ansi_colour(
                        indicator,
                        SECTION_COLOURS["progress"],
                        bold=True,
                    )
                )
            return ansi_block(lines)

        player_summary = _build_summary(
            player,
            stage_label_player,
            player_total,
            technique_bonus_player,
            technique_sources_player,
            player_bond_bonus,
        )
        partner_summary = _build_summary(
            partner,
            stage_label_partner,
            partner_total,
            technique_bonus_partner,
            technique_sources_partner,
            partner_bond_bonus,
        )

        partner_member = guild.get_member(partner.user_id)
        partner_name = (
            partner_member.display_name
            if partner_member and partner_member.display_name
            else partner.name
        )

        description = (
            f"{SECTION_ICONS['bond']} You and {partner_name} synchronise your qi "
            f"through the {bond.name} bond."
        )
        embed = _make_embed(
            "bond",
            "SYNCHRONISED CULTIVATION",
            description,
            discord.Colour.green(),
            include_icon=True,
        )
        self._apply_avatar_portrait(
            embed, self._player_avatar_thumbnail_url(interaction, player)
        )
        embed.add_field(
            name=_label_with_icon("cultivation", player.name, include_icon=True),
            value=player_summary,
            inline=False,
        )
        embed.add_field(
            name=_label_with_icon("cultivation", partner_name, include_icon=True),
            value=partner_summary,
            inline=False,
        )

        view = CultivationActionView(
            interaction.user.id,
            return_callback=return_callback,
        )
        await self._respond_interaction(
            interaction,
            embed=embed,
            view=view,
            ephemeral=ephemeral,
        )

    @app_commands.command(name="register", description="Begin your cultivation journey")
    @app_commands.guild_only()
    @app_commands.describe(
        gender="Select how the sect should address you",
    )
    @app_commands.choices(
        gender=[
            app_commands.Choice(name="Male", value="male"),
            app_commands.Choice(name="Female", value="female"),
        ]
    )
    async def register(
        self,
        interaction: discord.Interaction,
        gender: str,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed("error",
                "Server Only",
                "This command must be used inside a server.",
                discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await self.ensure_guild_loaded(guild.id)
        await self._load_rarity_config(guild.id)
        await self._load_birth_trait_config(guild.id)
        await self._load_birth_race_config(guild.id)
        existing = await self._fetch_player(guild.id, interaction.user.id)
        if existing:
            embed = _make_embed("warning",
                "Already Registered",
                "You are already a cultivator.",
                discord.Colour.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        qi_stage_key = await self._get_stage_key(path=CultivationPath.QI.value)
        if not qi_stage_key:
            embed = _make_embed("error",
                "Missing Configuration",
                "No cultivation stages have been configured yet.",
                discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        member = await self._resolve_member(guild, interaction.user)
        available_races = list(self.state.races.values())
        race_template, race_error, race_status = self._choose_race_for_member(
            member, available_races
        )
        if (
            race_template is None
            and self._birth_race_config_present
            and race_status not in {"multiple-role"}
        ):
            birth_race_key = self._roll_birth_race_key()
            if birth_race_key and birth_race_key in self.state.races:
                race_template = self.state.races[birth_race_key]
                race_error = None
                race_status = "configured"
        if race_template is None:
            embed = _make_embed(
                "error",
                "Race Not Assigned",
                race_error or "You do not meet the requirements for any race.",
                discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        qi_stage = self.state.get_stage(qi_stage_key, CultivationPath.QI)
        if qi_stage is None:
            embed = _make_embed("error",
                "Unknown Stage",
                "The configured qi stage could not be found.",
                discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        def _required_exp(stage: CultivationStage) -> int:
            try:
                return max(1, int(getattr(stage, "exp_required", 100)))
            except (TypeError, ValueError):
                return 100

        cultivation_exp_required = _required_exp(qi_stage)

        innate_stats = roll_talent_stats(
            self.config.innate_stat_min,
            self.config.innate_stat_max,
            dice_count=race_template.innate_dice_count,
            dice_faces=race_template.innate_dice_faces,
            drop_lowest=race_template.innate_drop_lowest,
        )
        martial_souls = roll_martial_souls(
            grade_weights=self._current_innate_soul_grade_weights(),
            count_weights=self._current_innate_soul_count_weights(),
            category="any",
            rng=random.Random(),
        )
        if not martial_souls:
            martial_souls = [MartialSoul.default(category="any")]
        active_soul_names = [martial_souls[0].name]
        player = PlayerProgress(
            user_id=interaction.user.id,
            name=member.display_name,
            cultivation_stage=qi_stage_key,
            active_path=CultivationPath.QI.value,
            body_cultivation_stage=None,
            soul_cultivation_stage=None,
            cultivation_exp=0,
            cultivation_exp_required=cultivation_exp_required,
            combat_exp=0,
            combat_exp_required=0,
            stats=Stats(),
            innate_stats=innate_stats,
            martial_souls=martial_souls,
            primary_martial_soul=martial_souls[0].name,
            active_martial_soul_names=active_soul_names,
            gender=gender or "unspecified",
            soul_exp_required=0,
            race_key=race_template.key,
            unlocked_paths=[CultivationPath.QI.value],
        )
        player.recalculate_stage_stats(qi_stage, None, None)
        player.restore_full_health()

        granted_traits: list[SpecialTrait] = []
        birth_trait_key = self._roll_birth_trait_key()
        if birth_trait_key and birth_trait_key in self.state.traits:
            if birth_trait_key not in player.trait_keys:
                player.trait_keys.append(birth_trait_key)
            granted_traits.append(self.state.traits[birth_trait_key])
        await self._save_player(guild.id, player)
        await self._sync_race_roles(guild, member, race_template)
        await self._sync_trait_roles(guild, member, player.trait_keys)
        await self._sync_player_path_roles(guild, member, player)
        embed = discord.Embed(
            title=_label_with_icon("welcome", "A New Cultivator Awakens"),
            description=(
                f"{SECTION_ICONS['progress']} Welcome to the sect, {interaction.user.mention}!\n"
                "Your destiny is now bound to the heavens."
            ),
            colour=discord.Colour.from_str("#f39c12"),
        )
        self._apply_avatar_portrait(
            embed, self._player_avatar_thumbnail_url(interaction, player)
        )
        def _safe_int(value: float | int | None) -> int:
            try:
                if value is None:
                    return 0
                return int(round(float(value)))
            except (TypeError, ValueError):
                return 0

        progress_current = _safe_int(player.cultivation_exp)
        progress_required = _safe_int(player.cultivation_exp_required)
        if qi_stage:
            realm_name = qi_stage.realm or qi_stage.name or self._stage_display(qi_stage)
            stage_name = qi_stage.phase_display or ""
            mortal_stage = _player_in_mortal_realm(player, stage=qi_stage)
        else:
            realm_name = "Unbound"
            stage_name = ""
            mortal_stage = False

        if progress_required:
            progress_value = (
                f"{format_number(progress_current)}/{format_number(progress_required)}"
            )
            progress_pct = max(0.0, (progress_current / float(progress_required)) * 100)
        else:
            progress_value = "0/0"
            progress_pct = 0.0

        stage_lines = [
            ansi_coloured_pair(
                "Realm",
                realm_name,
                label_colour=SECTION_COLOURS["stage"],
                value_colour="white",
                bold_value=True,
            ),
        ]
        if not mortal_stage and stage_name:
            stage_lines.append(
                ansi_coloured_pair(
                    "Stage",
                    stage_name,
                    label_colour=SECTION_COLOURS["stage"],
                    value_colour="white",
                    bold_value=True,
                )
            )
        stage_lines.extend(
            [
                ansi_coloured_pair(
                    "Progress",
                    f"{progress_value} ({progress_pct:.0f}%)",
                    label_colour=SECTION_COLOURS["progress"],
                    value_colour="white",
                    bold_value=True,
                ),
                progress_bar(progress_current, progress_required),
            ]
        )

        indicator_text = breakthrough_ready_indicator(progress_current, progress_required)
        if indicator_text:
            stage_lines.append(
                ansi_colour(
                    indicator_text,
                    SECTION_COLOURS["progress"],
                    bold=True,
                )
            )
        embed.add_field(
            name=_label_with_icon("stage", "Cultivation", player=player),
            value=ansi_block(stage_lines),
            inline=False,
        )
        for name, value, inline in talent_field_entries(
            player,
            _innate_min=self.config.innate_stat_min,
            innate_max=self.config.innate_stat_max,
            qi_stage=qi_stage,
        ):
            embed.add_field(name=name, value=value, inline=inline)
        if granted_traits:
            trait_lines = [
                coloured_pair(
                    trait.name,
                    trait.description or "Unique birthright.",
                    label_colour=SECTION_COLOURS["traits"],
                    value_colour="white",
                )
                for trait in granted_traits
            ]
            embed.add_field(
                name=_label_with_icon("traits", "Birth Traits"),
                value=coloured_block(trait_lines),
                inline=False,
            )
        await interaction.response.send_message(embed=embed)

    def _build_profile_view(
        self,
        player: PlayerProgress,
        *,
        guild: discord.Guild,
        owner_id: int,
        avatar_url: str,
        main_menu_callback: SimpleCallback | None = None,
    ) -> tuple[ProfileView, discord.Embed]:
        qi_stage = self.state.get_stage(player.cultivation_stage, CultivationPath.QI)
        if qi_stage is None:
            raise LookupError("Unknown cultivation stage")
        body_stage = None
        if player.body_cultivation_stage:
            body_stage = self.state.get_stage(
                player.body_cultivation_stage, CultivationPath.BODY
            )
        soul_stage = None
        if player.soul_cultivation_stage:
            soul_stage = self.state.get_stage(
                player.soul_cultivation_stage, CultivationPath.SOUL
            )
        race = self.state.races.get(player.race_key) if player.race_key else None
        trait_objects = [
            self.state.traits[key]
            for key in player.trait_keys
            if key in self.state.traits
        ]
        equipped_items = equipped_items_for_player(player, self.state.items)
        active_path = CultivationPath.from_value(player.active_path)

        effective_stats = player.effective_stats(
            qi_stage,
            body_stage,
            soul_stage,
            race,
            trait_objects,
            equipped_items,
        )
        passive_bonus_map = self.passive_skill_bonuses(player)
        total_passive_bonus = Stats()
        for bonus in passive_bonus_map.values():
            total_passive_bonus.add_in_place(bonus)
        effective_stats.add_in_place(total_passive_bonus)
        effective_base = player.combined_innate_soul(trait_objects)
        has_passive_bonus = any(
            abs(getattr(total_passive_bonus, stat)) > 1e-6 for stat in PLAYER_STAT_NAMES
        )
        mortal_stage = _player_in_mortal_realm(player, stage=qi_stage)

        title_names: list[str] = []
        orphan_titles: list[str] = []
        for key in player.titles:
            title_obj = self.state.titles.get(key)
            if title_obj:
                title_names.append(title_obj.name)
            else:
                orphan_titles.append(key)

        # Alignment is planned as a future progression system.  Until it is
        # implemented we surface a demonic placeholder so the menu layout can
        # be finalised.
        alignment_display = "Demonic"

        def _cultivation_field(
            path: CultivationPath,
        ) -> str:
            if path is CultivationPath.QI:
                stage_obj = qi_stage
                progress_current = player.cultivation_exp
                progress_required = player.cultivation_exp_required
            elif path is CultivationPath.BODY:
                stage_obj = body_stage
                progress_current = player.combat_exp
                progress_required = player.combat_exp_required
            else:
                stage_obj = soul_stage
                progress_current = player.soul_exp
                progress_required = player.soul_exp_required

            if stage_obj:
                realm_name = stage_obj.realm or stage_obj.name
                stage_name = stage_obj.phase_display or ""
                step_value = getattr(stage_obj, "step", 1)
                step_label = _ordinal_step_label(step_value)
                mortal_stage = stage_obj.is_mortal
            else:
                realm_name = "Unbound"
                stage_name = ""
                step_label = "Unbound"
                mortal_stage = False

            if progress_required:
                progress_value = (
                    f"{format_number(progress_current)}/"
                    f"{format_number(progress_required)}"
                )
                progress_pct = max(
                    0.0, (progress_current / float(progress_required)) * 100
                )
            else:
                progress_value = "0/0"
                progress_pct = 0.0

            cultivation_lines: list[str] = []

            stage_entries = [
                ansi_coloured_pair(
                    "Step",
                    step_label,
                    label_colour=SECTION_COLOURS["stage"],
                    value_colour="white",
                    bold_value=True,
                ),
                ansi_coloured_pair(
                    "Realm",
                    realm_name,
                    label_colour=SECTION_COLOURS["stage"],
                    value_colour="white",
                    bold_value=True,
                ),
            ]
            if stage_name and not mortal_stage:
                stage_entries.append(
                    ansi_coloured_pair(
                        "Stage",
                        stage_name,
                        label_colour=SECTION_COLOURS["stage"],
                        value_colour="white",
                        bold_value=True,
                    )
                )
            if stage_obj and getattr(stage_obj, "lifespan", ""):
                stage_entries.append(
                    ansi_coloured_pair(
                        "Lifespan",
                        stage_obj.lifespan,
                        label_colour=SECTION_COLOURS["stage"],
                        value_colour="white",
                        bold_value=True,
                    )
                )
            stage_entries.extend(
                [
                    ansi_coloured_pair(
                        "Progress",
                        f"{progress_value} ({progress_pct:.0f}%)",
                        label_colour=SECTION_COLOURS["progress"],
                        value_colour="white",
                        bold_value=True,
                    ),
                    progress_bar(progress_current, progress_required),
                ]
            )
            cultivation_lines.extend(stage_entries)

            indicator_text = breakthrough_ready_indicator(
                progress_current, progress_required
            )
            if indicator_text:
                cultivation_lines.append(
                    ansi_colour(
                        indicator_text,
                        SECTION_COLOURS["progress"],
                        bold=True,
                    )
                )

            return ansi_block(cultivation_lines)

        def build_overview_embed() -> discord.Embed:
            overview_embed = discord.Embed(
                title=_label_with_icon("guide", f"{player.name}'s Profile Overview"),
                colour=discord.Colour.blurple(),
                description=coloured(
                    "Use the buttons below to open a detailed page. "
                    "This overview highlights your current status at a glance.",
                    dim=True,
                ),
            )
            self._apply_avatar_portrait(overview_embed, avatar_url)

            cultivation_field_value = _cultivation_field(active_path)
            overview_embed.add_field(
                name=_label_with_icon("stage", "CULTIVATION", player=player),
                value=cultivation_field_value,
                inline=False,
            )

            inventory_slots = inventory_load(player)
            inventory_capacity_value = inventory_capacity(player, self.state.items)
            base_capacity = max(0, int(player.inventory_capacity))
            bonus_capacity = max(0, inventory_capacity_value - base_capacity)
            ring_usage = min(inventory_slots, bonus_capacity)
            base_usage = min(
                max(0, inventory_slots - ring_usage),
                base_capacity,
            )
            storage_lines = [
                ansi_coloured_pair(
                    "Satchel",
                    f"{format_number(base_usage)}/{format_number(base_capacity)}",
                    label_colour=SECTION_COLOURS["inventory"],
                    value_colour="white",
                    bold_value=True,
                ),
            ]
            overview_embed.add_field(
                name=_label_with_icon("inventory", "Storage"),
                value=ansi_block(storage_lines),
                inline=True,
            )

            currency_lines: list[str] = []
            currency_icons: list[str] = []
            currency_keys = set(self.state.currencies) | set(player.currencies)
            if currency_keys:
                def _currency_sort_key(key: str) -> tuple[int, str]:
                    currency = self.state.currencies.get(key)
                    if currency:
                        return (0, currency.name.lower())
                    return (1, key.replace("_", " ").lower())

                for key in sorted(currency_keys, key=_currency_sort_key):
                    amount = player.currencies.get(key, 0)
                    currency = self.state.currencies.get(key)
                    label = currency.name if currency else key.replace("_", " ").title()
                    icon = _currency_icon_for_display(key, currency) or CURRENCY_EMOJI_TEXT
                    if icon not in currency_icons:
                        currency_icons.append(icon)
                    currency_lines.append(
                        ansi_coloured_pair(
                            label,
                            format_number(amount),
                            label_colour=SECTION_COLOURS["currency"],
                            value_colour="white",
                            bold_value=amount > 0,
                            dim_value=amount <= 0,
                        )
                    )
            else:
                currency_lines.append(
                    ansi_colour("No currency held.", "gray", dim=True)
                )
            currency_field_name = _label_with_icon("currency", "Wealth")
            if currency_icons:
                currency_field_name = f"{currency_field_name} {' '.join(currency_icons)}"
            overview_embed.add_field(
                name=currency_field_name,
                value=ansi_block(currency_lines),
                inline=True,
            )

            overview_embed.add_field(
                name=_label_with_icon("stats", "Status"),
                value=_status_block(
                    player,
                    effective_stats=effective_stats,
                    active_path=active_path,
                    has_passive_bonus=has_passive_bonus,
                ),
                inline=False,
            )

            overview_embed.add_field(
                name=_label_with_icon("martial_soul", "Martial Souls"),
                value=_martial_soul_block(player, mortal_stage=mortal_stage),
                inline=False,
            )

            return overview_embed

        def build_profile_embed() -> discord.Embed:
            profile_embed = discord.Embed(
                title=_label_with_icon("profile", f"{player.name}'s Profile"),
                colour=discord.Colour.blurple(),
            )
            self._apply_avatar_portrait(profile_embed, avatar_url)

            title_lines: list[str] = []
            if title_names:
                title_lines.append(
                    profile_pair(
                        "Titles",
                        ", ".join(title_names),
                        label_key="titles",
                    )
                )
            if orphan_titles:
                title_lines.append(
                    profile_pair(
                        "Unconfigured",
                        ", ".join(orphan_titles),
                        label_key="warning",
                        dim_value=True,
                    )
                )
            if not title_lines:
                title_lines.append(coloured("• No titles earned.", dim=True))
            profile_embed.add_field(
                name=_label_with_icon("titles", "Titles"),
                value=coloured_block(title_lines),
                inline=False,
            )

            identity_lines = [
                ansi_coloured_pair(
                    "Race",
                    race.name if race else "Unbound",
                    label_colour=SECTION_COLOURS["profile"],
                    value_colour="white",
                    bold_value=True,
                )
            ]
            if race and race.description:
                identity_lines.append(
                    ansi_colour(
                        race.description,
                        SECTION_COLOURS["profile"],
                        dim=True,
                    )
                )
            profile_embed.add_field(
                name=_label_with_icon("profile", "Identity"),
                value=ansi_block(identity_lines),
                inline=False,
            )
            # Alignment is not yet implemented, so surface a placeholder spectrum
            # with all players leaning slightly demonic for now.
            spectrum_width = 20
            alignment_ratio = 0.6
            demonic_segments = min(
                spectrum_width,
                max(0, int(round(alignment_ratio * spectrum_width))),
            )
            righteous_segments = spectrum_width - demonic_segments

            spectrum_segments: list[str] = []
            spectrum_segments.extend(
                ansi_colour("■", "green", bold=True) for _ in range(righteous_segments)
            )
            spectrum_segments.extend(
                ansi_colour("■", "red", bold=True) for _ in range(demonic_segments)
            )

            alignment_bar = "".join(spectrum_segments)
            alignment_spectrum = " ".join(
                (
                    ansi_colour("Righteous", "green", bold=True),
                    alignment_bar,
                    ansi_colour("Demonic", "red", bold=True),
                )
            )
            profile_embed.add_field(
                name=_label_with_icon("warning", "Alignment"),
                value=ansi_block([alignment_spectrum]),
                inline=False,
            )

            path_field_map = {
                path: _cultivation_field(
                    path,
                )
                for path in (
                    CultivationPath.QI,
                    CultivationPath.BODY,
                    CultivationPath.SOUL,
                )
            }
            field_value = path_field_map[active_path]
            profile_embed.add_field(
                name=_label_with_icon("stage", "CULTIVATION", player=player),
                value=field_value,
                inline=False,
            )

            core_stat_sections = [
                format_coloured_stats_block(
                    effective_stats,
                    stat_names=PRIMARY_STAT_DISPLAY_ORDER,
                )
            ]
            secondary_stats_text = format_coloured_stats_block(
                effective_stats,
                stat_names=SECONDARY_STAT_DISPLAY_ORDER,
            )
            core_stats_text = "\n".join(core_stat_sections)
            profile_embed.add_field(
                name=_label_with_icon("stats", "Main Stats"),
                value=core_stats_text,
                inline=False,
            )
            profile_embed.add_field(
                name=_label_with_icon("combat", "Sub-Stats"),
                value=secondary_stats_text,
                inline=False,
            )

            trait_lines: list[str] = []
            if player.trait_keys:
                for key in player.trait_keys:
                    trait = self.state.traits.get(key)
                    if not trait:
                        trait_lines.append(f"**{key}:** Unknown trait.")
                        continue
                    effect_parts: list[str] = []
                    for stat_name in PLAYER_STAT_NAMES:
                        multiplier = getattr(trait, stat_name, 1.0)
                        if abs(multiplier - 1.0) > 1e-6:
                            effect_parts.append(
                                f"{stat_name.replace('_', ' ').title()} ×{multiplier:g}"
                            )
                    details: list[str] = []
                    if effect_parts:
                        details.append(f"Effects: {', '.join(effect_parts)}")
                    if trait.grants_affinities:
                        affinity_labels = ", ".join(
                            affinity_token(affinity) for affinity in trait.grants_affinities
                        )
                        details.append(f"Affinities: {affinity_labels}")
                    detail_text = (
                        "\n    " + "\n    ".join(details)
                        if details
                        else ""
                    )
                    trait_lines.append(
                        f"**{trait.name}:** {trait.description}{detail_text}"
                    )
            else:
                trait_lines.append(coloured("No traits awakened.", dim=True))
            profile_embed.add_field(
                name=_label_with_icon("traits", "Traits"),
                value=coloured_block(trait_lines),
                inline=False,
            )

            return profile_embed

        def build_talent_embed() -> discord.Embed:
            talent_embed = discord.Embed(
                title=_label_with_icon("talent", f"{player.name}'s Talents"),
                colour=discord.Colour.blurple(),
            )
            self._apply_avatar_portrait(talent_embed, avatar_url)
            for name, value, inline in talent_field_entries(
                player,
                _innate_min=self.config.innate_stat_min,
                innate_max=self.config.innate_stat_max,
                effective_base=effective_base,
                qi_stage=qi_stage,
            ):
                talent_embed.add_field(name=name, value=value, inline=inline)

            return talent_embed

        def build_active_skills_embed() -> discord.Embed:
            skills_embed = discord.Embed(
                title=_label_with_icon("guide", f"{player.name}'s Active Techniques"),
                colour=discord.Colour.blurple(),
            )
            self._apply_avatar_portrait(skills_embed, avatar_url)

            skill_entries = self._skill_entries(
                player,
                effective_base,
                category=SkillCategory.ACTIVE,
                stats=effective_stats,
            )
            if not skill_entries:
                skills_embed.description = coloured(
                    "No combat techniques learned.", dim=True
                )
            else:
                blocks = self._skill_summary_blocks(
                    player, effective_base, entries=skill_entries
                )
                for heading, block in blocks:
                    field_name = (
                        heading
                        if heading == "\u200b"
                        else _label_with_icon("guide", heading)
                    )
                    skills_embed.add_field(
                        name=field_name,
                        value=block,
                        inline=False,
                    )
            return skills_embed

        def build_passive_skills_embed() -> discord.Embed:
            passive_embed = discord.Embed(
                title=_label_with_icon("guide", f"{player.name}'s Passive Skills"),
                colour=discord.Colour.blurple(),
            )
            self._apply_avatar_portrait(passive_embed, avatar_url)

            summary_lines: list[str] = []
            for stat_name in PLAYER_STAT_NAMES:
                total_value = getattr(total_passive_bonus, stat_name)
                if not total_value:
                    continue
                icon = STAT_ICONS.get(stat_name, "•")
                label = f"{icon} {stat_name.replace('_', ' ').title()}"
                label_text = ansi_colour(label, SECTION_COLOURS["stats"], bold=True)
                value_text = ansi_colour(
                    f"+{_format_stat_value(total_value)}",
                    SECTION_COLOURS["progress"],
                    bold=True,
                )
                summary_lines.append(f"{label_text}: {value_text}")
            if summary_lines:
                passive_embed.add_field(
                    name=_label_with_icon("progress", "Aura Summary"),
                    value=ansi_block(summary_lines),
                    inline=False,
                )

            def passive_sort_key(item: tuple[str, int]) -> tuple[str, str]:
                key, _ = item
                skill = self.state.skills.get(key)
                name = skill.name if skill else key
                return (name.lower(), key)

            has_entries = False
            for key, prof in sorted(
                player.skill_proficiency.items(), key=passive_sort_key
            ):
                skill = self.state.skills.get(key)
                if not skill or skill.category is not SkillCategory.PASSIVE:
                    continue
                has_entries = True
                stars = self._skill_grade_stars(skill.grade)
                grade_line = ansi_colour(stars, "yellow", bold=True)
                description_line = (
                    ansi_colour(
                        skill.description.strip(), "gray", dim=True, italic=True
                    )
                    if skill.description and skill.description.strip()
                    else None
                )
                current_bonus = passive_bonus_map.get(key, Stats())
                base_bonus = skill.stat_bonuses
                bonus_lines: list[str] = []
                for stat_name in PLAYER_STAT_NAMES:
                    max_value = getattr(base_bonus, stat_name)
                    if not max_value:
                        continue
                    current_value = getattr(current_bonus, stat_name)
                    icon = STAT_ICONS.get(stat_name, "•")
                    label = f"{icon} {stat_name.replace('_', ' ').title()}"
                    label_text = ansi_colour(label, SECTION_COLOURS["stats"], bold=True)
                    current_text = _format_stat_value(current_value)
                    max_text = _format_stat_value(max_value)
                    value_colour = STAT_COLOURS.get(stat_name, "white")
                    value_parts = [
                        ansi_colour(f"+{current_text}", value_colour, bold=True)
                    ]
                    if current_value < max_value and skill.proficiency_max > 1:
                        value_parts.append(ansi_colour(f"(max +{max_text})", "gray", dim=True))
                    bonus_lines.append(f"{label_text}: {' '.join(value_parts)}")
                if not bonus_lines:
                    bonus_lines.append(
                        ansi_colour("No stat bonuses unlocked yet.", "gray", dim=True)
                    )
                proficiency_ceiling = max(1, skill.proficiency_max)
                proficiency_ratio = prof / proficiency_ceiling
                proficiency_cap = skill.proficiency_max or proficiency_ceiling
                proficiency_value = (
                    f"{format_number(prof)}/{format_number(proficiency_cap)} "
                    f"({_format_percentage(proficiency_ratio)})"
                )
                proficiency_line = ansi_coloured_pair(
                    "Proficiency",
                    proficiency_value,
                    label_colour=SECTION_COLOURS["progress"],
                    value_colour=SECTION_COLOURS["progress"],
                    bold_value=True,
                )
                block_lines = [grade_line, proficiency_line, *bonus_lines]
                if description_line:
                    block_lines.insert(1, description_line)
                passive_embed.add_field(
                    name=coloured(
                        self._skill_display_name(skill), colour="white", bold=True
                    ),
                    value=ansi_block(block_lines),
                    inline=False,
                )
            if not has_entries:
                passive_embed.description = coloured(
                    "No passive skills unlocked.", dim=True
                )
            return passive_embed

        def build_cultivation_techniques_embed() -> discord.Embed:
            technique_embed = discord.Embed(
                title=_label_with_icon(
                    "cultivation", f"{player.name}'s Cultivation Techniques"
                ),
                colour=discord.Colour.blurple(),
            )
            self._apply_avatar_portrait(technique_embed, avatar_url)

            entries = self._technique_entry_blocks(player, trait_objects)
            if not entries:
                technique_embed.description = coloured(
                    "No cultivation techniques mastered.", dim=True
                )
            else:
                for heading, detail_lines in entries:
                    technique_embed.add_field(
                        name=coloured(heading, colour="white", bold=True),
                        value=ansi_block(detail_lines),
                        inline=False,
                    )

            return technique_embed

        def build_inventory_embed() -> discord.Embed:
            inventory_embed = discord.Embed(
                title=_label_with_icon("inventory", f"{player.name}'s Inventory"),
                colour=discord.Colour.blurple(),
            )
            self._apply_avatar_portrait(inventory_embed, avatar_url)

            load = inventory_load(player)
            capacity = inventory_capacity(player, self.state.items)
            summary_lines = [
                coloured_pair(
                    "Slots Used",
                    f"{format_number(load)}/{format_number(capacity)}",
                    label_colour=SECTION_COLOURS["inventory"],
                    value_colour="white",
                    bold_value=True,
                )
            ]
            inventory_embed.add_field(
                name=_label_with_icon("inventory", "Inventory Summary"),
                value=coloured_block([f"• {line}" for line in summary_lines]),
                inline=True,
            )

            item_lines: list[str] = []
            for item_key, amount in sorted(player.inventory.items()):
                if amount <= 0:
                    continue
                item = self.state.items.get(item_key)
                name = item.name if item else item_key
                item_lines.append(f"• {format_number(amount)}× {name}")
            if item_lines:
                inventory_embed.add_field(
                    name=_label_with_icon("inventory", "Items Carried"),
                    value=coloured_block(item_lines),
                    inline=False,
                )
            else:
                inventory_embed.add_field(
                    name=_label_with_icon("inventory", "Items Carried"),
                    value=coloured("No items carried.", dim=True),
                    inline=False,
                )

            currency_lines: list[str] = []
            for currency_key, amount in sorted(player.currencies.items()):
                if amount <= 0:
                    continue
                currency = self.state.currencies.get(currency_key)
                label = currency.name if currency else currency_key
                currency_lines.append(
                    coloured_pair(
                        label,
                        format_number(amount),
                        label_colour=SECTION_COLOURS["currency"],
                        value_colour="white",
                        bold_value=True,
                    )
                )
            if currency_lines:
                inventory_embed.add_field(
                    name=_label_with_icon("currency", "Currencies"),
                    value=coloured_block([f"• {line}" for line in currency_lines]),
                    inline=True,
                )
            else:
                inventory_embed.add_field(
                    name=_label_with_icon("currency", "Currencies"),
                    value=coloured("No currency held.", dim=True),
                    inline=True,
                )

            return inventory_embed

        equipment_panel = ProfileEquipmentPanel(
            self,
            guild.id,
            owner_id,
            avatar_url=avatar_url,
            initial_player=player,
        )

        def build_equipment_embed() -> discord.Embed:
            return equipment_panel.build_embed()

        def equipment_controls(view: ProfileView) -> list[discord.ui.Item[ProfileView]]:
            equipment_panel.attach(view)
            return equipment_panel.controls()

        pages = {
            "overview": ("Overview", WANDER_TRAVEL_EMOJI_TEXT, build_overview_embed),
            "stats": ("Stats", "📊", build_profile_embed),
            "skills": ("Skills", "📜", build_active_skills_embed),
            "inventory": ("Inventory", None, build_inventory_embed),
        }
        stats_tabs = {
            "profile": ("Profile", "🪪", build_profile_embed),
            "talent": ("Talents", "🧬", build_talent_embed),
        }
        skill_tabs = {
            "skills_active": ("Active Skills", "⚔️", build_active_skills_embed),
            "skills_passive": ("Passive Skills", "✨", build_passive_skills_embed),
            "skills_techniques": (
                "Cultivation Techniques",
                "<:heaven_cultivation:1433142350590378086>",
                build_cultivation_techniques_embed,
            ),
        }
        inventory_tabs = {
            "inventory_items": ("Inventory", None, build_inventory_embed),
            "inventory_equipment": ("Equipment", "🛡️", build_equipment_embed),
        }

        view = ProfileView(
            owner_id=owner_id,
            pages=pages,
            main_menu_callback=main_menu_callback,
            submenus={"stats": stats_tabs},
            skill_tabs=skill_tabs,
            inventory_tabs=inventory_tabs,
            initial_page="overview",
            submenu_return_pages={"stats": "overview"},
            page_item_factories={"inventory_equipment": equipment_controls},
        )
        initial_embed = view.initial_embed()
        return view, initial_embed

    def _player_avatar_url(
        self, interaction: discord.Interaction, player: PlayerProgress
    ) -> str:
        if player.profile_image_url:
            return player.profile_image_url
        return interaction.user.display_avatar.url

    def _resize_avatar_url(self, base_url: str, *, size: int = 1024) -> str:
        try:
            parsed = urlparse(base_url)
        except ValueError:
            return base_url

        if not parsed.scheme or not parsed.netloc:
            return base_url

        netloc = parsed.netloc.lower()
        path = parsed.path or ""
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))

        def _int_param(key: str) -> int:
            value = params.get(key)
            if value is None:
                return 0
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        updated = False
        if netloc.endswith(("discordapp.com", "discordapp.net")):
            if "/attachments/" in path:
                if _int_param("width") < size:
                    params["width"] = str(size)
                    updated = True
                if _int_param("height") < size:
                    params["height"] = str(size)
                    updated = True
            else:
                if _int_param("size") < size:
                    params["size"] = str(size)
                    updated = True

        # Bump a cache-busting token any time we request a new portrait size so
        # Discord is forced to refetch the asset at the larger dimensions.
        params["portrait"] = str(size)
        updated = True

        if not updated:
            return base_url

        new_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def _player_avatar_thumbnail_url(
        self,
        interaction: discord.Interaction,
        player: PlayerProgress,
        *,
        size: int = 1024,
    ) -> str:
        base_url = self._player_avatar_url(interaction, player)
        return self._resize_avatar_url(base_url, size=size)

    def _apply_avatar_portrait(
        self,
        embed: discord.Embed,
        avatar_url: str | None,
        *,
        size: int = 512,
    ) -> None:
        """Attach the player's avatar using embed thumbnail metadata.

        Discord constrains thumbnail rendering, but explicitly providing the
        target dimensions in the payload helps convince the client to draw a
        much larger preview.  We still fall back to the standard behaviour if
        discord.py changes its internals.
        """

        if not avatar_url:
            return

        embed.set_thumbnail(url=avatar_url)
        try:
            thumbnail = embed._thumbnail  # type: ignore[attr-defined]
        except AttributeError:
            thumbnail = None

        if isinstance(thumbnail, dict):
            thumbnail.setdefault("proxy_url", avatar_url)
            thumbnail["height"] = max(int(thumbnail.get("height", 0) or 0), size)
            thumbnail["width"] = max(int(thumbnail.get("width", 0) or 0), size)

    async def _show_profile_menu(
        self,
        interaction: discord.Interaction,
        *,
        guild: discord.Guild,
        main_menu_callback: SimpleCallback | None,
        ephemeral: bool = True,
        player: PlayerProgress | None = None,
    ) -> None:
        await self.ensure_guild_loaded(guild.id)
        if player is None:
            player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(
                interaction, embed=embed, ephemeral=ephemeral
            )
            return
        avatar_url = self._player_avatar_url(interaction, player)
        avatar_thumbnail_url = self._player_avatar_thumbnail_url(interaction, player)

        try:
            view, initial_embed = self._build_profile_view(
                player,
                guild=guild,
                owner_id=interaction.user.id,
                avatar_url=avatar_thumbnail_url,
                main_menu_callback=main_menu_callback,
            )
        except LookupError:
            embed = _make_embed(
                "error",
                "Unknown Stage",
                "Your recorded cultivation stage no longer exists.",
                discord.Colour.red(),
            )
            await self._respond_interaction(
                interaction, embed=embed, ephemeral=ephemeral
            )
            return

        await self._respond_interaction(
            interaction, embed=initial_embed, view=view, ephemeral=ephemeral
        )
        try:
            if interaction.type is InteractionType.component:
                view.message = interaction.message
            else:
                view.message = await interaction.original_response()
        except discord.HTTPException:
            view.message = None

    def _trade_item_label(self, item_key: str) -> str:
        item = self.state.items.get(item_key)
        if item and item.name:
            return item.name
        text = item_key.replace("_", " ").replace("-", " ")
        return text.title() if text else item_key

    def _trade_currency_label(self, currency_key: str) -> str:
        currency = self.state.currencies.get(currency_key)
        name = currency.name if currency and currency.name else currency_key
        emoji = _currency_icon_for_display(currency_key, currency)
        return f"{emoji} {name}" if emoji else name

    def _format_trade_section(
        self,
        *,
        items: Mapping[str, int],
        currencies: Mapping[str, int],
        empty: str,
    ) -> str:
        lines: list[str] = []
        for key, amount in sorted(
            items.items(), key=lambda entry: self._trade_item_label(entry[0]).lower()
        ):
            try:
                count = int(amount)
            except (TypeError, ValueError):
                continue
            if count <= 0:
                continue
            lines.append(
                f"• {format_number(count)}× {self._trade_item_label(key)}"
            )
        for key, amount in sorted(
            currencies.items(),
            key=lambda entry: self._trade_currency_label(entry[0]).lower(),
        ):
            try:
                value = int(amount)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            lines.append(
                f"• {format_number(value)} {self._trade_currency_label(key)}"
            )
        if not lines:
            return empty
        max_lines = 10
        if len(lines) > max_lines:
            visible = lines[:max_lines]
            visible.append(f"…and {len(lines) - max_lines} more.")
            lines = visible
        text = "\n".join(lines)
        if len(text) > 1024:
            trimmed: list[str] = []
            total = 0
            for line in lines:
                line_length = len(line) + 1
                if total + line_length > 1000:
                    break
                trimmed.append(line)
                total += line_length
            trimmed.append("…trade summary truncated…")
            return "\n".join(trimmed)
        return text

    @staticmethod
    def _adjust_mapping(store: dict[str, int], key: str, delta: int) -> None:
        try:
            current = int(store.get(key, 0))
        except (TypeError, ValueError):
            current = 0
        new_value = current + delta
        if new_value <= 0:
            store.pop(key, None)
        else:
            store[key] = new_value

    def _validate_trade_configuration(
        self,
        initiator: PlayerProgress,
        partner: PlayerProgress,
        proposal: TradeProposal,
    ) -> tuple[bool, str | None]:
        for key, amount in proposal.offer.items.items():
            try:
                required = int(amount)
            except (TypeError, ValueError):
                required = 0
            if required <= 0:
                continue
            try:
                available = int(initiator.inventory.get(key, 0))
            except (TypeError, ValueError):
                available = 0
            if available < required:
                name = self._trade_item_label(key)
                return False, (
                    f"{proposal.initiator_name} no longer has"
                    f" {format_number(required)}× {name}."
                )
        for key, amount in proposal.offer.currencies.items():
            try:
                required = int(amount)
            except (TypeError, ValueError):
                required = 0
            if required <= 0:
                continue
            try:
                available = int(initiator.currencies.get(key, 0))
            except (TypeError, ValueError):
                available = 0
            if available < required:
                name = self._trade_currency_label(key)
                return False, (
                    f"{proposal.initiator_name} lacks {format_number(required)}"
                    f" {name} for the trade."
                )
        for key, amount in proposal.request.items.items():
            try:
                required = int(amount)
            except (TypeError, ValueError):
                required = 0
            if required <= 0:
                continue
            try:
                available = int(partner.inventory.get(key, 0))
            except (TypeError, ValueError):
                available = 0
            if available < required:
                name = self._trade_item_label(key)
                return False, (
                    f"{proposal.partner_name} no longer has"
                    f" {format_number(required)}× {name}."
                )
        for key, amount in proposal.request.currencies.items():
            try:
                required = int(amount)
            except (TypeError, ValueError):
                required = 0
            if required <= 0:
                continue
            try:
                available = int(partner.currencies.get(key, 0))
            except (TypeError, ValueError):
                available = 0
            if available < required:
                name = self._trade_currency_label(key)
                return False, (
                    f"{proposal.partner_name} lacks {format_number(required)}"
                    f" {name} for the trade."
                )

        def _safe_sum(values: Mapping[str, int]) -> int:
            total = 0
            for value in values.values():
                try:
                    total += max(0, int(value))
                except (TypeError, ValueError):
                    continue
            return total

        initiator_capacity = inventory_capacity(initiator, self.state.items)
        partner_capacity = inventory_capacity(partner, self.state.items)
        offer_items_total = _safe_sum(proposal.offer.items)
        request_items_total = _safe_sum(proposal.request.items)
        initiator_load = (
            max(0, inventory_load(initiator) - offer_items_total) + request_items_total
        )
        partner_load = (
            max(0, inventory_load(partner) - request_items_total) + offer_items_total
        )
        if initiator_load > initiator_capacity:
            return False, (
                f"{proposal.initiator_name} does not have enough inventory space"
                " to receive the requested items."
            )
        if partner_load > partner_capacity:
            return False, (
                f"{proposal.partner_name} does not have enough inventory space"
                " for the offered goods."
            )
        return True, None

    def _apply_trade_changes(
        self,
        initiator: PlayerProgress,
        partner: PlayerProgress,
        proposal: TradeProposal,
    ) -> None:
        for key, amount in proposal.offer.items.items():
            try:
                delta = int(amount)
            except (TypeError, ValueError):
                continue
            if delta <= 0:
                continue
            self._adjust_mapping(initiator.inventory, key, -delta)
            self._adjust_mapping(partner.inventory, key, delta)
        for key, amount in proposal.request.items.items():
            try:
                delta = int(amount)
            except (TypeError, ValueError):
                continue
            if delta <= 0:
                continue
            self._adjust_mapping(partner.inventory, key, -delta)
            self._adjust_mapping(initiator.inventory, key, delta)
        for key, amount in proposal.offer.currencies.items():
            try:
                delta = int(amount)
            except (TypeError, ValueError):
                continue
            if delta <= 0:
                continue
            self._adjust_mapping(initiator.currencies, key, -delta)
            self._adjust_mapping(partner.currencies, key, delta)
        for key, amount in proposal.request.currencies.items():
            try:
                delta = int(amount)
            except (TypeError, ValueError):
                continue
            if delta <= 0:
                continue
            self._adjust_mapping(partner.currencies, key, -delta)
            self._adjust_mapping(initiator.currencies, key, delta)

    async def _process_trade(
        self, guild_id: int, proposal: TradeProposal
    ) -> tuple[bool, str | None, PlayerProgress | None, PlayerProgress | None]:
        initiator = await self._fetch_player(guild_id, proposal.initiator_id)
        partner = await self._fetch_player(guild_id, proposal.partner_id)
        if initiator is None or partner is None:
            return False, "One of the cultivators could not be found.", initiator, partner
        valid, error = self._validate_trade_configuration(
            initiator, partner, proposal
        )
        if not valid:
            return False, error, initiator, partner
        self._apply_trade_changes(initiator, partner, proposal)
        await self._save_player(guild_id, initiator)
        await self._save_player(guild_id, partner)
        return True, None, initiator, partner

    def _trade_summary_embed(
        self,
        title: str,
        description: str,
        proposal: TradeProposal,
        *,
        footer: str,
        colour: discord.Colour,
    ) -> discord.Embed:
        embed = _make_embed(
            "trade",
            title,
            description,
            colour,
            include_icon=True,
        )
        embed.add_field(
            name="They Offer",
            value=self._format_trade_section(
                items=proposal.offer.items,
                currencies=proposal.offer.currencies,
                empty="Nothing",
            ),
            inline=False,
        )
        embed.add_field(
            name="They Request",
            value=self._format_trade_section(
                items=proposal.request.items,
                currencies=proposal.request.currencies,
                empty="Nothing",
            ),
            inline=False,
        )
        embed.set_footer(text=footer)
        return embed

    async def _start_trade_negotiation(
        self,
        guild_id: int,
        thread: discord.Thread,
        proposal: TradeProposal,
        initiator: PlayerProgress,
        partner: PlayerProgress,
    ) -> TradeNegotiationView | None:
        async def load_initiator() -> PlayerProgress | None:
            return await self._fetch_player(guild_id, initiator.user_id)

        async def load_partner() -> PlayerProgress | None:
            return await self._fetch_player(guild_id, partner.user_id)

        view = TradeNegotiationView(
            thread_id=thread.id,
            initiator=initiator,
            partner=partner,
            items=self.state.items,
            currencies=self.state.currencies,
            player_loaders={
                initiator.user_id: load_initiator,
                partner.user_id: load_partner,
            },
            finalize_callback=self._finalize_trade_session,
            cancel_callback=self._cancel_trade_session,
        )
        embed = view.build_embed()
        content = (
            f"<@{proposal.initiator_id}> and <@{proposal.partner_id}>, negotiate your "
            "trade below and press Ready when both of you are satisfied."
        )
        try:
            message = await thread.send(content=content, embed=embed, view=view)
        except discord.HTTPException:
            view.stop()
            return None
        view.message = message
        self._trade_sessions[thread.id] = view
        return view

    async def _finalize_trade_session(
        self,
        interaction: discord.Interaction,
        view: TradeNegotiationView,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            message = "Trades can only be finalized within a server channel."
            if interaction.response.is_done():
                try:
                    await interaction.followup.send(message, ephemeral=True)
                except discord.HTTPException:
                    pass
            else:
                await interaction.response.send_message(message, ephemeral=True)
            view.accepted.clear()
            view.status_message = message
            if view.message is not None:
                try:
                    await view.message.edit(embed=view.build_embed(), view=view)
                except discord.HTTPException:
                    pass
            return

        proposal = view.as_proposal()
        latest_initiator = await self._fetch_player(guild.id, proposal.initiator_id)
        latest_partner = await self._fetch_player(guild.id, proposal.partner_id)
        if latest_initiator is None or latest_partner is None:
            view.accepted.clear()
            view.status_message = "One of the cultivators could not be found."
            view.update_players(initiator=latest_initiator, partner=latest_partner)
            if view.message is not None:
                try:
                    await view.message.edit(embed=view.build_embed(), view=view)
                except discord.HTTPException:
                    pass
            if interaction.response.is_done():
                try:
                    await interaction.followup.send(
                        "One of the cultivators could not be found.", ephemeral=True
                    )
                except discord.HTTPException:
                    pass
            else:
                await interaction.response.send_message(
                    "One of the cultivators could not be found.", ephemeral=True
                )
            return

        success, error, updated_initiator, updated_partner = await self._process_trade(
            guild.id, proposal
        )
        if not success:
            view.accepted.clear()
            view.status_message = error or "The trade could not be completed."
            view.update_players(
                initiator=updated_initiator, partner=updated_partner
            )
            if view.message is not None:
                try:
                    await view.message.edit(embed=view.build_embed(), view=view)
                except discord.HTTPException:
                    pass
            if interaction.response.is_done():
                try:
                    await interaction.followup.send(
                        error or "The trade could not be completed.", ephemeral=True
                    )
                except discord.HTTPException:
                    pass
            else:
                await interaction.response.send_message(
                    error or "The trade could not be completed.", ephemeral=True
                )
            return

        view.disable()
        summary = self._trade_summary_embed(
            "Trade Completed",
            (
                f"{proposal.initiator_name} and {proposal.partner_name} finalized their "
                "trade."
            ),
            proposal,
            footer="The listed items and currency have been exchanged.",
            colour=discord.Colour.from_str("#2ecc71"),
        )
        content = (
            f"Trade between <@{proposal.initiator_id}> and "
            f"<@{proposal.partner_id}> completed successfully!"
        )
        if view.message is not None:
            try:
                await view.message.edit(content=content, embed=summary, view=view)
            except discord.HTTPException:
                pass
        self._trade_sessions.pop(view.thread_id, None)
        if interaction.response.is_done():
            try:
                await interaction.followup.send(
                    "Trade completed successfully.", ephemeral=True
                )
            except discord.HTTPException:
                pass
        else:
            await interaction.response.send_message(
                "Trade completed successfully.", ephemeral=True
            )
        view.stop()

    async def _cancel_trade_session(
        self,
        interaction: discord.Interaction | None,
        view: TradeNegotiationView,
    ) -> None:
        self._trade_sessions.pop(view.thread_id, None)
        if interaction is None:
            view.stop()
            return
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True)
            except discord.HTTPException:
                pass
        cancelled_by = interaction.user.mention
        proposal = view.as_proposal()
        view.disable()
        summary = self._trade_summary_embed(
            "Trade Cancelled",
            f"{cancelled_by} cancelled the trade negotiation.",
            proposal,
            footer="No items or currency were exchanged.",
            colour=discord.Colour.from_str("#f87171"),
        )
        content = (
            f"Trade between <@{proposal.initiator_id}> and "
            f"<@{proposal.partner_id}> was cancelled by {cancelled_by}."
        )
        if view.message is not None:
            try:
                await view.message.edit(content=content, embed=summary, view=view)
            except discord.HTTPException:
                pass
        try:
            await interaction.followup.send(
                "You cancelled the trade negotiation.", ephemeral=True
            )
        except discord.HTTPException:
            pass
        view.stop()

    async def _open_main_menu(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed(
                "error",
                "Guild Only",
                "Use this in a server.",
                discord.Colour.red(),
            )
            await self._respond_interaction(interaction, embed=embed)
            return

        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed)
            return

        async def return_to_main(menu_interaction: discord.Interaction) -> None:
            await self._open_main_menu(menu_interaction)

        async def open_profile(menu_interaction: discord.Interaction) -> None:
            await self._show_profile_menu(
                menu_interaction,
                guild=guild,
                main_menu_callback=return_to_main,
                ephemeral=True,
                player=player,
            )

        async def open_travel(menu_interaction: discord.Interaction) -> None:
            await self._open_travel_menu(
                menu_interaction,
                return_callback=return_to_main,
                player=player,
            )

        async def open_cultivate(menu_interaction: discord.Interaction) -> None:
            await self._send_cultivation_menu(
                menu_interaction,
                return_callback=return_to_main,
                player=player,
            )

        async def open_trade(menu_interaction: discord.Interaction) -> None:
            await self._open_trade_menu(
                menu_interaction,
                return_callback=return_to_main,
                player=player,
            )

        view = OwnedView(interaction.user.id, timeout=120.0)
        view.add_item(
            CallbackButton(
                label="Profile",
                emoji="🪪",
                style=discord.ButtonStyle.primary,
                callback=open_profile,
            )
        )
        view.add_item(
            CallbackButton(
                label="Travel",
                emoji=WANDER_TRAVEL_EMOJI,
                style=discord.ButtonStyle.success,
                callback=open_travel,
            )
        )
        view.add_item(
            CallbackButton(
                label="Cultivate",
                emoji="<:heaven_cultivation:1433142350590378086>",
                style=discord.ButtonStyle.primary,
                callback=open_cultivate,
            )
        )
        view.add_item(
            CallbackButton(
                label="Trade",
                emoji="🤝",
                style=discord.ButtonStyle.secondary,
                callback=open_trade,
            )
        )

        menu_description = "\n".join(
            [
                "Choose an option to continue your journey:",
                "• Profile — Review your cultivation progress.\n",
                "• Travel — Attune to another location or wander nearby.",
                "• Cultivate — Focus on your current path.",
                "• Trade — Exchange items and currency with fellow cultivators.",
            ]
        )
        embed = _make_embed(
            "guide",
            f"{player.name}'s Main Menu",
            menu_description,
            discord.Colour.blurple(),
            include_icon=True,
        )
        await self._respond_interaction(
            interaction,
            embed=embed,
            view=view,
        )

    async def _open_trade_menu(
        self,
        interaction: discord.Interaction,
        *,
        return_callback: SimpleCallback | None,
        player: PlayerProgress | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed(
                "error",
                "Guild Only",
                "Use this in a server.",
                discord.Colour.red(),
            )
            await self._respond_interaction(interaction, embed=embed)
            return

        await self.ensure_guild_loaded(guild.id)
        if player is None:
            player = await self._fetch_player(guild.id, interaction.user.id)
        if player is None:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed)
            return

        players_bucket = await self.store.get(guild.id, "players")
        partner_options: list[TradePartnerOption] = []
        for user_id_str, payload in players_bucket.items():
            try:
                user_id = int(user_id_str)
            except (TypeError, ValueError):
                continue
            if user_id == player.user_id:
                continue
            raw_name = payload.get("name") if isinstance(payload, Mapping) else None
            display_name = str(raw_name or f"Cultivator {user_id}")
            member = guild.get_member(user_id)
            if member and member.display_name:
                display_name = member.display_name
            stage = None
            if isinstance(payload, Mapping):
                stage_value = payload.get("cultivation_stage")
                if isinstance(stage_value, str) and stage_value:
                    stage = stage_value.replace("-", " ").title()
            partner_options.append(
                TradePartnerOption(
                    user_id=user_id,
                    display_name=display_name,
                    description=stage,
                )
            )
        partner_options.sort(key=lambda option: option.display_name.lower())

        if not partner_options:
            embed = _make_embed(
                "warning",
                "No Trading Partners",
                "No other cultivators have registered yet.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        async def load_partner(user_id: int) -> Optional[PlayerProgress]:
            if user_id == player.user_id:
                return None
            data = await self.store.get_player(guild.id, user_id)
            if not data:
                return None
            partner = PlayerProgress(**data)
            self.state.register_player(partner)
            return partner

        async def handle_accept(
            response_interaction: discord.Interaction,
            confirmation_view: TradeConfirmationView,
        ) -> None:
            proposal = confirmation_view.proposal
            guild_obj = response_interaction.guild
            message = confirmation_view.message or response_interaction.message
            if guild_obj is None or message is None:
                notice = "Trade negotiations can only continue inside a server channel."
                if response_interaction.response.is_done():
                    try:
                        await response_interaction.followup.send(
                            notice, ephemeral=True
                        )
                    except discord.HTTPException:
                        pass
                else:
                    await response_interaction.response.send_message(
                        notice, ephemeral=True
                    )
                confirmation_view.stop()
                return

            if not response_interaction.response.is_done():
                try:
                    await response_interaction.response.defer()
                except discord.HTTPException:
                    pass

            latest_initiator = await self._fetch_player(
                guild_obj.id, proposal.initiator_id
            )
            latest_partner = await self._fetch_player(
                guild_obj.id, proposal.partner_id
            )
            if latest_initiator is None or latest_partner is None:
                confirmation_view.disable()
                summary = self._trade_summary_embed(
                    "Trade Failed",
                    "One of the cultivators could not be found.",
                    proposal,
                    footer="No items or currency were exchanged.",
                    colour=discord.Colour.red(),
                )
                content = (
                    f"The trade between <@{proposal.initiator_id}> and "
                    f"<@{proposal.partner_id}> could not be completed."
                )
                try:
                    await message.edit(content=content, embed=summary, view=confirmation_view)
                except discord.HTTPException:
                    pass
                confirmation_view.stop()
                try:
                    await response_interaction.followup.send(
                        "One of the cultivators could not be found.", ephemeral=True
                    )
                except discord.HTTPException:
                    pass
                return

            thread_name = (
                f"Trade — {proposal.initiator_name} & {proposal.partner_name}"
            )[:90]
            try:
                thread = await message.create_thread(
                    name=thread_name, auto_archive_duration=60
                )
            except discord.HTTPException:
                confirmation_view.disable()
                summary = self._trade_summary_embed(
                    "Trade Failed",
                    "A negotiation thread could not be created.",
                    proposal,
                    footer="No items or currency were exchanged.",
                    colour=discord.Colour.red(),
                )
                content = (
                    f"The trade between <@{proposal.initiator_id}> and "
                    f"<@{proposal.partner_id}> could not be started."
                )
                try:
                    await message.edit(content=content, embed=summary, view=confirmation_view)
                except discord.HTTPException:
                    pass
                confirmation_view.stop()
                try:
                    await response_interaction.followup.send(
                        "The trade could not be started.", ephemeral=True
                    )
                except discord.HTTPException:
                    pass
                return

            negotiation_view = await self._start_trade_negotiation(
                guild_obj.id, thread, proposal, latest_initiator, latest_partner
            )
            if negotiation_view is None:
                confirmation_view.disable()
                summary = self._trade_summary_embed(
                    "Trade Failed",
                    "The negotiation controls could not be sent.",
                    proposal,
                    footer="No items or currency were exchanged.",
                    colour=discord.Colour.red(),
                )
                content = (
                    f"The trade between <@{proposal.initiator_id}> and "
                    f"<@{proposal.partner_id}> could not be started."
                )
                try:
                    await message.edit(content=content, embed=summary, view=confirmation_view)
                except discord.HTTPException:
                    pass
                confirmation_view.stop()
                try:
                    await response_interaction.followup.send(
                        "The trade could not be started.", ephemeral=True
                    )
                except discord.HTTPException:
                    pass
                return

            confirmation_view.disable()
            summary = self._trade_summary_embed(
                "Negotiation Started",
                (
                    f"{proposal.partner_name} accepted {proposal.initiator_name}'s trade. "
                    f"Continue negotiating in {thread.mention}."
                ),
                proposal,
                footer="Adjust the trade within the negotiation thread before both parties press Ready.",
                colour=discord.Colour.from_str("#38bdf8"),
            )
            content = (
                f"Trade negotiation between <@{proposal.initiator_id}> and "
                f"<@{proposal.partner_id}> now continues in {thread.mention}."
            )
            try:
                await message.edit(content=content, embed=summary, view=confirmation_view)
            except discord.HTTPException:
                pass
            confirmation_view.message = message
            try:
                await response_interaction.followup.send(
                    f"Created negotiation thread: {thread.mention}", ephemeral=True
                )
            except discord.HTTPException:
                pass
            confirmation_view.stop()

        async def handle_decline(
            response_interaction: discord.Interaction,
            confirmation_view: TradeConfirmationView,
        ) -> None:
            confirmation_view.disable()
            proposal = confirmation_view.proposal
            declined_by = response_interaction.user.mention
            summary = self._trade_summary_embed(
                "Trade Declined",
                f"{declined_by} declined the trade request.",
                proposal,
                footer="No items or currency were exchanged.",
                colour=discord.Colour.from_str("#f87171"),
            )
            content = (
                f"{declined_by} declined the trade from <@{proposal.initiator_id}>."
            )
            try:
                await response_interaction.response.edit_message(
                    content=content,
                    embed=summary,
                    view=confirmation_view,
                )
            except discord.HTTPException:
                pass
            else:
                confirmation_view.message = response_interaction.message
            await response_interaction.followup.send(
                "You declined the trade request.", ephemeral=True
            )
            confirmation_view.stop()

        async def submit_trade(
            trade_interaction: discord.Interaction, view: TradeBuilderView
        ) -> None:
            if view.partner is None:
                view.status_message = "Select a trading partner first."
                await view._edit(trade_interaction)
                return
            latest_initiator = await self._fetch_player(guild.id, view.initiator.user_id)
            latest_partner = await self._fetch_player(guild.id, view.partner.user_id)
            if latest_initiator is None or latest_partner is None:
                view.status_message = "One of the cultivators could not be loaded."
                await view._edit(trade_interaction)
                return
            view.update_profiles(initiator=latest_initiator, partner=latest_partner)
            proposal = view.as_proposal()
            valid, error = self._validate_trade_configuration(
                latest_initiator, latest_partner, proposal
            )
            if not valid:
                view.status_message = error or "This trade cannot be completed."
                await view._edit(trade_interaction)
                return
            channel = trade_interaction.channel
            if channel is None:
                view.status_message = (
                    "Trade requests can only be sent from visible guild channels."
                )
                await view._edit(trade_interaction)
                return

            view.status_message = f"Trade request sent to {proposal.partner_name}."
            view.refresh_selects()
            await view._edit(trade_interaction)

            confirmation_view = TradeConfirmationView(
                owner_id=proposal.partner_id,
                proposal=proposal,
                on_accept=handle_accept,
                on_decline=handle_decline,
            )
            embed = self._trade_summary_embed(
                "Trade Proposal",
                f"{proposal.initiator_name} proposes a trade with {proposal.partner_name}.",
                proposal,
                footer="Accept to exchange the listed items and currency.",
                colour=discord.Colour.gold(),
            )
            content = (
                f"<@{proposal.partner_id}>, {trade_interaction.user.mention} has "
                "sent you a trade proposal."
            )
            try:
                message = await channel.send(
                    content=content, embed=embed, view=confirmation_view
                )
            except discord.HTTPException:
                confirmation_view.stop()
                view.status_message = "Failed to send the trade request."
                await view._edit(trade_interaction)
                return

            confirmation_view.message = message

        view = TradeBuilderView(
            interaction.user.id,
            player=player,
            items=self.state.items,
            currencies=self.state.currencies,
            partner_options=partner_options,
            partner_loader=load_partner,
            submit_callback=submit_trade,
            return_callback=return_callback,
        )
        embed = view.build_embed()
        await self._respond_interaction(
            interaction, embed=embed, view=view, ephemeral=True
        )
        try:
            if interaction.type is InteractionType.component:
                view.message = interaction.message
            else:
                view.message = await interaction.original_response()
        except discord.HTTPException:
            view.message = None

    async def _handle_breakthrough_flow(
        self,
        interaction: discord.Interaction,
        *,
        player: PlayerProgress | None = None,
        selected_path: CultivationPath | None = None,
        ephemeral: bool = True,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed(
                "error",
                "Guild Only",
                "Attempt breakthroughs within a server channel.",
                discord.Colour.red(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=ephemeral)
            return

        await self.ensure_guild_loaded(guild.id)
        if player is None:
            player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=ephemeral)
            return

        if selected_path is None:
            path_choice = CultivationPath.from_value(
                player.active_path or CultivationPath.QI.value
            )
        else:
            path_choice = (
                selected_path
                if isinstance(selected_path, CultivationPath)
                else CultivationPath.from_value(selected_path)
            )

        path_themes: dict[CultivationPath, dict[str, object]] = {
            CultivationPath.QI: {
                "section": "cultivation",
                "colour": "#8b5cf6",
                "success_title": "BREAKTHROUGH",
                "success_flavour": (
                    "{next_realm_success} Step into **{next_stage}**."
                ),
                "chronicle_label": "{next_realm_title} Tribulation Chronicle",
                "success_lines": [
                    ("#c084fc", "╔═══════════════ {next_realm_tribulation} ═══════════════╗"),
                    ("#38bdf8", "{next_realm_success}"),
                    ("#f5d0fe", "Focus: {next_realm_scope}"),
                    ("#38bdf8", "Ascended Realm: {next_stage}"),
                ],
                "failure_title": "{next_realm_title} Tribulation Rebuffed",
                "failure_flavour": (
                    "{next_realm_failure} {next_realm_readiness}"
                ),
                "failure_lines": [
                    ("#f97316", "{next_realm_failure}"),
                    ("#f43f5e", "{current_realm_scope}"),
                    ("#fb7185", "{next_realm_readiness}"),
                ],
                "not_ready_title": "{next_realm_title} Out of Reach",
                "not_ready_flavour": (
                    "{next_realm_readiness} {next_realm_power}"
                ),
                "not_ready_lines": [
                    ("#a855f7", "{next_realm_readiness}"),
                    ("#f0abfc", "Aim: {next_realm_scope}"),
                ],
                "stage_field_label": "New Cultivation Stage",
                "resonance_label": "Lingering Resonance",
                "resonance_text": (
                    "{next_realm_resonance}\n"
                    "• Meridian constellations realign to bolster your core stats."
                ),
            },
            CultivationPath.BODY: {
                "section": "combat",
                "colour": "#f97316",
                "success_title": "BREAKTHROUGH",
                "success_flavour": (
                    "{next_realm_success} Step into **{next_stage}**."
                ),
                "chronicle_label": "{next_realm_title} Tempering Chronicle",
                "success_lines": [
                    ("#fb923c", "╔═══════════════ {next_realm_tribulation} ═══════════════╗"),
                    ("#f97316", "{next_realm_success}"),
                    ("#facc15", "Scope: {next_realm_scope}"),
                    ("#fed7aa", "Tempered Form: {next_stage}"),
                ],
                "failure_title": "{next_realm_title} Furnace Rebuffed",
                "failure_flavour": (
                    "{next_realm_failure} {next_realm_readiness}"
                ),
                "failure_lines": [
                    ("#f97316", "{next_realm_failure}"),
                    ("#f87171", "{current_realm_scope}"),
                    ("#fed7aa", "{next_realm_readiness}"),
                ],
                "not_ready_title": "{next_realm_title} Flesh Unrefined",
                "not_ready_flavour": (
                    "{next_realm_readiness} {next_realm_power}"
                ),
                "not_ready_lines": [
                    ("#fb923c", "{next_realm_readiness}"),
                    ("#facc15", "Temper to withstand {next_realm_scope}"),
                ],
                "stage_field_label": "New Body Refinement Stage",
                "resonance_label": "Battle Echoes",
                "resonance_text": (
                    "{next_realm_resonance}\n"
                    "• Muscles resonate with draconic might, boosting your combat prowess.\n"
                    "• Every strike now carries a lingering volcanic aftershock."
                ),
            },
            CultivationPath.SOUL: {
                "section": "soul",
                "colour": "#9b59b6",
                "success_title": "BREAKTHROUGH",
                "success_flavour": (
                    "{next_realm_success} Step into **{next_stage}**."
                ),
                "chronicle_label": "{next_realm_title} Spirit Chronicle",
                "success_lines": [
                    ("#c084fc", "╔═══════════════ {next_realm_tribulation} ═══════════════╗"),
                    ("#a855f7", "{next_realm_success}"),
                    ("#d8b4fe", "Scope: {next_realm_scope}"),
                    ("#c4b5fd", "Refined Consciousness: {next_stage}"),
                ],
                "failure_title": "{next_realm_title} Mindsea Rebuffed",
                "failure_flavour": (
                    "{next_realm_failure} {next_realm_readiness}"
                ),
                "failure_lines": [
                    ("#f472b6", "{next_realm_failure}"),
                    ("#f97316", "{current_realm_scope}"),
                    ("#a855f7", "{next_realm_readiness}"),
                ],
                "not_ready_title": "{next_realm_title} Lattice Unsteady",
                "not_ready_flavour": (
                    "{next_realm_readiness} {next_realm_power}"
                ),
                "not_ready_lines": [
                    ("#a855f7", "{next_realm_readiness}"),
                    ("#fbcfe8", "Envision {next_realm_scope} within your sea of consciousness."),
                ],
                "stage_field_label": "New Soul Purification Stage",
                "resonance_label": "Dream Echoes",
                "resonance_text": (
                    "{next_realm_resonance}\n"
                    "• Vision fragments linger, guiding future breakthroughs."
                ),
            },
        }

        theme = path_themes[path_choice]

        def _hex_to_ansi(colour_hex: str) -> str:
            colour = str(colour_hex).lstrip("#")
            if len(colour) != 6:
                return ""
            try:
                r = int(colour[0:2], 16)
                g = int(colour[2:4], 16)
                b = int(colour[4:6], 16)
            except ValueError:
                return ""
            return f"\u001b[38;2;{r};{g};{b}m"

        def _render_tribulation(
            lines: Sequence[tuple[str, str]],
            context: Mapping[str, str],
        ) -> str:
            if not lines:
                return ""
            formatted: list[str] = []
            for colour_hex, text_line in lines:
                prefix = _hex_to_ansi(str(colour_hex))
                body = str(text_line).format_map(context)
                if prefix:
                    formatted.append(f"{prefix}{body}")
                else:
                    formatted.append(body)
            return "```ansi\n" + "\n".join(formatted) + "\n```"

        if path_choice is CultivationPath.BODY:
            stage_key = player.body_cultivation_stage
            stage_candidates = list(self.state.body_cultivation_stages.values())
        elif path_choice is CultivationPath.SOUL:
            stage_key = player.soul_cultivation_stage
            stage_candidates = list(self.state.soul_cultivation_stages.values())
        else:
            stage_key = player.cultivation_stage
            stage_candidates = list(self.state.qi_cultivation_stages.values())

        current_stage = self.state.get_stage(stage_key, path_choice)
        if current_stage is None:
            embed = _make_embed(
                "error",
                "Unknown Stage",
                "Your current stage is not configured.",
                discord.Colour.red(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=ephemeral)
            return

        if not stage_candidates:
            stage_candidates = [
                stage
                for stage in self.state.iter_all_stages()
                if CultivationPath.from_value(stage.path) is path_choice
            ]

        ordered_stages = sorted(stage_candidates, key=lambda x: x.ordering_tuple)
        try:
            idx = next(
                i for i, stage in enumerate(ordered_stages) if stage.key == current_stage.key
            )
            next_stage = ordered_stages[idx + 1]
        except (StopIteration, IndexError):
            await self._respond_interaction(
                interaction,
                content="You have reached the peak configured for this path.",
                ephemeral=ephemeral,
            )
            return

        if (
            current_stage.phase is CultivationPhase.PEAK
            and next_stage.realm != current_stage.realm
        ):
            target_realm = next_stage.realm
            realm_candidates = [
                stage for stage in ordered_stages if stage.realm == target_realm
            ]
            if realm_candidates:
                next_stage = min(
                    realm_candidates, key=lambda stage: stage.phase.order_index
                )

        realm_context = _compose_realm_context(current_stage, next_stage)
        context_map = _SafeFormatDict(realm_context)
        next_label = next_stage.combined_name

        if not cultivation_ready(player, path_choice.value):
            readiness = context_map["next_realm_readiness"]
            description_lines = [
                (
                    f"{SECTION_ICONS['progress']} Breakthrough not ready — {readiness}."
                ),
                (
                    f"{SECTION_ICONS['stage']} Target: **{context_map['next_stage']}**."
                ),
            ]
            await self._send_cultivation_menu(
                interaction,
                player=player,
                title=str(theme["not_ready_title"]).format_map(context_map),
                description="\n".join(description_lines),
                colour=discord.Colour.orange(),
                ephemeral=ephemeral,
            )
            return

        previous_stats = (
            player.stats.copy() if isinstance(player.stats, Stats) else Stats()
        )

        success = attempt_breakthrough(player, current_stage, path_choice.value)
        member = await self._resolve_member(guild, interaction.user)

        if success:
            stat_gain_lines: list[str] = []
            qi_stage_obj: CultivationStage | None = None
            body_stage_obj: CultivationStage | None = None
            soul_stage_obj: CultivationStage | None = None
            if path_choice is CultivationPath.BODY:
                player.body_cultivation_stage = next_stage.key
                player.combat_exp = 0
                required_exp = getattr(next_stage, "exp_required", None)
                if required_exp is None:
                    player.combat_exp_required += 100
                else:
                    try:
                        player.combat_exp_required = max(1, int(required_exp))
                    except (TypeError, ValueError):
                        player.combat_exp_required += 100
                qi_stage_obj = self.state.get_stage(
                    player.cultivation_stage, CultivationPath.QI
                )
                soul_stage_obj = self.state.get_stage(
                    player.soul_cultivation_stage, CultivationPath.SOUL
                )
                if qi_stage_obj is None:
                    qi_stage_obj = next_stage
                player.recalculate_stage_stats(
                    qi_stage_obj,
                    next_stage,
                    soul_stage_obj,
                )
                body_stage_obj = next_stage
            elif path_choice is CultivationPath.SOUL:
                player.soul_cultivation_stage = next_stage.key
                player.soul_exp = 0
                required_exp = getattr(next_stage, "exp_required", None)
                if required_exp is None:
                    player.soul_exp_required += 100
                else:
                    try:
                        player.soul_exp_required = max(1, int(required_exp))
                    except (TypeError, ValueError):
                        player.soul_exp_required += 100
                qi_stage_obj = self.state.get_stage(
                    player.cultivation_stage, CultivationPath.QI
                )
                body_stage_obj = self.state.get_stage(
                    player.body_cultivation_stage, CultivationPath.BODY
                )
                if qi_stage_obj is None:
                    qi_stage_obj = next_stage
                player.recalculate_stage_stats(
                    qi_stage_obj,
                    body_stage_obj,
                    next_stage,
                )
                soul_stage_obj = next_stage
            else:
                player.cultivation_stage = next_stage.key
                player.cultivation_exp = 0
                required_exp = getattr(next_stage, "exp_required", None)
                if required_exp is None:
                    player.cultivation_exp_required += 100
                else:
                    try:
                        player.cultivation_exp_required = max(1, int(required_exp))
                    except (TypeError, ValueError):
                        player.cultivation_exp_required += 100
                qi_stage_obj = next_stage
                body_stage_obj = self.state.get_stage(
                    player.body_cultivation_stage, CultivationPath.BODY
                )
                soul_stage_obj = self.state.get_stage(
                    player.soul_cultivation_stage, CultivationPath.SOUL
                )
                player.recalculate_stage_stats(
                    next_stage,
                    body_stage_obj,
                    soul_stage_obj,
                )

            stat_gain_lines = _build_stat_gain_lines(previous_stats, player.stats)
            self.state.ensure_player_energy(player, force_refill=True)

            if member is not None:
                await self._sync_player_path_roles(guild, member, player)

            await self._save_player(guild.id, player)

            description_lines: list[str] = [context_map["next_realm_success"]]
            if stat_gain_lines:
                description_lines.append("\n".join(stat_gain_lines))

            normalized_next_key = str(next_stage.key or "").strip().lower().replace(
                "_", "-"
            )
            normalized_next_realm = (
                str(next_stage.realm or "")
                .strip()
                .lower()
                .replace("_", " ")
                .replace("-", " ")
            )
            should_reveal_stats = (
                path_choice is CultivationPath.QI
                and current_stage.is_mortal
                and next_stage.phase is CultivationPhase.INITIAL
                and (
                    normalized_next_key == "qi-condensation"
                    or normalized_next_realm == "qi condensation"
                )
            )

            reveal_sections: list[str] = []
            if should_reveal_stats:
                mortal_stage_flag = _player_in_mortal_realm(player, stage=next_stage)
                martial_block = _martial_soul_block(
                    player, mortal_stage=mortal_stage_flag
                )
                reveal_sections.append(
                    "\n".join(
                        [
                            _label_with_icon("martial_soul", "Martial Souls"),
                            martial_block,
                        ]
                    )
                )

            if reveal_sections:
                description_lines.extend(reveal_sections)

            await self._send_cultivation_menu(
                interaction,
                player=player,
                title=str(theme["success_title"]).format_map(context_map),
                description="\n\n".join(description_lines) if description_lines else None,
                colour=discord.Colour.from_str(str(theme["colour"])),
                ephemeral=ephemeral,
                include_path_fields=True,
                show_extra_sections=False,
            )
            return

        await self._save_player(guild.id, player)

        failure_lines = [
            f"{SECTION_ICONS['cultivation']} Breakthrough destabilized. Regather your understanding.",
            f"{SECTION_ICONS['stage']} Target: **{next_label}**.",
            f"{SECTION_ICONS['progress']} {context_map['next_realm_readiness']}",
        ]

        await self._send_cultivation_menu(
            interaction,
            player=player,
            title=str(theme["failure_title"]).format_map(context_map),
            description="\n".join(failure_lines),
            colour=discord.Colour.dark_red(),
            ephemeral=ephemeral,
        )

    def _build_return_view(
        self,
        interaction: discord.Interaction,
        callback: SimpleCallback | None,
        *,
        timeout: float = 90.0,
    ) -> ReturnButtonView | None:
        if callback is None:
            return None
        return ReturnButtonView(interaction.user.id, callback=callback, timeout=timeout)

    def _ensure_player_coordinate(self, player: PlayerProgress) -> TileCoordinate:
        coordinate = player.tile_coordinate()
        if coordinate is not None:
            return coordinate
        candidate: str | None = player.world_position
        if not candidate and player.location:
            location = self.state.locations.get(player.location)
            if location and location.map_coordinate:
                candidate = location.map_coordinate
        if not candidate:
            candidate = "0:0:0"
        try:
            coordinate = TileCoordinate.from_key(candidate)
        except ValueError:
            coordinate = TileCoordinate(0, 0, 0)
        player.mark_tile_explored(coordinate, surveyed=True)
        mapped = self.state.tile_locations.get(coordinate.to_key())
        if mapped:
            player.location = mapped
        return coordinate

    def _build_travel_embed(
        self,
        player: PlayerProgress,
        *,
        path: TravelPath | None = None,
        events: Sequence[TravelEvent] = (),
        headline: str | None = None,
    ) -> discord.Embed:
        self.state.ensure_world_map()
        coordinate = self._ensure_player_coordinate(player)
        world_map = self.state.world_map
        tile = world_map.tile_for_key(coordinate.to_key()) if world_map else None
        location = self.state.location_for_tile(coordinate.to_key())
        energy_cap = self.state.ensure_player_energy(player)
        description = headline or "Chart the wilderness, weigh your options, and decide the next leg of your journey."
        embed = _make_embed(
            "travel",
            "Expedition Planner",
            description,
            discord.Colour.from_str("#4c6ef5"),
            include_icon=True,
        )
        def _join_lines(lines: list[str]) -> str:
            cleaned = [line for line in lines if line]
            if cleaned:
                return "\n".join(cleaned)
            return coloured("No data available", colour="gray", dim=True)

        def _emoji_pair(label: str, value: str, *, emoji: str) -> str:
            return f"{emoji} **{label}:** {value}"

        event_colours: dict[EncounterBucket, str] = {
            EncounterBucket.AMBIENT_FAUNA: "green",
            EncounterBucket.HOSTILE_PATROL: "red",
            EncounterBucket.ROGUE_CULTIVATOR: "magenta",
            EncounterBucket.FACTION_AGENT: "yellow",
            EncounterBucket.WORLD_BOSS_SCOUT: "orange",
        }

        base_message = headline or description
        log_lines: list[str] = [ansi_colour(base_message, "cyan", bold=True)]
        if path and path.segments:
            destination = path.segments[-1].coordinates[-1]
            segment_count = len(path.segments)
            log_lines.append(
                ansi_colour(
                    f"Route prepared → {destination.to_key()} ({segment_count} segment{'s' if segment_count != 1 else ''})",
                    "yellow",
                    bold=True,
                )
            )
            for index, segment in enumerate(path.segments[:5], start=1):
                final_coord = segment.coordinates[-1]
                summary = (
                    f"[{index}] {segment.mode.name.title()} → {final_coord.to_key()}"
                    f" | {segment.cost.time_seconds:.1f}s · noise {segment.cost.noise_generated:.2f}"
                )
                log_lines.append(ansi_colour(summary, "white"))
            if len(path.segments) > 5:
                remaining = len(path.segments) - 5
                log_lines.append(
                    ansi_colour(f"… {remaining} more segment{'s' if remaining != 1 else ''} queued", "gray", dim=True)
                )
        if events:
            for event in events[-10:]:
                bucket = event.bucket.name.replace("_", " ").title() if event.bucket else "Event"
                colour = event_colours.get(event.bucket, "teal")
                log_lines.append(
                    ansi_colour(f"[{bucket}] {event.description}", colour)
                )
        history_entries = list(player.travel_log[-5:])
        if history_entries:
            log_lines.append(ansi_colour("Recent Tale", "gray", dim=True))
            narrative = build_travel_narrative(history_entries)
            log_lines.append(ansi_colour(narrative, "white"))
        if not log_lines:
            log_lines.append(ansi_colour("Awaiting expedition activity…", "gray", dim=True))
        log_block = "```ansi\n" + "\n".join(log_lines) + "\n```"

        tile_lines: list[str] = [
            _emoji_pair("Coordinate", f"`{coordinate.to_key()}`", emoji="🧭")
        ]
        if location:
            name = f"**{location.name}**"
            if location.is_safe or (tile and tile.is_safe):
                name += " (Sanctuary)"
            tile_lines.append(_emoji_pair("Location", name, emoji="📍"))
        elif tile and tile.is_safe:
            tile_lines.append(
                _emoji_pair(
                    "Location",
                    "**Unmarked Sanctuary**",
                    emoji="📍",
                )
            )
        if tile:
            tile_type = (
                "Point of Interest"
                if tile.category is TileCategory.POINT_OF_INTEREST
                else "Normal"
            )
            tile_lines.append(
                _emoji_pair(
                    "Tile Type",
                    f"{tile_type} · Elev {int(tile.elevation)} m",
                    emoji="⛰️",
                )
            )
            tile_lines.append(
                _emoji_pair(
                    "Qi Density",
                    f"{tile.environmental.qi_density:.2f}",
                    emoji="🌌",
                )
            )
            tile_lines.append(
                _emoji_pair(
                    "Hazard Level",
                    f"{tile.environmental.hazard_level:.2f}",
                    emoji="⚠️",
                )
            )
        embed.add_field(
            name=_label_with_icon("travel", "Current Tile", include_icon=True),
            value=_join_lines(tile_lines),
            inline=False,
        )

        if location:
            intel_lines: list[str] = []
            if location.quests:
                quest_names = [
                    self.state.quests.get(key).name
                    for key in location.quests
                    if key in self.state.quests
                ][:3]
                if quest_names:
                    intel_lines.append("Quests: " + ", ".join(quest_names))
            if location.npcs:
                npc_names = [npc.name for npc in location.npcs[:3]]
                if npc_names:
                    intel_lines.append("NPCs: " + ", ".join(npc_names))
            if location.enemies:
                enemy_names = [
                    self.state.enemies.get(key).name
                    for key in location.enemies
                    if key in self.state.enemies
                ][:3]
                if enemy_names:
                    intel_lines.append("Enemies: " + ", ".join(enemy_names))
            if location.wander_loot:
                loot_labels: list[str] = []
                for loot_key in list(location.wander_loot.keys())[:3]:
                    if loot_key in self.state.items:
                        loot_labels.append(self.state.items[loot_key].name)
                    elif loot_key in self.state.currencies:
                        loot_labels.append(self.state.currencies[loot_key].name)
                    else:
                        loot_labels.append(loot_key)
                if loot_labels:
                    intel_lines.append("Loot: " + ", ".join(loot_labels))
            if intel_lines:
                embed.add_field(
                    name=_label_with_icon("quest", "Local Intel"),
                    value=_join_lines([f"🟡 {line}" for line in intel_lines]),
                    inline=False,
                )

        mode_lines = [
            _emoji_pair(
                "Movement",
                player.active_travel_mode.name.title(),
                emoji="🌀",
            ),
            _emoji_pair(
                "Energy",
                f"{player.energy:.1f}/{energy_cap:.1f}",
                emoji="🔥",
            ),
            _emoji_pair(
                "Noise Meter",
                f"{player.travel_noise.value:.2f}",
                emoji="🔔",
            ),
        ]
        if path:
            mode_lines.append(
                _emoji_pair(
                    "Segments Traversed",
                    str(len(path.segments)),
                    emoji="🧭",
                )
            )
        embed.add_field(
            name=_label_with_icon("travel", "Travel Readings"),
            value=_join_lines(mode_lines),
            inline=True,
        )

        fog = self.state.fog_of_war_for(player.user_id)

        if path:
            total = path.total_cost
            cost_lines = [
                _emoji_pair(
                    "Travel Time",
                    f"{total.time_seconds:.1f}s",
                    emoji="⏱️",
                ),
                _emoji_pair(
                    "Energy Cost",
                    f"{total.stamina_cost:.1f}",
                    emoji="💪",
                ),
                _emoji_pair(
                    "Noise Gained",
                    f"{total.noise_generated:.2f}",
                    emoji="📣",
                ),
                _emoji_pair(
                    "Corruption Exposure",
                    f"{total.corruption_exposure:.2f}",
                    emoji="☣️",
                ),
            ]
            embed.add_field(
                name=_label_with_icon("travel", "Segment Cost"),
                value=_join_lines(cost_lines),
                inline=True,
            )

        recent_summary = (
            "📜 Expedition log updated below with the latest travel events."
            if events
            else "📜 No travel events recorded yet."
        )
        embed.add_field(
            name=_label_with_icon("travel", "Recent Events", include_icon=True),
            value=_join_lines([recent_summary]),
            inline=False,
        )

        if tile and tile.points_of_interest:
            poi_lines = [f"✨ {poi}" for poi in tile.points_of_interest[:6]]
            embed.add_field(
                name=_label_with_icon("travel", "Points of Interest"),
                value=_join_lines(poi_lines),
                inline=False,
            )

        world_lines: list[str] = [f"🕒 {self.state.world_time_summary()}"]
        if self.state.world_map and self.state.world_map.active_season():
            season = self.state.world_map.active_season()
            if season:
                world_lines.append(
                    _emoji_pair("Season", season.name, emoji="🍂")
                )
        embed.add_field(
            name=_label_with_icon("travel", "World Conditions", include_icon=True),
            value=_join_lines(world_lines),
            inline=True,
        )

        if player.last_expedition_plan:
            plan = player.last_expedition_plan
            step_summary = ", ".join(step.step_type.name.title() for step in plan.steps)
            expedition_lines = [
                _emoji_pair(
                    "Destination",
                    f"`{plan.destination.to_key()}`",
                    emoji="🎯",
                ),
                _emoji_pair(
                    "Steps",
                    step_summary,
                    emoji="🛤️",
                ),
                _emoji_pair(
                    "Estimated Risk",
                    f"{plan.risk_rating:.2f}",
                    emoji="⚖️",
                ),
            ]
            if plan.objectives:
                expedition_lines.append(
                    _emoji_pair(
                        "Objectives",
                        "; ".join(plan.objectives[:2]),
                        emoji="📌",
                    )
                )
            embed.add_field(
                name=_label_with_icon("travel", "Last Expedition"),
                value=_join_lines(expedition_lines),
                inline=False,
            )

        if world_map:
            renderer = MiniMapRenderer(world_map)
            minimap = renderer.render(coordinate, fog=fog, size=14)
            minimap_lines = [
                minimap,
                "",
                f"📍 You @ `{coordinate.to_key()}`",
                "N ↑ | E →",
                "Legend:",
                "⬛ undiscovered · 🟩 known tile · ✴️ point of interest · 🏠 sanctuary",
                "Terrain: plains (rolling fields) · desert (arid dunes) · coast (shorelines)",
                "         urban (settlements) · subterranean (underground) · sky (floating isles)",
            ]
            embed.add_field(
                name=_label_with_icon("travel", "Logs", include_icon=True),
                value=log_block,
                inline=False,
            )
            embed.add_field(
                name=_label_with_icon("travel", "Mini Map", include_icon=True),
                value="\n".join(minimap_lines),
                inline=False,
            )

        embed.set_footer(text="Use movement, camping, and foraging to reveal new opportunities.")
        return embed

    async def _open_travel_menu(
        self,
        interaction: discord.Interaction,
        *,
        return_callback: SimpleCallback | None = None,
        player: PlayerProgress | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed(
                "error",
                "Guild Only",
                "Use this in a server.",
                discord.Colour.red(),
            )
            await self._respond_interaction(interaction, embed=embed)
            return

        await self.ensure_guild_loaded(guild.id)
        if player is None:
            player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(
                interaction,
                embed=embed,
                view=self._build_return_view(interaction, return_callback),
            )
            return
        channel = interaction.channel
        if isinstance(channel, discord.abc.GuildChannel):
            location = self.state.get_location_for_channel(channel.id)
            if location is None:
                embed = _make_embed(
                    "warning",
                    "Travel Unavailable",
                    "This channel has not been designated as a travel zone yet. Ask an admin to run /create_zone here.",
                    discord.Colour.orange(),
                )
                await self._respond_interaction(
                    interaction,
                    embed=embed,
                    view=self._build_return_view(interaction, return_callback),
                )
                return
            location = self.state.ensure_channel_location(
                channel.id, channel_name=getattr(channel, "name", None)
            )
            anchor: TileCoordinate | None = None
            if location.map_coordinate:
                try:
                    anchor = TileCoordinate.from_key(location.map_coordinate)
                except ValueError:
                    anchor = None
            if anchor is None:
                anchor = self.state.channel_anchor(channel.id)
            if player.location != location.location_id or player.tile_coordinate() is None:
                player.mark_tile_explored(anchor, surveyed=True)
            player.location = location.location_id
        self.state.ensure_world_map()
        coordinate = self._ensure_player_coordinate(player)
        await self._save_player(guild.id, player)

        embed = self._build_travel_embed(
            player,
            headline="Survey the land, attune your movement technique, and decide the next expedition step.",
        )
        engine = self.state.travel_engine

        async def save_player_state(progress: PlayerProgress) -> None:
            await self._save_player(guild.id, progress)

        view = TravelNavigatorView(
            interaction.user.id,
            state=self.state,
            engine=engine,
            player_id=player.user_id,
            status_builder=lambda prog, travel_path, travel_events, message=None: self._build_travel_embed(
                prog,
                path=travel_path,
                events=travel_events,
                headline=message,
            ),
            save_callback=save_player_state,
            return_callback=return_callback,
        )

        await self._respond_interaction(
            interaction,
            embed=embed,
            view=view,
        )
        try:
            if interaction.type is InteractionType.component:
                view.message = interaction.message
            else:
                view.message = await interaction.original_response()
        except discord.HTTPException:
            view.message = None

    @app_commands.command(
        name="legacy_mark",
        description="Inscribe a mastered technique into your ancestral legacy",
    )
    @app_commands.describe(
        technique_key="Key of the technique you wish to pass to your heir",
    )
    @app_commands.guild_only()
    async def legacy_mark(
        self, interaction: discord.Interaction, technique_key: str
    ) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed(
                "error",
                "Sect Boundaries",
                "Legacies can only be carved within a sect's halls.",
                discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        normalized = str(technique_key).strip()
        if normalized not in player.cultivation_technique_keys:
            embed = _make_embed(
                "warning",
                "Unmastered Technique",
                "Only techniques you have woven into your dao can be inscribed.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        if not player.add_legacy_technique(normalized):
            embed = _make_embed(
                "warning",
                "Tablet Already Etched",
                "That inscription already glows upon your legacy tablet.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        await self._save_player(guild.id, player)
        technique = self.state.cultivation_techniques.get(normalized)
        technique_name = technique.name if technique else normalized
        embed = _make_embed(
            "progress",
            "Legacy Tablet Etched",
            (
                "You guide ancestral qi to bind "
                f"**{technique_name}** for the next generation."
            ),
            discord.Colour.teal(),
        )
        await self._respond_interaction(interaction, embed=embed, ephemeral=True)

    @legacy_mark.autocomplete("technique_key")
    async def legacy_mark_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        guild = interaction.guild
        if guild is None:
            return []
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            return []
        query = current.lower().strip()
        choices: list[app_commands.Choice[str]] = []
        for key in player.cultivation_technique_keys:
            technique = self.state.cultivation_techniques.get(key)
            name = technique.name if technique else key
            if query and query not in key.lower() and query not in name.lower():
                continue
            label = f"{name} ({key})"
            choices.append(app_commands.Choice(name=label[:100], value=key))
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(
        name="legacy_unmark",
        description="Release an inscription from your legacy tablet",
    )
    @app_commands.describe(
        technique_key="Key of the legacy technique to remove",
    )
    @app_commands.guild_only()
    async def legacy_unmark(
        self, interaction: discord.Interaction, technique_key: str
    ) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed(
                "error",
                "Sect Boundaries",
                "Legacies can only be carved within a sect's halls.",
                discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        normalized = str(technique_key).strip()
        if not player.remove_legacy_technique(normalized):
            embed = _make_embed(
                "warning",
                "No Such Inscription",
                "That technique is not etched upon your legacy tablet.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        await self._save_player(guild.id, player)
        technique = self.state.cultivation_techniques.get(normalized)
        technique_name = technique.name if technique else normalized
        embed = _make_embed(
            "info",
            "Inscription Released",
            (
                "The carving of **{0}** fades, its dao returning to the river of fate."
            ).format(technique_name),
            discord.Colour.blurple(),
        )
        await self._respond_interaction(interaction, embed=embed, ephemeral=True)

    @legacy_unmark.autocomplete("technique_key")
    async def legacy_unmark_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        guild = interaction.guild
        if guild is None:
            return []
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            return []
        query = current.lower().strip()
        choices: list[app_commands.Choice[str]] = []
        for key in player.legacy_techniques:
            technique = self.state.cultivation_techniques.get(key)
            name = technique.name if technique else key
            if query and query not in key.lower() and query not in name.lower():
                continue
            label = f"{name} ({key})"
            choices.append(app_commands.Choice(name=label[:100], value=key))
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(
        name="legacy_assign",
        description="Bind a disciple as heir to your dao legacy",
    )
    @app_commands.guild_only()
    async def legacy_assign(
        self, interaction: discord.Interaction, heir: discord.Member
    ) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed(
                "error",
                "Sect Boundaries",
                "Heirs must be named within a sect.",
                discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        if heir.id == interaction.user.id:
            embed = _make_embed(
                "warning",
                "Solitary Thread",
                "You cannot name yourself as heir to your own dao.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        if not player.designate_legacy_heir(heir.id):
            embed = _make_embed(
                "warning",
                "Heir Already Bound",
                f"A karmic thread already links you to {heir.display_name}.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        await self._save_player(guild.id, player)
        embed = _make_embed(
            "progress",
            "Heir Thread Woven",
            (
                f"The sect records {heir.display_name} as the inheritor of your dao."
            ),
            discord.Colour.teal(),
        )
        await self._respond_interaction(interaction, embed=embed, ephemeral=True)

    @app_commands.command(
        name="legacy_revoke",
        description="Sever a disciple's claim on your dao legacy",
    )
    @app_commands.guild_only()
    async def legacy_revoke(
        self, interaction: discord.Interaction, heir: discord.Member
    ) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed(
                "error",
                "Sect Boundaries",
                "Heirs must be named within a sect.",
                discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        if not player.revoke_legacy_heir(heir.id):
            embed = _make_embed(
                "warning",
                "No Karmic Thread",
                f"{heir.display_name} is not currently named as your heir.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        await self._save_player(guild.id, player)
        embed = _make_embed(
            "info",
            "Heir Thread Severed",
            f"You release {heir.display_name} from inheriting your dao legacy.",
            discord.Colour.blurple(),
        )
        await self._respond_interaction(interaction, embed=embed, ephemeral=True)

    @app_commands.command(
        name="retire",
        description="Withdraw from the mortal sect and bequeath your legacy",
    )
    @app_commands.guild_only()
    async def retire(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed(
                "error",
                "Sect Boundaries",
                "Retirement may only be witnessed within a sect.",
                discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        if player.retired_at:
            embed = _make_embed(
                "warning",
                "Legacy Already Released",
                "Your dao has already stepped beyond the mortal coil.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        if not player.legacy_techniques and not player.legacy_traits:
            embed = _make_embed(
                "warning",
                "Empty Legacy Tablet",
                "Etch techniques or request legacy traits before retiring.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        if not player.legacy_heirs:
            embed = _make_embed(
                "warning",
                "No Named Heirs",
                "Name at least one heir before relinquishing your dao.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        fulfilled: list[tuple[str, list[str], list[str]]] = []
        missing: list[int] = []
        for heir_id in list(player.legacy_heirs):
            if heir_id == player.user_id:
                continue
            heir_player = await self._fetch_player(guild.id, heir_id)
            if not heir_player:
                missing.append(heir_id)
                continue
            granted_techniques, granted_traits = grant_legacy_to_heir(player, heir_player)
            await self._save_player(guild.id, heir_player)
            member = guild.get_member(heir_id)
            display_name = member.display_name if member else str(heir_id)
            fulfilled.append((display_name, granted_techniques, granted_traits))

        player.retired_at = time.time()
        await self._save_player(guild.id, player)

        def technique_label(key: str) -> str:
            technique = self.state.cultivation_techniques.get(key)
            return technique.name if technique else key

        def trait_label(key: str) -> str:
            trait = self.state.traits.get(key)
            return trait.name if trait else key

        description_lines = [
            "You allow your cultivation to settle, entrusting its echoes to the sect.",
        ]
        embed = _make_embed(
            "progress",
            "Dao Legacy Released",
            "\n".join(description_lines),
            discord.Colour.teal(),
        )

        if fulfilled:
            lines: list[str] = []
            for display_name, techniques, traits in fulfilled:
                segments: list[str] = []
                if techniques:
                    rendered = ", ".join(technique_label(key) for key in techniques)
                    segments.append(f"Techniques: {rendered}")
                if traits:
                    rendered = ", ".join(trait_label(key) for key in traits)
                    segments.append(f"Traits: {rendered}")
                if not segments:
                    segments.append("Their foundation already held each boon.")
                lines.append(f"**{display_name}** — {'; '.join(segments)}")
            embed.add_field(
                name="Heirs who received your dao",
                value="\n".join(lines),
                inline=False,
            )

        if missing:
            missing_lines = []
            for heir_id in missing:
                member = guild.get_member(heir_id)
                label = member.display_name if member else str(heir_id)
                missing_lines.append(
                    f"{label} was not present to receive your legacy."
                )
            embed.add_field(
                name="Karmic threads awaiting claim",
                value="\n".join(missing_lines),
                inline=False,
            )

        await self._respond_interaction(interaction, embed=embed, ephemeral=True)

    @app_commands.command(
        name="bond_mission",
        description="Complete your cooperative bond mission with your partner",
    )
    @app_commands.guild_only()
    async def bond_mission(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed(
                "error",
                "Sect Required",
                "Bond missions may only be undertaken within a sect.",
                discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        partner_id = player.bond_partner_id
        if not partner_id:
            embed = _make_embed(
                "warning",
                "No Bonded Partner",
                "Configure a bonded partner before attempting this mission.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        partner = await self._fetch_player(guild.id, partner_id)
        if not partner:
            embed = _make_embed(
                "warning",
                "Partner Missing",
                "Your bonded partner could not be found.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        if partner.bond_partner_id != player.user_id:
            embed = _make_embed(
                "warning",
                "Bond Unreciprocated",
                "Your partner has not set you as their bonded cultivator.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        bond_key = player.bond_key or ""
        bond = self.state.bonds.get(bond_key)
        if bond is None:
            embed = _make_embed(
                "warning",
                "No Cooperative Bond",
                "Your current bond does not grant a shared mission.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return
        if not bond.bond_mission_key or not bond.bond_soul_techniques:
            embed = _make_embed(
                "warning",
                "Mission Not Available",
                "This bond does not currently offer a cooperative mission.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        if not self.state.bond_conditions_met(player, partner, bond):
            embed = _make_embed(
                "warning",
                "Bond Requirements Not Met",
                (
                    "Both cultivators must share the bond, active path, "
                    "and minimum realm before attempting this mission."
                ),
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        outcome = complete_bond_mission(self.state, player, partner, bond)

        if not outcome.player_updated and not outcome.partner_updated:
            embed = _make_embed(
                "info",
                "Mission Already Completed",
                "You have already synchronised this mission's rewards.",
                discord.Colour.blurple(),
            )
            await self._respond_interaction(interaction, embed=embed, ephemeral=True)
            return

        if outcome.player_updated:
            await self._save_player(guild.id, player)
        if outcome.partner_updated:
            await self._save_player(guild.id, partner)

        partner_member = guild.get_member(partner.user_id)
        partner_name = (
            partner_member.display_name
            if partner_member and partner_member.display_name
            else partner.name
        )

        mission_label = outcome.mission_key.replace("-", " ").title()
        if outcome.already_completed:
            description = (
                f"{SECTION_ICONS['bond']} You rekindle the {bond.name} mission, "
                f"sharing renewed understanding with {partner_name}."
            )
            colour = discord.Colour.blurple()
        else:
            description = (
                f"{SECTION_ICONS['bond']} You and {partner_name} complete the "
                f"{bond.name} mission ({mission_label})."
            )
            colour = discord.Colour.green()

        embed = _make_embed(
            "bond",
            "Bond Mission Complete",
            description,
            colour,
            include_icon=True,
        )
        self._apply_avatar_portrait(
            embed, self._player_avatar_thumbnail_url(interaction, player)
        )

        def _technique_names(keys: Sequence[str]) -> list[str]:
            names: list[str] = []
            for key in keys:
                technique = self.state.cultivation_techniques.get(key)
                names.append(technique.name if technique and technique.name else key)
            return names

        def _skill_names(keys: Sequence[str]) -> list[str]:
            names: list[str] = []
            for key in keys:
                skill = self.state.skills.get(key)
                names.append(skill.name if skill and skill.name else key)
            return names

        player_lines: list[str] = []
        player_names = _technique_names(outcome.player_new_techniques)
        if player_names:
            player_lines.append(
                ansi_coloured_pair(
                    f"{SECTION_ICONS['cultivation']} Techniques",
                    ", ".join(player_names),
                    label_colour=SECTION_COLOURS["cultivation"],
                    value_colour="white",
                    bold_value=True,
                )
            )
        player_skill_names = _skill_names(outcome.player_new_skills)
        if player_skill_names:
            player_lines.append(
                ansi_coloured_pair(
                    f"{SECTION_ICONS['combat']} Skills",
                    ", ".join(player_skill_names),
                    label_colour=SECTION_COLOURS["combat"],
                    value_colour="white",
                    bold_value=True,
                )
            )
        if not player_lines:
            player_lines.append(
                ansi_colour(
                    "No new revelations discovered this time.",
                    SECTION_COLOURS["progress"],
                    dim=True,
                )
            )

        partner_lines: list[str] = []
        partner_names = _technique_names(outcome.partner_new_techniques)
        if partner_names:
            partner_lines.append(
                ansi_coloured_pair(
                    f"{SECTION_ICONS['cultivation']} Techniques",
                    ", ".join(partner_names),
                    label_colour=SECTION_COLOURS["cultivation"],
                    value_colour="white",
                    bold_value=True,
                )
            )
        partner_skill_names = _skill_names(outcome.partner_new_skills)
        if partner_skill_names:
            partner_lines.append(
                ansi_coloured_pair(
                    f"{SECTION_ICONS['combat']} Skills",
                    ", ".join(partner_skill_names),
                    label_colour=SECTION_COLOURS["combat"],
                    value_colour="white",
                    bold_value=True,
                )
            )
        if not partner_lines:
            partner_lines.append(
                ansi_colour(
                    f"{partner_name} had already mastered these boons.",
                    SECTION_COLOURS["progress"],
                    dim=True,
                )
            )

        embed.add_field(
            name=_label_with_icon("cultivation", player.name, include_icon=True),
            value=ansi_block(player_lines),
            inline=False,
        )
        embed.add_field(
            name=_label_with_icon("cultivation", partner_name, include_icon=True),
            value=ansi_block(partner_lines),
            inline=False,
        )

        await self._respond_interaction(
            interaction,
            embed=embed,
            ephemeral=True,
        )

    @app_commands.command(name="profile", description="Inspect your cultivation status")
    @app_commands.guild_only()
    async def profile(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        await self._show_profile_menu(
            interaction,
            guild=guild,
            main_menu_callback=None,
            ephemeral=True,
        )

    @app_commands.command(name="main", description="Open your cultivation main menu")
    @app_commands.guild_only()
    async def main(self, interaction: discord.Interaction) -> None:
        await self._open_main_menu(interaction)

    @app_commands.command(name="trade", description="Open the trading interface")
    @app_commands.guild_only()
    async def trade(self, interaction: discord.Interaction) -> None:
        await self._open_trade_menu(interaction, return_callback=None)

    @app_commands.command(
        name="set_gender", description="Update how the sect addresses you"
    )
    @app_commands.guild_only()
    async def set_gender(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        current = PronounSet.from_gender(player.gender)
        current_display = "/".join(
            (current.subject, current.obj, current.possessive)
        )
        embed = _make_embed(
            "profile",
            "Choose Your Address",
            (
                "Select how the sect should refer to you. "
                f"You are currently addressed as {current_display}."
            ),
            discord.Colour.blurple(),
        )

        async def _apply_gender(inter: discord.Interaction, value: str) -> None:
            refreshed = await self._fetch_player(guild.id, inter.user.id)
            if not refreshed:
                await inter.response.send_message(
                    "Use `/register` first.", ephemeral=True
                )
                return
            refreshed.gender = value
            await self._save_player(guild.id, refreshed)
            pronouns = PronounSet.from_gender(refreshed.gender)
            pronoun_display = "/".join(
                (pronouns.subject, pronouns.obj, pronouns.possessive)
            )
            confirmation = _make_embed(
                "profile",
                "Identity Affirmed",
                (
                    "The sect will now address you as "
                    f"{pronoun_display}."
                ),
                discord.Colour.blurple(),
            )
            await inter.response.send_message(embed=confirmation, ephemeral=True)

        view = OwnedView(interaction.user.id, timeout=60.0)
        view.add_item(
            CallbackButton(
                label="Masculine",
                emoji="♂️",
                style=discord.ButtonStyle.primary,
                callback=lambda inter: _apply_gender(inter, "male"),
            )
        )
        view.add_item(
            CallbackButton(
                label="Feminine",
                emoji="♀️",
                style=discord.ButtonStyle.primary,
                callback=lambda inter: _apply_gender(inter, "female"),
            )
        )
        view.add_item(
            CallbackButton(
                label="Neutral",
                emoji="🌟",
                style=discord.ButtonStyle.secondary,
                callback=lambda inter: _apply_gender(inter, "unspecified"),
            )
        )

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name="cultivate",
        description="Meditate to gain cultivation clarity and review your soul resonance",
    )
    @app_commands.guild_only()
    async def cultivate(self, interaction: discord.Interaction) -> None:
        await self._send_cultivation_menu(interaction)

    async def _handle_body_training_action(
        self,
        interaction: discord.Interaction,
        *,
        ephemeral: bool,
        player: PlayerProgress | None = None,
        trait_objects: Sequence[SpecialTrait] | None = None,
        combined_base: InnateSoulSet | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed("error",
                "Action Not Available",
                "This action must be performed within a server.",
                discord.Colour.red(),
            )
            await self._respond_interaction(
                interaction, embed=embed, ephemeral=ephemeral
            )
            return

        if player is None:
            player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Training Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(
                interaction, embed=embed, ephemeral=ephemeral
            )
            return

        if player.active_path != CultivationPath.BODY.value:
            embed = _make_embed(
                "warning",
                "Path Not Active",
                "Align with the Body Path before attempting to train your physique.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(
                interaction, embed=embed, ephemeral=ephemeral
            )
            return

        await self._ensure_cultivation_cooldown_config(guild.id)
        remaining = self._time_until_next_cultivation_action(guild.id, player)
        if remaining > 0:
            embed = _make_embed("warning",
                "Body Still Recovering",
                f"Try again in {remaining} seconds.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(
                interaction, embed=embed, ephemeral=ephemeral
            )
            return

        if trait_objects is None:
            trait_list = self._trait_objects(player)
        else:
            trait_list = list(trait_objects)
        if combined_base is None:
            combined_base = player.combined_innate_soul(trait_list)

        body_stage = None
        if player.body_cultivation_stage:
            body_stage = self.state.get_stage(
                player.body_cultivation_stage, CultivationPath.BODY
            )

        max_gain = max(1, int(self.config.combat_exp_gain))
        lower_bound = max(1, max_gain // 2)
        base_gain = random.randint(lower_bound, max_gain)
        addition_bonus, multiplier_bonus, bonus_sources = self._technique_exp_bonus(
            player,
            CultivationPath.BODY,
            innate_soul_set=combined_base,
        )
        adjusted = max(1.0, base_gain + addition_bonus)
        if multiplier_bonus:
            adjusted *= 1 + multiplier_bonus
        final_gain = int(round(adjusted))
        if final_gain < base_gain:
            final_gain = base_gain
        technique_bonus = final_gain - base_gain

        gain_combat_exp(player, final_gain)
        await self._save_player(guild.id, player)
        progress_pct = (
            (player.combat_exp / player.combat_exp_required) * 100
            if player.combat_exp_required
            else 0
        )
        indicator = breakthrough_ready_indicator(
            player.combat_exp, player.combat_exp_required
        )
        flavour_line = random.choice(TRAINING_VARIATIONS)
        stage_label = self._stage_display(body_stage) if body_stage else "Unbound"
        summary_lines = [
            ansi_colour(flavour_line, SECTION_COLOURS["combat"], dim=True),
            ansi_coloured_pair(
                "Stage",
                stage_label,
                label_colour=SECTION_COLOURS["stage"],
                value_colour="white",
                bold_value=True,
            ),
            ansi_coloured_pair(
                f"{SECTION_ICONS['combat']} Gains",
                f"+{format_number(final_gain)} Body Refinement EXP",
                label_colour=SECTION_COLOURS["combat"],
                value_colour="white",
                bold_value=True,
            ),
        ]
        if technique_bonus > 0:
            source_text = ", ".join(bonus_sources) if bonus_sources else "techniques"
            summary_lines.append(
                ansi_coloured_pair(
                    f"{SECTION_ICONS['cultivation']} Technique Bonus",
                    f"+{format_number(technique_bonus)} EXP ({source_text})",
                    label_colour=SECTION_COLOURS["cultivation"],
                    value_colour="white",
                )
            )
        summary_lines.append(
            ansi_coloured_pair(
                "Progress",
                f"{format_number(player.combat_exp)}/{format_number(player.combat_exp_required)} ({progress_pct:.0f}%)",
                label_colour=SECTION_COLOURS["progress"],
                value_colour="white",
                bold_value=True,
            )
        )
        summary_lines.append(
            progress_bar(player.combat_exp, player.combat_exp_required)
        )
        if indicator:
            summary_lines.append(
                ansi_colour(
                    indicator,
                    SECTION_COLOURS["progress"],
                    bold=True,
                )
            )
        embed = _make_embed(
            "combat",
            f"Training at {stage_label}",
            ansi_block(summary_lines),
            discord.Colour.red(),
        )
        self._apply_avatar_portrait(
            embed, self._player_avatar_thumbnail_url(interaction, player)
        )
        await self._respond_interaction(
            interaction, embed=embed, ephemeral=ephemeral
        )

    async def _handle_soul_tempering_action(
        self,
        interaction: discord.Interaction,
        *,
        ephemeral: bool,
        player: PlayerProgress | None = None,
        trait_objects: Sequence[SpecialTrait] | None = None,
        combined_base: InnateSoulSet | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed("error",
                "Action Not Available",
                "This action must be performed within a server.",
                discord.Colour.red(),
            )
            await self._respond_interaction(
                interaction, embed=embed, ephemeral=ephemeral
            )
            return

        if player is None:
            player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Soul Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(
                interaction, embed=embed, ephemeral=ephemeral
            )
            return

        if player.active_path != CultivationPath.SOUL.value:
            embed = _make_embed(
                "warning",
                "Path Not Active",
                "Align with the Soul Path before attempting to temper your spirit.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(
                interaction, embed=embed, ephemeral=ephemeral
            )
            return

        await self._ensure_cultivation_cooldown_config(guild.id)
        remaining = self._time_until_next_cultivation_action(guild.id, player)
        if remaining > 0:
            embed = _make_embed(
                "warning",
                "Soul Still Settling",
                f"Try again in {remaining} seconds.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(
                interaction, embed=embed, ephemeral=ephemeral
            )
            return

        if trait_objects is None:
            trait_list = self._trait_objects(player)
        else:
            trait_list = list(trait_objects)
        if combined_base is None:
            combined_base = player.combined_innate_soul(trait_list)

        soul_stage = None
        if player.soul_cultivation_stage:
            soul_stage = self.state.get_stage(
                player.soul_cultivation_stage, CultivationPath.SOUL
            )

        base_gain = max(1, int(self.config.soul_exp_gain))
        lower_bound = max(1, base_gain // 2)
        rolled_gain = random.randint(lower_bound, base_gain)
        addition_bonus, multiplier_bonus, bonus_sources = self._technique_exp_bonus(
            player,
            CultivationPath.SOUL,
            innate_soul_set=combined_base,
        )
        adjusted = max(1.0, rolled_gain + addition_bonus)
        if multiplier_bonus:
            adjusted *= 1 + multiplier_bonus
        final_gain = int(round(adjusted))
        if final_gain < rolled_gain:
            final_gain = rolled_gain
        technique_bonus = final_gain - rolled_gain

        gain_soul_exp(player, final_gain)
        await self._save_player(guild.id, player)
        progress_pct = (
            (player.soul_exp / player.soul_exp_required) * 100
            if player.soul_exp_required
            else 0
        )
        indicator = breakthrough_ready_indicator(
            player.soul_exp, player.soul_exp_required
        )
        stage_name = self._stage_display(soul_stage) if soul_stage else "Unbound"
        flavour_line = random.choice(SOUL_TEMPERING_VARIATIONS)
        summary_lines = [
            ansi_colour(flavour_line, SECTION_COLOURS["soul"], dim=True),
            ansi_coloured_pair(
                "Stage",
                stage_name,
                label_colour=SECTION_COLOURS["stage"],
                value_colour="white",
                bold_value=True,
            ),
            ansi_coloured_pair(
                f"{SECTION_ICONS['soul']} Gains",
                f"+{format_number(final_gain)} Soul Purification EXP",
                label_colour=SECTION_COLOURS["soul"],
                value_colour="white",
                bold_value=True,
            ),
        ]
        if technique_bonus > 0:
            source_text = ", ".join(bonus_sources) if bonus_sources else "techniques"
            summary_lines.append(
                ansi_coloured_pair(
                    f"{SECTION_ICONS['cultivation']} Technique Bonus",
                    f"+{format_number(technique_bonus)} EXP ({source_text})",
                    label_colour=SECTION_COLOURS["cultivation"],
                    value_colour="white",
                )
            )
        summary_lines.append(
            ansi_coloured_pair(
                "Progress",
                f"{format_number(player.soul_exp)}/{format_number(player.soul_exp_required)} ({progress_pct:.0f}%)",
                label_colour=SECTION_COLOURS["progress"],
                value_colour="white",
                bold_value=True,
            )
        )
        summary_lines.append(
            progress_bar(player.soul_exp, player.soul_exp_required)
        )
        if indicator:
            summary_lines.append(
                ansi_colour(
                    indicator,
                    SECTION_COLOURS["progress"],
                    bold=True,
                )
            )
        embed = _make_embed(
            "soul",
            f"Tempering at {stage_name}",
            ansi_block(summary_lines),
            discord.Colour.from_str("#8e44ad"),
        )
        self._apply_avatar_portrait(
            embed, self._player_avatar_thumbnail_url(interaction, player)
        )
        await self._respond_interaction(
            interaction, embed=embed, ephemeral=ephemeral
        )

    async def _send_cultivation_menu(
        self,
        interaction: discord.Interaction,
        *,
        return_callback: SimpleCallback | None = None,
        player: PlayerProgress | None = None,
        title: str | None = None,
        description: str | None = None,
        colour: discord.Colour | None = None,
        ephemeral: bool | None = None,
        include_path_fields: bool = True,
        show_extra_sections: bool = True,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed(
                "error",
                "Action Not Available",
                "This action must be performed within a server.",
                discord.Colour.red(),
            )
            await self._respond_interaction(interaction, embed=embed)
            return

        await self.ensure_guild_loaded(guild.id)
        player, traits, combined_base = await self._resolve_player_context(
            guild,
            interaction.user.id,
            player=player,
        )
        if not player:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(interaction, embed=embed)
            return

        bond_profile: BondProfile | None = None
        bond_partner: PlayerProgress | None = None
        bond_field_value: str | None = None
        bond_ready = False
        if player.bond_key:
            bond_profile = self.state.bonds.get(player.bond_key)
        if bond_profile and player.bond_partner_id:
            partner_candidate = await self._fetch_player(
                guild.id, player.bond_partner_id
            )
            if partner_candidate:
                bond_partner = partner_candidate
                condition_met = self.state.bond_conditions_met(
                    player, bond_partner, bond_profile
                )
                cooldown_ready = False
                remaining_self = remaining_partner = 0
                if condition_met:
                    await self._ensure_cultivation_cooldown_config(guild.id)
                    remaining_self = self._time_until_next_cultivation_action(
                        guild.id, player
                    )
                    remaining_partner = self._time_until_next_cultivation_action(
                        guild.id, bond_partner
                    )
                    cooldown_ready = remaining_self <= 0 and remaining_partner <= 0
                bond_ready = condition_met and cooldown_ready
                partner_member = guild.get_member(bond_partner.user_id)
                partner_name = (
                    partner_member.display_name
                    if partner_member and partner_member.display_name
                    else bond_partner.name
                )
                status_value: str
                if not condition_met:
                    status_value = "Prerequisites unmet"
                elif not cooldown_ready:
                    status_value = (
                        f"Stabilising ({max(remaining_self, remaining_partner)}s)"
                        if max(remaining_self, remaining_partner) > 0
                        else "Stabilising"
                    )
                else:
                    status_value = "Ready"
                lines = [
                    ansi_coloured_pair(
                        "Partner",
                        partner_name,
                        label_colour=SECTION_COLOURS["bond"],
                        value_colour="white",
                        bold_value=True,
                    ),
                    ansi_coloured_pair(
                        "Bond",
                        bond_profile.name,
                        label_colour=SECTION_COLOURS["bond"],
                        value_colour="white",
                        bold_value=True,
                    ),
                    ansi_coloured_pair(
                        "Status",
                        status_value,
                        label_colour=SECTION_COLOURS["progress"],
                        value_colour="white",
                        bold_value=True,
                    ),
                ]
                if bond_profile.description:
                    lines.append(
                        ansi_colour(
                            bond_profile.description,
                            SECTION_COLOURS["bond"],
                            dim=True,
                        )
                    )
                if bond_profile.min_stage:
                    stage = self.state.get_stage(
                        bond_profile.min_stage, bond_profile.required_path
                    )
                    stage_label = (
                        stage.combined_name
                        if stage is not None
                        else bond_profile.min_stage.replace("-", " ").title()
                    )
                    lines.append(
                        ansi_coloured_pair(
                            "Requirement",
                            f"{stage_label}+ ({bond_profile.required_path.value.title()})",
                            label_colour=SECTION_COLOURS["stage"],
                            value_colour="white",
                        )
                    )
                bonus_fragments: list[str] = []
                if bond_profile.exp_multiplier:
                    bonus_fragments.append(
                        f"{bond_profile.exp_multiplier * 100:.0f}%"
                    )
                if bond_profile.flat_bonus:
                    bonus_fragments.append(
                        f"+{format_number(bond_profile.flat_bonus)}"
                    )
                if bonus_fragments:
                    lines.append(
                        ansi_coloured_pair(
                            "Bonus",
                            " & ".join(bonus_fragments) + " EXP",
                            label_colour=SECTION_COLOURS["bond"],
                            value_colour="white",
                        )
                    )
                bond_field_value = ansi_block(lines)

        active_path = CultivationPath.from_value(player.active_path)
        path_titles = {
            CultivationPath.QI: "QI CULTIVATION",
            CultivationPath.BODY: "BODY REFINEMENT",
            CultivationPath.SOUL: "SOUL PURIFICATION",
        }
        path_sections = {
            CultivationPath.QI: "cultivation",
            CultivationPath.BODY: "combat",
            CultivationPath.SOUL: "soul",
        }
        embed_title = title or "FOCUS YOUR PATH"
        embed_description = description or ""
        embed_colour = colour or discord.Colour.purple()
        embed = _make_embed(
            "cultivation",
            embed_title,
            embed_description,
            embed_colour,
            include_icon=True,
        )
        self._apply_avatar_portrait(
            embed, self._player_avatar_thumbnail_url(interaction, player)
        )

        unlocked_paths: set[CultivationPath] = set()
        for value in player.unlocked_paths or []:
            try:
                unlocked_paths.add(CultivationPath.from_value(value))
            except ValueError:
                continue
        unlocked_paths.add(CultivationPath.QI)

        def _resolve_stage_info(
            path: CultivationPath,
        ) -> tuple[CultivationStage | None, CultivationStage | None]:
            if path is CultivationPath.BODY:
                stage_key = player.body_cultivation_stage
                stage_candidates = list(self.state.body_cultivation_stages.values())
            elif path is CultivationPath.SOUL:
                stage_key = player.soul_cultivation_stage
                stage_candidates = list(self.state.soul_cultivation_stages.values())
            else:
                stage_key = player.cultivation_stage
                stage_candidates = list(self.state.qi_cultivation_stages.values())

            current_stage = self.state.get_stage(stage_key, path)
            if not stage_candidates:
                stage_candidates = [
                    stage
                    for stage in self.state.iter_all_stages()
                    if CultivationPath.from_value(stage.path) is path
                ]

            next_stage: CultivationStage | None = None
            if current_stage is not None and stage_candidates:
                ordered = sorted(stage_candidates, key=lambda stage: stage.ordering_tuple)
                try:
                    index = next(
                        i for i, stage in enumerate(ordered) if stage.key == current_stage.key
                    )
                except StopIteration:
                    index = -1
                if index >= 0 and index + 1 < len(ordered):
                    next_stage = ordered[index + 1]
                if (
                    next_stage
                    and current_stage.phase is CultivationPhase.PEAK
                    and next_stage.realm != current_stage.realm
                ):
                    target_realm = next_stage.realm
                    realm_candidates = [
                        stage for stage in ordered if stage.realm == target_realm
                    ]
                    if realm_candidates:
                        next_stage = min(
                            realm_candidates, key=lambda stage: stage.phase.order_index
                        )
            return current_stage, next_stage

        def _path_progress(
            path: CultivationPath,
        ) -> tuple[int, int]:
            if path is CultivationPath.BODY:
                return player.combat_exp, player.combat_exp_required
            if path is CultivationPath.SOUL:
                return player.soul_exp, player.soul_exp_required
            return player.cultivation_exp, player.cultivation_exp_required

        def _build_path_field(
            path: CultivationPath,
        ) -> tuple[str, bool]:
            unlocked = path in unlocked_paths
            lines: list[str] = []
            ready_flag = False

            if not unlocked:
                lines.append(
                    ansi_colour(
                        "Path locked — seek new adventures to unlock this discipline.",
                        "gray",
                        dim=True,
                    )
                )
                return ansi_block(lines), ready_flag

            current_stage, _ = _resolve_stage_info(path)
            progress_current, progress_required = _path_progress(path)

            if current_stage is None:
                lines.append(
                    ansi_colour(
                        "Stage configuration missing for this discipline.",
                        SECTION_COLOURS["error"],
                        bold=True,
                    )
                )
            else:
                realm_name = current_stage.realm or current_stage.name
                if not current_stage.is_mortal:
                    step_value = getattr(current_stage, "step", 1)
                    step_label = _ordinal_step_label(step_value)
                    lines.append(
                        ansi_coloured_pair(
                            "Step",
                            step_label,
                            label_colour=SECTION_COLOURS["stage"],
                            value_colour="white",
                            bold_value=True,
                        )
                    )
                lines.append(
                    ansi_coloured_pair(
                        "Realm",
                        realm_name,
                        label_colour=SECTION_COLOURS["stage"],
                        value_colour="white",
                        bold_value=True,
                    )
                )
                if current_stage.phase_display:
                    lines.append(
                        ansi_coloured_pair(
                            "Stage",
                            current_stage.phase_display,
                            label_colour=SECTION_COLOURS["stage"],
                            value_colour="white",
                            bold_value=True,
                        )
                    )

            if progress_required > 0:
                progress_value = (
                    f"{format_number(progress_current)}/{format_number(progress_required)}"
                )
                progress_pct = max(
                    0.0, (progress_current / float(progress_required)) * 100.0
                )
            else:
                progress_value = "0/0"
                progress_pct = 0.0

            lines.append(
                ansi_coloured_pair(
                    "Progress",
                    f"{progress_value} ({progress_pct:.0f}%)",
                    label_colour=SECTION_COLOURS["progress"],
                    value_colour="white",
                    bold_value=True,
                )
            )
            lines.append(progress_bar(progress_current, progress_required))

            indicator = breakthrough_ready_indicator(progress_current, progress_required)
            if indicator:
                lines.append(
                    ansi_colour(indicator, SECTION_COLOURS["progress"], bold=True)
                )

            if progress_required > 0 and progress_current >= progress_required:
                ready_flag = True

            return ansi_block(lines), ready_flag

        field_value, breakthrough_ready = _build_path_field(active_path)
        if include_path_fields:
            embed.add_field(
                name=_label_with_icon(path_sections[active_path], path_titles[active_path]),
                value=field_value,
                inline=False,
            )

        trait_lines: list[str] = []
        for trait in traits[:3]:
            trait_lines.append(
                (
                    f"{ansi_colour('✨', SECTION_COLOURS['traits'], bold=True)} "
                    f"{ansi_colour(trait.name, SECTION_COLOURS['traits'], bold=True)} — "
                    f"{ansi_colour(trait.description or 'Unique birthright.', 'white', dim=True, italic=True)}"
                )
            )
        if show_extra_sections and trait_lines:
            embed.add_field(
                name=_label_with_icon("talent", "Meditation Resonance"),
                value=ansi_block(trait_lines),
                inline=False,
            )

        if show_extra_sections and bond_field_value and bond_profile and bond_partner:
            embed.add_field(
                name=_label_with_icon("bond", "Bonded Cultivation", include_icon=True),
                value=bond_field_value,
                inline=False,
            )

        async def _perform_qi(inter: discord.Interaction) -> None:
            refreshed, resolved_traits, resolved_base = await self._resolve_player_context(
                guild, inter.user.id
            )
            if not refreshed:
                await self._respond_interaction(
                    inter, content="Use `/register` first.", ephemeral=True
                )
                return
            await self._handle_cultivation_interaction(
                inter,
                ephemeral=True,
                player=refreshed,
                trait_objects=resolved_traits,
                combined_base=resolved_base,
                return_callback=return_callback,
            )

        async def _perform_body(inter: discord.Interaction) -> None:
            refreshed, resolved_traits, resolved_base = await self._resolve_player_context(
                guild, inter.user.id
            )
            if not refreshed:
                await self._respond_interaction(
                    inter, content="Use `/register` first.", ephemeral=True
                )
                return
            await self._handle_body_training_action(
                inter,
                ephemeral=True,
                player=refreshed,
                trait_objects=resolved_traits,
                combined_base=resolved_base,
            )

        async def _perform_soul(inter: discord.Interaction) -> None:
            refreshed, resolved_traits, resolved_base = await self._resolve_player_context(
                guild, inter.user.id
            )
            if not refreshed:
                await self._respond_interaction(
                    inter, content="Use `/register` first.", ephemeral=True
                )
                return
            await self._handle_soul_tempering_action(
                inter,
                ephemeral=True,
                player=refreshed,
                trait_objects=resolved_traits,
                combined_base=resolved_base,
            )

        async def _perform_breakthrough(inter: discord.Interaction) -> None:
            await self._handle_breakthrough_flow(
                inter,
                selected_path=active_path,
                ephemeral=True,
            )

        async def _perform_cooperative(inter: discord.Interaction) -> None:
            refreshed, resolved_traits, resolved_base = await self._resolve_player_context(
                guild,
                inter.user.id,
            )
            if not refreshed:
                await self._respond_interaction(
                    inter, content="Use `/register` first.", ephemeral=True
                )
                return
            partner_id = refreshed.bond_partner_id
            if not partner_id:
                await self._respond_interaction(
                    inter,
                    content="You do not have a bonded cultivation partner configured.",
                    ephemeral=True,
                )
                return
            partner, partner_traits, partner_base = await self._resolve_player_context(
                guild,
                partner_id,
            )
            if not partner:
                await self._respond_interaction(
                    inter,
                    content="Your bonded partner could not be found.",
                    ephemeral=True,
                )
                return
            bond = (
                self.state.bonds.get(refreshed.bond_key)
                if refreshed.bond_key
                else None
            )
            if bond is None:
                await self._respond_interaction(
                    inter,
                    content="This bond does not grant cooperative cultivation bonuses.",
                    ephemeral=True,
                )
                return
            await self._handle_cooperative_cultivation(
                inter,
                ephemeral=True,
                player=refreshed,
                partner=partner,
                player_traits=resolved_traits,
                partner_traits=partner_traits,
                player_base=resolved_base,
                partner_base=partner_base,
                bond=bond,
                return_callback=return_callback,
            )

        callback_kwargs = {
            "on_cultivate_qi": _perform_qi if active_path is CultivationPath.QI else None,
            "on_train_body": _perform_body if active_path is CultivationPath.BODY else None,
            "on_temper_soul": _perform_soul if active_path is CultivationPath.SOUL else None,
            "on_breakthrough": _perform_breakthrough if breakthrough_ready else None,
        }
        if bond_profile and bond_partner:
            callback_kwargs["on_cooperate"] = _perform_cooperative
        view = CultivationActionView(
            interaction.user.id,
            return_callback=return_callback,
            **callback_kwargs,
        )
        await self._respond_interaction(
            interaction,
            embed=embed,
            view=view,
            ephemeral=True if ephemeral is None else ephemeral,
        )

    @app_commands.command(name="breakthrough", description="Attempt to advance to the next cultivation stage")
    @app_commands.guild_only()
    async def breakthrough(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await self._handle_breakthrough_flow(interaction)

    @app_commands.command(
        name="conceal",
        description="Disguise your cultivation path roles with previously attained realms",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        cultivation="Stage key to display for your cultivation path",
        body="Stage key to display for your body refinement path",
        soul="Stage key to display for your soul purification path",
    )
    async def conceal(
        self,
        interaction: discord.Interaction,
        cultivation: str | None = None,
        body: str | None = None,
        soul: str | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        stage_keys: dict[CultivationPath, str | None] = {
            CultivationPath.QI: player.cultivation_stage,
            CultivationPath.BODY: player.body_cultivation_stage,
            CultivationPath.SOUL: player.soul_cultivation_stage,
        }
        actual_stages: dict[CultivationPath, CultivationStage | None] = {
            path: self.state.get_stage(key, path) if key else None
            for path, key in stage_keys.items()
        }

        overrides = dict(player.path_role_overrides)
        changes: list[str] = []
        errors: list[str] = []

        def process_choice(path: CultivationPath, value: str | None, label: str) -> None:
            if value is None:
                return
            if value == CONCEAL_CLEAR_SENTINEL:
                if overrides.pop(path.value, None) is not None:
                    changes.append(f"{label}: revealing your true realm")
                else:
                    changes.append(f"{label}: already revealing your true realm")
                return
            actual_stage = actual_stages.get(path)
            if actual_stage is None:
                errors.append(f"{label}: your current realm configuration is missing.")
                return
            chosen_stage = self.state.get_stage(value, path)
            if chosen_stage is None:
                errors.append(f"{label}: unknown stage '{value}'.")
                return
            if chosen_stage.ordering_tuple > actual_stage.ordering_tuple:
                errors.append(
                    f"{label}: you have not yet attained {chosen_stage.combined_name}."
                )
                return
            overrides[path.value] = chosen_stage.key
            changes.append(f"{label}: displaying {chosen_stage.combined_name}")

        process_choice(CultivationPath.QI, cultivation, "Cultivation Path")
        process_choice(CultivationPath.BODY, body, "Body Refinement Path")
        process_choice(CultivationPath.SOUL, soul, "Soul Purification Path")

        if errors:
            embed = _make_embed(
                "warning",
                "Unable to Conceal",
                "\n".join(errors[:4]),
                discord.Colour.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if not changes:
            embed = _make_embed(
                "information",
                "No Changes Applied",
                "You are already displaying the selected realms.",
                discord.Colour.blurple(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        player.path_role_overrides = overrides
        member = await self._resolve_member(guild, interaction.user)
        if member is not None:
            await self._sync_player_path_roles(guild, member, player)
        await self._save_player(guild.id, player)

        display_lines: list[str] = []
        for path, label in (
            (CultivationPath.QI, "Cultivation Path"),
            (CultivationPath.BODY, "Body Refinement Path"),
            (CultivationPath.SOUL, "Soul Purification Path"),
        ):
            actual_stage = actual_stages.get(path)
            shown_stage = self._resolve_display_stage(player, path, actual_stage)
            if shown_stage is None:
                display_lines.append(f"{label}: Unconfigured")
            else:
                display_lines.append(f"{label}: {shown_stage.combined_name}")

        embed = _make_embed(
            "mask",
            "Cultivation Roles Concealed",
            "\n".join(display_lines),
            discord.Colour.from_str("#6c3483"),
        )
        embed.add_field(
            name="Changes Applied",
            value="\n".join(changes),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _conceal_stage_choices(
        self,
        interaction: discord.Interaction,
        path: CultivationPath,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        guild = interaction.guild
        if guild is None:
            return []
        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            return []
        stage_key = (
            player.cultivation_stage
            if path is CultivationPath.QI
            else (
                player.body_cultivation_stage
                if path is CultivationPath.BODY
                else player.soul_cultivation_stage
            )
        ) or player.cultivation_stage
        actual_stage = self.state.get_stage(stage_key, path)
        if actual_stage is None:
            return []
        query = current.strip().lower()
        stages = sorted(
            self._stages_for_path(path), key=lambda stage: stage.ordering_tuple, reverse=True
        )
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name="Reveal actual realm", value=CONCEAL_CLEAR_SENTINEL)
        ]
        for stage in stages:
            if stage.ordering_tuple > actual_stage.ordering_tuple:
                continue
            name = stage.combined_name
            if query and query not in stage.key.lower() and query not in name.lower():
                continue
            choices.append(app_commands.Choice(name=name[:100], value=stage.key))
            if len(choices) >= 25:
                break
        return choices[:25]

    @conceal.autocomplete("cultivation")
    async def conceal_cultivation_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._conceal_stage_choices(
            interaction, CultivationPath.QI, current
        )

    @conceal.autocomplete("body")
    async def conceal_body_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._conceal_stage_choices(
            interaction, CultivationPath.BODY, current
        )

    @conceal.autocomplete("soul")
    async def conceal_soul_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._conceal_stage_choices(
            interaction, CultivationPath.SOUL, current
        )

    @app_commands.command(name="skills", description="Review your learned skills")
    @app_commands.guild_only()
    async def skills(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed("warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = discord.Embed(
            title=_label_with_icon("guide", "Techniques & Skills"),
            colour=discord.Colour.green(),
        )
        self._apply_avatar_portrait(
            embed, self._player_avatar_thumbnail_url(interaction, player)
        )
        skill_entries = self._skill_entries(
            player, category=SkillCategory.ACTIVE
        )
        if not skill_entries:
            embed.description = (
                "No skills learned yet. Seek masters or tomes to expand your arsenal."
            )
        else:
            blocks = self._skill_summary_blocks(
                player, None, entries=skill_entries
            )
            for heading, block in blocks:
                field_name = (
                    heading
                    if heading == "\u200b"
                    else _label_with_icon("guide", heading)
                )
                embed.add_field(
                    name=field_name,
                    value=block,
                    inline=False,
                )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="equip_title", description="Equip a prefix or suffix title")
    @app_commands.guild_only()
    async def equip_title(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        titles_by_position: dict[TitlePosition, list[Title]] = {
            TitlePosition.PREFIX: [],
            TitlePosition.SUFFIX: [],
        }
        for key in player.titles:
            title_obj = self.state.titles.get(key)
            if not title_obj:
                continue
            titles_by_position[title_obj.position].append(title_obj)

        if not any(titles_by_position.values()):
            embed = _make_embed(
                "warning",
                "No Titles Available",
                "You have not earned any titles yet.",
                discord.Colour.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        prefix_current = player.active_title_prefix
        suffix_current = player.active_title_suffix
        summary_lines = []
        if prefix_current:
            prefix_name = self.state.titles.get(prefix_current)
            prefix_label = prefix_name.name if prefix_name else prefix_current
        else:
            prefix_label = "None equipped"
        if suffix_current:
            suffix_name = self.state.titles.get(suffix_current)
            suffix_label = suffix_name.name if suffix_name else suffix_current
        else:
            suffix_label = "None equipped"
        summary_lines.append(f"Prefix: {prefix_label}")
        summary_lines.append(f"Suffix: {suffix_label}")

        embed = _make_embed(
            "guide",
            "Manage Titles",
            "\n".join(summary_lines),
            discord.Colour.blurple(),
        )

        async def _apply_title(
            inter: discord.Interaction,
            position: TitlePosition,
            value: str,
        ) -> None:
            refreshed = await self._fetch_player(guild.id, inter.user.id)
            if not refreshed:
                await inter.response.send_message(
                    "Use `/register` first.", ephemeral=True
                )
                return
            if value == "__clear__":
                refreshed.equip_title(position, None)
                await self._save_player(guild.id, refreshed)
                await inter.response.send_message(
                    f"Cleared your active {position.value} title.",
                    ephemeral=True,
                )
                return
            if value not in refreshed.titles:
                await inter.response.send_message(
                    "You have not earned that title.", ephemeral=True
                )
                return
            title_obj = self.state.titles.get(value)
            if not title_obj:
                await inter.response.send_message(
                    "That title is no longer configured.", ephemeral=True
                )
                return
            if title_obj.position is not position:
                await inter.response.send_message(
                    "That title cannot be equipped in this slot.",
                    ephemeral=True,
                )
                return
            refreshed.equip_title(position, value)
            await self._save_player(guild.id, refreshed)
            await inter.response.send_message(
                f"Equipped {position.value} title **{title_obj.name}**.",
                ephemeral=True,
            )

        def _build_options(
            titles: list[Title], current: str | None
        ) -> list[discord.SelectOption]:
            options: list[discord.SelectOption] = [
                discord.SelectOption(
                    label="Unequip",
                    value="__clear__",
                    description="Remove the current title",
                    default=current is None,
                )
            ]
            for title in sorted(titles, key=lambda obj: obj.name.lower()):
                options.append(
                    discord.SelectOption(
                        label=title.name,
                        value=title.key,
                        description=title.description[:95] if title.description else None,
                        default=current == title.key,
                    )
                )
            return options

        view = OwnedView(interaction.user.id, timeout=120.0)
        if titles_by_position[TitlePosition.PREFIX]:
            view.add_item(
                CallbackSelect(
                    options=_build_options(
                        titles_by_position[TitlePosition.PREFIX], prefix_current
                    ),
                    placeholder="Select a prefix title",
                    callback=lambda inter, value: _apply_title(
                        inter, TitlePosition.PREFIX, value
                    ),
                )
            )
        if titles_by_position[TitlePosition.SUFFIX]:
            view.add_item(
                CallbackSelect(
                    options=_build_options(
                        titles_by_position[TitlePosition.SUFFIX], suffix_current
                    ),
                    placeholder="Select a suffix title",
                    callback=lambda inter, value: _apply_title(
                        inter, TitlePosition.SUFFIX, value
                    ),
                )
            )

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="evolve", description="Evolve a skill if requirements are met")
    @app_commands.guild_only()
    async def evolve(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        eligible: list[Skill] = []
        for key in player.skill_proficiency:
            skill = self.state.skills.get(key)
            if not skill or not skill.evolves_to:
                continue
            if skill.evolves_to not in self.state.skills:
                continue
            if can_evolve_skill(skill, player):
                eligible.append(skill)

        if not eligible:
            embed = _make_embed(
                "warning",
                "No Evolutions",
                "None of your skills meet their evolution requirements.",
                discord.Colour.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        options: list[discord.SelectOption] = []
        for skill in sorted(eligible, key=lambda item: item.name.lower()):
            target = self.state.skills.get(skill.evolves_to)
            target_name = target.name if target else skill.evolves_to
            options.append(
                discord.SelectOption(
                    label=skill.name,
                    value=skill.key,
                    description=f"Evolves into {target_name}",
                )
            )

        async def _perform_evolution(inter: discord.Interaction, key: str) -> None:
            refreshed = await self._fetch_player(guild.id, inter.user.id)
            if not refreshed:
                await inter.response.send_message(
                    "Use `/register` first.", ephemeral=True
                )
                return
            skill_obj = self.state.skills.get(key)
            if not skill_obj:
                await inter.response.send_message(
                    "That skill is no longer available.", ephemeral=True
                )
                return
            if not can_evolve_skill(skill_obj, refreshed):
                await inter.response.send_message(
                    "You no longer meet the evolution requirements.",
                    ephemeral=True,
                )
                return
            if not skill_obj.evolves_to or skill_obj.evolves_to not in self.state.skills:
                await inter.response.send_message(
                    "The evolution target is not configured.", ephemeral=True
                )
                return
            refreshed.skill_proficiency.pop(skill_obj.key, None)
            refreshed.skill_proficiency[skill_obj.evolves_to] = 0
            await self._save_player(guild.id, refreshed)
            evolved = self.state.skills[skill_obj.evolves_to]
            embed = _make_embed(
                "guide",
                "Skill Evolution",
                f"{skill_obj.name} evolves into **{evolved.name}**!",
                discord.Colour.from_str("#1abc9c"),
            )
            await inter.response.send_message(embed=embed, ephemeral=True)

        view = OwnedView(interaction.user.id, timeout=90.0)
        view.add_item(
            CallbackSelect(
                options=options,
                placeholder="Select a skill to evolve",
                callback=_perform_evolution,
            )
        )
        embed = _make_embed(
            "guide",
            "Eligible Evolutions",
            "Choose a skill to evolve from the menu below.",
            discord.Colour.from_str("#1abc9c"),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="inventory", description="Review your inventory")
    @app_commands.guild_only()
    async def inventory(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed("warning",
                "No Inventory",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        load = inventory_load(player)
        capacity = inventory_capacity(player, self.state.items)
        lines = [
            f"{SECTION_ICONS['inventory']} Slots Used: {format_number(load)}/{format_number(capacity)}"
        ]
        for item, amount in player.inventory.items():
            item_obj = self.state.items.get(item)
            name = item_obj.name if item_obj else item
            icon = "[Item]"
            if item_obj:
                icon = ITEM_TYPE_ICONS.get(item_obj.item_type.lower(), "[Item]")
            lines.append(f"{icon} **{name}** ×{format_number(amount)}")
        equipment_lines = self._equipment_summary_lines(player)
        if equipment_lines:
            lines.append("")
            lines.append(_label_with_icon("inventory", "Equipment Slots"))
            lines.extend(f"• {entry}" for entry in equipment_lines)
        if not player.inventory:
            lines.append("Your inventory is empty.")
        embed = discord.Embed(
            title=_label_with_icon("inventory", "Inventory Overview"),
            description="\n".join(lines),
            colour=discord.Colour.from_str("#11806a"),
        )
        self._apply_avatar_portrait(
            embed, self._player_avatar_thumbnail_url(interaction, player)
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="base_mutate",
        description="Review or apply pending spiritual innate soul mutation opportunities",
    )
    @app_commands.describe(
        mutation_id="Identifier of the mutation opportunity to accept"
    )
    @app_commands.guild_only()
    async def base_mutate(
        self,
        interaction: discord.Interaction,
        mutation_id: str | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed(
                "error",
                "Guild Only",
                "This command must be used within a server.",
                discord.Colour.red(),
            )
            await self._respond_interaction(interaction, embed=embed)
            return

        player = await self._fetch_player(guild.id, interaction.user.id)
        if player is None:
            embed = _make_embed(
                "innate_soul",
                "No Character Found",
                "Create a character before attempting innate soul mutations.",
                SECTION_COLOURS["innate_soul"],
            )
            await self._respond_interaction(interaction, embed=embed)
            return

        player.expire_innate_soul_mutations()

        if mutation_id:
            opportunity = player.find_innate_soul_mutation_opportunity(mutation_id)
            if opportunity is None:
                embed = _make_embed(
                    "innate_soul",
                    "Mutation Not Found",
                    "No pending mutation matches that identifier.",
                    SECTION_COLOURS["innate_soul"],
                )
                await self._respond_interaction(interaction, embed=embed)
                return

            player.apply_innate_soul_mutation(opportunity)
            await self._save_player(guild.id, player)
            new_base = describe_innate_soul(player.innate_soul_set, use_ansi=False)
            variant_display = describe_innate_soul(
                opportunity.variant, use_ansi=False
            )
            description = (
                f"Your bases shift towards **{opportunity.variant.name}**."
                if opportunity.variant.name
                else "Your bases resonate with new possibilities."
            )
            embed = _make_embed(
                "innate_soul",
                "Mutation Applied",
                description,
                SECTION_COLOURS["innate_soul"],
            )
            if variant_display:
                embed.add_field(name="Variant", value=variant_display, inline=False)
            if new_base:
                embed.add_field(name="Updated Souls", value=new_base, inline=False)
            await self._respond_interaction(interaction, embed=embed)
            return

        if not player.pending_innate_soul_mutations:
            embed = _make_embed(
                "innate_soul",
                "No Mutation Opportunities",
                "Venture into hazardous encounters to trigger innate soul mutations.",
                SECTION_COLOURS["innate_soul"],
            )
            await self._respond_interaction(interaction, embed=embed)
            return

        now = time.time()
        embed = _make_embed(
            "innate_soul",
            "Pending Soul Mutations",
            "Use `/base_mutate mutation_id:<id>` to embrace a listed mutation.",
            SECTION_COLOURS["innate_soul"],
        )
        for opportunity in player.pending_innate_soul_mutations:
            variant_display = describe_innate_soul(opportunity.variant, use_ansi=False)
            expires_in = ""
            if opportunity.expires_at is not None:
                remaining = max(0.0, opportunity.expires_at - now)
                minutes = int(remaining // 60)
                seconds = int(remaining % 60)
                expires_in = f"Expires in {minutes}m {seconds}s"
            fragments: list[str] = []
            if variant_display:
                fragments.append(variant_display)
            if opportunity.hybridized:
                fragments.append("Hybridization potential detected.")
            if expires_in:
                fragments.append(expires_in)
            value = "\n".join(fragments) if fragments else "Awaiting resonance."
            title = (
                f"{opportunity.identifier} — {opportunity.variant.name}"
                if opportunity.variant.name
                else opportunity.identifier
            )
            embed.add_field(name=title, value=value, inline=False)

        await self._respond_interaction(interaction, embed=embed)

    async def _open_equipment_manager(
        self,
        interaction: discord.Interaction,
        *,
        focus: str,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed(
                "error",
                "Guild Only",
                "Use this in a server.",
                discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Inventory",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        has_equippable = any(
            amount > 0
            and (item := self.state.items.get(key)) is not None
            and item.item_type == "equipment"
            for key, amount in player.inventory.items()
        )
        has_equipped = any(player.equipment.values())

        view = EquipmentManagerView(
            self,
            guild.id,
            interaction.user.id,
            avatar_url=self._player_avatar_thumbnail_url(interaction, player),
            initial_player=player,
            focus=focus,
        )
        if focus == "equip" and not has_equippable:
            view.status_message = view._status_warning(
                "You have no equippable items in your inventory."
            )
        elif focus == "unequip" and not has_equipped:
            view.status_message = view._status_warning(
                "You have no gear equipped right now."
            )

        embed = view.initial_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        try:
            if interaction.type is InteractionType.component:
                view.message = interaction.message
            else:
                view.message = await interaction.original_response()
        except discord.HTTPException:
            view.message = None

    @app_commands.command(
        name="equip",
        description="Manage your gear and equip new items via an interactive menu",
    )
    @app_commands.guild_only()
    async def equip(self, interaction: discord.Interaction) -> None:
        await self._open_equipment_manager(interaction, focus="equip")

    @app_commands.command(
        name="unequip",
        description="Inspect or remove equipped gear using the equipment manager",
    )
    @app_commands.guild_only()
    async def unequip(self, interaction: discord.Interaction) -> None:
        await self._open_equipment_manager(interaction, focus="unequip")

    @app_commands.command(name="reforge", description="Reforge an item if requirements are met")
    @app_commands.guild_only()
    async def reforge(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Inventory",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        options: list[discord.SelectOption] = []
        for key, amount in sorted(player.inventory.items()):
            if amount <= 0:
                continue
            item = self.state.items.get(key)
            if not item or not item.evolves_to:
                continue
            target = self.state.items.get(item.evolves_to)
            target_name = target.name if target else item.evolves_to
            options.append(
                discord.SelectOption(
                    label=item.name,
                    value=key,
                    description=f"Reforges into {target_name}",
                )
            )

        if not options:
            embed = _make_embed(
                "warning",
                "No Reforge Targets",
                "You have no items that can currently be reforged.",
                discord.Colour.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        async def _perform_reforge(inter: discord.Interaction, key: str) -> None:
            refreshed = await self._fetch_player(guild.id, inter.user.id)
            if not refreshed:
                await inter.response.send_message(
                    "Use `/register` first.", ephemeral=True
                )
                return
            if refreshed.inventory.get(key, 0) <= 0:
                await inter.response.send_message(
                    "You do not possess that item.", ephemeral=True
                )
                return
            item = self.state.items.get(key)
            if not item or not item.evolves_to:
                await inter.response.send_message(
                    "That item can no longer be reforged.", ephemeral=True
                )
                return
            requirements = item.evolution_requirements
            stage_required = requirements.get("stage")
            if stage_required and refreshed.cultivation_stage != stage_required:
                await inter.response.send_message(
                    "Reach a higher cultivation stage before reforging this item.",
                    ephemeral=True,
                )
                return
            location_required = requirements.get("location")
            if location_required and refreshed.location != location_required:
                await inter.response.send_message(
                    "Travel to the required forge before reforging.",
                    ephemeral=True,
                )
                return
            catalyst = requirements.get("item")
            if catalyst and refreshed.inventory.get(catalyst, 0) <= 0:
                await inter.response.send_message(
                    "You lack the required catalyst item.", ephemeral=True
                )
                return
            if item.evolves_to not in self.state.items:
                await inter.response.send_message(
                    "The reforged item configuration is missing.",
                    ephemeral=True,
                )
                return
            refreshed.inventory[key] -= 1
            if refreshed.inventory[key] <= 0:
                refreshed.inventory.pop(key)
            refreshed.inventory[item.evolves_to] = (
                refreshed.inventory.get(item.evolves_to, 0) + 1
            )
            await self._save_player(guild.id, refreshed)
            evolved = self.state.items[item.evolves_to]
            embed = _make_embed(
                "inventory",
                "Reforge Complete",
                f"{item.name} reforges into **{evolved.name}**!",
                discord.Colour.from_str("#c27c0e"),
            )
            await inter.response.send_message(embed=embed, ephemeral=True)

        view = OwnedView(interaction.user.id, timeout=90.0)
        view.add_item(
            CallbackSelect(
                options=options,
                placeholder="Select an item to reforge",
                callback=_perform_reforge,
            )
        )
        embed = _make_embed(
            "inventory",
            "Reforge Item",
            "Choose an item below to attempt a reforge.",
            discord.Colour.from_str("#c27c0e"),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="use_item", description="Consume an item from your inventory")
    @app_commands.guild_only()
    async def use_item(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Inventory",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        options: list[discord.SelectOption] = []
        for key, amount in sorted(player.inventory.items()):
            if amount <= 0:
                continue
            item = self.state.items.get(key)
            if not item:
                continue
            if item.item_type != "consumable" and not item.race_transformation:
                continue
            options.append(
                discord.SelectOption(
                    label=item.name,
                    value=key,
                    description=f"Owned ×{format_number(amount)}",
                )
            )

        if not options:
            embed = _make_embed(
                "warning",
                "No Consumables",
                "You have no consumable items available to use.",
                discord.Colour.orange(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        async def _consume(inter: discord.Interaction, key: str) -> None:
            refreshed = await self._fetch_player(guild.id, inter.user.id)
            if not refreshed:
                await inter.response.send_message(
                    "Use `/register` first.", ephemeral=True
                )
                return
            quantity = refreshed.inventory.get(key, 0)
            if quantity <= 0:
                await inter.response.send_message(
                    "You no longer possess that item.", ephemeral=True
                )
                return
            item_obj = self.state.items.get(key)
            if not item_obj:
                await inter.response.send_message(
                    "That item is no longer configured.", ephemeral=True
                )
                return
            refreshed.inventory[key] = quantity - 1
            if refreshed.inventory[key] <= 0:
                refreshed.inventory.pop(key)

            description: str
            colour = discord.Colour.from_str("#2ecc71")
            if item_obj.race_transformation:
                target_race = self.state.races.get(item_obj.race_transformation)
                if target_race is None:
                    refreshed.inventory[key] = refreshed.inventory.get(key, 0) + 1
                    await inter.response.send_message(
                        "That transformation elixir has lost its potency.",
                        ephemeral=True,
                    )
                    return
                refreshed.race_key = target_race.key
                member_obj = await self._resolve_member(guild, inter.user)
                await self._sync_race_roles(guild, member_obj, target_race)
                qi_stage = self.state.get_stage(
                    refreshed.cultivation_stage, CultivationPath.QI
                )
                body_stage = self.state.get_stage(
                    refreshed.body_cultivation_stage, CultivationPath.BODY
                )
                soul_stage = self.state.get_stage(
                    refreshed.soul_cultivation_stage, CultivationPath.SOUL
                )
                refreshed.recalculate_stage_stats(qi_stage, body_stage, soul_stage)
                description = (
                    f"Your lineage shifts as the potion rewrites your essence. "
                    f"You are now of the {target_race.name} race."
                )
            else:
                description = (
                    f"You consume **{item_obj.name}**, but its effects are subtle and leave no lasting trace."
                )
            await self._save_player(guild.id, refreshed)
            embed = _make_embed(
                "inventory",
                "Item Consumed",
                description,
                colour,
            )
            await inter.response.send_message(embed=embed, ephemeral=True)

        view = OwnedView(interaction.user.id, timeout=90.0)
        view.add_item(
            CallbackSelect(
                options=options,
                placeholder="Select an item to consume",
                callback=_consume,
            )
        )
        embed = _make_embed(
            "inventory",
            "Use Item",
            "Choose a consumable to use from your inventory.",
            discord.Colour.from_str("#2ecc71"),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="travel", description="Travel to a configured location")
    @app_commands.guild_only()
    async def travel(self, interaction: discord.Interaction) -> None:
        await self._open_travel_menu(interaction)

    @app_commands.command(name="wander", description="Explore your current location for encounters")
    @app_commands.guild_only()
    async def wander(self, interaction: discord.Interaction) -> None:
        await self._handle_wander_action(interaction)

    async def _handle_wander_action(
        self,
        interaction: discord.Interaction,
        *,
        return_callback: SimpleCallback | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            embed = _make_embed(
                "error",
                "Guild Only",
                "Use this in a server.",
                discord.Colour.red(),
            )
            await self._respond_interaction(
                interaction,
                embed=embed,
                view=self._build_return_view(interaction, return_callback),
            )
            return
        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            embed = _make_embed(
                "warning",
                "No Cultivation Record",
                "Use /register first.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(
                interaction,
                embed=embed,
                view=self._build_return_view(interaction, return_callback),
            )
            return
        if player.in_combat:
            embed = _make_embed(
                "warning",
                "In Combat",
                "You are already engaged in an encounter. Resolve it before wandering again.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(
                interaction,
                embed=embed,
                view=self._build_return_view(interaction, return_callback),
            )
            return
        if (player.current_hp is not None and player.current_hp <= 0) or (
            player.current_soul_hp is not None and player.current_soul_hp <= 0
        ):
            embed = _make_embed(
                "warning",
                "Too Wounded",
                "Seek shelter in a safe zone to recover before venturing out again.",
                discord.Colour.orange(),
            )
            await self._respond_interaction(
                interaction,
                embed=embed,
                view=self._build_return_view(interaction, return_callback),
            )
            return

        self.state.ensure_world_map()
        engine = self.state.travel_engine
        current = self._ensure_player_coordinate(player)
        world_map = self.state.world_map
        events: list[TravelEvent] = []
        last_path: TravelPath | None = None

        try:
            plan = engine.random_wander_plan(player)
            outcome = engine.execute_plan(player, plan)
            events = list(outcome.events)
            last_path = outcome.travel_path
            current = plan.destination
        except ValueError:
            events = []
            last_path = None

        await self._save_player(guild.id, player)

        headline = (
            f"Your wandering stirs {len(events)} notable happenings."
            if events
            else "The wilderness is quiet, but your map grows sharper."
        )
        embed = self._build_travel_embed(
            player,
            path=last_path,
            events=events,
            headline=headline,
        )

        async def _repeat_wander(button_interaction: discord.Interaction) -> None:
            await self._handle_wander_action(
                button_interaction,
                return_callback=return_callback,
            )

        wander_view = WanderExpeditionView(
            player.user_id,
            wander_callback=_repeat_wander,
            return_callback=return_callback,
        )
        await self._respond_interaction(
            interaction,
            embed=embed,
            view=wander_view,
        )
        try:
            if interaction.type is InteractionType.component:
                wander_view.message = interaction.message
            else:
                wander_view.message = await interaction.original_response()
        except discord.HTTPException:
            wander_view.message = None
    @app_commands.command(name="catalogue", description="Show configured cultivation data")
    @require_admin()
    async def catalogue(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            embed = _make_embed("error",
                "Guild Only",
                "Use this in a server.",
                discord.Colour.red(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        guild = interaction.guild
        await self.ensure_guild_loaded(guild.id)

        players_bucket = await self.store.get(guild.id, "players")
        
        def format_ansi_lines(lines: Sequence[str], limit: int = 20) -> str:
            cleaned = [line for line in lines if line]
            if not cleaned:
                return ansi_block([ansi_colour("No data configured", "gray", dim=True)])

            limited = list(cleaned[:limit])
            overflow_count = max(0, len(cleaned) - len(limited))
            overflow_note = (
                ansi_colour(
                    f"…and {overflow_count} more entries", "yellow", dim=True
                )
                if overflow_count > 0
                else None
            )
            truncation_note = ansi_colour(
                "…additional entries truncated…", "yellow", dim=True
            )
            embed_field_limit = 1024

            visible_lines: list[str] = []
            for line in limited:
                candidate = visible_lines + [line]
                if len(ansi_block(candidate)) > embed_field_limit:
                    break
                visible_lines.append(line)

            truncated_for_length = len(visible_lines) < len(limited)
            notes: list[str] = []
            if overflow_note:
                notes.append(overflow_note)
            if truncated_for_length:
                notes.append(truncation_note)

            if visible_lines:
                combined_lines: list[str] = [*visible_lines, *notes]
            else:
                combined_lines = notes.copy() or [truncation_note]

            rendered = ansi_block(combined_lines)
            while len(rendered) > embed_field_limit:
                if notes:
                    notes.pop()
                elif visible_lines:
                    visible_lines.pop()
                    truncated_for_length = True
                else:
                    combined_lines = [truncation_note]
                    rendered = ansi_block(combined_lines)
                    break
                if visible_lines:
                    combined_lines = [*visible_lines, *notes]
                else:
                    combined_lines = notes.copy()
                if not combined_lines:
                    combined_lines = [truncation_note]
                rendered = ansi_block(combined_lines)

            if truncated_for_length and truncation_note not in combined_lines:
                candidate_lines = [*combined_lines, truncation_note]
                candidate_rendered = ansi_block(candidate_lines)
                if len(candidate_rendered) <= embed_field_limit:
                    combined_lines = candidate_lines
                    rendered = candidate_rendered

            if len(rendered) > embed_field_limit:
                return ansi_block([truncation_note])
            return rendered
        
        def highlight(text: str, colour: str, *, bold: bool = True, dim: bool = False) -> str:
            return ansi_colour(text, colour, bold=bold, dim=dim)
        
        PATH_ORDER = (CultivationPath.QI, CultivationPath.BODY, CultivationPath.SOUL)
        PATH_ICONS = {
            CultivationPath.QI: "[Qi]",
            CultivationPath.BODY: "[Body]",
            CultivationPath.SOUL: "[Soul]",
        }
        PATH_COLOURS = {
            CultivationPath.QI: "cyan",
            CultivationPath.BODY: "green",
            CultivationPath.SOUL: "magenta",
        }
        
        stage_lists: dict[CultivationPath, list[CultivationStage]] = {
            path: [] for path in PATH_ORDER
        }
        stage_detail_lines: dict[CultivationPath, list[str]] = {
            path: [] for path in PATH_ORDER
        }
        realm_overview_lines: list[str] = []
        
        for stage in sorted(
            self.state.iter_all_stages(), key=lambda entry: entry.ordering_tuple
        ):
            try:
                path = CultivationPath.from_value(
                    getattr(stage, "path", CultivationPath.QI.value)
                )
            except ValueError:
                path = CultivationPath.QI
            stage_lists.setdefault(path, []).append(stage)
            bonuses = stage.stat_bonuses
            focus_stats = [
                stat.replace("_", " ").title()
                for stat, value in bonuses.items()
                if abs(value) > 1e-6
            ]
            focus_text = (
                " / ".join(focus_stats)
                if focus_stats
                else "Balanced Focus"
            )
            summary_parts = [
                highlight(f"Success {stage.success_rate * 100:.0f}%", "green"),
                highlight(f"EXP {format_number(stage.exp_required)}", "yellow"),
                highlight(
                    f"Base {stage.base_stat / DEFAULT_STAGE_BASE_STAT:.2f}×"
                    if DEFAULT_STAGE_BASE_STAT
                    else f"Base {stage.base_stat:.2f}",
                    "cyan",
                ),
                highlight(focus_text, "violet", dim=not focus_stats),
            ]
            label = highlight(
                f"{PATH_ICONS.get(path, '[Path]')} {stage.combined_name}",
                PATH_COLOURS.get(path, "white"),
                bold=True,
            )
            stage_detail_lines.setdefault(path, []).append(
                f"{label} — {' • '.join(summary_parts)}"
            )
        
        for path in PATH_ORDER:
            stages = stage_lists.get(path, [])
            icon = PATH_ICONS.get(path, "[Path]")
            colour = PATH_COLOURS.get(path, "white")
            label = highlight(
                f"{icon} {path.value.replace('_', ' ').title()}", colour, bold=True
            )
            if not stages:
                realm_overview_lines.append(
                    f"{label} — {highlight('No realms configured', 'gray', dim=True)}"
                )
                continue
            peak = stages[-1]
            peak_text = highlight(peak.combined_name, colour)
            realm_overview_lines.append(
                f"{label} — {highlight(str(len(stages)), 'yellow')} realms • Peak {peak_text}"
            )
        
        active_skill_lines: list[str] = []
        passive_skill_lines: list[str] = []
        for skill in sorted(self.state.skills.values(), key=lambda entry: entry.name):
            stars = self._skill_grade_stars(skill.grade)
            damage_colour = DAMAGE_TYPE_COLOURS.get(skill.skill_type.value, "white")
            weapon_text = ""
            if skill.weapon:
                weapon_text = highlight(
                    skill.weapon.value.replace("_", " ").title(), "orange"
                )
            affinity_entries = skill.elements or (
                (skill.element,) if skill.element else ()
            )
            if affinity_entries:
                labels = [entry.display_name for entry in affinity_entries]
                element_text = highlight(" / ".join(labels), "cyan")
            else:
                element_text = highlight("Neutral", "gray", dim=True)
            description = skill.description.strip() if skill.description else ""
            base_parts = [
                highlight(
                    f"{skill.damage_ratio:.2f}× {skill.skill_type.value.title()}",
                    damage_colour,
                ),
                element_text,
            ]
            if skill.proficiency_max:
                base_parts.append(
                    highlight(
                        f"Mastery {format_number(skill.proficiency_max)}", "yellow"
                    )
                )
            if weapon_text:
                base_parts.append(weapon_text)
            if description:
                base_parts.append(highlight(description, "white", dim=True))
            line = (
                f"{highlight(f'[Skill] {skill.name} {stars}', 'red')} — "
                + " • ".join(base_parts)
            )
            if skill.category is SkillCategory.ACTIVE:
                active_skill_lines.append(line)
            else:
                bonus_parts = []
                for stat_name, raw_value in skill.stat_bonuses.items():
                    if abs(raw_value) <= 1e-6:
                        continue
                    stat_label = stat_name.replace("_", " ").title()
                    bonus_parts.append(
                        highlight(f"{stat_label} +{raw_value:.1f}", "teal")
                    )
                if skill.passive_heal_amount > 0 and skill.passive_heal_interval > 0:
                    pool = "Soul" if skill.passive_heal_pool == "soul" else "HP"
                    bonus_parts.append(
                        highlight(
                            f"Heal {skill.passive_heal_amount:.1f} {pool} / {skill.passive_heal_interval}s",
                            "green",
                        )
                    )
                if bonus_parts:
                    line = f"{line}\n  • " + "\n  • ".join(bonus_parts)
                passive_skill_lines.append(line)
        
        technique_lines: list[str] = []
        for technique in sorted(
            self.state.cultivation_techniques.values(), key=lambda entry: entry.name
        ):
            path_value = CultivationPath.from_value(technique.path)
            effect_text, stat_details = _technique_effect_details(technique, path_value)
            stars = self._skill_grade_stars(technique.grade)
            description = technique.description.strip() if technique.description else ""
            detail_parts = []
            if effect_text:
                detail_parts.append(
                    highlight(effect_text, PATH_COLOURS.get(path_value, "magenta"))
                )
            if stat_details:
                detail_parts.append(
                    highlight("; ".join(stat_details), "yellow")
                )
            if technique.skills:
                linked = ", ".join(
                    self.state.skills.get(key, Skill(
                        key, key, "", DamageType.PHYSICAL, 1.0, 1
                    )).name
                    for key in technique.skills
                )
                detail_parts.append(
                    highlight(f"Synergises with: {linked}", "cyan")
                )
            if description:
                detail_parts.append(highlight(description, "white", dim=True))
            label = highlight(
                f"{PATH_ICONS.get(path_value, '[Path]')} {technique.name} {stars}",
                PATH_COLOURS.get(path_value, "magenta"),
            )
            technique_lines.append(f"{label} — {' • '.join(detail_parts) if detail_parts else highlight('No listed bonuses', 'gray', dim=True)}")
        
        item_type_icons = {
            "equipment": "[Equipment]",
            "material": "[Material]",
            "quest": "[Quest]",
            "consumable": "[Consumable]",
        }
        item_categories: dict[str, list[str]] = {}
        item_overview_lines: list[str] = []
        for item in sorted(self.state.items.values(), key=lambda entry: entry.name):
            type_key = str(getattr(item, "item_type", "item") or "item").lower()
            colour = ITEM_TYPE_COLOURS.get(type_key, "white")
            icon = item_type_icons.get(type_key, "[Item]")
            label = highlight(f"{icon} {item.name}", colour)
            summary_parts: list[str] = []
            if item.equipment_slot:
                slot = item.equipment_slot.value.replace("_", " ").title()
                summary_parts.append(highlight(slot, "teal"))
            modifiers = item.stat_modifiers
            modifier_bits = [
                f"{stat.replace('_', ' ').title()} +{value:.0f}"
                for stat, value in modifiers.items()
                if abs(value) > 1e-6
            ]
            if modifier_bits:
                summary_parts.append(highlight(", ".join(modifier_bits), "green"))
            if item.inventory_space_bonus:
                summary_parts.append(
                    highlight(
                        f"+{format_number(item.inventory_space_bonus)} slots", "yellow"
                    )
                )
            if item.skill_unlocks:
                summary_parts.append(
                    highlight(
                        f"Unlocks {len(item.skill_unlocks)} skill(s)", "purple"
                    )
                )
            if item.race_transformation:
                summary_parts.append(
                    highlight(
                        f"Transforms into {item.race_transformation.title()}", "magenta"
                    )
                )
            description = item.description.strip() if item.description else ""
            if description:
                summary_parts.append(highlight(description, "white", dim=True))
            item_categories.setdefault(type_key, []).append(
                f"{label} — {' • '.join(summary_parts) if summary_parts else highlight('No additional details', 'gray', dim=True)}"
            )
        
        for type_key, entries in item_categories.items():
            icon = item_type_icons.get(type_key, "[Item]")
            colour = ITEM_TYPE_COLOURS.get(type_key, "white")
            label = highlight(f"{icon} {type_key.title()}", colour)
            item_overview_lines.append(
                f"{label} — {highlight(format_number(len(entries)), 'yellow')} entries"
            )
        
        npc_type_icons = {
            "dialog": "[Dialogue]",
            "shop": "[Shop]",
            "enemy": "[Enemy]",
            "ally": "[Ally]",
        }
        npc_lines: list[str] = []
        for _, npc in sorted(self.state.npcs.items()):
            npc_type = npc.npc_type.value
            icon = npc_type_icons.get(npc_type, "[NPC]")
            descriptor = npc.description or npc.dialogue or "No description provided."
            detail_bits = [highlight(descriptor, "white", dim=True)]
            if npc.shop_items:
                detail_bits.append(
                    highlight(f"Offers {len(npc.shop_items)} wares", "yellow")
                )
            npc_lines.append(
                f"{highlight(f'{icon} {npc.name}', 'cyan')} — {' • '.join(detail_bits)}"
            )
        
        player_lines: list[str] = []
        for payload in players_bucket.values():
            name = str(payload.get("name") or f"Cultivator {payload.get('user_id', '???')}")
            stage_key = payload.get("cultivation_stage")
            stage_obj = self.state.get_stage(stage_key)
            if stage_obj:
                path = CultivationPath.from_value(stage_obj.path)
                stage_text = highlight(stage_obj.combined_name, PATH_COLOURS.get(path, "white"))
            else:
                stage_text = highlight(stage_key or "Unbound", "gray", dim=True)
            player_lines.append(
                f"{highlight(f'[Player] {name}', 'cyan')} — {stage_text}"
            )
        player_lines.sort()
        
        race_lines: list[str] = []
        for race in sorted(self.state.races.values(), key=lambda entry: entry.name):
            dice_text = f"{race.innate_dice_count}d{race.innate_dice_faces}"
            if race.innate_drop_lowest:
                dice_text += f" drop {race.innate_drop_lowest}"
            multipliers = race.stat_multipliers.to_mapping()
            modifier_bits = [
                f"{name.title()} ×{value:.2f}"
                for name, value in multipliers.items()
                if abs(value - 1.0) > 1e-3
            ]
            description = race.description.strip() if race.description else ""
            parts = [highlight(f"Dice {dice_text}", "yellow")]
            if modifier_bits:
                parts.append(highlight(", ".join(modifier_bits), "green"))
            if description:
                parts.append(highlight(description, "white", dim=True))
            race_lines.append(
                f"{highlight(f'[Race] {race.name}', 'cyan')} — {' • '.join(parts)}"
            )
        
        trait_lines: list[str] = []
        for trait in sorted(self.state.traits.values(), key=lambda entry: entry.name):
            multipliers = trait.stat_multipliers.to_mapping()
            modifier_bits = [
                f"{name.title()} ×{value:.2f}"
                for name, value in multipliers.items()
                if abs(value - 1.0) > 1e-3
            ]
            affinity_bits = [
                affinity_description(affinity, include_relationships=True)
                for affinity in trait.grants_affinities
                if isinstance(affinity, SpiritualAffinity)
            ]
            description = trait.description.strip() if trait.description else ""
            detail_parts = []
            if modifier_bits:
                detail_parts.append(highlight(", ".join(modifier_bits), "magenta"))
            if affinity_bits:
                detail_parts.append(
                    highlight("Affinities: " + ", ".join(affinity_bits), "cyan")
                )
            if trait.grants_titles:
                detail_parts.append(
                    highlight(f"Grants {len(trait.grants_titles)} title(s)", "yellow")
                )
            if description:
                detail_parts.append(highlight(description, "white", dim=True))
            trait_lines.append(
                f"{highlight(f'[Trait] {trait.name}', 'purple')} — {' • '.join(detail_parts) if detail_parts else highlight('No bonuses listed', 'gray', dim=True)}"
            )
        
        title_lines: list[str] = []
        for title in sorted(self.state.titles.values(), key=lambda entry: entry.name):
            placement = "Prefix" if title.position is TitlePosition.PREFIX else "Suffix"
            description = title.description.strip() if title.description else ""
            detail_parts = [highlight(f"{placement} title", "yellow")]
            if description:
                detail_parts.append(highlight(description, "white", dim=True))
            title_lines.append(
                f"{highlight(f'[Title] {title.name}', 'orange')} — {' • '.join(detail_parts)}"
            )
        
        quest_lines: list[str] = []
        for quest in sorted(self.state.quests.values(), key=lambda entry: entry.name):
            objective = quest.objective
            target_label = objective.target_key or objective.target_type.title()
            rewards = []
            for item_key, amount in quest.rewards.items():
                item_name = self.state.items.get(item_key)
                label = item_name.name if item_name else item_key
                rewards.append(f"{format_number(amount)}× {label}")
            reward_text = ", ".join(rewards) if rewards else "No rewards"
            quest_lines.append(
                f"{highlight(f'[Quest] {quest.name}', 'yellow')} — "
                f"{highlight(f'{objective.kill_count}× {objective.target_type.title()} ({target_label})', 'teal')} • "
                f"{highlight('Rewards: ' + reward_text, 'green')}"
            )
        
        enemy_lines: list[str] = []
        for enemy in sorted(self.state.enemies.values(), key=lambda entry: entry.name):
            stage_key = enemy.cultivation_stage or enemy.body_cultivation_stage
            stage_obj = self.state.get_stage(stage_key, enemy.active_path)
            stage_text = (
                highlight(stage_obj.combined_name, "red") if stage_obj else highlight("Unspecified", "gray", dim=True)
            )
            affinity = (
                enemy.affinity.display_name if enemy.affinity else "None"
            )
            loot_count = len(enemy.loot_table)
            detail_parts = [
                stage_text,
                highlight(f"Affinity: {affinity}", "purple"),
                highlight(f"Skills: {len(enemy.skills)}", "yellow"),
                highlight(f"Loot entries: {loot_count}", "green"),
            ]
            enemy_lines.append(
                f"{highlight(f'[Enemy] {enemy.name}', 'red')} — {' • '.join(detail_parts)}"
            )
        
        boss_lines: list[str] = []
        for boss in sorted(self.state.bosses.values(), key=lambda entry: entry.name):
            mechanics = boss.special_mechanics or "No special mechanics described."
            boss_lines.append(
                f"{highlight(f'[Boss] {boss.name}', 'orange')} — {highlight(mechanics, 'white', dim=not mechanics.strip())}"
            )
        
        location_lines: list[str] = []
        for location in sorted(self.state.locations.values(), key=lambda entry: entry.name):
            encounter = highlight(f"Encounter {location.encounter_rate * 100:.0f}%", "red")
            zone = highlight("Sanctuary" if location.is_safe else "Wilderness", "cyan")
            counts = []
            if location.enemies:
                counts.append(highlight(f"Enemies: {len(location.enemies)}", "orange"))
            if location.bosses:
                counts.append(highlight(f"Bosses: {len(location.bosses)}", "purple"))
            if location.quests:
                counts.append(highlight(f"Quests: {len(location.quests)}", "yellow"))
            if location.npcs:
                counts.append(highlight(f"NPCs: {len(location.npcs)}", "green"))
            description = location.description or ""
            if description:
                counts.append(highlight(description, "white", dim=True))
            location_lines.append(
                f"{highlight(f'[Travel] {location.name}', 'teal')} — {zone} • {encounter}"
                + (" • " + " • ".join(counts) if counts else "")
            )
        
        currency_lines: list[str] = []
        for currency in sorted(
            self.state.currencies.values(), key=lambda entry: entry.name
        ):
            description = currency.description.strip() if currency.description else ""
            parts = [highlight(description or "No description provided", "white", dim=not description)]
            currency_lines.append(
                f"{highlight(f'[Currency] {currency.name}', 'yellow')} — {' • '.join(parts)}"
            )
        
        shop_lines: list[str] = []
        for shop_item in sorted(
            self.state.shop_items.values(), key=lambda entry: entry.item_key
        ):
            item = self.state.items.get(shop_item.item_key)
            currency = self.state.currencies.get(shop_item.currency_key)
            item_name = item.name if item else shop_item.item_key
            currency_name = currency.name if currency else shop_item.currency_key
            shop_lines.append(
                f"{highlight(f'[Shop Item] {item_name}', 'green')} — {highlight(f'Cost {format_number(shop_item.price)} {currency_name}', 'yellow')}"
            )
        
        people_overview_lines = [
            f"{highlight('[Players]', 'cyan')} — {highlight(format_number(len(player_lines)), 'yellow')} registered",
            f"{highlight('[NPCs]', 'cyan')} — {highlight(format_number(len(npc_lines)), 'yellow')} notable figures",
            f"{highlight('[Races]', 'cyan')} — {highlight(format_number(len(race_lines)), 'yellow')} lineages",
            f"{highlight('[Traits]', 'purple')} — {highlight(format_number(len(trait_lines)), 'yellow')} distinctive marks",
            f"{highlight('[Titles]', 'orange')} — {highlight(format_number(len(title_lines)), 'yellow')} honors",
        ]
        
        world_overview_lines = [
            f"{highlight('[Quests]', 'yellow')} — {highlight(format_number(len(quest_lines)), 'yellow')} adventures",
            f"{highlight('[Enemies]', 'red')} — {highlight(format_number(len(enemy_lines)), 'yellow')} adversaries",
            f"{highlight('[Bosses]', 'orange')} — {highlight(format_number(len(boss_lines)), 'yellow')} calamities",
            f"{highlight('[Travel] Locations', 'teal')} — {highlight(format_number(len(location_lines)), 'yellow')} regions",
        ]
        
        commerce_overview_lines = [
            f"{highlight('[Currencies]', 'yellow')} — {highlight(format_number(len(currency_lines)), 'yellow')} tender",
            f"{highlight('[Shop Wares]', 'green')} — {highlight(format_number(len(shop_lines)), 'yellow')} offerings",
        ]
        
        def build_overview_embed() -> discord.Embed:
            embed = discord.Embed(
                title=_label_with_icon("guide", "Catalogue Overview"),
                colour=discord.Colour.from_str("#16a085"),
            )
            embed.add_field(
                name=_label_with_icon("guide", "Repository Summary"),
                value=format_ansi_lines(realm_overview_lines),
                inline=False,
            )
            embed.add_field(
                name=_label_with_icon("inventory", "Treasury Snapshot"),
                value=format_ansi_lines(item_overview_lines),
                inline=False,
            )
            embed.add_field(
                name=_label_with_icon("race", "People of the Sect"),
                value=format_ansi_lines(people_overview_lines),
                inline=False,
            )
            embed.add_field(
                name=_label_with_icon("location", "World at a Glance"),
                value=format_ansi_lines(world_overview_lines),
                inline=False,
            )
            embed.add_field(
                name=_label_with_icon("currency", "Commerce Highlights"),
                value=format_ansi_lines(commerce_overview_lines),
                inline=False,
            )
            return embed
        
        def build_realms_embed() -> discord.Embed:
            embed = discord.Embed(
                title=_label_with_icon("stage", "Cultivation Realms"),
                colour=discord.Colour.from_str("#9b59b6"),
            )
            embed.description = (
                "Use the buttons below to dive into each path's detailed stage flow."
            )
            embed.add_field(
                name=_label_with_icon("stage", "Path Highlights"),
                value=format_ansi_lines(realm_overview_lines),
                inline=False,
            )
            return embed
        
        def build_realms_detail_embed(path: CultivationPath) -> discord.Embed:
            embed = discord.Embed(
                title=_label_with_icon(
                    "stage",
                    f"{path.value.replace('_', ' ').title()} Path"
                ),
                colour=discord.Colour.from_str("#8e44ad"),
            )
            embed.add_field(
                name=_label_with_icon("stage", "Realm Progression"),
                value=format_ansi_lines(stage_detail_lines.get(path, []), limit=25),
                inline=False,
            )
            return embed
        
        def build_skills_embed() -> discord.Embed:
            embed = discord.Embed(
                title=_label_with_icon("guide", "Sect Techniques"),
                colour=discord.Colour.gold(),
            )
            embed.description = (
                "Browse the tabs for active martial skills, supportive scriptures, and cultivation techniques."
            )
            embed.add_field(
                name=_label_with_icon("combat", "Active Techniques"),
                value=format_ansi_lines(active_skill_lines, limit=5),
                inline=False,
            )
            embed.add_field(
                name=_label_with_icon("cultivation", "Passive Arts"),
                value=format_ansi_lines(passive_skill_lines, limit=5),
                inline=False,
            )
            embed.add_field(
                name=_label_with_icon("cultivation", "Cultivation Methods"),
                value=format_ansi_lines(technique_lines, limit=5),
                inline=False,
            )
            return embed
        
        def build_skills_page(lines: list[str], title: str, colour: discord.Colour) -> discord.Embed:
            embed = discord.Embed(
                title=_label_with_icon("cultivation", title),
                colour=colour,
            )
            embed.add_field(
                name=_label_with_icon("cultivation", "Detailed Entries"),
                value=format_ansi_lines(lines, limit=25),
                inline=False,
            )
            return embed
        
        def build_items_embed() -> discord.Embed:
            embed = discord.Embed(
                title=_label_with_icon("inventory", "Catalogue of Relics"),
                colour=discord.Colour.from_str("#2ecc71"),
            )
            embed.description = (
                "Use the buttons to inspect every relic, material, or quest item in detail."
            )
            embed.add_field(
                name=_label_with_icon("inventory", "Inventory Overview"),
                value=format_ansi_lines(item_overview_lines),
                inline=False,
            )
            return embed
        
        def build_item_category_embed(key: str, label: str) -> discord.Embed:
            embed = discord.Embed(
                title=_label_with_icon("inventory", label),
                colour=discord.Colour.from_str("#27ae60"),
            )
            embed.add_field(
                name=_label_with_icon("inventory", "Registered Entries"),
                value=format_ansi_lines(item_categories.get(key, []), limit=25),
                inline=False,
            )
            return embed
        
        def build_people_embed() -> discord.Embed:
            embed = discord.Embed(
                title=_label_with_icon("guide", "Sect Dossiers"),
                colour=discord.Colour.blurple(),
            )
            embed.description = (
                "Use the buttons to review disciples, allies, lineages, and titles."
            )
            embed.add_field(
                name=_label_with_icon("guide", "At a Glance"),
                value=format_ansi_lines(people_overview_lines),
                inline=False,
            )
            return embed
        
        def build_people_page(lines: list[str], title: str, colour: discord.Colour) -> discord.Embed:
            embed = discord.Embed(
                title=_label_with_icon("guide", title),
                colour=colour,
            )
            embed.add_field(
                name=_label_with_icon("guide", "Entries"),
                value=format_ansi_lines(lines, limit=25),
                inline=False,
            )
            return embed
        
        def build_world_embed() -> discord.Embed:
            embed = discord.Embed(
                title=_label_with_icon("location", "World Ledger"),
                colour=discord.Colour.from_str("#1abc9c"),
            )
            embed.description = (
                "Use the buttons to review quests, enemies, bosses, or locations."
            )
            embed.add_field(
                name=_label_with_icon("location", "Highlights"),
                value=format_ansi_lines(world_overview_lines),
                inline=False,
            )
            return embed
        
        def build_world_page(lines: list[str], title: str, colour: discord.Colour) -> discord.Embed:
            embed = discord.Embed(
                title=_label_with_icon("location", title),
                colour=colour,
            )
            embed.add_field(
                name=_label_with_icon("location", "Entries"),
                value=format_ansi_lines(lines, limit=25),
                inline=False,
            )
            return embed
        
        def build_commerce_embed() -> discord.Embed:
            embed = discord.Embed(
                title=_label_with_icon("currency", "Commerce Registry"),
                colour=discord.Colour.from_str("#f1c40f"),
            )
            embed.description = "Track the flow of currency and the wares available through sect vendors."
            embed.add_field(
                name=_label_with_icon("currency", "Highlights"),
                value=format_ansi_lines(commerce_overview_lines),
                inline=False,
            )
            return embed
        
        def build_commerce_page(lines: list[str], title: str, colour: discord.Colour) -> discord.Embed:
            embed = discord.Embed(
                title=_label_with_icon("currency", title),
                colour=colour,
            )
            embed.add_field(
                name=_label_with_icon("currency", "Entries"),
                value=format_ansi_lines(lines, limit=25),
                inline=False,
            )
            return embed
        
        pages = {
            "overview": (
                "Overview",
                WANDER_TRAVEL_EMOJI_TEXT,
                build_overview_embed,
            ),
            "realms": (
                "Realms",
                "<:heaven_cultivation:1433142350590378086>",
                build_realms_embed,
            ),
            "skills": ("Skills", "📜", build_skills_embed),
            "items": ("Treasury", "🎁", build_items_embed),
            "people": ("Dossiers", "🧬", build_people_embed),
            "world": ("World", "🌍", build_world_embed),
            "commerce": ("Commerce", CURRENCY_EMOJI_TEXT, build_commerce_embed),
        }
        
        submenus: dict[str, dict[str, tuple[str, str | None, ProfilePageBuilder]]] = {
            "realms": {
                "realms-qi": (
                    "Qi Path",
                    "💠",
                    lambda path=CultivationPath.QI: build_realms_detail_embed(path),
                ),
                "realms-body": (
                    "Body Path",
                    "💪",
                    lambda path=CultivationPath.BODY: build_realms_detail_embed(path),
                ),
                "realms-soul": (
                    "Soul Path",
                    "🧠",
                    lambda path=CultivationPath.SOUL: build_realms_detail_embed(path),
                ),
            },
            "skills": {
                "skills-active": (
                    "Active Techniques",
                    "⚔️",
                    lambda: build_skills_page(
                        active_skill_lines,
                        "Active Techniques",
                        discord.Colour.red(),
                    ),
                ),
                "skills-passive": (
                    "Passive Arts",
                    "📿",
                    lambda: build_skills_page(
                        passive_skill_lines,
                        "Passive Arts",
                        discord.Colour.blurple(),
                    ),
                ),
                "skills-techniques": (
                    "Cultivation Methods",
                    "<:heaven_cultivation:1433142350590378086>",
                    lambda: build_skills_page(
                        technique_lines,
                        "Cultivation Techniques",
                        discord.Colour.from_str("#9b59b6"),
                    ),
                ),
            },
            "items": {
                key: (
                    f"{key.title()}",
                    item_type_icons.get(key, "📦"),
                    (lambda item_key=key: build_item_category_embed(
                        item_key,
                        f"{item_key.title()} Inventory",
                    )),
                )
                for key in item_categories
            },
            "people": {
                "people-players": (
                    "Cultivators",
                    "👥",
                    lambda: build_people_page(
                        player_lines,
                        "Registered Cultivators",
                        discord.Colour.blurple(),
                    ),
                ),
                "people-npcs": (
                    "NPCs",
                    "🧙",
                    lambda: build_people_page(
                        npc_lines,
                        "Notable NPCs",
                        discord.Colour.from_str("#1abc9c"),
                    ),
                ),
                "people-races": (
                    "Races",
                    "🧬",
                    lambda: build_people_page(
                        race_lines,
                        "Lineages",
                        discord.Colour.from_str("#3498db"),
                    ),
                ),
                "people-traits": (
                    "Traits",
                    "✨",
                    lambda: build_people_page(
                        trait_lines,
                        "Innate Traits",
                        discord.Colour.from_str("#9b59b6"),
                    ),
                ),
                "people-titles": (
                    "Titles",
                    "🎖️",
                    lambda: build_people_page(
                        title_lines,
                        "Honorific Titles",
                        discord.Colour.from_str("#f39c12"),
                    ),
                ),
            },
            "world": {
                "world-quests": (
                    "Quests",
                    "🗺️",
                    lambda: build_world_page(
                        quest_lines,
                        "Quest Ledger",
                        discord.Colour.from_str("#f1c40f"),
                    ),
                ),
                "world-enemies": (
                    "Enemies",
                    "⚔️",
                    lambda: build_world_page(
                        enemy_lines,
                        "Enemy Bestiary",
                        discord.Colour.from_str("#e74c3c"),
                    ),
                ),
                "world-bosses": (
                    "Bosses",
                    "👑",
                    lambda: build_world_page(
                        boss_lines,
                        "Boss Compendium",
                        discord.Colour.from_str("#d35400"),
                    ),
                ),
                "world-locations": (
                    "Locations",
                    WANDER_TRAVEL_EMOJI_TEXT,
                    lambda: build_world_page(
                        location_lines,
                        "Exploration Atlas",
                        discord.Colour.from_str("#1abc9c"),
                    ),
                ),
            },
            "commerce": {
                "commerce-currencies": (
                    "Currencies",
                    CURRENCY_EMOJI_TEXT,
                    lambda: build_commerce_page(
                        currency_lines,
                        "Currency Ledger",
                        discord.Colour.from_str("#f1c40f"),
                    ),
                ),
                "commerce-shop": (
                    "Shop Wares",
                    "🛒",
                    lambda: build_commerce_page(
                        shop_lines,
                        "Shop Inventory",
                        discord.Colour.from_str("#2ecc71"),
                    ),
                ),
            },
        }
        
        def refresh_only_factory(_: ProfileView) -> Iterable[discord.ui.Item[ProfileView]]:
            return [ProfileRefreshButton()]

        page_item_factories: dict[
            str, Callable[[ProfileView], Iterable[discord.ui.Item[ProfileView]]]
        ] = {key: refresh_only_factory for key in pages}

        for entries in submenus.values():
            for key in entries:
                page_item_factories[key] = refresh_only_factory
        
        view = ProfileView(
            owner_id=interaction.user.id,
            pages=pages,
            submenus=submenus,
            initial_page="overview",
            timeout=240.0,
            page_item_factories=page_item_factories,
        )
        embed = view.initial_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        try:
            view.message = await interaction.original_response()
        except discord.HTTPException:
            view.message = None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PlayerCog(bot))
