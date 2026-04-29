import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod

from groq import AsyncGroq

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
_LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "15"))


class LLMService(ABC):
    @abstractmethod
    async def explain_topic(self, topic: str, user_question: str) -> str:
        """Объяснить сложную тему простым языком"""
        pass

    @abstractmethod
    async def generate_tasks(self, topic: str, count: int = 3) -> list[dict]:
        """Сгенерировать задачи по теме. Возвращает список словарей:
        [{'question': '...', 'answer': '...', 'hint': '...'}]"""
        pass

    @abstractmethod
    async def check_solution(self, task: dict, user_answer: str) -> dict:
        """Проверить решение. Возвращает:
        {'is_correct': bool, 'feedback': '...', 'score': int}"""
        pass

    @abstractmethod
    async def get_hint(self, task: dict) -> str:
        """Дать подсказку к задаче, не раскрывая ответ"""
        pass


class GroqLLMService(LLMService):
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        self._client = AsyncGroq(api_key=api_key)
        self._model = model

    async def _request(self, system_prompt: str, user_prompt: str, temperature: float = 0.5) -> str:
        response = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=1000,
            ),
            timeout=_LLM_TIMEOUT,
        )
        return response.choices[0].message.content

    async def explain_topic(self, topic: str, user_question: str) -> str:
        system = (
            "Ты репетитор для подготовки к ЕГЭ и ОГЭ. "
            "Объясняй просто и коротко — не более 4 предложений. "
            "Приводи один конкретный пример. Отвечай на русском языке."
        )
        user = f"Тема: {topic}\nВопрос ученика: {user_question}"
        try:
            return await self._request(system, user, temperature=0.4)
        except asyncio.TimeoutError:
            logger.error("Groq explain_topic timeout")
            return "⏳ ИИ-репетитор не отвечает. Попробуй через минуту."
        except Exception as e:
            logger.error("Groq explain_topic error: %s", e)
            return "Не удалось получить объяснение. Попробуй позже."

    async def generate_tasks(self, topic: str, count: int = 3) -> list[dict]:
        system = (
            "Ты составитель задач для подготовки к ЕГЭ и ОГЭ. "
            "Отвечай строго в формате JSON-массива без пояснений и markdown. "
            'Формат: [{"question": "...", "answer": "...", "hint": "..."}] '
            "Задачи и ответы на русском языке."
        )
        user = f"Составь {count} задачи по теме: {topic}"
        try:
            raw = await self._request(system, user, temperature=0.7)
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            tasks = json.loads(raw)
            if isinstance(tasks, list) and tasks:
                return tasks
        except asyncio.TimeoutError:
            logger.error("Groq generate_tasks timeout")
        except Exception as e:
            logger.error("Groq generate_tasks error: %s", e)
        return []

    async def check_solution(self, task: dict, user_answer: str) -> dict:
        system = (
            "Ты проверяешь ответы учеников. "
            "Отвечай строго в формате JSON без пояснений и markdown. "
            'Формат: {"is_correct": true/false, "feedback": "короткий комментарий на русском"}'
        )
        user = (
            f"Задача: {task['question']}\n"
            f"Правильный ответ: {task['answer']}\n"
            f"Ответ ученика: {user_answer}"
        )
        try:
            raw = await self._request(system, user, temperature=0.1)
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            result = json.loads(raw)
            return {
                "is_correct": bool(result.get("is_correct", False)),
                "feedback": result.get("feedback", ""),
                "score": 10 if result.get("is_correct") else 0,
            }
        except asyncio.TimeoutError:
            logger.error("Groq check_solution timeout")
            return {"is_correct": False, "feedback": "Не удалось проверить — попробуй ещё раз.", "score": 0}
        except Exception as e:
            logger.error("Groq check_solution error: %s", e)
            return {"is_correct": False, "feedback": "Не удалось проверить ответ. Попробуй позже.", "score": 0}

    async def get_hint(self, task: dict) -> str:
        system = (
            "Ты репетитор для подготовки к ЕГЭ и ОГЭ. "
            "Дай подсказку к задаче, НЕ раскрывая ответ. "
            "Направь ход мысли: укажи метод или шаг, с которого стоит начать. "
            "Не более 3 предложений. Отвечай на русском языке."
        )
        user = f"Задача: {task['question']}"
        try:
            return await self._request(system, user, temperature=0.4)
        except asyncio.TimeoutError:
            logger.error("Groq get_hint timeout")
            return "⏳ ИИ-репетитор не отвечает. Попробуй через минуту."
        except Exception as e:
            logger.error("Groq get_hint error: %s", e)
            return "Не удалось получить подсказку. Попробуй позже."


class MockLLMService(LLMService):
    async def explain_topic(self, topic: str, user_question: str) -> str:
        question = user_question.lower()
        if "дискриминант" in question or "d =" in question:
            return "Дискриминант — это как сердце квадратного уравнения. Формула: D = b² - 4ac. Если он положительный — у уравнения два корня, если ноль — один, если отрицательный — корней нет (в реальных числах)."
        if "корень" in question:
            return "Корень — это значение X, которое превращает уравнение в верное равенство (0=0)."
        return "Я пока учусь, но кажется, тут нужно применить формулу из теории. Попробуй спросить про дискриминант или коэффициенты."

    async def generate_tasks(self, topic: str, count: int = 3) -> list[dict]:
        return [{"question": f"Задача по {topic} №{i+1}", "answer": "42", "hint": "Подумай"} for i in range(count)]

    async def check_solution(self, task: dict, user_answer: str) -> dict:
        is_correct = user_answer.strip() == task["answer"]
        return {
            "is_correct": is_correct,
            "feedback": "Верно!" if is_correct else "Попробуй ещё раз.",
            "score": 10 if is_correct else 0,
        }

    async def get_hint(self, task: dict) -> str:
        return task.get("hint", "Попробуй вспомнить формулу из теории и подставить данные из условия.")


if GROQ_API_KEY:
    llm_service: LLMService = GroqLLMService(GROQ_API_KEY)
    logger.info("LLM: Groq подключён")
else:
    llm_service: LLMService = MockLLMService()
    logger.warning("LLM: GROQ_API_KEY не задан — используется заглушка")
