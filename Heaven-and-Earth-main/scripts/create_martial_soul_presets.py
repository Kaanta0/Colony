import json
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("SOUL_LAND_SKIP_PRESETS", "1")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.models.combat import SpiritualAffinity
from bot.models.soul_land import MartialSoulType

random.seed(20240527)

NON_MIXED_AFFINITIES: tuple[SpiritualAffinity, ...] = tuple(
    affinity for affinity in SpiritualAffinity if not affinity.is_mixed
)
MIXED_AFFINITIES: tuple[SpiritualAffinity, ...] = tuple(
    affinity for affinity in SpiritualAffinity if affinity.is_mixed
)


@dataclass(frozen=True)
class BeastForm:
    creature: str
    shapes: tuple[str, ...]
    habits: tuple[str, ...]
    haunts: tuple[str, ...]
    fusion_notes: tuple[str, ...]
    projection_notes: tuple[str, ...]


@dataclass(frozen=True)
class ToolForm:
    name: str
    designs: tuple[str, ...]
    craftsmanship: tuple[str, ...]
    battlefield_roles: tuple[str, ...]
    detachment: tuple[str, ...]


@dataclass(frozen=True)
class BodyAnchor:
    focus: str
    sensations: tuple[str, ...]
    refinements: tuple[str, ...]
    awakenings: tuple[str, ...]


BEAST_FORMS: tuple[BeastForm, ...] = (
    BeastForm(
        "Lynx",
        (
            "shadow-dappled lynx",
            "silver-eyed prowler",
            "midnight-furred huntress",
            "barbed-ruff guardian",
        ),
        (
            "stalks the edges of battlefield torchlight",
            "vanishes between cedar roots before reappearing behind the foe",
            "keeps watch from high ledges and pounces with little warning",
            "tracks enemies through snowfall with effortless patience",
        ),
        (
            "fog-choked canyons",
            "moonlit terraces",
            "emberlit cliff monasteries",
            "pine-shadowed ridges",
        ),
        (
            "lends slashing power to the cultivator's shoulders",
            "threads balance through every pivot and leap",
            "stiffens the spine to bear heavy momentum",
            "sharpens peripheral vision until every twitch is noticed",
        ),
        (
            "projects mirrored hunters that circle like pale ghosts",
            "casts a phantom tail that distracts archers",
            "summons a silent twin to harry flanks",
            "unfurls an echoing glare that arrests weaker foes in place",
        ),
    ),
    BeastForm(
        "Serpent",
        (
            "opal-scaled serpent",
            "stormcoil wyrm",
            "river-sheen constrictor",
            "jade-banded viper",
        ),
        (
            "threads between opponents with ribbon-slick grace",
            "wraps the cultivator in layered coils before striking",
            "lashes forward with a double-fanged feint",
            "slides across slick stone without yielding speed",
        ),
        (
            "reedbound marshes",
            "subterranean spring caverns",
            "salt-flat ruins",
            "tide-washed causeways",
        ),
        (
            "teaches the ribs to flex like interlocking scales",
            "fills muscles with surging tidal rhythm",
            "lets knuckles snap forward like striking fangs",
            "pours cold focus through every vertebra",
        ),
        (
            "casts looping avatars that constrict distant threats",
            "forms a hovering hood that spits mirage venom",
            "etches sigils into sand that animate serpentine guardians",
            "splits into twin mirages that weave in overlapping arcs",
        ),
    ),
    BeastForm(
        "Roc",
        (
            "wind-scored roc",
            "sunburst-wing raptor",
            "storm-browed talon lord",
            "sea-washed skywhale",
        ),
        (
            "dives through enemy formations to shatter morale",
            "beats cyclonic wings that pull allies aloft",
            "glides on thermal drafts before collapsing like thunder",
            "banks sharply to cleave through defensive wards",
        ),
        (
            "highland cliffs",
            "coastal skyspires",
            "glacier crags",
            "dust-wreathed mesas",
        ),
        (
            "bolts vigor into the legs for explosive takeoffs",
            "lets shoulders flex like broad wings during possession",
            "teaches breathing that syncs with gusting qi",
            "feeds aerial instinct into every sidestep",
        ),
        (
            "releases sweeping phantasms that harry from overhead",
            "lends a shimmering afterimage to guard the rear",
            "casts a feathered projection that intercepts projectiles",
            "summons whirling wing-prints that stir localized storms",
        ),
    ),
    BeastForm(
        "Stag",
        (
            "antlered tundra stag",
            "crystal-horned charger",
            "glacier-blood elk",
            "ember-antler guardian",
        ),
        (
            "plants hooves with ritual precision before surging",
            "carries the cultivator in surging strides across water",
            "lashes out with antlers that weave warding sigils",
            "kicks forward and leaves luminous hoofmarks behind",
        ),
        (
            "snow-swept vales",
            "frostbitten cedar groves",
            "glimmering aurora plains",
            "mountain shrine courtyards",
        ),
        (
            "thickens bone and sinew to bear sudden impacts",
            "channels cold stamina through every muscle",
            "teaches the lungs to drink in thin air",
            "plants an unwavering rhythm in the heart",
        ),
        (
            "casts argent antler arcs that guard allies",
            "raises mirrored hooves to batter siege engines",
            "summons a stag shadow to shoulder enemy charges",
            "threads spectral antlers to pin agile foes mid-leap",
        ),
    ),
    BeastForm(
        "Shark",
        (
            "tidal reef shark",
            "starfin predator",
            "abyssal lantern shark",
            "thunderjaw hunter",
        ),
        (
            "spirals through surf before breaching with bone-rattling force",
            "circles prey patiently and strikes in sudden bursts",
            "knifes through spirit shields like foam",
            "thrashes in tight quarters without losing momentum",
        ),
        (
            "storm-tossed reefs",
            "sunken palaces",
            "kelp-wreathed grottoes",
            "open-ocean monasteries",
        ),
        (
            "toughens skin into resilient hide",
            "fills the senses with the taste of spiritual currents",
            "lets the cultivator pivot within knee-deep water",
            "compresses the torso for ruthless spins",
        ),
        (
            "spins off spectral fins that guard flanks",
            "projects a tooth-lined maw that chews through obstacles",
            "casts rippling doubles that herd targets together",
            "emits echoing sonar pulses that reveal hidden enemies",
        ),
    ),
    BeastForm(
        "Tiger",
        (
            "ember-striped tiger",
            "dawnclaw monarch",
            "ashen-furred striker",
            "thunder-sinewed predator",
        ),
        (
            "paces the perimeter with lethal patience",
            "leaps between pillars before raking through armor",
            "roars in pulses that fracture courage",
            "sweeps its tail to destabilize footing",
        ),
        (
            "jade terraces",
            "jungle monasteries",
            "mist-draped ruins",
            "lightning-split bamboo groves",
        ),
        (
            "threads aggressive force through arms and spine",
            "lets wrists roll with claw-born accuracy",
            "anchors footing even atop slick stone",
            "pours feral intent into each heartbeat",
        ),
        (
            "casts flickering stripes that intercept strikes",
            "mirrors its roar through phantom maws",
            "splits into afterimages that bombard from three angles",
            "projects a prowling guardian that protects wounded allies",
        ),
    ),
    BeastForm(
        "Dragon Turtle",
        (
            "stone-shelled dragon turtle",
            "tidal-sage guardian",
            "volcanic-backed sentinel",
            "aurora-carapace patriarch",
        ),
        (
            "steps with slow certainty until unleashing tidal waves",
            "withdraws into a radiant shell before barreling forward",
            "drinks fire and exhales cooling mist",
            "settles into fortified stances that shrug off artillery",
        ),
        (
            "ancient lake citadels",
            "luminous undersea plateaus",
            "volcanic archipelagos",
            "glacier-fed reservoirs",
        ),
        (
            "lets qi fortify the cultivator's spine like layered plates",
            "teaches patience and measured retaliation",
            "bolsters stamina for protracted sieges",
            "roots the practitioner as if anchored to bedrock",
        ),
        (
            "projects a shimmering shell to shelter companions",
            "casts a lumbering avatar that draws enemy fire",
            "sends tidal projections forward to batter barriers",
            "summons pearl-lit orbs that orbit as rotating wards",
        ),
    ),
    BeastForm(
        "Phoenix",
        (
            "ember-plume phoenix",
            "dawnflame ascendant",
            "vermillion reborn",
            "suncrest firebird",
        ),
        (
            "spirals upward on pillars of flame before stooping",
            "sings clarion calls that steady allies",
            "sheds feathers that ignite mid-flight",
            "soars in radiant arcs that blind tyrants",
        ),
        (
            "sacred kiln temples",
            "volcanic sanctuaries",
            "sun-bright citadels",
            "desert oasis sky-altars",
        ),
        (
            "wraps the cultivator in searing wings",
            "kindles marrow until muscles hum with rebirth",
            "keeps the heart beating to a triumphant rhythm",
            "teaches how to ride thermals of spiritual heat",
        ),
        (
            "casts luminous afterimages that sear corrupt qi",
            "summons a radiant plume to ward comrades",
            "weaves twin flame avatars that spiral together",
            "projects a blazing halo that punishes ranged attackers",
        ),
    ),
    BeastForm(
        "Wolf",
        (
            "moonhowl wolf",
            "ashfang sentinel",
            "ironwind alpha",
            "startrack hunter",
        ),
        (
            "runs in crescent arcs to harry foes",
            "coordinates ally movements with bone-rattling howls",
            "presses relentless flanking attacks",
            "sinks fangs into spirit shields to pry them open",
        ),
        (
            "open steppe bastions",
            "crater-born forests",
            "nightwatch strongholds",
            "aurora-lit plains",
        ),
        (
            "lends tireless endurance during night hunts",
            "lets joints coil for sudden lunges",
            "sharpens senses toward distant heartbeats",
            "locks the body into pack-step synchrony",
        ),
        (
            "casts lupine silhouettes to harry distant lines",
            "summons spectral packmates that share damage",
            "etches a projected howl that disrupts concentration",
            "creates ghostly hunters that shepherd civilians to safety",
        ),
    ),
    BeastForm(
        "Mantis",
        (
            "jadeblade mantis",
            "sunfang cutter",
            "midnight-prayer mantid",
            "silver-hook stalker",
        ),
        (
            "bows politely before unleashing shearing strikes",
            "hangs inverted from bamboo to survey the battlefield",
            "darts in spirals while the cultivator blurs",
            "folds arms together before splitting shields",
        ),
        (
            "bamboo monasteries",
            "orchid conservatories",
            "glasswork atriums",
            "rain-slick ravines",
        ),
        (
            "teaches joint locking that mimics serrated legs",
            "lets elbows snap like cutting blades",
            "sharpens perception of minute tremors",
            "imbues arms with patient tension before release",
        ),
        (
            "casts mirrored blades that hover beside the practitioner",
            "splits into four angled phantoms to confuse foes",
            "projects a prayer-wheel halo that deflects arrows",
            "summons luminous scythe wings to guard the rear",
        ),
    ),
)


