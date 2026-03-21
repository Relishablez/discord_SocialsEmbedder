## Changelog

Short notes on what changed. This is meant for humans skimming the repo — not a full security spec.

### Docs

- Interactive HTML documentation lives in **`documentation.html`** (renamed from `bot_spec.html` for clarity). Linked from the top of `README.md`.

### v1.6 – `.env` first

- **Owner ID:** `BOT_OWNER_ID` in `.env` is read before optional `private_config.py`.
- **Control bot IDs:** `BEDBOT_CONTROL_TRUSTED_IDS` in `.env` (comma-separated) is read before optional `private_config.py`.
- Added **`.env.example`** as a safe template to commit.

### v1.5 – Token / control-bot config

- **Bot token:** read from `.env` first; if missing, optionally from `private_config.DISCORD_TOKEN` (still gitignored).
- **`bedbotControl.py`:** no hardcoded Discord user IDs; use `BEDBOT_CONTROL_TRUSTED_IDS` in `.env` or `private_config.py`. Fixed missing `import os` and wired `load_dotenv`.

### v1.4 – Private config file & cleaner Instagram links

- **Owner ID without editing the big bot file**  
  You can put your Discord user ID in `private_config.py` (copy from `private_config.example.py`).  
  If you prefer, you can still use `BOT_OWNER_ID` in `.env` instead. The bot checks the private file first, then the environment.

- **Safer to share the main script**  
  Same `sizzbedbot_V1.py` can live in a public or private repo: keep secrets in `.env` and/or gitignored `private_config.py`.

- **Instagram links**  
  Posts that include tracking bits on the URL (like `?igsh=…`) still get detected. The bot builds clean share links without those extras.  
  Links on the usual Instagram domain and the common mirror domains are recognized the same way.

### v1.3 – Mention-safe previews & link cooldown

- While someone is picking an embed option, the bot’s preview text avoids pinging people extra times (mentions are shown in a safe way).
- The final message after someone picks ✅/❌ still matches what they originally sent.
- If someone spams many supported links very fast, the bot may ask them to pause briefly before processing more.

### v1.2 – Saving settings & env-based owner ID

- Invite-related settings can be saved in a small local JSON file so they survive restarts (where your host allows it).
- Bot owner is configured with `BOT_OWNER_ID` in `.env` (and now optionally `private_config.py` — see v1.4).

### v1.1 – Permissions, safety, and management

- Documented permission bundle updated to match what Discord expects for this bot.
- **Info commands** try to show only what’s appropriate: e.g. channel-visible flows stay on the current server; owner tools can see more when used privately.
- **Invites**: bot-created invites are tracked in a simple way; sensitive bits are not blasted into public channels for everyone to see.
- **Abuse**: management commands and invite creation have cooldowns and caps so one person can’t hammer the bot or mint endless invites. Exact numbers may change between releases.

### Help & docs

- In-Discord help is split into a few short pages (embeds, management, command list) so it’s easier to read than one giant wall of text.
