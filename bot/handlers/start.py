import logging
from html import escape

from aiogram import Router, F
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from services.llm_interface import llm_service
from services.course_service import CourseService
from sqlalchemy import select
from database import async_session, get_or_create_user, User, UserProgress
from aiogram import types

logger = logging.getLogger(__name__)
router = Router()
course_service = CourseService()


def build_main_keyboard(courses: list) -> ReplyKeyboardMarkup:
    """Строит обычную клавиатуру из списка курсов"""
    keyboard = []
    for course in courses:
        title = course.get("title", course.get("course_id", "Курс"))
        keyboard.append([KeyboardButton(text=f"🚀 {title}")])
    
    # Добавляем кнопку "▶️ Продолжить" в ряд с Профилем и Помощью
    keyboard.append([
        KeyboardButton(text="👤 Профиль"),
        KeyboardButton(text="▶️ Продолжить"),  # Новая кнопка
        KeyboardButton(text="ℹ️ Помощь")
    ])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def build_inline_course_keyboard(courses: list) -> InlineKeyboardMarkup:
    """Строит inline-клавиатуру для выбора курса"""
    keyboard = []
    for course in courses:
        course_id = course.get("course_id")
        title = course.get("title", course.get("course_id", "Курс"))
        keyboard.append([
            InlineKeyboardButton(text=f"🚀 {title}", callback_data=f"course_{course_id}")
        ])
    
    # Добавляем inline-кнопку "▶️ Продолжить"
    keyboard.append([
        InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
        InlineKeyboardButton(text="▶️ Продолжить", callback_data="continue_inline"),  # ← Новая кнопка
        InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help_inline")
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

HELP_TEXT = """
📚 **Быстрая справка:**

**🎓 Обучение:**
/start — Главное меню и выбор курса
/continue — Продолжить с последнего урока 📍
▶️ Продолжить — Кнопка в главном меню (то же, что /continue)
/reset — Сбросить прогресс текущего курса ⚠️
/profile — Твой прогресс и статистика 🔥
/stats — Статистика ответов на задачи 📊
/quiz — Быстрая практика по случайной теме 🎲

**💡 Как учиться:**
1. Выбери курс в меню
2. Читай теорию и смотри видео
3. Решай задачи — вводи ответ текстом (например: «3», «x=2», «нет»)
4. Поддерживай серию дней! 🔥

**Застрял?**
Нажми «❓ Не понял» под уроком — ИИ объяснит тему.
/help — Эта справка
"""


@router.message(CommandStart())
async def start(message: Message):
    async with async_session() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
        )
        is_new = user.selected_course is None
        logger.info("User %s: %s", message.from_user.id, "registered" if is_new else "returned")

    courses = course_service.get_all_courses()

    if is_new:
        await message.answer(
            "👋 Как это работает:\n\n"
            "📚 Выбери курс → читай теорию → решай задачи\n"
            "🔥 Занимайся каждый день — строй серию\n"
            "🤖 Жми «Не понял» — ИИ объяснит тему\n"
            "🏆 Зарабатывай достижения и следи за прогрессом",
            reply_markup=build_main_keyboard(courses),
        )
        welcome_text = f"Привет, {message.from_user.first_name}! Выбери курс, чтобы начать:"
    else:
        await message.answer(
            f"С возвращением, {message.from_user.first_name}!",
            reply_markup=build_main_keyboard(courses),
        )
        welcome_text = "Выбери курс:"

    await message.answer(welcome_text, reply_markup=build_inline_course_keyboard(courses))


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def help_message_handler(message: Message):
    """Обработчик справки для текстовых команд"""
    await message.answer(HELP_TEXT, parse_mode="Markdown")

@router.callback_query(F.data == "help_inline")
async def help_callback_handler(callback: types.CallbackQuery):
    """Обработчик справки для inline-кнопки"""
    await callback.message.answer(HELP_TEXT, parse_mode="Markdown")
    await callback.answer()  # Просто закрываем "часики" на кнопке


# Обработчик inline-кнопки "▶️ Продолжить"
@router.callback_query(F.data == "continue_inline")
async def continue_inline_handler(callback: types.CallbackQuery):
    """Продолжение обучения через inline-кнопку"""
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
    
    if not user.current_lesson_id:
        await callback.answer("📚 Ты ещё не начал обучение. Выбери курс!", show_alert=True)
        return
    
    # Перенаправляем на показ урока (код как в continue_learning)
    lesson = course_service.get_lesson(user.current_lesson_id)
    if not lesson:
        await callback.answer("⚠️ Урок не найден. Начни с /start", show_alert=True)
        return

    # Отправляем урок как новое сообщение (не edit, чтобы не ломать inline)
    text = f"📚 <b>{escape(lesson['title'])}</b>\n"
    text += f"<i>Модуль: {escape(lesson['module_title'])}</i>\n\n"
    text += escape(lesson["content"])
    text += f"\n\n💡 <b>Главное за 1 минуту:</b>\n{escape(lesson['summary'])}"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Понял, дальше", callback_data=f"next_lesson_{user.current_lesson_id}"),
            InlineKeyboardButton(text="❓ Не понял", callback_data=f"ask_ai_{user.current_lesson_id}"),
        ],
        [
            InlineKeyboardButton(text="📝 Хочу практику", callback_data=f"practice_{user.current_lesson_id}"),
        ],
    ])
    if lesson.get("video_resources"):
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="🎥 Видео по теме", callback_data=f"videos_{user.current_lesson_id}")
        ])
    
    await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


