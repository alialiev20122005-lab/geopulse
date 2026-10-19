import os
from typing import Dict, Any

# Настройки Telegram
# Чтобы получать уведомления в Telegram:
# 1. Создайте бота в @BotFather в Telegram и скопируйте токен сюда (или в переменную окружения TELEGRAM_BOT_TOKEN)
# 2. Узнайте свой chat_id через бота @userinfobot и вставьте сюда (или в TELEGRAM_CHAT_ID)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8758602246:AAFglq5spoLAqxpo4FwmZ92qmnKjeVV7d2E")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "8919849369")
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "my_geopulse_bot")

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
