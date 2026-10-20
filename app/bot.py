import asyncio
import hashlib
import html
import logging
from datetime import datetime
from typing import List, Optional
import httpx

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SESSION_SECRET
from app.database import (
    link_telegram_owner,
    get_business,
    get_business_chat_ids,
    get_business_stats,
    get_businesses_by_chat_id
)

logger = logging.getLogger("GeoPulseBot")

def get_manager_token(business_id: str) -> str:
    """Генерация контрольного токена доступа для директора заведения."""
    raw = f"manager:{business_id}:{SESSION_SECRET}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]

async def send_message(chat_id: str, text: str, reply_markup: Optional[dict] = None) -> bool:
    """Отправка сообщения в Telegram по chat_id с разметкой HTML."""
    if not TELEGRAM_BOT_TOKEN:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            else:
                logger.error(f"Telegram API error {resp.status_code} for user {chat_id}: {resp.text}")
                return False
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения пользователю {chat_id}: {e}")
        return False

async def broadcast_feedback(business_id: str, rating: int, comment: str, contact: str, table_number: str = ""):
    """Рассылка жалобы всем привязанным управляющим этого заведения с защитой от XSS/HTML Injection."""
    business = get_business(business_id)
    business_name = business["name"] if business else business_id

    chat_ids = get_business_chat_ids(business_id)
    # Если заведение еще не привязано конкретным директором, отправляем в общий админский чат
    if not chat_ids and TELEGRAM_CHAT_ID:
        chat_ids = [TELEGRAM_CHAT_ID]

    if not chat_ids:
        logger.warning(f"⚠️ Для заведения {business_name} нет привязанных Telegram аккаунтов!")
        return 0

    from app.ai_assistant import generate_review_responses

    category = business["category"] if business else "Сфера услуг"
    personal_reply, public_reply = await generate_review_responses(
        business_name=business_name,
        category=category,
        rating=rating,
        comment=comment,
        contact=contact
    )

    import re
    import urllib.parse

    # 1. Валидация рейтинга и экранирование спецсимволов для HTML Telegram
    safe_rating = max(1, min(5, int(rating)))
    stars_str = "⭐" * safe_rating
    safe_business_name = html.escape(str(business_name))
    safe_table_info = f"\n🪑 <b>Столик:</b> {html.escape(str(table_number))}" if table_number else ""
    safe_comment = html.escape(str(comment))
    safe_contact = html.escape(str(contact)) if contact else "Не указан"
    safe_personal = html.escape(str(personal_reply))
    safe_public = html.escape(str(public_reply))
    
    text = (
        f"🚨 <b>ВНИМАНИЕ: ПЕРЕХВАЧЕН НЕГАТИВ!</b>\n\n"
        f"🏢 <b>Заведение:</b> {safe_business_name}{safe_table_info}\n"
        f"📊 <b>Оценка клиента:</b> {stars_str} ({safe_rating} из 5)\n"
        f"💬 <b>Претензия:</b>\n<i>«{safe_comment}»</i>\n\n"
        f"📱 <b>Контакт:</b> {safe_contact}\n"
        f"🕒 <b>Время:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        f"────────────────────\n"
        f"🤖 <b>ИИ-ПОМОЩНИК ПОДГОТОВИЛ ОТВЕТЫ:</b>\n\n"
        f"💬 <b>1. Для отправки клиенту в WhatsApp / SMS:</b>\n"
        f"<code>{safe_personal}</code>\n\n"
        f"🗺️ <b>2. Для публичного ответа на Яндекс.Картах:</b>\n"
        f"<code>{safe_public}</code>\n\n"
        f"────────────────────\n"
        f"💡 <i>Нажмите на текст в рамке выше, чтобы скопировать в 1 клик!</i>"
    )

    # Формируем интерактивные кнопки для мгновенной связи
    reply_markup = None
    if contact:
        buttons = []
        # Извлекаем только цифры из телефона
        digits = re.sub(r"\D", "", contact)
        
        # Если это российский номер (89... -> 79...)
        if len(digits) == 11 and digits.startswith("8"):
            digits = "7" + digits[1:]
        elif len(digits) == 10:
            digits = "7" + digits

        if len(digits) >= 10:
            wa_encoded_text = urllib.parse.quote(personal_reply)
            buttons.append([
                {
                    "text": "💬 Написать клиенту в WhatsApp",
                    "url": f"https://wa.me/{digits}?text={wa_encoded_text}"
                }
            ])
        elif contact.strip().startswith("@") or "t.me/" in contact.lower():
            tg_username = contact.strip().replace("https://", "").replace("http://", "").replace("t.me/", "").lstrip("@").strip()
            buttons.append([
                {
                    "text": "✈️ Написать клиенту в Telegram",
                    "url": f"https://t.me/{tg_username}"
                }
            ])

        if buttons:
            reply_markup = {"inline_keyboard": buttons}

    success_count = 0
    for cid in chat_ids:
        if await send_message(cid, text, reply_markup=reply_markup):
            success_count += 1

    return success_count

