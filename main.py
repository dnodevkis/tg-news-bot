import os
import json
import logging
import requests
import openai  # Библиотека для работы с OpenAI API (DALL-E)
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, CallbackContext
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG  # Для отладки; в продакшене можно сменить на INFO
)
logger = logging.getLogger(__name__)

# Константы для бота (из .env)
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@Echo_of_Langinion")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-3-7-sonnet-20250219")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Константы для подключения к БД
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "mydb")
DB_USER = os.getenv("DB_USER", "admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "password")

# Настраиваем OpenAI
openai.api_key = OPENAI_API_KEY

# Системный промт для редактора (можно вынести в отдельный файл)
SYSTEM_PROMPT = (
    "Вы — опытный редактор городской газеты с чутьем на интересные местные истории и необычные происшествия. "
    "Ваша задача — находить в репортерских заметках забавные эпизоды и превращать их в увлекательные городские байки. "
    "Когда пользователь предоставляет набор новостных заметок, связанных с одним событием и упорядоченных по времени, следуйте этому алгоритму:\n\n"
    "1. Внимательно проанализируйте содержание, выискивая необычные, курьезные или интересные для городских жителей детали и эпизоды.\n\n"
    "2. Оцените потенциал материала для создания занимательной городской истории и примите решение о публикации.\n\n"
    "3. В случае одобрения, разработайте КРАТКИЙ, ЯРКИЙ ЗАГОЛОВОК ПОЛНОСТЬЮ ЗАГЛАВНЫМИ БУКВАМИ (не более 5-7 слов), который моментально привлечет внимание и заинтригует читателя.\n\n"
    "4. Сформулируйте краткую заметку объемом 3-4 предложения, которая:\n"
    "   - Фокусируется на одном интересном эпизоде или детали, даже если это второстепенный аспект исходной истории\n"
    "   - Использует газетные обороты типа \"от тайного информатора\", \"местные жители дали интервью\", \"в таверне ходят слухи\"\n"
    "   - Представляет историю с точки зрения города и его жителей максимально ярко и сочно\n"
    "   - Содержит элементы сенсационности и преувеличения для придания истории большей привлекательности\n"
    "   - Имеет легкий юмористический подтекст или необычный ракурс\n\n"
    "5. Обязательно создайте промт для иллюстрации, который:\n"
    "   - Всегда должен начинаться с фразы \"watercolor illustration of...\"\n"
    "   - Должен быть комичным и простым, с максимум 1-2 объектами\n"
    "   - Всегда должен заканчиваться фразой \"light sepia effect\"\n\n"
    "6. Предоставьте свой ответ в формате JSON согласно следующей схеме:\n"
    "{\n"
    "  \"resolution\": \"approve\" или \"deny\",\n"
    "  \"post\": {\n"
    "    \"title\": \"ЗАГОЛОВОК НОВОСТИ\",\n"
    "    \"body\": \"содержание новости\",\n"
    "    \"illustration\": \"описание иллюстрации\"\n"
    "  }\n"
    "}\n\n"
    "Поле \"post\" должно присутствовать только если resolution=\"approve\".\n\n"
    "Ваша задача — превратить обычные события в яркие, броские городские истории, которые заставят жителей города оторваться от своих дел и с интересом обсуждать их на площадях и в тавернах."
)

# --- Функции для работы с базой данных ---

def get_db_connection():
    """Возвращает новое соединение с базой данных."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )

def get_unposted_news_groups():
    """
    Извлекает все записи из таблицы fetched_events, где isPosted IS NULL.
    Группирует их по groupId, сортирует по eventDate и оставляет последние 3 записи для каждой группы.
    Возвращает словарь: { groupId: [news, ...] }
    """
    groups = {}
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        query = '''
            SELECT event_id, "groupId", "eventDate", report, "isPosted"
            FROM fetched_events
            WHERE "isPosted" IS NULL
            ORDER BY "eventDate" ASC;
        '''
        cur.execute(query)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        for row in rows:
            group_id = row["groupId"]
            groups.setdefault(group_id, []).append(row)
        # Для каждой группы оставляем последние 3 (сортировка по eventDate)
        for group_id, news_list in groups.items():
            news_list.sort(key=lambda r: r.get("eventDate", ""))
            groups[group_id] = news_list[-3:]
        logger.debug("Группировка новостей завершена: %s", groups)
        return groups
    except Exception as e:
        logger.error("Ошибка получения новостей из БД: %s", e)
        return {}

def update_news_status_by_group(group_id, status):
    """
    Обновляет поле isPosted для всех записей с заданным group_id, где isPosted IS NULL.
    status: True (опубликовать) или False (отклонить)
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = 'UPDATE fetched_events SET "isPosted" = %s WHERE "groupId" = %s AND "isPosted" IS NULL;'
        cur.execute(query, (status, group_id))
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Статус новостей группы %s обновлён на %s.", group_id, status)
    except Exception as e:
        logger.error("Ошибка обновления статуса группы %s: %s", group_id, e)

# --- Функции для работы с API редактора и генерации изображений ---

def call_editor_api(news_group):
    """
    Вызывает API Claude для генерации поста.
    Формирует текст запроса на основе совокупных новостных заметок news_group и системного промта.
    Возвращает словарь с результатом, либо None при ошибке.
    """
    news_text = "\n".join([n["report"] for n in news_group])
    prompt = f"Набор новостных заметок:\n{news_text}\n\nПреобразуй их согласно описанию:\n{SYSTEM_PROMPT}"
    
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 4000,
        "system": SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01"
    }
    
    logger.debug("Отправляем payload в API Claude:\n%s", json.dumps(payload, indent=2, ensure_ascii=False))
    try:
        response = requests.post("https://api.anthropic.com/v1/messages",
                                 json=payload, headers=headers, timeout=20)
        response.raise_for_status()
        raw_response = response.text
        logger.debug("Сырой ответ от API:\n%s", raw_response)
        
        data = response.json()
        if "content" in data and isinstance(data["content"], list) and data["content"]:
            reply = data["content"][0].get("text", "")
        elif "completion" in data:
            reply = data["completion"]
        else:
            reply = ""
        logger.debug("Извлечённый ответ:\n%s", reply)
        
        def clean_reply(text):
            text = text.strip()
            if text.startswith("```json"):
                text = text[len("```json"):].strip()
            if text.endswith("```"):
                text = text[:-3].strip()
            return text
        
        cleaned_reply = clean_reply(reply)
        logger.debug("Очищенный ответ для парсинга:\n%s", cleaned_reply)
        
        try:
            result = json.loads(cleaned_reply)
            logger.debug("Распарсенный результат:\n%s", json.dumps(result, indent=2, ensure_ascii=False))
            return result
        except Exception as e:
            logger.error("Ошибка парсинга ответа редактора: %s", e)
            logger.error("Сырой ответ после очистки: %s", cleaned_reply)
            return None
    except requests.RequestException as e:
        logger.error("Ошибка вызова API редактора: %s", e)
        return None