TOOL_FORMS: tuple[ToolForm, ...] = (
    ToolForm(
        "Sky Loom",
        (
            "crescent-backed loom",
            "cloud-silk shuttle",
            "starlit weaving frame",
            "wind-thread loom",
        ),
        (
            "silver filaments hum along its warp",
            "glyph-etched beams keep the frame suspended",
            "dragonbone braces steady each throw",
            "soft aurora lamplight pools beneath its struts",
        ),
        (
            "weaves barrier screens mid-battle",
            "braids anima cords that lift allies out of danger",
            "spins camouflage veils for infiltration",
            "anchors aerial battle lines with tensile wards",
        ),
        (
            "detaches as a floating dais that can pivot independently",
            "unfurls airborne threads to fight alongside the master",
            "rotates above the host and drops shimmering cloth phantoms",
            "dissolves into motes that continue weaving on command",
        ),
    ),
    ToolForm(
        "Star Compass",
        (
            "polished astral compass",
            "obsidian navigational plate",
            "glacier-glass astrolabe",
            "bronze starfinder",
        ),
        (
            "etched constellations glow under touch",
            "spirit needles align with hidden qi flows",
            "rings chime when danger approaches",
            "magnetic threads bind the moving parts",
        ),
        (
            "maps enemy formations mid-fight",
            "locks teleportation attempts in place",
            "draws ley-lines into clean geometric lattices",
            "predicts tribulation arcs for allied breakthroughs",
        ),
        (
            "splits into orbiting needles that harass aggressors",
            "projects a hovering dome that tracks movement",
            "casts light-threads that mark safe paths",
            "floats ahead like a scout lantern while the master covers",
        ),
    ),
    ToolForm(
        "Thorn Atelier",
        (
            "thorn-wrapped artisan gauntlet",
            "spiral-lathe gauntlet",
            "obsidian briar bracer",
            "living vine glove",
        ),
        (
            "veins of jade sap pulse under its surface",
            "tiny chisels bloom like petals",
            "seed-crystal studs tighten the fit",
            "rare pollen caches tint its bark",
        ),
        (
            "whittles battlefield remedies in moments",
            "mends shattered armor between clashes",
            "mixes toxins with obsessive precision",
            "raises palisades of entwined vines",
        ),
        (
            "lets sections detach into flying talon tools",
            "sprouts wooden familiars that finish delicate work",
            "sends thorn tendrils to shield the master from afar",
            "hangs in the air like a patient assistant",
        ),
    ),
    ToolForm(
        "Tempest Cauldron",
        (
            "triple-walled storm cauldron",
            "plasma-etched crucible",
            "sapphire pressure kettle",
            "thunder-banded brewpot",
        ),
        (
            "arc sigils crackle along its rim",
            "condensation halos shimmer over the lid",
            "drain spouts reshape into various spouts",
            "stormglass panels reveal roiling mixtures",
        ),
        (
            "distills cloud elixirs that fuel aerial maneuvers",
            "brews spirit tonics to patch wounds mid-siege",
            "condenses lightning charges for artillery",
            "purifies poisoned wells between campaigns",
        ),
        (
            "levitates and pours autonomously",
            "splits into floating kettles that circle the field",
            "erupts into hovering steam sprites that obey gestures",
            "rolls on arcs of lightning while the master issues commands",
        ),
    ),
    ToolForm(
        "Sunshield Disk",
        (
            "gilded solar disk",
            "sun-forged shield",
            "mirrorbright halo",
            "auric ward disk",
        ),
        (
            "layers of polished metal overlap like petals",
            "rays engrave protective sutras along its edge",
            "light crystals stud the inner surface",
            "luminous rivets pulse in time with the bearer",
        ),
        (
            "throws wide sanctified arcs",
            "reflects hostile spells back toward their casters",
            "anchors defensive formations with radiant lances",
            "focuses sunlight into curative waves",
        ),
        (
            "floats beside allies to shield them",
            "splits into miniature mirrors that orbit the team",
            "projects a hovering bastion that intercepts artillery",
            "rolls outward like a blazing chariot wheel",
        ),
    ),
    ToolForm(
        "Echo Koto",
        (
            "lacquered spirit koto",
            "crystalline resonance zither",
            "moon-threaded harp",
            "sapphire-stringed cittern",
        ),
        (
            "strings shimmer with hummingbird light",
            "bridges are carved with tide motifs",
            "soundboard sigils flare when danger rises",
            "sandalwood panels breathe slow chords",
        ),
        (
            "casts harmonies that soothe allied minds",
            "stitches sonic barriers against projectiles",
            "unleashes dissonant crescendos that fracture enemy focus",
            "records battlefield rhythms for later study",
        ),
        (
            "lets spectral performers continue the melody",
            "floats overhead and rains vibrating notes",
            "splits into smaller zithers that accompany squads",
            "broadcasts illusions of allied numbers",
        ),
    ),
    ToolForm(
        "Gravemoon Lantern",
        (
            "inkstone grave lantern",
            "selenite mourning lamp",
            "obsidian vigil lantern",
            "moonshroud beacon",
        ),
        (
            "smoke motifs coil across its panes",
            "spiritflame wicks burn without fuel",
            "cold iron ribs give it solemn poise",
            "bell chimes dangle from each corner",
        ),
        (
            "guides souls to calm departure",
            "thickens ambient qi into protective veils",
            "exposes undead masquerades",
            "marks burial grounds with serene resonance",
        ),
        (
            "floats ahead to reveal treacherous ground",
            "sends mourning bells to orbit allies",
            "casts a spectral ferryman that fights in their stead",
            "hangs overhead while the master commands from cover",
        ),
    ),
    ToolForm(
        "Celestial Quill",
        (
            "meteor-feather brush",
            "nebula-ink stylus",
            "starmap drafting pen",
            "prismatic calligrapher",
        ),
        (
            "ink droplets hover mid-air awaiting commands",
            "constellation rings orbit the handle",
            "dragon sinew bristles flex without snapping",
            "jade seals along the shaft glow softly",
        ),
        (
            "writes combat scripts that spring to life",
            "seals demonic contracts with righteous glyphs",
            "draws formations that compress space",
            "composes tactical poems that inspire squads",
        ),
        (
            "splits into orbiting pens that continue the stanza",
            "writes mid-air while the master directs elsewhere",
            "casts hovering scrolls that chant the recorded words",
            "sketches projected sigils that explode on impact",
        ),
    ),
    ToolForm(
        "Atlas Forge",
        (
            "gravity-forged warhammer",
            "folding titan anvil",
            "earthpulse forge wheel",
            "molten core hammer",
        ),
        (
            "runed pistons anchor the central hub",
            "veins of starmetal pulse along the haft",
            "hammer head reconfigures with each swing",
            "heat sinks vent steady plumes of light",
        ),
        (
            "rebuilds fortress gates in moments",
            "shapes siege weapons from raw ore",
            "compresses minerals into dense bucklers",
            "tempers allied armor for incoming tribulations",
        ),
        (
            "hurls free-floating anvils that strike like meteors",
            "deploys smithing drones forged from slag",
            "suspends mid-air to hammer alongside the master",
            "rolls forward as an armored juggernaut",
        ),
    ),
)


