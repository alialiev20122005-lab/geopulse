import os
import logging
from typing import Dict, Any, Tuple
import httpx

logger = logging.getLogger("GeoPulseAI")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")

async def generate_review_responses(
    business_name: str,
    category: str,
    rating: int,
    comment: str,
    contact: str = ""
) -> Tuple[str, str]:
    """
    Генерирует два варианта ответа:
    1. Личное сообщение клиенту в WhatsApp / SMS / Telegram (для оперативного урегулирования)
    2. Публичный ответ для Яндекс.Карт / 2ГИС (для защиты репутации перед будущими клиентами)
    """

    # Если задан API-ключ нейросети, вызываем реальную LLM
    if OPENAI_API_KEY:
        try:
            return await _call_llm(business_name, category, rating, comment, contact)
        except Exception as e:
            logger.error(f"Ошибка вызова LLM: {e}, переключаемся на эвристический генератор.")

    # Интеллектуальный генератор по правилам сервиса (работает мгновенно без внешних ключей)
    return _generate_heuristic_responses(business_name, category, rating, comment, contact)

async def _call_llm(business_name: str, category: str, rating: int, comment: str, contact: str) -> Tuple[str, str]:
    prompt = f"""
Ты — топовый эксперт по клиентскому сервису и управлению репутацией (ORM) в России.
Заведение: «{business_name}» ({category}).
Клиент оставил оценку: {rating} из 5.
Суть жалобы: «{comment}»
Контакт: {contact}

Сгенерируй строго ДВА текста:

1. ЛИЧНОЕ СООБЩЕНИЕ (для отправки клиенту в WhatsApp/SMS):
- Тон: искренний, вежливый, заботливый, от лица управляющего.
- Задача: снять агрессию, признать недочет, предложить конкретное решение или компенсацию (десерт/скидку/переделку).

2. ПУБЛИЧНЫЙ ОТВЕТ (для Яндекс.Карт):
- Тон: дипломатичный, сдержанный, профессиональный.
- Задача: показать всем будущим клиентам на картах, что заведение адекватное, слышит гостей и держит качество под контролем. Не оправдываться, не спорить.

Формат ответа: раздели два текста строкой ===SPLIT===
    """

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": "Ты эксперт по PR и клиентскому сервису. Отвечай кратко, емко и убедительно."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{OPENAI_BASE_URL}/chat/completions", headers=headers, json=payload)
        if resp.status_code == 200:
            result = resp.json()["choices"][0]["message"]["content"]
            if "===SPLIT===" in result:
                parts = result.split("===SPLIT===")
                return parts[0].strip(), parts[1].strip()
            return result.strip(), result.strip()
        else:
            raise RuntimeError(f"LLM API Error: {resp.text}")

def _generate_heuristic_responses(
    business_name: str,
    category: str,
    rating: int,
    comment: str,
    contact: str
) -> Tuple[str, str]:
    """Эвристическая генерация профессиональных ответов по нишам и паттернам жалобы."""
    comment_lower = comment.lower()

    # Анализ контекста
    is_waiting = any(w in comment_lower for w in ["долго", "ждали", "ожидание", "минут", "час", "медленн"])
    is_staff = any(w in comment_lower for w in ["официант", "мастер", "персонал", "администратор", "груб", "хам"])
    is_quality = any(w in comment_lower for w in ["стриж", "волос", "еда", "блюдо", "холодн", "грязн", "ремонт", "детал"])

    # 1. Личное сообщение
    if is_waiting:
        solution = "Мы уже провели беседу со сменой по поводу скорости отдачи и стандартов обслуживания."
        bonus = "Хотим в качестве извинений угостить вас фирменным десертом / кофе при следующем визите."
    elif is_staff:
        solution = "Мы очень строго относимся к внимательности персонала и обязательно разберем этот случай с сотрудником."
        bonus = "Пожалуйста, дайте нам шанс исправить впечатление — будем рады предоставить вам персональную скидку 15%."
    elif is_quality:
        solution = "Мы дорожим качеством нашей работы и готовы полностью переделать работу или вернуть средства."
        bonus = "Нам очень важно детально разобраться в ситуации."
    else:
        solution = "Мы зафиксировали каждое ваше замечание и уже передали информацию руководству."
        bonus = "Будем благодарны за возможность загладить вину и исправить ситуацию."

    personal = (
        f"Здравствуйте! Меня зовут [Имя], я управляющий «{business_name}».\n"
        f"Увидел ваш отзыв по поводу «{comment[:60]}...». Искренне приношу извинения за испорченное настроение!\n"
        f"{solution}\n"
        f"{bonus}\n"
        f"Скажите, когда вам было бы удобно подойти или пообщаться подробнее?"
    )

    # 2. Публичный ответ для Яндекс.Карт
    public = (
        f"Здравствуйте! Спасибо за обратную связь. Нам искренне жаль, что ваш визит в «{business_name}» оставил "
        f"неприятный осадок. Мы очень внимательно относимся к замечаниям наших гостей и уже взяли ситуацию на личный контроль "
        f"руководства. Стандарты сервиса и комфорт клиентов — наш главный приоритет. "
        f"Будем рады возможности изменить ваше мнение о нас к лучшему!"
    )

    return personal, public
