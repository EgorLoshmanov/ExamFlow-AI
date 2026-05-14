import logging
from datetime import datetime, timezone

from aiogram import Bot
from sqlalchemy import select

from database import async_session, User
from services.streak_service import check_streak_loss

logger = logging.getLogger(__name__)


async def send_daily_reminders(bot: Bot) -> None:
    """Отправляет напоминание пользователям с активной серией, которые не занимались сегодня."""
    today = datetime.now(timezone.utc).date()

    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.streak_count > 0)
        )
        users = result.scalars().all()

        sent = 0
        for user in users:
            # Уже занимался сегодня — не напоминаем
            if user.last_activity_date:
                last_active = user.last_activity_date
                if last_active.tzinfo is None:
                    last_active = last_active.replace(tzinfo=timezone.utc)
                if last_active.date() >= today:
                    continue

            # Уже получал напоминание сегодня — не дублируем
            if user.last_reminder_date:
                last_reminded = user.last_reminder_date
                if last_reminded.tzinfo is None:
                    last_reminded = last_reminded.replace(tzinfo=timezone.utc)
                if last_reminded.date() >= today:
                    continue

            try:
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=f"🔥 Не потеряй серию! У тебя {user.streak_count} дней подряд — займись сегодня.",
                )
                user.last_reminder_date = datetime.now(timezone.utc)
                sent += 1
            except Exception as e:
                logger.warning("Не удалось отправить напоминание пользователю %s: %s", user.telegram_id, e)

        if sent:
            await session.commit()
            logger.info("Напоминания о серии отправлены: %d пользователей", sent)


async def check_all_streaks(bot: Bot) -> None:
    """Ежедневная задача планировщика (19:00):
    1. Проверяет сгорание/заморозку серий у всех пользователей.
    2. Отправляет напоминания тем, кто ещё не занимался сегодня.
    """
    async with async_session() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()
        for user in users:
            await check_streak_loss(session, user, bot=bot)

    await send_daily_reminders(bot)
