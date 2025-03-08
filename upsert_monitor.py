#!/usr/bin/env python3
import os
import time
import json
import datetime
import psycopg2
from psycopg2.extras import execute_values

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
            print(f"Ошибка преобразования даты '{eventDate_str}': {e}")
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
        print(f"Upsert выполнен для {len(values)} записей.")
    else:
        print("Нет записей для обработки.")

def process_file(conn, filepath):
    print(f"Обработка файла: {filepath}")
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            upsert_records(conn, data)
            os.remove(filepath)
            print(f"Файл {filepath} успешно обработан и удалён.")
        else:
            print(f"Файл {filepath} имеет неверный формат (ожидается список записей).")
    except Exception as e:
        print(f"Ошибка обработки файла {filepath}: {e}")

def main():
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
    except Exception as e:
        print(f"Ошибка подключения к БД: {e}")
        return

    create_table(conn)
    print(f"Начало мониторинга каталога: {MONITOR_DIR}")
    
    while True:
        try:
            if os.path.exists(MONITOR_DIR):
                files = os.listdir(MONITOR_DIR)
                for filename in files:
                    if filename.endswith(".json"):
                        filepath = os.path.join(MONITOR_DIR, filename)
                        process_file(conn, filepath)
            else:
                print(f"Каталог {MONITOR_DIR} не найден.")
        except Exception as e:
            print(f"Ошибка мониторинга: {e}")
        time.sleep(POLL_INTERVAL)
    
    conn.close()

if __name__ == '__main__':
    main()
