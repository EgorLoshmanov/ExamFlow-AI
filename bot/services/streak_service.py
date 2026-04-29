from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession
from database import User

if TYPE_CHECKING:
    from aiogram import Bot


async def update_streak(session: AsyncSession, user: User, bot: "Bot | None" = None):
    """
    Обновляет серию дней при активности пользователя.
    Вызывать после каждого завершённого урока/задачи.
    """
    now = datetime.utcnow()
    last_activity = user.last_activity_date or now

    delta_days = (now.date() - last_activity.date()).days

    notification: str | None = None

    if delta_days == 0:
        user.last_activity_date = now
    elif delta_days == 1:
        user.streak_count += 1
        user.last_activity_date = now
    else:
        if user.freeze_available:
            saved_streak = user.streak_count
            user.freeze_available = False
            user.streak_count += 1
            user.last_activity_date = now
            notification = f"❄️ Заморозка спасла твою серию {saved_streak} дней!"
        else:
            burned = user.streak_count
            user.streak_count = 0
            user.last_activity_date = now
            if burned > 0:
                notification = (
                    f"💔 Серия {burned} дней сгорела. "
                    "Начни новую — первый шаг уже сделан!"
                )

    await session.commit()

    if bot and notification:
        try:
            await bot.send_message(chat_id=user.telegram_id, text=notification)
        except Exception:
            pass


async def check_streak_loss(session: AsyncSession, user: User, bot: "Bot | None" = None) -> bool:
    """
    Проверка: потерял ли пользователь серию за вчерашний день.
    Вызывать планировщиком раз в сутки.
    Возвращает True, если серия сгорела или заморозка была потрачена.
    """
    now = datetime.utcnow()
    last_activity = user.last_activity_date or now
    delta_days = (now.date() - last_activity.date()).days

    if delta_days < 2:
        return False

    notification: str | None = None

    if user.freeze_available:
        saved_streak = user.streak_count
        user.freeze_available = False
        notification = f"❄️ Заморозка спасла твою серию! Осталось: {saved_streak} дней."
    else:
        burned = user.streak_count
        user.streak_count = 0
        if burned > 0:
            notification = f"💔 Серия {burned} дней сгорела. Начни новую прямо сейчас!"

    user.last_activity_date = now
    await session.commit()

    if bot and notification:
        try:
            await bot.send_message(chat_id=user.telegram_id, text=notification)
        except Exception:
            pass

    return True


async def grant_freeze(session: AsyncSession, user: User, count: int = 1):
    """Выдать пользователю заморозку серии"""
    user.freeze_available = True
    # Можно хранить несколько: user.freeze_count += count
    await session.commit()