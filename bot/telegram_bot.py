import asyncio
import logging
import os

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from ai.graph import run_graph
from bot.message import Message
from utils import redact

logger = logging.getLogger(__name__)


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    user    = update.effective_user
    user_id = f"telegram:{user.id}"
    chat_id = str(update.effective_chat.id)
    logger.info("Telegram [%s]: %s", user_id, redact.text(update.message.text))

    msg = Message(platform="telegram", user_id=user_id, chat_id=chat_id, text=update.message.text)
    try:
        await update.message.chat.send_action("typing")
        reply = await asyncio.to_thread(run_graph, msg)
        await update.message.reply_text(reply[:4096], parse_mode="HTML")
    except Exception as exc:
        logger.error("Telegram handler error: %s", exc, exc_info=True)
        await update.message.reply_text(
            f"⚠️ Something went wrong — please try again.",
            parse_mode=None,
        )


def run_telegram() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    logger.info("Telegram bot starting...")
    app.run_polling(drop_pending_updates=True)
