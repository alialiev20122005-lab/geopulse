import os
import sqlite3
import datetime
import logging
import asyncio
from typing import Optional
import httpx

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from app.database import DB_PATH

logger = logging.getLogger("GeoPulseBackup")

def get_backups_dir() -> str:
    """Определение папки для хранения локальных резервных копий."""
    if os.path.exists("/data") and os.path.isdir("/data"):
        bdir = "/data/backups"
    else:
        bdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups")
    os.makedirs(bdir, exist_ok=True)
    return bdir

def create_database_backup() -> str:
    """
    Атомарное создание резервной копии SQLite с использованием Online Backup API.
    Гарантирует 100% целостность данных без повреждений даже при активных транзакциях WAL.
    """
    bdir = get_backups_dir()
    date_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"geopulse_backup_{date_str}.db"
    backup_path = os.path.join(bdir, backup_filename)

    # 1. Атомарное онлайн-копирование средствами SQLite
    src_conn = sqlite3.connect(DB_PATH)
    dst_conn = sqlite3.connect(backup_path)
    with dst_conn:
        src_conn.backup(dst_conn, pages=100)
    dst_conn.close()
    src_conn.close()

    logger.info(f"✅ Создана резервная копия базы данных: {backup_path}")

    # 2. Ротация старых копий: оставляем последние 7 файлов
    try:
        existing = sorted([
            os.path.join(bdir, f) for f in os.listdir(bdir) 
            if f.startswith("geopulse_backup_") and f.endswith(".db")
        ])
        while len(existing) > 7:
            oldest = existing.pop(0)
            try:
                os.remove(oldest)
                logger.info(f"🗑 Удалена устаревшая резервная копия: {oldest}")
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Ошибка при ротации бэкапов: {e}")

    return backup_path

async def send_backup_to_telegram(backup_path: str, chat_id: Optional[str] = None) -> bool:
    """Отправка файла резервной копии в Telegram администратору через sendDocument."""
    target_chat = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target_chat:
        logger.warning("Бэкап в Telegram не отправлен: отсутствует TELEGRAM_BOT_TOKEN или CHAT_ID.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    filename = os.path.basename(backup_path)
    file_size_kb = round(os.path.getsize(backup_path) / 1024, 1)

    caption = (
        f"💾 <b>Автоматический бэкап базы данных GeoPulse</b>\n\n"
        f"📅 Дата: <code>{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
        f"📦 Размер: <b>{file_size_kb} КБ</b>\n"
        f"🛡 Хранилище: <code>/data/geopulse.db</code>\n\n"
        f"<i>Файл надежно сохранен в облаке Telegram. При необходимости его можно развернуть на сервере за 10 секунд.</i>"
    )

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            with open(backup_path, "rb") as f:
                files = {"document": (filename, f, "application/x-sqlite3")}
                data = {"chat_id": target_chat, "caption": caption, "parse_mode": "HTML"}
                resp = await client.post(url, data=data, files=files)
                if resp.status_code == 200:
                    logger.info("✅ Бэкап базы успешно доставлен в Telegram.")
                    return True
                else:
                    logger.error(f"❌ Ошибка отправки бэкапа в Telegram: {resp.text}")
                    return False
    except Exception as e:
        logger.error(f"❌ Исключение при отправке бэкапа в Telegram: {e}")
        return False

async def backup_scheduler_loop():
    """
    Фоновый планировщик: создает и отправляет резервную копию базы данных
    каждые 24 часа (при старте и затем каждые сутки).
    """
    # Ждем 60 секунд после старта сервера, чтобы не нагружать cold-start
    await asyncio.sleep(60)
    
    while True:
        try:
            logger.info("⏰ Запуск планового создания ночного бэкапа базы данных...")
            backup_path = create_database_backup()
            await send_backup_to_telegram(backup_path)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Ошибка в фоновом планировщике бэкапов: {e}")

        # Спим 24 часа до следующего планового создания
        await asyncio.sleep(24 * 3600)
