# bot/services/course_service.py
import json
from pathlib import Path
from typing import Optional, List

class CourseService:
    def __init__(self, courses_path: str = None):
        if courses_path is None:
            courses_path = Path(__file__).parent.parent / "data" / "courses.json"
        self.courses_path = Path(courses_path)
        self._cache: Optional[dict] = None
    
    def _load_courses(self) -> dict:
        """Загружает курсы из JSON (с кэшированием)"""
        if self._cache is None:
            with open(self.courses_path, "r", encoding="utf-8") as f:
                self._cache = json.load(f)
        return self._cache
    
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