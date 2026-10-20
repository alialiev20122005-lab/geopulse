import csv
import hashlib
import io
import logging
import re
import secrets
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import (
    ADMIN_USERNAME,
    ADMIN_PASSWORD,
    SESSION_SECRET,
    TELEGRAM_BOT_USERNAME
)
from app.database import (
    get_global_stats,
    get_all_businesses,
    get_all_businesses_with_meta,
    get_business,
    create_business,
    update_business,
    delete_business,
    get_recent_leads,
    update_lead_status,
    delete_lead,
    get_all_feedbacks_filtered,
    get_all_managers,
    unlink_telegram_owner_by_id,
    get_feedbacks_for_export,
    get_leads_for_export,
    link_telegram_owner
)

logger = logging.getLogger("GeoPulseAdmin")

router = APIRouter(prefix="/admin", tags=["Admin Panel"])
templates = Jinja2Templates(directory="app/templates")

COOKIE_NAME = "geopulse_admin_session"

def get_auth_token() -> str:
    """Генерация контрольного токена для сессии администратора."""
    raw = f"{ADMIN_USERNAME}:{ADMIN_PASSWORD}:{SESSION_SECRET}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def is_admin(request: Request) -> bool:
    """Проверка наличия валидной сессии администратора с защитой от тайминг-атак."""
    cookie_token = request.cookies.get(COOKIE_NAME)
    if not cookie_token:
        return False
    return secrets.compare_digest(cookie_token, get_auth_token())

def require_admin(request: Request):
    """FastAPI Dependency для проверки прав доступа к админке."""
    if not is_admin(request):
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})

# --- 1. Авторизация и Выход ---

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    """Страница входа в панель администратора."""
    if is_admin(request):
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={"error": error}
    )

@router.post("/login")
async def handle_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    """Обработка попытки входа с защитой от тайминг-атак."""
    valid_user = secrets.compare_digest(username.strip(), ADMIN_USERNAME)
    valid_pass = secrets.compare_digest(password, ADMIN_PASSWORD)
    if valid_user and valid_pass:
        response = RedirectResponse(url="/admin", status_code=303)
        # Устанавливаем защищенную куку сессии на 7 дней
        response.set_cookie(
            key=COOKIE_NAME,
            value=get_auth_token(),
            max_age=7 * 24 * 3600,
            httponly=True,
            samesite="lax"
        )
        logger.info("🔑 Администратор успешно вошел в панель управления.")
        return response
    else:
        logger.warning(f"⚠️ Неудачная попытка входа в админку с логином: {username}")
        return templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={"error": "Неверный логин или пароль администратора."}
        )

@router.get("/logout")
async def handle_logout():
    """Выход из панели администратора."""
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response

# --- 2. Главная страница (Сводка & KPI) ---

