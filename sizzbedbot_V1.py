# Discord Bot Permissions Required (Bot Developer Portal):
# - Send Messages
# - Use Slash Commands
# - Add Reactions
# - Read Message History
# - Mention Everyone (if needed)
# - Attach Files (for Reddit video downloads)
# - Embed Links
# - Read Messages/View Channels
# - Send Messages in Threads
# - Manage Messages (to suppress embeds on original messages)
# Combined Permission Integer for the above (excluding Mention Everyone): 277025516608

import discord
import re
import os
import time
import asyncio
import json
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from keep_alive import keep_alive
import aiohttp  # Required for making HTTP requests to Reddit's API
# pre-requisites above and/or input in the requirements.txt file
# Load environment variables from .env file


load_dotenv()

# Optional local overrides (owner ID, etc.). Copy private_config.example.py → private_config.py
try:
    import private_config as _private_config  # type: ignore
except ImportError:
    _private_config = None


def _resolve_bot_owner_id():
    """Prefer BOT_OWNER_ID from `.env` / environment; optional fallback: private_config.BOT_OWNER_ID."""
    env_raw = (os.getenv("BOT_OWNER_ID") or "").strip()
    if env_raw and env_raw != "0":
        try:
            return int(env_raw)
        except ValueError:
            print("BOT_OWNER_ID in environment is not a valid integer.")
    if _private_config is not None:
        raw = getattr(_private_config, "BOT_OWNER_ID", None)
        if raw is not None and raw != "" and raw != 0 and raw != "0":
            try:
                return int(raw)
            except (TypeError, ValueError):
                print("private_config.BOT_OWNER_ID is not a valid integer.")
    try:
        return int(env_raw or "0")
    except ValueError:
        return 0


# Define intents and enable privileged ones
intents = discord.Intents.default()
intents.messages = True
intents.reactions = True  # Enable reaction tracking
intents.message_content = True  # Enable message content intent
intents.guilds = True  # Enable guild information access

# Bot setup with the correct intents
bot = commands.Bot(command_prefix="!", intents=intents)

# Safe mentions for all bot messages that might include user content
SAFE_MENTIONS = discord.AllowedMentions.none()

# Bot owner ID: BOT_OWNER_ID in `.env` first; optional fallback private_config.py
BOT_OWNER_ID = _resolve_bot_owner_id()

# State persistence
STATE_FILE = "bot_state.json"

# Server settings - stores which servers have opted in to invite generation
SERVER_SETTINGS = {}  # {guild_id: {"invite_enabled": True/False}}

# Rate limiting and invite abuse tracking
COMMAND_RATE_LIMIT_WINDOW = 10  # seconds
COMMAND_RATE_LIMIT_SHORT_THRESHOLD = 3
COMMAND_RATE_LIMIT_HARD_THRESHOLD = 5
COMMAND_RATE_LIMIT_SHORT_DURATION = 60  # seconds   
COMMAND_RATE_LIMIT_HARD_DURATION = 3600  # seconds (1 hour)

# Separate buckets for view vs write-style management commands
COMMAND_USAGE = {
    "view": {},   # {user_id: [timestamps]}
    "write": {},  # {user_id: [timestamps]}
}
USER_RESTRICTIONS = {
    "view": {},   # {user_id: until_timestamp}
    "write": {},  # {user_id: until_timestamp}
}

NON_OWNER_INVITE_TIMESTAMPS = []  # list of timestamps for non-owner invite creations (global)
INVITE_GLOBAL_RESTRICT_UNTIL = 0  # epoch seconds until which non-owner invites are blocked

# Per-guild non-owner invite tracking
GUILD_INVITE_TIMESTAMPS = {}  # {guild_id: [timestamps]}

# Detailed invite tracking for /invites and DM stats
INVITE_RECORDS = []  # list of dicts: {user_id, guild_id, invite_url, created_at, expires_at, created_by_owner}

# Link processing anti-spam cooldown
LINK_RATE_LIMIT_WINDOW = 20  # seconds
LINK_RATE_LIMIT_THRESHOLD = 4
LINK_RATE_LIMIT_COOLDOWN = 45  # seconds
LINK_USAGE = {}  # {user_id: [timestamps]}
LINK_RESTRICTIONS = {}  # {user_id: until_timestamp}

# Mention token parsing for safe, non-pinging preview messages
USER_MENTION_REGEX = re.compile(r"<@!?(\d+)>")
ROLE_MENTION_REGEX = re.compile(r"<@&(\d+)>")


def format_duration(seconds):
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def load_state():
    """Load persisted server settings and invite records from disk."""
    global SERVER_SETTINGS, INVITE_RECORDS, GUILD_INVITE_TIMESTAMPS
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        SERVER_SETTINGS = data.get("server_settings", {})
        INVITE_RECORDS[:] = data.get("invite_records", [])
        GUILD_INVITE_TIMESTAMPS.update(data.get("guild_invite_timestamps", {}))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Failed to load state from {STATE_FILE}: {e}")


