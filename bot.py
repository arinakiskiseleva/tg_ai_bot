import os
import time
import threading
import requests
from dotenv import load_dotenv
from flask import Flask

# -----------------------------
# Flask для Render
# -----------------------------
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# -----------------------------
# Настройки и переменные
# -----------------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_STT_URL = "https://api.openai.com/v1/audio/transcriptions"

# режимы бота по чатам: "text" или "image"
USER_MODE = {}

MAX_MESSAGE_LENGTH = 3800  # запас до лимита телеги 4096 символов

# -----------------------------
# Вспомогательные функции
# -----------------------------
def split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH):
    """
    Делим длинный текст на несколько сообщений, стараемся резать по строкам и пробелам.
    """
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
    try:
        for part in split_message(text):
            requests.post(
                f"{TG_API}/sendMessage",
                json={"chat_id": chat_id, "text": part},
                timeout=10,
            )
    except Exception as e:
        print("Ошибка send_message:", e)


def send_typing(chat_id):
    """Показываем "бот печатает..."."""
    try:
        requests.post(
            f"{TG_API}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
        )
    except Exception as e:
        print("Ошибка send_typing:", e)


def get_updates(offset=None):
    params = {"timeout": 20}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(f"{TG_API}/getUpdates", params=params)
        data = r.json()
        return data.get("result", [])
    except Exception as e:
        print("Ошибка get_updates:", e)
        return []


# -----------------------------
# OpenAI: общий вызов
# -----------------------------
def openai_chat(prompt_text: str, max_tokens: int = 600):
    """
    Общая функция для обращения к OpenAI.
    На вход: уже собранный текстовый промпт.
    """
    try:
        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "user", "content": prompt_text}
                ],
                "max_tokens": max_tokens,
            },
            timeout=40,
        )
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("Ошибка openai_chat:", e)
        return "Что то пошло не так при обращении к ИИ."


def ask_ai_text_answer(user_text: str):
    """
    Обычный текстовый режим.
    Ответ: живой, дружелюбный, до примерно 4000 символов.
    Бот не упоминает никакие лимиты.
    """
    prompt = (
        "Ты умный, дружелюбный ассистент для чата в Telegram. "
        "Отвечай по делу, но простым и живым языком. "
        "Старайся отвечать так, чтобы текст влезал примерно в 4000 символов, "
        "но не упоминай никаких ограничений и не говори про количество символов.\n\n"
        f"Вопрос пользователя: {user_text}"
    )
    return openai_chat(prompt_text=prompt, max_tokens=800)


def make_image_prompt(user_text: str):
    """
    Режим картинок.
    Возвращает один английский промпт для генерации изображения в Gemini
    или другом генераторе. Промпт без пояснений.
    """
    prompt = (
        "Сформулируй один краткий и выразительный промпт на английском языке "
        "для генерации изображения в нейросети по описанию пользователя. "
        "Формат: только сам промпт. "
        "Никаких объяснений, переводов и дополнительных фраз. "
        "Опиши стиль, важные объекты, атмосферу. "
        "Если описание похоже на персонажа, уточни позу или действие.\n\n"
        f"Описание пользователя на русском: {user_text}"
    )
    return openai_chat(prompt_text=prompt, max_tokens=300)


# -----------------------------
# Работа с голосовыми
# -----------------------------
def download_file(file_id):
    """Скачиваем голосовое по file_id и возвращаем байты."""
    try:
        r = requests.get(f"{TG_API}/getFile", params={"file_id": file_id})
        file_data = r.json()
        file_path = file_data["result"]["file_path"]

        file_url = f"{TG_FILE_API}/{file_path}"
        file_resp = requests.get(file_url)
        return file_resp.content
    except Exception as e:
        print("Ошибка download_file:", e)
        return None


