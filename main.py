#!/usr/bin/env python3
"""
Telegram Bot for News Processing and Publication
===============================================

This bot fetches news from a database, processes them using Claude AI for content generation,
creates illustrations with DALL-E, and allows an admin to review and publish to a channel.

Features:
- Automatic news processing and editorial review
- Image generation for each news item
- Scheduled posting capabilities
- Admin approval workflow
- Error recovery and rate limiting
"""

import os
import json
import logging
import logging.handlers
import time
import requests
import openai
import psycopg2
import random
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
from datetime import datetime, timedelta
from functools import wraps
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, ParseMode
from telegram.ext import (
    Updater, CommandHandler, CallbackQueryHandler, 
    CallbackContext, JobQueue, ConversationHandler,
    MessageHandler, Filters
)
from dotenv import load_dotenv

# --- Module imports ---
from modules.database import (
    get_db_pool, get_unposted_news_groups, 
    update_news_status_by_group, schedule_post,
    get_scheduled_posts, update_post_status
)
from modules.api_clients import (
    call_editor_api, generate_image
)
from modules.config import (
    validate_environment, SYSTEM_PROMPT,
    BOT_TOKEN, ADMIN_ID, CHANNEL_ID,
    CLAUDE_API_KEY, CLAUDE_MODEL, OPENAI_API_KEY
)
from modules.utils import retry, rate_limited

# --- Logging setup ---
def setup_logging():
    """Configure application logging with rotation and formatting."""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_format)
    
    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        'bot.log', maxBytes=10*1024*1024, backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)
    file_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s'
    )
    file_handler.setFormatter(file_format)
    
    # Add handlers
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger

# Initialize logger
logger = setup_logging()

# --- States for conversation handlers ---
SCHEDULE_TIME, SCHEDULE_CONFIRM = range(2)

