"""Miu's Discord client, commands, and event handlers."""

from __future__ import annotations

import asyncio

import logging

import os

import random

import re

import time

from collections.abc import Mapping

from datetime import datetime, timedelta, timezone

from pathlib import Path

from typing import Final

import discord

from discord import app_commands

from discord.ext import commands

from .database import Database

LOGGER = logging.getLogger("miu")

BOT_TOKEN_ENV: Final[str] = "DISCORD_TOKEN"

DATABASE_ENV: Final[str] = "MIU_DB_PATH"

GUILD_ENV: Final[str] = "DISCORD_GUILD_ID"

HEARTBEAT_ENV: Final[str] = "MIU_HEARTBEAT_PATH"

HEARTBEAT_INTERVAL_SECONDS: Final[int] = 15

ACCENT: Final[discord.Color] = discord.Color.from_rgb(

    235, 145, 190

)

SUCCESS: Final[discord.Color] = discord.Color.from_rgb(

    127, 205, 169

)

WARNING: Final[discord.Color] = discord.Color.from_rgb(

    244, 190, 104

)

ERROR: Final[discord.Color] = discord.Color.from_rgb(

    232, 115, 135

)

MAX_RESPONSE_LENGTH: Final[int] = 1_900

COOLDOWNS: Final[dict[str, int]] = {

    "daily": 86_400,

    "work": 3_600,

}

MONEY_REWARDS: Final[dict[str, tuple[int, int]]] = {

    "daily": (100, 250),

    "work": (35, 120),

}

EIGHT_BALL_RESPONSES: Final[tuple[str, ...]] = (

    "It is certain.",

    "Absolutely yes.",

    "The stars say yes.",

    "Most likely.",

    "Ask me again in a little while.",

    "The answer is hazy.",

    "Probably not.",

    "My magic is saying no.",

    "Very unlikely.",

)

COMMAND_GROUPS: Final[dict[str, str]] = {

    "✦ Getting started":

        "`/help` `/commands` `/about` `/ping`",

    "✧ Server tools":

        "`/serverinfo` `/userinfo` `/avatar` `/roll` `/8ball`",

    "♡ Economy":

        "`/balance` `/daily` `/work` `/pay` `/leaderboard` `/economy`",

    "🛡 Moderation":

        "`/kick` `/ban` `/timeout` `/clear`",

    "⚙ Admin setup":

        "`/autoresponder` `/customcommand` `/welcome` "

        "`/leave` `/boost setup` `/set boost message` `/test boost`",

}

def miu_embed(

    title: str,

    description: str,

    *,

    color: discord.Color = ACCENT,

) -> discord.Embed:

    return discord.Embed(

        title=title,

        description=description,

        color=color,

    )

def render_template(

    template: str,

    member: discord.Member,

    guild: discord.Guild,

) -> str:

    replacements = {

        "user": member.mention,

        "username": discord.utils.escape_markdown(

            member.display_name

        ),

        "server": discord.utils.escape_markdown(

            guild.name

        ),

        "member_count": str(

            guild.member_count or len(guild.members)

        ),

    }

    rendered = template

    for key, value in replacements.items():

        rendered = rendered.replace(

            "{" + key + "}",

            value,

        )

    return rendered[:MAX_RESPONSE_LENGTH]

def normalize_name(value: str) -> str:

    return value.strip().casefold()

def is_valid_custom_name(value: str) -> bool:

    return (

        re.fullmatch(

            r"[a-z0-9_-]{1,32}",

            value,

        )

        is not None

    )