def transcribe_audio(audio_bytes):
    """Отправляем аудио в OpenAI Whisper и получаем текст."""
    try:
        files = {
            "file": ("audio.ogg", audio_bytes, "audio/ogg")
        }
        data = {
            "model": "whisper-1",
            "language": "ru",
            "response_format": "json",
        }

        r = requests.post(
            OPENAI_STT_URL,
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            files=files,
            data=data,
            timeout=60,
        )

        print("STT статус:", r.status_code)
        print("STT ответ:", r.text)

        if r.status_code != 200:
            return None

        result = r.json()
        return result.get("text")
    except Exception as e:
        print("Ошибка transcribe_audio:", e)
        return None


# -----------------------------
# Основной цикл бота
# -----------------------------
def main():
    print("Бот запущен: принимает текст и голос, показывает typing, есть режимы текста и картинок.")

    # сразу запускаем Flask в отдельном потоке для Render
    threading.Thread(target=run_web, daemon=True).start()

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
            voice = message.get("voice")

            print("Сообщение:", chat_id, "text:", text, "voice:", bool(voice))

            # режим по умолчанию: текст
            if chat_id not in USER_MODE:
                USER_MODE[chat_id] = "text"

            # обработка /start
            if text and text.startswith("/start"):
                USER_MODE[chat_id] = "text"

                # простая клавиатура с режимами
                keyboard = {
                    "keyboard": [
                        [{"text": "💬 Текст"}, {"text": "🖼 Картинки"}]
                    ],
                    "resize_keyboard": True
                }

                try:
                    requests.post(
                        f"{TG_API}/sendMessage",
                        json={
                            "chat_id": chat_id,
                            "text": (
                                "Привет: я твой ИИ бот CTRL+ART 💜\n"
                                "Я умею отвечать на текст и голосовые сообщения.\n\n"
                                "Ниже есть меню: выбери режим работы."
                            ),
                            "reply_markup": keyboard,
                        },
                    )
                except Exception as e:
                    print("Ошибка отправки /start:", e)
                continue

            # переключение режимов по кнопкам
            if text in ("💬 Текст", "Текст"):
                USER_MODE[chat_id] = "text"
                send_message(chat_id, "Готов болтать в текстовом режиме 💬")
                continue

            if text in ("🖼 Картинки", "Картинки"):
                USER_MODE[chat_id] = "image"
                send_message(
                    chat_id,
                    "Сейчас включён режим генерации картинок 🖼\n"
                    "Опиши, что нужно нарисовать, как для промпта. "
                    "Я подготовлю для тебя красивый промпт, который можно вставить в Gemini.",
                )
                continue

            # Голосовое сообщение: всегда расшифровываем и отвечаем текстом
            if voice:
                send_typing(chat_id)

                file_id = voice["file_id"]
                audio_bytes = download_file(file_id)

                print("Размер аудио:", 0 if audio_bytes is None else len(audio_bytes))

                if not audio_bytes:
                    send_message(chat_id, "Не смог скачать голосовое сообщение 😢")
                    continue

                transcript = transcribe_audio(audio_bytes)
                if not transcript:
                    send_message(chat_id, "Не удалось распознать голос 😔")
                    continue

                send_typing(chat_id)
                ai_answer = ask_ai_text_answer(transcript)

                send_message(
                    chat_id,
                    f"Ты сказала: {transcript}\n\nМой ответ:\n{ai_answer}",
                )
                continue

            # Обычный текст
            if text:
                mode = USER_MODE.get(chat_id, "text")

                # текстовый режим
                if mode == "text":
                    send_typing(chat_id)
                    ai_answer = ask_ai_text_answer(text)
                    send_message(chat_id, ai_answer)
                    continue

                # режим картинок: делаем промпт
                if mode == "image":
                    send_typing(chat_id)
                    prompt_for_image = make_image_prompt(text)

                    reply = (
                        "Вот промпт для генерации изображения:\n\n"
                        f"{prompt_for_image}\n\n"
                        "Скопируй его и вставь в Gemini или другой генератор картинок 💜"
                    )
                    send_message(chat_id, reply)
                    continue

        time.sleep(1)


if __name__ == "__main__":
    main()
