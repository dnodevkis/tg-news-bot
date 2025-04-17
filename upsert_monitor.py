#!/usr/bin/env python3
import os
import time
import json
import datetime
import logging
import psycopg2
from psycopg2.extras import execute_values
import re

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
# Интервал опроса (300 секунд = 5 минут)
POLL_INTERVAL = 300

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

def parse_date(date_str):
    """
    Парсит строку даты в формате ISO, обрабатывая различные варианты микросекунд.
    """
    try:
        # Стандартный парсинг ISO формата
        return datetime.datetime.fromisoformat(date_str)
    except ValueError:
        # Если стандартный парсинг не сработал, пробуем обработать нестандартный формат
        # Регулярное выражение для извлечения компонентов даты
        pattern = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d+)'
        match = re.match(pattern, date_str)
        if match:
            base_date_str = match.group(1)
            microseconds_str = match.group(2)
            # Дополняем микросекунды до 6 цифр
            microseconds_str = microseconds_str.ljust(6, '0')
            # Собираем дату заново
            full_date_str = f"{base_date_str}.{microseconds_str}"
            return datetime.datetime.fromisoformat(full_date_str)
        else:
            # Если не удалось распарсить, выбрасываем исключение
            raise ValueError(f"Невозможно распарсить дату: {date_str}")

def upsert_records(conn, records):
    values = []
    for rec in records:
        # Предполагается, что в файле ключ записи называется "id"
        event_id = str(rec.get("id"))
        groupId = rec.get("groupId")
        eventDate_str = rec.get("eventDate")
        try:
            # Используем улучшенную функцию парсинга даты
            eventDate = parse_date(eventDate_str)
        except Exception as e:
            logger.error("Ошибка преобразования даты '%s': %s", eventDate_str, e)
            continue
        report = rec.get("report")
        isPosted = rec.get("isPosted")
        values.append((event_id, groupId, eventDate, report, isPosted))
    
    if values:
        try:
            sql = """
                INSERT INTO fetched_events (event_id, "groupId", "eventDate", report, "isPosted")
                VALUES %s
                ON CONFLICT (event_id) DO NOTHING;
            """
            with conn.cursor() as cur:
                execute_values(cur, sql, values)
            conn.commit()
            logger.info("Upsert выполнен для %d записей.", len(values))
        except psycopg2.OperationalError as e:
            logger.error("Ошибка базы данных при вставке: %s", e)
            # Возвращаем False, чтобы сигнализировать о необходимости переподключения
            return False
    else:
        logger.info("Нет записей для обработки.")
    
    return True

def process_file(conn, filepath):
    logger.info("Начало обработки файла: %s", filepath)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        logger.debug("Длина файла %s: %d символов", filepath, len(content))
        # Можно временно вывести первые 300 символов для отладки:
        logger.debug("Начало содержимого файла: %s", content[:300] + "..." if len(content) > 300 else content)
        
        # Проверка на пустой файл
        if not content.strip():
            logger.error("Файл %s пуст или содержит только пробельные символы.", filepath)
            try:
                os.remove(filepath)
                logger.info("Пустой файл %s удалён.", filepath)
            except Exception as e:
                logger.error("Ошибка удаления пустого файла %s: %s", filepath, e)
            return True
        
        data = json.loads(content)
        if isinstance(data, list):
            logger.debug("Найдено %d записей в файле.", len(data))
            # Обрабатываем записи и проверяем результат
            if not upsert_records(conn, data):
                # Если вставка не удалась из-за проблем с соединением, не удаляем файл
                return False
            
            try:
                os.remove(filepath)
                logger.info("Файл %s успешно обработан и удалён.", filepath)
            except Exception as e:
                logger.error("Ошибка удаления файла %s: %s", filepath, e)
        else:
            logger.error("Файл %s имеет неверный формат (ожидается список записей).", filepath)
    except json.JSONDecodeError as e:
        logger.error("Ошибка декодирования JSON в файле %s: %s", filepath, e)
        try:
            # Перемещаем проблемный файл в подкаталог для дальнейшего анализа
            error_dir = os.path.join(MONITOR_DIR, "errors")
            os.makedirs(error_dir, exist_ok=True)
            error_file = os.path.join(error_dir, os.path.basename(filepath))
            os.rename(filepath, error_file)
            logger.info("Проблемный файл %s перемещен в %s", filepath, error_file)
        except Exception as move_error:
            logger.error("Не удалось переместить проблемный файл: %s", move_error)
    except Exception as e:
        logger.error("Ошибка обработки файла %s: %s", filepath, e)
        import traceback
        logger.error("Трассировка: %s", traceback.format_exc())
        return False
    
    return True

def get_db_connection():
    """Создает и возвращает соединение с базой данных."""
    try:
        logger.info("Подключение к базе данных %s:%s/%s...", DB_HOST, DB_PORT, DB_NAME)
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        # Проверка соединения
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            result = cur.fetchone()
        logger.info("Подключение к БД успешно установлено.")
        return conn
    except Exception as e:
        logger.error("Ошибка подключения к БД: %s", e)
        return None

def main():
    while True:
        # Устанавливаем соединение с базой данных
        conn = get_db_connection()
        if not conn:
            logger.error("Не удалось подключиться к базе данных. Повторная попытка через %d секунд.", POLL_INTERVAL)
            time.sleep(POLL_INTERVAL)
            continue
        
        try:
            create_table(conn)
            logger.info("Начало мониторинга каталога: %s", MONITOR_DIR)
            
            # Создаем каталог, если он не существует
            os.makedirs(MONITOR_DIR, exist_ok=True)
            
            # Обрабатываем файлы
            connection_valid = True
            if os.path.exists(MONITOR_DIR):
                files = os.listdir(MONITOR_DIR)
                if files:
                    logger.debug("Найдено файлов для обработки: %s", files)
                else:
                    logger.debug("В каталоге %s файлов не найдено.", MONITOR_DIR)
                
                for filename in files:
                    # Пропускаем подкаталоги и не-JSON файлы
                    filepath = os.path.join(MONITOR_DIR, filename)
                    if os.path.isdir(filepath) or not filename.endswith(".json"):
                        continue
                    
                    # Обрабатываем файл
                    if not process_file(conn, filepath):
                        connection_valid = False
                        break
            else:
                logger.error("Каталог %s не найден.", MONITOR_DIR)
            
            # Если соединение стало недействительным, закрываем его и начинаем заново
            if not connection_valid:
                logger.warning("Соединение с БД потеряно. Переподключение...")
                try:
                    conn.close()
                except:
                    pass
                continue
            
            logger.debug("Ожидание %d секунд перед следующим опросом...", POLL_INTERVAL)
            time.sleep(POLL_INTERVAL)
            
        except Exception as e:
            logger.error("Ошибка в основном цикле: %s", e)
            import traceback
            logger.error("Трассировка: %s", traceback.format_exc())
        finally:
            # Закрываем соединение в любом случае
            try:
                conn.close()
            except:
                pass

if __name__ == '__main__':
    main()
