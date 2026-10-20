import io
import json
import logging
import asyncio
import hashlib
import secrets
from datetime import datetime
from typing import Optional
from contextlib import asynccontextmanager

import time
from collections import defaultdict

import qrcode
from fastapi import FastAPI, HTTPException, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, Response, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# In-memory Rate Limiter для защиты от флуда и спама
class InMemoryRateLimiter:
    def __init__(self, requests_per_minute: int = 30):
        self.rpm = requests_per_minute
        self.history = defaultdict(list)

    def check(self, client_ip: str) -> bool:
        now = time.time()
        window_start = now - 60
        self.history[client_ip] = [t for t in self.history[client_ip] if t > window_start]
        if len(self.history[client_ip]) >= self.rpm:
            return False
        self.history[client_ip].append(now)
        return True

feedback_rate_limiter = InMemoryRateLimiter(requests_per_minute=20)
pilot_rate_limiter = InMemoryRateLimiter(requests_per_minute=10)

from app.config import TELEGRAM_BOT_USERNAME, TELEGRAM_CHAT_ID, ADMIN_PASSWORD, SESSION_SECRET, PLANS, FOUNDER_PHONE
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
    get_recent_leads,
    update_feedback_status,
    get_feedback_by_id,
    get_payment_by_provider_id,
    mark_payment_succeeded
)
from app.bot import broadcast_feedback, telegram_polling_loop, send_message, get_manager_token
from app.admin import is_admin
from app.payments import init_payment

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

# Подключение статических ресурсов и фавиконки
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon_endpoint():
    return FileResponse("app/static/favicon.ico")

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

from app.admin import is_admin

@app.get("/new", response_class=RedirectResponse)
async def new_business_page(request: Request):
    """Публичный доступ закрыт: клиенты направляются на заявку, администратор — в панель."""
    if is_admin(request):
        return RedirectResponse(url="/admin/businesses", status_code=303)
    return RedirectResponse(url="/#connect", status_code=303)

@app.post("/new")
async def handle_new_business(
    request: Request,
    name: str = Form(...),
    category: str = Form(...),
    id: str = Form(...),
    address: str = Form(...),
    yandex_maps_url: str = Form(...)
):
    """Создание заведений доступно только авторизованному администратору платформы."""
    if not is_admin(request):
        raise HTTPException(
            status_code=403, 
            detail="Прямое добавление заведений ограничено. Пожалуйста, оставьте заявку на подключение на главной странице."
        )

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

def get_manager_token(business_id: str) -> str:
    """Генерация криптографического токена доступа для директора заведения."""
    raw = f"manager:{business_id}:{SESSION_SECRET}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]

def is_manager_authorized(business_id: str, request: Request, auth_token: Optional[str] = None) -> bool:
    """Проверка прав доступа к дашборду заведения (Администратор платформы или директор точки)."""
    # 1. Администратор платформы имеет полный доступ ко всем дашбордам
    if is_admin(request):
        return True

    expected = get_manager_token(business_id)

    # 2. Проверка токена из параметров URL (magic-link из Telegram)
    if auth_token and secrets.compare_digest(auth_token.strip(), expected):
        return True

    # 3. Проверка сохраненной сессионной куки директора
    cookie_token = request.cookies.get(f"geopulse_mgr_{business_id}")
    if cookie_token and secrets.compare_digest(cookie_token, expected):
        return True

    return False

