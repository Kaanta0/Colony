from __future__ import annotations

import asyncio
import random
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from ..config import BotConfig
from ..game import award_loot, gain_combat_exp, gain_skill_proficiency, resolve_damage
from ..models.combat import (
    DamageType,
    PassiveHealEffect,
    Skill,
    SkillCategory,
    SpiritualAffinity,
    Stats,
    WeaponType,
    resistance_reduction_fraction,
    skill_grade_number,
)
from ..models.players import (
    PlayerProgress,
    PronounSet,
    active_weapon_types,
    equipped_items_for_player,
)
from ..models.progression import (
    CultivationPath,
    CultivationStage,
    Race,
    SpecialTrait,
)
from ..models.world import Enemy, Item
from .base import HeavenCog
from ..utils import format_number
from ..views import CombatDecision, CombatDecisionView, LootDetailsView


class CombatSetupError(Exception):
    """Raised when an encounter cannot be started due to party state."""


ANSI_RESET = "\x1b[0m"
DEFAULT_ANSI_COLOUR = "37"
ANSI_ITALIC = "\x1b[3m"
ANSI_DARK_GREY = "\x1b[90m"
DESCRIPTION_STYLE = f"{ANSI_ITALIC}{ANSI_DARK_GREY}"

ANSI_COLOUR_CODES: Dict[str, str] = {
    "red": "31",
    "blue": "34",
    "teal": "36",
    "cyan": "36",
    "yellow": "33",
    "gray": "90",
    "grey": "90",
    "white": "97",
    "purple": "35",
    "magenta": "35",
    "green": "32",
    "orange": "38;5;208",
    "amber": "38;5;214",
    "violet": "38;5;177",
    "black": "30",
}

