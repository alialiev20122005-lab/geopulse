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
    """Главная страница панели со всеми зарегистрированными заведениями."""
    businesses = get_all_businesses()
    
    html_cards = ""
    for b in businesses:
        owners = get_business_chat_ids(b["id"])
        owner_badge = f'<span class="bg-emerald-100 text-emerald-800 text-[10px] font-bold px-2 py-0.5 rounded-full">TG: {len(owners)} подключен(о)</span>' if owners else '<span class="bg-amber-100 text-amber-800 text-[10px] font-bold px-2 py-0.5 rounded-full">TG не привязан</span>'
        
        html_cards += f"""
        <div class="p-5 rounded-2xl border border-slate-200/80 hover:border-amber-400 bg-white shadow-sm hover:shadow-md transition flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
            <div>
                <div class="flex items-center gap-2 mb-1">
                    <span class="text-xs font-semibold text-slate-500 bg-slate-100 px-2.5 py-0.5 rounded-md">{b["category"]}</span>
                    {owner_badge}
                </div>
                <h3 class="text-lg font-bold text-slate-900">{b["name"]}</h3>
                <p class="text-xs text-slate-500">{b["address"]}</p>
            </div>
            <div class="flex flex-wrap items-center gap-2 w-full sm:w-auto">
                <a href="/dashboard/{b['id']}" class="flex-1 sm:flex-none text-center bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold px-3 py-2 rounded-xl transition">
                    Дашборд 📊
                </a>
                <a href="/print/{b['id']}" target="_blank" class="flex-1 sm:flex-none text-center bg-white border border-slate-300 hover:bg-slate-50 text-slate-700 text-xs font-semibold px-3 py-2 rounded-xl transition">
                    Тейбл-тент 🖨️
                </a>
                <a href="/r/{b['id']}" target="_blank" class="flex-1 sm:flex-none text-center bg-slate-900 hover:bg-slate-800 text-white text-xs font-semibold px-4 py-2 rounded-xl transition">
                    Экран гостя ↗
                </a>
            </div>
        </div>
        """

    content = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>GeoPulse — Мульти-тенант платформа</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
        <style>body {{ font-family: 'Inter', sans-serif; }}</style>
    </head>
    <body class="bg-slate-50 text-slate-900 p-4 sm:p-8 min-h-screen">
        <div class="max-w-3xl mx-auto">
            
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8 pb-6 border-b border-slate-200">
                <div>
                    <div class="inline-flex items-center gap-1.5 bg-amber-100 text-amber-800 text-[11px] px-3 py-1 rounded-full font-bold uppercase tracking-wider mb-2">
                        <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                        Мульти-тенант движок активен
                    </div>
                    <h1 class="text-3xl font-extrabold text-slate-900">GeoPulse Control Center</h1>
                    <p class="text-xs text-slate-500 mt-1">Единая платформа перехвата негатива и защиты рейтинга</p>
                </div>
                <div>
                    <a href="/new" class="inline-flex items-center gap-2 bg-gradient-to-r from-amber-500 to-amber-600 hover:from-amber-600 hover:to-amber-700 text-white font-bold text-xs py-3 px-5 rounded-xl shadow-lg shadow-amber-500/20 transition active:scale-[0.98]">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"></path></svg>
                        + Подключить новое заведение
                    </a>
                </div>
            </div>

            <div class="space-y-4 mb-8">
                {html_cards}
            </div>

            <div class="bg-slate-900 text-white rounded-3xl p-6 shadow-xl relative overflow-hidden">
                <div class="relative z-10">
                    <h3 class="text-base font-bold mb-1">Как работает подключение для бизнеса:</h3>
                    <p class="text-xs text-slate-400 mb-4 leading-relaxed">
                        Любое заведение регистрируется за 1 минуту. Владелец нажимает одну кнопку в Telegram и мгновенно связывается с точкой. Все отзывы направляются только ему.
                    </p>
                    <a href="/new" class="inline-block bg-white text-slate-900 font-bold text-xs py-2 px-4 rounded-xl hover:bg-slate-100 transition">
                        Попробовать создать тестовое заведение →
                    </a>
                </div>
            </div>

        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=content)

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
