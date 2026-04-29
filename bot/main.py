import asyncio
import logging
import os
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode


from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from handlers import lessons, profile, start, reset

import traceback
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent
from apscheduler.triggers.cron import CronTrigger


from database import async_session, User, init_db


# Теперь можно читать

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

dp = Dispatcher()


from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from database import async_session, User
from services.streak_service import check_streak_loss

async def send_streak_reminders(bot):
    """Ежедневная проверка серий (в 19:00): уведомления о сгорании и заморозке."""
    async with async_session() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()

        for user in users:
            await check_streak_loss(session, user, bot=bot)


load_dotenv()


logger = logging.getLogger(__name__)

@dp.errors()
async def error_handler(event: ErrorEvent):
    """Глобальный обработчик необработанных исключений"""
    exception = event.exception
    update = event.update

    full_traceback = traceback.format_exception(type(exception), exception, exception.__traceback__)
    logger.error("🚨 Ошибка бота:\n%s", "".join(full_traceback))

    return True


async def main():
    logging.basicConfig(level=logging.INFO)

    from services.llm_interface import llm_service
    logging.info("LLM сервис: %s", llm_service.__class__.__name__)

    if not TOKEN:
        logging.error("Не найден BOT_TOKEN в переменных окружения")
        raise RuntimeError("Не найден BOT_TOKEN в переменных окружения")

    await init_db()
    logging.info("База данных инициализирована")

    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # Регистрируем роутеры
    dp.include_router(profile.router)
    dp.include_router(reset.router)  # Добавлен reset
    dp.include_router(lessons.router)
    dp.include_router(start.router)

    

    # Планировщик
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        send_streak_reminders,
        "cron",
        hour=19,
        minute=0,
        args=[bot],
        id="streak_reminders",
        replace_existing=True
    )
    scheduler.start()
    logging.info("Планировщик напоминаний запущен")

    try:
        logging.info("Бот запущен...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except KeyboardInterrupt:
        logging.info("Бот остановлен пользователем")
    finally:
        scheduler.shutdown()
        await bot.session.close()
        logging.info("Сессия бота закрыта")


if __name__ == "__main__":
    asyncio.run(main())
