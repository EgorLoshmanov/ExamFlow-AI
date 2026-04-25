import json
import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func
from database import async_session, get_user_profile, TaskHistory, User
from services.achievements import ACHIEVEMENTS
from services.course_service import CourseService

logger = logging.getLogger(__name__)

router = Router()
course_service = CourseService()  #  Инициализируем сервис

COURSES_JSON_PATH = Path(__file__).parent.parent / "data" / "courses.json"



@router.message(Command("profile"))
async def profile_command_handler(message: Message, state: FSMContext):
    """Обработчик команды /profile — сбрасывает состояние"""
    await state.clear()  # Сбрасываем любое активное состояние
    await profile_handler(message)

@router.message(F.text == "👤 Профиль")
async def profile_button_handler(message: Message, state: FSMContext):
    """Обработчик кнопки профиля — сбрасывает состояние"""
    await state.clear()  # Сбрасываем любое активное состояние
    await profile_handler(message)

@router.callback_query(F.data == "profile")
async def profile_callback(callback: types.CallbackQuery):
    await profile_handler(callback.message, user_id=callback.from_user.id)
    await callback.answer()

def _load_courses_index() -> dict[str, dict]:
    """Возвращает {course_id: {total_lessons, modules: {module_id: {title, lesson_ids}}}}."""
    try:
        with open(COURSES_JSON_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    index = {}
    
    # Проверяем формат JSON: новый (с массивом courses) или старый
    if "courses" in data:
        # Новый формат: несколько курсов
        courses_list = data["courses"]
    else:
        # Старый формат: один курс
        courses_list = [data]
    
    for course in courses_list:
        course_id = course.get("course_id")
        if not course_id:
            continue
            
        modules = {}
        total = 0
        for module in course.get("modules", []):
            lesson_ids = [l["lesson_id"] for l in module.get("lessons", [])]
            modules[module["module_id"]] = {
                "title": module["title"],
                "lesson_ids": lesson_ids,
            }
            total += len(lesson_ids)
        
        index[course_id] = {
            "title": course.get("title", "Без названия"),
            "total_lessons": total,
            "modules": modules
        }
    
    return index


def _streak_bar(streak: int) -> str:
    if streak == 0:
        return "0 дней"
    fire = min(streak // 3 + 1, 5)
    return "🔥" * fire + f" {streak} дн."


def _progress_bar(done: int, total: int) -> str:
    if total == 0:
        return "—"
    pct = done / total
    filled = round(pct * 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"{bar} {done}/{total}"


async def _get_weak_topics(session, user: User, min_attempts: int = 2, top_n: int = 3) -> list[tuple[str, int]]:
    """Возвращает топ-N слабых тем: [(lesson_title, percent_correct), ...]"""
    result = await session.execute(
        select(TaskHistory).where(TaskHistory.user_id == user.id)
    )
    rows = result.scalars().all()

    totals: dict[str, int] = defaultdict(int)
    corrects: dict[str, int] = defaultdict(int)
    for row in rows:
        key = row.lesson_id or "unknown"
        totals[key] += 1
        if row.is_correct:
            corrects[key] += 1

    stats = []
    for lesson_id, total in totals.items():
        if total < min_attempts:
            continue
        percent = round(corrects[lesson_id] / total * 100)
        lesson = course_service.get_lesson(lesson_id)
        title = lesson["title"] if lesson else lesson_id
        stats.append((title, percent))

    stats.sort(key=lambda x: x[1])
    return stats[:top_n]


def _readiness_label(pct: int) -> str:
    if pct < 30:
        return "Только начало пути"
    if pct < 60:
        return "Хороший прогресс"
    if pct <= 85:
        return "Ты почти готов"
    return "Отличная форма!"


async def _get_readiness(session, user: User, courses_index: dict) -> int:
    """Возвращает % готовности к экзамену по формуле 60/40."""
    total_lessons = sum(c["total_lessons"] for c in courses_index.values())
    lessons_done = sum(1 for p in user.progress if p.status == "completed")

    result = await session.execute(
        select(TaskHistory).where(TaskHistory.user_id == user.id)
    )
    history = result.scalars().all()
    avg_score = (sum(1 for r in history if r.is_correct) / len(history)) if history else 0

    lesson_ratio = (lessons_done / total_lessons) if total_lessons else 0
    readiness = lesson_ratio * 0.6 + avg_score * 0.4
    return round(readiness * 100)


async def profile_handler(message: Message, user_id: int | None = None):
    courses_index = _load_courses_index()

    async with async_session() as session:
        user = await get_user_profile(session, user_id or message.from_user.id)
        if user is None:
            await message.answer("Профиль не найден. Напиши /start, чтобы зарегистрироваться.")
            return

        today = date.today()

        lessons_today = sum(
            1 for p in user.progress
            if p.status == "completed"
            and p.completed_at
            and p.completed_at.date() == today
        )

        start_of_day = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
        tasks_today = await session.scalar(
            select(func.count()).select_from(TaskHistory).where(
                TaskHistory.user_id == user.id,
                TaskHistory.created_at >= start_of_day
            )
        )
        tasks_today = tasks_today or 0

        weak_topics = await _get_weak_topics(session, user)
        readiness_pct = await _get_readiness(session, user, courses_index)

    # --- Серия дней ---
    streak_line = _streak_bar(user.streak_count)
    freeze_line = "❄️ Заморозка: доступна" if user.freeze_available else "❄️ Заморозка: нет"

    # --- 🔥 Текущий урок (для быстрого продолжения) ---
    continue_section = []

    if user.current_lesson_id:
        lesson = course_service.get_lesson(user.current_lesson_id)
        if lesson:
            continue_section = [
                f"",
                f"📍 <b>Следующий урок:</b> {lesson['title']}",
                f"<i>Модуль: {lesson['module_title']}</i>",
            ]

    # --- Клавиатура быстрых действий ---
    action_rows = []
    first_row = []
    if user.current_lesson_id:
        first_row.append(InlineKeyboardButton(text="▶️ Продолжить",
                                              callback_data=f"lesson_{user.current_lesson_id}"))
    first_row.append(InlineKeyboardButton(text="🎲 Практика", callback_data="quiz_inline"))
    action_rows.append(first_row)
    action_rows.append([InlineKeyboardButton(text="📊 Статистика", callback_data="stats_inline")])
    continue_keyboard = InlineKeyboardMarkup(inline_keyboard=action_rows)

    # --- Прогресс по курсам ---
    completed_ids: set[str] = {
        p.lesson_id for p in user.progress if p.status == "completed"
    }
    # Группируем выполненные уроки по course_id
    completed_by_course: dict[str, set[str]] = defaultdict(set)
    for p in user.progress:
        if p.status == "completed" and p.course_id:
            completed_by_course[p.course_id].add(p.lesson_id)

    progress_lines = []
    for course_id, course_data in courses_index.items():
        done_total = len(completed_by_course.get(course_id, set()))
        total = course_data["total_lessons"]
        progress_lines.append(f"\n📚 {course_data['title']}")
        for mod_id, mod_data in course_data["modules"].items():
            done_mod = sum(
                1 for lid in mod_data["lesson_ids"]
                if lid in completed_by_course.get(course_id, set())
            )
            total_mod = len(mod_data["lesson_ids"])
            bar = _progress_bar(done_mod, total_mod)
            progress_lines.append(f"  {mod_data['title']}: {bar}")
        overall_pct = int(done_total / total * 100) if total else 0
        progress_lines.append(f"  Итого: {done_total}/{total} ({overall_pct}%)")

    if not progress_lines:
        progress_lines = ["  Ещё не начато — выбери курс и приступай!"]

    # --- Достижения ---
    earned_ids = {a.achievement_id for a in user.achievements}
    achievement_lines = []
    for ach_id, ach in ACHIEVEMENTS.items():
        if ach_id in earned_ids:
            achievement_lines.append(f"  ✅ {ach['emoji']} {ach['title']} — {ach['desc']}")
        else:
            achievement_lines.append(f"  ⬜ {ach['emoji']} {ach['title']} — {ach['desc']}")

    earned_count = len(earned_ids)
    total_count = len(ACHIEVEMENTS)

    # --- Сборка сообщения ---
    lines = [
        f"👤 <b>Личный кабинет</b>",
        f"",
        f"🔥 <b>Серия дней:</b> {streak_line}",
        freeze_line,
    ]

    # 🔥 Добавляем секцию текущего урока
    lines.extend(continue_section)

    # --- Слабые темы ---
    if weak_topics:
        weak_lines = [f"  • {title} — {pct}%" for title, pct in weak_topics]
    else:
        weak_lines = ["  Реши задачи, чтобы увидеть слабые темы"]

    readiness_bar = _progress_bar(readiness_pct, 100)
    readiness_label = _readiness_label(readiness_pct)

    lessons_text = "1/1 ✅" if lessons_today >= 1 else f"{lessons_today}/1"
    tasks_text = "5/5 ✅" if tasks_today >= 5 else f"{tasks_today}/5"

    lines += [
        f"",
        f"📅 <b>Сегодня:</b>",
        f"• Уроков: {lessons_text}",
        f"• Задач: {tasks_text}",
        f"",
        f"🎯 <b>Готовность к экзамену: {readiness_pct}%</b>",
        f"  {readiness_bar}",
        f"  {readiness_label}",
        f"",
        f"📊 <b>Прогресс по курсам:</b>",
    ] + progress_lines + [
        f"",
        f"📉 <b>Слабые темы:</b>",
    ] + weak_lines + [
        f"",
        f"🏅 <b>Достижения ({earned_count}/{total_count}):</b>",
    ] + achievement_lines + [
        f"",
        f"💡 <b>Команды:</b> /help — справка"
    ]

    await message.answer(
        "\n".join(lines),
        reply_markup=continue_keyboard,
        parse_mode="HTML"
    )


@router.message(Command("stats"))
async def stats_handler(message: Message, user_id: int | None = None):
    async with async_session() as session:
        user = await get_user_profile(session, user_id or message.from_user.id)
        if user is None:
            await message.answer("Профиль не найден. Напиши /start.")
            return

        result = await session.execute(
            select(TaskHistory).where(TaskHistory.user_id == user.id)
        )
        history = result.scalars().all()

    if not history:
        await message.answer("📊 Ты ещё не решал задачи. Начни практику в любом уроке!")
        return

    total = len(history)
    correct = sum(1 for r in history if r.is_correct)
    incorrect = total - correct
    correct_pct = round(correct / total * 100)
    incorrect_pct = 100 - correct_pct

    today = date.today()
    today_count = sum(
        1 for r in history
        if r.created_at and r.created_at.date() == today
    )

    by_day: dict[date, int] = defaultdict(int)
    for r in history:
        if r.created_at:
            by_day[r.created_at.date()] += 1
    best_day = max(by_day.values()) if by_day else 0

    await message.answer(
        "📊 <b>Статистика задач:</b>\n\n"
        f"• Всего попыток: {total}\n"
        f"• Правильных: {correct} ({correct_pct}%)\n"
        f"• Неправильных: {incorrect} ({incorrect_pct}%)\n"
        f"• Задач сегодня: {today_count}\n"
        f"• Лучший день: {best_day} задач",
        parse_mode="HTML"
    )


@router.callback_query(F.data == "stats_inline")
async def stats_inline_handler(callback: types.CallbackQuery):
    await stats_handler(callback.message, user_id=callback.from_user.id)
    await callback.answer()