class MiuBot(commands.Bot):

    def __init__(self) -> None:

        intents = discord.Intents.default()

        intents.members = True

        intents.message_content = True

        super().__init__(

            command_prefix=commands.when_mentioned_or("!"),

            description=(

                "Miu — a cute, helpful Discord companion."

            ),

            intents=intents,

        )

        self.database = Database(

            os.getenv(

                DATABASE_ENV,

                "miu.sqlite3",

            )

        )

        self.started_at = datetime.now(timezone.utc)

        self.sync_scope = "guild"

        heartbeat_path = os.getenv(

            HEARTBEAT_ENV

        )

        self._heartbeat_path = (

            Path(heartbeat_path)

            if heartbeat_path

            else None

        )

        self._has_connected = False

        self._discord_connection_healthy = False

        self._health_task: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:

        guild_id_text = os.getenv(

            GUILD_ENV

        )

        if not guild_id_text:

            LOGGER.error(

                "DISCORD_GUILD_ID is not configured."

            )

            return

        try:

            guild_id = int(guild_id_text)

        except ValueError:

            LOGGER.error(

                "DISCORD_GUILD_ID must be a valid integer."

            )

            return

        guild = discord.Object(

            id=guild_id

        )

        self.tree.copy_global_to(

            guild=guild

        )

        await self.tree.sync(

            guild=guild

        )

        self._health_task = asyncio.create_task(

            self._health_heartbeat()

        )

        LOGGER.info(

            "Commands synced to guild %s.",

            guild_id,

        )

    async def on_ready(self) -> None:

        if self.user is None:

            return

        self._has_connected = True

        self._discord_connection_healthy = True

        self._write_health_state(

            "online"

        )

        LOGGER.info(

            "Logged in as %s (user ID: %s)",

            self.user,

            self.user.id,

        )

        LOGGER.info(

            "Serving %d server(s); command scope: %s.",

            len(self.guilds),

            self.sync_scope,

        )

    async def on_resumed(self) -> None:

        self._discord_connection_healthy = True

        self._write_health_state(

            "online"

        )

        LOGGER.info(

            "Discord connection resumed."

        )

    async def on_disconnect(self) -> None:

        self._discord_connection_healthy = False

        self._write_health_state(

            "offline"

        )

        LOGGER.warning(

            "Disconnected from Discord; waiting for automatic reconnection."

        )

    async def _health_heartbeat(self) -> None:

        while True:

            if (

                self._discord_connection_healthy

                and self.is_ready()

            ):

                self._write_health_state(

                    "online"

                )

            elif not self._has_connected:

                self._write_health_state(

                    "starting"

                )

            await asyncio.sleep(

                HEARTBEAT_INTERVAL_SECONDS

            )

    def _write_health_state(

        self,

        state: str,

    ) -> None:

        if self._heartbeat_path is None:

            return

        temporary_path = (

            self._heartbeat_path.with_name(

                f".{self._heartbeat_path.name}.tmp"

            )

        )

        try:

            temporary_path.write_text(

                f"{state}\n",

                encoding="utf-8",

            )

            temporary_path.replace(

                self._heartbeat_path

            )

        except OSError:

            LOGGER.warning(

                "Could not update the local Miu health marker."

            )

    async def on_message(

        self,

        message: discord.Message,

    ) -> None:

        if (

            message.author.bot

            or message.guild is None

        ):

            return

        content = message.content.strip()

        if (

            content.startswith("!")

            and len(content) > 1

        ):

            command_name = (

                content[1:]

                .split(

                    maxsplit=1

                )[0]

                .casefold()

            )

            custom = (

                self.database.get_custom_command(

                    message.guild.id,

                    command_name,

                )

            )

            if custom:

                await message.channel.send(

                    render_message_response(

                        custom["response"],

                        message,

                    ),

                    allowed_mentions=(

                        discord.AllowedMentions.none()

                    ),

                )

                return

        settings = self.database.get_settings(

            message.guild.id

        )

        if (

            settings["autoresponder_enabled"]

            and content

        ):

            matches = (

                self.database.matching_autoresponders(

                    message.guild.id,

                    content,

                )

            )

            if matches:

                await message.channel.send(

                    render_message_response(

                        matches[0]["response"],

                        message,

                    ),

                    allowed_mentions=(

                        discord.AllowedMentions.none()

                    ),

                )

    async def on_member_join(

        self,

        member: discord.Member,

    ) -> None:

        await self._send_membership_message(

            member,

            joining=True,

        )

    async def on_member_remove(

        self,

        member: discord.Member,

    ) -> None:

        await self._send_membership_message(

            member,

            joining=False,

        )

    async def on_member_update(

        self,

        before: discord.Member,

        after: discord.Member,

    ) -> None:

        if (

            before.premium_since is None

            and after.premium_since is not None

        ):

            await self._send_boost_notification(

                after

            )

    async def _send_membership_message(

        self,

        member: discord.Member,

        *,

        joining: bool,

    ) -> None:

        settings = self.database.get_settings(

            member.guild.id

        )

        enabled_key = (

            "welcome_enabled"

            if joining

            else "leave_enabled"

        )

        channel_key = (

            "welcome_channel_id"

            if joining

            else "leave_channel_id"

        )

        message_key = (

            "welcome_message"

            if joining

            else "leave_message"

        )

        if (

            not settings[enabled_key]

            or not settings[channel_key]

        ):

            return

        channel = member.guild.get_channel(

            settings[channel_key]

        )

        if channel is None:

            try:

                channel = await self.fetch_channel(

                    settings[channel_key]

                )

            except (

                discord.NotFound,

                discord.Forbidden,

                discord.HTTPException,

            ):

                LOGGER.warning(

                    "Could not find configured membership channel in guild %d.",

                    member.guild.id,

                )

                return

        if not isinstance(

            channel,

            discord.TextChannel,

        ):

            LOGGER.warning(

                "Configured membership channel is not a text channel in guild %d.",

                member.guild.id,

            )

            return

        try:

            await channel.send(

                render_template(

                    settings[message_key],

                    member,

                    member.guild,

                ),

                allowed_mentions=(

                    discord.AllowedMentions(

                        users=False,

                        roles=False,

                        everyone=False,

                    )

                ),

            )

        except (

            discord.Forbidden,

            discord.HTTPException,

        ):

            LOGGER.warning(

                "Could not send membership message in guild %d.",

                member.guild.id,

            )

    async def _send_boost_notification(

        self,

        member: discord.Member,

    ) -> None:

        settings = self.database.get_settings(

            member.guild.id

        )

        channel_id = settings[

            "boost_channel_id"

        ]

        if not channel_id:

            return

        channel = member.guild.get_channel(

            channel_id

        )

        if channel is None:

            try:

                channel = await self.fetch_channel(

                    channel_id

                )

            except (

                discord.NotFound,

                discord.Forbidden,

                discord.HTTPException,

            ):

                LOGGER.warning(

                    "Could not find configured boost channel in guild %d.",

                    member.guild.id,

                )

                return

        if not isinstance(

            channel,

            discord.TextChannel,

        ):

            return

        if member.premium_since is None:

            return

        boost_started_at = (

            member.premium_since

            .astimezone(timezone.utc)

            .isoformat()

        )

        if not self.database.claim_boost_notification(

            member.guild.id,

            member.id,

            boost_started_at,

            int(time.time()),

        ):

            return

        embed = build_boost_embed(

            member,

            settings,

        )

        try:

            await channel.send(

                embed=embed,

                allowed_mentions=(

                    discord.AllowedMentions(

                        users=True,

                        roles=False,

                        everyone=False,

                    )

                ),

            )

        except (

            discord.Forbidden,

            discord.HTTPException,

        ):

            LOGGER.warning(

                "Could not send boost notification in guild %d.",

                member.guild.id,

            )

    async def close(self) -> None:

        if self._health_task is not None:

            health_task = self._health_task

            self._health_task = None

            health_task.cancel()

            try:

                await health_task

            except asyncio.CancelledError:

                pass

        self._write_health_state(

            "offline"

        )

        self.database.close()

        await super().close()

def render_message_response(

    response: str,

    message: discord.Message,

) -> str:

    guild = message.guild

    if guild is None:

        return response[:MAX_RESPONSE_LENGTH]

    replacements = {

        "{user}": message.author.mention,

        "{username}": discord.utils.escape_markdown(

            message.author.display_name

        ),

        "{server}": discord.utils.escape_markdown(

            guild.name

        ),

        "{member_count}": str(

            guild.member_count

            or len(guild.members)

        ),

    }

    rendered = response

    for key, value in replacements.items():

        rendered = rendered.replace(

            key,

            value,

        )

    return rendered[:MAX_RESPONSE_LENGTH]

def load_discord_token() -> str:

    raw_token = os.environ.get(

        BOT_TOKEN_ENV,

        "",

    )

    token = raw_token.strip()

    if token.casefold().startswith(

        "bot "

    ):

        token = token[4:].strip()

    if (

        len(token) >= 2

        and token[0] == token[-1]

        and token[0] in {"'", '"'}

    ):

        token = token[1:-1].strip()

    if not token:

        raise RuntimeError(

            f"{BOT_TOKEN_ENV} is not set. "

            "Add Miu's bot token as a Replit Secret."

        )

    return token

