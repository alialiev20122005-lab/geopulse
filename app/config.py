import os
from typing import Dict, Any

# Автоматическое считывание .env без обязательной внешней библиотеки dotenv
def _load_env_fallback():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(base_dir, ".env"),
        os.path.join(os.getcwd(), ".env")
    ]
    for p in candidates:
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip().strip('"').strip("'")
                            if k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass

_load_env_fallback()

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Настройки Telegram
# Конфигурация подтягивается из переменных окружения (.env или настроек хостинга)
# С надежным fallback для безотказной работы на Render и локально
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8758602246:AAFglq5spoLAqxpo4FwmZ92qmnKjeVV7d2E")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "8919849369")
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "my_geopulse_bot")

# Прямые контакты основателя для клиентов и партнеров
FOUNDER_PHONE = os.getenv("FOUNDER_PHONE", "79038272579")
FOUNDER_PHONE_DISPLAY = os.getenv("FOUNDER_PHONE_DISPLAY", "+7 (903) 827-25-79")
FOUNDER_TELEGRAM = os.getenv("FOUNDER_TELEGRAM", "mbappeeed")

# Базовый домен сервиса для формирования внешних ссылок (Amvera Cloud РФ)
BASE_URL = os.getenv("BASE_URL", "https://g-wolfdag.msk1.amvera.tech").rstrip("/")

# Настройки интернет-эквайринга (ЮKassa)
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "")

# Тарифные планы платформы GeoPulse
PLANS: Dict[str, Dict[str, Any]] = {
    "start": {
        "id": "start",
        "name": "Тариф «Старт»",
        "price": 1990,
        "period": "месяц",
        "tables": "до 10 столов / мастеров"
    },
    "business": {
        "id": "business",
        "name": "Тариф «Бизнес»",
        "price": 3900,
        "period": "месяц",
        "tables": "до 30 столов / мастеров"
    },
    "network": {
        "id": "network",
        "name": "Тариф «Сеть»",
        "price": 6900,
        "period": "месяц",
        "tables": "без ограничений"
    }
}

# Настройки доступа в Админ-панель
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "geopulse2026")
SESSION_SECRET = os.getenv("SESSION_SECRET", "geopulse-super-secret-session-key-2026")

# Предустановленные карточки заведений для тестов и демонстраций
BUSINESSES: Dict[str, Dict[str, Any]] = {
    "daikon": {
        "name": "Кафе «Daikon»",
        "category": "Кафе / Ресторан",
        "address": "г. Махачкала, ул. Гамида Далгата, 35",
        "yandex_maps_url": "https://yandex.ru/maps/org/142666129662/reviews",
        "two_gis_url": "",
        "welcome_text": "Поделитесь впечатлениями о визите в кафе «Daikon». Ваше мнение помогает нам становиться лучше!",
        "negative_cta": "Нам очень жаль! Напишите управляющему, чтобы мы могли сразу исправить ситуацию:",
        "positive_cta": "Спасибо за вашу оценку! Пожалуйста, поддержите нас отзывом на Яндекс.Картах — это очень важно для нашей команды ❤️"
    },
    "picasso": {
        "name": "Парикмахерская «Picasso»",
        "category": "Салон красоты / Парикмахерская",
        "address": "г. Махачкала, ул. М. Ярагского, 106",
        "yandex_maps_url": "https://yandex.ru/maps/org/155501453855/reviews",
        "two_gis_url": "",
        "welcome_text": "Оцените качество работы мастера в парикмахерской «Picasso»",
        "negative_cta": "Мастер не услышал ваши пожелания? Расскажите управляющему — решим вопрос за 10 минут:",
        "positive_cta": "Рады, что вам понравилось! Оставьте пару приятных слов на Яндекс.Картах:"
    },
    "katoff": {
        "name": "Автосервис «Katoff»",
        "category": "Автотехцентр / Ремонт авто",
        "address": "г. Махачкала, ул. Каммаева, 39",
        "yandex_maps_url": "https://yandex.ru/maps/org/113305011221/reviews",
        "two_gis_url": "",
        "welcome_text": "Контроль качества обслуживания в автотехцентре «Katoff»",
        "negative_cta": "Есть претензии по ремонту или срокам? Сообщение уйдет лично директору:",
        "positive_cta": "Спасибо за доверие! Оцените нас на Яндекс.Картах:"
    },
    "demo": {
        "name": "Демонстрационное заведение",
        "category": "Сфера услуг",
        "address": "Тестовая локация",
        "yandex_maps_url": "https://yandex.ru/maps",
        "two_gis_url": "",
        "welcome_text": "Оцените наш сервис за 10 секунд!",
        "negative_cta": "Что пошло не так? Напишите напрямую директору:",
        "positive_cta": "Спасибо! Поделитесь впечатлением на картах:"
    }
}
