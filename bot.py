import os
import time
import requests
from dotenv import load_dotenv

from flask import Flask
import threading

# --------------------------------------------------
# Flask: чтобы Render видел, что сервис жив
# --------------------------------------------------

app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Запускаем Flask в отдельном потоке
threading.Thread(target=run_web, daemon=True).start()

# --------------------------------------------------
# Настройки бота и OpenAI
# --------------------------------------------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_STT_URL = "https://api.openai.com/v1/audio/transcriptions"

# Режимы по чатам: "text" или "image"
user_modes = {}  # {chat_id: "text" | "image"}

# --------------------------------------------------
# Вспомогательные функции для Telegram
# --------------------------------------------------

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


def send_message(chat_id, text, reply_markup=None):
    """Отправка сообщения, при необходимости с клавиатурой."""
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        requests.post(
            f"{TG_API}/sendMessage",
            json=payload,
            timeout=10,
        )
    except Exception as e:
        print("Ошибка send_message:", e)


def send_typing(chat_id):
    """Показываем 'бот печатает...'."""
    try:
        requests.post(
            f"{TG_API}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
        )
    except Exception as e:
        print("Ошибка send_typing:", e)

# --------------------------------------------------
# Работа с OpenAI: текст
# --------------------------------------------------

def ask_ai(text):
    """Отправляем текст в OpenAI и получаем ответ."""
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
                    {
                        "role": "system",
                        "content": (
                            "Ты дружелюбный русскоязычный ассистент. "
                            "Отвечай понятно, красиво и по существу. "
                            "Не упоминай никаких лимитов, символов, правил и ограничений. "
                            "Просто формируй ответ так, чтобы он полностью умещался "
                            "в сообщении Telegram примерно до 4000 символов."
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                # Лимит токенов: чтобы модель не раздувалась
                "max_tokens": 3500,
            },
        )
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("Ошибка ask_ai:", e)
        return "Что то пошло не так при обращении к ИИ."

# --------------------------------------------------
# Голос: скачивание и распознавание
# --------------------------------------------------

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
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
            },
            files=files,
            data=data,
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

# --------------------------------------------------
# Основная логика бота
# --------------------------------------------------

def get_main_keyboard():
    """Клавиатура с режимами."""
    return {
        "keyboard": [
            [
                {"text": "💬 Текст"},
                {"text": "🖼 Картинки"},
            ]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def main():
    print("Бот запущен: принимает текст и голосовые, показывает 'печатает'.")

    offset = None

    while True:
        updates = get_updates(offset)

        for upd in updates:
            offset = upd["update_id"] + 1
            print("Получен апдейт:", upd)

            message = upd.get("message")
            if not message:
                continue

            chat = message.get("chat", {})
            chat_id = chat.get("id")
            if not chat_id:
                continue

            text = message.get("text")
            voice = message.get("voice")

            print("Сообщение:", chat_id, "text:", text, "voice:", bool(voice))

            # ----------------------------------------
            # /start: привет и показ меню
            # ----------------------------------------
            if text and text.startswith("/start"):
                user_modes[chat_id] = "text"
                kb = get_main_keyboard()
                send_message(
                    chat_id,
                    "Привет: я твой ИИ бот CTRL+ART 💜\n"
                    "Я умею отвечать на текст и голосовые сообщения.\n"
                    "Ниже есть меню: выбери режим работы.",
                    reply_markup=kb,
                )
                continue

            # ----------------------------------------
            # Нажатия по кнопкам меню
            # ----------------------------------------
            if text == "💬 Текст":
                user_modes[chat_id] = "text"
                kb = get_main_keyboard()
                send_message(
                    chat_id,
                    "Готов болтать в текстовом режиме 💬",
                    reply_markup=kb,
                )
                continue

            if text == "🖼 Картинки":
                user_modes[chat_id] = "image"
                kb = get_main_keyboard()
                send_message(
                    chat_id,
                    "Режим генерации картинок включен 🖼\n"
                    "Пока что я ещё не подключен к Gemini, но меню уже работает.",
                    reply_markup=kb,
                )
                continue

            # Узнаем текущий режим для чата
            mode = user_modes.get(chat_id, "text")

            # ----------------------------------------
            # Голосовое сообщение: пока в любом режиме
            # ----------------------------------------
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
                ai_answer = ask_ai(transcript)

                send_message(
                    chat_id,
                    f"Ты сказала: {transcript}\n\nМой ответ:\n{ai_answer}",
                )
                continue

            # ----------------------------------------
            # Обычный текст
            # ----------------------------------------
            if text:
                # Режим картинок: пока просто заглушка
                if mode == "image":
                    send_message(
                        chat_id,
                        "Сейчас включен режим генерации картинок 🖼\n"
                        "Чуть позже я подключу сюда Gemini и буду рисовать по твоим описаниям 💜",
                    )
                    continue

                # Режим текста
                send_typing(chat_id)
                ai_answer = ask_ai(text)
                send_message(chat_id, ai_answer)
                continue

        time.sleep(1)


if __name__ == "__main__":
    main()
