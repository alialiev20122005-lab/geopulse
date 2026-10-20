import io
import json
import logging
import asyncio
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

import qrcode
from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, Response, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import TELEGRAM_BOT_USERNAME, TELEGRAM_CHAT_ID
from app.database import (
    init_db,
    seed_initial_data,
    get_business,
    get_all_businesses,
    create_business,
    add_feedback,
    get_business_stats,
    get_business_chat_ids,
    create_lead,
    get_recent_leads
)
from app.bot import broadcast_feedback, telegram_polling_loop, send_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("GeoPulse")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Инициализация БД и сидинг демо-данных
    init_db()
    seed_initial_data(TELEGRAM_CHAT_ID)
    logger.info("✅ База данных SQLite успешно инициализирована.")

    # 2. Запуск фонового поллинга Telegram для приема /start biz_<id>
    polling_task = asyncio.create_task(telegram_polling_loop())
    yield
    polling_task.cancel()

app = FastAPI(title="GeoPulse Multi-Tenant", lifespan=lifespan)
templates = Jinja2Templates(directory="app/templates")

from app.admin import router as admin_router
app.include_router(admin_router)

import html
import re
from pydantic import BaseModel, Field

class FeedbackPayload(BaseModel):
    business_id: str = Field(..., min_length=1, max_length=50)
    rating: int = Field(..., ge=1, le=5)
    comment: str = Field(..., min_length=1, max_length=2000)
    contact: Optional[str] = Field("", max_length=100)
    table_number: Optional[str] = Field("", max_length=30)

class PilotRequestPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    phone: str = Field(..., min_length=5, max_length=50)
    business_name: str = Field(..., min_length=1, max_length=120)
    city: Optional[str] = Field("", max_length=80)
    tables_count: Optional[str] = Field("", max_length=30)
    source: Optional[str] = Field("web", max_length=50)