BODY_ANCHORS: tuple[BodyAnchor, ...] = (
    BodyAnchor(
        "Spine",
        (
            "qi tremors rumble along the vertebrae",
            "each breath traces luminous threads up the back",
            "a chill climbs the marrow before flaring warm",
            "the practitioner feels braids of force tugging posture true",
        ),
        (
            "vertebrae articulate with uncanny precision",
            "back muscles coil like braided cords",
            "nerve pathways grow sharp as glass",
            "blood hums with measured percussion",
        ),
        (
            "unlocks a Bronze-level second awakening focused on balance",
            "opens a Silver-level awakening where vertebrae emit runic light",
            "sets the stage for a Golden third awakening that births a draconic ridge",
            "reveals hidden posture memories from ancient ancestors",
        ),
    ),
    BodyAnchor(
        "Heart",
        (
            "heartbeat shifts into a measured war drum",
            "warmth surges outward in each pulse",
            "faint echoes of twin heartbeats answer in resonance",
            "the chest fills with aurora motes",
        ),
        (
            "qi channels lace around the cardiac meridians",
            "bloodline motifs brighten under the skin",
            "valves hum with chantlike rhythms",
            "breath synchronizes with allied pulses",
        ),
        (
            "reveals Bronze-level resonance with emotional qi",
            "ushers in Silver awakenings tied to empathy fields",
            "promises Golden awakenings where the heart projects shimmering wards",
            "evokes ancestral melodies long thought forgotten",
        ),
    ),
    BodyAnchor(
        "Hands",
        (
            "palms spark with prickling motes",
            "fingers extend like unfolding lotuses",
            "veins brighten into calligraphy strokes",
            "auric strands dangle from each knuckle",
        ),
        (
            "joints loosen until every gesture is precise",
            "tendons braid into luminous threads",
            "finger bones resonate like tuning forks",
            "sigils bloom across the skin",
        ),
        (
            "grants Bronze-level awakening where gestures channel elementals",
            "guides Silver awakenings that spin phantom mudras",
            "prepares for Golden awakenings that unleash cathedral-sized projections",
            "connects nerve endings to ancient artisan memories",
        ),
    ),
    BodyAnchor(
        "Eyes",
        (
            "vision fractures into layered spectra",
            "pupils stretch into vertical sigils",
            "lids flutter with starlight",
            "the world slows for a heartbeat with every blink",
        ),
        (
            "optic nerves hum with information",
            "the brow tingles as pathways bloom",
            "lashes shimmer with runic dust",
            "sightlines extend into far horizons",
        ),
        (
            "reveals Bronze awakenings granting clairvoyant glimpses",
            "ushers Silver awakenings where sights rewrite battlefield angles",
            "prepares Golden awakenings that manifest mirrored gazes fighting independently",
            "uncovers ancestral memory palaces hidden in light",
        ),
    ),
    BodyAnchor(
        "Blood",
        (
            "blood feels as if threaded with molten gold",
            "capillaries thrum like hidden lyres",
            "each heartbeat releases fragrant steam",
            "the body tastes copper and ozone before each surge",
        ),
        (
            "meridians widen to channel torrents",
            "bone marrow hums with sparkling charge",
            "skin glows with quiet phosphorescence",
            "breath lines swirl around every vessel",
        ),
        (
            "invites Bronze awakenings of regenerative pulses",
            "ushers Silver awakenings that weaponize spilled blood",
            "ushers Golden awakenings where the circulatory map becomes a battlefield",
            "recalls ancestral sagas etched in crimson",
        ),
    ),
    BodyAnchor(
        "Voice",
        (
            "tones resonate with hidden harmonics",
            "spoken words leave luminous glyphs in the air",
            "whispers stir gusts of elemental force",
            "chants bloom into layered chords",
        ),
        (
            "larynx muscles strengthen like braided cords",
            "breath control refines beyond mortal limits",
            "tongue and palate tingle with secret mantras",
            "ears ring with sympathetic chimes",
        ),
        (
            "opens Bronze awakenings that command sonic shields",
            "guides Silver awakenings where syllables sculpt matter",
            "signals Golden awakenings that project vocal avatars",
            "summons long-quiet choirs of lineage spirits",
        ),
    ),
)