def render_boost_text(

    template: str,

    member: discord.Member,

) -> str:

    boost_count = (

        member.guild.premium_subscription_count

    )

    replacements = {

        "{user}": member.mention,

        "{username}": discord.utils.escape_markdown(

            member.display_name

        ),

        "{server}": discord.utils.escape_markdown(

            member.guild.name

        ),

        "{boosts}": (

            str(boost_count)

            if boost_count is not None

            else "0"

        ),

    }

    rendered = template

    for key, value in replacements.items():

        rendered = rendered.replace(

            key,

            value,

        )

    return rendered

def is_server_owner_or_admin(

    interaction: discord.Interaction,

) -> bool:

    guild = interaction.guild

    return bool(

        guild

        and (

            interaction.user.id

            == guild.owner_id

            or getattr(

                interaction.user.guild_permissions,

                "administrator",

                False,

            )

        )

    )

def build_boost_embed(

    member: discord.Member,

    settings: Mapping[str, object] | None = None,

) -> discord.Embed:

    if settings is None:

        settings = bot.database.get_settings(

            member.guild.id

        )

    embed = discord.Embed(

        title=render_boost_text(

            str(settings["boost_title"]),

            member,

        )[:256],

        description=render_boost_text(

            str(settings["boost_description"]),

            member,

        )[:4_096],

        color=ACCENT,

    )

    embed.set_author(

        name=member.display_name,

        icon_url=member.display_avatar.url,

    )

    embed.set_thumbnail(

        url=member.display_avatar.url

    )

    embed.add_field(

        name="Boosted by",

        value=member.mention,

        inline=True,

    )

    boost_count = (

        member.guild.premium_subscription_count

    )

    if boost_count is not None:

        embed.add_field(

            name="Server boosts",

            value=f"**{boost_count}** ✦",

            inline=True,

        )

    embed.set_footer(

        text=render_boost_text(

            str(settings["boost_footer"]),

            member,

        )[:2_048],

    )

    image_url = settings[

        "boost_image_url"

    ]

    if image_url:

        embed.set_image(

            url=str(image_url)

        )

    return embed

bot = MiuBot()

def guild_or_none(

    interaction: discord.Interaction,

) -> discord.Guild | None:

    return interaction.guild

async def send_ephemeral(

    interaction: discord.Interaction,

    content: str,

) -> None:

    if interaction.response.is_done():

        await interaction.followup.send(

            content,

            ephemeral=True,

        )

    else:

        await interaction.response.send_message(

            content,

            ephemeral=True,

        )

def currency_text(

    settings: object,

    amount: int,

) -> str:

    return (

        f"{settings['currency_emoji']} "

        f"{amount:,} "

        f"{settings['currency_name']}"

    )  # type: ignore[index]

def remaining_cooldown(

    guild_id: int,

    user_id: int,

    action: str,

) -> int:

    claimed_at = bot.database.get_cooldown(

        guild_id,

        user_id,

        action,

    )

    if claimed_at is None:

        return 0

    return max(

        0,

        COOLDOWNS[action]

        - (

            int(time.time())

            - claimed_at

        ),

    )

def human_duration(

    seconds: int,

) -> str:

    hours, remainder = divmod(

        seconds,

        3_600,

    )

    minutes, _ = divmod(

        remainder,

        60,

    )

    if hours:

        return f"{hours}h {minutes}m"

    return f"{minutes}m"

@bot.tree.command(

    name="help",

    description="Open Miu's categorized help menu.",

)

async def help_command(

    interaction: discord.Interaction,

) -> None:

    embed = miu_embed(

        "Miu's little guide ✦",

        "Here are the things I can do for your server.",

    )

    for name, commands_text in COMMAND_GROUPS.items():

        embed.add_field(

            name=name,

            value=commands_text,

            inline=False,

        )

    embed.set_footer(

        text="Use /commands for the compact command list."

    )

    await interaction.response.send_message(

        embed=embed

    )

@bot.tree.command(

    name="commands",

    description="Show Miu's available commands.",

)

async def commands_list(

    interaction: discord.Interaction,

) -> None:

    await interaction.response.send_message(

        embed=miu_embed(

            "Miu's commands ✧",

            "\n".join(

                f"{name}: {value}"

                for name, value in COMMAND_GROUPS.items()

            ),

        )

    )

@bot.tree.command(

    name="ping",

    description="Check Miu's response time.",

)

async def ping(

    interaction: discord.Interaction,

) -> None:

    await interaction.response.send_message(

        embed=miu_embed(

            "Pong! ♡",

            (

                f"Gateway latency: "

                f"**{round(bot.latency * 1000)} ms**"

            ),

            color=SUCCESS,

        )

    )

@bot.tree.command(

    name="about",

    description="Learn a little about Miu.",

)

async def about(

    interaction: discord.Interaction,

) -> None:

    uptime = (

        datetime.now(timezone.utc)

        - bot.started_at

    )

    await interaction.response.send_message(

        embed=miu_embed(

            "About Miu",

            (

                "A cute, clean Discord companion "

                "for everyday server life.\n\n"

                f"Online for "

                f"**{str(uptime).split('.')[0]}**\n"

                f"Serving **{len(bot.guilds)}** server(s)"

            ),

        )

    )

@bot.tree.command(

    name="serverinfo",

    description="Show information about this server.",

)

async def server_info(

    interaction: discord.Interaction,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "This command can only be used inside a server.",

        )

        return

    embed = miu_embed(

        f"{guild.name} ✦",

        "A quick look at this server.",

    )

    embed.add_field(

        name="Owner",

        value=(

            f"<@{guild.owner_id}>"

            if guild.owner_id

            else "Unknown"

        ),

    )

    embed.add_field(

        name="Members",

        value=str(

            guild.member_count

            or len(guild.members)

        ),

    )

    embed.add_field(

        name="Channels",

        value=str(

            len(guild.channels)

        ),

    )

    embed.add_field(

        name="Created",

        value=discord.utils.format_dt(

            guild.created_at,

            style="D",

        ),

    )

    if guild.icon:

        embed.set_thumbnail(

            url=guild.icon.url

        )

    await interaction.response.send_message(

        embed=embed

    )

@bot.tree.command(

    name="userinfo",

    description="Show information about a server member.",

)

