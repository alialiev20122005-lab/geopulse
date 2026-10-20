import asyncio
import hashlib
import html
import logging
from datetime import datetime
from typing import List, Optional
import httpx

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SESSION_SECRET, BASE_URL
from app.database import (
    link_telegram_owner,
    get_business,
    get_business_chat_ids,
    get_business_stats,
    get_businesses_by_chat_id,
    get_daily_digest,
    get_all_businesses
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
        clean_contact = contact.strip()
        is_telegram = clean_contact.startswith("@") or "t.me/" in clean_contact.lower()

        # Извлекаем только цифры из телефона
        digits = re.sub(r"\D", "", contact)
        
        # Если это российский номер (89... -> 79...)
        if len(digits) == 11 and digits.startswith("8"):
            digits = "7" + digits[1:]
        elif len(digits) == 10:
            digits = "7" + digits

        if is_telegram:
            tg_username = clean_contact.replace("https://", "").replace("http://", "").replace("t.me/", "").lstrip("@").strip()
            buttons.append([
                {
                    "text": f"💬 Открыть диалог в Telegram (@{tg_username})",
                    "url": f"https://t.me/{tg_username}"
                }
            ])
        elif len(digits) >= 10:
            wa_encoded_text = urllib.parse.quote(personal_reply)
            buttons.append([
                {
                    "text": "💬 Написать клиенту в WhatsApp",
                    "url": f"https://wa.me/{digits}?text={wa_encoded_text}"
                }
            ])
        else:
            # Проверяем, если контакт введен без @ (например: ivan_rest)
            tg_username = clean_contact.replace("https://", "").replace("http://", "").replace("t.me/", "").lstrip("@").strip()
            if re.fullmatch(r"[a-zA-Z0-9_]{3,32}", tg_username):
                buttons.append([
                    {
                        "text": f"💬 Открыть диалог в Telegram (@{tg_username})",
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

def generate_digest_text(business_id: Optional[str] = None, business_name: Optional[str] = None) -> str:
    """Генерация текста суточного отчета для Telegram."""
    digest = get_daily_digest(business_id)
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    if business_name:
        title = f"📊 <b>СУТОЧНЫЙ ДАЙДЖЕСТ: {html.escape(business_name)}</b>"
    else:
        title = "🌐 <b>ГЛОБАЛЬНЫЙ ДАЙДЖЕСТ GEOPULSE (24 ЧАСА)</b>"

    total = digest["total_24h"]
    avg_r = digest["avg_rating_24h"]
    neg = digest["negative_24h"]
    pos = digest["positive_24h"]
    unres = digest["unresolved_24h"]
    leads = digest["new_leads_24h"]

    lines = [
        title,
        f"🕒 <i>Данные на {now_str}</i>\n",
        f"📝 Новых отзывов за 24 ч: <b>{total}</b>",
        f"⭐ Средняя оценка: <b>{avg_r} / 5.0</b>",
        f"💚 Положительных (4-5★): <b>{pos}</b>",
        f"🚨 Перехвачено негатива: <b>{neg}</b>",
    ]

    if unres > 0:
        lines.append(f"⚠️ <b>Требуют ответа управляющего: {unres} 🔴</b>")
    else:
        lines.append("✅ Все инциденты урегулированы!")

    if not business_id and leads > 0:
        lines.append(f"\n🎯 <b>Новых заявок на пилот: {leads}</b>")

    lines.append("\n💡 <i>Отслеживайте динамику в режиме реального времени на платформе.</i>")
    return "\n".join(lines)

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

                        # 1. Универсальная обработка команды /start
                        if text.startswith("/start"):
                            parts = text.split()
                            raw_param = ""
                            if len(parts) > 1:
                                raw_param = parts[1].strip()
                            elif "@" in parts[0] and len(parts) > 1:
                                raw_param = parts[1].strip()

                            # Очищаем параметр от суффикса @bot_username
                            if "@" in raw_param:
                                raw_param = raw_param.split("@")[0].strip()

                            if raw_param:
                                # Очищаем возможный префикс biz_ или biz-
                                clean_id = raw_param
                                if clean_id.lower().startswith("biz_"):
                                    clean_id = clean_id[4:]
                                elif clean_id.lower().startswith("biz-"):
                                    clean_id = clean_id[4:]
                                clean_id = clean_id.strip().lower()

                                # Проверяем варианты: clean_id, raw_param, biz_clean_id
                                candidate_ids = [clean_id, raw_param.strip().lower(), f"biz_{clean_id}"]
                                business = None
                                for cid in candidate_ids:
                                    if cid:
                                        b = get_business(cid)
                                        if b:
                                            business = b
                                            break

                                # Поиск по названию (регистронезависимо)
                                if not business:
                                    all_b = get_all_businesses()
                                    for b in all_b:
                                        if b["id"].lower() == clean_id or b["name"].lower().strip() == clean_id:
                                            business = b
                                            break

                                if business:
                                    link_telegram_owner(business["id"], chat_id)
                                    welcome_msg = (
                                        f"🎉 <b>Поздравляем, {html.escape(user_name)}!</b>\n\n"
                                        f"Вы успешно подключили уведомления для заведения:\n"
                                        f"🏢 <b>«{html.escape(business['name'])}»</b>\n"
                                        f"📍 {html.escape(business['address'])}\n\n"
                                        f"🔔 <i>Теперь каждое обращение недовольного клиента будет приходить лично вам за 1 секунду.</i>\n\n"
                                        f"Команды:\n"
                                        f"📊 /stats — статистика вашего заведения\n"
                                        f"📈 /digest — суточный дайджест"
                                    )
                                    mgr_token = get_manager_token(business["id"])
                                    welcome_markup = {
                                        "inline_keyboard": [[
                                            {"text": "📊 Открыть личный дашборд", "url": f"{BASE_URL}/dashboard/{business['id']}?auth={mgr_token}"}
                                        ]]
                                    }
                                    await send_message(chat_id, welcome_msg, reply_markup=welcome_markup)
                                else:
                                    all_b = get_all_businesses()
                                    biz_lines = [f"• <b>{html.escape(b['name'])}</b>: <code>/start biz_{b['id']}</code>" for b in all_b[:5]]
                                    list_str = "\n".join(biz_lines) if biz_lines else "Пока нет добавленных заведений."
                                    help_msg = (
                                        f"❌ <b>Заведение с кодом «{html.escape(raw_param)}» не найдено в базе.</b>\n\n"
                                        f"<b>Возможные причины:</b>\n"
                                        f"1. Заведение создано на локальном сервере, а бот подключен к облачной базе Render.\n"
                                        f"2. Опечатка в ID (проверьте ID в панели управления).\n\n"
                                        f"<b>Доступные заведения:</b>\n"
                                        f"{list_str}\n\n"
                                        f"💡 <i>Нажмите на команду вида <code>/start biz_...</code> выше, чтобы подключить его.</i>"
                                    )
                                    await send_message(chat_id, help_msg)

                            else:
                                # Обычный старт без параметров
                                my_businesses = get_businesses_by_chat_id(chat_id)
                                if my_businesses:
                                    linked_str = ", ".join([f"«{html.escape(b['name'])}»" for b in my_businesses])
                                    msg = (
                                        f"👋 С возвращением, {html.escape(user_name)}!\n\n"
                                        f"Вы привязаны к заведениям: <b>{linked_str}</b>.\n\n"
                                        f"Команды:\n"
                                        f"📊 /stats — статистика обращений\n"
                                        f"📈 /digest — отчет за последние 24 часа"
                                    )
                                else:
                                    all_b = get_all_businesses()
                                    biz_lines = [f"• <b>{html.escape(b['name'])}</b>: <code>/start biz_{b['id']}</code>" for b in all_b[:5]]
                                    list_str = "\n".join(biz_lines) if biz_lines else "Пока нет добавленных заведений."
                                    msg = (
                                        f"👋 Привет, {html.escape(user_name)}!\n\n"
                                        f"Я официальный бот платформы <b>GeoPulse</b>.\n"
                                        f"Я защищаю рейтинги заведений на Яндекс.Картах и передаю жалобы клиентов напрямую руководству.\n\n"
                                        f"<b>Как подключить заведение:</b>\n"
                                        f"Нажмите ссылку в личном кабинете или отправьте команду:\n"
                                        f"{list_str}"
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
                                            {"text": "📊 Открыть полный дашборд", "url": f"{BASE_URL}/dashboard/{b['id']}?auth={mgr_token}"}
                                        ]]
                                    }
                                    await send_message(chat_id, stat_msg, reply_markup=stat_markup)

                        # 4. Суточный дайджест за 24 часа: /digest
                        elif text.startswith("/digest"):
                            if chat_id == TELEGRAM_CHAT_ID:
                                global_text = generate_digest_text()
                                await send_message(chat_id, global_text)
                            
                            my_businesses = get_businesses_by_chat_id(chat_id)
                            if my_businesses:
                                for b in my_businesses[:5]:
                                    b_text = generate_digest_text(business_id=b["id"], business_name=b["name"])
                                    mgr_token = get_manager_token(b["id"])
                                    digest_markup = {
                                        "inline_keyboard": [[
                                            {"text": "📊 Дашборд заведения", "url": f"{BASE_URL}/dashboard/{b['id']}?auth={mgr_token}"}
                                        ]]
                                    }
                                    await send_message(chat_id, b_text, reply_markup=digest_markup)
                            elif chat_id != TELEGRAM_CHAT_ID:
                                await send_message(
                                    chat_id,
                                    "⚠️ <b>Вы еще не привязаны ни к одному заведению.</b>\n\n"
                                    "Привяжите заведение через кнопку «Подключить Telegram» в личном кабинете."
                                )
                else:
                    logger.warning(f"Telegram polling status {resp.status_code}. Sleeping 5s before retry.")
                    await asyncio.sleep(5)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Ошибка в цикле Telegram поллинга: {e}")
            await asyncio.sleep(5)
