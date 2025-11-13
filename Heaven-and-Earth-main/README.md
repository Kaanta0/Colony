# Heaven & Earth

> _A Xianxia-inspired cultivation RPG for Discord, powered by slash commands and rich interactive views._

Heaven & Earth turns your Discord server into a cooperative cultivation sect. Players roll unique innate souls, gather qi, refine their bodies and souls, and venture into shared encounters while administrators sculpt the world with a fully in-server content editor. The bot ships with a flexible TOML datastore, making it easy to back up and extend your universe without redeploying code.

## ✨ Highlights

- **Deep character growth** – Cultivation, body refinement, and soul paths advance separately, each affecting the statistics they govern.
- **Party-focused adventures** – Location travel, shared encounters, and cooperative loot keep your sect coordinated.
- **Automatic turn-based combat** – Duels and PvE battles resolve through rich Discord views that surface affinities, resistances, and cooldowns.
- **Weapon resonance** – Martial souls grant bonus damage to skills that match their favoured weapons and elemental affinities.
- **Living economy** – Guild-specific shops, currencies, and loot tables are editable on the fly.
- **Admin worldbuilding suite** – Slash commands create stages, enemies, quests, techniques, items, and more without touching TOML by hand.
- **TOML persistence** – Every guild has isolated world data and player saves stored on disk for easy backup or migration.

## 📁 Repository tour

```
├── bot/                # Core bot package
│   ├── bot.py          # Entry point and Discord client definition
│   ├── cogs/           # Slash command implementations (player, party, combat, economy, admin)
│   ├── game.py         # Combat and progression logic
│   ├── models.py       # Dataclasses describing cultivators, items, traits, etc.
│   └── storage.py      # Async datastore powered by config/storage.toml and migrations/
├── config/             # Persistent storage configuration (see storage.toml)
├── data/
│   ├── static/         # Shared TOML definitions checked into source control
│   └── worlds/         # Per-guild world state (world.toml per guild)
├── migrations/         # Schema migration scripts organised by collection
├── playerdata/         # Player save files (guild scoped TOML directories)
└── README.md
```

## 🚀 Getting started

## 📄 Documentation & resources

- [Cultivation Realm Progression](docs/realm-progression.md)
- [Great Step Cultivation Overview](docs/cultivation-steps.md)
- [Spiritual Affinity Relationships](docs/affinity-relationships.md)
- [Terms of Service](docs/terms-of-service.md)
- [Privacy Policy](docs/privacy-policy.md)
- [Discord Install Link](https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=277025508352&scope=bot%20applications.commands)

Replace `YOUR_CLIENT_ID` with your bot application's client ID before sharing the install link with server administrators. The linked documents outline acceptable use, data handling practices, and expectations for hosts who operate the Heaven & Earth bot.

### Prerequisites