@app.get("/dashboard/{business_id}", response_class=HTMLResponse)
async def dashboard_page(business_id: str, request: Request, auth: Optional[str] = None):
    """Дашборд аналитики заведения с защитой конфиденциальности."""
    business = get_business(business_id)
    if not business:
        raise HTTPException(status_code=404, detail="Заведение не найдено")

    # Проверяем авторизацию
    if not is_manager_authorized(business_id, request, auth):
        return templates.TemplateResponse(
            request=request,
            name="dashboard_lock.html",
            context={
                "business": business,
                "bot_username": TELEGRAM_BOT_USERNAME,
                "error": None
            }
        )

    stats_info = get_business_stats(business_id)
    owners = get_business_chat_ids(business_id)
    telegram_deep_link = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start=biz_{business_id}"

    response = templates.TemplateResponse(
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

    # Если вход по magic-link из Telegram — сохраняем сессию на 30 дней
    if auth and secrets.compare_digest(auth.strip(), get_manager_token(business_id)):
        response.set_cookie(
            key=f"geopulse_mgr_{business_id}",
            value=get_manager_token(business_id),
            max_age=30 * 86400,
            httponly=True,
            samesite="lax"
        )

    return response

@app.post("/dashboard/{business_id}/unlock")
async def unlock_dashboard(business_id: str, request: Request, access_code: str = Form(...)):
    """Разблокировка дашборда по коду доступа или паролю администратора."""
    business = get_business(business_id)
    if not business:
        raise HTTPException(status_code=404, detail="Заведение не найдено")

    mgr_token = get_manager_token(business_id)
    valid_code = secrets.compare_digest(access_code.strip(), mgr_token) or secrets.compare_digest(access_code.strip(), ADMIN_PASSWORD)

    if valid_code:
        response = RedirectResponse(url=f"/dashboard/{business_id}", status_code=303)
        response.set_cookie(
            key=f"geopulse_mgr_{business_id}",
            value=mgr_token,
            max_age=30 * 86400,
            httponly=True,
            samesite="lax"
        )
        return response
    else:
        return templates.TemplateResponse(
            request=request,
            name="dashboard_lock.html",
            context={
                "business": business,
                "bot_username": TELEGRAM_BOT_USERNAME,
                "error": "Неверный код доступа или пароль администратора."
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
def generate_qr(business_id: str, request: Request, table: Optional[str] = ""):
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
async def receive_feedback(payload: FeedbackPayload, request: Request, background_tasks: BackgroundTasks):
    """Прием отзыва клиента: запись в базу SQLite и мгновенная рассылка в Telegram в фоне."""
    client_ip = request.client.host if request.client else "unknown"
    if not feedback_rate_limiter.check(client_ip):
        raise HTTPException(status_code=429, detail="Слишком много обращений. Пожалуйста, подождите 1 минуту.")

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

    # 2. Мгновенная рассылка всем привязанным управляющим через BackgroundTasks
    background_tasks.add_task(
        broadcast_feedback,
        business_id=payload.business_id,
        rating=payload.rating,
        comment=payload.comment,
        contact=payload.contact,
        table_number=payload.table_number or ""
    )

    return {"status": "ok", "feedback_id": feedback_id, "sent_to_telegram_count": 1}

@app.post("/api/feedback/{feedback_id}/status")
async def change_feedback_status(feedback_id: int, request: Request, status: str = Form(...)):
    """Изменение статуса обработки жалобы/отзыва ('new' | 'resolved'). Доступно директору или админу."""
    fb = get_feedback_by_id(feedback_id)
    if not fb:
        raise HTTPException(status_code=404, detail="Отзыв не найден")

    # Проверка прав: администратор или директор этого заведения
    admin_access = is_admin(request)
    manager_cookie = request.cookies.get(f"dashboard_unlocked_{fb['business_id']}")
    auth_param = request.query_params.get("auth") or request.headers.get("x-auth-token")
    expected_token = get_manager_token(fb['business_id'])

    manager_access = (manager_cookie == "1") or (auth_param == expected_token)

    if not (admin_access or manager_access):
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    new_status = "resolved" if status.strip().lower() == "resolved" else "new"
    update_feedback_status(feedback_id, new_status)
    return {"status": "ok", "feedback_id": feedback_id, "new_status": new_status}

@app.get("/pitch", response_class=HTMLResponse)
@app.get("/offer", response_class=HTMLResponse)
async def pitch_presentation_page(request: Request):
    """Коммерческое предложение для владельцев и управляющих заведений."""
    return templates.TemplateResponse(
        request=request,
        name="pitch.html",
        context={}
    )

@app.get("/privacy", response_class=HTMLResponse)
async def privacy_policy_page(request: Request):
    """Политика обработки персональных данных (152-ФЗ)."""
    return templates.TemplateResponse(
        request=request,
        name="privacy.html",
        context={}
    )

@app.get("/terms", response_class=HTMLResponse)
async def terms_of_service_page(request: Request):
    """Пользовательское соглашение и публичная оферта."""
    return templates.TemplateResponse(
        request=request,
        name="terms.html",
        context={}
    )

@app.post("/api/pilot-request")
async def handle_pilot_request(payload: PilotRequestPayload, request: Request, background_tasks: BackgroundTasks):
    """Прием заявки на 14-дневный бесплатный тест-драйв с мгновенным оповещением в Telegram."""
    client_ip = request.client.host if request.client else "unknown"
    if not pilot_rate_limiter.check(client_ip):
        raise HTTPException(status_code=429, detail="Слишком много заявок. Пожалуйста, подождите 1 минуту.")

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

    # Отправляем оповещение основателю в Telegram в фоне
    if TELEGRAM_CHAT_ID:
        background_tasks.add_task(send_message, TELEGRAM_CHAT_ID, text, reply_markup)

    return {
        "status": "ok",
        "lead_id": lead_id,
        "message": "Заявка успешно принята! Мы свяжемся с вами в течение 15 минут."
    }

@app.get("/leads", response_class=RedirectResponse)
async def view_leads_redirect():
    """Безопасное перенаправление в защищенный раздел админ-панели."""
    return RedirectResponse(url="/admin/leads", status_code=303)

# --- Платежный модуль (ЮKassa / Банковские счета) ---

class CreatePaymentPayload(BaseModel):
    business_id: str = Field(..., min_length=1, max_length=50)
    plan_id: str = Field(..., pattern=r"^(start|business|network)$")
    phone: Optional[str] = Field("", max_length=50)
    email: Optional[str] = Field("", max_length=100)

@app.post("/api/payments/create")
async def create_payment_endpoint(payload: CreatePaymentPayload):
    """Инициализация оплаты тарифа заведения."""
    biz = get_business(payload.business_id)
    if not biz:
        raise HTTPException(status_code=404, detail="Заведение не найдено")

    result = await init_payment(
        business_id=payload.business_id,
        plan_id=payload.plan_id,
        phone=payload.phone or "",
        email=payload.email or ""
    )
    return result

@app.get("/pay/sandbox", response_class=HTMLResponse)
async def pay_sandbox_page(pid: str, request: Request):
    """Экран оплаты подписки (тестовый режим / выставление счета)."""
    payment = get_payment_by_provider_id(pid)
    if not payment:
        raise HTTPException(status_code=404, detail="Платеж не найден")

    biz = get_business(payment["business_id"])
    plan = PLANS.get(payment["plan"], PLANS["start"])

    # Готовим текст для выставления счета
    invoice_msg = (
        f"Здравствуйте, Али! Прошу выставить счет на оплату сервиса GeoPulse:\n"
        f"• Заведение: {biz['name'] if biz else payment['business_id']}\n"
        f"• Тариф: {plan['name']} ({plan['price']} ₽)\n"
        f"• ID платежа: {pid}\n\n"
        f"Наши реквизиты высылаю ниже:"
    )
    import urllib.parse
    invoice_text = urllib.parse.quote(invoice_msg)

    return templates.TemplateResponse(
        request=request,
        name="pay_sandbox.html",
        context={
            "payment": payment,
            "business": biz,
            "plan": plan,
            "invoice_text": invoice_text
        }
    )

@app.post("/api/payments/sandbox-confirm")
async def sandbox_confirm_payment(pid: str = Form(...), background_tasks: BackgroundTasks = None):
    """Подтверждение оплаты в песочнице."""
    payment = get_payment_by_provider_id(pid)
    if not payment:
        raise HTTPException(status_code=404, detail="Платеж не найден")

    mark_payment_succeeded(pid, days=30)
    biz = get_business(payment["business_id"])
    biz_name = biz["name"] if biz else payment["business_id"]

    # Уведомление основателю в Telegram
    if TELEGRAM_CHAT_ID:
        notice = (
            f"💰 <b>НОВАЯ ОПЛАТА ПОДПИСКИ!</b>\n\n"
            f"🏢 <b>Заведение:</b> {html.escape(biz_name)}\n"
            f"📦 <b>Тариф:</b> {payment['plan'].upper()}\n"
            f"💵 <b>Сумма:</b> {payment['amount']} ₽\n"
            f"📱 <b>Контакт:</b> {payment.get('payer_phone') or 'Не указан'}\n"
            f"✅ <i>Статус заведения переведен в 'active' на 30 дней.</i>"
        )
        if background_tasks:
            background_tasks.add_task(send_message, TELEGRAM_CHAT_ID, notice)
        else:
            await send_message(TELEGRAM_CHAT_ID, notice)

    return RedirectResponse(url=f"/pay/success?bid={payment['business_id']}&plan={payment['plan']}", status_code=303)

@app.post("/api/payments/webhook")
async def yookassa_webhook(request: Request, background_tasks: BackgroundTasks):
    """Официальный вебхук ЮKassa для обработки события payment.succeeded."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event = data.get("event")
    obj = data.get("object", {})

    if event == "payment.succeeded":
        payment_id = obj.get("id")
        if payment_id:
            success = mark_payment_succeeded(payment_id, days=30)
            if success:
                logger.info(f"✅ Успешный платеж ЮKassa #{payment_id} обработан, подписка продлена.")
                if TELEGRAM_CHAT_ID:
                    amount = obj.get("amount", {}).get("value", "0")
                    msg = (
                        f"🎉 <b>ПОЛУЧЕНА ОПЛАТА ЧЕРЕЗ ЮKASSA!</b>\n\n"
                        f"💳 <b>ID платежа:</b> <code>{payment_id}</code>\n"
                        f"💵 <b>Сумма:</b> {amount} ₽\n"
                        f"✅ <i>Подписка заведения успешно активирована на 30 дней.</i>"
                    )
                    background_tasks.add_task(send_message, TELEGRAM_CHAT_ID, msg)

    return {"status": "ok"}

@app.get("/pay/success", response_class=HTMLResponse)
async def payment_success_page(request: Request, bid: str = "daikon", plan: str = "start"):
    """Экран успешной оплаты подписки."""
    biz = get_business(bid)
    if not biz:
        biz = {"id": bid, "name": "Ваше заведение"}

    from datetime import datetime, timedelta
    paid_until = (datetime.now() + timedelta(days=30)).strftime("%d.%m.%Y")

    return templates.TemplateResponse(
        request=request,
        name="pay_success.html",
        context={
            "business": biz,
            "plan_id": plan,
            "paid_until": paid_until
        }
    )