# --- Веб-роуты ---

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Главная страница — конверсионный SaaS-лендинг платформы GeoPulse."""
    businesses = get_all_businesses()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "businesses": businesses
        }
    )

@app.get("/new", response_class=HTMLResponse)
async def new_business_page(request: Request):
    """Форма добавления нового заведения."""
    return templates.TemplateResponse(request=request, name="new_business.html", context={})

@app.post("/new")
async def handle_new_business(
    name: str = Form(...),
    category: str = Form(...),
    id: str = Form(...),
    address: str = Form(...),
    yandex_maps_url: str = Form(...)
):
    """Обработка создания заведения."""
    business_id = id.lower().strip()
    
    if not re.fullmatch(r"[a-z0-9_-]{2,30}", business_id):
        raise HTTPException(
            status_code=400,
            detail="ID заведения должен содержать от 2 до 30 символов: только латинские буквы, цифры, дефис или подчеркивание"
        )
    
    # Проверяем, существует ли уже такой ID
    if get_business(business_id):
        raise HTTPException(status_code=400, detail="Заведение с таким ID уже существует")

    data = {
        "id": business_id,
        "name": name.strip(),
        "category": category,
        "address": address.strip(),
        "yandex_maps_url": yandex_maps_url.strip(),
        "two_gis_url": "",
        "welcome_text": f"Поделитесь впечатлениями о визите в «{name.strip()}». Ваше мнение помогает нам становиться лучше!",
        "negative_cta": "Нам очень жаль! Напишите управляющему, чтобы мы могли сразу исправить ситуацию:",
        "positive_cta": "Спасибо за вашу оценку! Пожалуйста, поддержите нас отзывом на Яндекс.Картах ❤️"
    }

    create_business(data)
    return RedirectResponse(url=f"/success/{business_id}", status_code=303)

@app.get("/success/{business_id}", response_class=HTMLResponse)
async def success_page(business_id: str, request: Request):
    """Экран успешного создания с deep link для Telegram."""
    business = get_business(business_id)
    if not business:
        raise HTTPException(status_code=404, detail="Заведение не найдено")

    telegram_deep_link = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start=biz_{business_id}"
    return templates.TemplateResponse(
        request=request,
        name="onboarding_success.html",
        context={
            "business": business,
            "telegram_deep_link": telegram_deep_link
        }
    )

@app.get("/dashboard/{business_id}", response_class=HTMLResponse)
async def dashboard_page(business_id: str, request: Request):
    """Дашборд аналитики заведения."""
    business = get_business(business_id)
    if not business:
        raise HTTPException(status_code=404, detail="Заведение не найдено")

    stats_info = get_business_stats(business_id)
    owners = get_business_chat_ids(business_id)
    telegram_deep_link = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start=biz_{business_id}"

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "business": business,
            "stats": stats_info["stats"],
            "feedbacks": stats_info["feedbacks"],
            "owner_count": len(owners),
            "telegram_deep_link": telegram_deep_link
        }
    )

@app.get("/r/{business_id}", response_class=HTMLResponse)
async def review_page(business_id: str, request: Request, table: Optional[str] = ""):
    """Страница оценки клиентом по QR-коду."""
    business = get_business(business_id)
    if not business:
        raise HTTPException(status_code=404, detail="Заведение не найдено")

    return templates.TemplateResponse(
        request=request,
        name="review.html",
        context={
            "business": business,
            "business_id": business_id,
            "table_number": table
        }
    )

@app.get("/print/{business_id}/all", response_class=HTMLResponse)
async def print_bulk_tents_page(business_id: str, request: Request, tables: int = 10):
    """Массовая печать карточек столиков (раскладка А4 для типографии)."""
    business = get_business(business_id)
    if not business:
        raise HTTPException(status_code=404, detail="Заведение не найдено")

    count = max(1, min(100, tables))
    return templates.TemplateResponse(
        request=request,
        name="table_tent_bulk.html",
        context={
            "business": business,
            "business_id": business_id,
            "tables_count": count,
            "tables_range": list(range(1, count + 1))
        }
    )

@app.get("/print/{business_id}", response_class=HTMLResponse)
async def print_tent_page(business_id: str, request: Request, table: Optional[str] = ""):
    """Страница тейбл-тента для печати на принтере."""
    business = get_business(business_id)
    if not business:
        raise HTTPException(status_code=404, detail="Заведение не найдено")

    return templates.TemplateResponse(
        request=request,
        name="table_tent.html",
        context={
            "business": business,
            "business_id": business_id,
            "table_number": table
        }
    )

@app.get("/api/qr/{business_id}")
async def generate_qr(business_id: str, request: Request, table: Optional[str] = ""):
    """Динамическая генерация эстетичного PNG QR-кода со скругленными точками."""
    business = get_business(business_id)
    if not business:
        raise HTTPException(status_code=404, detail="Заведение не найдено")

    from qrcode.image.styledpil import StyledPilImage
    from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer
    from qrcode.image.styles.colormasks import HorizontalGradiantColorMask

    # Поддержка корректного протокола HTTPS за reverse-proxy (Render, Amvera, Cloudflare)
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    base_url = f"{scheme}://{host}".rstrip("/")
    target_url = f"{base_url}/r/{business_id}"
    if table:
        target_url += f"?table={table}"

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=12,
        border=2,
    )
    qr.add_data(target_url)
    qr.make(fit=True)

    # Красочный градиент для самого QR-кода (от сочного индиго к неоновому розовому/коралловому)
    color_mask = HorizontalGradiantColorMask(
        back_color=(255, 255, 255),
        left_color=(79, 70, 229),    # Индиго #4f46e5
        right_color=(225, 29, 72)    # Сочный розовый #e11d48
    )

    img = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=RoundedModuleDrawer(),
        color_mask=color_mask
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400, stale-while-revalidate=604800"}
    )

@app.post("/api/feedback")
async def receive_feedback(payload: FeedbackPayload):
    """Прием отзыва клиента: запись в базу SQLite и мгновенная рассылка в Telegram."""
    business = get_business(payload.business_id)
    if not business:
        raise HTTPException(status_code=404, detail="Заведение не найдено")

    # 1. Запись в постоянную базу данных SQLite
    feedback_id = add_feedback(
        business_id=payload.business_id,
        rating=payload.rating,
        comment=payload.comment,
        contact=payload.contact,
        table_number=payload.table_number or ""
    )

    logger.info(
        f"📝 НОВЫЙ ОТЗЫВ #{feedback_id} [{business['name']}] "
        f"Оценка: {payload.rating}/5 | Комментарий: {payload.comment}"
    )

    # 2. Мгновенная рассылка всем привязанным управляющим через Telegram
    sent_count = await broadcast_feedback(
        business_id=payload.business_id,
        rating=payload.rating,
        comment=payload.comment,
        contact=payload.contact,
        table_number=payload.table_number or ""
    )

    return {"status": "ok", "feedback_id": feedback_id, "sent_to_telegram_count": sent_count}

@app.get("/pitch", response_class=HTMLResponse)
@app.get("/offer", response_class=HTMLResponse)
async def pitch_presentation_page(request: Request):
    """Коммерческое предложение для владельцев и управляющих заведений."""
    return templates.TemplateResponse(
        request=request,
        name="pitch.html",
        context={}
    )

@app.post("/api/pilot-request")
async def handle_pilot_request(payload: PilotRequestPayload):
    """Прием заявки на 14-дневный бесплатный тест-драйв с мгновенным оповещением в Telegram."""
    import re
    import urllib.parse

    # 1. Запись лида в постоянную базу данных SQLite
    lead_id = create_lead(
        name=payload.name,
        business_name=payload.business_name,
        phone=payload.phone,
        city=payload.city or "",
        tables_count=payload.tables_count or "",
        source=payload.source or "pitch"
    )

    logger.info(f"🔥 НОВАЯ ЗАЯВКА НА ПИЛОТ #{lead_id}: {payload.business_name} ({payload.name}, {payload.phone})")

    # 2. Формирование богатого уведомления для основателя/администратора с защитой от HTML Injection
    safe_biz = html.escape(payload.business_name)
    safe_name = html.escape(payload.name)
    safe_phone = html.escape(payload.phone)
    safe_city = html.escape(payload.city) if payload.city else ""
    safe_tables = html.escape(payload.tables_count) if payload.tables_count else ""
    safe_source = html.escape(payload.source or "pitch")

    city_line = f"\n🏙 <b>Город:</b> {safe_city}" if safe_city else ""
    tables_line = f"\n🪑 <b>Столиков/мастеров:</b> {safe_tables}" if safe_tables else ""

    text = (
        f"🔥 <b>НОВАЯ ЗАЯВКА НА 14-ДНЕВНЫЙ ПИЛОТ!</b>\n\n"
        f"🏢 <b>Заведение:</b> {safe_biz}\n"
        f"👤 <b>Контакт:</b> {safe_name}\n"
        f"📱 <b>Телефон:</b> {safe_phone}{city_line}{tables_line}\n"
        f"🌐 <b>Источник:</b> {safe_source}\n"
        f"🕒 <b>Время:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        f"────────────────────\n"
        f"💡 <i>Нажмите кнопку ниже, чтобы сразу открыть диалог в WhatsApp с готовым приветствием:</i>"
    )

    # Формируем кликабельные контакты
    digits = re.sub(r"\D", "", payload.phone)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits

    buttons = []
    if len(digits) >= 10:
        wa_greeting = (
            f"Здравствуйте, {payload.name}! Меня зовут Али, я основатель сервиса защиты репутации GeoPulse.\n\n"
            f"Вы оставили заявку на бесплатный 14-дневный пилот для «{payload.business_name}».\n"
            f"Скажите, вам удобно сейчас согласовать дизайн тейбл-тентов и подключение?"
        )
        buttons.append([
            {
                "text": "💬 Написать в WhatsApp",
                "url": f"https://wa.me/{digits}?text={urllib.parse.quote(wa_greeting)}"
            }
        ])

    reply_markup = {"inline_keyboard": buttons} if buttons else None

    # Отправляем оповещение основателю в Telegram
    if TELEGRAM_CHAT_ID:
        await send_message(TELEGRAM_CHAT_ID, text, reply_markup=reply_markup)

    return {
        "status": "ok",
        "lead_id": lead_id,
        "message": "Заявка успешно принята! Мы свяжемся с вами в течение 15 минут."
    }

@app.get("/leads", response_class=RedirectResponse)
async def view_leads_redirect():
    """Безопасное перенаправление в защищенный раздел админ-панели."""
    return RedirectResponse(url="/admin/leads", status_code=303)

