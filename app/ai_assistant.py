import os
import re
import logging
from typing import Dict, Any, Tuple, Optional
import httpx

logger = logging.getLogger("GeoPulseAI")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

def sanitize_llm_response(text: str) -> Optional[Tuple[str, str]]:
    """
    Строго очищает ответ нейросети от размышлений (<think>, 'Here is thinking process')
    и валидирует качество русского текста.
    """
    if not text:
        return None

    # 1. Удаляем блоки размышлений <think>...</think>
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)

    # 2. Удаляем англоязычные вступления с рассуждениями
    if "thinking process" in text.lower() or "analyze user request" in text.lower():
        # Если разделитель уже встретился после рассуждений
        if "===SPLIT===" in text:
            parts = text.split("===SPLIT===")
            cleaned_p1 = re.sub(r'^(Here is|Let\'s think|Thinking process|1\.|\bAnalyze\b).*?\n\n', '', parts[0], flags=re.DOTALL | re.IGNORECASE).strip()
            text = f"{cleaned_p1}\n===SPLIT===\n{parts[1].strip()}"
        else:
            return None

    if "===SPLIT===" not in text:
        return None

    parts = text.split("===SPLIT===")
    if len(parts) < 2:
        return None

    personal = parts[0].strip()
    public = parts[1].strip()

    # Очищаем заголовки вроде "1. ЛИЧНОЕ СООБЩЕНИЕ:" если модель их повторила
    personal = re.sub(r'^(1\.\s*)?ЛИЧНОЕ СООБЩЕНИЕ.*?:?\s*', '', personal, flags=re.IGNORECASE).strip()
    public = re.sub(r'^(2\.\s*)?ПУБЛИЧНЫЙ ОТВЕТ.*?:?\s*', '', public, flags=re.IGNORECASE).strip()

    # 3. Черный список маркеров утечки CoT (Chain of Thought)
    forbidden_markers = [
        "thinking process", "analyze user request", "task:", "goal:", "text 1:", "text 2:",
        "russian nuances:", "here's", "role: expert", "greeting ->", "structure:",
        "deconstruct the", "key elements:"
    ]
    lower_check = (personal + " " + public).lower()
    for marker in forbidden_markers:
        if marker in lower_check:
            logger.warning(f"Отказ LLM: обнаружен артефакт рассуждений '{marker}'")
            return None

    # 4. Проверка достаточной плотности русского текста (не менее 30 кириллических символов в каждом)
    cyrillic_p1 = len(re.findall(r'[а-яА-ЯёЁ]', personal))
    cyrillic_p2 = len(re.findall(r'[а-яА-ЯёЁ]', public))

    if cyrillic_p1 < 30 or cyrillic_p2 < 30:
        logger.warning(f"Отказ LLM: недостаточно кириллицы (p1={cyrillic_p1}, p2={cyrillic_p2})")
        return None

    return personal, public

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
    # 1. Попытка вызова внешней нейросети с валидацией чистоты текста
    if OPENROUTER_API_KEY or DEEPSEEK_API_KEY or OPENAI_API_KEY:
        try:
            result = await _call_llm(business_name, category, rating, comment, contact)
            if result:
                return result
        except Exception as e:
            logger.info(f"Внешняя LLM недоступна ({e}), переключаемся на встроенный интеллект-генератор.")

    # 2. Мгновенный интеллектуальный генератор по правилам ресторанного сервиса (0 задержек, 100% русский текст)
    return _generate_heuristic_responses(business_name, category, rating, comment, contact)

