"""
Optional fallback only — you do NOT need this if everything is in `.env`.

Recommended: put these in `.env` (see `.env.example`):
  DISCORD_TOKEN=...
  BOT_OWNER_ID=...
  BEDBOT_CONTROL_TRUSTED_IDS=123456789,987654321

Precedence (each value is read in this order):
  • Token: `.env` / environment first, then DISCORD_TOKEN here if still empty.
  • Owner ID: `.env` BOT_OWNER_ID first, then BOT_OWNER_ID here if env unset / zero.
  • Control IDs: BEDBOT_CONTROL_TRUSTED_IDS in `.env` first, then list here if env empty.

Copy to private_config.py (gitignored) only if you want local Python overrides.
"""

DISCORD_TOKEN = None

BOT_OWNER_ID = None

BEDBOT_CONTROL_TRUSTED_IDS = []
