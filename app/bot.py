import asyncio
import logging
from datetime import datetime
from typing import List, Optional
import httpx

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from app.database import link_telegram_owner, get_business, get_business_chat_ids, get_business_stats

logger = logging.getLogger("GeoPulseBot")

async def send_message(chat_id: str, text: str, reply_markup: Optional[dict] = None) -> bool:
    """Отправка сообщения в Telegram по chat_id."""
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
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения пользователю {chat_id}: {e}")
        return False

async def broadcast_feedback(business_id: str, rating: int, comment: str, contact: str, table_number: str = ""):
    """Рассылка жалобы всем привязанным управляющим этого заведения."""
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

    stars_str = "⭐" * rating
    table_info = f"\n🪑 <b>Столик:</b> {table_number}" if table_number else ""
    
    text = (
        f"🚨 <b>ВНИМАНИЕ: ПЕРЕХВАЧЕН НЕГАТИВ!</b>\n\n"
        f"🏢 <b>Заведение:</b> {business_name}{table_info}\n"
        f"📊 <b>Оценка клиента:</b> {stars_str} ({rating} из 5)\n"
        f"💬 <b>Претензия:</b>\n<i>«{comment}»</i>\n\n"
        f"📱 <b>Контакт:</b> {contact if contact else 'Не указан'}\n"
        f"🕒 <b>Время:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        f"────────────────────\n"
        f"🤖 <b>ИИ-ПОМОЩНИК ПОДГОТОВИЛ ОТВЕТЫ:</b>\n\n"
        f"💬 <b>1. Для отправки клиенту в WhatsApp / SMS:</b>\n"
        f"<code>{personal_reply}</code>\n\n"
        f"🗺️ <b>2. Для публичного ответа на Яндекс.Картах:</b>\n"
        f"<code>{public_reply}</code>\n"
        f"────────────────────\n"
        f"💡 <i>Нажмите на текст в рамке выше, чтобы скопировать в 1 клик!</i>"
    )

    success_count = 0
    for cid in chat_ids:
        if await send_message(cid, text):
            success_count += 1

    return success_count

async def telegram_polling_loop():
    """Фоновый цикл приема сообщений от Telegram (обработка /start и привязки)."""
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
                            business_id = text.replace("/start biz_", "").strip()
                            business = get_business(business_id)

                            if business:
                                link_telegram_owner(business_id, chat_id)
                                welcome_msg = (
                                    f"🎉 <b>Поздравляем, {user_name}!</b>\n\n"
                                    f"Вы успешно подключили уведомления для заведения:\n"
                                    f"🏢 <b>«{business['name']}»</b>\n"
                                    f"📍 {business['address']}\n\n"
                                    f"🔔 <i>Теперь каждое обращение недовольного клиента будет приходить лично вам за 1 секунду.</i>\n\n"
                                    f"Команды:\n"
                                    f"📊 /stats — статистика заведения"
                                )
                                await send_message(chat_id, welcome_msg)
                            else:
                                await send_message(chat_id, "❌ Заведение с таким идентификатором не найдено.")

                        # 2. Обычный старт
                        elif text == "/start":
                            msg = (
                                f"👋 Привет, {user_name}!\n\n"
                                f"Я официальный бот платформы <b>GeoPulse</b>.\n"
                                f"Я защищаю рейтинги заведений на Яндекс.Картах и передаю жалобы клиентов напрямую руководству.\n\n"
                                f"Чтобы привязать свое заведение, добавьте его на сайте и нажмите кнопку «Подключить Telegram»."
                            )
                            await send_message(chat_id, msg)

                        # 3. Статистика
                        elif text.startswith("/stats"):
                            # Находим заведения этого управляющего
                            # Для простоты выводим Daikon или первое связанное
                            business = get_business("daikon")
                            if business:
                                stats_data = get_business_stats("daikon")
                                s = stats_data["stats"]
                                total = s["total_count"] or 0
                                avg_r = round(s["avg_rating"] or 5.0, 1)
                                neg = s["negative_count"] or 0
                                pos = s["positive_count"] or 0
                                
                                stat_msg = (
                                    f"📊 <b>Статистика: {business['name']}</b>\n\n"
                                    f"👥 Всего отзывов: <b>{total}</b>\n"
                                    f"⭐ Средняя оценка: <b>{avg_r} / 5.0</b>\n"
                                    f"🚨 Перехвачено негатива: <b>{neg}</b>\n"
                                    f"💚 Направлено на карты: <b>{pos}</b>"
                                )
                                await send_message(chat_id, stat_msg)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Ошибка в цикле Telegram поллинга: {e}")
            await asyncio.sleep(5)
