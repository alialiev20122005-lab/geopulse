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
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
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

        # 4. Таблица заявок на 14-дневный пилот (Лиды)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            business_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            city TEXT DEFAULT '',
            tables_count TEXT DEFAULT '',
            source TEXT DEFAULT 'web',
            status TEXT DEFAULT 'new',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Миграция: столбец status для businesses
        try:
            cursor.execute("ALTER TABLE businesses ADD COLUMN status TEXT DEFAULT 'pilot';")
        except sqlite3.OperationalError:
            pass

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
        data_with_status = {**data, "status": data.get("status", "pilot")}
        cursor.execute("""
        INSERT INTO businesses (id, name, category, address, yandex_maps_url, two_gis_url, welcome_text, negative_cta, positive_cta, status)
        VALUES (:id, :name, :category, :address, :yandex_maps_url, :two_gis_url, :welcome_text, :negative_cta, :positive_cta, :status);
        """, data_with_status)
        conn.commit()
        return data["id"]

def get_businesses_by_chat_id(telegram_chat_id: str) -> List[Dict[str, Any]]:
    """Получение заведений, привязанных к конкретному Telegram-пользователю."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT b.* FROM businesses b
        JOIN business_owners bo ON b.id = bo.business_id
        WHERE bo.telegram_chat_id = ?
        ORDER BY b.created_at DESC;
        """, (str(telegram_chat_id).strip(),))
        return [dict(r) for r in cursor.fetchall()]

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

def create_lead(name: str, business_name: str, phone: str, city: str = "", tables_count: str = "", source: str = "web") -> int:
    """Сохранение новой заявки на 14-дневный бесплатный пилот."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO leads (name, business_name, phone, city, tables_count, source)
        VALUES (?, ?, ?, ?, ?, ?);
        """, (name.strip(), business_name.strip(), phone.strip(), city.strip(), str(tables_count).strip(), source.strip()))
        conn.commit()
        return cursor.lastrowid

def get_recent_leads(limit: int = 20) -> List[Dict[str, Any]]:
    """Получение последних заявок на пилот."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM leads ORDER BY created_at DESC LIMIT ?;", (limit,))
        return [dict(r) for r in cursor.fetchall()]

# --- Административные функции платформы ---

def get_global_stats() -> Dict[str, Any]:
    """Сводная глобальная статистика по всей платформе для админ-панели."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Заведения
        cursor.execute("SELECT COUNT(*) FROM businesses;")
        total_businesses = cursor.fetchone()[0]
        
        # Отзывы
        cursor.execute("""
        SELECT 
            COUNT(*) as total_feedbacks,
            SUM(CASE WHEN rating <= 3 THEN 1 ELSE 0 END) as negative_count,
            SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END) as positive_count,
            AVG(rating) as avg_rating
        FROM feedbacks;
        """)
        fb_stats = dict(cursor.fetchone())
        
        # Лиды
        cursor.execute("SELECT COUNT(*) FROM leads;")
        total_leads = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM leads WHERE status = 'new';")
        new_leads = cursor.fetchone()[0]
        
        # Привязанные менеджеры
        cursor.execute("SELECT COUNT(*) FROM business_owners;")
        total_managers = cursor.fetchone()[0]

        return {
            "total_businesses": total_businesses,
            "total_feedbacks": fb_stats["total_feedbacks"] or 0,
            "negative_feedbacks": fb_stats["negative_count"] or 0,
            "positive_feedbacks": fb_stats["positive_count"] or 0,
            "avg_rating": round(fb_stats["avg_rating"] or 5.0, 1),
            "total_leads": total_leads,
            "new_leads": new_leads,
            "total_managers": total_managers
        }