- Python **3.11** or newer (uses the built-in `tomllib` module).
- A Discord bot application with the **Message Content Intent** enabled.
- The [`discord.py`](https://discordpy.readthedocs.io/en/stable/) library (2.3+ recommended).

Install dependencies inside your virtual environment:

```bash
python -m pip install -U discord.py
```

### Configure the bot

The bot is configured entirely via environment variables. Only the token is required; everything else has sensible defaults.

| Variable | Description | Default |
| --- | --- | --- |
| `DISCORD_TOKEN` | **Required.** Discord bot token used to connect to the gateway. | – |
| `CULTIVATION_TICK` | Base cultivation EXP awarded per `/cultivate`. | `25` |
| `COMBAT_EXP_GAIN` | Average combat EXP per encounter. | `25` |
| `SOUL_EXP_GAIN` | Soul cultivation EXP per tick (defaults to `CULTIVATION_TICK`). | `CULTIVATION_TICK` |
| `INNATE_STAT_MIN` | Lowest possible innate stat rolled at registration. | `1` |
| `INNATE_STAT_MAX` | Highest possible innate stat rolled at registration. | `20` |

### Run the bot

```bash
python -m bot.bot
```

At first launch the bot automatically syncs its slash commands with every guild it can reach. Populate your world using the admin commands listed below, or copy existing TOML files into the configured `data/` directory to bootstrap new guilds.

## 🎮 Player experience

Most player interactions live under the `/` command menu and open Discord views for richer interactions. A quick sampling:

| Command | Purpose |
| --- | --- |
| `/register` | Roll innate stats, pick pronouns, and bind yourself to an innate soul. |
| `/profile` or `/main` | Review your cultivation stage, stats, techniques, inventory, and action timers. |
| `/cultivate` | Meditate for cultivation EXP and review martial souls, spirit rings, and affinity resonance (respects cooldowns defined in `bot/constants.py`). |
| `/breakthrough` | Attempt to advance to the next realm milestone. |
| `/skills` & `/evolve` | Inspect techniques and evolve them when conditions are met. |
| `/inventory`, `/reforge`, `/use_item` | Manage equipment, reforging, and consumables with slot limits enforced. |
| `/travel` & `/wander` | Move between configured locations, triggering random encounters and quests. |
| `/duel` & `/engage` | Initiate automatic PvP duels or PvE encounters, leveraging elemental affinities and resistances. |
| `/party create|join|leave|info` | Coordinate sect members into shared parties. |
| `/shop` & `/buy` | Browse the marketplace and spend sect currencies. |
| `/trade` | Negotiate, propose, and confirm trades with other cultivators. |
| `/catalogue` | Browse configured skills, items, traits, quests, and more for your guild. |

## 🛠️ Worldbuilding & administration

The admin cog exposes a comprehensive toolkit for customizing every facet of your sect without editing files manually:

- **Player management**: `/delete_profile` removes dormant cultivators by Discord user.
- **Content creation**: `/create_skill`, `/create_item`, `/create_trait`, `/create_race`, `/create_quest`, `/create_enemy`, `/create_boss`, `/create_npc`, `/create_location`, `/create_shop_item`, `/create_currency`, `/create_title`, `/create_cultivation_technique`.
- **Realm scaffolding**: `/create_qi_realm`, `/create_body_realm`, and `/create_soul_realm` generate all five milestones (Initial → Early → Mid → Late → Peak) for the chosen path in one command.
- **Progression tweaks**: `/grant_skill`, `/revoke_skill`, `/grant_item`, `/revoke_item`, `/grant_cultivation_technique`, `/revoke_cultivation_technique`, `/grant_trait`, `/revoke_trait`, `/grant_title`, `/revoke_title`, `/grant_currency`, `/birth_trait`, `/birth_race`.

Each command uses structured slash command options—no manual payload editing required—and offers autocomplete for cross-referenced entities so you can safely link items to loot tables, quests, or traits.

### Command line helpers

Prefer working from a shell? The repository ships with a lightweight CLI that mirrors the bot’s internal storage expectations. Run `python -m bot.utils` from the project directory to see available actions:

- `python -m bot.utils list` enumerates every guild, the collections it owns, and how many player directories are present.
- `python -m bot.utils validate` (or `lint`) parses each TOML payload and reports structural issues such as malformed files, mismatched keys, or warnings returned by the optional validation layer.
- `python -m bot.utils export-data --guild <id> --output backups/<id>.tar.gz` produces a compressed archive containing world content and referenced player saves for the selected guild(s).
- `python -m bot.utils import-data --input backups/<id>.tar.gz --force` restores an archive into the current `data/` and `playerdata/` directories. Use `--guild` to import a subset when migrating several guilds at once.
- `python -m bot.utils delete-player --guild <id> --user <id> --force` removes a player's save file so they can register again.

For a quick migration between hosts:

1. Run `python -m bot.utils export-data --guild <id> --output backups/<id>.tar.gz` on the source machine.
2. Copy the archive to the destination and unpack it with `python -m bot.utils import-data --input backups/<id>.tar.gz --guild <id>`.
3. Execute `python -m bot.utils validate --guild <id>` before restarting the bot to catch schema or content regressions.

## 💾 Data persistence

The bot stores information using the layout described in `config/storage.toml`:

- **World data** (skills, items, quests, enemies, etc.) lives in a single TOML file per guild: `data/worlds/<guild-id>/world.toml`. Each top-level section mirrors a collection name from the storage configuration.
- **Static definitions** that ship with the repository (optional defaults) live under `data/static/<collection>/*.toml`.
- **Player saves** live under `playerdata/<guild-id>/players/<discord-user-id>.toml`. Additional guild-scoped directories (for backups or custom extensions) may sit alongside the `players/` folder.

Files are written synchronously per operation, and schema versions are tracked via `schema_version.toml` files alongside the data. You can manually seed a new guild by copying an existing `data/worlds/<guild-id>` folder (and associated `playerdata/<guild-id>` directory if desired) before inviting the bot.

## 🧰 Storage configuration & migrations

`config/storage.toml` is the single source of truth for every collection the datastore exposes. Each entry defines:

- the relative `path` that should be used on disk (placeholders such as `{guild_id}` and `{key}` are substituted automatically),
- the serialisation `format` (always `toml`),
- the top-level `section` inside a document (for collections stored in shared files),
- the target schema `version`, and
- the `migration` key that groups collections sharing a migration history.

When the bot starts, `bot.storage.DataStore` reads this configuration, ensures that the on-disk schema version matches the desired version, and automatically executes any missing migrations from `migrations/<migration-key>/`. Each migration module must expose `FROM_VERSION`, `TO_VERSION`, and an `apply(context)` function. The context object exposes helpers for resolving paths and logging progress.

To introduce a new schema change:

1. Increment the `version` value for the relevant collection(s) inside `config/storage.toml`.
2. Add a new script (e.g. `migrations/worlds/0002_add_field.py`) that defines the required constants and performs the transformation.
3. The datastore will run migrations automatically the next time the collection is touched.

Custom content can be stored safely by:

- placing shared, source-controlled data under `data/static/<collection>/`,
- editing per-guild state via the admin commands (or by modifying `data/worlds/<guild-id>/world.toml` directly when the bot is offline), and
- creating guild-specific extensions under `playerdata/<guild-id>/` (adjacent to the `players/` directory) for out-of-band backups or integration data.

## 🤝 Contributing & development tips

- Clone the repository and create a virtual environment for experimentation.
- Run `python -m compileall bot` if you want a quick syntax check before deploying.
- Keep guild-specific content out of version control—only the empty directory scaffolding under `data/` and `playerdata/` is tracked.

Bug reports and pull requests are welcome! Feel free to open an issue describing the feature or balance change you have in mind.

---

May your sect flourish, your loot tables overflow, and your breakthroughs never backfire.