BEAST_PREFIXES_BY_GRADE = {
    1: (
        "Sprout",
        "Hearth",
        "River",
        "Dawn",
        "Lantern",
        "Clover",
        "Amber",
        "Whisper",
        "Misty",
        "Softstep",
        "Crescent",
        "Bloom",
        "Pebble",
        "Gossamer",
        "Petal",
        "Brook",
    ),
    2: (
        "Verdant",
        "Gale",
        "Ashen",
        "Moon",
        "Briar",
        "Silver",
        "Boulder",
        "Rain",
        "Ridge",
        "Thorn",
        "Howl",
        "Torrent",
        "Keen",
        "Ripple",
        "Warden",
        "Skylark",
    ),
    3: (
        "Storm",
        "Frost",
        "Iron",
        "Tempest",
        "Sunrise",
        "Seastone",
        "Duskwind",
        "Verdigris",
        "Starborn",
        "Aster",
        "Bastion",
        "Crag",
        "Grove",
        "Tidal",
        "Runic",
        "Wild",
    ),
    4: (
        "Auric",
        "Thunder",
        "Vortex",
        "Obsidian",
        "Sable",
        "Sapphire",
        "Bramble",
        "Cascade",
        "Nightglow",
        "Skysunder",
        "Marrow",
        "Zephyr",
        "Lion",
        "Cinder",
        "Glaive",
        "Moonbreak",
    ),
    5: (
        "Solar",
        "Umbral",
        "Stone",
        "Rift",
        "Tremor",
        "Abyss",
        "Arc",
        "Runebound",
        "Skyforge",
        "Starveil",
        "Fable",
        "Valiant",
        "Tide",
        "Glimmer",
        "Evergreen",
        "Seaborne",
    ),
    6: (
        "Dragon",
        "Thunderwake",
        "Gilded",
        "Blight",
        "Stormflare",
        "Void",
        "Mirror",
        "Gravemoon",
        "Eclipse",
        "Ironheart",
        "Zephyrfall",
        "Bastion",
        "Myriad",
        "Dawntide",
        "Helix",
        "Radiant",
    ),
    7: (
        "Celestial",
        "Primordial",
        "Chrono",
        "Aether",
        "Ragnar",
        "Mythweave",
        "Tempestrum",
        "Soulforge",
        "Spectral",
        "Nightravager",
        "Aureate",
        "Skydominion",
        "Eternal",
        "Seastorm",
        "Starshard",
        "Nether",
    ),
    8: (
        "Paragon",
        "Everlight",
        "Nirvana",
        "Infinity",
        "Heavensunder",
        "Seraphic",
        "Worldsong",
        "Arcadian",
        "Voidcrest",
        "Oracle",
        "Abysswalker",
        "Dragonchant",
        "Thunderborne",
        "Zenith",
        "Covenant",
        "Mythic",
    ),
    9: (
        "Sovereign",
        "Transcendent",
        "Empyrean",
        "Genesis",
        "Eternal",
        "Godspire",
        "Heavenbreaker",
        "Epoch",
        "Immortal",
        "Worldrender",
        "Lightspire",
        "Dawnguard",
        "Aeonic",
        "Aurora",
        "Cataclysm",
        "Omniscient",
    ),
}


TOOL_PREFIXES_BY_GRADE = {
    1: (
        "Willow",
        "Gentle",
        "Tinker's",
        "Meadow",
        "Rainstep",
        "Herbal",
        "Twine",
        "Lattice",
        "Lantern",
        "Hearth",
    ),
    2: (
        "Artisan",
        "Skybound",
        "Silver",
        "River",
        "Thorn",
        "Glade",
        "Luminous",
        "Hidden",
        "Echo",
        "Serene",
    ),
    3: (
        "Stormglass",
        "Verdant",
        "Runespun",
        "Phoenix",
        "Gale",
        "Moon",
        "Forge",
        "Aegis",
        "Sunglint",
        "Tempest",
    ),
    4: (
        "Aurora",
        "Thunder",
        "Obsidian",
        "Starfall",
        "Seafoam",
        "Iron",
        "Prismatic",
        "Glyph",
        "Rift",
        "Dawntide",
    ),
    5: (
        "Celestial",
        "Runic",
        "Tempest",
        "Quill",
        "Mirage",
        "Atlas",
        "Dragon",
        "Warden",
        "Leviathan",
        "Arc",
    ),
    6: (
        "Myriad",
        "Gravemoon",
        "Sapphire",
        "Skyforge",
        "Thunderwake",
        "Starforged",
        "Glacial",
        "Everlit",
        "Nebula",
        "Void",
    ),
    7: (
        "Chronicle",
        "Transcendent",
        "Epoch",
        "Mythic",
        "Oracle",
        "Vanguard",
        "Aether",
        "Celestium",
        "Runebinder",
        "Arcane",
    ),
    8: (
        "Paragon",
        "Infinity",
        "Heavenwoven",
        "Omniscient",
        "Ethereal",
        "Worldsong",
        "Astral",
        "Covenant",
        "Majestic",
        "Zenith",
    ),
    9: (
        "Sovereign",
        "Empyrean",
        "Nirvana",
        "Genesis",
        "Cataclysm",
        "Heavenbreaker",
        "Godforged",
        "Eternity",
        "Aeonic",
        "Crown",
    ),
}


