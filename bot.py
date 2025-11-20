import os
import time
import base64
import threading
import requests
from dotenv import load_dotenv
from flask import Flask

# ---------- Flask для Render ----------

app = Flask(__name__)


@app.route("/")
def index():
    return "Bot is running"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# ---------- Настройки и переменные ----------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

# Лимит Телеграма 4096, берем небольшой запас
MAX_MESSAGE_LENGTH = 3800

# Режим чата на пользователя: "memory" или "temp"
chat_mode = {}  # chat_id -> str
# История для основного чата
chat_history = {}  # chat_id -> list[dict]


# ---------- Вспомогательные функции ----------

def split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH):
    """Делим длинный текст на несколько сообщений."""
    if text is None:
        return []

    text = str(text)
    parts = []

    while len(text) > max_len:
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_len)
            if split_at == -1:
                split_at = max_len

        parts.append(text[:split_at].rstrip())
        text = text[split_at:].lstrip()

    if text:
        parts.append(text)

    return parts


def send_message(chat_id, text):
    """Отправляем текст, при необходимости режем на части."""
    try:
        for part in split_message(text):
            requests.post(
                f"{TG_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": part,
                },
                timeout=10,
            )
    except Exception as e:
        print("Ошибка send_message:", e)


def send_menu(chat_id):
    """Отправляем меню выбора режима."""
    text = (
        "Я умею отвечать на текст и анализировать картинки.\n\n"
        "Выбери режим работы:\n"
        "💬 Основной чат: бот запоминает контекст беседы.\n"
        "⏳ Временный чат: ответ только на текущее сообщение, без памяти."
    )
    try:
        requests.post(
            f"{TG_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "reply_markup": {
                    "keyboard": [
                        [{"text": "💬 Основной чат"}, {"text": "⏳ Временный чат"}],
                    ],
                    "resize_keyboard": True,
                },
            },
            timeout=10,
        )
    except Exception as e:
        print("Ошибка send_menu:", e)


def send_typing(chat_id):
    """Показываем, что бот печатает."""
    try:
        requests.post(
            f"{TG_API}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=5,
        )
    except Exception as e:
        print("Ошибка send_typing:", e)


def get_updates(offset=None):
    """Читаем апдейты от Телеграма."""
    params = {"timeout": 20}
    if offset is not None:
        params["offset"] = offset

    try:
        r = requests.get(f"{TG_API}/getUpdates", params=params, timeout=25)
        data = r.json()
        return data.get("result", [])
    except Exception as e:
        print("Ошибка get_updates:", e)
        return []


def download_file(file_id):
    """Скачиваем файл по file_id и возвращаем байты."""
    try:
        r = requests.get(f"{TG_API}/getFile", params={"file_id": file_id}, timeout=10)
        file_data = r.json()
        file_path = file_data["result"]["file_path"]

        file_url = f"{TG_FILE_API}/{file_path}"
        file_resp = requests.get(file_url, timeout=30)
        return file_resp.content
    except Exception as e:
        print("Ошибка download_file:", e)
        return None


# ---------- OpenAI ----------

def build_system_message():
    """Общий системный промпт."""
    return {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": (
                    "Ты ИИ помощник CTRL+ART для Телеграм.\n"
                    "Отвечай по-русски, дружелюбно, как креативный собеседник.\n"
                    "Пиши простым текстом без Markdown разметки: не используй звездочки, решетки, подчеркивание.\n"
                    "Структурируй текст абзацами и списками, но только обычным текстом.\n"
                    "Следи за длиной: ответ должен уместиться в одно сообщение Телеграм до 4000 символов, "
                    "но не упоминай это ограничение и не говори про символы.\n"
                    "Если пользователь прислал картинку, сначала опиши, что на ней, затем дай полезные комментарии и идеи."
                ),
            }
        ],
    }