# Обработчик текстовой кнопки "▶️ Продолжить"
@router.message(F.text == "▶️ Продолжить")
async def continue_button_handler(message: Message):
    """Продолжение обучения через текстовую кнопку в главном меню"""
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
    
    if not user.current_lesson_id:
        await message.answer("📚 Ты ещё не начал обучение. Выбери курс в /start")
        return
    
    # Перенаправляем на существующую функцию continue_learning
    await continue_learning(message)



def _build_course_modules_text_keyboard(course: dict, course_id: str) -> tuple[str, InlineKeyboardMarkup]:
    text = f"📚 <b>{escape(course['title'])}</b>\n\n"
    text += f"{escape(course.get('description', ''))}\n\n"
    text += f"📦 <b>Модули:</b>\n\n"
    keyboard = []
    for i, module in enumerate(course.get("modules", []), 1):
        module_id = module["module_id"]
        title = module["title"]
        lessons_count = len(module.get("lessons", []))
        text += f"{i}. {escape(title)} ({lessons_count} уроков)\n"
        keyboard.append([
            InlineKeyboardButton(text=f"📖 {title}", callback_data=f"module_{course_id}:{module_id}")
        ])
    keyboard.append([InlineKeyboardButton(text="↩ Назад к курсам", callback_data="start_inline")])
    return text, InlineKeyboardMarkup(inline_keyboard=keyboard)


@router.callback_query(F.data.startswith("course_"))
async def show_course_modules(callback):
    """Показывает модули выбранного курса. При смене курса запрашивает подтверждение."""
    course_id = callback.data.removeprefix("course_")
    course = course_service.get_course_by_id(course_id)

    if not course:
        await callback.answer("❌ Курс не найден", show_alert=True)
        return

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        current_course = user.selected_course

    new_title = course.get("title", course_id)
    if current_course and current_course != new_title:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, сменить", callback_data=f"confirm_switch_{course_id}")],
            [InlineKeyboardButton(text="❌ Остаться", callback_data="cancel_switch")],
        ])
        await callback.message.edit_text(
            f"⚠️ <b>Сменить курс?</b>\n\n"
            f"Прогресс по текущему курсу сохранится, "
            f"но позиция урока сбросится.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        await callback.answer()
        return

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        user.selected_course = new_title
        await session.commit()

    text, keyboard = _build_course_modules_text_keyboard(course, course_id)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_switch_"))
async def confirm_course_switch(callback):
    """Подтверждение смены курса: сбрасывает current_lesson_id и открывает новый курс."""
    course_id = callback.data.removeprefix("confirm_switch_")
    course = course_service.get_course_by_id(course_id)

    if not course:
        await callback.answer("❌ Курс не найден", show_alert=True)
        return

    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        user.selected_course = course.get("title", course_id)
        user.current_lesson_id = None
        await session.commit()

    text, keyboard = _build_course_modules_text_keyboard(course, course_id)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "cancel_switch")