BODY_PREFIXES_BY_GRADE = {
    1: (
        "Quiet",
        "Gentle",
        "Patient",
        "Soft",
        "Lantern",
        "Rising",
        "Seedling",
        "Pulse",
        "Wisp",
        "Harbor",
    ),
    2: (
        "Steady",
        "River",
        "Aspiring",
        "Resolve",
        "Morning",
        "Ember",
        "Singing",
        "Hearth",
        "Quell",
        "Courage",
    ),
    3: (
        "Balanced",
        "Focused",
        "Resolute",
        "Bridging",
        "Arc",
        "Verdant",
        "Iron",
        "Tempest",
        "Warden",
        "Insight",
    ),
    4: (
        "Copper",
        "Adept",
        "Rising",
        "Echo",
        "Harmonic",
        "Nimbus",
        "Forge",
        "Ever",
        "Pulse",
        "Astral",
    ),
    5: (
        "Silver",
        "Chromatic",
        "Fervent",
        "Storm",
        "Vigorous",
        "Bright",
        "Sanguine",
        "Resonant",
        "Glyph",
        "Star",
    ),
    6: (
        "Golden",
        "Eclipse",
        "Bastion",
        "Dragon",
        "Void",
        "Synchronous",
        "Celestial",
        "Chrono",
        "Echo",
        "Radiant",
    ),
    7: (
        "Primordial",
        "Mythic",
        "Transcendent",
        "Eternal",
        "Oracle",
        "Ancestral",
        "Everwoven",
        "Worldpulse",
        "Luminous",
        "Empyreal",
    ),
    8: (
        "Paragon",
        "Infinity",
        "Omniscient",
        "Seraphic",
        "Auspicious",
        "Timeless",
        "Worldsong",
        "Zenith",
        "Genesis",
        "Everliving",
    ),
    9: (
        "Sovereign",
        "Godblood",
        "Empyrean",
        "Aeonic",
        "Immortal",
        "Eternal",
        "Skyvein",
        "Heavenpulse",
        "Myriad",
        "Omnipulse",
    ),
}


AFFINITY_TRAITS = {
    SpiritualAffinity.FIRE: (
        "fire-suffused",
        "cinder trails",
        "ignites decisive strikes",
    ),
    SpiritualAffinity.WATER: (
        "tide-steeped",
        "spiraling currents",
        "keeps momentum fluid and adaptive",
    ),
    SpiritualAffinity.WIND: (
        "windborne",
        "eddying gusts",
        "sharpens reflexive dodges",
    ),
    SpiritualAffinity.EARTH: (
        "stone-rooted",
        "gravel sparks",
        "reinforces unwavering stances",
    ),
    SpiritualAffinity.METAL: (
        "steel-etched",
        "gleaming edges",
        "lets each motion cleave with precision",
    ),
    SpiritualAffinity.ICE: (
        "frost-veiled",
        "glacial prisms",
        "cools raging temper and locks enemies still",
    ),
    SpiritualAffinity.LIGHTNING: (
        "stormwired",
        "arcing filaments",
        "quickens nerves until movements blur",
    ),
    SpiritualAffinity.LIGHT: (
        "radiance-touched",
        "halo flares",
        "bathes allies in fortifying luminance",
    ),
    SpiritualAffinity.DARKNESS: (
        "night-clad",
        "velvet shadows",
        "lets tactics thrive in obscured terrain",
    ),
    SpiritualAffinity.LIFE: (
        "verdant",
        "sprouting motes",
        "repairs wounds in steady pulses",
    ),
    SpiritualAffinity.DEATH: (
        "grave-quiet",
        "ashen whispers",
        "shepherds endings with precise mercy",
    ),
    SpiritualAffinity.SAMSARA: (
        "cycle-spun",
        "twinned dusk and dawn",
        "turns reversals into renewed vigor",
    ),
    SpiritualAffinity.SPACE: (
        "void-kissed",
        "folded horizons",
        "bends distance for tactical leverage",
    ),
    SpiritualAffinity.TIME: (
        "chronicle-tuned",
        "echoing ticks",
        "lets strategies unfold three beats ahead",
    ),
    SpiritualAffinity.GRAVITY: (
        "weightwoven",
        "orbiting motes",
        "anchors foes with crushing inevitability",
    ),
    SpiritualAffinity.POISON: (
        "venom-haloed",
        "emerald fumes",
        "threads toxins along every feint",
    ),
    SpiritualAffinity.MUD: (
        "silt-bound",
        "oily tides",
        "stalls aggressors with clinging drag",
    ),
    SpiritualAffinity.TEMPERATURE: (
        "flux-tempered",
        "heat shimmer",
        "modulates extremes for surgical strikes",
    ),
    SpiritualAffinity.LAVA: (
        "magma-forged",
        "molten gouts",
        "melts obstinate defenses to slag",
    ),
    SpiritualAffinity.TWILIGHT: (
        "dusk-veined",
        "violet hush",
        "cloaks intentions between light and dark",
    ),
    SpiritualAffinity.ENTROPY: (
        "ruin-threaded",
        "shattering motes",
        "erodes fortifications by degrees",
    ),
    SpiritualAffinity.PERMAFROST: (
        "permafrost-chilled",
        "crackling hoarfrost",
        "locks momentum beneath glacial calm",
    ),
    SpiritualAffinity.DUSTSTORM: (
        "sand-swept",
        "abrasive vortices",
        "flays sightlines away from enemies",
    ),
    SpiritualAffinity.PLASMA: (
        "starfire-charged",
        "ionised flares",
        "turns every gesture into a cutting arc",
    ),
    SpiritualAffinity.STEAM: (
        "mist-forged",
        "billowing vapour",
        "screens allies behind veils of heat",
    ),
    SpiritualAffinity.INFERNO: (
        "inferno-bound",
        "whirling bonfires",
        "erupts in wide devastation without pause",
    ),
    SpiritualAffinity.FLASHFROST: (
        "flashfrost-honed",
        "shards of frozen lightning",
        "snaps opponents into brittle stasis",
    ),
    SpiritualAffinity.FROSTFLOW: (
        "frostflow-steeped",
        "liquid ice streams",
        "lets momentum glide while freezing wounds shut",
    ),
    SpiritualAffinity.BLIZZARD: (
        "whiteout-born",
        "swallowing squalls",
        "drowns hostiles beneath relentless snow",
    ),
    SpiritualAffinity.TEMPEST: (
        "tempest-wreathed",
        "savage vortices",
        "commands weather-fronts as striking partners",
    ),
    SpiritualAffinity.MIST: (
        "mist-wreathed",
        "drifting veils",
        "confuses pursuit with elusive echoes",
    ),
}


