import os
import json
import logging
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

# Загружаем переменные окружения из файла .env
load_dotenv()

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Получение переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL")

# Системная инструкция для Claude
SYSTEM_INSTRUCTION = "Ты должен ответить на следующий запрос максимально подробно и понятно."

# Глобальный кэш для хранения контекста диалога для каждого пользователя
# Для каждого chat_id будем хранить список последних сообщений (до 5 сообщений: сообщения пользователя и ответы ассистента)
user_context = {}

def start(update: Update, context: CallbackContext):
    """Обработчик команды /start."""
    update.message.reply_text("Привет! Отправь сообщение, и я передам его в API Claude. Используй /reset для сброса контекста.")

def reset_context(update: Update, context: CallbackContext):
    """Сброс контекста диалога для данного пользователя."""
    chat_id = update.message.chat_id
    if chat_id in user_context:
        del user_context[chat_id]
    update.message.reply_text("Контекст диалога сброшен.")

def handle_message(update: Update, context: CallbackContext):
    """Обрабатывает входящие сообщения, сохраняет контекст и отправляет запрос к API Claude."""
    user_text = update.message.text
    if not user_text:
        return

    chat_id = update.message.chat_id

    # Инициализируем контекст для пользователя, если его нет
    if chat_id not in user_context:
        user_context[chat_id] = []

    # Добавляем новое сообщение пользователя в контекст
    user_context[chat_id].append({"role": "user", "content": user_text})
    # Ограничиваем контекст: оставляем только последние 5 сообщений
    if len(user_context[chat_id]) > 5:
        user_context[chat_id] = user_context[chat_id][-5:]

    # Формируем payload для API Claude
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 2000,
        "system": SYSTEM_INSTRUCTION,
        "messages": user_context[chat_id]
    }

    headers = {
        "Content-Type": "application/json",
        "x-api-key": CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01"
    }

    try:
        # Отправляем запрос на рабочий эндпоинт /v1/messages
        response = requests.post("https://api.anthropic.com/v1/messages",
                                 json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        # Извлекаем текст ответа (в рабочем варианте API возвращает ответ в поле content)
        if "content" in data and isinstance(data["content"], list) and len(data["content"]) > 0:
            reply_text = data["content"][0].get("text", "")
        elif "completion" in data:
            reply_text = data["completion"]
        else:
            reply_text = json.dumps(data)

        # Добавляем ответ ассистента в контекст
        user_context[chat_id].append({"role": "assistant", "content": reply_text})
        if len(user_context[chat_id]) > 10:
            user_context[chat_id] = user_context[chat_id][-10:]

        update.message.reply_text(reply_text)

    except requests.exceptions.RequestException as e:
        logger.error("Ошибка при запросе к API Claude: %s", e)
        update.message.reply_text("Произошла ошибка при обращении к API Claude. Попробуйте позже.")

def main():
    """Запуск бота."""
    if not BOT_TOKEN or not CLAUDE_API_KEY:
        logger.error("Не заданы BOT_TOKEN или CLAUDE_API_KEY в переменных окружения.")
        return

    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("reset", reset_context))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))

    updater.start_polling()
    logger.info("Бот запущен и ожидает сообщений.")
    updater.idle()

if __name__ == '__main__':
    main()
