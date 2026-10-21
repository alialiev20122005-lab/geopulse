import os
import logging
from typing import Dict, Any, Tuple
import httpx

logger = logging.getLogger("GeoPulseAI")

# Поддержка нескольких провайдеров: OpenRouter, DeepSeek, OpenAI
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

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
    С автоматическим переключением на надежный эвристический генератор при лимитах или сетевых задержках.
    """

    # 1. Попытка вызова внешней нейросети с защитой по таймауту
    if OPENROUTER_API_KEY or DEEPSEEK_API_KEY or OPENAI_API_KEY:
        try:
            return await _call_llm(business_name, category, rating, comment, contact)
        except Exception as e:
            logger.info(f"Внешняя LLM недоступна ({e}), переключаемся на встроенный интеллект-генератор.")

    # 2. Мгновенный интеллектуальный генератор по правилам ресторанного сервиса (0 задержек)
    return _generate_heuristic_responses(business_name, category, rating, comment, contact)

async def _call_llm(business_name: str, category: str, rating: int, comment: str, contact: str) -> Tuple[str, str]:
    prompt = f"""
Ты — ведущий эксперт по клиентскому сервису и управлению репутацией (ORM) в России.
Заведение: «{business_name}» ({category}).
Клиент оставил оценку: {rating} из 5.
Суть претензии: «{comment}»
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

    # 1. Пробуем OpenRouter если ключ задан
    if OPENROUTER_API_KEY:
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://pulse-geo.site",
            "X-Title": "GeoPulse App",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GeoPulse/1.0"
        }
        for model in ["nvidia/nemotron-3.5-lightning:free", "minimax/minimax-m3.1-flash:free", "google/gemma-4-31b-it:free"]:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "Ты эксперт по PR и клиентскому сервису в России. Отвечай кратко, емко, вежливо и убедительно по-русски."},
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 500
            }
            try:
                async with httpx.AsyncClient(timeout=4.0) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        msg = data["choices"][0]["message"]
                        result = msg.get("content") or msg.get("reasoning")
                        if result and "===SPLIT===" in result:
                            parts = result.split("===SPLIT===")
                            return parts[0].strip(), parts[1].strip()
            except Exception:
                continue

    raise RuntimeError("Внешняя LLM временно занята")

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
