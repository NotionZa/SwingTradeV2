from __future__ import annotations

import logging
import re
import time

import discord
from discord import app_commands
from discord.ext import commands

from swingtrade.settings import get_settings
from swingtrade.watchlist_store import (
    format_watchlist_discord,
    load_watchlist_yaml,
    save_watchlist_yaml_atomic,
)

logger = logging.getLogger(__name__)

TICKER_RE = re.compile(r"^[A-Z]{1,5}$")

_user_last_cmd: dict[int, float] = {}
COOLDOWN_SEC = 2.0


def normalize_ticker(raw: str) -> str | None:
    t = raw.strip().upper()
    if not TICKER_RE.match(t):
        return None
    return t


def run_bot() -> None:
    settings = get_settings()
    if not settings.discord_bot_token.strip():
        raise RuntimeError("DISCORD_BOT_TOKEN is not set")

    intents = discord.Intents.default()
    intents.guilds = True

    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready() -> None:
        assert bot.user is not None
        logger.info("Logged in as %s (%s)", bot.user.name, bot.user.id)
        try:
            synced = await bot.tree.sync()
            logger.info("Synced %s application commands", len(synced))
        except Exception as e:
            logger.exception("Command sync failed: %s", e)

    def _allowed(interaction: discord.Interaction) -> bool:
        guilds = settings.allowed_guild_ids()
        if guilds and interaction.guild_id not in guilds:
            return False
        roles = settings.allowed_role_ids()
        if not roles:
            return True
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        user_roles = {r.id for r in interaction.user.roles}
        return bool(user_roles & roles)

    def _cooldown_ok(user_id: int) -> bool:
        now = time.monotonic()
        last = _user_last_cmd.get(user_id, 0.0)
        if now - last < COOLDOWN_SEC:
            return False
        _user_last_cmd[user_id] = now
        return True

    @bot.tree.command(name="add", description="Add a ticker to a watchlist category")
    @app_commands.describe(ticker="US equity symbol", category="Category name (must exist)")
    async def add_cmd(
        interaction: discord.Interaction,
        ticker: str,
        category: str,
    ) -> None:
        if not _allowed(interaction):
            await interaction.response.send_message("Not allowed.", ephemeral=True)
            return
        if not _cooldown_ok(interaction.user.id):
            await interaction.response.send_message("Slow down.", ephemeral=True)
            return
        sym = normalize_ticker(ticker)
        if sym is None:
            await interaction.response.send_message(
                "Invalid ticker (use 1–5 A–Z).", ephemeral=True
            )
            return
        path = settings.watchlist_path()
        data = load_watchlist_yaml(path)
        if category not in data:
            await interaction.response.send_message(
                f"Unknown category `{category}`. Existing: {', '.join(sorted(data.keys()))}",
                ephemeral=True,
            )
            return
        if sym in data[category]:
            await interaction.response.send_message(
                f"`{sym}` already in **{category}**.", ephemeral=True
            )
            return
        data[category].append(sym)
        save_watchlist_yaml_atomic(path, data)
        await interaction.response.send_message(f"Added `{sym}` to **{category}**.", ephemeral=True)

    @bot.tree.command(name="remove", description="Remove a ticker from a category")
    @app_commands.describe(ticker="US equity symbol", category="Category name")
    async def remove_cmd(
        interaction: discord.Interaction,
        ticker: str,
        category: str,
    ) -> None:
        if not _allowed(interaction):
            await interaction.response.send_message("Not allowed.", ephemeral=True)
            return
        if not _cooldown_ok(interaction.user.id):
            await interaction.response.send_message("Slow down.", ephemeral=True)
            return
        sym = normalize_ticker(ticker)
        if sym is None:
            await interaction.response.send_message(
                "Invalid ticker (use 1–5 A–Z).", ephemeral=True
            )
            return
        path = settings.watchlist_path()
        data = load_watchlist_yaml(path)
        if category not in data:
            await interaction.response.send_message("Unknown category.", ephemeral=True)
            return
        if sym not in data[category]:
            await interaction.response.send_message(
                f"`{sym}` not found in **{category}**.", ephemeral=True
            )
            return
        data[category] = [t for t in data[category] if t != sym]
        save_watchlist_yaml_atomic(path, data)
        await interaction.response.send_message(
            f"Removed `{sym}` from **{category}**.", ephemeral=True
        )

    @bot.tree.command(name="liststocks", description="Show the watchlist by category")
    async def list_cmd(interaction: discord.Interaction) -> None:
        if not _allowed(interaction):
            await interaction.response.send_message("Not allowed.", ephemeral=True)
            return
        if not _cooldown_ok(interaction.user.id):
            await interaction.response.send_message("Slow down.", ephemeral=True)
            return
        path = settings.watchlist_path()
        data = load_watchlist_yaml(path)
        body = format_watchlist_discord(data)
        await interaction.response.send_message(body[:2000], ephemeral=True)

    bot.run(settings.discord_bot_token, log_handler=None)