async def user_info(

    interaction: discord.Interaction,

    user: discord.Member | None = None,

) -> None:

    member = (

        user

        or interaction.user

    )

    embed = miu_embed(

        f"{member.display_name} ✧",

        (

            f"Username: `{member}`\n"

            f"User ID: `{member.id}`"

        ),

    )

    embed.add_field(

        name="Joined server",

        value=(

            discord.utils.format_dt(

                member.joined_at,

                style="D",

            )

            if member.joined_at

            else "Unknown"

        ),

    )

    embed.add_field(

        name="Account created",

        value=discord.utils.format_dt(

            member.created_at,

            style="D",

        ),

    )

    embed.set_thumbnail(

        url=member.display_avatar.url

    )

    await interaction.response.send_message(

        embed=embed

    )

@bot.tree.command(

    name="avatar",

    description="Show a member's avatar.",

)

async def avatar(

    interaction: discord.Interaction,

    user: discord.Member | None = None,

) -> None:

    member = (

        user

        or interaction.user

    )

    embed = miu_embed(

        f"{member.display_name}'s avatar",

        (

            f"[Open full-size avatar]"

            f"({member.display_avatar.url})"

        ),

    )

    embed.set_image(

        url=member.display_avatar.url

    )

    await interaction.response.send_message(

        embed=embed

    )

@bot.tree.command(

    name="8ball",

    description="Ask Miu a question.",

)

async def eight_ball(

    interaction: discord.Interaction,

    question: str,

) -> None:

    answer = random.choice(

        EIGHT_BALL_RESPONSES

    )

    await interaction.response.send_message(

        embed=miu_embed(

            "Magic 8-ball ✦",

            (

                f"**Question:** "

                f"{question[:500]}\n\n"

                f"**Answer:** {answer}"

            ),

        )

    )

@bot.tree.command(

    name="roll",

    description="Roll a random number between 1 and the chosen sides.",

)

async def roll(

    interaction: discord.Interaction,

    sides: app_commands.Range[

        int,

        2,

        1_000

    ] = 6,

) -> None:

    await interaction.response.send_message(

        embed=miu_embed(

            "Your roll 🎲",

            (

                f"**{random.randint(1, sides)}** "

                f"(1–{sides})"

            ),

        )

    )

@bot.tree.command(

    name="balance",

    description="Check a member's server balance.",

)

async def balance(

    interaction: discord.Interaction,

    user: discord.Member | None = None,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "Economy commands only work inside a server.",

        )

        return

    member = (

        user

        or interaction.user

    )

    settings = bot.database.get_settings(

        guild.id

    )

    amount = bot.database.get_balance(

        guild.id,

        member.id,

    )

    await interaction.response.send_message(

        embed=miu_embed(

            f"{member.display_name}'s balance ♡",

            currency_text(

                settings,

                amount,

            ),

            color=SUCCESS,

        )

    )

async def claim_reward(

    interaction: discord.Interaction,

    action: str,

    title: str,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "Economy commands only work inside a server.",

        )

        return

    wait = remaining_cooldown(

        guild.id,

        interaction.user.id,

        action,

    )

    if wait:

        await send_ephemeral(

            interaction,

            (

                f"You can use `/{action}` again in "

                f"**{human_duration(wait)}**."

            ),

        )

        return

    amount = random.randint(

        *MONEY_REWARDS[action]

    )

    bot.database.set_cooldown(

        guild.id,

        interaction.user.id,

        action,

        int(time.time()),

    )

    new_balance = bot.database.add_balance(

        guild.id,

        interaction.user.id,

        amount,

    )

    settings = bot.database.get_settings(

        guild.id

    )

    await interaction.response.send_message(

        embed=miu_embed(

            title,

            (

                f"You earned **"

                f"{currency_text(settings, amount)}**.\n"

                f"Your new balance is **"

                f"{currency_text(settings, new_balance)}**."

            ),

            color=SUCCESS,

        )

    )

@bot.tree.command(

    name="daily",

    description="Claim your daily server currency reward.",

)

async def daily(

    interaction: discord.Interaction,

) -> None:

    await claim_reward(

        interaction,

        "daily",

        "Daily petals ✦",

    )

@bot.tree.command(

    name="work",

    description="Work once for a random currency reward.",

)

async def work(

    interaction: discord.Interaction,

) -> None:

    await claim_reward(

        interaction,

        "work",

        "Work complete ✧",

    )

@bot.tree.command(

    name="pay",

    description="Transfer server currency to another member.",

)

async def pay(

    interaction: discord.Interaction,

    user: discord.Member,

    amount: app_commands.Range[

        int,

        1,

        1_000_000

    ],

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "Economy commands only work inside a server.",

        )

        return

    if user.bot:

        await send_ephemeral(

            interaction,

            "Bots cannot receive currency.",

        )

        return

    try:

        sender_balance, _ = (

            bot.database.transfer(

                guild.id,

                interaction.user.id,

                user.id,

                amount,

            )

        )

    except ValueError as error:

        await send_ephemeral(

            interaction,

            str(error),

        )

        return

    settings = bot.database.get_settings(

        guild.id

    )

    await interaction.response.send_message(

        embed=miu_embed(

            "Transfer complete ♡",

            (

                f"{interaction.user.mention} sent "

                f"**{currency_text(settings, amount)}** "

                f"to {user.mention}.\n"

                f"Your remaining balance: "

                f"**{currency_text(settings, sender_balance)}**."

            ),

            color=SUCCESS,

        ),

        allowed_mentions=(

            discord.AllowedMentions(

                users=True

            )

        ),

    )

@bot.tree.command(

    name="leaderboard",

    description="Show the richest members in this server.",

)

async def leaderboard(

    interaction: discord.Interaction,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "Economy commands only work inside a server.",

        )

        return

    settings = bot.database.get_settings(

        guild.id

    )

    rows = bot.database.get_leaderboard(

        guild.id

    )

    if not rows:

        description = (

            "No balances yet — use `/daily` or `/work` "

            "to get started."

        )

    else:

        lines = []

        for position, row in enumerate(

            rows,

            start=1,

        ):

            member = guild.get_member(

                row["user_id"]

            )

            name = (

                member.display_name

                if member

                else f"User {row['user_id']}"

            )

            lines.append(

                f"**{position}.** {name} — "

                f"{currency_text(settings, row['balance'])}"

            )

        description = "\n".join(

            lines

        )

    await interaction.response.send_message(

        embed=miu_embed(

            "Server leaderboard ✦",

            description,

        )

    )