AFFINITY_COLOUR_NAMES: Dict[SpiritualAffinity, str] = {
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
    SpiritualAffinity.SAMSARA: "magenta",
    SpiritualAffinity.SPACE: "purple",
    SpiritualAffinity.TIME: "orange",
    SpiritualAffinity.GRAVITY: "blue",
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

POOL_COLOUR_NAMES: Dict[str, str] = {"hp": "green", "soul": "purple"}

POOL_LABELS: Dict[str, str] = {"hp": "HP", "soul": "SpH"}


def style_description(text: str) -> str:
    if not text:
        return text
    styled = text.replace(ANSI_RESET, f"{ANSI_RESET}{DESCRIPTION_STYLE}")
    return f"{DESCRIPTION_STYLE}{styled}{ANSI_RESET}"

SKILL_PATTERNS: list[str] = [
    "{attacker} channels {skill}, {weapon_motion}, summoning {affinity_image} to {affinity_effect} toward {target}.",
    "{attacker}'s {skill} surges as {subject} {weapon_motion}, letting {affinity_image} {affinity_effect} around {target}.",
    "{attacker} weaves {skill} while {weapon_motion}, driving {affinity_image} to {affinity_effect} against {target}.",
    "{attacker} unleashes {skill}; {weapon_motion} sends {affinity_image} to {affinity_effect} toward {target}.",
    "{attacker} melds {skill} with {weapon_motion}, forcing {affinity_image} to {affinity_effect} upon {target}.",
    "{attacker}'s aura roars as {skill} guides {weapon_motion}, causing {affinity_image} to {affinity_effect} across {target}'s guard.",
    "{attacker} threads {skill} through {weapon_motion}, urging {affinity_image} to {affinity_effect} toward {target}.",
    "{attacker} directs {skill} with {weapon_motion}, hurling {affinity_image} to {affinity_effect} around {target}.",
    "{attacker} storms forward with {skill} as {subject} {weapon_motion}, sending {affinity_image} to {affinity_effect} toward {target}.",
    "{attacker} invokes {skill}; {weapon_motion} births {affinity_image} that {affinity_effect} around {target}.",
]

BASIC_PATTERNS: list[str] = [
    "{attacker} {weapon_motion}, conjuring {affinity_image} to {affinity_effect} toward {target}.",
    "{attacker} presses the assault as {subject} {weapon_motion}, urging {affinity_image} to {affinity_effect} around {target}.",
    "{attacker} flows with {weapon_motion}, guiding {affinity_image} to {affinity_effect} against {target}.",
    "{attacker} advances with {weapon_motion}, sending {affinity_image} to {affinity_effect} toward {target}.",
    "{attacker} anchors {weapon_motion} in firm stance, causing {affinity_image} to {affinity_effect} upon {target}.",
    "{attacker} whips up qi as {subject} {weapon_motion}, driving {affinity_image} to {affinity_effect} around {target}.",
    "{attacker} releases a sharp breath and {weapon_motion}, letting {affinity_image} {affinity_effect} toward {target}.",
    "{attacker} pivots on the heel while {weapon_motion}, making {affinity_image} {affinity_effect} against {target}.",
    "{attacker} channels intent through {weapon_motion}, propelling {affinity_image} to {affinity_effect} toward {target}.",
    "{attacker} focuses qi along {weapon_motion}, urging {affinity_image} to {affinity_effect} around {target}.",
]

WEAPON_MOTIONS: Dict[WeaponType, Dict[str, list[str]]] = {
    WeaponType.BARE_HAND: {
        "skill": [
            "wreathing {possessive} fists in auric qi and shattering the air",
            "layering palm shadows into a mountain-crushing seal",
            "stepping through eight trigrams with comet fists",
            "compressing qi knuckles into a roaring dragon head",
            "spinning waist and shoulders to forge a tidal palm",
            "hammering void-cracking punches in a river of blows",
            "launching a cyclone of elbow strikes that fold space",
            "channeling tidal breath into a collapsing fist star",
            "twisting spine and tendons to erupt a thunderous palm",
            "driving knee and fist together in a heaven-rending smash",
        ],
        "basic": [
            "rolling shoulders and unleashing a crashing iron-mountain fist",
            "driving a shockwave palm that quakes the arena floor",
            "darting in with spiral footwork and a meteor punch",
            "uncoiling a serpent strike that snaps toward {target}'s core",
            "dropping low before exploding upward with a comet uppercut",
            "knitting fingers into a spear-hand that pierces the void",
            "circling {target} with sweeping kicks that churn dust devils",
            "pressing forward with a river of hammering palm strikes",
            "snapping a lightning jab that leaves afterimages behind",
            "slamming a shoulder check that ripples qi in waves",
        ],
    },
    WeaponType.SWORD: {
        "skill": [
            "tracing a seven-petal lotus arc with {possessive} blade",
            "drawing cloud-splitting swordlight in layered petals",
            "carving a drifting starlight slash across the void",
            "unleashing a dragon-tooth thrust that splits the horizon",
            "spinning a mirrored sword wheel beneath {possessive} feet",
            "summoning a rain of sword qi threads from the tip",
            "stepping through shadows with a void-splitting cut",
            "igniting the blade spine in shimmering sword intent",
            "painting the air with a crescent of luminous steel",
            "folding swordlight into a roaring river of edge",
        ],
        "basic": [
            "sweeping a quick crescent slash that hums with qi",
            "thrusting the blade in a spiral that drills the air",
            "pivoting to deliver a waist-high sword sweep",
            "dropping low and drawing an upward arc of steel",
            "snapping a backhand cut that whistles past",
            "raising the blade overhead before cleaving straight down",
            "gliding forward with a piercing sword thrust",
            "fanning swordlight in a fluttering lotus petal",
            "reversing the grip to carve a shadowed slash",
            "unfurling a sword-drawing strike that flashes like lightning",
        ],
    },
    WeaponType.SPEAR: {
        "skill": [
            "whipping the spear into a dragon-spiral vortex",
            "extending the tip with qi to pierce distant clouds",
            "vaulting skyward and driving a comet thrust downward",
            "spinning the haft into a storm of mirrored points",
            "slamming the butt to ripple a tidal shockwave forward",
            "casting the spear into a nine-segment serpent lash",
            "drawing runic circles before lunging in a blurring streak",
            "hooking the shaft to upbase the heavens themselves",
            "whistling a gale along the spear as it whirls in orbit",
            "sweeping spear shadows that knit into a rotating fortress",
        ],
        "basic": [
            "driving a straight thrust that whistles like falling rain",
            "sweeping the haft low to scythe at {target}'s stance",
            "twirling the spear before lashing out in a flurry",
            "yanking the butt end upward into a rib-cracking blow",
            "spinning once to unleash a crescent spear sweep",
            "pressing forward with a barrage of piercing jabs",
            "hooking the spear to tug {target} off balance",
            "stamping the haft to send vibrations along the ground",
            "coiling the spear around {target} like a constricting dragon",
            "lunging with a spear tip that gleams like cold starlight",
        ],
    },
    WeaponType.BOW: {
        "skill": [
            "drawing the bow into a full moon that hums with qi",
            "loosing three arrows that weave a stellar net",
            "sending a comet arrow that fractures space in its wake",
            "kneeling low to arc a meteor volley across the sky",
            "snapping the string to release a thunderous rain of shafts",
            "guiding arrow light into a soaring phoenix plume",
            "letting a spectral arrow split into a swarm mid-flight",
            "rolling the bow to unfurl a spiral of luminous bolts",
            "calling a dragon-shaped shaft that howls through the air",
            "firing a mirrored arrow that warps the heavens",
        ],
        "basic": [
            "nocking an arrow and releasing a rapid snap shot",
            "drawing to the cheek before sending a piercing shaft",
            "letting fly a rain of arrows in quick succession",
            "sidestepping and loosing a reflexive shot",
            "sliding back while firing a suppressing volley",
            "arching an arrow high so it dives like a hawk",
            "spinning the bow to launch twin arrows at once",
            "grounding the bow and firing a close-range burst",
            "tucking the string and delivering a whip-fast arrow",
            "aiming calmly before loosing a heart-seeking shot",
        ],
    },
    WeaponType.INSTRUMENT: {
        "skill": [
            "plucking thunderous chords that braid into blades",
            "sweeping zither strings to forge a crystalline tempest",
            "hammering drumbeats that ripple like falling mountains",
            "drawing a flute note that becomes a dragon's roar",
            "striking a bell tone that fractures the surrounding void",
            "twisting melody into a shimmering cage of sound",
            "rolling a guqin glissando that coalesces into swords",
            "snapping fan bells to scatter razor harmonics",
            "chanting a mantra that condenses into sonic spears",
            "flicking pipa strings so notes erupt as blazing meteors",
        ],
        "basic": [
            "plucking a sharp note that lashes outward",
            "striking a steady rhythm that batters {target}",
            "drawing a haunting whistle that rattles the soul",
            "tapping the instrument body to unleash sonic ripples",
            "rolling scales that condense into cutting vibrations",
            "sweeping harmonics that buffet {target}'s stance",
            "sliding tones that coil into serpents of sound",
            "pounding a cadence that thunders like war drums",
            "flicking strings to release piercing resonance",
            "raising the tempo until notes rain like arrows",
        ],
    },
    WeaponType.BRUSH: {
        "skill": [
            "sweeping calligraphy that blooms into sword talismans",
            "painting a dragon sigil whose strokes ignite midair",
            "flicking ink meteors that burst into runic shackles",
            "circling the brush to weave a grand sealing array",
            "splattering constellation dots that erupt like stars",
            "drawing a heavenly decree that descends as radiant chains",
            "inking a vortex glyph that devours surrounding qi",
            "writing a prayer stroke that manifests as divine light",
            "sketching mountains that surge forward as titanic phantoms",
            "layering characters until a pagoda of light collapses",
        ],
        "basic": [
            "snapping the brush to fling shards of ink",
            "dragging a bold stroke that slams into {target}",
            "twirling the brush so bristles carve glowing arcs",
            "painting a swift character that detonates on contact",
            "scattering droplets that sting like iron sand",
            "drawing a looping curve that trips {target}'s footing",
            "stabbing the brush tip forward like a dagger",
            "flourishing the brush to whip up a gust of ink qi",
            "sweeping wide to create a shimmering curtain",
            "inscribing a short mantra that pulses outward",
        ],
    },
    WeaponType.WHIP: {
        "skill": [
            "coiling the whip into a dragon that snaps apart",
            "cracking the lash to open thunder along its length",
            "looping the whip overhead to rain spectral strikes",
            "splitting the whip into phantom strands mid-flight",
            "anchoring the base before launching a scything wave",
            "whipping up a hurricane spiral that cages {target}",
            "snaring the air with a serpent lash that explodes",
            "lashing the ground to raise shards of force",
            "twining the whip around qi light to form a blazing coil",
            "snapping twin echoes that converge like meteors",
        ],
        "basic": [
            "cracking the whip toward {target}'s flank",
            "snapping a backhand lash that whistles sharply",
            "looping the whip low to sweep {target}'s legs",
            "jerking the handle to send a stinging strike",
            "coiling the whip and hurling it like a spear",
            "lashing overhead before slamming the whip down",
            "striking twice in quick succession with flickering snaps",
            "snaring an opening with a swift tightening coil",
            "spiraling the whip to create a spinning barrier",
            "flicking the tip to sting like a venomous serpent",
        ],
    },
    WeaponType.FAN: {
        "skill": [
            "unfolding the fan into a storm of petal blades",
            "painting mirrored crescents that chase one another",
            "twirling the fan to summon a cyclone of jade feathers",
            "clapping the ribs shut to fire razor gusts",
            "whisking the fan to scatter shimmering talismans",
            "pirouetting as the fan trails comet tails of qi",
            "fanning embers into a blazing phoenix plume",
            "drawing sigils in the air that burst into tempests",
            "raising a gale that condenses into swordlike gusts",
            "slicing downward so wind shears crack like thunder",
        ],
        "basic": [
            "snapping the fan open to launch a keen breeze",
            "flicking the edge to slap aside defenses",
            "waving the fan to buffet {target} with cutting wind",
            "stepping in close and jabbing with the closed ribs",
            "spinning once to scatter biting petals",
            "drawing a figure-eight gust that confounds footing",
            "lifting the fan to deflect before countering",
            "dancing sideways while lashing out with a breeze",
            "dropping the fan low then whipping it upward",
            "rolling the fan between fingers to send slicing currents",
        ],
    },
}

AFFINITY_IMAGERY: Dict[SpiritualAffinity | None, Dict[str, list[str]]] = {
    None: {
        "manifestations": [
            "untamed qi currents",
            "a tide of raw astral light",
            "uncoloured sword intent",
            "a storm of formless force",
            "veiled primordial vapours",
        ],
        "effects": [
            "coil in relentless waves",
            "pulse with unbridled momentum",
            "crash like thunder across the arena",
            "surge in layered crescendos",
            "rumble like distant dragons",
            "spiral outward as rippling halos",
            "cascade through {target}'s guard",
            "drum a rhythm of inevitability",
            "thunder in wild, unfettered beats",
            "shudder the air with primal vigor",
        ],
    },
    SpiritualAffinity.FIRE: {
        "manifestations": [
            "a phoenix-wing inferno",
            "vermillion dragonfire spirals",
            "a blazing lotus bloom",
            "an emperor's scarlet sun",
            "crackling ember storms",
        ],
        "effects": [
            "scorch the battlefield into wavering mirages",
            "pour incandescent brands across the sky",
            "erupts in rivers of molten flame",
            "flare like sunrise over shattered mountains",
            "trail blazing feathers that sear the void",
            "detonate in cascades of meteor sparks",
            "roar with furnace-breath heat",
            "lick along {target}'s defenses like wildfire",
            "carve molten sigils across the air",
            "ignite the winds in blazing coronas",
        ],
    },
    SpiritualAffinity.WATER: {
        "manifestations": [
            "tides of azure moonlight",
            "a dragon-whirlpool surge",
            "glacial mirror waves",
            "serpentine river spirits",
            "rain-laden storm ribbons",
        ],
        "effects": [
            "cascade in crushing currents",
            "drown the arena beneath tidal force",
            "spiral in mirrored torrents",
            "flood the air with jade droplets",
            "hammer like monsoon drums",
            "swirl in moon-touched ripples",
            "wash away footholds with surging foam",
            "twine around {target} like leviathan coils",
            "erupts into geysers that shatter stone",
            "rain down in relentless watery spears",
        ],
    },
    SpiritualAffinity.WIND: {
        "manifestations": [
            "jade cyclone blossoms",
            "sky-splitting gale blades",
            "tempest phoenix plumes",
            "whistling storm wyverns",
            "dancing typhoon ribbons",
        ],
        "effects": [
            "howl with heaven-rending force",
            "spiral in needle-sharp gusts",
            "shear the clouds into ribbons",
            "spin around {target} like a storm cage",
            "flay the air in screaming arcs",
            "ripple the void with azure winds",
            "scatter debris in tempestuous bursts",
            "slice through silence like a gale blade",
            "roar past like stampeding hurricanes",
            "weave whirling vortices that devour breath",
        ],
    },
    SpiritualAffinity.EARTH: {
        "manifestations": [
            "mountain-crushing qi slabs",
            "golden basalt bulwarks",
            "terracotta guardian fists",
            "crystal-laced boulders",
            "geomantic pillar surges",
        ],
        "effects": [
            "rumble like awakening mountains",
            "slam down with tectonic finality",
            "send fault lines racing underfoot",
            "grind {target}'s footing into dust",
            "crown the arena with rising monoliths",
            "crash outward as landslide avalanches",
            "pound like ancestral drums",
            "lock the air in immovable weight",
            "erupt in showers of ironstone shards",
            "anchor every blow with titanic gravity",
        ],
    },
    SpiritualAffinity.METAL: {
        "manifestations": [
            "celestial sword rain",
            "mirror-bright spear halos",
            "orichalcum wing blades",
            "resonant bell crescents",
            "platinum saber arcs",
        ],
        "effects": [
            "sing with razor-edged harmony",
            "shred the air in ringing crescendos",
            "cleave through defenses like molten steel",
            "scatter sparks that etch sword marks",
            "hammer the void with brazen peals",
            "spiral like forged comets",
            "slice past in glittering torrents",
            "resound as temple bells of war",
            "flash with argent lightning",
            "split the horizon with metallic brilliance",
        ],
    },
    SpiritualAffinity.ICE: {
        "manifestations": [
            "frost-lotus spears",
            "glacial dragon breaths",
            "snowflake mirror shards",
            "aurora-crowned icicles",
            "whiteout blizzard veils",
        ],
        "effects": [
            "freeze the air into crystalline rings",
            "spiral in diamond-hard gusts",
            "encase {target} in winter's decree",
            "fall as razor-edged snow",
            "trail hoarfrost sigils behind",
            "sting with polar needles",
            "hiss like ten thousand ice serpents",
            "quench the battlefield in breathless cold",
            "burst outward as mirrored frost petals",
            "slow time with glacial inevitability",
        ],
    },
    SpiritualAffinity.LIGHTNING: {
        "manifestations": [
            "thunder-dragon arcs",
            "azure thunderpeal lances",
            "storm crow talons",
            "celestial lightning wheels",
            "sparking constellation chains",
        ],
        "effects": [
            "crash down with deafening fury",
            "fork through the void in blinding webs",
            "detonate around {target} like thunder blossoms",
            "race along the ground as silver serpents",
            "ignite the air in sizzling halos",
            "strobe in relentless cadence",
            "brand afterimages into the horizon",
            "rumble with heavenly judgment",
            "lash in crackling barrages",
            "sear the senses with stormfire brilliance",
        ],
    },
    SpiritualAffinity.LIGHT: {
        "manifestations": [
            "radiant sun halos",
            "dawn-feathered lances",
            "seraphic prism flares",
            "aurora silk streams",
            "celestial candle flames",
        ],
        "effects": [
            "wash the battlefield in dawnlight",
            "cascade as sanctified beams",
            "purge shadows with piercing brilliance",
            "crown {target} in luminous chains",
            "shimmer like temple bells made visible",
            "dance in mirrored halos",
            "burn away doubt with holy fire",
            "sparkle like starlit rain",
            "flare in banners of resplendent gold",
            "compose hymns of light around the clash",
        ],
    },
    SpiritualAffinity.DARKNESS: {
        "manifestations": [
            "midnight demon banners",
            "shadow-snake torrents",
            "void lotus blossoms",
            "eclipsed star curtains",
            "abyssal crow wings",
        ],
        "effects": [
            "swallow the light in hungry spirals",
            "coil like vipers of ink",
            "drain warmth from the battlefield",
            "drag echoes of night across {target}",
            "flutter in silent, smothering veils",
            "erupt as obsidian shards",
            "wrap opponents in moonless shackles",
            "pulse with sinister heartbeats",
            "gnaw at reality's edge",
            "fall like a curtain of devouring dusk",
        ],
    },
    SpiritualAffinity.LIFE: {
        "manifestations": [
            "emerald spring vines",
            "blooming lotus spirits",
            "jade starlight pollen",
            "verdant crane wings",
            "sun-dappled leaf glyphs",
        ],
        "effects": [
            "spiral in rejuvenating torrents",
            "burst into fields of blossoming qi",
            "wrap {target} in flourishing growth",
            "rain shimmering motes that pulse with vitality",
            "sing with choruses of living light",
            "wash the arena in verdant tides",
            "spark new buds that explode outward",
            "twine like vines reclaiming ruins",
            "surge with heartbeat rhythms",
            "spill over as waterfalls of life qi",
        ],
    },
    SpiritualAffinity.DEATH: {
        "manifestations": [
            "ashen reaper mists",
            "soul-harvesting scythes",
            "ebon lantern flames",
            "funereal wraith talons",
            "grave-cold lotus petals",
        ],
        "effects": [
            "sap warmth with every sweep",
            "spiral as bone-chilling drafts",
            "drag {target} toward the Yellow Springs",
            "echo with knells of mourning",
            "shred vitality into drifting motes",
            "smother the arena in sepulchral frost",
            "wreathe everything in corpse-lantern glow",
            "gnaw at lifelines like hungry ghosts",
            "crawl as skeletal serpents",
            "snuff courage with funereal quiet",
        ],
    },
    SpiritualAffinity.SAMSARA: {
        "manifestations": [
            "samsaric phoenix mandalas",
            "twin-spiral soul flames",
            "lotus wheels of dusk and dawn",
            "cycle-wreathed spirit feathers",
            "life-death helix auroras",
        ],
        "effects": [
            "pulse in alternating waves of vigor and hush",
            "reweave shattered qi before rupturing it anew",
            "encircle {target} in cycles of renewal and release",
            "scatter phoenix ash that rekindles mid-flight",
            "turn the ground into a wheel of creation and decay",
            "thrum like heartbeats echoing across lifetimes",
            "wash over {target} as tides of memory and oblivion",
            "spiral upward in interlaced bands of jade and obsidian",
            "fold mortal essence into samsaric sparks",
            "cascade in reincarnating plumes that bloom and fade",
        ],
    },
    SpiritualAffinity.SPACE: {
        "manifestations": [
            "void-splitting crescents",
            "starlight warp threads",
            "eventide mirrors",
            "astral gateway sigils",
            "ravenous horizon arcs",
        ],
        "effects": [
            "bend distance in rippling folds",
            "shear reality like silk",
            "compress trajectories into impossible angles",
            "twist {target}'s footing through mirrored space",
            "scatter fragments of shattered constellations",
            "open gashes in the fabric of the arena",
            "slide silently through overlapping realms",
            "snap back with elastic void force",
            "bloom as gateways that collapse inward",
            "rewrite paths in dizzying spirals",
        ],
    },
    SpiritualAffinity.TIME: {
        "manifestations": [
            "chronal sand tides",
            "hourglass eclipse rings",
            "temporal lotus clocks",
            "silver pendulum arcs",
            "epochal ripple threads",
        ],
        "effects": [
            "slow breaths into elongated beats",
            "accelerate like racing seasons",
            "rewind {target}'s momentum mid-stride",
            "fracture moments into mirrored afterimages",
            "hum with ticking inevitability",
            "wash outward in ageless waves",
            "compress instants into cutting edges",
            "surge as timelines converging",
            "shiver in overlapping temporal echoes",
            "stretch shadows into strands of time",
        ],
    },
    SpiritualAffinity.GRAVITY: {
        "manifestations": [
            "black-iron weight sigils",
            "singularity pearl orbs",
            "celestial anchor chains",
            "collapsing star halos",
            "mountain yoke glyphs",
        ],
        "effects": [
            "crush the air with invisible pressure",
            "drag {target} earthward in heavy pulses",
            "warp trajectories into relentless arcs",
            "pin shadows to the ground like stakes",
            "pulse as tidal wells of force",
            "drag constellations down in blazing streaks",
            "crash like falling meteorites",
            "fold space into dense nodes",
            "thunder with the weight of ten thousand mountains",
            "stack gravity waves into hammer blows",
        ],
    },
    SpiritualAffinity.POISON: {
        "manifestations": [
            "violet miasma serpents",
            "jade venom droplets",
            "toxin-laced lotus threads",
            "smoldering plague butterflies",
            "emerald fume banners",
        ],
        "effects": [
            "coil in suffocating swathes",
            "eat away defenses with corrosive whispers",
            "stain the air with virulent haze",
            "seep along {target}'s guard like creeping ivy",
            "pulse with sickly phosphorescence",
            "scatter droplets that sizzle on contact",
            "gather in fumes that choke breath",
            "slither as venomous specters",
            "drip like the fangs of specter serpents",
            "spiral into venom storms",
        ],
    },
    SpiritualAffinity.MUD: {
        "manifestations": [
            "earth-water slurry dragons",
            "bog mist tendrils",
            "peatstone tidal fists",
            "murky lotus eddies",
            "swamp spirit coils",
        ],
        "effects": [
            "suck at footing with grasping suction",
            "splatter in heavy, clinging waves",
            "drag {target} into mirebound quagmires",
            "ooze forward with relentless pull",
            "erupts in sludge-laden geysers",
            "muffle sounds in dampening curtains",
            "spray gritty droplets that weigh down limbs",
            "flow like quicksand hungry for purchase",
            "swallow momentum in thick torrents",
            "smear the arena in viscous tides",
        ],
    },
    SpiritualAffinity.TEMPERATURE: {
        "manifestations": [
            "frostfire cyclone crowns",
            "dual-colored flame snowflakes",
            "thermal yin-yang blossoms",
            "boiling blizzard halos",
            "glimmering heat-haze shards",
        ],
        "effects": [
            "lash between scalding and freezing breaths",
            "crackle with alternating extremes",
            "shatter {target}'s balance through thermal shocks",
            "weave hot and cold into whiplashing bands",
            "steam and freeze simultaneously",
            "flash-boil the air into glittering mist",
            "layer frost over burning embers",
            "pulse like a furnace wrapped in winter",
            "twist the senses with sudden climate shifts",
            "blaze and chill in synchronized waves",
        ],
    },
    SpiritualAffinity.LAVA: {
        "manifestations": [
            "magma wyrm torrents",
            "obsidian-cored fire rivers",
            "molten crown geysers",
            "crimson basalt avalanches",
            "seething volcanic lances",
        ],
        "effects": [
            "pour in incandescent streams",
            "splatter as blazing stone shards",
            "carve slag trenches beneath {target}",
            "erupt in volcanic thunder",
            "ooze forward with blistering inevitability",
            "ignite the ground into molten pools",
            "sear the air with sulfurous breath",
            "burst in molten lotuses",
            "crackle with magma-sheathed fury",
            "rain flaming rock fragments",
        ],
    },
    SpiritualAffinity.TWILIGHT: {
        "manifestations": [
            "duskfire veil ribbons",
            "sunset mirage tides",
            "amber-mauve comet trails",
            "half-light crane wings",
            "evening star lanterns",
        ],
        "effects": [
            "blur the line between light and shadow",
            "wash over {target} in dusky serenity",
            "pulse with horizon-born glow",
            "drift like falling twilight petals",
            "twine daybreak and nightfall in tandem",
            "soften reality into dreamlike hues",
            "glimmer as distant evening constellations",
            "roll in gentle yet unyielding waves",
            "cloak movements in sunset mirages",
            "echo with the hush of approaching night",
        ],
    },
    SpiritualAffinity.PERMAFROST: {
        "manifestations": [
            "glacier-crusted earthen spires",
            "rimebound tectonic plates",
            "snow-laden monolith shards",
            "aurora-lit permafrost tusks",
            "ice-veined stone ramparts",
        ],
        "effects": [
            "lock the ground beneath {target} in glacial grip",
            "radiate tundra chill that stiffens sinew",
            "erupt in frozen quakes that crawl outward",
            "encase assaults in hoarfrost armour",
            "drag polar winds that bite to the bone",
            "flash-freeze debris into jagged shrapnel",
            "anchor limbs with frostbitten chains",
            "spill blizzard-dust that blinds sight",
            "thrum with ancient glacier pulses",
            "seal wounds in biting winter crust",
        ],
    },
    SpiritualAffinity.DUSTSTORM: {
        "manifestations": [
            "saffron cyclone curtains",
            "sandwraith spiral funnels",
            "haze-wreathed dune serpents",
            "sun-baked grit halos",
            "storm-churned loess clouds",
        ],
        "effects": [
            "flay exposed skin with rasping sand",
            "scour {target}'s guard in grinding sheets",
            "bury the arena beneath rolling dunes",
            "howl with parching desert winds",
            "erase silhouettes within swirling grit",
            "hammer senses with sunburnt gusts",
            "carve mirage trails that mislead eyes",
            "pelt the air with needlelike grains",
            "swallow sound in muffled squalls",
            "spin dust devils that wrench footing",
        ],
    },
    SpiritualAffinity.PLASMA: {
        "manifestations": [
            "ionized aurora lashes",
            "starflare spear filaments",
            "sun-corona vortex discs",
            "violet arcstorm halos",
            "superheated comet shards",
        ],
        "effects": [
            "sear the sky with radiant arcs",
            "carve ozone-scented scars across the arena",
            "detonate in magnetized shockwaves",
            "shear armour with liquefied light",
            "lash {target} in coronal whips",
            "ignite the air in ultraviolet bloom",
            "pulse like captive solar flares",
            "trace incandescent sigils mid-strike",
            "boil defenses in roaring photon floods",
            "surge forward on crackling plasma jets",
        ],
    },
    SpiritualAffinity.STEAM: {
        "manifestations": [
            "scalding vapor plumes",
            "mist-laden furnace vents",
            "billowing geyser crowns",
            "cloud-forged spiral ribbons",
            "ember-flecked fog banks",
        ],
        "effects": [
            "cloak the battlefield in searing haze",
            "hiss around {target} with blistering pressure",
            "flash into condensing needle rain",
            "soften steel beneath relentless heat",
            "roar upward as erupting columns",
            "swallow silhouettes in swirling vapor",
            "roll across the floor in boiling tides",
            "beat like piston bursts against defenses",
            "infuse each strike with humid fury",
            "linger as ghostly clouds that scald breath",
        ],
    },
    SpiritualAffinity.INFERNO: {
        "manifestations": [
            "cyclonic wildfire wings",
            "scarlet tornado flares",
            "emberstorm hurricane fans",
            "sunforged blaze banners",
            "pyroclastic gale claws",
        ],
        "effects": [
            "devour oxygen with roaring heat",
            "spiral skyward in incandescent tornadoes",
            "lash {target} with wildfire gusts",
            "reduce obstacles to drifting ash",
            "ignite the horizon in stormfire arcs",
            "hurl flame-laced wind scythes",
            "cascade embers in burning squalls",
            "whip battlefields into furnace maelstroms",
            "howl like dragons forged of cinder",
            "paint the air in streaks of molten wind",
        ],
    },
    SpiritualAffinity.FLASHFROST: {
        "manifestations": [
            "cobalt lightning sleet",
            "glimmering froststrike bolts",
            "auroral shard lances",
            "crackling rime arcs",
            "stormbound ice prisms",
        ],
        "effects": [
            "snap-freeze air with thunderclap chill",
            "shatter around {target} in crystalline sparks",
            "etch frigid sigils that paralyze motion",
            "drum the ground with brittle static",
            "unleash hail forged from frozen lightning",
            "flash-burn heat into blinding cold",
            "draw jagged conduits through the storm",
            "web armor in numbing filaments",
            "sing with electrified winter wind",
            "hurl bolts that explode in frost shards",
        ],
    },
    SpiritualAffinity.FROSTFLOW: {
        "manifestations": [
            "glacial tide banners",
            "icewater ribbon torrents",
            "frost-laden river spirits",
            "snowmelt cataract coils",
            "crystal current arcs",
        ],
        "effects": [
            "pour in subzero waves that numb limbs",
            "encircle {target} in chilling eddies",
            "freeze surfaces under sweeping flows",
            "fill the air with drifting ice motes",
            "surge like winter rivers unleashed",
            "slow pulses to a glacial rhythm",
            "carve channels that refreeze instantly",
            "drag foes with relentless polar tides",
            "cast mirrors of shimmering frostwater",
            "crash like avalanches dissolved to spray",
        ],
    },
    SpiritualAffinity.BLIZZARD: {
        "manifestations": [
            "whiteout tempest veils",
            "howling snowstorm gyres",
            "diamond dust hurricanes",
            "frostwraith cyclone plumes",
            "winter gale pennants",
        ],
        "effects": [
            "erase the horizon in sweeping white",
            "bury {target} beneath layered drifts",
            "scream with icy hurricane force",
            "pepper defenses with needle snow",
            "spin frozen crescents through the air",
            "smother flames under polar squalls",
            "lash exposed skin with stinging sleet",
            "cloak movement behind whirling flurries",
            "shake structures with avalanche bursts",
            "leave afterimages of roaming snow phantoms",
        ],
    },
    SpiritualAffinity.TEMPEST: {
        "manifestations": [
            "stormcrown vortex wheels",
            "thunderhead spiral mantles",
            "lightning-wing squalls",
            "gale-lashed storm banners",
            "cyclonic arc halos",
        ],
        "effects": [
            "call down spears of screaming thunder",
            "shear {target}'s stance with hurricane arcs",
            "flood the arena with jagged windstorms",
            "flash with storm-born radiance",
            "slam in waves of concussive pressure",
            "rip banners free with electric gusts",
            "surround foes in stormwall prisons",
            "pulse like a sky-splitting maelstrom",
            "crack the heavens with ceaseless rumble",
            "drive rainless thunder squalls across stone",
        ],
    },
    SpiritualAffinity.MIST: {
        "manifestations": [
            "moonlit vapor veils",
            "silver wisp currents",
            "shrouded drizzle ribbons",
            "lotus-scented fog blooms",
            "twilight dew halos",
        ],
        "effects": [
            "soften edges with clinging haze",
            "glide around {target} in shrouded coils",
            "absorb flame into damp curtains",
            "scatter light into prismatic glimmer",
            "seep through armor in whispering tendrils",
            "muffle movement with gentle rain",
            "trail beads that blossom into veils",
            "saturate the air in cool humidity",
            "veil footfalls beneath whispering spray",
            "ebb and flow like breathing clouds",
        ],
    },
    SpiritualAffinity.ENTROPY: {
        "manifestations": [
            "starlit decay motes",
            "fracture spiral glyphs",
            "dissolution mist banners",
            "cinder-black ruin threads",
            "ashen entropy vortices",
        ],
        "effects": [
            "unravel matter into drifting dust",
            "gnaw at order with silent hunger",
            "dissipate {target}'s momentum into nothing",
            "erode the air with whispering decay",
            "splinter constructs into fading motes",
            "spread voidburn cracks through space",
            "flicker like dying stars",
            "rot courage with inevitable decline",
            "collapse patterns into chaos",
            "leech vibrancy from everything they touch",
        ],
    },
}

MIN_WEAPON_MOTION_VARIATIONS = 10
MIN_AFFINITY_VARIATIONS = 50


def _validate_narration_tables() -> None:
    """Ensure narration tables retain the requested breadth of variations."""

    weapon_shortfalls: list[str] = []
    for weapon, pools in WEAPON_MOTIONS.items():
        for pool_name in ("skill", "basic"):
            entries = list(pools.get(pool_name) or [])
            if len(entries) < MIN_WEAPON_MOTION_VARIATIONS:
                weapon_shortfalls.append(
                    f"{weapon.value} ({pool_name}) has {len(entries)} variations"
                )

    affinity_shortfalls: list[str] = []
    for affinity, imagery in AFFINITY_IMAGERY.items():
        manifestations = list(imagery.get("manifestations") or [])
        effects = list(imagery.get("effects") or [])
        combinations = len(manifestations) * len(effects)
        if combinations < MIN_AFFINITY_VARIATIONS:
            affinity_name = "default" if affinity is None else affinity.name
            affinity_shortfalls.append(
                f"{affinity_name} provides {combinations} combinations"
            )

    if weapon_shortfalls or affinity_shortfalls:
        issues = []
        if weapon_shortfalls:
            issues.append(
                "Weapon motion pools below requirement: " + ", ".join(weapon_shortfalls)
            )
        if affinity_shortfalls:
            issues.append(
                "Affinity imagery below requirement: " + ", ".join(affinity_shortfalls)
            )
        raise ValueError("; ".join(issues))


_validate_narration_tables()

PLAYER_NAME_COLOUR = "blue"
ENEMY_NAME_COLOUR = "red"
DEFEATED_NAME_COLOUR = "gray"
ROUND_LINE_COLOUR = "gray"
DUEL_OPPONENT_COLOUR = "purple"


@dataclass(slots=True)
class FighterState:
    identifier: str
    name: str
    is_player: bool
    stats: Stats
    hp: float
    max_hp: float
    soul_hp: float
    max_soul_hp: float
    agility: float
    skills: List[Skill]
    proficiency: Dict[str, int]
    resistances: Sequence[SpiritualAffinity]
    player: Optional[PlayerProgress]
    qi_stage: Optional[CultivationStage]
    body_stage: Optional[CultivationStage]
    soul_stage: Optional[CultivationStage]
    race: Optional[Race]
    traits: List[SpecialTrait]
    items: List[Item]
    weapon_types: Set[WeaponType]
    passive_heals: List[PassiveHealEffect]
    pronouns: PronounSet
    primary_affinity: Optional[SpiritualAffinity] = None
    decision_prompt_chance: float = 0.0
    escape_chance: float = 0.0
    display_colour: str | None = None

    def defeated(self) -> bool:
        return self.hp <= 0 or self.soul_hp <= 0


@dataclass(slots=True)
class CombatReport:
    player_victory: bool
    rounds: int
    log: List[str]
    surviving_players: List[FighterState]
    defeated_players: List[FighterState]
    players_escaped: bool = False
    players_surrendered: bool = False


class DuelChallengeView(discord.ui.View):
    def __init__(
        self,
        cog: "CombatCog",
        guild_id: int,
        challenger_id: int,
        opponent_id: int,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.cog = cog
        self.guild_id = guild_id
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.message: Optional[discord.Message] = None

    def disable_all(self) -> None:
        for child in self.children:
            child.disabled = True

    async def on_timeout(self) -> None:
        self.disable_all()
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="⚔️")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message(
                "Only the challenged cultivator can accept.", ephemeral=True
            )
            return
        await interaction.response.defer(thinking=True)
        await self.cog._start_duel(interaction, self.guild_id, self.challenger_id, self.opponent_id)
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="🛑")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # type: ignore[override]
        if interaction.user.id != self.opponent_id:
            await interaction.response.send_message(
                "Only the challenged cultivator can decline.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            (
                f"<@{self.opponent_id}> declines the duel from <@{self.challenger_id}>."
            ),
            ephemeral=True,
        )
        self.disable_all()
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass
        self.stop()


