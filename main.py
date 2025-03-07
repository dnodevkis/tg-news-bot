import os
import json
import logging
import requests
import openai  # Библиотека для работы с OpenAI API (DALL-E)
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

# Константы из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@Echo_of_Langinion")
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-3-7-sonnet-20250219")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Настраиваем OpenAI
openai.api_key = OPENAI_API_KEY

# Системный промт для редактора (AI-агента)
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

# --- Заглушка "База данных" ---
# Имитируем таблицу EventReports (новостные заметки)
event_reports = [
    {"id": 1, "group_id": "A", "report": "Проснувшись в трактире, и почувствовав ряд изменений в своих магических ощущениях, приключенцы поспешили в пещеру, карту, которой прикупил Шаль накануне. По дороге они встретили Эльмиру, у которой Эмма узнала про чудесный цветок, способный излечить ее зрение. При выходе из городских ворот, чародейки помогли работягам, жаловавшимся на плохие кирки и отсутствие кузнеца, растопить лед, сковавший городскую стену. Чтобы лишний раз не рисковать, Аваслава отправила Сову на разведку. Та принесла весть, что пещера мало походит на то, что описывал торговец. Ощущение, что это древняя дворфийская обустроенная пещера. По крайней мере на ее входе стоит красивая дверь дворфийской ковки и защищенная рунами. А еще сова увидела там гигантскую змею, нависавшую над проходом. Набравшись храбрости, команда отправилась на вход. Змея к тому моменту куда-то уползла, а вот дверь таила в себе загадку Лишь только пожертвовав рукой, можно было войти внутрь, гласила надпись на двери. Пока искали, чем можно заменить руку, Шаль обрубил корни дерева, где притаилась змея. Так получилось, что совершенно случайно та стала жертвой  его острого меча. Эфросима с помощью своего магического кольца превратилась в пчелку и посмотрела механизм ловушки на двери. Она поняла, что ловушка не сконструирована для отрубания руки и Шаль решился на жертву. Он вставил руку, дверь начала отворяться и потащила его за собой. Остальные поторопились войти следом. Потом дверь стала закрываться, а ловушка ослабевать. Шаль ловко вывернул руку и в последний момент просочился в пещеру через закрывающуюся дверь. В пещере они действительно нашли признаки того, что дворфы здесь жили раньше. Не очень хорошо сохранившиеся столы и стулья напоминали обычное убранство дворфийских крепостей. Осмотрев всю пещеру, компания нашла озерцо с чудесными цветами, о которых говорила Эльмира и место, где зарыт клад, однако путь туда преграждает электрический угорь, достаточно агрессивный, чтобы пускать кого-то в свое логово. Также они нашли сундук с пятью черными мантиями и старую разрушенную статую. В другой части пещеры были комнаты с дверьми, закрытыми на ключ. Опять же в образе пчелы Ефросима пролетела по остальным помещениям и рассказала друзьям, что видела там.", "created_at": "2025-03-01T12:00:00", "isPosted": None},
    {"id": 2, "group_id": "A", "report": "Девушки наконец-то дошли до города Иворанд, по крайней мере так было написано на старом пошарпанном столбе. При входе в город была огромная глыба льда, которую горожане разбивали полусломанными кирками, ругаясь, что не могут получить нормальный инструмент, потому что кузнец куда-то запропастился. Войдя в город, дамы обнаружили прекрасную рыночную площадь, на которой стоял не менее прекрасный воин. Он о чем-то тихо беседовал с торговцем артефактами. Во время знакомства раздался мощный взрыв. Пламя охватило внушительный старый особняк и началась суматоха. Чернокнижницы отправились на помощь. Потушив пожар, они стали выяснять, в чем дело. Оказывается, Эльмира, глава местного филиала гильдии магов вела обычную лекцию и показывала действие стихийного снаряда. Но что-то пошло не так. Магия будто взбесилась и стала на мгновение бесконтрольной. Эльмира пообещала, что хорошо вознаградит приключенцев. если они помогут убрать последствия взрыва. ", "created_at": "2025-03-02T13:00:00", "isPosted": None},
]

def get_unposted_news_groups():
    """
    Группирует новости с isPosted == None по group_id и оставляет последние 3 записи по дате.
    Если поле "created_at" отсутствует, используется пустая строка.
    Возвращает словарь: { group_id: [news, ...] }
    """
    groups = {}
    for report in event_reports:
        if report.get("isPosted") is None:
            group_id = report.get("group_id")
            groups.setdefault(group_id, []).append(report)
    for group_id, reports in groups.items():
        reports.sort(key=lambda r: r.get("created_at", ""))
        groups[group_id] = reports[-3:]
    return groups