@bot.tree.command(

    name="economy",

    description="View economy settings or configure the server currency.",

)

@app_commands.describe(

    currency_name="Admin-only: the currency name",

    currency_emoji="Admin-only: a short currency symbol",

)

async def economy(

    interaction: discord.Interaction,

    currency_name: str | None = None,

    currency_emoji: str | None = None,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "Economy commands only work inside a server.",

        )

        return

    if (

        currency_name is not None

        or currency_emoji is not None

    ) and not interaction.user.guild_permissions.manage_guild:

        await send_ephemeral(

            interaction,

            (

                "Only members with Manage Server "

                "can change economy settings."

            ),

        )

        return

    updates: dict[str, str] = {}

    if currency_name is not None:

        if not 1 <= len(

            currency_name.strip()

        ) <= 20:

            await send_ephemeral(

                interaction,

                "Currency names must be 1–20 characters.",

            )

            return

        updates["currency_name"] = (

            currency_name.strip()

        )

    if currency_emoji is not None:

        if not 1 <= len(

            currency_emoji.strip()

        ) <= 4:

            await send_ephemeral(

                interaction,

                "Currency symbols must be 1–4 characters.",

            )

            return

        updates["currency_emoji"] = (

            currency_emoji.strip()

        )

    settings = bot.database.update_settings(

        guild.id,

        **updates,

    )

    amount = bot.database.get_balance(

        guild.id,

        interaction.user.id,

    )

    description = (

        f"Your balance: **"

        f"{currency_text(settings, amount)}**\n"

        f"Daily reward: **"

        f"{MONEY_REWARDS['daily'][0]}–"

        f"{MONEY_REWARDS['daily'][1]}**\n"

        f"Work reward: **"

        f"{MONEY_REWARDS['work'][0]}–"

        f"{MONEY_REWARDS['work'][1]}**"

    )

    if updates:

        description = (

            "Economy settings saved.\n\n"

            + description

        )

    await interaction.response.send_message(

        embed=miu_embed(

            "Server economy ♡",

            description,

            color=SUCCESS,

        )

    )

# ---------------------------------------------------------

# AUTORESPONDER

# ---------------------------------------------------------

autoresponder_group = app_commands.Group(

    name="autoresponder",

    description="Manage automatic keyword replies.",

)

@autoresponder_group.command(

    name="add",

    description="Add or update an automatic reply trigger.",

)

@app_commands.checks.has_permissions(

    manage_guild=True

)

async def autoresponder_add(

    interaction: discord.Interaction,

    trigger: str,

    response: str,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "This setup command can only be used inside a server.",

        )

        return

    trigger = normalize_name(

        trigger

    )

    if (

        not 1 <= len(trigger) <= 50

        or trigger.startswith("!")

    ):

        await send_ephemeral(

            interaction,

            (

                "Triggers must be 1–50 characters "

                "and cannot start with `!`."

            ),

        )

        return

    if not 1 <= len(

        response

    ) <= MAX_RESPONSE_LENGTH:

        await send_ephemeral(

            interaction,

            (

                f"Responses must be 1–"

                f"{MAX_RESPONSE_LENGTH} characters."

            ),

        )

        return

    bot.database.remove_autoresponder(

        guild.id,

        trigger,

    )

    bot.database.add_autoresponder(

        guild.id,

        trigger,

        response,

    )

    await interaction.response.send_message(

        f"Added an autoresponder for `{trigger}`.",

        ephemeral=True,

    )

@autoresponder_group.command(

    name="remove",

    description="Remove an automatic reply trigger.",

)

@app_commands.checks.has_permissions(

    manage_guild=True

)

async def autoresponder_remove(

    interaction: discord.Interaction,

    trigger: str,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "This setup command can only be used inside a server.",

        )

        return

    removed = (

        bot.database.remove_autoresponder(

            guild.id,

            normalize_name(trigger),

        )

    )

    await interaction.response.send_message(

        (

            "Autoresponder removed."

            if removed

            else "I couldn't find that autoresponder."

        ),

        ephemeral=True,

    )

@autoresponder_group.command(

    name="list",

    description="List this server's automatic reply triggers.",

)

@app_commands.checks.has_permissions(

    manage_guild=True

)

async def autoresponder_list(

    interaction: discord.Interaction,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "This setup command can only be used inside a server.",

        )

        return

    rows = (

        bot.database.list_autoresponders(

            guild.id

        )

    )

    description = "\n".join(

        (

            f"• `{row['trigger']}` → "

            f"{row['response'][:80]}"

        )

        for row in rows

    )

    await interaction.response.send_message(

        embed=miu_embed(

            "Autoresponders",

            (

                description

                or "No autoresponders configured yet."

            ),

        ),

        ephemeral=True,

    )

@autoresponder_group.command(

    name="toggle",

    description="Enable or disable automatic replies.",

)

@app_commands.checks.has_permissions(

    manage_guild=True

)

async def autoresponder_toggle(

    interaction: discord.Interaction,

    enabled: bool,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "This setup command can only be used inside a server.",

        )

        return

    bot.database.update_settings(

        guild.id,

        autoresponder_enabled=int(enabled),

    )

    await interaction.response.send_message(

        (

            "Autoresponders are now "

            f"**{'enabled' if enabled else 'disabled'}**."

        ),

        ephemeral=True,

    )

# ---------------------------------------------------------

# CUSTOM COMMANDS

# ---------------------------------------------------------

custom_command_group = app_commands.Group(

    name="customcommand",

    description="Manage this server's custom text commands.",

)

@custom_command_group.command(

    name="add",

    description="Create a custom command used with !name.",

)

@app_commands.checks.has_permissions(

    manage_guild=True

)

async def customcommand_add(

    interaction: discord.Interaction,

    name: str,

    response: str,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "This setup command can only be used inside a server.",

        )

        return

    name = normalize_name(

        name

    )

    if not is_valid_custom_name(

        name

    ):

        await send_ephemeral(

            interaction,

            (

                "Names must be 1–32 characters "

                "using letters, numbers, `_`, or `-`."

            ),

        )

        return

    if not 1 <= len(

        response

    ) <= MAX_RESPONSE_LENGTH:

        await send_ephemeral(

            interaction,

            (

                f"Responses must be 1–"

                f"{MAX_RESPONSE_LENGTH} characters."

            ),

        )

        return

    created = (

        bot.database.add_custom_command(

            guild.id,

            name,

            response,

        )

    )

    await interaction.response.send_message(

        (

            f"Custom command `!{name}` "

            f"{'created' if created else 'already exists — use edit to change it'}."

        ),

        ephemeral=True,

    )

