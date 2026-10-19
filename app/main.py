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
    get_business_chat_ids
)
from app.bot import broadcast_feedback, telegram_polling_loop

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

class FeedbackPayload(BaseModel):
    business_id: str
    rating: int
    comment: str
    contact: Optional[str] = ""
    table_number: Optional[str] = ""

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

    base_url = str(request.base_url).rstrip("/")
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

    return Response(content=buf.getvalue(), media_type="image/png")

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
