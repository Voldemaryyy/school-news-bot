#!/usr/bin/env python3
# ======================================================
# bot.py — точка входу Telegram-бота
# Запуск: python bot.py
# Вимога: python-telegram-bot >= 20.0
# ======================================================

import sys
import asyncio
import logging

try:
    import telegram
    _ver = tuple(int(x) for x in telegram.__version__.split(".")[:2])
    if _ver < (20, 0):
        print(
            f"\n❌ Встановлена версія python-telegram-bot: {telegram.__version__}\n"
            f"   Потрібна версія >= 20.0\n\n"
            f"   Виконайте:\n"
            f"   pip install 'python-telegram-bot>=20.0,<22.0' --upgrade\n"
        )
        sys.exit(1)
except ImportError:
    print(
        "\n❌ Бібліотека python-telegram-bot не знайдена.\n\n"
        "   Виконайте:\n"
        "   pip install 'python-telegram-bot>=20.0,<22.0'\n"
    )
    sys.exit(1)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

from config import BOT_TOKEN
from states import (
    TITLE, TEXT, PHOTOS, CATEGORY,
    MAIN_PHOTO, INSERT_PHOTOS, PREVIEW, HTML_PREVIEW, SCHEDULE_DATE,
)
from handlers.news import (
    cmd_start,
    handle_title,
    handle_text,
    handle_photo,
    handle_done_photos,
    handle_category,
    handle_main_photo,
    handle_insert_photo,
    handle_preview_action,
    handle_html_confirm,
    handle_schedule_date,
    handle_schedule_time,
    cmd_cancel,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def build_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CommandHandler("news",  cmd_start),
        ],
        states={
            TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_title),
            ],
            TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
            ],
            PHOTOS: [
                MessageHandler(filters.PHOTO, handle_photo),
                CommandHandler("done", handle_done_photos),
            ],
            CATEGORY: [
                CallbackQueryHandler(handle_category, pattern=r"^cat_\d+$"),
            ],
            MAIN_PHOTO: [
                CallbackQueryHandler(handle_main_photo, pattern=r"^main_\d+$"),
            ],
            INSERT_PHOTOS: [
                CallbackQueryHandler(handle_insert_photo, pattern=r"^ins_"),
            ],
            PREVIEW: [
                CallbackQueryHandler(handle_preview_action, pattern=r"^publish_"),
            ],
            HTML_PREVIEW: [
                CallbackQueryHandler(handle_html_confirm, pattern=r"^confirm_"),
            ],
            SCHEDULE_DATE: [
                # Кнопки вибору дня / ручний режим / скасування
                CallbackQueryHandler(handle_schedule_date, pattern=r"^sched_"),
                # Введення часу (ГГ:ХХ) або повної дати (ДД.ММ.РРРР ГГ:ХХ)
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_schedule_time),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
        ],
        allow_reentry=True,
    )


async def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(build_conversation())

    logger.info(
        "Бот запущено (PTB %s, Python %s). Очікування повідомлень…",
        telegram.__version__, sys.version.split()[0],
    )

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            logger.info("Зупиняємо бота…")
            await app.updater.stop()
            await app.stop()


if __name__ == "__main__":
    asyncio.run(main())