@custom_command_group.command(

    name="edit",

    description="Edit an existing custom command.",

)

@app_commands.checks.has_permissions(

    manage_guild=True

)

async def customcommand_edit(

    interaction: discord.Interaction,

    name: str,

    response: str,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "This setup command can only be used inside a server.",

        )

        return

    name = normalize_name(

        name

    )

    if (

        not is_valid_custom_name(name)

        or not 1 <= len(response)

        <= MAX_RESPONSE_LENGTH

    ):

        await send_ephemeral(

            interaction,

            (

                "Use a valid command name "

                "and a response of 1–1,900 characters."

            ),

        )

        return

    updated = (

        bot.database.update_custom_command(

            guild.id,

            name,

            response,

        )

    )

    await interaction.response.send_message(

        (

            f"Custom command `!{name}` "

            f"{'updated' if updated else 'was not found'}."

        ),

        ephemeral=True,

    )

@custom_command_group.command(

    name="remove",

    description="Delete a custom command.",

)

@app_commands.checks.has_permissions(

    manage_guild=True

)

async def customcommand_remove(

    interaction: discord.Interaction,

    name: str,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "This setup command can only be used inside a server.",

        )

        return

    removed = (

        bot.database.remove_custom_command(

            guild.id,

            normalize_name(name),

        )

    )

    await interaction.response.send_message(

        (

            "Custom command removed."

            if removed

            else "I couldn't find that custom command."

        ),

        ephemeral=True,

    )

@custom_command_group.command(

    name="list",

    description="List this server's custom commands.",

)

@app_commands.checks.has_permissions(

    manage_guild=True

)

async def customcommand_list(

    interaction: discord.Interaction,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "This setup command can only be used inside a server.",

        )

        return

    rows = (

        bot.database.list_custom_commands(

            guild.id

        )

    )

    description = "\n".join(

        (

            f"• `!{row['name']}` → "

            f"{row['response'][:80]}"

        )

        for row in rows

    )

    await interaction.response.send_message(

        embed=miu_embed(

            "Custom commands",

            (

                description

                or "No custom commands configured yet."

            ),

        ),

        ephemeral=True,

    )

# ---------------------------------------------------------

# MODERATION

# ---------------------------------------------------------

def can_moderate(

    guild: discord.Guild,

    target: discord.Member,

    actor_id: int,

) -> tuple[bool, str]:

    if target.id == actor_id:

        return False, "You cannot moderate yourself."

    if target.id == guild.owner_id:

        return False, "The server owner cannot be moderated."

    me = guild.me

    if (

        me is None

        or target.top_role >= me.top_role

    ):

        return (

            False,

            (

                "My role must be higher than "

                "the target member's highest role."

            ),

        )

    return True, ""

@bot.tree.command(

    name="kick",

    description="Kick a member from this server.",

)

@app_commands.checks.has_permissions(

    kick_members=True

)

@app_commands.checks.bot_has_permissions(

    kick_members=True

)

async def kick(

    interaction: discord.Interaction,

    member: discord.Member,

    reason: str | None = None,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "Moderation commands only work inside a server.",

        )

        return

    allowed, message = can_moderate(

        guild,

        member,

        interaction.user.id,

    )

    if not allowed:

        await send_ephemeral(

            interaction,

            message,

        )

        return

    try:

        await member.kick(

            reason=(

                reason

                or f"Requested by {interaction.user}"

            )

        )

    except discord.Forbidden:

        await send_ephemeral(

            interaction,

            (

                "Discord refused that action. "

                "Check my permissions and role position."

            ),

        )

        return

    except discord.HTTPException:

        await send_ephemeral(

            interaction,

            "Discord could not complete the kick right now.",

        )

        return

    await interaction.response.send_message(

        f"✦ {member} was kicked.",

        ephemeral=True,

    )

    await log_moderation(

        guild,

        (

            f"Kick: {member} by {interaction.user}. "

            f"Reason: {reason or 'none'}"

        ),

    )

@bot.tree.command(

    name="ban",

    description="Ban a member from this server.",

)

@app_commands.checks.has_permissions(

    ban_members=True

)

@app_commands.checks.bot_has_permissions(

    ban_members=True

)

async def ban(

    interaction: discord.Interaction,

    member: discord.Member,

    reason: str | None = None,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "Moderation commands only work inside a server.",

        )

        return

    allowed, message = can_moderate(

        guild,

        member,

        interaction.user.id,

    )

    if not allowed:

        await send_ephemeral(

            interaction,

            message,

        )

        return

    try:

        await member.ban(

            reason=(

                reason

                or f"Requested by {interaction.user}"

            )

        )

    except discord.Forbidden:

        await send_ephemeral(

            interaction,

            (

                "Discord refused that action. "

                "Check my permissions and role position."

            ),

        )

        return

    except discord.HTTPException:

        await send_ephemeral(

            interaction,

            "Discord could not complete the ban right now.",

        )

        return

    await interaction.response.send_message(

        f"✦ {member} was banned.",

        ephemeral=True,

    )

    await log_moderation(

        guild,

        (

            f"Ban: {member} by {interaction.user}. "

            f"Reason: {reason or 'none'}"

        ),

    )

@bot.tree.command(

    name="timeout",

    description="Temporarily timeout a member.",

)

@app_commands.checks.has_permissions(

    moderate_members=True

)

@app_commands.checks.bot_has_permissions(

    moderate_members=True

)

