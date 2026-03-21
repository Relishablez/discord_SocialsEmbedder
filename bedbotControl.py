import os
import subprocess

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# Same optional local module as the main bot (gitignored)
try:
    import private_config as _private_config  # type: ignore
except ImportError:
    _private_config = None


def _parse_trusted_ids_from_env():
    raw = (os.getenv("BEDBOT_CONTROL_TRUSTED_IDS") or "").strip()
    if not raw:
        return []
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                print(f"Skipping invalid ID in BEDBOT_CONTROL_TRUSTED_IDS: {part!r}")
    return out


def _resolve_control_trusted_ids():
    """User IDs for /start, /restart, /shutdown — `.env` first, then optional private_config."""
    ids = _parse_trusted_ids_from_env()
    if ids:
        return ids
    if _private_config is not None:
        pc_ids = getattr(_private_config, "BEDBOT_CONTROL_TRUSTED_IDS", None)
        if pc_ids:
            try:
                return [int(x) for x in pc_ids]
            except (TypeError, ValueError):
                print("private_config.BEDBOT_CONTROL_TRUSTED_IDS must be a list of integers.")
    return []


CONTROL_TRUSTED_IDS = _resolve_control_trusted_ids()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


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


def _is_trusted(user_id: int) -> bool:
    if not CONTROL_TRUSTED_IDS:
        return False
    return user_id in CONTROL_TRUSTED_IDS


@bot.event
async def on_ready():
    print(f"Control bot logged in as {bot.user}")
    if not CONTROL_TRUSTED_IDS:
        print(
            "Warning: no trusted user IDs configured. Set BEDBOT_CONTROL_TRUSTED_IDS in `.env` "
            "(comma-separated) or in private_config.py (see private_config.example.py)."
        )
    try:
        await bot.tree.sync()
        print("Slash commands synced successfully!")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")


@bot.tree.command(name="start", description="Start the main bot using PM2")
async def start(interaction: discord.Interaction):
    """Slash command to start the main bot process using PM2."""
    if not _is_trusted(interaction.user.id):
        await interaction.response.send_message("You do not have permission to start the bot.")
        return
    try:
        await interaction.response.send_message("Starting the main bot...", ephemeral=False)
        print("Starting the main bot process via pm2...")
        subprocess.run(["pm2", "start", "disBedBot"], check=True)
        print("Main bot process successfully started via pm2.")
        await interaction.followup.send("Main bot started successfully!", ephemeral=False)
    except subprocess.CalledProcessError as e:
        print(f"Failed to start main bot via pm2: {e}")
        await interaction.followup.send("Failed to start the main bot. Please check the logs.", ephemeral=False)


@bot.tree.command(name="restart", description="Restart the main bot using PM2")
async def restart(interaction: discord.Interaction):
    """Slash command to restart the main bot process using PM2."""
    if not _is_trusted(interaction.user.id):
        await interaction.response.send_message("You do not have permission to restart the bot.")
        return
    try:
        await interaction.response.send_message("Restarting the main bot...", ephemeral=False)
        print("Restarting the main bot process via pm2...")
        subprocess.run(["pm2", "restart", "disBedBot"], check=True)
        print("Main bot process successfully restarted via pm2.")
        await interaction.followup.send("Main bot restarted successfully!", ephemeral=False)
    except subprocess.CalledProcessError as e:
        print(f"Failed to restart main bot via pm2: {e}")
        await interaction.followup.send("Failed to restart the main bot. Please check the logs.", ephemeral=False)


@bot.tree.command(name="shutdown", description="Stop the main bot using PM2")
async def shutdown(interaction: discord.Interaction):
    """Slash command to stop the main bot process using PM2."""
    if not _is_trusted(interaction.user.id):
        await interaction.response.send_message("You do not have permission to shut down the bot.")
        return
    try:
        await interaction.response.send_message("Shutting down the main bot...", ephemeral=False)
        print("Stopping the main bot process via pm2...")
        subprocess.run(["pm2", "stop", "disBedBot"], check=True)
        print("Main bot process successfully stopped via pm2.")
        await interaction.followup.send("Main bot shut down successfully!", ephemeral=False)
    except subprocess.CalledProcessError as e:
        print(f"Failed to shut down main bot via pm2: {e}")
        await interaction.followup.send("Failed to shut down the main bot. Please check the logs.", ephemeral=False)


if __name__ == "__main__":
    token = get_bot_token()
    if token:
        bot.run(token)
    else:
        print("Bot token not found. Exiting...")