# --- Helpers and decorators ---
def admin_only(func):
    """Decorator to restrict command access to admin only."""
    @wraps(func)
    def wrapped(update, context, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            update.message.reply_text("Извините, эта команда доступна только администратору.")
            return
        return func(update, context, *args, **kwargs)
    return wrapped

def sanitize_input(text):
    """Sanitize user input to prevent injection attacks."""
    if not text:
        return ""
    # Remove potentially dangerous characters
    sanitized = text.replace(';', '').replace('--', '')
    # Limit length
    return sanitized[:1000]

# --- Bot handlers ---
def start(update: Update, context: CallbackContext):
    """Handler for /start command."""
    commands_info = """
Доступные команды:
/checknews - проверить новые записи в базе данных
/status - показать статус бота и количество записей
/scheduled - показать запланированные публикации
/help - показать эту справку
"""
    update.message.reply_text(f"Привет! Я бот для обработки и публикации новостей.{commands_info}")

@admin_only
def check_news(update: Update, context: CallbackContext):
    """Handler for /checknews command - processes unposted news."""
    update.message.reply_text("Начинаю проверку базы данных на наличие новых записей...")
    groups = get_unposted_news_groups()
    
    if not groups:
        update.message.reply_text("Новых записей не найдено.")
        return
    
    update.message.reply_text(f"Найдено {len(groups)} групп новостей для обработки.")
    process_news(groups, context, send_loading_msg=True, update_obj=update)

@admin_only
def show_status(update: Update, context: CallbackContext):
    """Handler for /status command - shows bot status and stats."""
    try:
        with get_db_pool().getconn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Count total records
                cur.execute("SELECT COUNT(*) as total FROM fetched_events")
                total_count = cur.fetchone()['total']
                
                # Count unposted records
                cur.execute("SELECT COUNT(*) as unposted FROM fetched_events WHERE \"isPosted\" IS NULL")
                unposted_count = cur.fetchone()['unposted']
                
                # Count posted records
                cur.execute("SELECT COUNT(*) as posted FROM fetched_events WHERE \"isPosted\" = true")
                posted_count = cur.fetchone()['posted']
                
                # Count rejected records
                cur.execute("SELECT COUNT(*) as rejected FROM fetched_events WHERE \"isPosted\" = false")
                rejected_count = cur.fetchone()['rejected']
                
                # Get last successful post time
                cur.execute("""
                    SELECT MAX("eventDate") as last_post 
                    FROM fetched_events 
                    WHERE "isPosted" = true
                """)
                last_post = cur.fetchone()['last_post']
                
            get_db_pool().putconn(conn)
            
        # Format message
        status_message = f"""
📊 *Статистика бота*
Всего записей: {total_count}
✅ Опубликовано: {posted_count}
❌ Отклонено: {rejected_count}
⏳ Ожидает обработки: {unposted_count}

🕒 Последняя публикация: {last_post.strftime('%d.%m.%Y %H:%M') if last_post else 'Нет данных'}
        """
        
        update.message.reply_text(status_message, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {e}")
        update.message.reply_text(f"Ошибка при получении статистики: {str(e)}")

@admin_only
def show_scheduled(update: Update, context: CallbackContext):
    """Handler for /scheduled command - shows scheduled posts."""
    scheduled_posts = get_scheduled_posts()
    
    if not scheduled_posts:
        update.message.reply_text("Нет запланированных публикаций.")
        return
    
    message = "*Запланированные публикации:*\n\n"
    for post in scheduled_posts:
        post_time = post['scheduled_time'].strftime('%d.%m.%Y %H:%M')
        group_id = post['group_id']
        title = post.get('title', 'Без заголовка')
        message += f"🕒 {post_time} - {title} (ID: {group_id})\n"
        
    update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

@admin_only
def help_command(update: Update, context: CallbackContext):
    """Handler for /help command."""
    help_text = """
*Команды бота:*
/checknews - проверить новые записи в базе данных
/status - показать статистику бота
/scheduled - показать запланированные публикации
/help - показать эту справку

*Функции администратора:*
- Просмотр и модерация новостей
- Генерация нового текста или изображения
- Публикация в канал немедленно или по расписанию
- Отклонение неподходящих новостей

*Советы:*
- Используйте кнопку "Запланировать" для отложенной публикации
- Вы можете запросить другой вариант текста или изображения
- Настройка CRON для автоматической проверки новостей включена
    """
    update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

def cancel_conversation(update: Update, context: CallbackContext):
    """Cancel current conversation."""
    update.message.reply_text("Действие отменено.")
    return ConversationHandler.END

# --- Scheduling conversation ---
def start_scheduling(update: Update, context: CallbackContext):
    """Start the scheduling conversation."""
    query = update.callback_query
    query.answer()
    
    # Extract group_id from the callback data
    _, group_id = query.data.split(":", 1)
    context.user_data['scheduling_group_id'] = group_id
    
    # Get current datetime and generate suggested times
    now = datetime.now()
    suggested_times = []
    
    # Morning (9-11 AM)
    morning = now.replace(hour=9, minute=0, second=0)
    if morning < now:
        morning = morning + timedelta(days=1)
    suggested_times.append(morning)
    
    # Lunch (12-2 PM)
    lunch = now.replace(hour=13, minute=0, second=0)
    if lunch < now:
        lunch = lunch + timedelta(days=1)
    suggested_times.append(lunch)
    
    # Evening (6-8 PM)
    evening = now.replace(hour=19, minute=0, second=0)
    if evening < now:
        evening = evening + timedelta(days=1)
    suggested_times.append(evening)
    
    # Format buttons for suggested times
    keyboard = []
    for time_option in suggested_times:
        time_str = time_option.strftime('%d.%m.%Y %H:%M')
        keyboard.append([InlineKeyboardButton(time_str, callback_data=f"time:{time_str}")])
    
    keyboard.append([InlineKeyboardButton("Отмена", callback_data="cancel_scheduling")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    query.edit_message_reply_markup(reply_markup=reply_markup)
    return SCHEDULE_TIME

def select_time(update: Update, context: CallbackContext):
    """Handle time selection for scheduling."""
    query = update.callback_query
    query.answer()
    
    if query.data == "cancel_scheduling":
        query.edit_message_reply_markup(None)
        return ConversationHandler.END
    
    # Extract selected time
    _, time_str = query.data.split(":", 1)
    context.user_data['scheduled_time'] = time_str
    
    # Confirm scheduling
    keyboard = [
        [
            InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_schedule"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel_scheduling")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    query.edit_message_text(
        f"Запланировать публикацию на {time_str}?",
        reply_markup=reply_markup
    )
    return SCHEDULE_CONFIRM

def confirm_schedule(update: Update, context: CallbackContext):
    """Confirm scheduling and save to database."""
    query = update.callback_query
    query.answer()
    
    group_id = context.user_data.get('scheduling_group_id')
    time_str = context.user_data.get('scheduled_time')
    
    if not group_id or not time_str:
        query.edit_message_text("Ошибка планирования. Пожалуйста, попробуйте снова.")
        return ConversationHandler.END
    
    try:
        # Parse time string
        scheduled_time = datetime.strptime(time_str, '%d.%m.%Y %H:%M')
        
        # Get post data
        group_data = context.chat_data.get(f"group_{group_id}")
        if not group_data:
            query.edit_message_text("Данные поста не найдены. Пожалуйста, попробуйте снова.")
            return ConversationHandler.END
        
        post = group_data["editor_result"].get("post", {})
        title = post.get("title", "Без заголовка")
        body = post.get("body", "")
        image_url = group_data.get("image_url")
        
        # Schedule post
        schedule_post(group_id, scheduled_time, title, body, image_url)
        
        # Mark as posted in database
        update_news_status_by_group(group_id, True)
        
        # Schedule job
        job_context = {
            'chat_id': CHANNEL_ID,
            'group_id': group_id,
            'title': title,
            'body': body,
            'image_url': image_url
        }
        context.job_queue.run_once(
            post_scheduled_content, 
            scheduled_time,
            context=job_context,
            name=f"scheduled_{group_id}"
        )
        
        query.edit_message_text(
            f"✅ Публикация запланирована на {time_str}.\n\n{title}"
        )
        
        # Cleanup
        context.user_data.clear()
        
    except Exception as e:
        logger.error(f"Ошибка при планировании публикации: {e}")
        query.edit_message_text(f"Ошибка при планировании публикации: {str(e)}")
    
    return ConversationHandler.END

def post_scheduled_content(context: CallbackContext):
    """Job to post scheduled content to channel."""
    job = context.job
    data = job.context
    
    try:
        # Post to channel
        if data.get('image_url'):
            context.bot.send_photo(
                chat_id=data['chat_id'],
                photo=data['image_url'],
                caption=f"{data['title']}\n\n{data['body']}"
            )
        else:
            context.bot.send_message(
                chat_id=data['chat_id'],
                text=f"{data['title']}\n\n{data['body']}"
            )
        
        # Update status in database
        update_post_status(data['group_id'], True)
        logger.info(f"Запланированный пост {data['group_id']} опубликован.")
        
    except Exception as e:
        logger.error(f"Ошибка при публикации запланированного поста: {e}")
        
        # Notify admin about failure
        context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"❌ Ошибка при публикации запланированного поста:\n{str(e)}"
        )

# --- News processing ---
def process_news(groups: dict, context: CallbackContext, send_loading_msg: bool = False, update_obj: Update = None):
    """
    Обрабатывает группы новостей, генерирует контент и подготавливает данные для модерации.
    Для каждой группы перед началом обработки отправляется сообщение "Генерирую, в очереди n постов",
    которое затем редактируется с заменой на пост новости или сообщение об ошибке.
    
    Args:
        groups: Словарь групп новостей.
        context: CallbackContext Telegram.
        send_loading_msg: Если True, отправка сообщений производится через update_obj.
        update_obj: Объект Update, если вызывается ручная команда.
    """
    # Гарантируем, что context.chat_data инициализирован
    if not hasattr(context, 'chat_data') or context.chat_data is None:
        context.chat_data = {}

    group_ids = list(groups.keys())
    total_groups = len(group_ids)
    
    # Определяем chat_id для сообщений (обычно ADMIN_ID)
    chat_id = ADMIN_ID if send_loading_msg or update_obj is None else update_obj.effective_chat.id
    
    for i, group_id in enumerate(group_ids):
        remaining = total_groups - i

        # Отправляем сообщение с информацией о количестве оставшихся групп
        status_msg = context.bot.send_message(
            chat_id=chat_id,
            text=f"⏳ Генерирую контент, в очереди {remaining} постов..."
        )
        
        news_group = groups[group_id]
        try:
            # Генерируем текст поста с помощью API редактора
            result = call_editor_api(news_group)
            if result is None:
                status_msg.edit_text(f"❌ Не удалось обработать новости группы {group_id}.")
                continue

            # Если результат одобрен, пытаемся сгенерировать изображение (если есть промт)
            post = result.get("post", {})
            illustration_prompt = post.get("illustration", "")
            image_url = None
            if illustration_prompt and result.get("resolution") == "approve":
                image_url = generate_image(illustration_prompt)

            # Сохраняем данные группы в context.chat_data для дальнейшей работы (например, для кнопок)
            context.chat_data[f"group_{group_id}"] = {
                "news_group": news_group,
                "editor_result": result,
                "image_url": image_url
            }
            
            if result.get("resolution") == "approve":
                title = post.get("title", "Без заголовка")
                body = post.get("body", "")
                message_text = f"{title}\n\n{body}"
                
                # Создаем клавиатуру для дальнейших действий
                keyboard = [
                    [
                        InlineKeyboardButton("🔄 Другой текст", callback_data=f"again:{group_id}"),
                        InlineKeyboardButton("🖼️ Другая картинка", callback_data=f"image:{group_id}")
                    ],
                    [
                        InlineKeyboardButton("📅 Запланировать", callback_data=f"schedule:{group_id}"),
                        InlineKeyboardButton("✅ Опубликовать", callback_data=f"approve:{group_id}")
                    ],
                    [
                        InlineKeyboardButton("❌ Отклонить", callback_data=f"cancel:{group_id}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Редактируем отправленное сообщение: если есть изображение – редактируем медиа, иначе – текст
                if image_url:
                    try:
                        context.bot.edit_message_media(
                            chat_id=chat_id,
                            message_id=status_msg.message_id,
                            media=InputMediaPhoto(media=image_url, caption=message_text),
                            reply_markup=reply_markup
                        )
                    except Exception as e:
                        logger.error(f"Ошибка редактирования медиа сообщения для группы {group_id}: {e}")
                        context.bot.send_photo(
                            chat_id=chat_id,
                            photo=image_url,
                            caption=message_text,
                            reply_markup=reply_markup
                        )
                else:
                    status_msg.edit_text(text=message_text, reply_markup=reply_markup)
            else:
                # Если редактор отклонил новость – редактируем сообщение с указанием причины
                reason = result.get("reason", "Нет объяснения")
                status_msg.edit_text(
                    f"Новости группы {group_id} отклонены редактором.\nПричина: {reason}"
                )
                update_news_status_by_group(group_id, False)
        except Exception as e:
            logger.error(f"Ошибка при обработке группы {group_id}: {e}")
            status_msg.edit_text(f"❌ Ошибка при обработке группы {group_id}: {str(e)}")


# --- Button callbacks ---
def button_handler(update: Update, context: CallbackContext):
    """Handle button presses from inline keyboards."""
    query = update.callback_query
    query.answer()
    
    try:
        action, group_id = query.data.split(":", 1)
    except Exception as e:
        logger.error(f"Неверный формат callback_data: {query.data}")
        return
    
    group_data = context.chat_data.get(f"group_{group_id}")
    if not group_data:
        query.edit_message_text("Данные группы не найдены.")
        return
    
    news_group = group_data["news_group"]
    
    if action == "approve":
        # Publish immediately
        update_news_status_by_group(group_id, True)
        post = group_data["editor_result"].get("post", {})
        title = post.get("title", "Без заголовка")
        body = post.get("body", "")
        message_text = f"{title}\n\n{body}"
        image_url = group_data.get("image_url")
        
        try:
            if image_url:
                context.bot.send_photo(
                    chat_id=CHANNEL_ID, 
                    photo=image_url, 
                    caption=message_text
                )
            else:
                context.bot.send_message(
                    chat_id=CHANNEL_ID, 
                    text=message_text
                )
            
            query.edit_message_reply_markup(None)
            query.message.reply_text("✅ Пост опубликован!")
            logger.info(f"Группа {group_id} опубликована.")
        except Exception as e:
            logger.error(f"Ошибка при публикации поста: {e}")
            query.message.reply_text(f"❌ Ошибка при публикации: {str(e)}")
    
    elif action == "cancel":
        # Reject news
        update_news_status_by_group(group_id, False)
        query.edit_message_reply_markup(None)
        query.message.reply_text("❌ Пост отклонен.")
        logger.info(f"Группа {group_id} отклонена.")
    
    elif action == "again":
        # Regenerate text
        query.edit_message_reply_markup(None)
        regenerate_msg = query.message.reply_text("🔄 Генерирую новый вариант текста...")
        
        try:
            result = call_editor_api(news_group)
            if result is None:
                regenerate_msg.edit_text("❌ Ошибка при повторной генерации текста.")
                return
            
            group_data["editor_result"] = result
            post = result.get("post", {})
            title = post.get("title", "Без заголовка")
            body = post.get("body", "")
            illustration_prompt = post.get("illustration", "")
            message_text = f"{title}\n\n{body}"
            
            # Generate new image for new text
            new_image_url = generate_image(illustration_prompt)
            group_data["image_url"] = new_image_url
            
            keyboard = [
                [
                    InlineKeyboardButton("🔄 Другой текст", callback_data=f"again:{group_id}"),
                    InlineKeyboardButton("🖼️ Другая картинка", callback_data=f"image:{group_id}")
                ],
                [
                    InlineKeyboardButton("📅 Запланировать", callback_data=f"schedule:{group_id}"),
                    InlineKeyboardButton("✅ Опубликовать", callback_data=f"approve:{group_id}")
                ],
                [
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"cancel:{group_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Delete original message and send new one
            regenerate_msg.delete()
            if new_image_url:
                context.bot.send_photo(
                    chat_id=ADMIN_ID, 
                    photo=new_image_url, 
                    caption=message_text, 
                    reply_markup=reply_markup
                )
            else:
                context.bot.send_message(
                    chat_id=ADMIN_ID, 
                    text=message_text, 
                    reply_markup=reply_markup
                )
                
        except Exception as e:
            logger.error(f"Ошибка при регенерации текста: {e}")
            regenerate_msg.edit_text(f"❌ Ошибка при регенерации текста: {str(e)}")
    
    elif action == "image":
        # Regenerate image only
        query.message.reply_text("🔄 Генерирую новое изображение...")
        
        post = group_data["editor_result"].get("post", {})
        illustration_prompt = post.get("illustration", "")
        
        try:
            new_image_url = generate_image(illustration_prompt)
            if new_image_url:
                group_data["image_url"] = new_image_url
                
                keyboard = [
                    [
                        InlineKeyboardButton("🔄 Другой текст", callback_data=f"again:{group_id}"),
                        InlineKeyboardButton("🖼️ Другая картинка", callback_data=f"image:{group_id}")
                    ],
                    [
                        InlineKeyboardButton("📅 Запланировать", callback_data=f"schedule:{group_id}"),
                        InlineKeyboardButton("✅ Опубликовать", callback_data=f"approve:{group_id}")
                    ],
                    [
                        InlineKeyboardButton("❌ Отклонить", callback_data=f"cancel:{group_id}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                try:
                    context.bot.edit_message_media(
                        chat_id=ADMIN_ID,
                        message_id=query.message.message_id,
                        media=InputMediaPhoto(media=new_image_url, caption=query.message.caption),
                        reply_markup=reply_markup
                    )
                    context.bot.send_message(chat_id=ADMIN_ID, text="✅ Изображение обновлено.")
                except Exception as e:
                    logger.error(f"Ошибка редактирования медиа сообщения: {e}")
                    context.bot.send_photo(
                        chat_id=ADMIN_ID, 
                        photo=new_image_url, 
                        caption=query.message.caption, 
                        reply_markup=reply_markup
                    )
            else:
                context.bot.send_message(chat_id=ADMIN_ID, text="❌ Ошибка генерации изображения.")
        except Exception as e:
            logger.error(f"Ошибка при генерации нового изображения: {e}")
            context.bot.send_message(
                chat_id=ADMIN_ID, 
                text=f"❌ Ошибка при генерации нового изображения: {str(e)}"
            )

# --- Scheduled jobs ---
def scheduled_check_news(context: CallbackContext):
    """Scheduled job to check for new news."""
    logger.info("Запущена плановая проверка новостей.")
    groups = get_unposted_news_groups()
    
    # If no news, do nothing
    if not groups:
        logger.info("Плановая проверка: новых записей не найдено.")
        return
    
    logger.info(f"Плановая проверка: найдено {len(groups)} групп для обработки.")
    process_news(groups, context)

def main():
    """Main function to start the bot."""
    # Validate environment
    if not validate_environment():
        logger.critical("Ошибка валидации переменных окружения. Бот не запущен.")
        return
    
    # Initialize OpenAI client
    openai.api_key = OPENAI_API_KEY
    
    # Setup telegram bot
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    
    # Add command handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("checknews", check_news))
    dp.add_handler(CommandHandler("status", show_status))
    dp.add_handler(CommandHandler("scheduled", show_scheduled))
    
    # Setup scheduling conversation
    schedule_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_scheduling, pattern='^schedule:')],
        states={
            SCHEDULE_TIME: [CallbackQueryHandler(select_time)],
            SCHEDULE_CONFIRM: [CallbackQueryHandler(confirm_schedule)],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_conversation, pattern='^cancel_scheduling$'),
            CommandHandler('cancel', cancel_conversation)
        ],
    )
    dp.add_handler(schedule_conv_handler)
    
    # Add general button handler for other callbacks
    dp.add_handler(CallbackQueryHandler(button_handler))
    
    # Setup job queue
    job_queue = updater.job_queue
    
    # Schedule news check every 10 minutes
    job_queue.run_repeating(
        scheduled_check_news, 
        interval=600,  # 10 minutes
        first=10  # Wait 10 seconds before first check
    )
    
    # Load and schedule saved posts from database
    try:
        scheduled_posts = get_scheduled_posts()
        for post in scheduled_posts:
            if post['scheduled_time'] > datetime.now():
                job_context = {
                    'chat_id': CHANNEL_ID,
                    'group_id': post['group_id'],
                    'title': post.get('title', 'Без заголовка'),
                    'body': post.get('body', ''),
                    'image_url': post.get('image_url')
                }
                job_queue.run_once(
                    post_scheduled_content, 
                    post['scheduled_time'],
                    context=job_context,
                    name=f"scheduled_{post['group_id']}"
                )
                logger.info(f"Загружена запланированная публикация: {post['group_id']} на {post['scheduled_time']}")
    except Exception as e:
        logger.error(f"Ошибка при загрузке запланированных публикаций: {e}")
    
    # Start the Bot
    logger.info("Бот запущен и начинает опрос...")
    updater.start_polling()
    
    # Run the bot until you press Ctrl-C
    updater.idle()

if __name__ == '__main__':
    main()