async def cancel_course_switch(callback):
    """Отказ от смены курса: возвращает к текущему курсу или списку курсов."""
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        current_title = user.selected_course

    courses = course_service.get_all_courses()
    current_course = next((c for c in courses if c.get("title") == current_title), None)

    if current_course:
        course_id = current_course["course_id"]
        text, keyboard = _build_course_modules_text_keyboard(current_course, course_id)
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await callback.message.edit_text(
            "👋 <b>Выбери курс для обучения:</b>",
            reply_markup=build_inline_course_keyboard(courses),
            parse_mode="HTML"
        )
    await callback.answer()


@router.callback_query(F.data.startswith("module_"))
async def show_module_lessons(callback):
    """Показывает уроки модуля со статусами прогресса (✅/▶️/🔒)"""

    data = callback.data.removeprefix("module_")
    if ":" not in data:
        logger.error(f"Invalid callback format (expected ':'): {callback.data}")
        await callback.answer("❌ Ошибка формата кнопки", show_alert=True)
        return

    course_id, module_id = data.split(":", 1)
    module = course_service.get_module(course_id, module_id)
    if not module:
        logger.error(f"Module not found: course={course_id}, module={module_id}")
        await callback.answer("❌ Модуль не найден", show_alert=True)
        return

    # Плоский список всех lesson_id курса по порядку (для определения доступности)
    course = course_service.get_course_by_id(course_id)
    all_lesson_ids = [
        lesson["lesson_id"]
        for mod in course.get("modules", [])
        for lesson in mod.get("lessons", [])
    ]

    # Получаем завершённые уроки пользователя
    async with async_session() as session:
        user_subq = select(User.id).where(User.telegram_id == str(callback.from_user.id)).scalar_subquery()
        result = await session.execute(
            select(UserProgress.lesson_id).where(
                UserProgress.user_id == user_subq,
                UserProgress.status == "completed"
            )
        )
        completed = {row for row in result.scalars()}

    def get_status(lesson_id: str) -> str:
        if lesson_id in completed:
            return "completed"
        idx = all_lesson_ids.index(lesson_id) if lesson_id in all_lesson_ids else -1
        if idx == 0 or (idx > 0 and all_lesson_ids[idx - 1] in completed):
            return "available"
        return "locked"

    STATUS_ICON = {"completed": "✅", "available": "▶️", "locked": "🔒"}

    text = f"📖 <b>{escape(module['title'])}</b>\n\n"
    keyboard = []

    for i, lesson in enumerate(module.get("lessons", []), 1):
        lesson_id = lesson["lesson_id"]
        title = lesson["title"]
        status = get_status(lesson_id)
        icon = STATUS_ICON[status]

        summary = lesson.get("summary", "")
        if summary:
            short = escape(summary)
            if len(short) > 60:
                short = short[:57] + "..."
            text += f"{i}. {icon} <b>{escape(title)}</b>\n   <i>{short}</i>\n\n"
        else:
            text += f"{i}. {icon} <b>{escape(title)}</b>\n\n"

        if status == "locked":
            cb_data = f"locked_{lesson_id}"
        else:
            cb_data = f"lesson_{lesson_id}"

        keyboard.append([
            InlineKeyboardButton(text=f"{icon} {escape(title)}", callback_data=cb_data)
        ])

    keyboard.append([
        InlineKeyboardButton(text="↩ Назад к модулям", callback_data=f"course_{course_id}")
    ])

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("locked_"))
async def locked_lesson_handler(callback):
    """Уведомление при нажатии на заблокированный урок"""
    await callback.answer("🔒 Сначала пройди предыдущий урок!", show_alert=True)