SINGLE_AURA_VERBS = ("shimmers with", "glows with", "glitters with")
DUAL_AURA_VERBS = ("braids", "weaves between", "threads")
MULTI_AURA_VERBS = ("swirls with", "storms with", "whorls with")

BEAST_ACCENT_TEMPLATES = (
    "with crystalline horns strung in aurora light",
    "with a mane of crackling starlight",
    "armoured in layered obsidian plates",
    "with translucent wings laced in runes",
    "whose scales glow like molten glass",
    "with eyes carved from pale moonfire",
    "wrapped in whirling mist plumes",
    "with claws tipped in argent flame",
    "with shadow-slick fur traced by sigils",
    "whose feathers fall as glittering petals",
    "with dorsal fins edged in lightning",
    "with antlers carved from glacier glass",
    "with a tail of braided comet trails",
    "veiled in drifting ember dust",
    "with a jaw lined in mirrored spines",
    "wreathed in ribbons of spectral smoke",
    "with plated scales that refract stray light",
    "ringed by hovering fangs of iceglass",
    "with armored haunches stamped in runes",
    "gilded with swirling constellations",
)

BEAST_MOTION_TEMPLATES = (
    "It prowls in slow hunting circles.",
    "It coils protectively around the summoner.",
    "It glides overhead like a silent omen.",
    "It stamps sparks against the earth.",
    "It perches on unseen wind and watches.",
    "It hovers beside the wielder with a patient stare.",
    "It darts forward in mirrored afterimages.",
    "It ripples through the air like flowing silk.",
    "It leaves claw-shaped glyphs suspended midair.",
    "It drifts with the hush of {haunt}.",
    "It crackles with echoes from {haunt}.",
    "It splashes phantom spray as though born of {haunt}.",
    "It circles above before folding into the host's shadow.",
    "It balances on streams of light that appear and fade.",
    "It snaps its gaze toward every new movement.",
)

TOOL_FINISHES = (
    "rimmed in auric filigree",
    "threaded with obsidian veins",
    "coated in frostglass plating",
    "etched in silver script",
    "studded with luminous cores",
    "wrapped in woven spiritcloth",
    "framed by magnetic spokes",
    "paneled with mirrored facets",
    "bound with braided cables",
    "inlaid with jewelbright runes",
    "forged from midnight steel",
    "suspended within a crystal lattice",
    "trimmed with cinder-bright rivets",
    "armoured in tempered brass",
    "haloed by whisper-thin plates",
)

TOOL_DETAILS = (
    "Angular seams breathe with soft light.",
    "Fine gears hum beneath translucent panels.",
    "A central core pulses like a captured star.",
    "Its edges emit a subtle harmonized tone.",
    "Runes crawl across the surface in steady loops.",
    "Shadowglass windows reveal drifting embers within.",
    "Spokes extend outward, sketching precise geometry.",
    "Levitating screws orbit the chassis like guardian drones.",
    "Segments overlap to mimic a protective shell.",
    "Dense conduits glow whenever the wielder inhales.",
    "Threadlike chains sway gently beneath the frame.",
    "The grip reforms itself to match any hand.",
    "Engraved reliefs shimmer when light touches them.",
    "A glassy sheen flows across every etched line.",
    "Thin plates flex like blades made of dusk.",
)

TOOL_GESTURES = (
    "It floats beside the wielder like a vigilant satellite.",
    "It spins in steady orbits that trace protective rings.",
    "It unfolds into a shimmering combat array.",
    "It locks into place above the palm, humming softly.",
    "It fans out into overlapping plates that guard allies.",
    "It releases trailing sparks that sketch tactical lines.",
    "It unfurls slender limbs to sculpt the battlefield.",
    "It projects a faint halo that mirrors each motion.",
    "It anchors itself with thin cables of light.",
    "It splits into paired constructs that flank the user.",
    "It pulses in rhythm with the wielder's breathing.",
    "It tilts to follow the wielder's gaze without lag.",
    "It drifts ahead, scouting for threats with luminous beams.",
    "It refracts ambient light into a hovering shield.",
    "It trails etched diagrams that hover briefly in the air.",
)

BODY_VISUALS = {
    "hands": (
        "Silver glyphs course across the hands like articulated gauntlets.",
        "Translucent plates bloom over each knuckle in layered fans.",
        "Finger bones shine through with braided crystal lines.",
        "Palms glow with rotating sigils that hover a breath above the skin.",
        "Mercury veins trace along the wrists before spiralling down each finger.",
        "A web of copper light stitches across the back of the hands.",
        "Shadowed markings wrap each digit with precise rings.",
        "Iridescent talons extend where nails once sat.",
    ),
    "eyes": (
        "Iris patterns fracture into prismatic facets.",
        "Lids gleam with thin aurora bands.",
        "Pupils stretch into vertical runes that flicker softly.",
        "Haloed lashes leave faint trails of sparks when blinking.",
        "A mirrored sheen floats over the sclera like liquid glass.",
        "Thin glyphs orbit the sockets like miniature astrolabes.",
        "Glowing tear-tracks trace luminous lines down the cheeks.",
        "An inner corona of light pulses behind the gaze.",
    ),
    "blood": (
        "Veins shine beneath the skin like molten filaments.",
        "Every heartbeat sends copper light racing along arteries.",
        "Skin blushes with a soft auric glow that swirls under the surface.",
        "A lattice of crimson sigils glimmers across exposed veins.",
        "Mist gathers around the pulse points in rhythmic bursts.",
        "Liquid gold seems to flow wherever the blood moves.",
        "Scarlet motes drift free whenever the heart quickens.",
        "Faint vapour escapes with each heartbeat, wrapping the host.",
    ),
    "spine": (
        "Segmented light plates run down the spine like a luminous helm.",
        "Floating rings stack along the back, orbiting each vertebra.",
        "The spine arcs with etched glyphs that pulse in sequence.",
        "A ribbon of starlight threads from the nape to the tailbone.",
        "Translucent fins flare along the back like spectral wings.",
        "Obsidian plates overlap along the spine with radiant edges.",
        "A cascade of sparks falls from each vertebra as the host moves.",
        "Glowing tendons rise from the spine to form a protective crest.",
    ),
    "voice": (
        "Breath condenses into glowing sigils around the lips.",
        "When speaking, luminous chords ripple across the throat.",
        "Runic halos drift from every syllable.",
        "The throat shines with braided strands of light.",
        "A silver microphone of aura hovers before the mouth.",
        "Echoing rings pulse from the larynx in steady waves.",
        "Wisps of colour stream from each word.",
        "A crystal sheen wraps the jawline whenever sound escapes.",
    ),
    "heart": (
        "A radiant emblem spins above the sternum.",
        "Beating light pulses beneath the chest like a lantern.",
        "Threads of energy weave outward from the heart in looping arcs.",
        "A crystalline cage forms around the heartbeat.",
        "Soft glows flare through the ribs with every breath.",
        "A halo of ember sparks circles the chest.",
        "The heart's silhouette appears as a floating glyph.",
        "Auric feathers spread across the torso from the core.",
    ),
}