async def timeout(

    interaction: discord.Interaction,

    member: discord.Member,

    duration_minutes: app_commands.Range[

        int,

        1,

        40_320

    ],

    reason: str | None = None,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "Moderation commands only work inside a server.",

        )

        return

    allowed, message = can_moderate(

        guild,

        member,

        interaction.user.id,

    )

    if not allowed:

        await send_ephemeral(

            interaction,

            message,

        )

        return

    try:

        await member.timeout(

            timedelta(

                minutes=duration_minutes

            ),

            reason=(

                reason

                or f"Requested by {interaction.user}"

            ),

        )

    except discord.Forbidden:

        await send_ephemeral(

            interaction,

            (

                "Discord refused that action. "

                "Check my permissions and role position."

            ),

        )

        return

    except discord.HTTPException:

        await send_ephemeral(

            interaction,

            "Discord could not complete the timeout right now.",

        )

        return

    await interaction.response.send_message(

        (

            f"✦ {member} was timed out for "

            f"**{duration_minutes} minutes**."

        ),

        ephemeral=True,

    )

    await log_moderation(

        guild,

        (

            f"Timeout: {member} for "

            f"{duration_minutes}m by {interaction.user}. "

            f"Reason: {reason or 'none'}"

        ),

    )

@bot.tree.command(

    name="clear",

    description="Delete recent messages from this channel.",

)

@app_commands.checks.has_permissions(

    manage_messages=True

)

@app_commands.checks.bot_has_permissions(

    manage_messages=True,

    read_message_history=True,

)

async def clear(

    interaction: discord.Interaction,

    amount: app_commands.Range[

        int,

        1,

        100

    ],

) -> None:

    channel = interaction.channel

    if not isinstance(

        channel,

        (

            discord.TextChannel,

            discord.Thread,

        ),

    ):

        await send_ephemeral(

            interaction,

            "I can only clear messages in a text channel.",

        )

        return

    await interaction.response.defer(

        ephemeral=True

    )

    try:

        deleted = await channel.purge(

            limit=amount

        )

    except discord.Forbidden:

        await interaction.followup.send(

            "Discord refused that action. Check my permissions.",

            ephemeral=True,

        )

        return

    except discord.HTTPException:

        await interaction.followup.send(

            "Discord could not clear messages right now.",

            ephemeral=True,

        )

        return

    await interaction.followup.send(

        f"Cleared **{len(deleted)}** message(s).",

        ephemeral=True,

    )

    if interaction.guild:

        await log_moderation(

            interaction.guild,

            (

                f"Clear: {len(deleted)} message(s) "

                f"in #{channel} by {interaction.user}."

            ),

        )

async def log_moderation(

    guild: discord.Guild,

    message: str,

) -> None:

    settings = bot.database.get_settings(

        guild.id

    )

    channel_id = settings[

        "moderation_log_channel_id"

    ]

    if not channel_id:

        return

    channel = guild.get_channel(

        channel_id

    )

    if isinstance(

        channel,

        discord.TextChannel,

    ):

        try:

            await channel.send(

                f"🛡 {message}",

                allowed_mentions=(

                    discord.AllowedMentions.none()

                ),

            )

        except (

            discord.Forbidden,

            discord.HTTPException,

        ):

            LOGGER.warning(

                "Could not send moderation log in guild %d.",

                guild.id,

            )

# ---------------------------------------------------------

# WELCOME / LEAVE

# ---------------------------------------------------------

async def configure_membership(

    interaction: discord.Interaction,

    *,

    joining: bool,

    channel: discord.TextChannel,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "This setup command can only be used inside a server.",

        )

        return

    key = (

        "welcome_channel_id"

        if joining

        else "leave_channel_id"

    )

    label = (

        "Welcome"

        if joining

        else "Leave"

    )

    bot.database.update_settings(

        guild.id,

        **{

            key: channel.id

        },

    )

    await interaction.response.send_message(

        (

            f"{label} messages will use "

            f"{channel.mention}."

        ),

        ephemeral=True,

    )

def setup_membership_commands(

    group: app_commands.Group,

    *,

    joining: bool,

) -> None:

    label = (

        "welcome"

        if joining

        else "leave"

    )

    title = (

        "welcome"

        if joining

        else "leave"

    )

    message_key = (

        "welcome_message"

        if joining

        else "leave_message"

    )

    enabled_key = (

        "welcome_enabled"

        if joining

        else "leave_enabled"

    )

    @group.command(

        name="setup",

        description=(

            f"Choose the channel for "

            f"{label} messages."

        ),

    )

    @app_commands.checks.has_permissions(

        manage_guild=True

    )

    async def setup(

        interaction: discord.Interaction,

        channel: discord.TextChannel,

    ) -> None:

        await configure_membership(

            interaction,

            joining=joining,

            channel=channel,

        )

    @group.command(

        name="message",

        description=(

            f"Set the {label} message text."

        ),

    )

    @app_commands.checks.has_permissions(

        manage_guild=True

    )

    async def message(

        interaction: discord.Interaction,

        text: str,

    ) -> None:

        guild = guild_or_none(

            interaction

        )

        if guild is None:

            await send_ephemeral(

                interaction,

                "This setup command can only be used inside a server.",

            )

            return

        if not 1 <= len(

            text

        ) <= MAX_RESPONSE_LENGTH:

            await send_ephemeral(

                interaction,

                (

                    f"Messages must be 1–"

                    f"{MAX_RESPONSE_LENGTH} characters."

                ),

            )

            return

        bot.database.update_settings(

            guild.id,

            **{

                message_key: text

            },

        )

        await interaction.response.send_message(

            (

                f"{title.title()} message saved. "

                "Supported variables: "

                "`{user}`, `{username}`, "

                "`{server}`, `{member_count}`."

            ),

            ephemeral=True,

        )

    @group.command(

        name="toggle",

        description=(

            f"Enable or disable {label} messages."

        ),

    )

    @app_commands.checks.has_permissions(

        manage_guild=True

    )

    async def toggle(

        interaction: discord.Interaction,

        enabled: bool,

    ) -> None:

        guild = guild_or_none(

            interaction

        )

        if guild is None:

            await send_ephemeral(

                interaction,

                "This setup command can only be used inside a server.",

            )

            return

        bot.database.update_settings(

            guild.id,

            **{

                enabled_key: int(enabled)

            },

        )

        await interaction.response.send_message(

            (

                f"{title.title()} messages are now "

                f"**{'enabled' if enabled else 'disabled'}**."

            ),

            ephemeral=True,

        )

    del setup, message, toggle

welcome_group = app_commands.Group(

    name="welcome",

    description="Configure new-member welcome messages.",

)

leave_group = app_commands.Group(

    name="leave",

    description="Configure member leave messages.",

)

setup_membership_commands(

    welcome_group,

    joining=True,

)