async def telegram_polling_loop():
    """Фоновый цикл приема сообщений от Telegram с защитой от циклических сбоев."""
    if not TELEGRAM_BOT_TOKEN:
        logger.info("Telegram Bot Token не задан. Фоновый поллинг отключен.")
        return

    logger.info("🤖 Запущен фоновый обработчик Telegram-бота...")
    offset = 0
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"

    while True:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, params={"offset": offset, "timeout": 20})
                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        message = update.get("message")
                        if not message or "text" not in message:
                            continue

                        chat_id = str(message["chat"]["id"])
                        text = message["text"].strip()
                        user_name = message["from"].get("first_name", "Управляющий")

                        # 1. Обработка deep-link привязки: /start biz_<id>
                        if text.startswith("/start biz_"):
                            business_id = text.replace("/start biz_", "").strip().lower()
                            business = get_business(business_id)

                            if business:
                                link_telegram_owner(business_id, chat_id)
                                welcome_msg = (
                                    f"🎉 <b>Поздравляем, {html.escape(user_name)}!</b>\n\n"
                                    f"Вы успешно подключили уведомления для заведения:\n"
                                    f"🏢 <b>«{html.escape(business['name'])}»</b>\n"
                                    f"📍 {html.escape(business['address'])}\n\n"
                                    f"🔔 <i>Теперь каждое обращение недовольного клиента будет приходить лично вам за 1 секунду.</i>\n\n"
                                    f"Команды:\n"
                                    f"📊 /stats — статистика вашего заведения"
                                )
                                mgr_token = get_manager_token(business_id)
                                welcome_markup = {
                                    "inline_keyboard": [[
                                        {"text": "📊 Открыть личный дашборд", "url": f"https://geopulse-yo8k.onrender.com/dashboard/{business_id}?auth={mgr_token}"}
                                    ]]
                                }
                                await send_message(chat_id, welcome_msg, reply_markup=welcome_markup)
                            else:
                                await send_message(chat_id, "❌ Заведение с таким идентификатором не найдено.")

                        # 2. Обычный старт
                        elif text == "/start":
                            msg = (
                                f"👋 Привет, {html.escape(user_name)}!\n\n"
                                f"Я официальный бот платформы <b>GeoPulse</b>.\n"
                                f"Я защищаю рейтинги заведений на Яндекс.Картах и передаю жалобы клиентов напрямую руководству.\n\n"
                                f"Чтобы привязать свое заведение, добавьте его на сайте и нажмите кнопку «Подключить Telegram»."
                            )
                            await send_message(chat_id, msg)

                        # 3. Динамическая статистика привязанных заведений
                        elif text.startswith("/stats"):
                            my_businesses = get_businesses_by_chat_id(chat_id)
                            if not my_businesses:
                                await send_message(
                                    chat_id,
                                    "⚠️ <b>Вы еще не привязаны ни к одному заведению.</b>\n\n"
                                    "Для привязки используйте ссылку администратора заведения вида:\n"
                                    "<code>https://t.me/my_geopulse_bot?start=biz_ID</code>"
                                )
                            else:
                                for b in my_businesses[:5]:
                                    stats_data = get_business_stats(b["id"])
                                    s = stats_data["stats"]
                                    total = s["total_count"] or 0
                                    avg_r = round(s["avg_rating"] or 5.0, 1)
                                    neg = s["negative_count"] or 0
                                    pos = s["positive_count"] or 0
                                    
                                    stat_msg = (
                                        f"📊 <b>Статистика: {html.escape(b['name'])}</b>\n\n"
                                        f"👥 Всего отзывов: <b>{total}</b>\n"
                                        f"⭐ Средняя оценка: <b>{avg_r} / 5.0</b>\n"
                                        f"🚨 Перехвачено жалоб: <b>{neg}</b>\n"
                                        f"💚 Отправлено на Карты: <b>{pos}</b>"
                                    )
                                    mgr_token = get_manager_token(b["id"])
                                    stat_markup = {
                                        "inline_keyboard": [[
                                            {"text": "📊 Открыть полный дашборд", "url": f"https://geopulse-yo8k.onrender.com/dashboard/{b['id']}?auth={mgr_token}"}
                                        ]]
                                    }
                                    await send_message(chat_id, stat_msg, reply_markup=stat_markup)
                else:
                    logger.warning(f"Telegram polling status {resp.status_code}. Sleeping 5s before retry.")
                    await asyncio.sleep(5)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Ошибка в цикле Telegram поллинга: {e}")
            await asyncio.sleep(5)