async def _call_llm(business_name: str, category: str, rating: int, comment: str, contact: str) -> Optional[Tuple[str, str]]:
    prompt = f"""Заведение: «{business_name}» ({category}).
Оценка гостя: {rating} из 5.
Претензия: «{comment}»
Контакт гостя: {contact or 'не указан'}

Напиши строго ДВА готовых текста на русском языке:

1. ЛИЧНОЕ СООБЩЕНИЕ (для отправки гостю в WhatsApp):
Искреннее извинение от лица управляющего, признание недочета и предложение конкретной приятной компенсации (десерт, кофе, скидка).

2. ПУБЛИЧНЫЙ ОТВЕТ (для Яндекс.Карт):
Сдержанный, вежливый и дипломатичный ответ для будущих гостей без споров и оправданий.

РАЗДЕЛИ ДВА ТЕКСТА СТРОКОЙ: ===SPLIT===
ВАЖНО: Пиши ТОЛЬКО готовые тексты ответов на русском языке без английских слов, рассуждений и пометок."""

    system_prompt = (
        "Ты — профессиональный директор ресторана и эксперт по клиентскому сервису в России.\n"
        "ПРАВИЛА:\n"
        "1. Пиши СТРОГО на русском языке.\n"
        "2. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать любые размышления, заметки ('thinking process', 'analysis') или английский текст.\n"
        "3. Выведи ТОЛЬКО два готовых текста, разделенных строкой ===SPLIT===."
    )

    # Надежные модели без утечки рассуждений (Instruction-tuned)
    reliable_models = [
        "meta-llama/llama-3.3-70b-instruct:free",
        "meta-llama/llama-3.1-8b-instruct:free",
        "mistralai/mistral-7b-instruct:free",
        "google/gemma-2-9b-it:free",
        "qwen/qwen-2.5-72b-instruct:free"
    ]

    if OPENROUTER_API_KEY:
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://pulse-geo.site",
            "X-Title": "GeoPulse App"
        }
        for model in reliable_models:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.5,
                "max_tokens": 700
            }
            try:
                async with httpx.AsyncClient(timeout=4.5) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        msg = data["choices"][0]["message"]
                        # Берем ТОЛЬКО чистый content, никогда reasoning!
                        content = msg.get("content")
                        if content:
                            cleaned = sanitize_llm_response(content)
                            if cleaned:
                                return cleaned
            except Exception:
                continue

    return None

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
    is_cold_or_food = any(w in comment_lower for w in ["еда", "блюдо", "холодн", "остыл", "невкусн", "мясо", "кофе", "суп", "сыр"])
    is_clean = any(w in comment_lower for w in ["грязн", "стол", "пыл", "посуд", "салфетк", "уборк"])
    is_bill = any(w in comment_lower for w in ["чек", "цен", "счет", "ошибк", "дорого"])

    # 1. Личное сообщение (без плейсхолдеров [Имя], готово к отправке)
    solutions = []
    if is_waiting:
        solutions.append("Мы уже провели беседу со сменой по поводу скорости отдачи заказов и стандартов обслуживания.")
    if is_cold_or_food:
        solutions.append("Температура подачи и вкус блюд — наш главный приоритет. Замечание уже передано шеф-повару на личный контроль.")
    if is_staff:
        solutions.append("Мы очень строго относимся к гостеприимству персонала и уже разбираем этот инцидент с сотрудником.")
    if is_clean:
        solutions.append("Мы уже усилили контроль чистоты и порядка в зале, чтобы подобные ситуации не повторялись.")
    if is_bill:
        solutions.append("Мы обязательно перепроверим расчет и стандарты формирования счета.")

    if not solutions:
        solutions.append("Мы зафиксировали каждое ваше замечание и уже передали информацию руководству.")

    solution_text = " ".join(solutions)

    bonus_text = "Хотим в качестве извинений угостить вас фирменным десертом и напитком при следующем визите, чтобы сгладить впечатление."

    contact_invitation = "Будем очень благодарны за возможность исправить ваше мнение о нас!"
    if contact:
        contact_invitation = f"Пожалуйста, напишите нам в ответном сообщении — мы с радостью решим вопрос и подготовим комплимент к вашему визиту."

    personal = (
        f"Здравствуйте! Я управляющий «{business_name}».\n"
        f"Увидел ваш отзыв по поводу «{comment[:80]}». Искренне приношу извинения за испорченное настроение!\n"
        f"{solution_text}\n"
        f"{bonus_text}\n"
        f"{contact_invitation}"
    )

    # 2. Публичный ответ для Яндекс.Карт
    public = (
        f"Здравствуйте! Спасибо за обратную связь. Нам искренне жаль, что ваш визит в «{business_name}» оставил "
        f"неприятный осадок. Мы очень внимательно относимся к замечаниям наших гостей и уже взяли описанную ситуацию на личный контроль "
        f"руководства. Стандарты сервиса и комфорт гостей — наш главный приоритет. "
        f"Будем рады возможности изменить ваше мнение о нас к лучшему!"
    )

    return personal, public