def get_all_businesses_with_meta() -> List[Dict[str, Any]]:
    """Получение всех заведений с метаданными (число менеджеров, отзывы, средний балл)."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT 
            b.*,
            (SELECT COUNT(*) FROM business_owners bo WHERE bo.business_id = b.id) as manager_count,
            (SELECT COUNT(*) FROM feedbacks f WHERE f.business_id = b.id) as feedback_count,
            (SELECT AVG(rating) FROM feedbacks f WHERE f.business_id = b.id) as avg_rating
        FROM businesses b
        ORDER BY b.created_at DESC;
        """)
        rows = cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["avg_rating"] = round(d["avg_rating"], 1) if d["avg_rating"] is not None else 5.0
            if not d.get("status"):
                d["status"] = "pilot"
            result.append(d)
        return result

def update_business(business_id: str, data: Dict[str, Any]) -> bool:
    """Обновление параметров заведения."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE businesses
        SET name = :name,
            category = :category,
            address = :address,
            yandex_maps_url = :yandex_maps_url,
            two_gis_url = :two_gis_url,
            welcome_text = :welcome_text,
            negative_cta = :negative_cta,
            positive_cta = :positive_cta,
            status = :status
        WHERE id = :id;
        """, {
            "id": business_id.lower().strip(),
            "name": data.get("name", "").strip(),
            "category": data.get("category", "").strip(),
            "address": data.get("address", "").strip(),
            "yandex_maps_url": data.get("yandex_maps_url", "").strip(),
            "two_gis_url": data.get("two_gis_url", "").strip(),
            "welcome_text": data.get("welcome_text", "").strip(),
            "negative_cta": data.get("negative_cta", "").strip(),
            "positive_cta": data.get("positive_cta", "").strip(),
            "status": data.get("status", "pilot")
        })
        conn.commit()
        return cursor.rowcount > 0

def delete_business(business_id: str) -> bool:
    """Полное удаление заведения и связанных данных."""
    with get_connection() as conn:
        cursor = conn.cursor()
        bid = business_id.lower().strip()
        cursor.execute("DELETE FROM feedbacks WHERE business_id = ?;", (bid,))
        cursor.execute("DELETE FROM business_owners WHERE business_id = ?;", (bid,))
        cursor.execute("DELETE FROM businesses WHERE id = ?;", (bid,))
        conn.commit()
        return cursor.rowcount > 0

def update_lead_status(lead_id: int, status: str) -> bool:
    """Обновление статуса заявки на пилот."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE leads SET status = ? WHERE id = ?;", (status.strip(), lead_id))
        conn.commit()
        return cursor.rowcount > 0

def delete_lead(lead_id: int) -> bool:
    """Удаление лида из CRM."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM leads WHERE id = ?;", (lead_id,))
        conn.commit()
        return cursor.rowcount > 0

def get_all_feedbacks_filtered(business_id: Optional[str] = None, rating_filter: Optional[str] = None, limit: int = 150) -> List[Dict[str, Any]]:
    """Получение отзывов со всех точек с фильтрами."""
    with get_connection() as conn:
        cursor = conn.cursor()
        query = """
        SELECT f.*, b.name as business_name, b.category as business_category
        FROM feedbacks f
        LEFT JOIN businesses b ON f.business_id = b.id
        WHERE 1=1
        """
        params = []
        if business_id and business_id != "all":
            query += " AND f.business_id = ?"
            params.append(business_id.lower().strip())
        
        if rating_filter == "negative":
            query += " AND f.rating <= 3"
        elif rating_filter == "positive":
            query += " AND f.rating >= 4"

        query += " ORDER BY f.created_at DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]

def get_all_managers() -> List[Dict[str, Any]]:
    """Список всех привязанных менеджеров в Telegram со всех заведений."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT bo.id as record_id, bo.business_id, bo.telegram_chat_id, bo.role, bo.registered_at,
               b.name as business_name, b.category as business_category
        FROM business_owners bo
        LEFT JOIN businesses b ON bo.business_id = b.id
        ORDER BY bo.registered_at DESC;
        """)
        return [dict(r) for r in cursor.fetchall()]

def unlink_telegram_owner_by_id(record_id: int) -> bool:
    """Отвязка Telegram-менеджера по ID записи."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM business_owners WHERE id = ?;", (record_id,))
        conn.commit()
        return cursor.rowcount > 0


