# Discord Embed Bot

A Discord bot that automatically detects and converts social media links into embeddable formats with multiple options. The bot supports Twitter/X, Instagram, Threads, YouTube, Reddit, and TikTok links, providing users with various embed format choices.

### Documentation (HTML)

This repository includes a **standalone documentation site** as [`index.html`](index.html) (configuration, architecture, security, workflows, **Ctrl+K** search, and in-page highlighting). Open it in a browser locally or host it with GitHub Pages.

| How to view | |
|-------------|---|
| **Locally** | After cloning, open `index.html` in Chrome, Edge, or Firefox (double-click or drag the file into a window). |
| **On GitHub** | From the repo file list, open [`index.html`](https://relishablez.github.io/discord_SocialsEmbedder). GitHub shows the source; for the interactive UI, clone/download and open locally, or use **GitHub Pages** (below). |
| **GitHub Pages** (optional) | Repo **Settings → Pages**: source = default branch, folder **/** (root). The site loads **`index.html`** automatically. Base URL: `https://relishablez.github.io/discord_SocialsEmbedder` (or `.../index.html`). |

## Features

- **Multi-Platform Support**: Automatically detects and processes links from:
  - Twitter/X
  - Instagram
  - Threads
  - YouTube
  - Reddit
  - TikTok

- **Smart Embed Options**: Each platform offers multiple embed format choices
- **Auto-Embed Suppression**: Automatically removes Discord's default embeds from original messages
- **Reaction-Based Feedback**: Users can react with ✅ or ❌ to indicate if the embed worked
- **Mention-Safe Preview Flow**: Intermediate bot preview messages neutralize mentions (shown as code-style `@name`) to avoid duplicate pings
- **Link Anti-Spam Cooldown**: Rapid repeated link drops by the same user trigger a short cooldown
- **Owner Commands**: Server management and invite generation features
- **Slash Commands**: Modern Discord slash command support

## Supported Platforms & Embed Options

### Twitter/X
- All Contents embed
- Text-only embed
- Video-only embed
- Gallery embed (Author + Media without captions)

### Instagram
- KK Instagram (Media Only)
- UU Instagram (Media + Info)

### Threads
- FixThreads embed

### YouTube
- Long Form Link (koutube.com)
- Short Form Link (koutu.be)

### Reddit
- rxddit.com
- old.rxddit.com
- Direct High-Quality Video Source (when available)

### TikTok
- Regular Embed (tnktok.com)
- Direct Embed (d.tnktok.com - no stats clutter)
- Embed with Description (a.tnktok.com - description at top)

## Prerequisites

- Python 3.8 or higher
- Discord Bot Token
- Discord Bot with required permissions (see below)

## Installation

1. **Clone or download this repository**

2. **Install required dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Create a `.env` file** in the bot directory (see `.env.example` for a template):
   ```
   DISCORD_TOKEN=your_bot_token_here
   BOT_OWNER_ID=your_discord_user_id_here
   BEDBOT_CONTROL_TRUSTED_IDS=111111111,222222222
   ```
   `BEDBOT_CONTROL_TRUSTED_IDS` is only needed if you run `bedbotControl.py` (comma‑separated user IDs, no spaces).

4. **Optional — `private_config.py`:** only if you want Python-side fallbacks. **Precedence:** `.env` wins for the token and for `BEDBOT_CONTROL_TRUSTED_IDS`; `.env` wins for `BOT_OWNER_ID` unless it’s missing or `0`, then `private_config` is used. Copy from `private_config.example.py`.

5. **Run the bot:**
   ```bash
   python sizzbedbot_V1.py
   ```

### Publishing `sizzbedbot_V1.py` (e.g. GitHub)

- **Safe to upload:** `sizzbedbot_V1.py` has **no** embedded token or owner ID.
- **Never commit:** `.env`, `private_config.py`, or any file with real tokens/IDs.
- If a bot token was ever pasted in chat, a ticket, or an old commit, **reset the token** in the [Developer Portal](https://discord.com/developers/applications) and update `.env` / `private_config.py`.

### `bedbotControl.py` (optional PM2 helper)

- Uses the same `DISCORD_TOKEN` rules as the main bot (`.env` first, then optional `private_config.py`).
- Set who may run `/start`, `/restart`, `/shutdown` with **`BEDBOT_CONTROL_TRUSTED_IDS`** in `.env` (comma‑separated). Optional fallback: list in `private_config.py`.

## Required Discord Bot Permissions

The bot requires the following permissions:

- ✅ Send Messages
- ✅ Use Slash Commands
- ✅ Add Reactions
- ✅ Read Message History
- ✅ Embed Links
- ✅ Read Messages/View Channels
- ✅ Send Messages in Threads
- ✅ Manage Messages (to suppress embeds on original messages)
- ✅ Attach Files (for Reddit video downloads)

**Permission Integer (combined, excluding “Mention Everyone”):** `277025516608` (or use individual permissions above)

### Required Discord Intents

Enable the following intents in the [Discord Developer Portal](https://discord.com/developers/applications):
- ✅ Message Content Intent (Privileged)
- ✅ Server Members Intent (if needed for server info)

## Usage

### Basic Usage

Simply post a link from any supported platform in a channel where the bot has access. The bot will:

1. Suppress the original message's auto-embed
2. Send a message with embed format options
3. Wait for your reaction to select a format
4. Send the embedded link
5. Allow you to react with ✅ (worked) or ❌ (failed) for feedback

### Commands

#### Public Commands

- `/ping` - Check bot latency and status
- `/help` - Open 3-page help (embeds, management, commands)
- `@bot ping` - Check bot latency (mention-based)
- `@bot help` - Show 3-page help in the channel (mention-based)
- `/info` - Show info about the current server (for non-owners)
- `@bot info` - Show info about the current server (mention-based, redacted to current server only)
- `/invitestatus` - Check invite generation status for the current server
- `@bot invitestatus` - Check invite status (mention-based, current server only)

#### Admin Commands

- `/sync` - Force sync slash commands for the server (Admin only)
- `@bot sync` - Force sync slash commands (mention-based, Admin only)
- `@bot join` - Generate an invite link for the current server (Admin or Bot Owner)

#### Owner Commands

- `/info` - Get server information (Bot Owner only, full details)
  - Usage: `/info` - Shows paginated server list with per-server actions
  - Usage: `/info user <user_id>` - Find servers owned by a user
  - Usage: `/info server <server_id>` - Get info for a specific server
 
- `/join` - Open an interactive menu to generate invites for any server the bot is in (Bot Owner only)

- `/enableinvites` - Enable invite generation for current server (Bot Owner only)
- `/disableinvites` - Disable invite generation for current server (Bot Owner only)
- `/invites` - Show a detailed list of active invites created by the bot (Bot Owner only)
- `@bot invites` - Show a redacted list of active invites in the current channel (Bot Owner only)

**Note:** Owner commands can also be used via mentions where applicable: `@bot info`, `@bot join`, `@bot invites`, etc.

## How It Works

1. **Link Detection**: The bot monitors all messages for links matching supported platforms
2. **Embed Suppression**: When a link is detected, the bot suppresses Discord's default embed on the original message
3. **Format Selection**: The bot sends a message with reaction options for different embed formats
4. **Embed Generation**: After selecting a format, the bot sends the converted link
5. **Feedback System**: Users can react with ✅ (embed worked) or ❌ (embed failed) on the embedded message
   - ✅: Reposts the final converted message (real mentions preserved only at final step)
   - ❌: Reposts the final original message + failed note (real mentions preserved only at final step)

### Mention Behavior (Ping Safety)

- In the **intermediate "You selected..."** preview message, user/role mentions and `@everyone`/`@here` are converted to safe code-style text so they do not ping.
- In the **final repost** (after ✅ or ❌), the bot keeps the original mention text behavior so the message matches what the user actually wrote.

### Link Cooldown Behavior

- If a user posts too many supported links quickly, the bot applies a short cooldown before processing more links from that user.
- This cooldown is separate from management-command rate limits (`/info`, `/join`, etc.).

## Configuration

### Environment Variables

Create a `.env` file with (see `.env.example`):
```
DISCORD_TOKEN=your_bot_token_here
BOT_OWNER_ID=your_discord_user_id_here
BEDBOT_CONTROL_TRUSTED_IDS=111111111,222222222
```
(Omit `BEDBOT_CONTROL_TRUSTED_IDS` if you don’t use `bedbotControl.py`.)

### Bot Owner ID

Prefer `BOT_OWNER_ID` in `.env`. Optional fallback: gitignored `private_config.py` (see step 4).

To find your Discord User ID:
1. Enable Developer Mode in Discord (User Settings → Advanced → Developer Mode)
2. Right-click on your profile → Copy ID

## File Structure

```
bot_twitterEmbeds/
├── sizzbedbot_V1.py           # Main bot file (safe to share if secrets stay out)
├── index.html                 # Interactive HTML docs (open in browser; GitHub Pages default page)
├── private_config.example.py  # Optional local overrides (see README)
├── .env.example               # Template for `.env` (safe to commit)
├── keep_alive.py              # Keep-alive script (for hosting services)
├── requirements.txt           # Python dependencies
├── .env                       # Environment variables (create this; gitignored)
└── README.md                  # This file

```

## Troubleshooting

### Bot doesn't respond to links
- Check that the bot has "Read Messages" and "Send Messages" permissions
- Verify "Message Content Intent" is enabled in Discord Developer Portal
- Ensure the bot is online and not rate-limited

### Slash commands not appearing
- Use `/sync` command (Admin only) to force sync commands
- Wait a few minutes for global command sync (can take up to 1 hour)
- Check that the bot has "Use Slash Commands" permission

### Can't suppress embeds
- Ensure the bot has "Manage Messages" permission
- Check that the bot's role is above the message author's role (if applicable)

### Reddit videos not working
- The bot attempts to fetch the highest quality video URL
- Some Reddit posts may not have direct video links available
- Try using the rxddit.com or old.rxddit.com options instead

## Hosting

The bot includes a `keep_alive.py` file for use with hosting services like Replit or similar platforms. The bot will automatically call `keep_alive()` when started.

For production hosting, consider:
- **VPS**: DigitalOcean, Linode, AWS EC2
- **Cloud Platforms**: Railway, Render, Heroku
- **Dedicated Hosting**: Vultr, Contabo

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Verify all permissions and intents are correctly configured
3. Check bot logs for error messages

## License

This bot is provided as-is for personal use.

## Credits

Created by Sizzle1337

---

**Note**: Make sure to keep your `.env` file secure and never commit it to version control. Add `.env` to your `.gitignore` file.

