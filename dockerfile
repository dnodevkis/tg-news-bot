# Используем официальный образ Python (версия 3.9-slim)
FROM python:3.9-slim

# Устанавливаем рабочую директорию
WORKDIR /app/tg-claude-bot

# Копируем файл зависимостей и устанавливаем их
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Копируем все файлы проекта в контейнер
COPY . .

# Определяем переменные окружения без жёстко заданных значений.
# Значения будут переданы во время запуска контейнера (например, через docker-compose)
ENV BOT_TOKEN="" \
    CLAUDE_API_KEY="" \
    CLAUDE_MODEL="claude-3-7-sonnet-20250219"

# Запуск бота
CMD ["python", "main.py"]
