# bot/services/course_service.py
import json
from pathlib import Path
from typing import Optional, List
import logging

logger = logging.getLogger(__name__)

class CourseService:
    def __init__(self, json_path: Optional[Path] = None):
        if json_path is None:
            json_path = Path(__file__).parent.parent / "data" / "courses.json"
        
        self._json_path = json_path
        self._data = self._load_courses()
        self._validate_courses()  # Валидация при инициализации
        self._lesson_index = self._build_lesson_index()
    
    def _load_courses(self) -> dict:
        """Загружает JSON с курсами"""
        try:
            with open(self._json_path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            logger.error(f"Файл курсов не найден: {self._json_path}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка JSON в {self._json_path}: {e}")
            raise
    
    def _validate_courses(self) -> None:
        """Проверяет структуру courses.json"""
        errors = []
        lesson_ids = set()
        
        # Определяем список курсов
        courses = self._data.get("courses", [self._data]) if "courses" in self._data else [self._data]
        
        for course_idx, course in enumerate(courses):
            course_path = f"courses[{course_idx}]"
            
            # Проверка обязательных полей курса
            if "course_id" not in course:
                errors.append(f"{course_path}: отсутствует 'course_id'")
            if "title" not in course:
                errors.append(f"{course_path}: отсутствует 'title'")
            
            modules = course.get("modules", [])
            if not modules:
                errors.append(f"{course_path}: пустой список 'modules'")
            
            for mod_idx, module in enumerate(modules):
                mod_path = f"{course_path}.modules[{mod_idx}]"
                
                if "module_id" not in module:
                    errors.append(f"{mod_path}: отсутствует 'module_id'")
                if "title" not in module:
                    errors.append(f"{mod_path}: отсутствует 'title'")
                
                lessons = module.get("lessons", [])
                if not lessons:
                    errors.append(f"{mod_path}: пустой список 'lessons'")
                
                for lesson_idx, lesson in enumerate(lessons):
                    lesson_path = f"{mod_path}.lessons[{lesson_idx}]"
                    
                    # Проверка обязательных полей урока
                    for field in ["lesson_id", "title", "content", "summary"]:
                        if field not in lesson:
                            errors.append(f"{lesson_path}: отсутствует '{field}'")
                    
                    # Проверка уникальности lesson_id
                    lesson_id = lesson.get("lesson_id")
                    if lesson_id:
                        if lesson_id in lesson_ids:
                            errors.append(f"{lesson_path}: дубликат lesson_id '{lesson_id}'")
                        lesson_ids.add(lesson_id)
                    
                    # Проверка длины content
                    content = lesson.get("content", "")
                    if len(content) < 50:
                        errors.append(f"{lesson_path}: content слишком короткий ({len(content)} симв.)")
        
        if errors:
            logger.error("🚨 Ошибки валидации courses.json:\n%s", "\n".join(errors))
            raise ValueError(f"Невалидный courses.json: {len(errors)} ошибок")
        
        logger.info("✅ courses.json успешно пройден валидацию (%d уроков)", len(lesson_ids))
    
    def _build_lesson_index(self) -> dict:
        """Строит индекс уроков для быстрого поиска"""
        index = {}
        courses = self._data.get("courses", [self._data]) if "courses" in self._data else [self._data]
        
        for course in courses:
            course_id = course.get("course_id", "unknown")
            for module in course.get("modules", []):
                module_id = module.get("module_id", "unknown")
                module_title = module.get("title", "Без названия")
                
                for lesson in module.get("lessons", []):
                    lesson_id = lesson.get("lesson_id")
                    if lesson_id:
                        index[lesson_id] = {
                            "course_id": course_id,
                            "module_id": module_id,
                            "module_title": module_title,
                            **lesson
                        }
        return index
    
    def get_all_courses(self) -> List[dict]:
        """Возвращает список всех курсов (для главного меню)"""
        courses = self._load_courses()
        # Если в JSON один курс — возвращаем его в списке
        if "course_id" in courses:
            return [courses]
        
        # Если несколько курсов (новый формат)
        return courses.get("courses", [courses])
    
    def get_course_by_id(self, course_id: str) -> Optional[dict]:
        """Находит курс по ID"""
        courses = self._load_courses()
        if courses.get("course_id") == course_id:
            return courses
        for course in courses.get("courses", []):
            if course.get("course_id") == course_id:
                return course
        return None
    
    def get_module(self, course_id: str, module_id: str) -> Optional[dict]:
        """Находит модуль по ID внутри курса"""
        course = self.get_course_by_id(course_id)
        if not course:
            return None
        for module in course.get("modules", []):
            if module["module_id"] == module_id:
                return module
        return None
    
    def get_lesson(self, lesson_id: str) -> Optional[dict]:
        """Находит урок по ID в любом модуле курса"""
        courses = self._load_courses()
        # Если один курс в JSON
        if "course_id" in courses:
            for module in courses.get("modules", []):
                for lesson in module.get("lessons", []):
                    if lesson["lesson_id"] == lesson_id:
                        return {
                            **lesson,
                            "module_id": module["module_id"],
                            "module_title": module["title"],
                            "course_id": courses["course_id"],
                        }
        # Если несколько курсов
        for course in courses.get("courses", []):
            for module in course.get("modules", []):
                for lesson in module.get("lessons", []):
                    if lesson["lesson_id"] == lesson_id:
                        return {
                            **lesson,
                            "module_id": module["module_id"],
                            "module_title": module["title"],
                            "course_id": course["course_id"],
                        }
        return None
    
    def get_lesson_topic(self, lesson_id: str) -> str:
        """Возвращает понятную тему для ИИ (не сырой ID)"""
        lesson = self.get_lesson(lesson_id)
        if lesson:
            return f"{lesson['module_title']}: {lesson['title']}"
        return lesson_id
    
    def get_all_lesson_ids_for_course(self, course_id: str) -> List[str]:
        """Возвращает все lesson_id курса по порядку"""
        course = self.get_course_by_id(course_id)
        if not course:
            return []
        return [
            lesson["lesson_id"]
            for module in course.get("modules", [])
            for lesson in module.get("lessons", [])
        ]

    def get_next_lesson_id(self, current_lesson_id: str) -> Optional[str]:
        """Находит ID следующего урока или None, если курс завершён"""
        courses = self._load_courses()
        found_module = False
        
        # Если один курс в JSON
        if "course_id" in courses:
            for module in courses.get("modules", []):
                lessons = module.get("lessons", [])
                for i, lesson in enumerate(lessons):
                    if lesson["lesson_id"] == current_lesson_id:
                        if i + 1 < len(lessons):
                            return lessons[i + 1]["lesson_id"]
                        found_module = True
                        break
                if found_module:
                    module_idx = courses["modules"].index(module)
                    if module_idx + 1 < len(courses["modules"]):
                        next_module = courses["modules"][module_idx + 1]
                        if next_module.get("lessons"):
                            return next_module["lessons"][0]["lesson_id"]
                    return None
        
        # Если несколько курсов
        for course in courses.get("courses", []):
            for module in course.get("modules", []):
                lessons = module.get("lessons", [])
                for i, lesson in enumerate(lessons):
                    if lesson["lesson_id"] == current_lesson_id:
                        if i + 1 < len(lessons):
                            return lessons[i + 1]["lesson_id"]
                        found_module = True
                        break
                if found_module:
                    module_idx = course["modules"].index(module)
                    if module_idx + 1 < len(course["modules"]):
                        next_module = course["modules"][module_idx + 1]
                        if next_module.get("lessons"):
                            return next_module["lessons"][0]["lesson_id"]
                    return None
        
        return None