def generate_image(prompt):
    """
    Генерирует изображение с помощью OpenAI DALL-E (DALL-E 3) по заданному промту.
    Возвращает URL изображения или None при ошибке.
    """
    try:
        response = openai.images.generate(
            prompt=prompt,
            model="dall-e-3",
            n=1,
            size="1024x1024",  # Возможные размеры: "256x256", "512x512", "1024x1024"
            response_format="url",
            quality="hd"
        )
        image_url = response.data[0].url
        logger.debug("Сгенерированное изображение: %s", image_url)
        return image_url
    except Exception as e:
        logger.error("Ошибка генерации изображения: %s", e)
        return None

# --- Handlers бота ---

def start(update: Update, context: CallbackContext):
    update.message.reply_text("Привет! Используйте команду /checknews для проверки новостей из БД.")

def check_news(update: Update, context: CallbackContext):
    groups = get_unposted_news_groups()
    if not groups:
        update.message.reply_text("Новых новостей нет.")
        return
    # Проходим по группам новостей (каждая группа – это набор до 3 новостей, объединённых по groupId)
    for group_id, news_group in groups.items():
        result = call_editor_api(news_group)
        if result is None:
            update.message.reply_text(f"Не удалось обработать новости группы {group_id}.")
            continue
        
        # Генерируем изображение до показа кнопок (если предусмотрено в ответе)
        post = result.get("post", {})
        illustration_prompt = post.get("illustration", "")
        image_url = generate_image(illustration_prompt)
        
        # Сохраняем данные группы для дальнейшей обработки
        context.chat_data[f"group_{group_id}"] = {
            "news_group": news_group,
            "editor_result": result,
            "image_url": image_url
        }
        
        if result.get("resolution") == "approve":
            title = post.get("title", "Без заголовка")
            body = post.get("body", "")
            message_text = f"{title}\n\n{body}"
            keyboard = [
                [
                    InlineKeyboardButton("🔄 Другой текст", callback_data=f"again:{group_id}"),
                    InlineKeyboardButton("🖼️ Другая картинка", callback_data=f"image:{group_id}")
                ],
                [
                    InlineKeyboardButton("✅ Опубликовать", callback_data=f"approve:{group_id}"),
                    InlineKeyboardButton("❌ Отменить", callback_data=f"cancel:{group_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            if image_url:
                context.bot.send_photo(chat_id=ADMIN_ID, photo=image_url, caption=message_text, reply_markup=reply_markup)
            else:
                context.bot.send_message(chat_id=ADMIN_ID, text=message_text, reply_markup=reply_markup)
        else:
            update.message.reply_text(f"Новости группы {group_id} отклонены редактором.")
            update_news_status_by_group(group_id, False)

def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    try:
        action, group_id = query.data.split(":", 1)
    except Exception as e:
        logger.error("Неверный формат callback_data: %s", query.data)
        return

    group_data = context.chat_data.get(f"group_{group_id}")
    if not group_data:
        query.edit_message_text("Данные группы не найдены.")
        return

    news_group = group_data["news_group"]
    if action == "approve":
        update_news_status_by_group(group_id, True)
        post = group_data["editor_result"].get("post", {})
        title = post.get("title", "Без заголовка")
        body = post.get("body", "")
        message_text = f"{title}\n\n{body}"
        image_url = group_data.get("image_url")
        if image_url:
            context.bot.send_photo(chat_id=CHANNEL_ID, photo=image_url, caption=message_text)
        else:
            context.bot.send_message(chat_id=CHANNEL_ID, text=message_text)
        query.edit_message_reply_markup(None)
        query.answer("Пост опубликован.")
        logger.info("Группа %s опубликована.", group_id)
    elif action == "cancel":
        update_news_status_by_group(group_id, False)
        query.edit_message_reply_markup(None)
        query.answer("Событие отклонено.")
        logger.info("Группа %s отклонена.", group_id)
    elif action == "again":
        result = call_editor_api(news_group)
        if result is None:
            query.answer("Ошибка при повторной генерации.")
            return
        group_data["editor_result"] = result
        post = result.get("post", {})
        title = post.get("title", "Без заголовка")
        body = post.get("body", "")
        illustration_prompt = post.get("illustration", "")
        message_text = f"{title}\n\n{body}"
        new_image_url = generate_image(illustration_prompt)
        group_data["image_url"] = new_image_url
        keyboard = [
            [
                InlineKeyboardButton("🔄 Другой текст", callback_data=f"again:{group_id}"),
                InlineKeyboardButton("🖼️ Другая картинка", callback_data=f"image:{group_id}")
            ],
            [
                InlineKeyboardButton("✅ Опубликовать", callback_data=f"approve:{group_id}"),
                InlineKeyboardButton("❌ Отменить", callback_data=f"cancel:{group_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            context.bot.edit_message_media(
                chat_id=ADMIN_ID,
                message_id=query.message.message_id,
                media=InputMediaPhoto(media=new_image_url, caption=message_text),
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error("Ошибка редактирования медиа сообщения: %s", e)
            context.bot.send_photo(chat_id=ADMIN_ID, photo=new_image_url, caption=message_text, reply_markup=reply_markup)
    elif action == "image":
        post = group_data["editor_result"].get("post", {})
        illustration_prompt = post.get("illustration", "")
        new_image_url = generate_image(illustration_prompt)
        if new_image_url:
            group_data["image_url"] = new_image_url
            keyboard = [
                [
                    InlineKeyboardButton("🔄 Другой текст", callback_data=f"again:{group_id}"),
                    InlineKeyboardButton("🖼️ Другая картинка", callback_data=f"image:{group_id}")
                ],
                [
                    InlineKeyboardButton("✅ Опубликовать", callback_data=f"approve:{group_id}"),
                    InlineKeyboardButton("❌ Отменить", callback_data=f"cancel:{group_id}")
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
            except Exception as e:
                logger.error("Ошибка редактирования медиа сообщения: %s", e)
                context.bot.send_photo(chat_id=ADMIN_ID, photo=new_image_url, caption=query.message.caption, reply_markup=reply_markup)
            query.answer("Изображение обновлено.")
        else:
            query.answer("Ошибка генерации изображения.")
    else:
        logger.error("Неизвестное действие: %s", action)

def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("checknews", check_news))
    dp.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("Бот запущен и начинает опрос...")
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