@router.callback_query(F.data == "start_inline")
async def back_to_courses_inline(callback):
    """Возврат к списку курсов (inline-версия)"""
    courses = course_service.get_all_courses()
    text = "👋 <b>Выбери курс для обучения:</b>"
    await callback.message.edit_text(
        text,
        reply_markup=build_inline_course_keyboard(courses),
        parse_mode="HTML"
    )
    await callback.answer()


# 🔥 Оставляем поддержку старых кнопок (для совместимости)
@router.message(F.text.startswith("🚀 "))
async def course_selected_by_text(message: Message):
    """Обработчик выбора курса через текстовую кнопку (старый формат)"""
    course_name = message.text.removeprefix("🚀 ").strip()
    
    # Ищем курс по названию в JSON
    courses = course_service.get_all_courses()
    course = next((c for c in courses if c.get("title") == course_name), None)
    
    if not course:
        await message.answer(f"❌ Курс «{course_name}» не найден. Попробуй /start")
        return
    
    # Сохраняем выбор в БД
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        user.selected_course = course_name
        await session.commit()
    
    # Показываем модули курса
    text = f"📚 <b>{course['title']}</b>\n\n"
    text += f"{course.get('description', '')}\n\n"
    text += f"📦 <b>Модули:</b>\n\n"
    
    keyboard = []
    for i, module in enumerate(course.get("modules", []), 1):
        module_id = module["module_id"]
        title = module["title"]
        lessons_count = len(module.get("lessons", []))
        text += f"{i}. {title} ({lessons_count} уроков)\n"
        keyboard.append([
            InlineKeyboardButton(text=f"📖 {title}", callback_data=f"module_{course['course_id']}:{module_id}")
        ])
    
    keyboard.append([
        InlineKeyboardButton(text="↩ Назад к курсам", callback_data="start_inline")
    ])
    
    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )


@router.message(Command("ask"))
async def ask_handler(message: Message):
    question = message.text.removeprefix("/ask").strip()
    if not question:
        await message.answer("Напиши вопрос после команды, например:\n/ask Что такое дискриминант?")
        return
    await message.answer("⏳ Думаю...")
    answer = await llm_service.explain_topic(topic="общая тема", user_question=question)
    await message.answer(f"💡 {answer}")


@router.message(Command("continue"))
async def continue_learning(message: Message):
    """Продолжить с последнего урока"""
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)

    if not user.current_lesson_id:
        await message.answer("📚 Ты ещё не начал обучение. Выбери курс в /start")
        return

    lesson = course_service.get_lesson(user.current_lesson_id)
    if not lesson:
        await message.answer("⚠️ Урок не найден. Начни заново: /start")
        return

    lesson_id = user.current_lesson_id
    text = f"📚 <b>{escape(lesson['title'])}</b>\n"
    text += f"<i>Модуль: {escape(lesson['module_title'])}</i>\n\n"
    text += escape(lesson["content"])
    text += f"\n\n💡 <b>Главное за 1 минуту:</b>\n{escape(lesson['summary'])}"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Понял, дальше", callback_data=f"next_lesson_{lesson_id}"),
            InlineKeyboardButton(text="❓ Не понял", callback_data=f"ask_ai_{lesson_id}"),
        ],
        [
            InlineKeyboardButton(text="📝 Хочу практику", callback_data=f"practice_{lesson_id}"),
        ],
    ])
    if lesson.get("video_resources"):
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="🎥 Видео по теме", callback_data=f"videos_{lesson_id}")
        ])
    if lesson.get("course_id") and lesson.get("module_id"):
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text="↩ К модулю",
                callback_data=f"module_{lesson['course_id']}:{lesson['module_id']}",
            )
        ])

    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(StateFilter(None))
async def fallback_handler(message: Message):
    courses = course_service.get_all_courses()
    # ПРОВЕРКА: если это команда — пропускаем
    if message.text and message.text.startswith("/"):
        return  # Важно! Пропускаем команды
    
    await message.answer(
        "Не понял команду. Выбери курс из меню или напиши /help.",
        reply_markup=build_main_keyboard(courses)
    )