def save_state():
    """Persist server settings and invite records to disk."""
    try:
        data = {
            "server_settings": SERVER_SETTINGS,
            "invite_records": INVITE_RECORDS,
            "guild_invite_timestamps": GUILD_INVITE_TIMESTAMPS,
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Failed to save state to {STATE_FILE}: {e}")


def is_user_rate_limited(user_id, bucket="view"):
    """Return (True, seconds_left) if user is currently rate-limited for a given bucket, otherwise (False, 0)."""
    now = time.time()
    until = USER_RESTRICTIONS.get(bucket, {}).get(user_id)
    if until and now < until:
        return True, int(until - now)
    if until and now >= until:
        USER_RESTRICTIONS[bucket].pop(user_id, None)
    return False, 0


def register_command_usage(user_id, bucket="view"):
    """Record a command use in the given bucket and return 'short', 'hard' or None depending on new restriction level."""
    if is_bot_owner(user_id):
        return None
    now = time.time()
    bucket_map = COMMAND_USAGE.setdefault(bucket, {})
    history = bucket_map.setdefault(user_id, [])
    history[:] = [t for t in history if now - t <= COMMAND_RATE_LIMIT_WINDOW]
    history.append(now)
    if len(history) >= COMMAND_RATE_LIMIT_HARD_THRESHOLD:
        USER_RESTRICTIONS[bucket][user_id] = now + COMMAND_RATE_LIMIT_HARD_DURATION
        return "hard"
    if len(history) >= COMMAND_RATE_LIMIT_SHORT_THRESHOLD:
        USER_RESTRICTIONS[bucket][user_id] = now + COMMAND_RATE_LIMIT_SHORT_DURATION
        return "short"
    return None


def is_user_link_rate_limited(user_id):
    """Return (True, seconds_left) if user is link-rate-limited, otherwise (False, 0)."""
    now = time.time()
    until = LINK_RESTRICTIONS.get(user_id)
    if until and now < until:
        return True, int(until - now)
    if until and now >= until:
        LINK_RESTRICTIONS.pop(user_id, None)
    return False, 0


def register_link_usage(user_id):
    """Track rapid link usage and apply short cooldown for spam bursts."""
    if is_bot_owner(user_id):
        return None
    now = time.time()
    history = LINK_USAGE.setdefault(user_id, [])
    history[:] = [t for t in history if now - t <= LINK_RATE_LIMIT_WINDOW]
    history.append(now)
    if len(history) >= LINK_RATE_LIMIT_THRESHOLD:
        LINK_RESTRICTIONS[user_id] = now + LINK_RATE_LIMIT_COOLDOWN
        return "cooldown"
    return None


def sanitize_mentions_for_preview(content, guild=None):
    """Convert mention tokens to non-pinging, code-style labels for interim bot messages."""
    sanitized = content.replace("@everyone", "`@everyone`").replace("@here", "`@here`")

    def replace_user(match):
        user_id = int(match.group(1))
        member = guild.get_member(user_id) if guild else None
        user_obj = member or bot.get_user(user_id)
        label = member.display_name if member else (user_obj.name if user_obj else f"user-{user_id}")
        return f"`@{label}`"

    def replace_role(match):
        role_id = int(match.group(1))
        role = guild.get_role(role_id) if guild else None
        label = role.name if role else f"role-{role_id}"
        return f"`@{label}`"

    sanitized = USER_MENTION_REGEX.sub(replace_user, sanitized)
    sanitized = ROLE_MENTION_REGEX.sub(replace_role, sanitized)
    return sanitized


def can_create_invite(user_id, guild_id=None):
    """Check global and per-guild invite abuse restriction for non-owners."""
    global INVITE_GLOBAL_RESTRICT_UNTIL
    if is_bot_owner(user_id):
        return True
    now = time.time()
    if INVITE_GLOBAL_RESTRICT_UNTIL and now < INVITE_GLOBAL_RESTRICT_UNTIL:
        return False
    if guild_id is not None and not is_bot_owner(user_id):
        timestamps = GUILD_INVITE_TIMESTAMPS.get(guild_id, [])
        timestamps = [t for t in timestamps if now - t <= 3600]
        GUILD_INVITE_TIMESTAMPS[guild_id] = timestamps
        if len(timestamps) >= 3:
            return False
    return True


def register_invite_creation(user_id, guild_id):
    """Track non-owner invite creations globally and per-guild, and apply cooldowns if needed."""
    global INVITE_GLOBAL_RESTRICT_UNTIL, NON_OWNER_INVITE_TIMESTAMPS, GUILD_INVITE_TIMESTAMPS
    if is_bot_owner(user_id):
        return
    now = time.time()
    NON_OWNER_INVITE_TIMESTAMPS = [t for t in NON_OWNER_INVITE_TIMESTAMPS if now - t <= 3600]
    NON_OWNER_INVITE_TIMESTAMPS.append(now)
    if len(NON_OWNER_INVITE_TIMESTAMPS) >= 3:
        INVITE_GLOBAL_RESTRICT_UNTIL = now + 3600

    # Per-guild tracking
    guild_history = GUILD_INVITE_TIMESTAMPS.setdefault(guild_id, [])
    guild_history[:] = [t for t in guild_history if now - t <= 3600]
    guild_history.append(now)


def register_detailed_invite(user_id, guild_id, invite_url, max_age, created_by_owner):
    """Store detailed info about an invite created by the bot."""
    now = time.time()
    INVITE_RECORDS.append(
        {
            "user_id": user_id,
            "guild_id": guild_id,
            "invite_url": invite_url,
            "created_at": now,
            "expires_at": now + max_age,
            "created_by_owner": created_by_owner,
        }
    )
    save_state()


def get_user_invite_stats(user_id):
    """Return (active_count, latest_expires_at_or_none) for a user."""
    now = time.time()
    active = [r for r in INVITE_RECORDS if r["user_id"] == user_id and r["expires_at"] > now]
    if not active:
        return 0, None
    latest_expires = max(r["expires_at"] for r in active)
    return len(active), latest_expires


async def generate_managed_invite(guild: discord.Guild, requested_by: discord.abc.User, reason: str):
    """
    Central helper to create a 3-day, single-use invite for a guild, track it,
    and compute per-user statistics. Returns (invite, invite_channel, stats_text).
    """
    # Find a suitable channel
    invite_channel = None
    for channel in guild.channels:
        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
            if channel.permissions_for(guild.me).create_instant_invite:
                invite_channel = channel
                break

    if not invite_channel:
        raise discord.Forbidden(invite_channel, "No suitable channel for creating invites.")

    max_age = 259200  # 3 days
    invite = await invite_channel.create_invite(
        max_age=max_age,
        max_uses=1,
        reason=reason
    )

    register_invite_creation(requested_by.id, guild.id)
    register_detailed_invite(
        user_id=requested_by.id,
        guild_id=guild.id,
        invite_url=invite.url,
        max_age=max_age,
        created_by_owner=is_bot_owner(requested_by.id),
    )

    active_count, latest_expires = get_user_invite_stats(requested_by.id)
    remaining = latest_expires - time.time() if latest_expires else max_age
    stats_text = f"You currently have **{active_count}** active invite(s). Latest invite expires in **{format_duration(remaining)}**."

    return invite, invite_channel, stats_text


def clear_invite_restrictions():
    """Clear global invite abuse restrictions (used by bot owner enable command)."""
    global INVITE_GLOBAL_RESTRICT_UNTIL, NON_OWNER_INVITE_TIMESTAMPS, GUILD_INVITE_TIMESTAMPS
    INVITE_GLOBAL_RESTRICT_UNTIL = 0
    NON_OWNER_INVITE_TIMESTAMPS = []
    GUILD_INVITE_TIMESTAMPS = {}
    save_state()

# Get the bot token: environment first (good for hosting), then optional private_config.py
def get_bot_token():
    token = (os.getenv("DISCORD_TOKEN") or "").strip()
    if not token and _private_config is not None:
        raw = getattr(_private_config, "DISCORD_TOKEN", None)
        if raw is not None and str(raw).strip():
            token = str(raw).strip()
    if not token:
        print(
            "Bot token not found. Set DISCORD_TOKEN in your `.env` file "
            "(optional fallback: private_config.py — see private_config.example.py)."
        )
        return None
    return token

# Check if user is bot owner
def is_bot_owner(user_id):
    return user_id == BOT_OWNER_ID

# Check if server has opted in to invite generation
def is_invite_enabled(guild_id):
    return SERVER_SETTINGS.get(guild_id, {}).get("invite_enabled", False)

# Enable/disable invite generation for a server
def set_invite_enabled(guild_id, enabled):
    if guild_id not in SERVER_SETTINGS:
        SERVER_SETTINGS[guild_id] = {}
    SERVER_SETTINGS[guild_id]["invite_enabled"] = enabled
    save_state()

# Regex patterns for supported platforms
TWITTER_LINK_REGEX = r'https?://(?:www\.)?twitter\.com(/[\w\d_]+/status/(\d+))?'
X_LINK_REGEX = r'https?://(?:www\.)?x\.com(/[\w\d_]+/status/(\d+))?'
# Instagram + common mirror hosts; optional ?query (e.g. ?igsh=…) matched but not kept in rebuilt links
INSTAGRAM_LINK_REGEX = re.compile(
    r"https?://(?:www\.)?(?:instagram\.com|kkinstagram\.com|uuinstagram\.com)"
    r"/(p|reel)/([\w-]+)"
    r"(?:[/?#][^\s]*)?",
    re.IGNORECASE,
)
THREADS_LINK_REGEX = r'https?://(?:www\.)?threads\.net/[\w\d@._/-]+'
# Updated Regex to also match m.youtube.com links
YOUTUBE_MAIN_REGEX = r'https?://(?:www\.)?(?:m\.)?youtube\.com/[\w\d?=&/.-]+'
YOUTUBE_SHORT_REGEX = r'https?://(?:www\.)?youtu\.be/[\w\d_-]+'

REDDIT_LINK_REGEX = r'https?://(?:www\.)?reddit\.com/[\w\d?=&/.-]+'
TIKTOK_LINK_REGEX = r'https?://(?:www\.)?(?:vm\.)?tiktok\.com/[\w\d?=&/.-]+'

# Reaction timeout (seconds) before bot deletes its own message when no one reacts
REACTION_TIMEOUT = 60.0


def delete_in_timestamp(seconds=None):
    """Return Discord relative timestamp <t:epoch:R> for 'delete in X seconds' (client shows live countdown, no edits)."""
    sec = int(seconds) if seconds is not None else int(REACTION_TIMEOUT)
    return f"<t:{int(time.time()) + sec}:R>"


# Helper function to handle common wave reaction logic
async def handle_wave_reaction(message, response_message, user_reaction):
    if str(user_reaction.emoji) == '👋':
        await message.add_reaction('👋')  # Add wave emoji to the original message
        await response_message.delete()  # Remove the bot's response
        return True
    return False

# Helper function to handle reactions on embedded messages (✅, ❌)
async def handle_embedded_message_reactions(embedded_message, original_author, original_message_content, original_link, new_content, channel):
    """Handle reactions on the embedded message (✅, ❌)"""
    def check(reaction, user):
        return (user == original_author and 
                str(reaction.emoji) in ['✅', '❌'] and 
                reaction.message.id == embedded_message.id)
    
    reaction_received = False

    try:
        reaction, user = await bot.wait_for("reaction_add", timeout=REACTION_TIMEOUT, check=check)
        reaction_received = True
        reaction_emoji = str(reaction.emoji)
        
        # Delete the embedded message
        try:
            await embedded_message.delete()
        except (discord.errors.NotFound, discord.errors.Forbidden):
            pass
        
        if reaction_emoji == '✅':
            # Embed worked - repost message with the embedded link (no status line)
            await channel.send(
                f"{original_author.display_name} posted:\n"
                f"{new_content}"
            )
        
        elif reaction_emoji == '❌':
            # Embed failed - repost original message with unsuccessful note
            await channel.send(
                f"{original_author.display_name} posted:\n"
                f"{original_message_content}\n"
                f"-# Embed unsuccessful"
            )
    
    except asyncio.TimeoutError:
        # No reaction within timeout - assume it worked and delete the message
        if not reaction_received:
            try:
                await embedded_message.delete()
            except (discord.errors.NotFound, discord.errors.Forbidden):
                pass
    except Exception as e:
        print(f"Error handling embedded message reactions: {e}")

@bot.event
async def on_ready():
    # Load persisted state once bot is ready
    load_state()
    print(f"Main bot logged in as {bot.user}")
    try:
        await bot.tree.sync()
        print("Slash commands synced successfully!")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")

# Slash command for ping
@bot.tree.command(name="ping", description="Check bot latency and status")
async def ping(interaction: discord.Interaction):
    """Slash command to check bot ping and status."""
    print(f"[{interaction.created_at.strftime('%Y-%m-%d %H:%M:%S')}] ({interaction.user.name}#{interaction.user.discriminator}) used '/ping' slash command in server [{interaction.guild.name if interaction.guild else 'DM'}]")
    
    latency = round(bot.latency * 1000)  # Convert latency to milliseconds
    embed = discord.Embed(
        title="L Ping, W Pong! 🏓",
        description=f"Latency: {latency}ms",
        color=0x00FF00  # Green color
    )
    embed.set_footer(text="Sizzle1337 is my Daddy")
    await interaction.response.send_message(embed=embed)

# Slash command for help
@bot.tree.command(name="help", description="Show bot help and supported platforms")
async def help_command(interaction: discord.Interaction):
    """Slash command to show bot help."""
    print(f"[{interaction.created_at.strftime('%Y-%m-%d %H:%M:%S')}] ({interaction.user.name} | ID: {interaction.user.id}) used '/help' slash command in server [{interaction.guild.name if interaction.guild else 'DM'}]")
    
    view = HelpView()
    embed = view.get_page_embed()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# Bot Owner Commands
# Pagination view for server list
class ServerListView(discord.ui.View):
    def __init__(self, servers, timeout=300):
        super().__init__(timeout=timeout)
        self.servers = list(servers)
        self.current_page = 0
        self.servers_per_page = 3
        self.max_pages = (len(self.servers) + self.servers_per_page - 1) // self.servers_per_page
        self.create_buttons()
        
    def create_buttons(self):
        """Create navigation and server buttons dynamically"""
        # Clear existing buttons
        self.clear_items()
        
        # Add navigation buttons
        prev_button = discord.ui.Button(label="◀️", style=discord.ButtonStyle.secondary, disabled=self.current_page == 0)
        next_button = discord.ui.Button(label="▶️", style=discord.ButtonStyle.secondary, disabled=self.current_page >= self.max_pages - 1)
        
        prev_button.callback = self.previous_page
        next_button.callback = self.next_page
        
        self.add_item(prev_button)
        self.add_item(next_button)
        
        # Add server buttons for current page
        start_idx = self.current_page * self.servers_per_page
        end_idx = min(start_idx + self.servers_per_page, len(self.servers))
        servers_on_page = end_idx - start_idx
        
        for i in range(servers_on_page):
            button = discord.ui.Button(
                label=f"{i + 1}️⃣", 
                style=discord.ButtonStyle.primary
            )
            button.callback = self.create_server_callback(i)
            self.add_item(button)
        
    def create_server_callback(self, relative_index):
        """Create a callback function for a server button"""
        async def server_callback(interaction: discord.Interaction):
            await self.show_server_info(interaction, relative_index)
        return server_callback
        
    def get_page_embed(self):
        start_idx = self.current_page * self.servers_per_page
        end_idx = min(start_idx + self.servers_per_page, len(self.servers))
        page_servers = self.servers[start_idx:end_idx]
        
        embed = discord.Embed(
            title="🤖 Bot Server List",
            description=f"Bot is currently in **{len(self.servers)}** server(s)\nPage {self.current_page + 1}/{self.max_pages}",
            color=0x00FF00
        )
        
        for i, guild in enumerate(page_servers, start_idx + 1):
            member_count = guild.member_count if guild.member_count else "Unknown"
            
            # Get owner information with better handling
            if guild.owner:
                owner_info = f"{guild.owner.mention} (`{guild.owner.name}#{guild.owner.discriminator}`)"
            else:
                try:
                    owner_id = guild.owner_id
                    if owner_id:
                        owner_info = f"ID: `{owner_id}` (Name not available)"
                    else:
                        owner_info = "Unknown"
                except:
                    owner_info = "Unknown"
            
            # Get invite status for this server
            invite_status = "✅ Enabled" if is_invite_enabled(guild.id) else "❌ Disabled"
            
            embed.add_field(
                name=f"{i}. {guild.name}",
                value=f"ID: `{guild.id}`\nMembers: {member_count}\nOwner: {owner_info}\nInvites: {invite_status}",
                inline=False
            )
        
        embed.set_footer(text="Use buttons to navigate • Click server number for detailed info")
        return embed
    
    async def previous_page(self, interaction: discord.Interaction):
        if self.current_page > 0:
            self.current_page -= 1
            self.create_buttons()
            await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
        else:
            await interaction.response.defer()
    
    async def next_page(self, interaction: discord.Interaction):
        if self.current_page < self.max_pages - 1:
            self.current_page += 1
            self.create_buttons()
            await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
        else:
            await interaction.response.defer()
    
    async def show_server_info(self, interaction: discord.Interaction, relative_index: int):
        server_index = self.current_page * self.servers_per_page + relative_index
        if server_index < len(self.servers):
            guild = self.servers[server_index]
            embed = create_server_info_embed(guild)
            view = ServerInfoView(guild, interaction.user)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.defer()
    
    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        await self.message.edit(view=self)


class HelpView(discord.ui.View):
    def __init__(self, timeout=300):
        super().__init__(timeout=timeout)
        self.current_page = 0
        self.max_pages = 3

    def get_page_embed(self):
        if self.current_page == 0:
            embed = discord.Embed(
                title="Help • Page 1/3 • Embeds",
                description=(
                    "**Main Use:** Drop a supported link and pick how you want it embedded.\n\n"
                    "- **Twitter/X**: All content, text-only, video-only, or gallery embeds.\n"
                    "- **Instagram**: KK (media only) or UU (media + info).\n"
                    "- **Threads**: FixThreads-style embeds.\n"
                    "- **YouTube**: Long (`koutube.com`) or short (`koutu.be`) links.\n"
                    "- **Reddit**: rxddit/old.rxddit or direct high-quality video (when available).\n"
                    "- **TikTok**: Regular, direct, or description-focused embeds."
                ),
                color=0x3498DB
            )
        elif self.current_page == 1:
            embed = discord.Embed(
                title="Help • Page 2/3 • Management",
                description=(
                    "**Management & Safety Features**\n\n"
                    "- Per-server invite enable/disable handled by bot owner.\n"
                    "- Invite creation restricted to bot owner or server admins.\n"
                    "- Info views are redacted to the current server when visible to a whole channel.\n"
                    "- Abuse protection: spammy command usage and excessive invites are automatically cooled down."
                ),
                color=0x3498DB
            )
        else:
            embed = discord.Embed(
                title="Help • Page 3/3 • Commands",
                description=(
                    "**Public Commands**\n"
                    "- `/ping`, `@bot ping`\n"
                    "- `/help`, `@bot help`\n"
                    "- `/invitestatus`, `@bot invitestatus`\n"
                    "- `/info` (current server only for non-owners), `@bot info` (current server only)\n\n"
                    "**Admin Only**\n"
                    "- `/sync`, `@bot sync`\n"
                    "- `@bot join` (invite creation for current server)\n\n"
                    "**Bot Owner Only**\n"
                    "- `/info` with lookup options across servers\n"
                    "- `/join` (interactive invite menu across servers)\n"
                    "- `/enableinvites`, `/disableinvites`, `@bot enableinvites`, `@bot disableinvites`\n"
                    "- `/invites`, `@bot invites`"
                ),
                color=0x3498DB
            )
        embed.set_footer(text="Use ◀️ / ▶️ to switch help pages • Sizzle1337 is my Father")
        return embed

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.max_pages - 1:
            self.current_page += 1
        await interaction.response.edit_message(embed=self.get_page_embed(), view=self)

class ServerInfoView(discord.ui.View):
    def __init__(self, guild, requester, timeout=300):
        super().__init__(timeout=timeout)
        self.guild = guild
        self.requester = requester

    @discord.ui.button(label="Enable Invites", style=discord.ButtonStyle.success)
    async def enable_invites_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_bot_owner(interaction.user.id):
            await interaction.response.send_message("❌ Only the bot owner can enable invites.", ephemeral=True)
            return
        set_invite_enabled(self.guild.id, True)
        clear_invite_restrictions()
        await interaction.response.send_message(f"✅ Invite generation enabled for **{self.guild.name}**", ephemeral=True)

    @discord.ui.button(label="Disable Invites", style=discord.ButtonStyle.danger)
    async def disable_invites_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_bot_owner(interaction.user.id):
            await interaction.response.send_message("❌ Only the bot owner can disable invites.", ephemeral=True)
            return
        set_invite_enabled(self.guild.id, False)
        await interaction.response.send_message(f"❌ Invite generation disabled for **{self.guild.name}**", ephemeral=True)

    @discord.ui.button(label="Create Invite", style=discord.ButtonStyle.primary)
    async def create_invite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not (is_bot_owner(interaction.user.id) or interaction.user.guild_permissions.administrator):
            await interaction.response.send_message("❌ Only the bot owner or a server administrator can create invites.", ephemeral=True)
            return

        if not is_invite_enabled(self.guild.id):
            await interaction.response.send_message(f"❌ Invite generation is **disabled** for **{self.guild.name}**.", ephemeral=True)
            return

        if not can_create_invite(interaction.user.id, self.guild.id):
            await interaction.response.send_message("❌ Invite creation is temporarily disabled due to excessive usage. Please try again later or contact the bot owner.", ephemeral=True)
            return

        try:
            invite, invite_channel, stats_text = await generate_managed_invite(
                self.guild,
                interaction.user,
                reason=f"Invite requested via server info by {interaction.user}",
            )

            # Send DM with invite link
            dm_failed = False
            try:
                await interaction.user.send(
                    f"Here is your invite link for **{self.guild.name}**:\n{invite.url}\n\n{stats_text}"
                )
            except Exception:
                dm_failed = True

            # Owner: also post in channel; others: ephemeral confirmation only
            embed = discord.Embed(
                title="🔗 Server Invite Generated",
                description=(
                    f"**Server:** {self.guild.name}\n"
                    f"**Channel:** {invite_channel.name}\n"
                    f"**Invite Link:** {invite.url if is_bot_owner(interaction.user.id) else '*Sent via DM*'}\n\n"
                    f"{stats_text}"
                ),
                color=0x00FF00
            )
            embed.set_footer(text="This invite expires in 3 days and can only be used once")

            if is_bot_owner(interaction.user.id):
                await interaction.response.send_message(embed=embed, ephemeral=False)
            else:
                notice = "✅ Invite link has been sent to your DMs."
                if dm_failed:
                    notice = "⚠️ Could not DM you the invite link. Please enable DMs from this server."
                await interaction.response.send_message(content=notice, embed=embed, ephemeral=True)

        except discord.Forbidden:
            await interaction.response.send_message(f"❌ No permission to create invite for **{self.guild.name}**.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error creating invite: {str(e)}", ephemeral=True)


@bot.tree.command(name="info", description="Get server information")
async def info_command(interaction: discord.Interaction, lookup_type: str = None, lookup_value: str = None):
    """Get server information with flexible lookup options."""
    limited, seconds_left = is_user_rate_limited(interaction.user.id, bucket="view")
    if limited:
        await interaction.response.send_message(f"❌ You are temporarily rate-limited from using management commands. Try again in {seconds_left} seconds.", ephemeral=True)
        return
    register_command_usage(interaction.user.id, bucket="view")
    print(f"[{interaction.created_at.strftime('%Y-%m-%d %H:%M:%S')}] ({interaction.user.name} | ID: {interaction.user.id}) used '/info' slash command in server [{interaction.guild.name if interaction.guild else 'DM'}]")
    is_owner = is_bot_owner(interaction.user.id)

    # Non-owners: always show only current server info, ignore lookup arguments
    if not is_owner:
        if interaction.guild is None:
            await interaction.response.send_message("❌ This command can only be used inside a server.", ephemeral=True)
            return
        embed = create_server_info_embed(interaction.guild)
        view = ServerInfoView(interaction.guild, interaction.user)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return

    # Owner: allow full multi-server lookups (ephemeral)
    servers = list(bot.guilds)
    if not servers:
        await interaction.response.send_message("❌ Bot is not in any servers.", ephemeral=True)
        return

    # If no arguments provided, show paginated server list
    if not lookup_type or not lookup_value:
        view = ServerListView(servers)
        embed = view.get_page_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return

    # Handle different lookup types for owner only
    if lookup_type.lower() == "user":
        try:
            user_id = int(lookup_value)
            matching_servers = [g for g in servers if g.owner_id == user_id]
            if not matching_servers:
                await interaction.response.send_message(f"❌ No servers found where user `{user_id}` is the owner.", ephemeral=True)
                return
            elif len(matching_servers) == 1:
                embed = create_server_info_embed(matching_servers[0])
                view = ServerInfoView(matching_servers[0], interaction.user)
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            else:
                embed = discord.Embed(
                    title=f"🔍 Servers owned by user {user_id}",
                    description=f"Found **{len(matching_servers)}** server(s):",
                    color=0x3498DB
                )
                for i, guild in enumerate(matching_servers, 1):
                    embed.add_field(
                        name=f"{i}. {guild.name}",
                        value=f"ID: `{guild.id}`\nMembers: {guild.member_count}",
                        inline=False
                    )
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid user ID. Please provide a numeric user ID.", ephemeral=True)

    elif lookup_type.lower() == "server":
        try:
            server_id = int(lookup_value)
            matching_server = next((g for g in servers if g.id == server_id), None)
            if not matching_server:
                await interaction.response.send_message(f"❌ Server `{server_id}` not found. Bot is not in this server.", ephemeral=True)
                return
            embed = create_server_info_embed(matching_server)
            view = ServerInfoView(matching_server, interaction.user)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Invalid server ID. Please provide a numeric server ID.", ephemeral=True)

    else:
        await interaction.response.send_message("❌ Invalid lookup type. Use `user` or `server`.", ephemeral=True)

class JoinServerView(discord.ui.View):
    def __init__(self, servers, requester, timeout=300):
        super().__init__(timeout=timeout)
        self.servers = list(servers)
        self.requester = requester
        self.current_page = 0
        self.servers_per_page = 3
        self.max_pages = (len(self.servers) + self.servers_per_page - 1) // self.servers_per_page or 1
        self.create_buttons()

    def create_buttons(self):
        self.clear_items()

        prev_button = discord.ui.Button(label="◀️", style=discord.ButtonStyle.secondary, disabled=self.current_page == 0)
        next_button = discord.ui.Button(label="▶️", style=discord.ButtonStyle.secondary, disabled=self.current_page >= self.max_pages - 1)
        prev_button.callback = self.previous_page
        next_button.callback = self.next_page
        self.add_item(prev_button)
        self.add_item(next_button)

        start_idx = self.current_page * self.servers_per_page
        end_idx = min(start_idx + self.servers_per_page, len(self.servers))
        for idx in range(start_idx, end_idx):
            guild = self.servers[idx]
            button = discord.ui.Button(
                label=f"{idx + 1}. {guild.name[:50]}",
                style=discord.ButtonStyle.primary
            )

            async def make_callback(interaction: discord.Interaction, g=guild):
                await self.create_invite_for_guild(interaction, g)

            button.callback = make_callback
            self.add_item(button)

    async def previous_page(self, interaction: discord.Interaction):
        if self.current_page > 0:
            self.current_page -= 1
            self.create_buttons()
            await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
        else:
            await interaction.response.defer()

    async def next_page(self, interaction: discord.Interaction):
        if self.current_page < self.max_pages - 1:
            self.current_page += 1
            self.create_buttons()
            await interaction.response.edit_message(embed=self.get_page_embed(), view=self)
        else:
            await interaction.response.defer()

    def get_page_embed(self):
        start_idx = self.current_page * self.servers_per_page
        end_idx = min(start_idx + self.servers_per_page, len(self.servers))
        page_servers = self.servers[start_idx:end_idx]

        desc_lines = [
            "Select a server below to generate an invite link.\n",
            "_Invite links are DMed to you and last for **3 days** (single use)._",
            ""
        ]
        for i, guild in enumerate(page_servers, start=start_idx + 1):
            desc_lines.append(f"**{i}. {guild.name}** — ID: `{guild.id}` • Members: {guild.member_count}")

        embed = discord.Embed(
            title="🔗 Generate Server Invite",
            description="\n".join(desc_lines),
            color=0x00FF00
        )
        return embed

    async def create_invite_for_guild(self, interaction: discord.Interaction, guild: discord.Guild):
        if not is_bot_owner(interaction.user.id):
            await interaction.response.send_message("❌ Only the bot owner can generate cross-server invites via this menu.", ephemeral=True)
            return

        limited, seconds_left = is_user_rate_limited(interaction.user.id)
        if limited:
            await interaction.response.send_message(f"❌ You are temporarily rate-limited from using management commands. Try again in {seconds_left} seconds.", ephemeral=True)
            return
        register_command_usage(interaction.user.id)

        if not can_create_invite(interaction.user.id, guild.id):
            await interaction.response.send_message("❌ Invite creation is temporarily disabled due to excessive usage. Please try again later or contact the bot owner.", ephemeral=True)
            return

        if not is_invite_enabled(guild.id):
            await interaction.response.send_message(f"❌ Invite generation is **disabled** for **{guild.name}**. Use `/enableinvites` in that server to enable it.", ephemeral=True)
            return

        try:
            invite, invite_channel, stats_text = await generate_managed_invite(
                guild,
                interaction.user,
                reason=f"Invite requested via /join by {interaction.user}",
            )

            dm_failed = False
            try:
                await interaction.user.send(
                    f"Here is your invite link for **{guild.name}**:\n{invite.url}\n\n{stats_text}"
                )
            except Exception:
                dm_failed = True

            channel_notice = f"🔗 Created invite for **{guild.name}** and sent it to your DMs.\n{stats_text}"
            if dm_failed:
                channel_notice = (
                    f"⚠️ Created invite for **{guild.name}**, but could not DM you. "
                    f"Here is the link:\n{invite.url}\n\n{stats_text}"
                )

            await interaction.response.send_message(channel_notice, ephemeral=False)

        except discord.Forbidden:
            await interaction.response.send_message(f"❌ No permission to create invite for **{guild.name}**.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error creating invite: {str(e)}", ephemeral=True)


@bot.tree.command(name="join", description="[OWNER ONLY] Open invite menu for all servers")
async def join_server(interaction: discord.Interaction):
    """Open an interactive menu to generate invites for any server the bot is in (bot owner only)."""
    if not is_bot_owner(interaction.user.id):
        await interaction.response.send_message("❌ Only the bot owner can use this command.", ephemeral=True)
        return

    print(f"[{interaction.created_at.strftime('%Y-%m-%d %H:%M:%S')}] ({interaction.user.name} | ID: {interaction.user.id}) used '/join' slash command in server [{interaction.guild.name if interaction.guild else 'DM'}]")

    servers = list(bot.guilds)
    if not servers:
        await interaction.response.send_message("❌ Bot is not in any servers.", ephemeral=True)
        return

    view = JoinServerView(servers, interaction.user)
    embed = view.get_page_embed()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# Helper function to create server info embed
def create_server_info_embed(guild):
    """Create a detailed server info embed for a guild."""
    # Get bot's permissions in this server
    bot_member = guild.get_member(bot.user.id)
    permissions = bot_member.guild_permissions if bot_member else discord.Permissions.none()
    
    # Count channels by type
    text_channels = len([c for c in guild.channels if isinstance(c, discord.TextChannel)])
    voice_channels = len([c for c in guild.channels if isinstance(c, discord.VoiceChannel)])
    categories = len([c for c in guild.channels if isinstance(c, discord.CategoryChannel)])
    
    # Get owner information with better handling
    if guild.owner:
        owner_info = f"{guild.owner.mention} (`{guild.owner.name}#{guild.owner.discriminator}`)"
    else:
        try:
            owner_id = guild.owner_id
            if owner_id:
                owner_info = f"ID: `{owner_id}` (Name not available)"
            else:
                owner_info = "Unknown"
        except:
            owner_info = "Unknown"
    
    embed = discord.Embed(
        title=f"📊 Server Info: {guild.name}",
        color=0x3498DB
    )
    
    embed.add_field(name="🆔 Server ID", value=f"`{guild.id}`", inline=True)
    embed.add_field(name="👑 Owner", value=owner_info, inline=True)
    embed.add_field(name="👥 Members", value=f"{guild.member_count}", inline=True)
    
    embed.add_field(name="📅 Created", value=f"<t:{int(guild.created_at.timestamp())}:F>", inline=True)
    embed.add_field(name="🤖 Bot Joined", value=f"<t:{int(bot_member.joined_at.timestamp())}:F>" if bot_member else "Unknown", inline=True)
    embed.add_field(name="📈 Boost Level", value=f"Level {guild.premium_tier} ({guild.premium_subscription_count} boosts)", inline=True)
    
    embed.add_field(name="📝 Channels", value=f"Text: {text_channels}\nVoice: {voice_channels}\nCategories: {categories}", inline=True)
    embed.add_field(name="🔒 Verification", value=guild.verification_level.name, inline=True)
    embed.add_field(name="😀 Emojis", value=f"{len(guild.emojis)}", inline=True)
    
    # Add invite status
    invite_status = "✅ Enabled" if is_invite_enabled(guild.id) else "❌ Disabled"
    embed.add_field(name="🔗 Invite Generation", value=invite_status, inline=True)
    
    # Bot permissions
    key_permissions = [
        "send_messages", "read_messages", "add_reactions", 
        "embed_links", "attach_files", "use_external_emojis"
    ]
    bot_perms = [perm for perm in key_permissions if getattr(permissions, perm)]
    
    embed.add_field(
        name="🤖 Bot Permissions", 
        value="✅ " + ", ".join(bot_perms) if bot_perms else "❌ No key permissions", 
        inline=False
    )
    
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    
    return embed

# Manual sync command for troubleshooting
@bot.tree.command(name="sync", description="[ADMIN ONLY] Force sync slash commands for this server")
async def sync_commands(interaction: discord.Interaction):
    """Force sync slash commands - server admin only."""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ You need Administrator permissions to use this command.", ephemeral=True)
        return
    
    print(f"[{interaction.created_at.strftime('%Y-%m-%d %H:%M:%S')}] ({interaction.user.name} | ID: {interaction.user.id}) used '/sync' slash command in server [{interaction.guild.name if interaction.guild else 'DM'}]")
    
    try:
        # Try guild-specific sync first
        synced = await bot.tree.sync(guild=interaction.guild)
        await interaction.response.send_message(f"✅ Successfully synced {len(synced)} slash commands to this server!", ephemeral=True)
        print(f"Manually synced {len(synced)} slash commands to guild {interaction.guild.name}")
    except Exception as e:
        try:
            # Fallback to global sync
            synced = await bot.tree.sync()
            await interaction.response.send_message(f"✅ Successfully synced {len(synced)} slash commands globally!", ephemeral=True)
            print(f"Manually synced {len(synced)} slash commands globally")
        except Exception as e2:
            await interaction.response.send_message(f"❌ Failed to sync commands: {str(e2)}", ephemeral=True)
            print(f"Manual sync failed: {e2}")


@bot.tree.command(name="enableinvites", description="[OWNER ONLY] Enable invite generation for this server")
async def enable_invites(interaction: discord.Interaction):
    """Enable invite generation for this server - owner only."""
    if not is_bot_owner(interaction.user.id):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    limited, seconds_left = is_user_rate_limited(interaction.user.id, bucket="write")
    if limited:
        await interaction.response.send_message(f"❌ You are temporarily rate-limited from using management commands. Try again in {seconds_left} seconds.", ephemeral=True)
        return
    register_command_usage(interaction.user.id, bucket="write")
    clear_invite_restrictions()
    print(f"[{interaction.created_at.strftime('%Y-%m-%d %H:%M:%S')}] ({interaction.user.name} | ID: {interaction.user.id}) used '/enableinvites' slash command in server [{interaction.guild.name if interaction.guild else 'DM'}]")
    
    set_invite_enabled(interaction.guild.id, True)
    await interaction.response.send_message(f"✅ Invite generation enabled for **{interaction.guild.name}**", ephemeral=True)
    print(f"Invite generation enabled for server: {interaction.guild.name} (ID: {interaction.guild.id})")

@bot.tree.command(name="disableinvites", description="[OWNER ONLY] Disable invite generation for this server")
async def disable_invites(interaction: discord.Interaction):
    """Disable invite generation for this server - owner only."""
    if not is_bot_owner(interaction.user.id):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
        return

    limited, seconds_left = is_user_rate_limited(interaction.user.id, bucket="write")
    if limited:
        await interaction.response.send_message(f"❌ You are temporarily rate-limited from using management commands. Try again in {seconds_left} seconds.", ephemeral=True)
        return
    register_command_usage(interaction.user.id, bucket="write")
    
    print(f"[{interaction.created_at.strftime('%Y-%m-%d %H:%M:%S')}] ({interaction.user.name} | ID: {interaction.user.id}) used '/disableinvites' slash command in server [{interaction.guild.name if interaction.guild else 'DM'}]")
    
    set_invite_enabled(interaction.guild.id, False)
    await interaction.response.send_message(f"❌ Invite generation disabled for **{interaction.guild.name}**", ephemeral=True)
    print(f"Invite generation disabled for server: {interaction.guild.name} (ID: {interaction.guild.id})")

@bot.tree.command(name="invitestatus", description="Check invite generation status for this server")
async def invite_status(interaction: discord.Interaction):
    """Check invite generation status for this server."""
    limited, seconds_left = is_user_rate_limited(interaction.user.id, bucket="view")
    if limited:
        await interaction.response.send_message(f"❌ You are temporarily rate-limited from using management commands. Try again in {seconds_left} seconds.", ephemeral=True)
        return
    register_command_usage(interaction.user.id, bucket="view")

    print(f"[{interaction.created_at.strftime('%Y-%m-%d %H:%M:%S')}] ({interaction.user.name} | ID: {interaction.user.id}) used '/invitestatus' slash command in server [{interaction.guild.name if interaction.guild else 'DM'}]")
    
    enabled = is_invite_enabled(interaction.guild.id)
    status = "✅ Enabled" if enabled else "❌ Disabled"
    await interaction.response.send_message(f"Invite generation status for **{interaction.guild.name}**: {status}", ephemeral=True)


@bot.tree.command(name="invites", description="[OWNER ONLY] List active invites created by the bot")
async def list_invites(interaction: discord.Interaction):
    """Show a summary of all active invites created via the bot (owner only, full details)."""
    if not is_bot_owner(interaction.user.id):
        await interaction.response.send_message("❌ Only the bot owner can view the invite audit log.", ephemeral=True)
        return

    now = time.time()
    active = [r for r in INVITE_RECORDS if r["expires_at"] > now]
    if not active:
        await interaction.response.send_message("There are currently no active invites created by the bot.", ephemeral=True)
        return

    # Sort by expiry soonest first
    active.sort(key=lambda r: r["expires_at"])

    lines = []
    for r in active[:25]:
        guild = bot.get_guild(r["guild_id"])
        guild_name = guild.name if guild else f"Unknown ({r['guild_id']})"
        user = bot.get_user(r["user_id"])
        user_label = f"{user} ({r['user_id']})" if user else f"User ID {r['user_id']}"
        remaining = r["expires_at"] - now
        lines.append(
            f"- **Server:** {guild_name} (`{r['guild_id']}`)\n"
            f"  **User:** {user_label}\n"
            f"  **Link:** {r['invite_url']}\n"
            f"  **Expires in:** {format_duration(remaining)}\n"
        )

    embed = discord.Embed(
        title="📜 Active Bot-Created Invites",
        description="\n".join(lines),
        color=0x00FF00
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    # Ignore messages that contain "You selected:" - these are bot-generated messages
    if "You selected:" in message.content:
        return

    # Bot mention and "ping" handler
    if bot.user.mentioned_in(message):
        # Remove the mention from content to get the actual command
        content = message.content.lower()
        # Remove bot mention patterns
        for mention_pattern in [f"<@{bot.user.id}>", f"<@!{bot.user.id}>"]:
            content = content.replace(mention_pattern.lower(), "").strip()
        
        # Handle "ping"
        if content == "ping":
            print(f"[{message.created_at.strftime('%Y-%m-%d %H:%M:%S')}] ({message.author.name}| ID: {message.author.id}) used '@bot ping' command in server [{message.guild.name if message.guild else 'DM'}]")
            latency = round(bot.latency * 1000)  # Convert latency to milliseconds
            embed = discord.Embed(
                title="L Ping, W Pong! 🏓",
                description=f"Latency: {latency}ms",
                color=0x00FF00  # Green color
            )
            embed.set_footer(text="Sizzle1337 is my Daddy")
            await message.reply(embed=embed, mention_author=True, allowed_mentions=SAFE_MENTIONS)
            return

        # Handle "help" - paginated help when content is just "help" after removing mention
        if content == "help":
            print(f"[{message.created_at.strftime('%Y-%m-%d %H:%M:%S')}] ({message.author.name}| ID: {message.author.id}) used '@bot help' command in server [{message.guild.name if message.guild else 'DM'}]")
            view = HelpView()
            embed = view.get_page_embed()
            await message.reply(embed=embed, view=view, mention_author=True, allowed_mentions=SAFE_MENTIONS)
            return

        # Handle "sync" command for non-owners (admin only)
        if content == "sync":
            print(f"[{message.created_at.strftime('%Y-%m-%d %H:%M:%S')}] ({message.author.name}| ID: {message.author.id}) used '@bot sync' command in server [{message.guild.name if message.guild else 'DM'}]")
            if not message.author.guild_permissions.administrator:
                await message.reply("❌ You need Administrator permissions to use this command.", mention_author=True, allowed_mentions=SAFE_MENTIONS)
                return
            
            try:
                synced = await bot.tree.sync(guild=message.guild)
                await message.reply(f"✅ Successfully synced {len(synced)} slash commands to this server!", mention_author=True, allowed_mentions=SAFE_MENTIONS)
                print(f"Manually synced {len(synced)} slash commands to guild {message.guild.name} via mention")
            except Exception as e:
                try:
                    synced = await bot.tree.sync()
                    await message.reply(f"✅ Successfully synced {len(synced)} slash commands globally!", mention_author=True, allowed_mentions=SAFE_MENTIONS)
                    print(f"Manually synced {len(synced)} slash commands globally via mention")
                except Exception as e2:
                    await message.reply(f"❌ Failed to sync commands: {str(e2)}", mention_author=True, allowed_mentions=SAFE_MENTIONS)
                    print(f"Manual sync failed via mention: {e2}")
            return

        # Handle "info" via mention - always redacted to current server only
        if content.startswith("info") and message.guild:
            limited, seconds_left = is_user_rate_limited(message.author.id, bucket="view")
            if limited:
                await message.reply(f"❌ You are temporarily rate-limited from using management commands. Try again in {seconds_left} seconds.", mention_author=True, allowed_mentions=SAFE_MENTIONS)
                return
            register_command_usage(message.author.id, bucket="view")
            print(f"[{message.created_at.strftime('%Y-%m-%d %H:%M:%S')}] ({message.author.name}| ID: {message.author.id}) used '@bot info' command in server [{message.guild.name if message.guild else 'DM'}]")
            embed = create_server_info_embed(message.guild)
            view = ServerInfoView(message.guild, message.author)
            await message.reply(embed=embed, view=view, mention_author=True, allowed_mentions=SAFE_MENTIONS)
            return

        # Handle "join" via mention for current server only
        if content.startswith("join") and message.guild:
            if not (is_bot_owner(message.author.id) or message.author.guild_permissions.administrator):
                await message.reply("❌ Only the bot owner or a server administrator can create invites.", mention_author=True, allowed_mentions=SAFE_MENTIONS)
                return

            limited, seconds_left = is_user_rate_limited(message.author.id, bucket="write")
            if limited:
                await message.reply(f"❌ You are temporarily rate-limited from using management commands. Try again in {seconds_left} seconds.", mention_author=True, allowed_mentions=SAFE_MENTIONS)
                return
            register_command_usage(message.author.id, bucket="write")

            if not can_create_invite(message.author.id, message.guild.id):
                await message.reply("❌ Invite creation is temporarily disabled due to excessive usage. Please try again later or contact the bot owner.", mention_author=True, allowed_mentions=SAFE_MENTIONS)
                return

            target_guild = message.guild

            if not is_invite_enabled(target_guild.id):
                await message.reply(f"❌ Invite generation is **disabled** for **{target_guild.name}**. Use `@bot enableinvites` in that server to enable it.", mention_author=True, allowed_mentions=SAFE_MENTIONS)
                return

            try:
                invite, invite_channel, stats_text = await generate_managed_invite(
                    target_guild,
                    message.author,
                    reason=f"Invite requested via mention by {message.author}",
                )

                dm_failed = False
                try:
                    await message.author.send(
                        f"Here is your invite link for **{target_guild.name}**:\n{invite.url}\n\n{stats_text}"
                    )
                except Exception:
                    dm_failed = True

                desc_link = invite.url if is_bot_owner(message.author.id) else "*Sent via DM*"
                embed = discord.Embed(
                    title="🔗 Server Invite Generated",
                    description=(
                        f"**Server:** {target_guild.name}\n"
                        f"**Channel:** {invite_channel.name}\n"
                        f"**Invite Link:** {desc_link}\n\n"
                        f"{stats_text}"
                    ),
                    color=0x00FF00
                )
                embed.set_footer(text="This invite expires in 3 days and can only be used once")

                notice = "🔗 Created an invite and sent it to your DMs."
                if dm_failed:
                    notice = (
                        "⚠️ Created an invite but could not DM you. "
                        f"Here is the link:\n{invite.url}"
                    )
                await message.reply(f"{notice}", embed=embed, mention_author=True, allowed_mentions=SAFE_MENTIONS)

            except discord.Forbidden:
                await message.reply(f"❌ No permission to create invite for **{target_guild.name}**.", mention_author=True)
            except Exception as e:
                await message.reply(f"❌ Error creating invite: {str(e)}", mention_author=True)
            return

        # Handle "enableinvites" command (mention) - owner only
        if content == "enableinvites" and message.guild:
            if not is_bot_owner(message.author.id):
                await message.reply("❌ Only the bot owner can enable invites.", mention_author=True, allowed_mentions=SAFE_MENTIONS)
                return
            limited, seconds_left = is_user_rate_limited(message.author.id, bucket="write")
            if limited:
                await message.reply(f"❌ You are temporarily rate-limited from using management commands. Try again in {seconds_left} seconds.", mention_author=True, allowed_mentions=SAFE_MENTIONS)
                return
            register_command_usage(message.author.id, bucket="write")
            print(f"[{message.created_at.strftime('%Y-%m-%d %H:%M:%S')}] ({message.author.name}| ID: {message.author.id}) used '@bot enableinvites' command in server [{message.guild.name if message.guild else 'DM'}]")
            set_invite_enabled(message.guild.id, True)
            clear_invite_restrictions()
            await message.reply(f"✅ Invite generation enabled for **{message.guild.name}**", mention_author=True, allowed_mentions=SAFE_MENTIONS)
            return

        # Handle "disableinvites" command (mention) - owner only
        if content == "disableinvites" and message.guild:
            if not is_bot_owner(message.author.id):
                await message.reply("❌ Only the bot owner can disable invites.", mention_author=True, allowed_mentions=SAFE_MENTIONS)
                return
            limited, seconds_left = is_user_rate_limited(message.author.id, bucket="write")
            if limited:
                await message.reply(f"❌ You are temporarily rate-limited from using management commands. Try again in {seconds_left} seconds.", mention_author=True, allowed_mentions=SAFE_MENTIONS)
                return
            register_command_usage(message.author.id, bucket="write")
            print(f"[{message.created_at.strftime('%Y-%m-%d %H:%M:%S')}] ({message.author.name}| ID: {message.author.id}) used '@bot disableinvites' command in server [{message.guild.name if message.guild else 'DM'}]")
            set_invite_enabled(message.guild.id, False)
            await message.reply(f"❌ Invite generation disabled for **{message.guild.name}**", mention_author=True, allowed_mentions=SAFE_MENTIONS)
            return

        # Handle "invitestatus" command (mention) - anyone, current server only
        if content == "invitestatus" and message.guild:
            limited, seconds_left = is_user_rate_limited(message.author.id, bucket="view")
            if limited:
                await message.reply(f"❌ You are temporarily rate-limited from using management commands. Try again in {seconds_left} seconds.", mention_author=True, allowed_mentions=SAFE_MENTIONS)
                return
            register_command_usage(message.author.id, bucket="view")
            print(f"[{message.created_at.strftime('%Y-%m-%d %H:%M:%S')}] ({message.author.name}| ID: {message.author.id}) used '@bot invitestatus' command in server [{message.guild.name if message.guild else 'DM'}]")
            enabled = is_invite_enabled(message.guild.id)
            status = "✅ Enabled" if enabled else "❌ Disabled"
            await message.reply(f"Invite generation status for **{message.guild.name}**: {status}", mention_author=True, allowed_mentions=SAFE_MENTIONS)
            return

        # Handle "invites" command (mention) - owner only, redacted
        if content == "invites":
            if not is_bot_owner(message.author.id):
                await message.reply("❌ Only the bot owner can view the invite audit log.", mention_author=True, allowed_mentions=SAFE_MENTIONS)
                return

            now = time.time()
            active = [r for r in INVITE_RECORDS if r["expires_at"] > now]
            if not active:
                await message.reply("There are currently no active invites created by the bot.", mention_author=True, allowed_mentions=SAFE_MENTIONS)
                return

            # Sort by expiry soonest first
            active.sort(key=lambda r: r["expires_at"])

            lines = []
            for r in active[:20]:
                guild = bot.get_guild(r["guild_id"])
                guild_name = guild.name if guild else f"Unknown ({r['guild_id']})"
                user = bot.get_user(r["user_id"])
                if user:
                    redacted_name = f"{user.name[0]}***#{user.discriminator}"
                else:
                    redacted_name = "User#****"
                user_id_tail = str(r["user_id"])[-4:]
                remaining = r["expires_at"] - now
                lines.append(
                    f"- **Server:** {guild_name}\n"
                    f"  **User:** {redacted_name} (ID …{user_id_tail})\n"
                    f"  **Link:** *(redacted in public channel)*\n"
                    f"  **Expires in:** {format_duration(remaining)}\n"
                )

            embed = discord.Embed(
                title="📜 Active Bot-Created Invites (Redacted)",
                description="\n".join(lines),
                color=0x00FF00
            )
            await message.reply(embed=embed, mention_author=True, allowed_mentions=SAFE_MENTIONS)
            return


    # Platform handling logic
    has_supported_link = any([
        re.search(twitter_x_combined_regex := r'https?://(?:www\.)?(?:twitter\.com|x\.com)(/[\w\d_]+/status/(\d+))?', message.content),
        INSTAGRAM_LINK_REGEX.search(message.content),
        re.search(THREADS_LINK_REGEX, message.content),
        re.search(youtube_combined_regex := r'https?://(?:www\.)?(?:m\.)?(?:youtube\.com/[\w\d?=&/.-]+|youtu\.be/[\w\d_-]+)', message.content),
        re.search(TIKTOK_LINK_REGEX, message.content),
        re.search(REDDIT_LINK_REGEX, message.content),
    ])

    if has_supported_link:
        limited, seconds_left = is_user_link_rate_limited(message.author.id)
        if limited:
            await message.reply(
                f"⏳ Slow down a bit — you're sending links too quickly. Try again in {seconds_left} seconds.",
                mention_author=False,
                allowed_mentions=SAFE_MENTIONS
            )
            return
        register_link_usage(message.author.id)

    # Platform handling logic
    async def process_link(platform_name, description, options, color, link_transformer, original_link):
        # Store original message content
        original_message_content = message.content
        safe_preview_content = sanitize_mentions_for_preview(original_message_content, message.guild)
        # Discord <t:epoch:R> shows live "in X seconds" countdown (no message edits = no rate limit)
        description_initial = description.replace("a few seconds", delete_in_timestamp())

        # Create and send the embed with options
        embed = discord.Embed(
            title=f"Choose {platform_name} Embed Format",
            description=description_initial,
            color=color
        )
        embed.set_footer(text="Sizzle1337 is my Father")
        try:
            response_message = await message.reply(embed=embed, mention_author=False)
        except discord.errors.DiscordException as e:
            print(f"Failed to send message: {e}")
            return  # Exit if the message couldn't be sent

        # Add reactions for each option
        reactions = list(options.keys()) + ['👋']
        for reaction in reactions:
            await response_message.add_reaction(reaction)

        def check(reaction, user):
            return user == message.author and str(reaction.emoji) in reactions and reaction.message.id == response_message.id

        try:
            # Wait for a reaction
            reaction, user = await bot.wait_for("reaction_add", timeout=REACTION_TIMEOUT, check=check)

            # Handle the wave reaction
            if await handle_wave_reaction(message, response_message, reaction):
                return

            # Get the selected label and link
            selected_emoji = str(reaction.emoji)
            selected_label = options[selected_emoji]
            selected_link = link_transformer(selected_emoji)[1]

            # Delete the original message
            try:
                await message.delete()
            except (discord.errors.NotFound, discord.errors.Forbidden) as e:
                print(f"Could not delete original message: {e}")

            # Replace the original link with the embed link in the content
            new_content = original_message_content.replace(original_link, selected_link)
            safe_new_content = safe_preview_content.replace(original_link, selected_link)
            
            # Post combined message with user's content and bot's instructions
            combined_message = (
                f"{message.author.display_name} posted:\n"
                f"{safe_new_content}\n\n"
                f"**You selected:** {selected_emoji}\n"
                f"*React with ✅ if embed preview worked, ❌ if it failed. Pick nothing and I'll assume it did not work. \n This message will disappear {delete_in_timestamp()}*"
            )
            embedded_message = await message.channel.send(combined_message, allowed_mentions=SAFE_MENTIONS)
            
            # Add ✅ and ❌ reactions to the embedded message
            for emoji in ['✅', '❌']:
                try:
                    await embedded_message.add_reaction(emoji)
                except Exception as e:
                    print(f"Error adding reaction {emoji}: {e}")
            
            # Start handling reactions on the embedded message (non-blocking)
            bot.loop.create_task(
                handle_embedded_message_reactions(
                    embedded_message,
                    message.author,
                    original_message_content,
                    original_link,
                    new_content,
                    message.channel,
                )
            )

        except asyncio.TimeoutError:
            # Silently handle timeout - message will self-destruct
            pass

        finally:
            # Safely attempt to delete the response message
            try:
                await response_message.delete()
            except discord.errors.NotFound:
                print("The response message was not found and could not be deleted.")
            except discord.errors.Forbidden:
                print("The bot doesn't have permission to delete the message.")






    # Twitter/X handling - use combined regex to avoid duplicate matches
    twitter_x_match = re.search(twitter_x_combined_regex, message.content)

    if twitter_x_match:
        # Get the original link from the message
        original_link = twitter_x_match.group(0)
        
        # Determine the base URL and username/status from the matched link
        is_twitter = "twitter.com" in original_link
        base_url = "fxtwitter.com" if is_twitter else "fixupx.com"
        # Group 1 contains the path part (/username/status/123) or None if not present
        username_status = twitter_x_match.group(1) or ""
        full_url = f"https://{base_url}{username_status}"

        # Debug print to verify the user who posted the link and the original link
        print(f"{message.author} posted Twitter/X link: {full_url}")

        # Proceed with processing the link
        await process_link(
            "Twitter/X",
            (
                "Which Twitter/X format do you want to see?\n\n"
                "1️⃣ - All Contents\n"
                "2️⃣ - Only Text\n"
                "3️⃣ - Only Video\n"
                "4️⃣ - Gallery (Author + Media without captions)\n"
                "👋 - Select this to ignore and get rid of this message, or don't select anything and this message will self destruct in a few seconds"
            ),
            {
                '1️⃣': "All Contents",
                '2️⃣': "Only Text",
                '3️⃣': "Only Video",
                '4️⃣': "Gallery"
            },
            0x1DA1F2,
            lambda reaction: (
                reaction,
                full_url.replace(base_url, 
                                 "fxtwitter.com" if reaction == "1️⃣" else 
                                 "t.fxtwitter.com" if reaction == "2️⃣" else 
                                 "d.fxtwitter.com" if reaction == "3️⃣" else 
                                 "g.fxtwitter.com" if reaction == "4️⃣" else "")
            ),
            original_link
        )

        # Debug print to verify the final processed URL after replacing the base URL
        print(f"Processed Twitter/X link: {full_url}")

        return


    # Instagram handling
    instagram_match = INSTAGRAM_LINK_REGEX.search(message.content)
    if instagram_match:
        original_link = instagram_match.group(0)
        content_type, content_id = instagram_match.groups()
        # Canonical path only — strips tracking query params (e.g. igsh=) from generated mirror URLs
        original_path = f"{content_type}/{content_id}"

        # Debug print to verify the user and the original Instagram link
        print(f"{message.author} posted Instagram link: https://www.instagram.com/{original_path}")

        await process_link(
            "Instagram",
            (
                "Which Instagram format do you want to see?\n\n"
                "1️⃣ - KK Instagram (Media Only)\n"
                "2️⃣ - UU Instagram (Media + Info)\n"
                "👋 - Select this to ignore and get rid of this message, or don't select anything and this message will self destruct in a few seconds"
            ),
            {
                '1️⃣': "KK Instagram (Media Only)",
                '2️⃣': "UU Instagram (Media + Info)"
            },
            0xE1306C,  # Instagram's pink color
            lambda reaction: (
                reaction,
                f"https://{'kkinstagram.com' if reaction == '1️⃣' else 'uuinstagram.com'}/{original_path}"
            ),
            original_link
        )

        # Debug print to verify the final processed Instagram link
        print(f"Processed Instagram link: https://instagram.com/{original_path}")
        
        return

    # Threads handling
    threads_match = re.search(THREADS_LINK_REGEX, message.content)
    if threads_match:
        original_link = threads_match.group(0)

        # Debug print to verify the user and the original Threads link
        print(f"{message.author} posted Threads link: {original_link}")

        await process_link(
            "Threads",
            (
                "Embed Threads link:\n\n"
                "1️⃣ - Prepend 'fix' to the Threads link\n"
                "👋 - Select this to ignore and get rid of this message, or don't select anything and this message will self destruct in a few seconds"
            ),
            {
                '1️⃣': "Prepend 'fix'"
            },
            0x9933CC,  # Threads color
            lambda reaction: (reaction, original_link.replace("threads", "fixthreads", 1)),
            original_link
        )

        # Debug print to verify the final processed Threads link
        print(f"Processed Threads link: {original_link.replace('threads', 'fixthreads', 1)}")
        
        return


    # YouTube handling - use combined regex to avoid duplicate matches
    youtube_match = re.search(youtube_combined_regex, message.content)

    if youtube_match:
        # Get the original link from the message (before any processing)
        original_link = youtube_match.group(0)
        
        # Check if it's from the "m.youtube.com" or "youtu.be" format
        processed_link = original_link

        # Debug print to verify the original link and the user who posted it
        print(f"{message.author} posted YouTube link: {original_link}")

        # Check if the URL contains 'm.youtube.com' and replace 'm.' with ''
        if "m.youtube.com" in processed_link:
            processed_link = processed_link.replace("m.youtube.com", "youtube.com")

        # If it's youtube.com (not mobile) - change it to koutube.com
        if "youtube.com" in processed_link:
            processed_link = processed_link.replace("youtube.com", "koutube.com")

        # If it's a shortened link (youtu.be) - change it to koutu.be
        elif "youtu.be" in processed_link:
            processed_link = processed_link.replace("youtu.be", "koutu.be")

        # Debug print to verify the final processed link
        print(f"Processed YouTube link: {processed_link}")

        # Proceed with processing the link
        await process_link(
            "YouTube",
            (
                "Which YouTube embed format do you want to see?\n\n"
                "1️⃣ - Long Form Link (`koutube.com`)\n"
                "2️⃣ - Short Form Link (`koutu.be`)\n"
                "👋 - Select this to ignore and get rid of this message, or don't select anything and this message will self destruct in a few seconds"
            ),
            {
                '1️⃣': "Long Form Link",
                '2️⃣': "Short Form Link"
            },
            0xFF0000,
            lambda reaction: (reaction, processed_link),
            original_link
        )
        return

    # TikTok handling
    tiktok_match = re.search(TIKTOK_LINK_REGEX, message.content)
    if tiktok_match:
        original_link = tiktok_match.group(0)
        
        # Debug print to verify the original link and the user who posted it
        print(f"{message.author} posted TikTok link: {original_link}")
        
        # Extract the path from the original TikTok URL
        # TikTok URLs can be: https://www.tiktok.com/@user/video/1234567890 or https://vm.tiktok.com/xxxxx
        # We need to preserve the full path after tiktok.com
        url_parts = original_link.split('tiktok.com', 1)
        if len(url_parts) > 1:
            tiktok_path = url_parts[1]
        else:
            tiktok_path = ''
        
        # Debug print to verify the final processed link
        print(f"TikTok path: {tiktok_path}")
        
        # Proceed with processing the link
        await process_link(
            "TikTok",
            (
                "Which TikTok embed format do you want to see?\n\n"
                "1️⃣ - Regular Embed (`tnktok.com`)\n"
                "2️⃣ - Direct Embed (`d.tnktok.com` - no stats clutter)\n"
                "3️⃣ - Embed with Description (`a.tnktok.com` - description at top)\n"
                "👋 - Select this to ignore and get rid of this message, or don't select anything and this message will self destruct in a few seconds"
            ),
            {
                '1️⃣': "Regular Embed",
                '2️⃣': "Direct Embed",
                '3️⃣': "Embed with Description"
            },
            0x000000,  # TikTok's black color
            lambda reaction: (
                reaction,
                f"https://{'tnktok.com' if reaction == '1️⃣' else 'd.tnktok.com' if reaction == '2️⃣' else 'a.tnktok.com'}{tiktok_path}"
            ),
            original_link
        )
        return

    
    # Reddit handling for direct video embedding and options
    reddit_match = re.search(REDDIT_LINK_REGEX, message.content)
    if reddit_match:
        original_link = reddit_match.group(0)

        # Debug print to verify the original Reddit link and the user who posted it
        print(f"{message.author} posted Reddit link: {original_link}")

        async def resolve_reddit_url(short_url):
            """Resolve shortened Reddit URLs to their canonical form."""
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(short_url, allow_redirects=True) as response:
                        if response.status == 200:
                            resolved_url = str(response.url)  # Get the redirected URL
                            # Debug print to verify the resolved Reddit URL
                            print(f"Resolved Reddit URL: {resolved_url}")
                            return resolved_url
            except Exception as e:
                print(f"Error resolving Reddit URL: {e}")
            return short_url  # Fallback to the original URL

        async def get_highest_quality_video(reddit_url):
            """Fetch the highest-quality video URL from Reddit's HTML or JSON."""
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36",
                        "Referer": reddit_url,
                    }

                    # Step 1: Check Reddit's JSON API
                    api_url = f"{reddit_url.rstrip('/')}/.json"
                    async with session.get(api_url, headers=headers, allow_redirects=True) as json_response:
                        if json_response.status == 200:
                            try:
                                data = await json_response.json()
                                post_data = data[0]['data']['children'][0]['data']

                                # Streamable URL from secure_media
                                secure_media = post_data.get("secure_media", {})
                                if secure_media:
                                    streamable_url = secure_media.get("oembed", {}).get("url")
                                    if streamable_url:
                                        print(f"Found streamable URL: {streamable_url}")
                                        return streamable_url

                                # Reddit-hosted fallback URL
                                fallback_url = secure_media.get("reddit_video", {}).get("fallback_url")
                                if fallback_url:
                                    print(f"Found fallback URL: {fallback_url}")
                                    return fallback_url

                            except Exception as json_error:
                                print(f"JSON Parsing Error: {json_error}")
                                print("Falling back to HTML parsing...")

                    # Step 2: Fallback to HTML Parsing
                    async with session.get(reddit_url, headers=headers) as html_response:
                        if html_response.status == 200:
                            html = await html_response.text()

                            # Streamable direct video link extraction
                            streamable_match = re.search(r'<source src="(https://[^"]+streamable[^"]+\.mp4[^"]*)"', html)
                            if streamable_match:
                                streamable_url = streamable_match.group(1)
                                print(f"Found streamable video URL: {streamable_url}")
                                return streamable_url

                            # Reddit-hosted packaged-media fallback
                            packaged_media_match = re.search(r'packaged-media-json="([^"]+)"', html)
                            if packaged_media_match:
                                import json
                                from html import unescape

                                packaged_media_json = packaged_media_match.group(1).replace("&quot;", '"')
                                packaged_media = json.loads(packaged_media_json)

                                highest_quality_url = None
                                max_height = 0
                                for permutation in packaged_media.get("playbackMp4s", {}).get("permutations", []):
                                    source = permutation.get("source", {})
                                    url = source.get("url", "")
                                    height = source.get("dimensions", {}).get("height", 0)

                                    if height > max_height:
                                        highest_quality_url = unescape(url)
                                        max_height = height

                                if highest_quality_url:
                                    print(f"Found highest quality video URL: {highest_quality_url}")
                                    return highest_quality_url

            except Exception as e:
                print(f"Error fetching video URL: {e}")
            return None

        # Store original message content
        original_message_content = message.content
        safe_preview_content = sanitize_mentions_for_preview(original_message_content, message.guild)

        # Resolve the canonical URL and fetch the highest-quality video link
        canonical_url = await resolve_reddit_url(original_link)
        print(f"Canonical Reddit URL: {canonical_url}")  # Debug print to verify canonical URL
        highest_quality_video_url = await get_highest_quality_video(canonical_url)

        # Dynamically prepare options
        options = {
            '1️⃣': "rxddit.com",
            '2️⃣': "old.rxddit.com",
        }
        if highest_quality_video_url:
            options['3️⃣'] = "Direct High-Quality Video Source"

        description_lines = [
            "Choose a Reddit format:\n",
            "1️⃣ - rxddit.com",
            "2️⃣ - old.rxddit.com",
        ]
        if highest_quality_video_url:
            description_lines.append("3️⃣ - Direct High-Quality Video Source")
        description_lines.append(f"👋 - Select this to ignore and get rid of this message, or don't select anything and this message will self destruct {delete_in_timestamp()}")
        description = "\n".join(description_lines)

        # Create and send the embed
        embed = discord.Embed(
            title="Choose Reddit Format",
            description=description,
            color=0xFF5700
        )
        embed.set_footer(text="Sizzle1337 is my Father")
        response_message = await message.reply(embed=embed, mention_author=False)

        # Add reactions
        reactions = list(options.keys()) + ['👋']
        for reaction in reactions:
            await response_message.add_reaction(reaction)

        def check(reaction, user):
            return user == message.author and str(reaction.emoji) in reactions and reaction.message.id == response_message.id

        try:
            reaction, user = await bot.wait_for("reaction_add", timeout=REACTION_TIMEOUT, check=check)

            if str(reaction.emoji) == '👋':
                await response_message.delete()
                await message.add_reaction('👋')
                return

            selected_emoji = str(reaction.emoji)
            selected_option = options[selected_emoji]
            link = (
                highest_quality_video_url if selected_emoji == '3️⃣'
                else canonical_url.replace("reddit.com", "old.rxddit.com" if selected_emoji == '2️⃣' else "rxddit.com")
            )

            # Delete the original message
            try:
                await message.delete()
            except (discord.errors.NotFound, discord.errors.Forbidden) as e:
                print(f"Could not delete original message: {e}")

            # Replace the original link with the embed link in the content
            new_content = original_message_content.replace(original_link, link)
            safe_new_content = safe_preview_content.replace(original_link, link)
            
            # Post combined message with user's content and bot's instructions
            combined_message = (
                f"{message.author.display_name} posted:\n"
                f"{safe_new_content}\n\n"
                f"**You selected:** {selected_emoji} [{selected_option}]({link})\n"
                f"*React with ✅ if embed preview worked, ❌ if it failed. Pick nothing and I'll assume it did not work. \n This message will disappear {delete_in_timestamp()}*"
            )
            embedded_message = await message.channel.send(combined_message, allowed_mentions=SAFE_MENTIONS)
            
            # Add ✅ and ❌ reactions to the embedded message
            for emoji in ['✅', '❌']:
                try:
                    await embedded_message.add_reaction(emoji)
                except Exception as e:
                    print(f"Error adding reaction {emoji}: {e}")
            
            # Start handling reactions on the embedded message (non-blocking)
            bot.loop.create_task(
                handle_embedded_message_reactions(
                    embedded_message,
                    message.author,
                    original_message_content,
                    original_link,
                    new_content,
                    message.channel,
                )
            )

        except asyncio.TimeoutError:
            # Silently handle timeout - message will self-destruct
            pass
        finally:
            await response_message.delete()

# Main entry point
if __name__ == "__main__":
    token = get_bot_token()
    if token:
        keep_alive()
        bot.run(token)
    else:
        print("Unable to start the bot. Please fix the token issue.")