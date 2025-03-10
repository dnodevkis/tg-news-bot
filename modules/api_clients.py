#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
API Clients module
==================
Provides functions to call external APIs like Claude (Anthropic) and OpenAI DALL-E.
"""

import os
import json
import logging
import requests
import openai
from modules.config import CLAUDE_API_KEY, CLAUDE_MODEL, SYSTEM_PROMPT, OPENAI_API_KEY

logger = logging.getLogger(__name__)

def call_editor_api(news_group):
    """
    Вызывает API Claude для генерации поста.
    Формирует текст запроса на основе совокупных новостных заметок news_group и системного промта.
    Возвращает словарь с результатом или None при ошибке.
    """
    # Собираем из news_group общий текст
    news_text = "\n".join([n["report"] for n in news_group])
    
    # Формируем промт
    prompt = (
        f"Набор новостных заметок:\n{news_text}\n\n"
        f"Преобразуй их согласно описанию:\n{SYSTEM_PROMPT}"
    )
    
    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 4000,
        "system": SYSTEM_PROMPT,  # Иногда не требуется, зависит от API
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": CLAUDE_API_KEY,
        # Обратите внимание: anthropic-version может отличаться
        "anthropic-version": "2023-06-01"
    }

    logger.debug("Отправляем payload в API Claude:\n%s", json.dumps(payload, indent=2, ensure_ascii=False))
    
    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            json=payload,
            headers=headers,
            timeout=20
        )
        response.raise_for_status()

        # Часть API Anthropic возвращает data["completion"],
        # часть - data["content"][...]. Ниже условная логика.
        data = response.json()
        raw_response = json.dumps(data, indent=2, ensure_ascii=False)
        logger.debug("Сырой ответ от Claude:\n%s", raw_response)

        # Извлекаем поле с текстом:
        if "content" in data and isinstance(data["content"], list) and data["content"]:
            reply = data["content"][0].get("text", "")
        elif "completion" in data:
            reply = data["completion"]
        else:
            reply = ""

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
    Генерирует изображение с помощью OpenAI (DALL-E).
    Возвращает URL изображения или None при ошибке.
    """
    # Устанавливаем ключ
    openai.api_key = OPENAI_API_KEY

    # Если prompt пустой или вдруг "deny", возвращаем None
    if not prompt or not prompt.strip():
        return None

    try:
        # Параметры модели DALL-E 3 могут меняться, проверяйте официальную документацию OpenAI
        response = openai.images.generate(
             prompt=prompt,
             model="dall-e-3",
             n=1,
             size="1024x1024",  # Возможные размеры: "256x256", "512x512", "1024x1024"
             response_format="url",
             quality="hd"
         )
        image_url = response["data"][0]["url"]
        logger.debug("Сгенерированное изображение: %s", image_url)
        return image_url
    except Exception as e:
        logger.error("Ошибка генерации изображения: %s", e)
        return None
