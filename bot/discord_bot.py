import asyncio
import logging
import os

import discord

from ai.graph import run_graph
from bot.message import Message
from utils import redact

logger = logging.getLogger(__name__)

_intents = discord.Intents.default()
_intents.message_content = True
_bot = discord.Client(intents=_intents)


@_bot.event
async def on_ready() -> None:
    logger.info("Discord bot ready as %s", _bot.user)


@_bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    if not isinstance(message.channel, discord.DMChannel):
        return  # DM-only

    user_id = f"discord:{message.author.id}"
    chat_id = str(message.channel.id)
    logger.info("Discord DM [%s]: %s", user_id, redact.text(message.content))

    msg = Message(platform="discord", user_id=user_id, chat_id=chat_id, text=message.content)
    try:
        async with message.channel.typing():
            reply = await asyncio.to_thread(run_graph, msg)
    except Exception as exc:
        logger.error("Discord handler error: %s", exc, exc_info=True)
        reply = f"⚠️ Error: {type(exc).__name__}: {exc}"

    await message.channel.send(reply[:2000])


def run_discord() -> None:
    token = os.environ["DISCORD_BOT_TOKEN"]
    logger.info("Discord bot starting...")
    _bot.run(token, log_handler=None)