class CombatCog(HeavenCog):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.config: BotConfig = bot.config  # type: ignore[assignment]

    async def _fetch_player(self, guild_id: int, user_id: int) -> Optional[PlayerProgress]:
        data = await self.store.get_player(guild_id, user_id)
        if not data:
            return None
        player = PlayerProgress(**data)
        self.state.register_player(player)
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(user_id) if guild else None
        if member and member.display_name and member.display_name != player.name:
            player.name = member.display_name
            await self._save_player(guild_id, player)
        return player

    async def _save_player(self, guild_id: int, player: PlayerProgress) -> None:
        await self.store.upsert_player(guild_id, asdict(player))

    async def _prepare_party(
        self, guild_id: int, player: PlayerProgress
    ) -> Tuple[str, List[PlayerProgress]]:
        party = self.state.parties.get(player.party_id) if player.party_id else None
        if not party:
            candidates = [player]
            party_id = uuid.uuid4().hex
        else:
            if player.user_id not in party.member_ids:
                raise CombatSetupError("You are not part of that party.")
            candidates = []
            for member_id in party.member_ids:
                record = await self._fetch_player(guild_id, member_id)
                if record:
                    candidates.append(record)
            if not candidates:
                raise CombatSetupError("Your party has no registered members.")
            party_id = party.party_id

        for record in candidates:
            if record.in_combat:
                raise CombatSetupError(f"{record.name} is already engaged in combat.")
            if (record.current_hp is not None and record.current_hp <= 0) or (
                record.current_soul_hp is not None and record.current_soul_hp <= 0
            ):
                raise CombatSetupError(
                    f"{record.name} must recover at a safe zone before battling again."
                )
        return (party_id, candidates)

    def _party_mentions(
        self,
        guild: discord.Guild,
        party_members: Sequence[PlayerProgress],
        actor_id: int,
    ) -> List[discord.abc.Snowflake]:
        mentions: List[discord.abc.Snowflake] = []
        for member in party_members:
            if member.user_id == actor_id:
                continue
            member_obj = guild.get_member(member.user_id)
            if member_obj:
                mentions.append(member_obj)
        return mentions

    async def _set_combat_flag(
        self, guild_id: int, members: Sequence[PlayerProgress], active: bool
    ) -> None:
        for record in members:
            record.in_combat = active
        for record in members:
            await self._save_player(guild_id, record)

    def _player_context(
        self, player: PlayerProgress
    ) -> Tuple[
        CultivationStage,
        Optional[CultivationStage],
        Optional[CultivationStage],
        Optional[Race],
        List[SpecialTrait],
        List[Item],
    ]:
        qi_stage = self.state.get_stage(player.cultivation_stage, CultivationPath.QI)
        if qi_stage is None:
            raise CombatSetupError("Your cultivation stage is not configured.")
        body_stage: Optional[CultivationStage] = None
        if player.body_cultivation_stage:
            body_stage = self.state.get_stage(
                player.body_cultivation_stage, CultivationPath.BODY
            )
        soul_stage: Optional[CultivationStage] = None
        if player.soul_cultivation_stage:
            soul_stage = self.state.get_stage(
                player.soul_cultivation_stage, CultivationPath.SOUL
            )
        race = self.state.races.get(player.race_key) if player.race_key else None
        traits = [
            self.state.traits[key]
            for key in player.trait_keys
            if key in self.state.traits
        ]
        items = equipped_items_for_player(player, self.state.items)
        return qi_stage, body_stage, soul_stage, race, traits, items

    def _player_skills(self, player: PlayerProgress) -> List[Skill]:
        qi_stage = self.state.get_stage(player.cultivation_stage, CultivationPath.QI)
        if qi_stage and qi_stage.is_mortal:
            return []

        skills: List[Skill] = []
        for key in player.skill_proficiency:
            skill = self.state.skills.get(key)
            if skill and skill.category is SkillCategory.ACTIVE:
                skills.append(skill)
        return skills

    def _build_player_fighter(self, player: PlayerProgress) -> FighterState:
        qi_stage, body_stage, soul_stage, race, traits, items = self._player_context(player)
        stats = player.effective_stats(
            qi_stage, body_stage, soul_stage, race, traits, items
        )
        stats.add_in_place(self.passive_skill_bonus(player))
        max_hp = max(1.0, stats.health_points)
        max_soul_hp = max_hp
        hp = max_hp if player.current_hp is None else max(0.0, min(player.current_hp, max_hp))
        soul_hp = (
            max_soul_hp
            if player.current_soul_hp is None
            else max(0.0, min(player.current_soul_hp, max_soul_hp))
        )
        skills = self._player_skills(player)
        passive_heals = list(self.passive_skill_heals(player).values())
        base = player.combined_innate_soul(traits)
        resistances = list(base.affinities) if base else []
        primary_affinity = base.affinity if base else None
        return FighterState(
            identifier=str(player.user_id),
            name=player.name or f"Cultivator {player.user_id}",
            is_player=True,
            stats=stats,
            hp=hp,
            max_hp=max_hp,
            soul_hp=soul_hp,
            max_soul_hp=max_soul_hp,
            agility=stats.agility,
            skills=skills,
            proficiency=dict(player.skill_proficiency),
            resistances=resistances,
            player=player,
            qi_stage=qi_stage,
            body_stage=body_stage,
            soul_stage=soul_stage,
            race=race,
            traits=traits,
            items=items,
            weapon_types=active_weapon_types(player, self.state.items),
            passive_heals=passive_heals,
            pronouns=player.pronouns(),
            primary_affinity=primary_affinity,
            decision_prompt_chance=0.0,
            escape_chance=0.0,
            display_colour=PLAYER_NAME_COLOUR,
        )

    def _build_enemy_fighter(self, enemy_key: str, enemy: Enemy) -> FighterState:
        qi_stage: Optional[CultivationStage] = None
        body_stage: Optional[CultivationStage] = None
        soul_stage: Optional[CultivationStage] = None
        if enemy.cultivation_stage:
            qi_stage = self.state.get_stage(
                enemy.cultivation_stage, CultivationPath.QI
            )
        if enemy.body_cultivation_stage:
            body_stage = self.state.get_stage(
                enemy.body_cultivation_stage, CultivationPath.BODY
            )
        if enemy.soul_cultivation_stage:
            soul_stage = self.state.get_stage(
                enemy.soul_cultivation_stage, CultivationPath.SOUL
            )
        stats = enemy.base_stats_for_stages(qi_stage, body_stage, soul_stage)
        stats = stats.copy()
        active_stage_map = {
            CultivationPath.QI: qi_stage,
            CultivationPath.BODY: body_stage,
            CultivationPath.SOUL: soul_stage,
        }
        active_stage = active_stage_map.get(
            CultivationPath.from_value(enemy.active_path),
        )
        if active_stage:
            stats.add_in_place(active_stage.stat_bonuses)
        hp = max(1.0, stats.health_points)
        max_hp = hp
        soul_hp = max_hp
        active_skills: List[Skill] = []
        passive_heals: List[PassiveHealEffect] = []
        for key in enemy.skills:
            skill = self.state.skills.get(key)
            if not skill:
                continue
            if skill.category is SkillCategory.PASSIVE:
                effect = skill.passive_heal_effect()
                if effect:
                    passive_heals.append(effect)
                continue
            active_skills.append(skill)
        weapon_types = {skill.weapon for skill in active_skills if skill.weapon} or {
            WeaponType.BARE_HAND
        }
        race = self.state.races.get(enemy.race_key) if enemy.race_key else None
        return FighterState(
            identifier=enemy_key,
            name=enemy.name,
            is_player=False,
            stats=stats,
            hp=hp,
            max_hp=hp,
            soul_hp=soul_hp,
            max_soul_hp=soul_hp,
            agility=stats.agility,
            skills=active_skills,
            proficiency={},
            resistances=list(enemy.elemental_resistances),
            player=None,
            qi_stage=qi_stage,
            body_stage=body_stage,
            soul_stage=soul_stage,
            race=race,
            traits=[],
            items=[],
            weapon_types=weapon_types,
            passive_heals=passive_heals,
            pronouns=PronounSet.neutral(),
            primary_affinity=enemy.affinity,
            decision_prompt_chance=enemy.decision_prompt_chance,
            escape_chance=enemy.escape_chance,
            display_colour=ENEMY_NAME_COLOUR,
        )

    def _basic_attack(self, attacker: FighterState, defender: FighterState) -> Tuple[float, str]:
        base = attacker.stats.attacks
        variance = random.uniform(0.9, 1.1)
        damage = max(1.0, base * variance)
        if defender.is_player:
            mitigation = defender.stats.defense * 0.2
        else:
            mitigation = defender.stats.defense * 0.1
        damage = max(1.0, damage - mitigation)
        return damage, "hp"

    def _player_skill_attack(
        self, attacker: FighterState, defender: FighterState, skill: Skill
    ) -> Tuple[float, str]:
        assert attacker.player is not None
        qi_stage = attacker.qi_stage or self.state.get_stage(
            attacker.player.cultivation_stage, CultivationPath.QI
        )
        if qi_stage is None:
            raise CombatSetupError("Your cultivation stage is not configured.")
        body_stage = attacker.body_stage
        race = attacker.race
        traits = attacker.traits
        items = attacker.items
        outcome = resolve_damage(
            attacker.player,
            defender.hp,
            defender.soul_hp,
            skill,
            qi_stage,
            body_stage,
            attacker.soul_stage,
            race,
            traits,
            items,
            defender.resistances,
            attacker.weapon_types,
        )
        damage = max(1.0, outcome.rolled)
        return damage, outcome.pool

    def _enemy_skill_attack(
        self, attacker: FighterState, defender: FighterState, skill: Skill
    ) -> Tuple[float, str]:
        stats = attacker.stats
        base_stat = stats.attacks
        damage = base_stat * skill.damage_ratio
        attack_elements = skill.elements or (() if skill.element is None else (skill.element,))
        if attack_elements:
            reduction = resistance_reduction_fraction(attack_elements, defender.resistances)
            damage *= max(0.0, 1 - 0.25 * reduction)
        variance = random.uniform(0.85, 1.15)
        damage = max(1.0, damage * variance)
        if defender.is_player:
            mitigation = defender.stats.defense
            damage = max(1.0, damage - mitigation * 0.2)
        return damage, "hp"

    def _skill_weapon_permitted(self, fighter: FighterState, skill: Skill) -> bool:
        if not fighter.is_player:
            return True
        requirement = getattr(skill, "weapon", None)
        if requirement is None:
            return True
        if not isinstance(requirement, WeaponType):
            try:
                requirement = WeaponType.from_value(requirement)
            except ValueError:
                return True
        if requirement is WeaponType.BARE_HAND:
            return WeaponType.BARE_HAND in fighter.weapon_types
        return requirement in fighter.weapon_types

    def _attempt_skill(
        self, fighter: FighterState
    ) -> Optional[Skill]:
        triggered: List[Skill] = []
        for skill in fighter.skills:
            if not self._skill_weapon_permitted(fighter, skill):
                continue
            chance = max(0.0, min(1.0, skill.trigger_chance))
            if fighter.is_player:
                proficiency_cap = max(0, int(skill.proficiency_max))
                if proficiency_cap > 0:
                    prof_value = fighter.proficiency.get(skill.key, 0)
                    if prof_value >= proficiency_cap:
                        chance = min(1.0, chance * 2)
            roll = random.random()
            if roll > chance:
                continue
            triggered.append(skill)
        if not triggered:
            return None
        triggered.sort(key=lambda entry: entry.damage_ratio, reverse=True)
        return triggered[0]

    def _apply_damage(
        self,
        attacker: FighterState,
        defender: FighterState,
        skill: Optional[Skill],
    ) -> Tuple[float, str]:
        if skill is None:
            damage, pool = self._basic_attack(attacker, defender)
        else:
            if attacker.is_player:
                damage, pool = self._player_skill_attack(attacker, defender, skill)
                if attacker.player is not None:
                    gain_skill_proficiency(attacker.player, skill)
                    attacker.proficiency[skill.key] = attacker.player.skill_proficiency.get(
                        skill.key, attacker.proficiency.get(skill.key, 0)
                    )
            else:
                damage, pool = self._enemy_skill_attack(attacker, defender, skill)
        if pool == "soul":
            defender.soul_hp = max(0.0, defender.soul_hp - damage)
        else:
            defender.hp = max(0.0, defender.hp - damage)
        return damage, pool

    def _colour_text(self, text: str, colour: str | None = None, *, bold: bool = False) -> str:
        params: list[str] = []
        if bold:
            params.append("1")
        if colour:
            params.append(ANSI_COLOUR_CODES.get(colour, DEFAULT_ANSI_COLOUR))
        if not params:
            return text
        return f"\x1b[{';'.join(params)}m{text}{ANSI_RESET}"

    def _pool_label(self, pool: str) -> str:
        return POOL_LABELS.get(pool, pool.upper())

    def _format_combat_name(self, fighter: FighterState) -> str:
        colour = fighter.display_colour
        if fighter.defeated():
            colour = DEFEATED_NAME_COLOUR
        elif not colour:
            colour = PLAYER_NAME_COLOUR if fighter.is_player else ENEMY_NAME_COLOUR
        return self._colour_text(fighter.name, colour=colour, bold=True)

    def _resolve_weapon_type(
        self, fighter: FighterState, skill: Optional[Skill]
    ) -> WeaponType:
        if skill and skill.weapon:
            return skill.weapon
        if fighter.weapon_types:
            return sorted(fighter.weapon_types, key=lambda w: w.value)[0]
        return WeaponType.BARE_HAND

    def _split_mixed_skill_label(self, text: str) -> tuple[str, str]:
        if not text:
            return "", ""
        midpoint = len(text) // 2
        split_index = midpoint
        left_space = text.rfind(" ", 0, midpoint)
        right_space = text.find(" ", midpoint)
        if left_space != -1 or right_space != -1:
            left_distance = midpoint - left_space if left_space != -1 else None
            right_distance = right_space - midpoint if right_space != -1 else None
            if left_distance is not None and (
                right_distance is None or left_distance <= right_distance
            ):
                split_index = left_space + 1
            elif right_distance is not None:
                split_index = right_space + 1
        first, second = text[:split_index], text[split_index:]
        if not first:
            first, second = text[:1], text[1:]
        elif not second:
            first, second = text[:-1], text[-1:]
        return first, second

    def _format_skill_name(self, skill: Optional[Skill]) -> Optional[str]:
        if skill is None:
            return None
        name = skill.name
        elements = skill.elements or (
            (skill.element,) if skill.element else ()
        )
        element = elements[0] if elements else None
        if element is None:
            return self._colour_text(name, colour="white", bold=True)
        if len(elements) > 1:
            components = tuple(elements)
        elif element.is_mixed:
            components = element.components
        else:
            components = (element,)
        primary = AFFINITY_COLOUR_NAMES.get(components[0], "white")
        secondary_source = components[1] if len(components) > 1 else components[0]
        secondary = AFFINITY_COLOUR_NAMES.get(secondary_source, primary)
        if len(components) > 1:
            first, second = self._split_mixed_skill_label(name)
            coloured_parts: list[str] = []
            if first:
                coloured_parts.append(
                    self._colour_text(first, colour=primary, bold=True)
                )
            if second:
                coloured_parts.append(
                    self._colour_text(second, colour=secondary, bold=True)
                )
            return "".join(coloured_parts) or self._colour_text(name, colour=primary, bold=True)
        colour_name = AFFINITY_COLOUR_NAMES.get(element, "white")
        return self._colour_text(name, colour=colour_name, bold=True)

    def _format_damage_amount(self, damage: float, pool: str) -> str:
        amount = int(round(damage))
        pool_label = self._pool_label(pool)
        colour = POOL_COLOUR_NAMES.get(pool, "yellow")
        formatted = format_number(amount)
        return self._colour_text(f"{formatted} {pool_label}", colour=colour, bold=True)

    def _format_heal_amount(self, healed: float, pool: str) -> str:
        amount = int(round(healed))
        pool_label = self._pool_label(pool)
        colour = POOL_COLOUR_NAMES.get(pool, "green")
        formatted = format_number(amount)
        return self._colour_text(f"{formatted} {pool_label}", colour=colour, bold=True)

    def _format_heal_line(
        self, fighter: FighterState, amount: float, effect: PassiveHealEffect
    ) -> str:
        name = self._format_combat_name(fighter)
        skill = self.state.skills.get(effect.skill_key)
        skill_name = skill.name if skill else effect.skill_key.replace("_", " ").title()
        heal_text = self._format_heal_amount(amount, effect.pool)
        return f"{name} is revitalised by {skill_name}, restoring {heal_text}."

    def _fighter_label(self, fighter: FighterState) -> str:
        if fighter.defeated():
            return f"~~{fighter.name}~~"
        return f"**{fighter.name}**"

    def _make_bar(self, current: float, maximum: float, *, length: int = 10) -> str:
        if maximum <= 0:
            maximum = 1.0
        ratio = max(0.0, min(1.0, current / maximum))
        filled = int(round(ratio * length))
        if current > 0 and filled == 0:
            filled = 1
        filled = min(length, filled)
        return f"{'▰' * filled}{'▱' * (length - filled)}"

    def _make_health_bar(self, current: float, maximum: float, *, length: int = 36) -> str:
        if maximum <= 0:
            maximum = 1.0
        ratio = max(0.0, min(1.0, current / maximum))
        filled = int(round(ratio * length))
        if current > 0 and filled == 0:
            filled = 1
        filled = min(length, filled)
        healthy = "░" * filled
        missing = "░" * (length - filled)
        green_bar = self._colour_text(healthy, colour="green") if healthy else ""
        red_bar = self._colour_text(missing, colour="red") if missing else ""
        return f"{green_bar}{red_bar}"

    def _resource_line(
        self,
        icon: str | None,
        current: float,
        maximum: float,
        label: str,
        *,
        colour: str | None = None,
    ) -> str:
        value = (
            f"{format_number(int(round(current)))}/{format_number(int(round(maximum)))}"
        )
        if label in {"HP", "SpH"}:
            bar = self._make_health_bar(current, maximum)
        else:
            bar = self._make_bar(current, maximum)
        prefix = f"{icon} " if icon else ""
        return f"{prefix}{label} {bar} `{value}`"

    def _fighter_status_line(
        self, fighter: FighterState, *, show_soul_hp: bool
    ) -> str:
        if fighter.defeated():
            status_label = "[DEFEATED]"
        elif fighter.is_player:
            status_label = "[ALLY]"
        else:
            status_label = "[ENEMY]"
        lines = [
            f"{status_label} {self._fighter_label(fighter)}",
            f"  {self._resource_line('[HP]', fighter.hp, fighter.max_hp, 'HP')}",
        ]
        if show_soul_hp:
            lines.append(
                f"  {self._resource_line('[Soul]', fighter.soul_hp, fighter.max_soul_hp, 'SpH')}"
            )
        return "\n".join(lines)

    def _format_status_block(
        self,
        fighters: Sequence[FighterState],
        *,
        allow_enemy_soul: bool,
    ) -> str:
        lines: list[str] = []
        for fighter in fighters:
            if fighter.is_player:
                show_soul = bool(
                    fighter.player
                    and fighter.player.active_path == CultivationPath.SOUL.value
                )
            else:
                show_soul = allow_enemy_soul
            lines.append(self._fighter_status_line(fighter, show_soul_hp=show_soul))
        if not lines:
            return "None"
        return "\n\n".join(lines[:6])

    def _format_status_field(
        self,
        fighters: Sequence[FighterState],
        *,
        allow_enemy_soul: bool,
    ) -> str:
        block = self._format_status_block(
            fighters, allow_enemy_soul=allow_enemy_soul
        )
        prefix = "```ansi\n"
        suffix = "\n```"
        limit = 1024 - len(prefix) - len(suffix)
        if len(block) > limit:
            truncated = block[:limit]
            last_break = truncated.rfind("\n")
            if last_break != -1:
                truncated = truncated[:last_break]
            block = truncated or block[:limit]
        return f"{prefix}{block}{suffix}"

    def _select_weapon_motion_template(
        self, weapon_type: WeaponType, *, use_skill: bool
    ) -> str:
        pools = WEAPON_MOTIONS.get(weapon_type) or WEAPON_MOTIONS[WeaponType.BARE_HAND]
        key = "skill" if use_skill else "basic"
        pool = pools.get(key) or pools.get("skill") or pools.get("basic")
        if not pool:
            pool = ["surges forward with untamed momentum"]
        return random.choice(pool)

    def _select_affinity_imagery(
        self, affinity: Optional[SpiritualAffinity]
    ) -> tuple[str, str]:
        imagery = AFFINITY_IMAGERY.get(affinity) or AFFINITY_IMAGERY[None]
        manifestations = imagery.get("manifestations", []) or AFFINITY_IMAGERY[None][
            "manifestations"
        ]
        effects = imagery.get("effects", []) or AFFINITY_IMAGERY[None]["effects"]
        return random.choice(manifestations), random.choice(effects)

    async def _offer_combat_decision(
        self,
        interaction: discord.Interaction,
        *,
        title: str,
        description: str,
        allowed_user_ids: Sequence[int],
        escape_chance: float,
        colour: discord.Colour = discord.Colour.orange(),
        thread: discord.Thread | None = None,
    ) -> CombatDecision:
        view = CombatDecisionView(allowed_user_ids, escape_enabled=escape_chance > 0)
        embed = discord.Embed(title=title, description=description, colour=colour)
        embed.set_footer(text="Decide swiftly — hesitation courts disaster.")
        if thread is not None:
            try:
                message = await thread.send(embed=embed, view=view)
            except discord.HTTPException:
                message = await interaction.followup.send(
                    embed=embed, view=view, wait=True
                )
        else:
            message = await interaction.followup.send(embed=embed, view=view, wait=True)
        view.message = message
        decision = await view.wait_for_result()
        return decision

    def _format_action_line(
        self,
        attacker: FighterState,
        target: FighterState,
        skill: Optional[Skill],
        damage: float,
        pool: str,
    ) -> str:
        attacker_label = self._format_combat_name(attacker)
        target_label = self._format_combat_name(target)
        skill_text = self._format_skill_name(skill)
        weapon_type = self._resolve_weapon_type(attacker, skill)
        use_skill = skill_text is not None
        motion_template = self._select_weapon_motion_template(weapon_type, use_skill=use_skill)
        affinity = (
            skill.elements[0]
            if skill and skill.elements
            else (skill.element if skill and skill.element else attacker.primary_affinity)
        )
        manifestation, effect = self._select_affinity_imagery(affinity)
        template_pool = SKILL_PATTERNS if use_skill else BASIC_PATTERNS
        template = random.choice(template_pool)
        pronouns = attacker.pronouns
        formatted_motion = motion_template.format(
            attacker=attacker_label,
            target=target_label,
            skill=skill_text or "",
            possessive=pronouns.possessive,
            subject=pronouns.subject,
            subject_capitalized=pronouns.subject.capitalize(),
            obj=pronouns.obj,
            reflexive=pronouns.reflexive,
        )
        action_text = template.format(
            attacker=attacker_label,
            target=target_label,
            skill=skill_text or "",
            possessive=pronouns.possessive,
            subject=pronouns.subject,
            subject_capitalized=pronouns.subject.capitalize(),
            obj=pronouns.obj,
            reflexive=pronouns.reflexive,
            weapon_motion=formatted_motion,
            affinity_image=manifestation,
            affinity_effect=effect,
        )
        damage_text = self._format_damage_amount(damage, pool)
        return f"{action_text}, dealing {damage_text} damage."

    def _round_banner(self, number: int) -> str:
        formatted = format_number(number).zfill(2)
        banner = f"ROUND {formatted} ─────────────"
        return self._colour_text(banner, colour=ROUND_LINE_COLOUR, bold=True)

    def _round_footer(self) -> str:
        return ""

    def _process_passive_healing(
        self,
        round_number: int,
        fighters: Sequence[FighterState],
        log: List[str],
    ) -> bool:
        healed_any = False
        for fighter in fighters:
            if fighter.defeated():
                continue
            for effect in fighter.passive_heals:
                if not effect.applies_on_round(round_number):
                    continue
                current = fighter.soul_hp if effect.is_soul_pool else fighter.hp
                maximum = fighter.max_soul_hp if effect.is_soul_pool else fighter.max_hp
                healed_amount = max(0.0, min(maximum, current + effect.amount) - current)
                if healed_amount <= 0:
                    continue
                if effect.is_soul_pool:
                    fighter.soul_hp = min(maximum, current + healed_amount)
                else:
                    fighter.hp = min(maximum, current + healed_amount)
                if not healed_any and log and log[-1]:
                    log.append("")
                log.append(self._format_heal_line(fighter, healed_amount, effect))
                healed_any = True
        return healed_any

    def _format_defeat_line(self, fighter: FighterState) -> str:
        name = self._format_combat_name(fighter)
        return f"{name} HAS BEEN DEFEATED!"

    def _log_chunks(self, log: Sequence[str], limit: int = 1024) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []
        current_length = 0
        for line in log:
            text = line
            separator = 1 if current else 0
            projected = current_length + separator + len(text)
            if current and projected > limit:
                chunks.append("\n".join(current) or "\u200b")
                current = []
                current_length = 0
                separator = 0
                projected = len(text)
            current.append(text)
            current_length = projected
        if current:
            chunks.append("\n".join(current) or "\u200b")
        return chunks if chunks else ["\u200b"]

    def _render_log_description(self, log: Sequence[str], limit: int = 4000) -> str:
        if not log:
            return "Awaiting actions..."
        text = "\n".join(log)
        if len(text) <= limit:
            return text
        truncated = text[-limit:]
        first_break = truncated.find("\n")
        if first_break != -1:
            truncated = truncated[first_break + 1 :]
        return f"...\n{truncated}"

    def _build_combat_embed(
        self,
        title: str,
        players: Sequence[FighterState],
        enemies: Sequence[FighterState],
        log: Sequence[str],
        rounds: int,
        *,
        colour: discord.Colour = discord.Colour.dark_teal(),
    ) -> discord.Embed:
        embed = discord.Embed(title=title, colour=colour)
        if log:
            description = self._render_log_description(log)
        else:
            description = "Awaiting actions..."
        embed.description = f"```ansi\n{style_description(description)}\n```"
        reveal_soul_hp = any(
            fighter.is_player
            and fighter.player
            and fighter.player.active_path == CultivationPath.SOUL.value
            for fighter in players
        )
        embed.add_field(
            name="Allies",
            value=self._format_status_field(
                players, allow_enemy_soul=reveal_soul_hp
            ),
            inline=False,
        )
        embed.add_field(
            name="Opponents",
            value=self._format_status_field(
                enemies, allow_enemy_soul=reveal_soul_hp
            ),
            inline=False,
        )
        embed.set_footer(text=f"Rounds fought: {format_number(rounds)}")
        return embed

    def _combat_thread_name(
        self, players: Sequence[FighterState], enemies: Sequence[FighterState]
    ) -> str:
        player_name = players[0].name if players else "Allies"
        enemy_name = enemies[0].name if enemies else "Opponents"
        base = f"{player_name} Vs {enemy_name}".strip()
        if not base:
            base = "Combat Encounter"
        if len(base) > 100:
            base = base[:97] + "..."
        return base

    async def _run_auto_combat(
        self,
        interaction: discord.Interaction,
        players: List[FighterState],
        enemies: List[FighterState],
        *,
        title: str,
        update_delay: float = 1.0,
        initial_content: str | None = None,
        allowed_mentions: discord.AllowedMentions | None = None,
        is_duel: bool = False,
        thread_name: str | None = None,
    ) -> Tuple[CombatReport, discord.Message]:
        log: List[str] = []
        rounds = 0
        defeated_players: List[FighterState] = []
        players_escaped = False
        players_surrendered = False
        duel_prompted: set[str] = set()
        low_health_prompted: set[str] = set()
        embed = self._build_combat_embed(title, players, enemies, log, rounds)
        placeholder = initial_content or "Combat encounter initiated."
        allowed = allowed_mentions if initial_content else discord.AllowedMentions.none()
        combat_thread: discord.Thread | None = None
        thread_title = thread_name or self._combat_thread_name(players, enemies)
        guild = interaction.guild
        if guild is None:
            raise CombatSetupError("Combat encounters require a guild context.")

        participant_ids: set[int] = set()
        for fighter in (*players, *enemies):
            if not fighter.is_player:
                continue
            try:
                participant_ids.add(int(fighter.identifier))
            except (TypeError, ValueError):
                continue
        participant_ids.add(interaction.user.id)

        base_channel: discord.TextChannel
        channel = interaction.channel
        if isinstance(channel, discord.Thread):
            parent = channel.parent
            if not isinstance(parent, discord.TextChannel):
                raise CombatSetupError("Combat encounters require a text channel.")
            base_channel = parent
        elif isinstance(channel, discord.TextChannel):
            base_channel = channel
        else:
            raise CombatSetupError("Combat encounters require a text channel.")

        try:
            thread = await base_channel.create_thread(
                name=thread_title,
                auto_archive_duration=1440,
                type=discord.ChannelType.private_thread,
                reason="Combat encounter",
                invitable=False,
            )
        except discord.HTTPException as exc:
            raise CombatSetupError("Unable to create combat thread.") from exc
        else:
            combat_thread = thread
            for user_id in participant_ids:
                member = guild.get_member(user_id)
                if member is None:
                    try:
                        member = await guild.fetch_member(user_id)
                    except discord.HTTPException:
                        continue
                try:
                    await thread.add_user(member)
                except discord.HTTPException:
                    continue

        send_kwargs = dict(content=placeholder, embed=embed, allowed_mentions=allowed)
        message: discord.Message
        if combat_thread is not None:
            try:
                message = await combat_thread.send(**send_kwargs)
            except discord.HTTPException:
                message = await interaction.followup.send(**send_kwargs, wait=True)
            else:
                ack_text = f"Combat encounter is underway in {combat_thread.mention}."
                try:
                    if interaction.response.is_done():
                        await interaction.followup.send(ack_text, ephemeral=True)
                    else:
                        await interaction.response.send_message(ack_text, ephemeral=True)
                except discord.HTTPException:
                    pass
        else:
            message = await interaction.followup.send(**send_kwargs, wait=True)
        display_message = message
        if initial_content is None:
            try:
                await display_message.edit(content=None)
            except discord.HTTPException:
                pass

        while True:
            active_players = [p for p in players if not p.defeated()]
            active_enemies = [e for e in enemies if not e.defeated()]
            if not active_players or not active_enemies:
                break
            rounds += 1
            if log:
                log.append("")
            log.append(self._round_banner(rounds))
            turn_order = sorted(
                active_players + active_enemies, key=lambda f: f.agility, reverse=True
            )
            round_had_actions = False
            for fighter in turn_order:
                if fighter.defeated():
                    continue
                if fighter in active_players:
                    targets = [
                        enemy
                        for enemy in active_enemies
                        if not enemy.defeated() and enemy is not fighter
                    ]
                else:
                    targets = [
                        player
                        for player in active_players
                        if not player.defeated() and player is not fighter
                    ]
                if not targets:
                    continue
                target = random.choice(targets)
                skill = self._attempt_skill(fighter)
                damage, pool = self._apply_damage(fighter, target, skill)
                log.append(
                    self._format_action_line(fighter, target, skill, damage, pool)
                )
                round_had_actions = True
                if target.defeated():
                    log.extend([
                        "",
                        self._format_defeat_line(target),
                        "",
                    ])
                    if target.is_player and target not in defeated_players:
                        defeated_players.append(target)
                embed = self._build_combat_embed(title, players, enemies, log, rounds)
                try:
                    await display_message.edit(embed=embed)
                except discord.HTTPException:
                    pass
                await asyncio.sleep(update_delay)

                if players_escaped or players_surrendered:
                    break

                if (
                    is_duel
                    and fighter.is_player
                    and target.is_player
                    and target.player is not None
                    and not target.defeated()
                    and target.identifier not in duel_prompted
                    and not players_surrendered
                    and not players_escaped
                ):
                    max_hp = target.max_hp or 1.0
                    max_soul = target.max_soul_hp or 1.0
                    hp_low = target.hp > 0 and target.hp <= 0.1 * max_hp
                    soul_low = target.soul_hp > 0 and target.soul_hp <= 0.1 * max_soul
                    if hp_low or soul_low:
                        duel_prompted.add(target.identifier)
                        allowed_ids = [target.player.user_id]
                        if allowed_ids:
                            description = (
                                f"{self._format_combat_name(target)} staggers at the brink of defeat.\n"
                                f"Will {target.pronouns.subject} continue the duel?"
                            )
                            decision = await self._offer_combat_decision(
                                interaction,
                                title="Duelist's Dilemma",
                                description=description,
                                allowed_user_ids=allowed_ids,
                                escape_chance=0.5,
                                colour=discord.Colour.purple(),
                                thread=combat_thread,
                            )
                            if log and log[-1]:
                                log.append("")
                            if decision is CombatDecision.KEEP_FIGHTING:
                                log.append(
                                    f"{self._format_combat_name(target)} steels {target.pronouns.possessive} resolve and fights on."
                                )
                            elif decision is CombatDecision.SURRENDER:
                                players_surrendered = True
                                target.hp = 0.0
                                target.soul_hp = 0.0
                                if target not in defeated_players:
                                    defeated_players.append(target)
                                log.append(
                                    f"{self._format_combat_name(target)} bows and concedes the duel."
                                )
                            else:
                                escape_success = random.random() < 0.5
                                if escape_success:
                                    players_escaped = True
                                    log.append(
                                        f"{self._format_combat_name(target)} withdraws in a blur, escaping the duel."
                                    )
                                else:
                                    log.append(
                                        f"{self._format_combat_name(target)} fails to break free and must keep fighting!"
                                    )
                            if players_surrendered or players_escaped:
                                break

                if (
                    not is_duel
                    and target.is_player
                    and target.player is not None
                    and target.identifier not in low_health_prompted
                    and not players_surrendered
                    and not players_escaped
                ):
                    max_hp = target.max_hp or 1.0
                    max_soul = target.max_soul_hp or 1.0
                    hp_low = target.hp > 0 and target.hp <= 0.1 * max_hp
                    soul_low = target.soul_hp > 0 and target.soul_hp <= 0.1 * max_soul
                    if hp_low or soul_low:
                        low_health_prompted.add(target.identifier)
                        escape_chance = max(
                            (enemy.escape_chance for enemy in active_enemies),
                            default=0.0,
                        )
                        allowed_ids = [target.player.user_id]
                        if allowed_ids:
                            description = (
                                f"{self._format_combat_name(target)} wavers at the brink of defeat.\n"
                                "Do you continue, surrender, or attempt to flee?"
                            )
                            decision = await self._offer_combat_decision(
                                interaction,
                                title="Perilous Choice",
                                description=description,
                                allowed_user_ids=allowed_ids,
                                escape_chance=escape_chance,
                                thread=combat_thread,
                            )
                            if log and log[-1]:
                                log.append("")
                            if decision is CombatDecision.KEEP_FIGHTING:
                                log.append(
                                    f"{self._format_combat_name(target)} grits {target.pronouns.possessive} teeth and fights on!"
                                )
                            elif decision is CombatDecision.SURRENDER:
                                players_surrendered = True
                                for ally in players:
                                    ally.hp = 0.0
                                    ally.soul_hp = 0.0
                                    if ally not in defeated_players:
                                        defeated_players.append(ally)
                                log.append("The party lowers their weapons and surrenders.")
                            else:
                                if escape_chance <= 0:
                                    log.append("Escape is impossible here; the battle rages on!")
                                else:
                                    escape_success = random.random() < escape_chance
                                    if escape_success:
                                        players_escaped = True
                                        log.append("The party seizes a fleeting opening and escapes!")
                                    else:
                                        log.append(
                                            "The party fails to escape and must continue fighting!"
                                        )
                            if players_surrendered or players_escaped:
                                break
            if players_surrendered or players_escaped:
                break

            healed = self._process_passive_healing(
                rounds, [*players, *enemies], log
            )
            if healed:
                embed = self._build_combat_embed(title, players, enemies, log, rounds)
                try:
                    await display_message.edit(embed=embed)
                except discord.HTTPException:
                    pass
            if round_had_actions:
                footer = self._round_footer()
                if footer:
                    log.append(footer)

        surviving_players = [p for p in players if not p.defeated()]
        player_victory = bool(surviving_players) and not any(
            not enemy.defeated() for enemy in enemies
        )
        report = CombatReport(
            player_victory=player_victory,
            rounds=rounds,
            log=log,
            surviving_players=surviving_players,
            defeated_players=defeated_players,
            players_escaped=players_escaped,
            players_surrendered=players_surrendered,
        )
        return report, display_message, combat_thread

    async def _close_combat_thread(
        self, thread: discord.Thread | None
    ) -> None:
        if thread is None:
            return
        try:
            await thread.edit(archived=True, locked=True, reason="Combat encounter concluded")
        except discord.HTTPException:
            pass

    async def _start_duel(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        challenger_id: int,
        opponent_id: int,
    ) -> None:
        await self.ensure_guild_loaded(guild_id)
        challenger = await self._fetch_player(guild_id, challenger_id)
        opponent = await self._fetch_player(guild_id, opponent_id)
        if not challenger or not opponent:
            await interaction.followup.send(
                "One of the duelists no longer has a cultivation record.",
                ephemeral=True,
            )
            return

        await self._set_combat_flag(guild_id, [challenger, opponent], True)
        challenger_fighter = self._build_player_fighter(challenger)
        opponent_fighter = self._build_player_fighter(opponent)
        challenger_fighter.display_colour = PLAYER_NAME_COLOUR
        opponent_fighter.display_colour = DUEL_OPPONENT_COLOUR
        finalised = False
        combat_thread: discord.Thread | None = None
        try:
            report, message, combat_thread = await self._run_auto_combat(
                interaction,
                [challenger_fighter],
                [opponent_fighter],
                title="Duel in Progress",
                is_duel=True,
            )
        except CombatSetupError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        colour = discord.Colour.dark_teal() if report.player_victory else discord.Colour.dark_red()
        if report.players_escaped:
            colour = discord.Colour.dark_gold()
        if challenger_fighter.defeated() and opponent_fighter.defeated():
            final_title = "Mutual Defeat"
            outcome_text = "Both duelists collapse, unable to continue."
        elif opponent_fighter.defeated():
            final_title = f"{challenger_fighter.name} triumphs!"
            outcome_text = f"{challenger_fighter.name} claims victory."
        elif challenger_fighter.defeated():
            final_title = f"{opponent_fighter.name} triumphs!"
            outcome_text = f"{opponent_fighter.name} stands victorious."
        elif report.players_escaped:
            final_title = "Duel Abandoned"
            outcome_text = "A duelist slips away, leaving the duel unresolved."
        else:
            final_title = "Duel Concluded"
            outcome_text = "The duel ends without a decisive victor."

        final_embed = self._build_combat_embed(
            final_title,
            [challenger_fighter],
            [opponent_fighter],
            report.log,
            report.rounds,
            colour=colour,
        )
        final_embed.add_field(name="Outcome", value=outcome_text, inline=False)
        relocated: list[str] = []
        for record, fighter in (
            (challenger, challenger_fighter),
            (opponent, opponent_fighter),
        ):
            record.current_hp = max(0.0, fighter.hp)
            record.current_soul_hp = max(0.0, fighter.soul_hp)
            record.in_combat = False
            record.sync_health()
            if (record.current_hp <= 0 or record.current_soul_hp <= 0) and record.last_safe_zone:
                record.location = record.last_safe_zone
                relocated.append(record.name or f"Cultivator {record.user_id}")
            await self._save_player(guild_id, record)
        if relocated:
            final_embed.add_field(
                name="Aftermath",
                value=", ".join(relocated)
                + " are returned to their last safe zones to recover.",
                inline=False,
            )
        finalised = True
        try:
            await message.edit(embed=final_embed)
        except discord.HTTPException:
            pass
        finally:
            if not finalised:
                await self._set_combat_flag(guild_id, [challenger, opponent], False)
            await self._close_combat_thread(combat_thread)

    def _prepare_encounter_fighters(
        self, party_members: Sequence[PlayerProgress], enemy_key: str, enemy: Enemy
    ) -> Tuple[List[FighterState], List[FighterState]]:
        players = [self._build_player_fighter(member) for member in party_members]
        enemies = [self._build_enemy_fighter(enemy_key, enemy)]
        return players, enemies

    def _reward_victory(
        self,
        guild_id: int,
        party_members: Sequence[PlayerProgress],
        enemy: Enemy,
        report: CombatReport,
    ) -> Tuple[List[Tuple[str, int]], List[Tuple[str, int]]]:
        loot_obtained: List[Tuple[str, int]] = []
        loot_skipped: List[Tuple[str, int]] = []
        if not report.player_victory:
            return loot_obtained, loot_skipped
        for member in party_members:
            gain = random.randint(self.config.combat_exp_gain // 2, self.config.combat_exp_gain)
            gain_combat_exp(member, gain)
            obtained, skipped = award_loot(
                member,
                enemy.loot_table,
                self.state.items,
                self.state.currencies,
            )
            loot_obtained.extend(obtained)
            loot_skipped.extend(skipped)
        return loot_obtained, loot_skipped

    def _loot_label(self, key: str) -> str:
        item = self.state.items.get(key)
        if item:
            return item.name
        currency = self.state.currencies.get(key)
        if currency:
            return currency.name
        return key

    def _parse_loot_grade(self, raw_grade: object) -> tuple[int, str]:
        if raw_grade is None:
            return 0, "Unranked"
        if isinstance(raw_grade, (int, float)):
            value = int(raw_grade)
            return value, f"Grade {value}"
        grade_text = str(raw_grade).strip()
        if not grade_text:
            return 0, "Unranked"
        numeric = skill_grade_number(grade_text)
        if numeric is not None:
            display_text = grade_text.title()
            if "grade" not in grade_text.lower() and str(numeric) not in grade_text:
                display_text = f"{display_text} (Grade {numeric})"
            return numeric, display_text
        grade_aliases = {
            "common": 1,
            "mortal": 1,
            "uncommon": 2,
            "rare": 3,
            "earth": 4,
            "spirit": 4,
            "epic": 5,
            "heaven": 7,
            "legendary": 8,
            "immortal": 9,
            "divine": 9,
            "celestial": 9,
            "mythic": 9,
            "saint": 8,
        }
        normalized = grade_text.lower()
        if normalized in grade_aliases:
            return grade_aliases[normalized], grade_text.title()
        return 0, grade_text.title()

    def _loot_colour_for_grade(self, grade_value: int, kind: str) -> str:
        if kind == "currency":
            return "teal"
        thresholds = [
            (9, "yellow"),
            (7, "amber"),
            (5, "magenta"),
            (3, "cyan"),
            (1, "green"),
        ]
        for threshold, colour in thresholds:
            if grade_value >= threshold:
                return colour
        return "gray"

    def _prepare_loot_display(
        self, entries: Sequence[tuple[str, int]]
    ) -> tuple[str | None, list[str]]:
        totals: Dict[str, int] = defaultdict(int)
        for key, amount in entries:
            try:
                totals[key] += int(amount)
            except (TypeError, ValueError):
                continue
        resolved: list[dict[str, Any]] = []
        for key, amount in totals.items():
            if amount <= 0:
                continue
            item = self.state.items.get(key)
            currency = self.state.currencies.get(key)
            if item:
                grade_value, grade_display = self._parse_loot_grade(getattr(item, "grade", None))
                description = (item.description or "").strip() or "No description provided."
                summary_description = description if len(description) <= 120 else description[:117] + "…"
                detail_description = description if len(description) <= 400 else description[:397] + "…"
                type_parts: list[str] = []
                if getattr(item, "item_type", ""):
                    type_parts.append(str(item.item_type).replace("-", " ").title())
                slot = getattr(item, "equipment_slot", None)
                if slot:
                    slot_name = getattr(slot, "value", str(slot))
                    type_parts.append(str(slot_name).replace("_", " ").title())
                type_label = " — ".join(type_parts) if type_parts else "Item"
                stats_lines: list[str] = []
                for stat_name, value in item.stat_modifiers.items():
                    if abs(value) < 1e-6:
                        continue
                    stats_lines.append(f"{stat_name.title()}: {value:+g}")
                if getattr(item, "inventory_space_bonus", 0):
                    stats_lines.append(
                        f"Inventory Space: +{format_number(getattr(item, 'inventory_space_bonus'))}"
                    )
                colour = self._loot_colour_for_grade(grade_value, "item")
                name = item.name
            elif currency:
                grade_value, grade_display = 0, "Currency"
                description = (currency.description or "").strip() or "A form of tender."
                summary_description = description if len(description) <= 120 else description[:117] + "…"
                detail_description = description if len(description) <= 400 else description[:397] + "…"
                type_label = "Currency"
                stats_lines = []
                colour = self._loot_colour_for_grade(grade_value, "currency")
                name = currency.name
            else:
                grade_value, grade_display = 0, "Uncatalogued"
                description = "An unfamiliar treasure yet to be recorded."
                summary_description = description
                detail_description = description
                type_label = "Unknown"
                stats_lines = []
                colour = self._loot_colour_for_grade(grade_value, "item")
                name = self._loot_label(key)
            fallback_line = f"{format_number(amount)}× {name}"
            if grade_display:
                fallback_line += f" ({grade_display})"
            resolved.append(
                {
                    "key": key,
                    "amount": amount,
                    "name": name,
                    "grade_value": grade_value,
                    "grade_display": grade_display or "Unranked",
                    "summary_description": summary_description,
                    "detail_description": detail_description,
                    "type_label": type_label,
                    "stats": stats_lines,
                    "colour": colour,
                    "fallback": fallback_line,
                }
            )
        if not resolved:
            return None, []
        resolved.sort(key=lambda entry: (-int(entry["grade_value"]), str(entry["name"]).lower()))
        summary_lines: list[str] = []
        for entry in resolved:
            amount_text = format_number(entry["amount"])
            name_line = self._colour_text(
                f"{entry['name']} × {amount_text}",
                colour=str(entry["colour"]),
                bold=True,
            )
            grade_line = self._colour_text(str(entry["grade_display"]), colour="yellow")
            summary_lines.append(f"{name_line} {grade_line}".rstrip())
            summary_lines.append(style_description(str(entry["summary_description"])))
            summary_lines.append("")
        if summary_lines and summary_lines[-1] == "":
            summary_lines.pop()
        body = "\n".join(summary_lines)
        prefix = "```ansi\n"
        suffix = "\n```"
        limit = 1024 - len(prefix) - len(suffix)
        if len(body) > limit:
            truncated = body[:limit]
            last_double_break = truncated.rfind("\n\n")
            if last_double_break != -1:
                truncated = truncated[:last_double_break]
            else:
                last_break = truncated.rfind("\n")
                if last_break != -1:
                    truncated = truncated[:last_break]
            body = truncated or body[:limit]
        summary_text = f"{prefix}{body}{suffix}"
        detail_blocks: list[str] = []
        for entry in resolved:
            lines = [
                self._colour_text(
                    f"{entry['name']} × {format_number(entry['amount'])}",
                    colour=str(entry["colour"]),
                    bold=True,
                )
            ]
            grade_display = str(entry["grade_display"])
            if grade_display:
                lines.append(self._colour_text(f"Grade: {grade_display}", colour="yellow"))
            type_label = str(entry["type_label"])
            if type_label:
                lines.append(self._colour_text(f"Type: {type_label}", colour="cyan"))
            lines.append(style_description(str(entry["detail_description"])))
            stats = list(entry["stats"])
            if stats:
                lines.append(self._colour_text("Stats:", colour="magenta", bold=True))
                for stat in stats:
                    lines.append(f"  {stat}")
            detail_body = "\n".join(lines)
            if len(detail_body) > 1900:
                detail_body = detail_body[:1897] + "…"
            detail_blocks.append(f"```ansi\n{detail_body}\n```")
        return summary_text, detail_blocks

    async def _finalise_encounter(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        party_members: Sequence[PlayerProgress],
        enemy_key: str,
        enemy: Enemy,
        report: CombatReport,
        message: discord.Message,
        players: Sequence[FighterState],
        enemies: Sequence[FighterState],
    ) -> None:
        loot_obtained, loot_skipped = self._reward_victory(
            guild_id, party_members, enemy, report
        )
        fighter_lookup: Dict[int, FighterState] = {}
        for fighter in players:
            if not fighter.is_player:
                continue
            try:
                identifier = int(fighter.identifier)
            except (TypeError, ValueError):
                continue
            fighter_lookup[identifier] = fighter
        relocated: list[str] = []
        for member in party_members:
            fighter = fighter_lookup.get(member.user_id)
            if fighter is not None:
                member.current_hp = max(0.0, fighter.hp)
                member.current_soul_hp = max(0.0, fighter.soul_hp)
            member.in_combat = False
            member.sync_health()
            moved = False
            if not report.player_victory and (
                (member.current_hp is not None and member.current_hp <= 0)
                or (member.current_soul_hp is not None and member.current_soul_hp <= 0)
            ):
                if member.last_safe_zone:
                    member.location = member.last_safe_zone
                    moved = True
            if moved:
                relocated.append(member.name or f"Cultivator {member.user_id}")
            await self._save_player(guild_id, member)
        colour = discord.Colour.dark_magenta() if report.player_victory else discord.Colour.dark_red()
        if report.player_victory:
            title = f"{enemy.name} defeated!"
            outcome_text = "Your party emerges victorious."
        else:
            if report.players_escaped:
                colour = discord.Colour.dark_gold()
                title = "Retreat Successful"
                outcome_text = "You break away from the encounter before annihilation strikes."
            elif report.players_surrendered:
                title = "Combat Conceded"
                outcome_text = "Your party yields, trusting in mercy to survive the day."
            else:
                title = f"Overwhelmed by {enemy.name}"
                outcome_text = "You are forced to retreat and recover."
        embed = self._build_combat_embed(
            title,
            players,
            enemies,
            report.log,
            report.rounds,
            colour=colour,
        )
        embed.add_field(name="Outcome", value=outcome_text, inline=False)
        loot_summary, loot_detail_blocks = self._prepare_loot_display(loot_obtained)
        loot_view: Optional[LootDetailsView] = None
        if loot_summary:
            embed.add_field(name="Loot Obtained", value=loot_summary, inline=False)
            if loot_detail_blocks:
                loot_view = LootDetailsView(owner_id=None, detail_blocks=loot_detail_blocks)
        skipped_summary, _ = self._prepare_loot_display(loot_skipped)
        if skipped_summary:
            embed.add_field(name="Unable to Carry", value=skipped_summary, inline=False)
        if relocated:
            embed.add_field(
                name="Retreat to Sanctuary",
                value=", ".join(relocated)
                + " are escorted to their last safe zones to recover.",
                inline=False,
            )
        try:
            if loot_view is not None:
                loot_view.bind_message(message)
            await message.edit(embed=embed, view=loot_view)
        except discord.HTTPException:
            pass

    @app_commands.command(name="duel", description="Challenge another cultivator to an automatic duel")
    @app_commands.describe(opponent="The cultivator you wish to duel")
    @app_commands.guild_only()
    async def duel(
        self, interaction: discord.Interaction, opponent: discord.Member
    ) -> None:
        guild = interaction.guild
        assert guild is not None
        if opponent.id == interaction.user.id:
            await interaction.response.send_message(
                "You cannot duel yourself.", ephemeral=True
            )
            return
        if opponent.bot:
            await interaction.response.send_message(
                "You cannot duel a construct of code.", ephemeral=True
            )
            return
        await self.ensure_guild_loaded(guild.id)
        challenger = await self._fetch_player(guild.id, interaction.user.id)
        if not challenger:
            await interaction.response.send_message(
                "Register first with /register.", ephemeral=True
            )
            return
        opponent_player = await self._fetch_player(guild.id, opponent.id)
        if not opponent_player:
            await interaction.response.send_message(
                "That cultivator has not registered.", ephemeral=True
            )
            return
        if challenger.in_combat:
            await interaction.response.send_message(
                "You are already engaged in an encounter.", ephemeral=True
            )
            return
        if opponent_player.in_combat:
            await interaction.response.send_message(
                "That cultivator is already engaged in an encounter.", ephemeral=True
            )
            return
        for participant, label in ((challenger, "You"), (opponent_player, opponent.display_name)):
            if (participant.current_hp is not None and participant.current_hp <= 0) or (
                participant.current_soul_hp is not None and participant.current_soul_hp <= 0
            ):
                await interaction.response.send_message(
                    f"{label} must recover at a safe zone before duelling.",
                    ephemeral=True,
                )
                return
        challenger_location = challenger.location
        opponent_location = opponent_player.location
        channel_key = self.state.location_key_for_channel(interaction.channel_id)
        if channel_key is None:
            channel_key = str(interaction.channel_id)
        shared_location = (
            challenger_location
            and opponent_location
            and challenger_location == opponent_location
        )
        location = (
            self.state.locations.get(challenger_location)
            if challenger_location
            else None
        )
        channel_matches = (
            location is not None
            and location.channel_id is not None
            and interaction.channel_id is not None
            and location.channel_id == interaction.channel_id
        )
        can_force_duel = (
            shared_location
            and location is not None
            and not location.is_safe
            and channel_matches
        )
        if can_force_duel:
            await interaction.response.defer(thinking=True)
            await self._start_duel(
                interaction, guild.id, interaction.user.id, opponent.id
            )
            return
        view = DuelChallengeView(self, guild.id, interaction.user.id, opponent.id)
        embed = discord.Embed(
            title="⚔️ Duel Challenge",
            description=(
                f"{interaction.user.mention} challenges {opponent.mention} to a duel!\n"
                "Press Accept to begin or Decline to refuse."
            ),
            colour=discord.Colour.dark_teal(),
        )
        embed.set_footer(text="The challenge will expire in 60 seconds.")
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    def _resolve_enemy(self, key: str) -> Optional[Enemy]:
        enemy = self.state.enemies.get(key)
        if enemy:
            return enemy
        boss = self.state.bosses.get(key)
        return boss

    @app_commands.command(name="engage", description="Start an automatic encounter at your location")
    @app_commands.describe(enemy_key="Optional specific enemy key to engage")
    @app_commands.guild_only()
    async def engage(self, interaction: discord.Interaction, enemy_key: str | None = None) -> None:
        guild = interaction.guild
        assert guild is not None
        await self.ensure_guild_loaded(guild.id)
        player = await self._fetch_player(guild.id, interaction.user.id)
        if not player:
            await interaction.response.send_message("Register first with /register", ephemeral=True)
            return
        location = self.state.get_location_for_channel(interaction.channel_id)
        channel_key = self.state.location_key_for_channel(interaction.channel_id)
        if location is None:
            await interaction.response.send_message(
                "Combat can only be started in a configured location channel.",
                ephemeral=True,
            )
            return
        if channel_key is None or player.location != channel_key:
            await interaction.response.send_message(
                "Travel to this location first with /travel.", ephemeral=True
            )
            return
        if location.is_safe:
            await interaction.response.send_message(
                "Combat cannot be initiated within a safe zone.", ephemeral=True
            )
            return
        try:
            party_id, party_members = await self._prepare_party(guild.id, player)
        except CombatSetupError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        candidate_enemies = location.enemies
        if not candidate_enemies:
            await interaction.response.send_message("No enemies configured here.", ephemeral=True)
            return
        if enemy_key and enemy_key not in candidate_enemies:
            await interaction.response.send_message(
                "That enemy does not appear in this location.", ephemeral=True
            )
            return
        chosen_key = enemy_key or random.choice(candidate_enemies)
        enemy = self._resolve_enemy(chosen_key)
        if not enemy:
            await interaction.response.send_message("Enemy data missing.", ephemeral=True)
            return
        await self._set_combat_flag(guild.id, party_members, True)
        players: List[FighterState]
        enemies: List[FighterState]
        report: CombatReport
        message: discord.Message
        finalised = False
        combat_thread: discord.Thread | None = None
        try:
            await interaction.response.defer(thinking=True)
            players, enemies = self._prepare_encounter_fighters(
                party_members, chosen_key, enemy
            )
            try:
                report, message, combat_thread = await self._run_auto_combat(
                    interaction,
                    players,
                    enemies,
                    title=f"Encounter vs {enemy.name}",
                )
            except CombatSetupError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            await self._finalise_encounter(
                interaction,
                guild.id,
                party_members,
                chosen_key,
                enemy,
                report,
                message,
                players,
                enemies,
            )
            finalised = True
        finally:
            if not finalised:
                await self._set_combat_flag(guild.id, party_members, False)
            await self._close_combat_thread(combat_thread)

    async def start_body_encounter(
        self,
        interaction: discord.Interaction,
        player: PlayerProgress,
        enemy_key: str,
        *,
        intro_prefix: str | None = None,
        share_with_party: bool = False,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            raise CombatSetupError("Encounters require a server context.")
        await self.ensure_guild_loaded(guild.id)
        location_key = player.location
        if location_key:
            location = self.state.locations.get(location_key)
            if location and location.is_safe:
                raise CombatSetupError("Combat cannot be started within a safe zone.")
        try:
            party_id, party_members = await self._prepare_party(guild.id, player)
        except CombatSetupError as exc:
            raise CombatSetupError(str(exc)) from exc
        enemy = self._resolve_enemy(enemy_key)
        if not enemy:
            raise CombatSetupError("Enemy data missing.")
        party_mentions: List[discord.abc.Snowflake] = []
        if share_with_party and len(party_members) > 1:
            party_mentions = self._party_mentions(
                guild, party_members, interaction.user.id
            )
        await self._set_combat_flag(guild.id, party_members, True)
        finalised = False
        combat_thread: discord.Thread | None = None
        try:
            await interaction.response.defer(thinking=True)
            players, enemies = self._prepare_encounter_fighters(
                party_members, enemy_key, enemy
            )
            intro_lines: List[str] = []
            if intro_prefix:
                intro_lines.append(f"{intro_prefix} **{enemy.name}**!")
            if party_mentions:
                mentions_text = " ".join(member.mention for member in party_mentions)
                intro_lines.append(mentions_text)
            initial_content = "\n".join(intro_lines) if intro_lines else None
            allowed_mentions = (
                discord.AllowedMentions(users=party_mentions)
                if party_mentions
                else None
            )
            try:
                report, message, combat_thread = await self._run_auto_combat(
                    interaction,
                    players,
                    enemies,
                    title=f"Encounter vs {enemy.name}",
                    initial_content=initial_content,
                    allowed_mentions=allowed_mentions,
                )
            except CombatSetupError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            await self._finalise_encounter(
                interaction,
                guild.id,
                party_members,
                enemy_key,
                enemy,
                report,
                message,
                players,
                enemies,
            )
            finalised = True
        finally:
            if not finalised:
                await self._set_combat_flag(guild.id, party_members, False)
            await self._close_combat_thread(combat_thread)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CombatCog(bot))
