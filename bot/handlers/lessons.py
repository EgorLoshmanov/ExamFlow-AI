import logging
import random
from datetime import datetime, date, timezone

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command, StateFilter

from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton


from database import async_session, get_or_create_user, get_user_profile, UserProgress, TaskHistory
from services.llm_interface import llm_service
from services.streak_service import update_streak
from services.achievements import check_and_award, ACHIEVEMENTS
from services.course_service import CourseService
from sqlalchemy import select, func

logger = logging.getLogger(__name__)

router = Router()
course_service = CourseService()  #  Инициализируем сервис курсов


class LessonState(StatesGroup):
    answering = State()
    asking_ai = State()
    reviewing = State()


def _result_label(percent: int) -> str:
    if percent < 30:
        return "Только начало пути"
    if percent < 60:
        return "Хороший прогресс"
    if percent <= 85:
        return "Ты почти готов"
    return "Отличная форма!"


def _build_final_screen(correct_count: int, total: int, lesson_id: str, has_errors: bool) -> tuple[str, InlineKeyboardMarkup]:
    incorrect_count = total - correct_count
    percent = round(correct_count / total * 100)
    text = (
        f"🎉 <b>Сессия завершена!</b>\n\n"
        f"✅ Правильно: {correct_count}/{total}\n"
        f"❌ Неправильно: {incorrect_count}/{total}\n"
        f"🏆 Результат: {percent}% — {_result_label(percent)}"
    )
    buttons = []
    if has_errors:
        buttons.append([InlineKeyboardButton(text="📋 Разбор ошибок", callback_data="show_errors")])
    buttons.append([InlineKeyboardButton(text="🔄 Ещё задачи", callback_data=f"practice_{lesson_id}")])
    buttons.append([InlineKeyboardButton(text="↩ Назад к уроку", callback_data=f"lesson_{lesson_id}")])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def _back_to_module_row(lesson: dict) -> list:
    """Возвращает строку кнопки «↩ К модулю» если у урока есть course_id и module_id."""
    course_id = lesson.get("course_id")
    module_id = lesson.get("module_id")
    if course_id and module_id:
        return [[InlineKeyboardButton(
            text="↩ К модулю",
            callback_data=f"module_{course_id}:{module_id}",
        )]]
    return []


def _achievement_text(achievement_id: str) -> str:
    ach = ACHIEVEMENTS.get(achievement_id, {})
    emoji = ach.get("emoji", "🏅")
    title = ach.get("title", achievement_id)
    desc = ach.get("desc", "")
    return f"🏅 Новое достижение: {emoji} <b>{title}</b>! {desc}"