setup_membership_commands(

    leave_group,

    joining=False,

)

# ---------------------------------------------------------

# BOOST

# ---------------------------------------------------------

boost_group = app_commands.Group(

    name="boost",

    description="Configure server boost notifications.",

)

@boost_group.command(

    name="setup",

    description="Choose the channel for boost thank-you messages.",

)

@app_commands.checks.has_permissions(

    manage_guild=True

)

async def boost_setup(

    interaction: discord.Interaction,

    channel: discord.TextChannel,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "This setup command can only be used inside a server.",

        )

        return

    bot.database.update_settings(

        guild.id,

        boost_channel_id=channel.id,

    )

    await interaction.response.send_message(

        (

            "Boost thank-you messages will use "

            f"{channel.mention}."

        ),

        ephemeral=True,

    )

set_group = app_commands.Group(

    name="set",

    description="Change Miu settings for this server.",

)

set_boost_group = app_commands.Group(

    name="boost",

    description="Customize the boost thank-you embed.",

)

@set_boost_group.command(

    name="message",

    description="Customize the boost thank-you embed.",

)

@app_commands.check(

    is_server_owner_or_admin

)

@app_commands.describe(

    title="Optional embed title",

    description="Optional embed description",

    footer="Optional embed footer",

    image_url="Optional image URL; use 'clear' to remove it",

)

async def set_boost_message(

    interaction: discord.Interaction,

    title: str | None = None,

    description: str | None = None,

    footer: str | None = None,

    image_url: str | None = None,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "This setup command can only be used inside a server.",

        )

        return

    if all(

        value is None

        for value in (

            title,

            description,

            footer,

            image_url,

        )

    ):

        await send_ephemeral(

            interaction,

            (

                "Provide at least one boost "

                "message value to update."

            ),

        )

        return

    updates: dict[

        str,

        str | None

    ] = {}

    fields = (

        (

            "boost_title",

            title,

            256,

            "Title",

        ),

        (

            "boost_description",

            description,

            4_096,

            "Description",

        ),

        (

            "boost_footer",

            footer,

            2_048,

            "Footer",

        ),

    )

    for (

        key,

        value,

        maximum,

        label,

    ) in fields:

        if value is None:

            continue

        value = value.strip()

        if not 1 <= len(

            value

        ) <= maximum:

            await send_ephemeral(

                interaction,

                (

                    f"{label} must be between "

                    f"1 and {maximum} characters."

                ),

            )

            return

        updates[key] = value

    if image_url is not None:

        image_url = image_url.strip()

        if image_url.casefold() in {

            "clear",

            "none",

            "off",

        }:

            updates[

                "boost_image_url"

            ] = None

        elif not image_url.startswith(

            (

                "https://",

                "http://",

            )

        ):

            await send_ephemeral(

                interaction,

                (

                    "Image URL must start with "

                    "`https://` or `http://`."

                ),

            )

            return

        else:

            updates[

                "boost_image_url"

            ] = image_url

    bot.database.update_settings(

        guild.id,

        **updates,

    )

    await interaction.response.send_message(

        (

            "Boost message settings saved. "

            "Use `/test boost` to preview the exact embed."

        ),

        ephemeral=True,

    )

set_group.add_command(

    set_boost_group

)

test_group = app_commands.Group(

    name="test",

    description="Preview Miu features.",

)

@test_group.command(

    name="boost",

    description="Preview the boost thank-you embed.",

)

async def test_boost(

    interaction: discord.Interaction,

) -> None:

    guild = guild_or_none(

        interaction

    )

    if guild is None:

        await send_ephemeral(

            interaction,

            "This preview command can only be used inside a server.",

        )

        return

    settings = bot.database.get_settings(

        guild.id

    )

    await interaction.response.send_message(

        embed=build_boost_embed(

            interaction.user,

            settings,

        ),

        ephemeral=True,

        allowed_mentions=(

            discord.AllowedMentions(

                users=True,

                roles=False,

                everyone=False,

            )

        ),

    )

# ---------------------------------------------------------

# REGISTER GROUPS

# ---------------------------------------------------------

bot.tree.add_command(

    autoresponder_group

)

bot.tree.add_command(

    custom_command_group

)

bot.tree.add_command(

    welcome_group

)

bot.tree.add_command(

    leave_group

)

bot.tree.add_command(

    boost_group

)

bot.tree.add_command(

    set_group

)

bot.tree.add_command(

    test_group

)

# ---------------------------------------------------------

# ERROR HANDLER

# ---------------------------------------------------------

@bot.tree.error

async def on_app_command_error(

    interaction: discord.Interaction,

    error: app_commands.AppCommandError,

) -> None:

    original = (

        error.original

        if isinstance(

            error,

            app_commands.CommandInvokeError,

        )

        else error

    )

    if isinstance(

        original,

        app_commands.CommandOnCooldown,

    ):

        message = (

            f"Please wait **"

            f"{human_duration(round(original.retry_after))}"

            f"** before trying again."

        )

    elif isinstance(

        original,

        app_commands.MissingPermissions,

    ):

        message = (

            "You do not have the Discord permission "

            "needed for that command."

        )

    elif isinstance(

        original,

        app_commands.BotMissingPermissions,

    ):

        message = (

            "I am missing a Discord permission "

            "needed for that command."

        )

    elif isinstance(

        original,

        app_commands.NoPrivateMessage,

    ):

        message = (

            "That command can only be used inside a server."

        )

    elif isinstance(

        original,

        app_commands.TransformerError,

    ):

        message = (

            "One of the values provided was invalid."

        )

    elif isinstance(

        original,

        app_commands.CheckFailure,

    ):

        message = (

            "You cannot use that command here."

        )

    else:

        LOGGER.exception(

            "Unhandled slash command error",

            exc_info=original,

        )

        message = (

            "Something went wrong while running "

            "that command. Please try again."

        )

    await send_ephemeral(

        interaction,

        message,

    )

# ---------------------------------------------------------

# MAIN

# ---------------------------------------------------------

def main() -> None:

    logging.basicConfig(

        level=logging.INFO,

        format=(

            "%(asctime)s | "

            "%(levelname)s | "

            "%(name)s | "

            "%(message)s"

        ),

    )

    token = load_discord_token()

    bot.run(

        token,

        reconnect=True,

        log_handler=None,

    )

if __name__ == "__main__":

    main()