BODY_GLEAMS = (
    "Fine motes orbit close to the skin.",
    "Translucent film mirrors the host's movements.",
    "Threads of light braid outward before dissolving.",
    "A hush of static dances across the aura.",
    "Gossamer sparks trail every gesture.",
    "A faint hum ripples whenever the soul stirs.",
    "Shadows bend inward as though respecting the form.",
    "Delicate flares ignite then fade along the outline.",
)

BODY_INTRO_TEMPLATES = (
    "The martial soul crowns the {focus} in radiant detail.",
    "Energy gathers along the {focus}, reshaping it.",
    "Manifestation centers on the {focus}, altering every contour.",
    "The {focus} becomes a conduit of sculpted light.",
    "A surge of aura frames the {focus} with intricate patterning.",
)


GRADE_THEMES = {
    1: (
        "novice lineages testing their first harmonies",
        "mentors emphasise gentle possession so bones acclimate",
        "training focuses on grounding habits and steady breath",
    ),
    2: (
        "young warriors refining battlefield instincts",
        "fusion drills build stamina for extended engagements",
        "masters monitor bloodline resonance closely",
    ),
    3: (
        "cultivators consolidating daring tactics",
        "possession deepens enough to leave minor physical marks",
        "squads rely on them for decisive vanguard plays",
    ),
    4: (
        "clans entrust these souls to specialist cadres",
        "fusion hardens bone and sinew beyond mortal measures",
        "projection practice becomes mandatory in drills",
    ),
    5: (
        "battle halls chronicle their feats meticulously",
        "bloodline adjustments are scheduled between expeditions",
        "possession can draw out dormant ancestral gifts",
    ),
    6: (
        "sect elders assign personal artificers for maintenance",
        "fusion radiates clear marks along the meridians",
        "projection doubles become nearly autonomous",
    ),
    7: (
        "royal archives reserve them for titled soul masters",
        "possession blurs the line between mortal and mythical",
        "strategists base entire war plans around their presence",
    ),
    8: (
        "legendary cohorts rely on their impossible adaptability",
        "fusion threads spiritual law through each gesture",
        "projection armies march as if independent generals",
    ),
    9: (
        "divine courts note every appearance",
        "possession transforms the user's very outline",
        "their projections operate like minor sovereigns",
    ),
}


CATEGORY_WEIGHTS = {
    1: (0.72, 0.23, 0.05),
    2: (0.7, 0.24, 0.06),
    3: (0.68, 0.26, 0.06),
    4: (0.6, 0.3, 0.1),
    5: (0.55, 0.32, 0.13),
    6: (0.5, 0.34, 0.16),
    7: (0.45, 0.35, 0.2),
    8: (0.42, 0.36, 0.22),
    9: (0.4, 0.35, 0.25),
}


TRAINING_LOCALES = (
    "sky pier academies",
    "tidal amphitheatres",
    "glacier libraries",
    "undersea dojos",
    "desert caravan arenas",
    "mountain reliquaries",
    "storm-tempered citadels",
    "orchid gardens",
    "obsidian drill yards",
    "floating monasteries",
    "ruined leyline spires",
    "luminous archive vaults",
)


BATTLEFIELD_MEMORIES = (
    "clashed with sea fiends during the Mist Siege",
    "guided caravans through blight storms",
    "held the line against tyrant lords from the west",
    "rescued wounded disciples beneath collapsing skyships",
    "charted hidden meridians in the Maze of Reeds",
    "subdued renegade spirit beasts without killing",
    "rallied border towns after a shadow incursion",
    "brokered truces between rival sect heirs",
    "outmaneuvered inquisitors along the Void Frontier",
    "guarded tribulation rites atop Stormglass Peaks",
)


PROJECTION_WARNINGS = (
    "Projection drills demand strict warding; the phantom can outrun the host if discipline lapses",
    "Those with martial soul projections must guard their bodies carefully, lest assassins slip past",
    "When projection manifests, the master rotates between field command and phantom oversight",
    "Projection tactics lighten the user's burden but tempt complacency during prolonged sieges",
    "A projection lets allies rally while the cultivator rests, yet it strains the link if the first ring falters",
)


BLOODLINE_GUIDANCE = (
    "Herbalists tune their bloodline tonics to keep marrow resonant with the martial soul",
    "Elders insist on weekly meditations aligning pulse with beast cadence",
    "Body cultivators braid fresh seals over key meridians before grand battles",
    "Every advance demands recalibrating blood essence to avoid backlash",
    "Mentors pair them with singers whose hymns temper restless qi",
)


TOOL_INSIGHTS = (
    "Tool-type masters learn to fight beside their creations instead of within them",
    "They polish each joint obsessively, knowing the tool must endure corruption in their stead",
    "Support squads rotate around them so the tool can maintain coverage",
    "Every engravement doubles as a tactical diagram awaiting activation",
    "Their manuals emphasise that a broken tool is easier to mend than a shattered spine",
)


BODY_INSIGHTS = (
    "Body-type wielders track every tendon, trusting no motion to chance",
    "They obsess over posture scripts that normal cultivators overlook",
    "Training partners remark on how their aura mirrors heartbeat patterns",
    "Even at rest, fine filaments of qi trace diagrams beneath their skin",
    "They rehearse potential second awakenings until muscle memory accepts the shift",
)


CLOSING_NOTES = (
    "Seasoned instructors cite them when explaining how martial soul philosophy bridges mortals and legends",
    "Records encourage pairing them with compatible ring spirits to maximize breakthroughs",
    "Survivors of their deployments speak in hushed awe about the synergy between host and soul",
    "Archivists underline their case studies whenever novices doubt martial soul cultivation",
    "Their chronicles remind future generations that discipline and imagination walk hand in hand",
)


def choose_category(grade: int) -> MartialSoulType:
    beast_weight, tool_weight, body_weight = CATEGORY_WEIGHTS[grade]
    roll = random.random()
    if roll < beast_weight:
        return MartialSoulType.BEAST
    if roll < beast_weight + tool_weight:
        return MartialSoulType.TOOL
    return MartialSoulType.BODY


def choose_affinities(grade: int) -> tuple[SpiritualAffinity, ...]:
    if grade <= 3:
        return (random.choice(NON_MIXED_AFFINITIES),)
    if grade <= 6:
        if random.random() < 0.5:
            return (random.choice(MIXED_AFFINITIES),)
        return tuple(sorted(random.sample(NON_MIXED_AFFINITIES, 2), key=lambda a: a.name))
    if random.random() < 0.5:
        return tuple(sorted(random.sample(MIXED_AFFINITIES, 2), key=lambda a: a.name))
    return tuple(
        sorted(random.sample(NON_MIXED_AFFINITIES, 4), key=lambda a: a.name)
    )