def ask_openai(chat_id, mode, user_content):
    """
    user_content: список блоков для сообщения пользователя
    (для текста: [{"type":"text","text": "..."}]
     для картинки: текст + image_url).
    """
    system_msg = build_system_message()
    user_msg = {"role": "user", "content": user_content}

    # История только в режиме "memory"
    if mode == "memory":
        history = chat_history.get(chat_id, [])
        messages = [system_msg] + history + [user_msg]
    else:
        messages = [system_msg, user_msg]

    try:
        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",  # модель с поддержкой картинок
                "messages": messages,
                "max_tokens": 4000,
            },
            timeout=60,
        )
        data = r.json()
        answer = data["choices"][0]["message"]["content"]

        # Сохраняем историю для основного чата
        if mode == "memory":
            ai_msg = {
                "role": "assistant",
                "content": [{"type": "text", "text": answer}],
            }
            history = history + [user_msg, ai_msg]
            # Ограничим историю чтобы не раздувалась
            chat_history[chat_id] = history[-20:]

        return answer

    except Exception as e:
        print("Ошибка ask_openai:", e)
        return "Что то пошло не так при обращении к ИИ."


# ---------- Основной цикл бота ----------

def main():
    print("Бот запущен: текст, картинки, режимы памяти.")

    offset = None

    while True:
        updates = get_updates(offset)

        for upd in updates:
            offset = upd["update_id"] + 1
            print("Получен апдейт:", upd)

            message = upd.get("message")
            if not message:
                continue

            chat_id = message["chat"]["id"]
            text = message.get("text")
            photos = message.get("photo")

            # Устанавливаем режим по умолчанию (основной чат)
            if chat_id not in chat_mode:
                chat_mode[chat_id] = "memory"

            # Обработка /start
            if text and text.startswith("/start"):
                chat_mode[chat_id] = "memory"
                chat_history.pop(chat_id, None)
                send_message(
                    chat_id,
                    "Привет: я твой ИИ бот CTRL+ART 💜\n"
                    "Я умею отвечать на текст и анализировать картинки.\n"
                    "По умолчанию включен основной чат с памятью.\n",
                )
                send_menu(chat_id)
                continue

            # Выбор режима через меню
            if text == "💬 Основной чат":
                chat_mode[chat_id] = "memory"
                send_message(
                    chat_id,
                    "Основной чат включен.\n"
                    "Я буду помнить контекст нашей беседы внутри этого диалога.",
                )
                continue

            if text == "⏳ Временный чат":
                chat_mode[chat_id] = "temp"
                send_message(
                    chat_id,
                    "Временный чат включен.\n"
                    "Я отвечаю только на текущее сообщение без памяти.",
                )
                continue

            mode = chat_mode.get(chat_id, "memory")

            # Фото (анализ картинки)
            if photos:
                send_typing(chat_id)

                # Берем самое большое фото
                file_id = photos[-1]["file_id"]
                img_bytes = download_file(file_id)

                if not img_bytes:
                    send_message(chat_id, "Не получилось скачать картинку.")
                    continue

                # Кодируем в base64 data URL
                try:
                    b64 = base64.b64encode(img_bytes).decode("utf-8")
                    data_url = f"data:image/jpeg;base64,{b64}"
                except Exception as e:
                    print("Ошибка base64:", e)
                    send_message(chat_id, "Не получилось обработать картинку.")
                    continue

                caption = text or "Пожалуйста, опиши эту картинку и дай комментарии."
                user_content = [
                    {"type": "text", "text": caption},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]

                answer = ask_openai(chat_id, mode, user_content)
                send_message(chat_id, answer)
                continue

            # Обычный текст
            if text:
                send_typing(chat_id)
                user_content = [{"type": "text", "text": text}]
                answer = ask_openai(chat_id, mode, user_content)
                send_message(chat_id, answer)
                continue

        time.sleep(1)


# ---------- Запуск ----------

if __name__ == "__main__":
    # Запускаем Flask в отдельном потоке для Render
    web_thread = threading.Thread(target=run_web)
    web_thread.daemon = True
    web_thread.start()

    # Запускаем бота
    main()