@router.callback_query(F.data.startswith("lesson_"))
async def show_lesson(callback: types.CallbackQuery):
    """ [EF-002] Загружает реальный контент урока из courses.json"""
    from html import escape
    lesson_id = callback.data.removeprefix("lesson_")

    lesson = course_service.get_lesson(lesson_id)
    if not lesson:
        await callback.answer("❌ Урок не найден. Начни с /start", show_alert=True)
        return

    text = f"📚 <b>{escape(lesson['title'])}</b>\n"
    text += f"<i>Модуль: {escape(lesson['module_title'])}</i>\n\n"
    text += escape(lesson["content"])
    text += f"\n\n💡 <b>Главное за 1 минуту:</b>\n{escape(lesson['summary'])}"
    
    # Кнопки навигации
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
    keyboard.inline_keyboard.extend(_back_to_module_row(lesson))

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("next_lesson_"))
async def next_lesson(callback: types.CallbackQuery, state: FSMContext):
    """ [EF-003] Переход к следующему уроку + сохранение прогресса"""
    from html import escape
    from sqlalchemy import select
    
    current_lesson_id = callback.data.removeprefix("next_lesson_")
    await state.clear()
    
    # Получаем информацию о текущем уроке, чтобы узнать course_id
    current_lesson = course_service.get_lesson(current_lesson_id)
    course_id = current_lesson.get("course_id") if current_lesson else None
    
    async with async_session() as session:
        user = await get_or_create_user(session, callback.from_user.id, callback.from_user.username)
        
        # Сохраняем прогресс
        stmt = select(UserProgress).where(
            UserProgress.user_id == user.id,
            UserProgress.lesson_id == current_lesson_id
        )
        result = await session.execute(stmt)
        progress = result.scalar_one_or_none()
        
        if not progress:
            progress = UserProgress(
                user_id=user.id,
                lesson_id=current_lesson_id,
                course_id=course_id  # Сохраняем course_id!
            )
            session.add(progress)
        else:
            progress.course_id = course_id  # Обновляем, если нужно
        
        progress.status = "completed"
        progress.score = max(progress.score or 0, 10)
        progress.completed_at = datetime.utcnow()

        # Находим следующий урок
        next_lesson_id = course_service.get_next_lesson_id(current_lesson_id)
        user.current_lesson_id = next_lesson_id

        await session.commit()
        await update_streak(session, user, bot=callback.bot)

        # Проверяем достижения (перезагружаем с relationship-ами)
        user_full = await get_user_profile(session, callback.from_user.id)
        new_achievements = await check_and_award(session, user_full)

    for ach_id in new_achievements:
        await callback.message.answer(_achievement_text(ach_id), parse_mode="HTML")

    # Проверяем, есть ли следующий урок
    if not next_lesson_id:
        await callback.message.edit_text(
            "🎉 <b>Поздравляем! Курс завершён!</b>\n\n"
            "Ты прошёл все уроки. Теперь можешь:\n"
            "• Повторить сложные темы в профиле\n"
            "• Пройти курс заново для закрепления",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📊 Мой прогресс", callback_data="profile")],
                [InlineKeyboardButton(text="🔄 Начать сначала", callback_data="course_restart")]
            ]),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    
    # Загружаем следующий урок
    lesson = course_service.get_lesson(next_lesson_id)
    if not lesson:
        await callback.answer("❌ Следующий урок не найден", show_alert=True)
        return
    
    # ЭКРАНИРУЕМ все поля перед вставкой в HTML!
    text = f"📚 <b>{escape(lesson['title'])}</b>\n"
    text += f"<i>Модуль: {escape(lesson['module_title'])}</i>\n\n"
    text += escape(lesson["content"])
    text += f"\n\n💡 <b>Главное за 1 минуту:</b>\n{escape(lesson['summary'])}"
    
    # Кнопки
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Понял, дальше", callback_data=f"next_lesson_{next_lesson_id}"),
            InlineKeyboardButton(text="❓ Не понял", callback_data=f"ask_ai_{next_lesson_id}"),
        ],
        [
            InlineKeyboardButton(text="📝 Хочу практику", callback_data=f"practice_{next_lesson_id}"),
        ],
    ])
    
    if lesson.get("video_resources"):
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="🎥 Видео по теме", callback_data=f"videos_{next_lesson_id}")
        ])
    keyboard.inline_keyboard.extend(_back_to_module_row(lesson))

    from aiogram.exceptions import TelegramBadRequest
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("ask_ai_"))
async def ask_ai_explanation(callback: types.CallbackQuery, state: FSMContext):
    """ [EF-002] Передаёт реальную тему урока в LLM, а не сырой ID"""
    lesson_id = callback.data.removeprefix("ask_ai_")
    
    # Получаем понятную тему для ИИ
    topic = course_service.get_lesson_topic(lesson_id)
    
    await state.set_state(LessonState.asking_ai)
    await state.update_data(lesson_id=lesson_id, topic=topic)
    
    await callback.message.answer(
        f"🤔 <b>Тема:</b> {topic}\n\nНапиши, что именно непонятно:",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(LessonState.asking_ai)
async def handle_ai_question(message: types.Message, state: FSMContext):
    if message.text and message.text.startswith("/"):
        cmd = message.text.split()[0].lower()
        if cmd == "/start":
            await state.clear()
            from handlers.start import start
            await start(message)
            return
        if cmd == "/profile":
            await state.clear()
            from handlers.profile import profile_handler
            await profile_handler(message)
            return
        if cmd == "/help":
            from handlers.start import HELP_TEXT
            await message.answer(HELP_TEXT, parse_mode="Markdown")
            return
        if cmd == "/quiz":
            await state.clear()
            await quiz_handler(message, state)
            return
        if cmd == "/stats":
            await state.clear()
            from handlers.profile import stats_handler
            await stats_handler(message)
            return
        if cmd == "/continue":
            await state.clear()
            from handlers.start import continue_learning
            await continue_learning(message)
            return
        await message.answer("⚠️ Сначала задай вопрос по теме или нажми ↩ Назад к уроку.")
        return

    data = await state.get_data()
    lesson_id = data.get("lesson_id", "")
    topic = data.get("topic", lesson_id)

    explanation = await llm_service.explain_topic(
        topic=topic,
        user_question=message.text
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩ Назад к уроку", callback_data=f"lesson_{lesson_id}")]
    ])
    
    await message.answer(
        f"🤖 <b>Объяснение:</b>\n\n{explanation}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await state.clear()


async def _run_practice_session(message: types.Message, lesson_id: str, state: FSMContext) -> bool:
    """Запускает сессию практики по lesson_id. Возвращает False если задачи не загрузились."""
    topic = course_service.get_lesson_topic(lesson_id)
    await state.clear()
    tasks = await llm_service.generate_tasks(topic=topic, count=5)

    if not tasks:
        await message.answer("⚠️ Не удалось загрузить задачи. Попробуй позже.")
        return False

    await state.update_data(
        tasks=tasks,
        current_task=0,
        correct_count=0,
        wrong_attempts=0,
        failed_tasks=[],
        lesson_id=lesson_id,
        topic=topic
    )
    await send_task(message, tasks[0], state, task_num=1, total=len(tasks))
    return True


@router.callback_query(F.data.startswith("practice_"))
async def start_practice(callback: types.CallbackQuery, state: FSMContext):
    """Генерирует задачи по реальной теме урока"""
    lesson_id = callback.data.removeprefix("practice_")
    await _run_practice_session(callback.message, lesson_id, state)
    await callback.answer()


@router.message(Command("quiz"))
async def quiz_handler(message: types.Message, state: FSMContext, user_id: int | None = None):
    """Быстрая практика по случайной теме текущего курса"""
    async with async_session() as session:
        uid = user_id or message.from_user.id
        uname = None if user_id else message.from_user.username
        user = await get_or_create_user(session, uid, uname)
        selected_course = user.selected_course

    if not selected_course:
        await message.answer("📚 Сначала выбери курс в /start")
        return

    # Ищем курс по title → course_id
    all_courses = course_service.get_all_courses()
    course = next((c for c in all_courses if c.get("title") == selected_course), None)
    if not course:
        await message.answer("⚠️ Курс не найден. Попробуй /start")
        return

    lesson_ids = course_service.get_all_lesson_ids_for_course(course["course_id"])
    if not lesson_ids:
        await message.answer("⚠️ В курсе нет уроков.")
        return

    lesson_id = random.choice(lesson_ids)
    topic = course_service.get_lesson_topic(lesson_id)
    await message.answer(f"🎲 Случайная тема: <b>{topic}</b>", parse_mode="HTML")
    await _run_practice_session(message, lesson_id, state)


@router.callback_query(F.data == "quiz_inline")
async def quiz_inline_handler(callback: types.CallbackQuery, state: FSMContext):
    await quiz_handler(callback.message, state, user_id=callback.from_user.id)
    await callback.answer()


async def send_task(message: types.Message, task: dict, state: FSMContext, task_num: int = 1, total: int = 5) -> None:
    """Вспомогательная функция: отправляет задачу пользователю"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💡 Подсказка", callback_data=f"hint_{task_num - 1}")]
    ])
    sent = await message.answer(
        f"📝 <b>Задача {task_num} из {total}</b>\n\n{task['question']}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await state.set_state(LessonState.answering)
    await state.update_data(current_solution_task=task, hint_used=False, task_message_id=sent.message_id)


@router.callback_query(F.data.startswith("hint_"))
async def give_hint(callback: types.CallbackQuery, state: FSMContext):
    """Выдаёт подсказку к текущей задаче (1 раз)"""
    data = await state.get_data()

    if data.get("hint_used"):
        await callback.answer("Подсказка уже использована", show_alert=True)
        return

    task = data.get("current_solution_task")
    if task is None:
        await callback.answer("Задача не найдена", show_alert=True)
        return

    hint_text = await llm_service.get_hint(task)
    await state.update_data(hint_used=True)

    # Убираем кнопку подсказки из сообщения с задачей
    task_message_id = data.get("task_message_id")
    if task_message_id:
        from aiogram.exceptions import TelegramBadRequest
        try:
            await callback.bot.edit_message_reply_markup(
                chat_id=callback.message.chat.id,
                message_id=task_message_id,
                reply_markup=None
            )
        except TelegramBadRequest:
            pass

    await callback.message.answer(f"💡 <b>Подсказка:</b>\n\n{hint_text}", parse_mode="HTML")
    await callback.answer()


@router.message(LessonState.answering)
async def check_answer(message: types.Message, state: FSMContext):
    """Проверяет ответ пользователя и даёт обратную связь"""
    # ПРОВЕРКА НА КОМАНДЫ — ДОБАВЬ ЭТО В НАЧАЛО
    if message.text.startswith("/"):
        if message.text == "/start":
            await state.clear()  # Сбрасываем состояние!
            # Перенаправляем на start
            from handlers.start import start
            await start(message)
            return
        elif message.text == "/profile":
            await state.clear()  # Сбрасываем состояние!
            # Перенаправляем на profile
            from handlers.profile import profile_handler
            await profile_handler(message)
            return
        elif message.text == "/help":
            from handlers.start import HELP_TEXT
            await message.answer(HELP_TEXT)
            return
        else:
            await message.answer(
                "⚠️ В режиме решения задач доступны только:\n"
                "• Ответы на задачу\n"
                "• /start — выйти в меню\n"
                "• /profile — посмотреть прогресс\n"
                "• /help — справка"
            )
            return
    # КОНЕЦ ПРОВЕРКИ

    data = await state.get_data()
    task = data.get("current_solution_task")
    
    if task is None:
        await message.answer("⚠️ Сессия истекла. Начни урок заново.")
        await state.clear()
        return
    
    result = await llm_service.check_solution(task, message.text)
    is_correct = result["is_correct"]

    # Сохраняем запись в TaskHistory при каждой проверке
    async with async_session() as session:
        user = await get_or_create_user(session, message.from_user.id, message.from_user.username)
        session.add(TaskHistory(
            user_id=user.id,
            lesson_id=data.get("lesson_id"),
            question=task.get("question", ""),
            user_answer=message.text,
            is_correct=is_correct,
            score=10 if is_correct else 0,
        ))
        await session.commit()

        today = date.today()
        start_of_day = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)

        tasks_today = await session.scalar(
            select(func.count()).select_from(TaskHistory).where(
                TaskHistory.user_id == user.id,
                TaskHistory.created_at >= start_of_day
            )
        )
        lessons_today = await session.scalar(
            select(func.count()).select_from(UserProgress).where(
                UserProgress.user_id == user.id,
                UserProgress.status == "completed",
                UserProgress.completed_at >= start_of_day
            )
        )
        lessons_today = lessons_today or 0

        if (
            tasks_today == 5
            and lessons_today >= 1
            and (
                user.last_daily_reward_date is None
                or user.last_daily_reward_date.date() != today
            )
        ):
            await message.answer("🎉 Дневная норма выполнена! Серия продолжается. +1 день 🔥")
            user.last_daily_reward_date = datetime.now(timezone.utc)
            await session.commit()

    tasks = data.get("tasks", [])
    current_task_index = data.get("current_task", 0)
    correct_count = data.get("correct_count", 0)
    total = len(tasks)

    if is_correct:
        correct_count += 1
        await state.update_data(correct_count=correct_count)
        await message.answer(f"✅ {result['feedback']}")

        # Обновляем стрик и проверяем достижения
        async with async_session() as session:
            await get_or_create_user(session, message.from_user.id, message.from_user.username)
            user_full = await get_user_profile(session, message.from_user.id)
            await update_streak(session, user_full, bot=message.bot)
            new_achievements = await check_and_award(session, user_full)

        for ach_id in new_achievements:
            await message.answer(_achievement_text(ach_id), parse_mode="HTML")

        next_task_index = current_task_index + 1
        if next_task_index < total:
            await state.update_data(current_task=next_task_index)
            await send_task(message, tasks[next_task_index], state, task_num=next_task_index + 1, total=total)
        else:
            lesson_id = data.get("lesson_id", "")
            failed_tasks = data.get("failed_tasks", [])
            text, keyboard = _build_final_screen(correct_count, total, lesson_id, bool(failed_tasks))
            await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
            await state.set_state(LessonState.reviewing)
    else:
        wrong_attempts = data.get("wrong_attempts", 0) + 1
        await state.update_data(wrong_attempts=wrong_attempts)

        if wrong_attempts >= 3:
            await state.update_data(wrong_attempts=0)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏭ Пропустить задачу", callback_data="next_task")]
            ])
            await message.answer(
                f"❌ {result['feedback']}\n\n"
                f"📖 Правильный ответ: <b>{task.get('answer', '—')}</b>\n"
                f"💡 {task.get('hint', '')}",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        else:
            await message.answer(
                f"❌ {result['feedback']}\n\n"
                f"💡 Подсказка: {task.get('hint', 'Попробуй ещё раз')}\n"
                f"Попробуй ещё раз: ({wrong_attempts}/3)"
            )


@router.callback_query(F.data == "next_task")
async def skip_to_next_task(callback: types.CallbackQuery, state: FSMContext):
    """Переход к следующей задаче после раскрытия ответа (задача засчитана как неверная)"""
    data = await state.get_data()
    tasks = data.get("tasks", [])
    current_task_index = data.get("current_task", 0)
    correct_count = data.get("correct_count", 0)
    total = len(tasks)

    # Фиксируем пропущенную задачу как ошибку
    failed_tasks: list = data.get("failed_tasks", [])
    current_task = data.get("current_solution_task")
    if current_task:
        failed_tasks = failed_tasks + [current_task]
        await state.update_data(failed_tasks=failed_tasks)

    next_task_index = current_task_index + 1
    if next_task_index < total:
        await state.update_data(current_task=next_task_index, wrong_attempts=0)
        await send_task(callback.message, tasks[next_task_index], state, task_num=next_task_index + 1, total=total)
    else:
        lesson_id = data.get("lesson_id", "")
        text, keyboard = _build_final_screen(correct_count, total, lesson_id, bool(failed_tasks))
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(LessonState.reviewing)
    await callback.answer()


@router.callback_query(F.data == "show_errors")
async def show_errors(callback: types.CallbackQuery, state: FSMContext):
    """Показывает разбор ошибок сессии"""
    data = await state.get_data()
    failed_tasks: list = data.get("failed_tasks", [])

    if not failed_tasks:
        await callback.answer("Ошибок нет!", show_alert=True)
        return

    lines = ["📋 <b>Разбор ошибок:</b>\n"]
    for i, task in enumerate(failed_tasks, 1):
        lines.append(f"{i}. {task.get('question', '—')}")
        lines.append(f"   ✅ Правильный ответ: <b>{task.get('answer', '—')}</b>\n")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩ Назад к итогам", callback_data="back_to_summary")]
    ])
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_summary")
async def back_to_summary(callback: types.CallbackQuery, state: FSMContext):
    """Возвращает к итоговому экрану сессии"""
    data = await state.get_data()
    correct_count = data.get("correct_count", 0)
    tasks = data.get("tasks", [])
    lesson_id = data.get("lesson_id", "")
    failed_tasks = data.get("failed_tasks", [])
    total = len(tasks)

    text, keyboard = _build_final_screen(correct_count, total, lesson_id, bool(failed_tasks))
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()


_REVIEWING_HINT = (
    "📋 Сессия завершена. Выбери действие на экране выше\n"
    "или напиши /start, /continue, /quiz"
)


@router.message(LessonState.reviewing)
async def reviewing_fallback(message: types.Message, state: FSMContext):
    """Любой текст/команда в состоянии reviewing."""
    if message.text and message.text.startswith("/"):
        cmd = message.text.split()[0].lower()
        if cmd == "/start":
            await state.clear()
            from handlers.start import start
            await start(message)
            return
        if cmd == "/profile":
            await state.clear()
            from handlers.profile import profile_handler
            await profile_handler(message)
            return
        if cmd == "/help":
            from handlers.start import HELP_TEXT
            await message.answer(HELP_TEXT, parse_mode="Markdown")
            return
        if cmd == "/quiz":
            await state.clear()
            await quiz_handler(message, state)
            return
        if cmd == "/stats":
            await state.clear()
            from handlers.profile import stats_handler
            await stats_handler(message)
            return
        if cmd == "/continue":
            await state.clear()
            from handlers.start import continue_learning
            await continue_learning(message)
            return
    await message.answer(_REVIEWING_HINT)


@router.message(Command("cancel"))
@router.message(F.text == "❌ Отменить")
async def cancel_handler(message: types.Message, state: FSMContext):
    """Сбросить текущее состояние"""
    await state.clear()
    await message.answer(
        "✅ Режим задачи сброшен.\n\n"
        "Выбери действие:\n"
        "/start — главное меню\n"
        "/profile — мой прогресс"
    )




@router.callback_query(F.data.startswith("videos_"))
async def show_videos(callback: types.CallbackQuery):
    """🔥 Показывает список видео по теме урока"""
    lesson_id = callback.data.removeprefix("videos_")
    lesson = course_service.get_lesson(lesson_id)
    
    if not lesson or not lesson.get("video_resources"):
        await callback.answer("🔜 Видео пока добавляются", show_alert=True)
        return
    
    videos = lesson["video_resources"]
    text = f"🎬 <b>Видео по теме:</b> {lesson['title']}\n\n"
    
    for i, video in enumerate(videos, 1):
        # Эмодзи платформы
        platform = video.get("platform", "")
        if "Bobr" in platform:
            emoji = "🟠"
            note = " ⚡ без VPN"
        elif "YouTube" in platform:
            emoji = "🔴"
            note = ""
        else:
            emoji = "📺"
            note = ""
        
        text += f"{i}. {emoji} <b>{video['title']}</b> ({video.get('duration', '?')}){note}\n"
        text += f"   Платформа: {platform}\n"
        text += f"   🔗 <a href=\"{video['url']}\">Смотреть</a>\n\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩ Назад к уроку", callback_data=f"lesson_{lesson_id}")]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_web_page_preview=True  # Не грузить превью ссылок
    )
    await callback.answer()