def update_news_status(group_id, status):
    """
    Обновляет поле isPosted для всех новостей с данным group_id,
    где isPosted пока None.
    status: True (одобрено), False (отменено)
    """
    for report in event_reports:
        if report.get("group_id") == group_id and report.get("isPosted") is None:
            report["isPosted"] = status

def call_editor_api(news_group):
    """
    Вызывает API Claude для генерации поста.
    Формирует текст запроса на основе новостных заметок и системного промта.
    Возвращает словарь с результатом, либо None при ошибке.
    """
    news_text = "\n".join([r["report"] for r in news_group])
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
    
    logger.debug("Отправляем payload:\n%s", json.dumps(payload, indent=2, ensure_ascii=False))
    
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
    update.message.reply_text("Привет! Используйте команду /checknews для проверки новостей.")

def check_news(update: Update, context: CallbackContext):
    groups = get_unposted_news_groups()
    if not groups:
        update.message.reply_text("Новых новостей нет.")
        return
    
    for group_id, news_group in groups.items():
        result = call_editor_api(news_group)
        if result is None:
            update.message.reply_text(f"Не удалось обработать новости группы {group_id}.")
            continue
        
        # Генерируем изображение до показа кнопок
        post = result.get("post", {})
        illustration_prompt = post.get("illustration", "")
        image_url = generate_image(illustration_prompt)
        
        # Сохраняем данные для дальнейшей обработки, включая сгенерированное изображение
        context.chat_data[f"group_{group_id}"] = {
            "news_group": news_group,
            "editor_result": result,
            "image_url": image_url
        }
        
        if result.get("resolution") == "approve":
            title = post.get("title", "Без заголовка")
            body = post.get("body", "")
            message_text = f"{title}\n\n{body}"
            # Изменён порядок кнопок и добавлены эмодзи
            keyboard = [
                [
                    InlineKeyboardButton("🔄 Другой текст", callback_data=f"again:{group_id}"),
                    InlineKeyboardButton("🖼️ Другая картинка", callback_data=f"image:{group_id}")
                ],
                [
                    InlineKeyboardButton("✅ Принять", callback_data=f"approve:{group_id}"),
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
            update_news_status(group_id, False)

def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    data = query.data  # Формат: "approve:GROUP", "again:GROUP", "cancel:GROUP", "image:GROUP"
    action, group_id = data.split(":")
    
    group_data = context.chat_data.get(f"group_{group_id}")
    if not group_data:
        query.edit_message_text("Данные группы не найдены.")
        return
    news_group = group_data["news_group"]
    
    if action == "approve":
        update_news_status(group_id, True)
        post = group_data["editor_result"].get("post", {})
        title = post.get("title", "Без заголовка")
        body = post.get("body", "")
        message_text = f"{title}\n\n{body}"
        image_url = group_data.get("image_url")
        if image_url:
            context.bot.send_photo(chat_id=CHANNEL_ID, photo=image_url, caption=message_text)
        else:
            context.bot.send_message(chat_id=CHANNEL_ID, text=message_text)
        # Убираем кнопки с сообщения после публикации
        query.edit_message_reply_markup(None)
        query.answer("Пост опубликован.")
        
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
        # Генерируем новое изображение
        new_image_url = generate_image(illustration_prompt)
        group_data["image_url"] = new_image_url
        keyboard = [
            [
                InlineKeyboardButton("🔄 Другой текст", callback_data=f"again:{group_id}"),
                InlineKeyboardButton("🖼️ Другая картинка", callback_data=f"image:{group_id}")
            ],
            [
                InlineKeyboardButton("✅ Принять", callback_data=f"approve:{group_id}"),
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
        # Регенерация изображения
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
                    InlineKeyboardButton("✅ Принять", callback_data=f"approve:{group_id}"),
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
            
    elif action == "cancel":
        update_news_status(group_id, False)
        # Убираем кнопки с сообщения после отмены
        query.edit_message_reply_markup(None)
        query.answer("Сценарий отменён.")

def main():
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("checknews", check_news))
    dp.add_handler(CallbackQueryHandler(button_handler))
    
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
