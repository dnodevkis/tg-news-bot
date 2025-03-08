#!/usr/bin/env python3
import os
import time
import json
import datetime
import logging
import psycopg2
from psycopg2.extras import execute_values

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.DEBUG  # Для отладки; можно переключить на INFO в продакшене
)
logger = logging.getLogger(__name__)

# Параметры подключения к БД берутся из переменных окружения
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "mydb")
DB_USER = os.getenv("DB_USER", "admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "password")

# Каталог для мониторинга файлов с записями
MONITOR_DIR = "/app/fetched-events"
# Интервал опроса (600 секунд = 10 минут)
POLL_INTERVAL = 600

def create_table(conn):
    logger.info("Проверяем наличие таблицы fetched_events...")
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fetched_events (
                event_id TEXT PRIMARY KEY,
                "groupId" TEXT NOT NULL,
                "eventDate" TIMESTAMP NOT NULL,
                report TEXT,
                "isPosted" BOOLEAN DEFAULT NULL
            );
        """)
    conn.commit()
    logger.info("Таблица fetched_events готова.")

def upsert_records(conn, records):
    values = []
    for rec in records:
        # Предполагается, что в файле ключ записи называется "id"
        event_id = str(rec.get("id"))
        groupId = rec.get("groupId")
        eventDate_str = rec.get("eventDate")
        try:
            eventDate = datetime.datetime.fromisoformat(eventDate_str)
        except Exception as e:
            logger.error("Ошибка преобразования даты '%s': %s", eventDate_str, e)
            continue
        report = rec.get("report")
        isPosted = rec.get("isPosted")
        values.append((event_id, groupId, eventDate, report, isPosted))
    
    if values:
        sql = """
            INSERT INTO fetched_events (event_id, "groupId", "eventDate", report, "isPosted")
            VALUES %s
            ON CONFLICT (event_id) DO NOTHING;
        """
        with conn.cursor() as cur:
            execute_values(cur, sql, values)
        conn.commit()
        logger.info("Upsert выполнен для %d записей.", len(values))
    else:
        logger.info("Нет записей для обработки.")

def process_file(conn, filepath):
    logger.info("Начало обработки файла: %s", filepath)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            logger.debug("Найдено %d записей в файле.", len(data))
            upsert_records(conn, data)
            try:
                os.remove(filepath)
                logger.info("Файл %s успешно обработан и удалён.", filepath)
            except Exception as e:
                logger.error("Ошибка удаления файла %s: %s", filepath, e)
        else:
            logger.error("Файл %s имеет неверный формат (ожидается список записей).", filepath)
    except Exception as e:
        logger.error("Ошибка обработки файла %s: %s", filepath, e)

def main():
    logger.info("Подключение к базе данных %s:%s/%s...", DB_HOST, DB_PORT, DB_NAME)
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
    except Exception as e:
        logger.error("Ошибка подключения к БД: %s", e)
        return

    create_table(conn)
    logger.info("Начало мониторинга каталога: %s", MONITOR_DIR)
    
    while True:
        try:
            if os.path.exists(MONITOR_DIR):
                files = os.listdir(MONITOR_DIR)
                if files:
                    logger.debug("Найдено файлов для обработки: %s", files)
                else:
                    logger.debug("В каталоге %s файлов не найдено.", MONITOR_DIR)
                for filename in files:
                    if filename.endswith(".json"):
                        filepath = os.path.join(MONITOR_DIR, filename)
                        process_file(conn, filepath)
            else:
                logger.error("Каталог %s не найден.", MONITOR_DIR)
        except Exception as e:
            logger.error("Ошибка мониторинга каталога: %s", e)
        logger.debug("Ожидание %d секунд перед следующим опросом...", POLL_INTERVAL)
        time.sleep(POLL_INTERVAL)
    
    conn.close()

if __name__ == '__main__':
    main()
