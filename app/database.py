import sqlite3
from datetime import datetime
from typing import Dict, Any, List, Optional
import os

# В Amvera постоянное хранилище подключается в папку /data.
# Если папка существует, база сохраняется навсегда между перезапусками контейнера.
if os.path.exists("/data") and os.path.isdir("/data"):
    DB_PATH = "/data/geopulse.db"
else:
    DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "geopulse.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Инициализация таблиц базы данных и начальных данных."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Таблица заведений
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS businesses (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            address TEXT NOT NULL,
            yandex_maps_url TEXT NOT NULL,
            two_gis_url TEXT DEFAULT '',
            welcome_text TEXT,
            negative_cta TEXT,
            positive_cta TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 2. Таблица привязанных управляющих (Telegram Chat IDs)
        # Одно заведение могут контролировать несколько менеджеров
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS business_owners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id TEXT NOT NULL,
            telegram_chat_id TEXT NOT NULL,
            role TEXT DEFAULT 'owner',
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (business_id) REFERENCES businesses (id),
            UNIQUE(business_id, telegram_chat_id)
        );
        """)

        # 3. Таблица отзывов и жалоб
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS feedbacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id TEXT NOT NULL,
            rating INTEGER NOT NULL,
            comment TEXT,
            contact TEXT,
            table_number TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (business_id) REFERENCES businesses (id)
        );
        """)

        conn.commit()

def seed_initial_data(default_chat_id: str = ""):
    """Предзаполнение демонстрационными заведениями при первом запуске."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM businesses;")
        if cursor.fetchone()[0] == 0:
            initial_businesses = [
                (
                    "daikon",
                    "Кафе «Daikon»",
                    "Кафе / Ресторан",
                    "г. Махачкала, ул. Гамида Далгата, 35",
                    "https://yandex.ru/maps/org/142666129662/reviews",
                    "",
                    "Поделитесь впечатлениями о визите в кафе «Daikon». Ваше мнение помогает нам становиться лучше!",
                    "Нам очень жаль! Напишите управляющему, чтобы мы могли сразу исправить ситуацию:",
                    "Спасибо за вашу оценку! Пожалуйста, поддержите нас отзывом на Яндекс.Картах — это очень важно для нашей команды ❤️"
                ),
                (
                    "picasso",
                    "Парикмахерская «Picasso»",
                    "Салон красоты / Парикмахерская",
                    "г. Махачкала, ул. М. Ярагского, 106",
                    "https://yandex.ru/maps/org/155501453855/reviews",
                    "",
                    "Оцените качество работы мастера в парикмахерской «Picasso»",
                    "Мастер не услышал ваши пожелания? Расскажите управляющему — решим вопрос за 10 минут:",
                    "Рады, что вам понравилось! Оставьте пару приятных слов на Яндекс.Картах:"
                ),
                (
                    "katoff",
                    "Автосервис «Katoff»",
                    "Автотехцентр / Ремонт авто",
                    "г. Махачкала, ул. Каммаева, 39",
                    "https://yandex.ru/maps/org/113305011221/reviews",
                    "",
                    "Контроль качества обслуживания в автотехцентре «Katoff»",
                    "Есть претензии по ремонту или срокам? Сообщение уйдет лично директору:",
                    "Спасибо за доверие! Оцените нас на Яндекс.Картах:"
                )
            ]

            cursor.executemany("""
            INSERT INTO businesses (id, name, category, address, yandex_maps_url, two_gis_url, welcome_text, negative_cta, positive_cta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, initial_businesses)

            # Если передан Chat ID пользователя, привязываем его к Daikon по умолчанию
            if default_chat_id:
                cursor.execute("""
                INSERT OR IGNORE INTO business_owners (business_id, telegram_chat_id)
                VALUES ('daikon', ?);
                """, (default_chat_id,))

            conn.commit()

# --- Функции работы с базой данных ---

def get_business(business_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM businesses WHERE id = ?;", (business_id.lower(),))
        row = cursor.fetchone()
        return dict(row) if row else None

def get_all_businesses() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM businesses ORDER BY created_at DESC;")
        return [dict(row) for row in cursor.fetchall()]

def create_business(data: Dict[str, Any]) -> str:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO businesses (id, name, category, address, yandex_maps_url, two_gis_url, welcome_text, negative_cta, positive_cta)
        VALUES (:id, :name, :category, :address, :yandex_maps_url, :two_gis_url, :welcome_text, :negative_cta, :positive_cta);
        """, data)
        conn.commit()
        return data["id"]

def link_telegram_owner(business_id: str, telegram_chat_id: str) -> bool:
    with get_connection() as conn:
        cursor = conn.cursor()
        # Проверяем, существует ли заведение
        cursor.execute("SELECT id FROM businesses WHERE id = ?;", (business_id.lower(),))
        if not cursor.fetchone():
            return False
            
        cursor.execute("""
        INSERT OR IGNORE INTO business_owners (business_id, telegram_chat_id)
        VALUES (?, ?);
        """, (business_id.lower(), telegram_chat_id))
        conn.commit()
        return True

def get_business_chat_ids(business_id: str) -> List[str]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_chat_id FROM business_owners WHERE business_id = ?;", (business_id.lower(),))
        return [row["telegram_chat_id"] for row in cursor.fetchall()]

def add_feedback(business_id: str, rating: int, comment: str, contact: str, table_number: str = "") -> int:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO feedbacks (business_id, rating, comment, contact, table_number)
        VALUES (?, ?, ?, ?, ?);
        """, (business_id.lower(), rating, comment, contact, table_number))
        conn.commit()
        return cursor.lastrowid

def get_business_stats(business_id: str) -> Dict[str, Any]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT 
            COUNT(*) as total_count,
            AVG(rating) as avg_rating,
            SUM(CASE WHEN rating <= 3 THEN 1 ELSE 0 END) as negative_count,
            SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END) as positive_count
        FROM feedbacks 
        WHERE business_id = ?;
        """, (business_id.lower(),))
        stats = dict(cursor.fetchone())

        cursor.execute("""
        SELECT * FROM feedbacks 
        WHERE business_id = ? 
        ORDER BY created_at DESC 
        LIMIT 20;
        """, (business_id.lower(),))
        recent_feedbacks = [dict(r) for r in cursor.fetchall()]

        return {
            "stats": stats,
            "feedbacks": recent_feedbacks
        }
