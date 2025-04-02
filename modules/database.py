#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Database module
==============
Provides functions to interact with PostgreSQL via a connection pool.
"""

import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
from datetime import datetime

logger = logging.getLogger(__name__)

# Чтение переменных окружения (можно хранить их в modules.config)
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "mydb")
DB_USER = os.getenv("DB_USER", "admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "password")

# Глобальный пул соединений
_db_pool = None

def get_db_pool():
    """
    Возвращает объект пула соединений (SimpleConnectionPool).
    Если пула ещё нет, создаётся новый.
    """
    global _db_pool
    if _db_pool is None:
        _db_pool = SimpleConnectionPool(
            minconn=1,
            maxconn=20,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME
        )
    return _db_pool

def get_unposted_news_groups():
    """
    Извлекает все записи из таблицы fetched_events, где isPosted IS NULL.
    Исключает новости, которые уже были обработаны (isPosted = FALSE).
    Группирует их по groupId, сортирует по eventDate и оставляет последние 3 записи для каждой группы.
    Возвращает словарь вида: { groupId: [news, ...] }
    """
    groups = {}
    query = """
        SELECT DISTINCT ON ("groupId") "groupId"
          FROM fetched_events
         WHERE "isPosted" IS NULL
         ORDER BY "groupId";
    """
    try:
        pool = get_db_pool()
        with pool.getconn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Сначала получаем список уникальных groupId, где есть записи с isPosted IS NULL
                cur.execute(query)
                group_ids = [row["groupId"] for row in cur.fetchall()]
                
                # Проверяем, есть ли у каждой группы записи с isPosted = FALSE
                # Если есть, значит группа уже обрабатывалась и ожидает резолюции
                filtered_group_ids = []
                for group_id in group_ids:
                    cur.execute("""
                        SELECT COUNT(*) as count
                          FROM fetched_events
                         WHERE "groupId" = %s AND "isPosted" = FALSE
                    """, (group_id,))
                    count = cur.fetchone()["count"]
                    if count == 0:  # Если нет записей с isPosted = FALSE, добавляем группу
                        filtered_group_ids.append(group_id)
                
                # Для каждой отфильтрованной группы получаем записи
                for group_id in filtered_group_ids:
                    cur.execute("""
                        SELECT event_id, "groupId", "eventDate", report, "isPosted"
                          FROM fetched_events
                         WHERE "groupId" = %s AND "isPosted" IS NULL
                         ORDER BY "eventDate" ASC
                    """, (group_id,))
                    news_list = cur.fetchall()
                    # Оставляем последние 3 записи
                    news_list.sort(key=lambda r: r.get("eventDate", ""))
                    groups[group_id] = news_list[-3:]
                    
                    # Отмечаем новости как обработанные (isPosted = False)
                    # Это предотвратит повторную обработку новостей, которые уже были отправлены в Telegram
                    mark_news_as_processed(group_id)
        
        pool.putconn(conn)
        logger.debug("Группировка новостей завершена: %s", groups)
        return groups

    except Exception as e:
        logger.error("Ошибка получения новостей из БД: %s", e)
        return {}

def mark_news_as_processed(group_id):
    """
    Отмечает новости группы как обработанные, устанавливая поле isPosted в False.
    Это предотвращает повторную обработку новостей, которые уже были отправлены в Telegram,
    но еще не получили окончательную резолюцию (Опубликовать).
    """
    query = """
        UPDATE fetched_events
           SET "isPosted" = FALSE
         WHERE "groupId" = %s
           AND "isPosted" IS NULL;
    """
    try:
        pool = get_db_pool()
        with pool.getconn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (group_id,))
            conn.commit()
        pool.putconn(conn)
        logger.info("Новости группы %s отмечены как обработанные.", group_id)
    except Exception as e:
        logger.error("Ошибка при отметке новостей группы %s как обработанных: %s", group_id, e)

def update_news_status_by_group(group_id, status):
    """
    Обновляет поле isPosted для всех записей с заданным group_id.
    status: True (опубликовать) или False (уже отмечено как обработанное)
    """
    query = """
        UPDATE fetched_events
           SET "isPosted" = %s
         WHERE "groupId" = %s
           AND ("isPosted" IS NULL OR "isPosted" = FALSE);
    """
    try:
        pool = get_db_pool()
        with pool.getconn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (status, group_id))
            conn.commit()
        pool.putconn(conn)
        logger.info("Статус новостей группы %s обновлён на %s.", group_id, status)
    except Exception as e:
        logger.error("Ошибка обновления статуса группы %s: %s", group_id, e)

def schedule_post(group_id, scheduled_time, title, body, image_url):
    """
    Сохраняет информацию о запланированной публикации в БД.
    Предполагается, что есть таблица scheduled_posts:
    
    CREATE TABLE IF NOT EXISTS scheduled_posts (
        id SERIAL PRIMARY KEY,
        group_id VARCHAR(50),
        scheduled_time TIMESTAMP,
        title TEXT,
        body TEXT,
        image_url TEXT,
        is_posted BOOLEAN DEFAULT false
    );
    """
    query = """
        INSERT INTO scheduled_posts (group_id, scheduled_time, title, body, image_url, is_posted)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    try:
        pool = get_db_pool()
        with pool.getconn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (group_id, scheduled_time, title, body, image_url, False))
            conn.commit()
        pool.putconn(conn)
        logger.info("Публикация для группы %s запланирована на %s.", group_id, scheduled_time)
    except Exception as e:
        logger.error("Ошибка при планировании публикации группы %s: %s", group_id, e)

def get_scheduled_posts():
    """
    Возвращает список несостоявшихся (неопубликованных) запланированных публикаций из таблицы scheduled_posts,
    где is_posted = false. Сортирует по времени (самые ранние сначала).
    """
    query = """
        SELECT id, group_id, scheduled_time, title, body, image_url, is_posted
          FROM scheduled_posts
         WHERE is_posted = false
         ORDER BY scheduled_time ASC;
    """
    try:
        pool = get_db_pool()
        with pool.getconn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query)
                rows = cur.fetchall()
        pool.putconn(conn)
        return rows
    except Exception as e:
        logger.error("Ошибка при получении запланированных публикаций: %s", e)
        return []

def update_post_status(group_id, posted):
    """
    Обновляет статус is_posted для запланированной публикации в таблице scheduled_posts.
    """
    query = """
        UPDATE scheduled_posts
           SET is_posted = %s
         WHERE group_id = %s
           AND is_posted = false;
    """
    try:
        pool = get_db_pool()
        with pool.getconn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (posted, group_id))
            conn.commit()
        pool.putconn(conn)
        logger.info("Статус запланированной публикации для группы %s изменён на %s", group_id, posted)
    except Exception as e:
        logger.error("Ошибка при обновлении статуса публикации %s: %s", group_id, e)
