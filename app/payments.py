import uuid
import logging
from typing import Dict, Any, Optional
import httpx

from app.config import (
    YOOKASSA_SHOP_ID,
    YOOKASSA_SECRET_KEY,
    BASE_URL,
    PLANS,
    FOUNDER_PHONE,
    FOUNDER_TELEGRAM
)
from app.database import create_payment, mark_payment_succeeded, get_business

logger = logging.getLogger("GeoPulsePayments")

async def init_payment(
    business_id: str,
    plan_id: str,
    phone: str = "",
    email: str = ""
) -> Dict[str, Any]:
    """
    Инициализация платежа через ЮKassa API или создание ссылки для тестового контура / счета.
    """
    plan = PLANS.get(plan_id, PLANS["start"])
    amount = plan["price"]
    plan_name = plan["name"]

    business = get_business(business_id)
    business_name = business["name"] if business else business_id

    # 1. Если настроены боевые ключи ЮKassa в .env
    if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
        idempotence_key = str(uuid.uuid4())
        url = "https://api.yookassa.ru/v3/payments"
        
        auth = (YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)
        headers = {
            "Idempotence-Key": idempotence_key,
            "Content-Type": "application/json"
        }
        
        payload = {
            "amount": {
                "value": f"{amount}.00",
                "currency": "RUB"
            },
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": f"{BASE_URL}/pay/success?bid={business_id}&plan={plan_id}"
            },
            "description": f"GeoPulse: {plan_name} для «{business_name}»",
            "metadata": {
                "business_id": business_id,
                "plan_id": plan_id
            }
        }

        # Передаем контакт для фискального чека 54-ФЗ
        if email or phone:
            receipt_customer = {}
            if email:
                receipt_customer["email"] = email
            if phone:
                receipt_customer["phone"] = "".join(filter(str.isdigit, phone))
            
            payload["receipt"] = {
                "customer": receipt_customer,
                "items": [
                    {
                        "description": f"Предоставление доступа к ПО GeoPulse ({plan_name})",
                        "quantity": "1.00",
                        "amount": {
                            "value": f"{amount}.00",
                            "currency": "RUB"
                        },
                        "vat_code": 1, # Без НДС
                        "payment_mode": "full_payment",
                        "payment_subject": "service"
                    }
                ]
            }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json=payload, headers=headers, auth=auth)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    provider_payment_id = data["id"]
                    confirmation_url = data["confirmation"]["confirmation_url"]

                    create_payment(
                        business_id=business_id,
                        amount=amount,
                        plan=plan_id,
                        provider_payment_id=provider_payment_id,
                        payer_phone=phone,
                        payer_email=email,
                        payment_provider="yookassa"
                    )

                    return {
                        "status": "ok",
                        "provider": "yookassa",
                        "payment_url": confirmation_url,
                        "payment_id": provider_payment_id
                    }
                else:
                    logger.error(f"YooKassa API error {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Ошибка вызова ЮKassa API: {e}")

    # 2. Если боевые ключи не заданы — используем безопасный контур песочницы / инвойса
    sandbox_id = f"test_{uuid.uuid4().hex[:12]}"
    create_payment(
        business_id=business_id,
        amount=amount,
        plan=plan_id,
        provider_payment_id=sandbox_id,
        payer_phone=phone,
        payer_email=email,
        payment_provider="sandbox"
    )

    sandbox_url = f"{BASE_URL}/pay/sandbox?pid={sandbox_id}"
    return {
        "status": "ok",
        "provider": "sandbox",
        "payment_url": sandbox_url,
        "payment_id": sandbox_id,
        "amount": amount,
        "plan_name": plan_name
    }
