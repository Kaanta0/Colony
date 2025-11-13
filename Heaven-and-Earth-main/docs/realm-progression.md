# Cultivation Realm Progression

Heaven & Earth ships with a Xianxia-inspired ladder of cultivation realms that applies to qi, body, and soul paths alike. Each milestone below automatically expands into the five standard phases (Initial → Early → Mid → Late → Peak) when stages are generated.

| Order | Realm Title | Highlights |
| --- | --- | --- |
| 0 | Mortal | Martial training has only begun; qi perception flickers at the edge of awareness. |
| 1 | Qi Condensation | Nebulous essence compresses into a dense core that steadies breath and strike. |
| 2 | Foundation Establishment | Dao pillars lock into place, forging bedrock for every future breakthrough. |
| 3 | Core Formation | A radiant inner star spins at the dantian’s heart, empowering each technique. |
| 4 | Nascent Soul | A nascent avatar of will emerges, coordinating body and spirit as one. |
| 5 | Soul Formation | Consciousness engraves dao runes, forming rivers of intent within the soul sea. |
| 6 | Soul Transformation | The nascent avatar moults into luminous flame, casting new divine authority. |
| 7 | Ascendant | Ascension qi surges upward, prying open hidden heavens for the cultivator’s path. |
| 8 | Illusory Yin | Boundless yin mist coalesces into mirages that enfold enemies within twilight layers. |
| 9 | Corporeal Yang | Brilliant yang furnaces temper the flesh into a blazing, battle-hardened vessel. |
| 10 | Nirvana Scryer | Karma mirrors bloom, allowing glimpses of destiny while tempering dao hearts. |
| 11 | Nirvana Cleanser | Nirvana flames scour impurities, revealing pristine law patterns beneath. |
| 12 | Nirvana Shatterer | Shattered karma reforms under command, granting freedom from prior shackles. |
| 13 | Heaven's Blight | Tribulation poison is refined into weaponised insight that stains hostile heavens. |
| 14 | Nirvana Void | Void tides answer every thought, weaving absence into obedient threads. |
| 15 | Spirit Void | Soul intent roams the void, deploying avatars across distant battlefronts. |
| 16 | Arcane Void | Esoteric dao glyphs carve the void, crafting paradoxes that kneel on command. |
| 17 | Void Tribulant | Each tribulation becomes fuel, forging stormcrowns that stride through calamity. |
| 18 | Half-Heaven Trampling | One foot anchors mortal realms while the other grinds against heavenly barriers. |
| 19 | Heaven Trampling | Dao authority tramples the firmament; heaven and earth alike bend to decree. |

Energy caps (`bot/energy.py`), realm tribulation narratives, and default stage seeds (`bot/game.GameState.ensure_default_stages`) all draw from this ladder. Server administrators can still customise or replace any stage via the `/create_*_realm` commands, but migrations now ensure legacy Soul Land labels map onto these core titles so existing saves continue to load cleanly.

## Step Alignment and Lifespan Benchmarks

To keep Heaven & Earth's progression compatible with Renegade Immortal (Xian Ni) lore, each realm aligns with the Step structure documented for the series. The canonical table distinguishes between full Steps and short "transitional" bridges that prepare a cultivator to break into the next Step. The lifespan guidance below follows those boundaries while preserving the qualitative beats described in `docs/cultivation-steps.md`.

| Order | Realm Title | Step Alignment | Proposed Lifespan Range |
| --- | --- | --- | --- |
| 0 | Mortal | Mortal baseline | 60 – 80 years |
| 1 | Qi Condensation | First Step | 90 – 140 years |
| 2 | Foundation Establishment | First Step | 140 – 220 years |
| 3 | Core Formation | First Step | 220 – 320 years |
| 4 | Nascent Soul | First Step | 320 – 450 years |
| 5 | Soul Formation | First Step | 450 – 650 years |
| 6 | Soul Transformation | First Step | 650 – 850 years |
| 7 | Ascendant | First Step | 850 – 1,100 years |
| 8 | Illusory Yin | First → Second Step Transition | 1,500 – 3,000 years |
| 9 | Corporeal Yang | First → Second Step Transition | 3,000 – 6,000 years |
| 10 | Nirvana Scryer | Second Step | 10,000 – 12,000 years per tribulation cycle |
| 11 | Nirvana Cleanser | Second Step | 12,000 – 15,000 years per cycle |
| 12 | Nirvana Shatterer | Second Step | 15,000 – 18,000 years per cycle |
| 13 | Heaven's Blight | Second → Third Step Transition | 18,000 – 22,000 years per cycle |
| 14 | Nirvana Void | Third Step | 80,000 – 120,000 years (practical immortality) |
| 15 | Spirit Void | Third Step | 120,000 – 200,000 years |
| 16 | Arcane Void | Third Step | 200,000 – 350,000 years |
| 17 | Void Tribulant | Third Step | 350,000 – 500,000 years |
| 18 | Half-Heaven Trampling | Third → Fourth Step Transition | 500,000 – 800,000 years |
| 19 | Heaven Trampling | Fourth Step | Lifespan undefined; existence outside linear time |

**Why transitional ranges jump sharply:** Illusory Yin and Corporeal Yang introduce the origin energy refinements that Xian Ni marks as a bridge between First and Second Step cultivation. Heaven's Blight fulfills a similar role for cultivators preparing to ignite an Essence and open the Void Gate, while Half-Heaven Trampling represents the nine Heaven Trampling Bridges that foreshadow a Fourth Step breakthrough. Each transition therefore spikes longevity compared to the Step before it without yet conferring the full security of the next Step.
