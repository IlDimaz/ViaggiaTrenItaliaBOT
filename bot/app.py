"""Application wiring: build and run the Telegram bot."""
from __future__ import annotations

import asyncio
import logging

from telegram import BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import trenitalia.lefrecce as lf
import trenitalia.viaggiatreno as vt
from config import require_token
from storage import Storage
from . import handlers, notifications

log = logging.getLogger(__name__)


async def _post_init(application: Application) -> None:
    storage: Storage = application.bot_data["storage"]
    await storage.connect()
    try:
        await application.bot.set_my_commands(
            [
                BotCommand("start", "welcome"),
                BotCommand("partenze", "tabellone partenze"),
                BotCommand("treno", "stato di un treno"),
                BotCommand("stazione", "imposta la stazione preferita"),
                BotCommand("prezzi", "attiva/disattiva i prezzi"),
                BotCommand("avvisi", "attiva/disattiva gli avvisi automatici"),
                BotCommand("avvisi_ora", "infomobilità adesso"),
                BotCommand("schedule", "promemoria settimanale prima della partenza"),
                BotCommand("unschedule", "rimuovi un promemoria"),
                BotCommand("segui", "segui un treno"),
                BotCommand("seguiti", "elenco treni seguiti"),
                BotCommand("stop", "interrompi il tracciamento"),
                BotCommand("help", "aiuto"),
            ]
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("set_my_commands failed: %s", exc)
    # Create the background pollers on the running loop. Using
    # Application.create_task here would warn because the application is not
    # yet in the RUNNING state, so we create and track the tasks ourselves.
    loop = asyncio.get_running_loop()
    application.bot_data["tasks"] = [
        loop.create_task(notifications.run_poller(application)),
        loop.create_task(notifications.run_tracker(application)),
        loop.create_task(notifications.run_scheduler(application)),
    ]
    log.info("Bot ready.")


async def _post_shutdown(application: Application) -> None:
    for task in application.bot_data.get("tasks", []):
        task.cancel()
    storage: Storage = application.bot_data["storage"]
    await storage.close()
    await vt.close()
    await lf.close()


def build_application() -> Application:
    application = (
        Application.builder()
        .token(require_token())
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    application.bot_data["storage"] = Storage()

    application.add_handler(CommandHandler("start", handlers.cmd_start))
    application.add_handler(CommandHandler("help", handlers.cmd_help))
    application.add_handler(CommandHandler("prezzi", handlers.cmd_prezzi))
    application.add_handler(CommandHandler("avvisi", handlers.cmd_avvisi))
    application.add_handler(CommandHandler("avvisi_ora", handlers.cmd_avvisi_ora))
    application.add_handler(CommandHandler("stazione", handlers.cmd_stazione))
    application.add_handler(CommandHandler("partenze", handlers.cmd_partenze))
    application.add_handler(CommandHandler("treno", handlers.cmd_treno))
    application.add_handler(CommandHandler("segui", handlers.cmd_segui))
    application.add_handler(CommandHandler("stop", handlers.cmd_stop))
    application.add_handler(CommandHandler("seguiti", handlers.cmd_seguiti))
    application.add_handler(CommandHandler("schedule", handlers.cmd_schedule))
    application.add_handler(CommandHandler("unschedule", handlers.cmd_unschedule))
    application.add_handler(CommandHandler("stats", handlers.cmd_stats))
    application.add_handler(CallbackQueryHandler(handlers.callback_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.text_handler)
    )
    application.add_error_handler(handlers.error_handler)
    return application


def _ensure_event_loop() -> None:
    # python-telegram-bot 21 calls asyncio.get_event_loop() in run_polling();
    # Python 3.12+ (notably 3.14) no longer creates a default loop implicitly.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    application = build_application()
    log.info("Starting polling...")
    _ensure_event_loop()
    application.run_polling(allowed_updates=None)


if __name__ == "__main__":
    main()