def format_affinity_sentence(affinities: Sequence[SpiritualAffinity]) -> str:
    motifs = [AFFINITY_TRAITS[affinity][1] for affinity in affinities]
    if not motifs:
        return ""
    if len(motifs) == 1:
        verb = random.choice(SINGLE_AURA_VERBS)
        return f"Aura {verb} {motifs[0]}."
    if len(motifs) == 2:
        verb = random.choice(DUAL_AURA_VERBS)
        return f"Aura {verb} {motifs[0]} and {motifs[1]}."
    verb = random.choice(MULTI_AURA_VERBS)
    head = ", ".join(motifs[:-1])
    return f"Aura {verb} {head}, and {motifs[-1]}."


def beast_description(name: str, grade: int, affinities: Sequence[SpiritualAffinity], form: BeastForm) -> str:
    appearance = random.choice(form.shapes)
    accent = random.choice(BEAST_ACCENT_TEMPLATES)
    haunt = random.choice(form.haunts)
    motion = random.choice(BEAST_MOTION_TEMPLATES).format(haunt=haunt)
    aura_line = format_affinity_sentence(affinities)
    lead_options = (
        f"A {appearance} {accent}.",
        f"Summons a {appearance} {accent}.",
        f"The martial soul forms a {appearance} {accent}.",
    )
    lead = random.choice(lead_options)
    parts = [lead, motion]
    if aura_line:
        parts.append(aura_line)
    return " ".join(parts)


def tool_description(name: str, grade: int, affinities: Sequence[SpiritualAffinity], form: ToolForm) -> str:
    appearance = random.choice(form.designs)
    finish = random.choice(TOOL_FINISHES)
    detail = random.choice(TOOL_DETAILS)
    gesture = random.choice(TOOL_GESTURES)
    aura_line = format_affinity_sentence(affinities)
    lead_options = (
        f"A {appearance} {finish}.",
        f"Summons a {appearance} {finish}.",
        f"The martial soul manifests as a {appearance} {finish}.",
    )
    lead = random.choice(lead_options)
    parts = [lead, detail, gesture]
    if aura_line:
        parts.append(aura_line)
    return " ".join(parts)


def body_description(name: str, grade: int, affinities: Sequence[SpiritualAffinity], anchor: BodyAnchor) -> str:
    focus_key = anchor.focus.lower()
    intro = random.choice(BODY_INTRO_TEMPLATES).format(focus=focus_key)
    visuals = BODY_VISUALS.get(focus_key, BODY_VISUALS["hands"])
    visual = random.choice(visuals)
    gleam = random.choice(BODY_GLEAMS)
    aura_line = format_affinity_sentence(affinities)
    parts = [intro, visual, gleam]
    if aura_line:
        parts.append(aura_line)
    return " ".join(parts)


def make_name(prefixes: Sequence[str], core: str, grade: int, *, suffixes: Sequence[str]) -> str:
    prefix = random.choice(prefixes)
    suffix_option = random.choice(suffixes)
    pattern = random.choice((
        "{prefix} {core}",
        "{prefix} {core} {suffix}",
        "{prefix} {suffix} {core}",
        "{prefix} {core} of {suffix}",
    ))
    if "{suffix}" in pattern:
        return pattern.format(prefix=prefix, core=core, suffix=suffix_option)
    return pattern.format(prefix=prefix, core=core, suffix=suffix_option)


BEAST_SUFFIXES = (
    "Harbinger",
    "Ward",
    "Nomad",
    "Howl",
    "Sentry",
    "Sage",
    "Envoy",
    "Ranger",
    "Oracle",
    "Marauder",
    "Keeper",
    "Singer",
    "Vanguard",
    "Pilgrim",
    "Ancestor",
    "Voyager",
)


TOOL_SUFFIXES = (
    "Engine",
    "Array",
    "Studio",
    "Codex",
    "Bastion",
    "Workshop",
    "Beacon",
    "Ward",
    "Span",
    "Matrix",
    "Archive",
    "Chorus",
    "Symphony",
    "Mandala",
    "Arsenal",
    "Sanctum",
)


BODY_SUFFIXES = (
    "Pulse",
    "Cycle",
    "Verse",
    "Script",
    "Mantra",
    "Mantle",
    "Diagram",
    "Vein",
    "Rhythm",
    "Echo",
    "Chorus",
    "Atlas",
    "Covenant",
    "Glyph",
    "Oath",
    "Continuum",
)


def generate_entries() -> list[dict[str, str | int | Sequence[str]]]:
    used_names: set[str] = set()
    presets: list[dict[str, str | int | Sequence[str]]] = []
    for grade in range(1, 10):
        count = 0
        while count < 100:
            category = choose_category(grade)
            affinities = choose_affinities(grade)
            if grade <= 3:
                if len(affinities) != 1 or affinities[0].is_mixed:
                    continue
            elif grade <= 6:
                if len(affinities) == 1:
                    if not affinities[0].is_mixed:
                        continue
                elif len(affinities) == 2:
                    if any(affinity.is_mixed for affinity in affinities):
                        continue
                else:
                    continue
            else:
                if len(affinities) == 2:
                    if any(not affinity.is_mixed for affinity in affinities):
                        continue
                elif len(affinities) == 4:
                    if any(affinity.is_mixed for affinity in affinities):
                        continue
                else:
                    continue
            if category is MartialSoulType.BEAST:
                form = random.choice(BEAST_FORMS)
                name = make_name(BEAST_PREFIXES_BY_GRADE[grade], form.creature, grade, suffixes=BEAST_SUFFIXES)
                if name in used_names:
                    continue
                description = beast_description(name, grade, affinities, form)
            elif category is MartialSoulType.TOOL:
                form = random.choice(TOOL_FORMS)
                name = make_name(TOOL_PREFIXES_BY_GRADE[grade], form.name, grade, suffixes=TOOL_SUFFIXES)
                if name in used_names:
                    continue
                description = tool_description(name, grade, affinities, form)
            else:
                form = random.choice(BODY_ANCHORS)
                name = make_name(BODY_PREFIXES_BY_GRADE[grade], form.focus, grade, suffixes=BODY_SUFFIXES)
                if name in used_names:
                    continue
                description = body_description(name, grade, affinities, form)
            used_names.add(name)
            presets.append(
                {
                    "name": name,
                    "grade": grade,
                    "category": category.name,
                    "affinities": [affinity.name for affinity in affinities],
                    "description": description,
                }
            )
            count += 1
    return presets


if __name__ == "__main__":
    entries = generate_entries()
    output_path = Path(__file__).resolve().parents[1] / "data" / "martial_souls.json"
    output_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(entries)} martial souls to {output_path}")