@router.get("", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """Главный экран дашборда администратора."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    stats = get_global_stats()
    recent_leads = get_recent_leads(limit=6)
    recent_feedbacks = get_all_feedbacks_filtered(limit=6)

    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={
            "active_page": "dashboard",
            "stats": stats,
            "recent_leads": recent_leads,
            "recent_feedbacks": recent_feedbacks,
            "new_leads_badge": stats.get("new_leads", 0),
            "msg": request.query_params.get("msg"),
            "err": request.query_params.get("err")
        }
    )

# --- 3. Управление заведениями ---

@router.get("/businesses", response_class=HTMLResponse)
async def admin_businesses_page(request: Request):
    """Список всех заведений с возможностью печати, копирования ссылок и редактирования."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    stats = get_global_stats()
    businesses = get_all_businesses_with_meta()

    return templates.TemplateResponse(
        request=request,
        name="admin_businesses.html",
        context={
            "active_page": "businesses",
            "businesses": businesses,
            "bot_username": TELEGRAM_BOT_USERNAME,
            "new_leads_badge": stats.get("new_leads", 0),
            "msg": request.query_params.get("msg"),
            "err": request.query_params.get("err")
        }
    )

@router.post("/businesses/create")
async def admin_create_business(
    request: Request,
    id: str = Form(...),
    name: str = Form(...),
    category: str = Form(...),
    address: str = Form(...),
    yandex_maps_url: str = Form(...),
    two_gis_url: Optional[str] = Form(""),
    status: Optional[str] = Form("pilot")
):
    """Создание нового заведения из панели администратора."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    business_id = id.lower().strip()
    if not re.fullmatch(r"[a-z0-9_-]{2,30}", business_id):
        return RedirectResponse(
            url="/admin/businesses?err=ID+должен+содержать+от+2+до+30+латинских+букв,+цифр+или+дефисов",
            status_code=303
        )
    if get_business(business_id):
        return RedirectResponse(url="/admin/businesses?err=Заведение+с+таким+ID+уже+существует", status_code=303)

    data = {
        "id": business_id,
        "name": name.strip(),
        "category": category.strip(),
        "address": address.strip(),
        "yandex_maps_url": yandex_maps_url.strip(),
        "two_gis_url": (two_gis_url or "").strip(),
        "welcome_text": f"Поделитесь впечатлениями о визите в «{name.strip()}». Ваше мнение помогает нам становиться лучше!",
        "negative_cta": "Нам очень жаль! Напишите управляющему, чтобы мы могли сразу исправить ситуацию:",
        "positive_cta": "Спасибо за вашу оценку! Пожалуйста, поддержите нас отзывом на Яндекс.Картах ❤️",
        "status": status or "pilot"
    }
    create_business(data)
    # Обновляем статус
    update_business(business_id, data)

    logger.info(f"🏢 Создано новое заведение в админке: {business_id} ({name})")
    return RedirectResponse(url="/admin/businesses?msg=Заведение+успешно+создано!+QR-коды+готовы", status_code=303)

@router.post("/businesses/{business_id}/update")
async def admin_update_business(
    request: Request,
    business_id: str,
    name: str = Form(...),
    category: str = Form(...),
    address: str = Form(...),
    yandex_maps_url: str = Form(...),
    two_gis_url: Optional[str] = Form(""),
    status: Optional[str] = Form("pilot")
):
    """Обновление параметров заведения."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    current = get_business(business_id)
    if not current:
        return RedirectResponse(url="/admin/businesses?err=Заведение+не+найдено", status_code=303)

    data = {
        "name": name.strip(),
        "category": category.strip(),
        "address": address.strip(),
        "yandex_maps_url": yandex_maps_url.strip(),
        "two_gis_url": (two_gis_url or "").strip(),
        "welcome_text": current.get("welcome_text", ""),
        "negative_cta": current.get("negative_cta", ""),
        "positive_cta": current.get("positive_cta", ""),
        "status": status or "pilot"
    }
    update_business(business_id, data)
    return RedirectResponse(url="/admin/businesses?msg=Параметры+заведения+успешно+обновлены", status_code=303)

@router.post("/businesses/{business_id}/delete")
async def admin_delete_business(request: Request, business_id: str):
    """Удаление заведения и всех связанных данных."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    import urllib.parse
    current = get_business(business_id)
    name = current["name"] if current else business_id

    delete_business(business_id)
    logger.info(f"🗑️ Заведение удалено администратором: {business_id} ({name})")
    encoded_msg = urllib.parse.quote(f"Заведение «{name}» и связанные отзывы успешно удалены")
    return RedirectResponse(url=f"/admin/businesses?msg={encoded_msg}", status_code=303)

# --- 4. CRM заявок на 14-дневный пилот ---

@router.get("/leads", response_class=HTMLResponse)
async def admin_leads_page(request: Request):
    """Таблица лидов с быстрой сменой статусов и связью в WhatsApp."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    stats = get_global_stats()
    leads = get_recent_leads(limit=150)

    return templates.TemplateResponse(
        request=request,
        name="admin_leads.html",
        context={
            "active_page": "leads",
            "leads": leads,
            "new_leads_badge": stats.get("new_leads", 0),
            "msg": request.query_params.get("msg"),
            "err": request.query_params.get("err")
        }
    )

@router.post("/leads/{lead_id}/status")
async def admin_update_lead_status(request: Request, lead_id: int, status: str = Form(...)):
    """Изменение статуса заявки в воронке."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    update_lead_status(lead_id, status)
    return RedirectResponse(url="/admin/leads?msg=Статус+заявки+обновлен", status_code=303)

@router.post("/leads/{lead_id}/delete")
async def admin_delete_lead(request: Request, lead_id: int):
    """Удаление лида из CRM."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    delete_lead(lead_id)
    return RedirectResponse(url="/admin/leads?msg=Заявка+удалена", status_code=303)

@router.get("/leads/export")
async def admin_export_leads(request: Request):
    """Экспорт всех заявок воронки в CSV (Excel-совместимый UTF-8 с BOM)."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    leads = get_leads_for_export()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["ID", "Дата заявки", "Имя контакта", "Заведение", "Телефон", "Город", "Столов/мастеров", "Источник", "Статус"])

    status_labels = {
        "new": "Новая",
        "contacted": "В диалоге",
        "pilot_started": "Пилот запущен",
        "paid": "Оплачено",
        "rejected": "Отказ"
    }

    for l in leads:
        writer.writerow([
            l.get("id", ""),
            l.get("created_at", ""),
            l.get("name", ""),
            l.get("business_name", ""),
            l.get("phone", ""),
            l.get("city", ""),
            l.get("tables_count", ""),
            l.get("source", ""),
            status_labels.get(l.get("status"), l.get("status", ""))
        ])

    csv_data = "\ufeff" + output.getvalue()
    filename = f"geopulse_leads_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_data.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

# --- 5. Единая лента отзывов ---

@router.get("/reviews", response_class=HTMLResponse)
async def admin_reviews_page(
    request: Request,
    business: Optional[str] = "all",
    rating: Optional[str] = None
):
    """Единая лента отзывов со всех точек с фильтрацией."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    stats = get_global_stats()
    businesses = get_all_businesses()
    feedbacks = get_all_feedbacks_filtered(business_id=business, rating_filter=rating, limit=200)

    return templates.TemplateResponse(
        request=request,
        name="admin_reviews.html",
        context={
            "active_page": "reviews",
            "feedbacks": feedbacks,
            "businesses": businesses,
            "selected_business": business or "all",
            "selected_rating": rating or "",
            "new_leads_badge": stats.get("new_leads", 0),
            "msg": request.query_params.get("msg"),
            "err": request.query_params.get("err")
        }
    )

@router.get("/reviews/export")
async def admin_export_reviews(request: Request, business: Optional[str] = "all"):
    """Экспорт отзывов всех заведений в CSV (Excel-совместимый UTF-8 с BOM)."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    bid = None if (not business or business == "all") else business
    feedbacks = get_feedbacks_for_export(bid)

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["ID", "Заведение", "Дата и время", "Оценка (1-5)", "Столик", "Отзыв / Претензия", "Контакты клиента", "Статус"])

    for f in feedbacks:
        writer.writerow([
            f.get("id", ""),
            f.get("business_name", ""),
            f.get("created_at", ""),
            f.get("rating", ""),
            f.get("table_number", ""),
            (f.get("comment", "") or "").replace("\r\n", " ").replace("\n", " "),
            f.get("contact", ""),
            "Урегулировано" if f.get("status") == "resolved" else "В работе"
        ])

    csv_data = "\ufeff" + output.getvalue()
    filename = f"geopulse_all_reviews_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_data.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

# --- 6. Менеджеры Telegram ---

@router.get("/managers", response_class=HTMLResponse)
async def admin_managers_page(request: Request):
    """Управление подключенными управляющими в Telegram."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    stats = get_global_stats()
    managers = get_all_managers()
    businesses = get_all_businesses()

    return templates.TemplateResponse(
        request=request,
        name="admin_managers.html",
        context={
            "active_page": "managers",
            "managers": managers,
            "businesses": businesses,
            "bot_username": TELEGRAM_BOT_USERNAME,
            "new_leads_badge": stats.get("new_leads", 0),
            "msg": request.query_params.get("msg"),
            "err": request.query_params.get("err")
        }
    )

@router.post("/managers/add")
async def admin_add_manager(
    request: Request,
    business_id: str = Form(...),
    telegram_chat_id: str = Form(...)
):
    """Прямая привязка Telegram-аккаунта к заведению из админки."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)

    clean_biz = business_id.lower().strip()
    clean_chat = telegram_chat_id.strip()

    if not get_business(clean_biz):
        return RedirectResponse(url="/admin/managers?err=Заведение+не+найдено", status_code=303)

    link_telegram_owner(clean_biz, clean_chat)
    return RedirectResponse(url="/admin/managers?msg=Менеджер+успешно+привязан+к+заведению!", status_code=303)

@router.post("/managers/{record_id}/delete")
async def admin_unlink_manager(request: Request, record_id: int):
    """Отвязка управляющего от заведения."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    unlink_telegram_owner_by_id(record_id)
    return RedirectResponse(url="/admin/managers?msg=Менеджер+успешно+отвязан", status_code=303)
