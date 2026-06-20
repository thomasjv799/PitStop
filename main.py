import logging
import os
import threading

from dotenv import load_dotenv


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> None:
    _configure_logging()
    load_dotenv()

    # Telegram is blocked in India; disabled by default. Set ENABLE_TELEGRAM to
    # re-enable it as a secondary transport. Discord is the required primary.
    enable_telegram = os.environ.get("ENABLE_TELEGRAM", "").lower() in ("1", "true", "yes")

    if enable_telegram and os.environ.get("TELEGRAM_BOT_TOKEN"):
        from bot.telegram_bot import run_telegram
        threading.Thread(target=run_telegram, name="telegram-bot", daemon=True).start()

    if not os.environ.get("DISCORD_BOT_TOKEN"):
        raise EnvironmentError("DISCORD_BOT_TOKEN is not set. Add it to .env.")
    from bot.discord_bot import run_discord
    run_discord()


if __name__ == "__main__":
    main()
