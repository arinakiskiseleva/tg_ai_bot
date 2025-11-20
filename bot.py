import os
import time
import base64
import threading

import requests
from dotenv import load_dotenv

from flask import Flask

# ----------------- Flask для Render (проверка живости) -----------------

app = Flask(__name__)


@app.route("/")
def index():
    return "Bot is running"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# ----------------- Настройки и ключи -----------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # добавь в .env и на Render

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_STT_URL = "https://api.openai.com/v1/audio/transcriptions"

# Пример эндпоинта для Imagen 3: обязательно проверь актуальный в доках Gemini
GEMINI_IMAGE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "imagen-3.0-generate-001:generateImage"
)

# Лимит Телеги – 4096, берем запас
MAX_MESSAGE_LENGTH = 3800

# Режимы по чатам: "text" или "image"
chat_modes = {}  # {chat_id: "text" | "image"}


# ----------------- Вспомогательные функции Telegram -----------------


def get_updates(offset=None):
    params = {"timeout": 20}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(f"{TG_API}/getUpdates", params=params, timeout=30)
        data = r.json()
        return data.get("result", [])
    except Exception as e:
        print("Ошибка get_updates:", e)
        return []


def split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH):
    """
    Делим длинный текст на несколько сообщений, стараемся резать по строкам/пробелам.
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


def send_message(chat_id, text, reply_markup=None):
    try:
        for part in split_message(text):
            payload = {"chat_id": chat_id, "text": part}
            if reply_markup is not None:
                payload["reply_markup"] = reply_markup
                # клавиатуру отправляем только с первым сообщением
                reply_markup = None

            requests.post(
                f"{TG_API}/sendMessage",
                json=payload,
                timeout=20,
            )
    except Exception as e:
        print("Ошибка send_message:", e)


def send_typing(chat_id):
    """Показываем 'бот печатает...'."""
    try:
        requests.post(
            f"{TG_API}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=10,
        )
    except Exception as e:
        print("Ошибка send_typing:", e)


def send_menu(chat_id):
    """Клавиатура выбора режима: текст / картинки."""
    keyboard = {
        "keyboard": [
            [
                {"text": "💬 Текст"},
                {"text": "🖼 Картинки"},
            ]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }
    send_message(chat_id, "Выбери режим работы бота:", reply_markup=keyboard)


# ----------------- OpenAI: текст и расшифровка голоса -----------------


def ask_ai(user_text: str) -> str:
    """
    Отправляем текст в OpenAI и получаем ответ.
    Внутри промпта говорим про лимит, но просим модель НЕ писать об этом.
    """
    prompt = (
        "Ты отвечаешь пользователю в чате Telegram на русском языке. "
        "Твой полный ответ вместе со всеми символами и форматированием "
        "должен влезать в ограничение Telegram примерно 4000 символов. "
        "Пиши по делу, структурированно, без лишней воды и без упоминаний "
        "ограничения по длине сообщения или числа символов. "
        "Не объясняй, что ты стараешься уместиться в лимит, просто делай это.\n\n"
        "Сообщение пользователя:\n"
        f"{user_text}"
    )

    try:
        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 700,
            },
            timeout=60,
        )
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("Ошибка ask_ai:", e)
        return "Что-то пошло не так при обращении к ИИ."


def download_file(file_id):
    """Скачиваем голосовое по file_id и возвращаем байты."""
    try:
        r = requests.get(
            f"{TG_API}/getFile", params={"file_id": file_id}, timeout=20
        )
        file_data = r.json()
        file_path = file_data["result"]["file_path"]

        file_url = f"{TG_FILE_API}/{file_path}"
        file_resp = requests.get(file_url, timeout=60)
        return file_resp.content
    except Exception as e:
        print("Ошибка download_file:", e)
        return None


def transcribe_audio(audio_bytes):
    """Отправляем аудио в OpenAI Whisper и получаем текст."""
    try:
        files = {
            "file": ("audio.ogg", audio_bytes, "audio/ogg"),
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
            timeout=120,
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


# ----------------- Gemini: генерация картинок -----------------


def generate_image_bytes(prompt: str):
    """
    Генерация картинки через Gemini / Imagen.

    ВАЖНО:
    1: эндпоинт и формат ответа могут меняться:
       обязательно сверяй с актуальной документацией Google AI Studio.
    2: если структура другая – смотри r.text в логах и поправь ключи.
    """
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY не задан")
        return None

    try:
        r = requests.post(
            GEMINI_IMAGE_URL,
            params={"key": GEMINI_API_KEY},
            json={
                "prompt": {"text": prompt},
                # можно добавить другие настройки: размер, количество, и т.д.
            },
            timeout=120,
        )
        print("Gemini image status:", r.status_code)
        print("Gemini image raw response:", r.text[:500])

        if r.status_code != 200:
            return None

        data = r.json()
        # примерная структура: { "images": [ { "bytesBase64Encoded": "..." } ] }
        images = data.get("images") or []
        if not images:
            return None

        b64 = images[0].get("bytesBase64Encoded")
        if not b64:
            return None

        return base64.b64decode(b64)

    except Exception as e:
        print("Ошибка generate_image_bytes:", e)
        return None


def send_image(chat_id, prompt: str):
    """
    Генерируем картинку и отправляем её как фото.
    """
    img_bytes = generate_image_bytes(prompt)
    if not img_bytes:
        send_message(
            chat_id,
            "Не удалось сгенерировать изображение. "
            "Проверь ключ Gemini и структуру ответа API.",
        )
        return

    try:
        files = {"photo": ("image.png", img_bytes)}
        requests.post(
            f"{TG_API}/sendPhoto",
            data={"chat_id": chat_id},
            files=files,
            timeout=60,
        )
    except Exception as e:
        print("Ошибка send_image:", e)
        send_message(chat_id, "Картинку сгенерировала, но не смогла отправить 😢")


# ----------------- Основной цикл бота -----------------


def main():
    print(
        "Бот запущен: принимает текст и голос, показывает typing. "
        "Есть режим чата и режим генерации картинок."
    )

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

            # /start
            if text and text.startswith("/start"):
                chat_modes[chat_id] = "text"
                hello = (
                    "Привет: я твой ИИ-бот CTRL+ART 💜\n\n"
                    "Я умею:\n"
                    "• общаться в текстовом режиме;\n"
                    "• расшифровывать голосовые и отвечать на них;\n"
                    "• генерировать картинки через Gemini (по описанию).\n\n"
                    "Выбери ниже режим работы:"
                )
                send_menu(chat_id)
                send_message(chat_id, hello)
                continue

            # Переключение режимов клавиатурой
            if text in ("💬 Текст", "Текст"):
                chat_modes[chat_id] = "text"
                send_message(chat_id, "Готова болтать в текстовом режиме 💬")
                continue

            if text in ("🖼 Картинки", "Картинки"):
                chat_modes[chat_id] = "image"
                send_message(
                    chat_id,
                    "Сейчас включён режим генерации картинок 🖼\n"
                    "Опиши, что нужно нарисовать, как для промпта.",
                )
                continue

            # Режим по умолчанию
            mode = chat_modes.get(chat_id, "text")

            # Голос всегда расшифровываем и отвечаем текстом
            if voice:
                send_typing(chat_id)

                file_id = voice["file_id"]
                audio_bytes = download_file(file_id)

                print(
                    "Размер аудио:",
                    0 if audio_bytes is None else len(audio_bytes),
                )

                if not audio_bytes:
                    send_message(
                        chat_id, "Не смогла скачать голосовое сообщение 😢"
                    )
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

            # Обычный текст
            if text:
                # Режим картинок
                if mode == "image":
                    send_typing(chat_id)
                    send_image(chat_id, text)
                else:
                    # Режим чата
                    send_typing(chat_id)
                    ai_answer = ask_ai(text)
                    send_message(chat_id, ai_answer)

        time.sleep(1)


# ----------------- Точка входа -----------------

if __name__ == "__main__":
    # Запускаем Flask в отдельном потоке для Render
    t = threading.Thread(target=run_web, daemon=True)
    t.start()

    # Основной цикл бота
    main()
