# Используем официальный образ Python (версия 3.9-slim)
FROM python:3.9-slim

# Устанавливаем рабочую директорию
WORKDIR /app/tg-news-bot

# Копируем файл зависимостей и устанавливаем их
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Копируем все файлы проекта в контейнер
COPY . .

# Определяем переменные окружения (значения передаются через docker-compose)
ENV DB_HOST="" \
    DB_PORT="" \
    DB_NAME="" \
    DB_USER="" \
    DB_PASSWORD="" \
    BOT_TOKEN="" \
    CLAUDE_API_KEY="" \
    CLAUDE_MODEL="claude-3-7-sonnet-20250219" \
    ADMIN_ID="" \
    CHANNEL_ID="" \
    OPENAI_API_KEY=""

# Запуск бота
CMD ["python", "main.py"]
