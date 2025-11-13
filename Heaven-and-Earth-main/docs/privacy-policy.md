# Heaven & Earth Privacy Policy

_Last updated: 2024-05-09_

Heaven & Earth is an open-source Discord bot that you self-host. This policy explains what information the bot processes and how that information is stored when you deploy it in your own environment.

## 1. Data Controller
Because Heaven & Earth is self-hosted, the individual or organization running the bot ("the Host") is the data controller. The maintainers of the open-source project do not receive any personal data by default.

## 2. Information We Process
When the bot is active in a Discord server, it processes:

- **Discord user identifiers** (user IDs, usernames, and nicknames) to associate characters with Discord accounts.
- **Server identifiers** (guild IDs and channel IDs) needed to respond to commands in the correct server and channel.
- **Gameplay data** stored in TOML files, such as character statistics, inventory, quest progress, and administrative configuration.
- **Interaction content** (slash command inputs, button clicks, select menus) necessary to execute requested actions.

The bot does **not** intentionally collect email addresses, passwords, payment information, or other sensitive categories of data.

## 3. Storage and Retention
- All data is stored locally on the host machine that runs the bot, under the `data/` and `playerdata/` directories described in the project README.
- Data persists until the host deletes the files or removes the bot from the server.
- Backups created with the provided CLI utilities remain under the host's control.

## 4. Data Sharing
- No gameplay or identifying data is transmitted to third parties by default.
- If the host enables community integrations or manually shares backups, they are responsible for ensuring appropriate safeguards.

## 5. Security
- Hosts should restrict filesystem and network access to the machine that runs the bot.
- TLS-secured connections to Discord are provided by the official Discord API libraries; no additional encryption is performed by the bot.

## 6. Your Rights
Because the bot is operated by independent hosts, users should contact the server administrators to:
- Request a copy of their character data.
- Ask for corrections or deletion of their save files.
- Report misuse or violations of this policy.

## 7. Children's Privacy
The bot is intended for communities that satisfy Discord's minimum age requirements (13+). Hosts are responsible for complying with any additional regional age restrictions.

## 8. Changes to This Policy
Updates will be reflected in the "Last updated" date. Material changes should be communicated by the host to their community.

## 9. Contact
For privacy inquiries, contact the host operating the bot in your server. Repository maintainers can be reached via the contact information on the project's GitHub page but cannot access or modify self-hosted data.
