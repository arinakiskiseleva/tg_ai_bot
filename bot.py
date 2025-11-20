import os
import time
import base64
import threading

import requests
from dotenv import load_dotenv
from flask import Flask

# -------------------- Flask для Render --------------------

app = Flask(__name__)


@app.route("/")
def index():
    return "Bot is running"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# -------------------- Конфиг и переменные --------------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_STT_URL = "https://api.openai.com/v1/audio/transcriptions"

# Gemini: текст-в-картинку через gemini-1.5-flash
GEMINI_IMAGE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-1.5-flash:generateContent"
)

MAX_MESSAGE_LENGTH = 3800  # запас до лимита телеги 4096 символов

# режимы пользователя: chat / image
user_modes = {}  # chat_id -> "text" или "image"

# добавочный промпт, чтобы ответ влезал в 4000 символов,
# но ИИ об этом НЕ говорил
LENGTH_HINT = (
    "\n\nОчень важно: сделай ответ таким, чтобы он помещался в пределах "
    "4000 символов в Telegram. Не упоминай это ограничение в тексте и "
    "ничего не пиши про количество символов."
)


# -------------------- Вспомогательные функции Telegram --------------------


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
    try:
        for part in split_message(text):
            requests.post(
                f"{TG_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": part,
                    "parse_mode": "Markdown",
                },
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


def send_photo(chat_id, image_bytes, mime_type="image/png"):
    """Отправляем фото в Telegram."""
    try:
        files = {
            "photo": ("image.png", image_bytes, mime_type),
        }
        r = requests.post(
            f"{TG_API}/sendPhoto",
            data={"chat_id": chat_id},
            files=files,
            timeout=60,
        )
        print("sendPhoto status:", r.status_code, r.text[:200])
    except Exception as e:
        print("Ошибка send_photo:", e)


def send_mode_keyboard(chat_id, current_mode="text"):
    """Клавиатура для переключения режимов."""
    if current_mode == "image":
        status = "Сейчас включён режим генерации картинок 🖼"
    else:
        status = "Сейчас включён текстовый режим 💬"

    keyboard = {
        "keyboard": [
            [{"text": "💬 Текст"}, {"text": "🖼 Картинки"}],
        ],
        "resize_keyboard": True,
    }

    send_message(chat_id, status)
    try:
        requests.post(
            f"{TG_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "Выбери режим работы:",
                "reply_markup": keyboard,
            },
            timeout=20,
        )
    except Exception as e:
        print("Ошибка отправки клавиатуры:", e)


# -------------------- OpenAI: текст + голос --------------------


def ask_ai(text):
    """Отправляем текст в OpenAI и получаем ответ."""
    try:
        user_text = text + LENGTH_HINT
        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": user_text}],
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
            f"{TG_API}/getFile",
            params={"file_id": file_id},
            timeout=30,
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
            timeout=90,
        )

        print("STT статус:", r.status_code)
        print("STT ответ:", r.text[:300])

        if r.status_code != 200:
            return None

        result = r.json()
        return result.get("text")
    except Exception as e:
        print("Ошибка transcribe_audio:", e)
        return None


# -------------------- Gemini: генерация картинки --------------------


def generate_image_with_gemini(prompt: str):
    """
    Генерируем картинку через Gemini 1.5 Flash:
    просим вернуть изображение, а не текст.
    Возвращаем (bytes, mime_type) или (None, error_text).
    """
    if not GEMINI_API_KEY:
        return None, "Не задан GEMINI_API_KEY в переменных окружения."

    params = {"key": GEMINI_API_KEY}

    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "image/png",
        },
    }

    try:
        r = requests.post(
            GEMINI_IMAGE_URL,
            params=params,
            json=body,
            timeout=90,
        )
        print("Gemini image status:", r.status_code)
        print("Gemini image raw:", r.text[:500])

        if r.status_code != 200:
            return None, "Не удалось сгенерировать изображение через Nano Banana."

        data = r.json()
        candidates = data.get("candidates")
        if not candidates:
            return None, "Gemini не вернул кандидатов для картинки."

        parts = candidates[0]["content"]["parts"]
        for p in parts:
            if "inlineData" in p:
                b64_data = p["inlineData"]["data"]
                mime_type = p["inlineData"].get("mimeType", "image/png")
                image_bytes = base64.b64decode(b64_data)
                return (image_bytes, mime_type), None

        return None, "Не удалось найти изображение в ответе Gemini."
    except Exception as e:
        print("Ошибка generate_image_with_gemini:", e)
        return None, "Ошибка при обращении к Nano Banana."


# -------------------- Основной цикл бота --------------------


def handle_text(chat_id, text):
    global user_modes

    # переключение режимов
    normalized = text.strip().lower()
    if "картин" in normalized:
        user_modes[chat_id] = "image"
        send_message(
            chat_id,
            "Режим картинок включён 🖼\n"
            "Опиши, что нужно нарисовать, а я сгенерирую изображение с Nano Banana.",
        )
        send_mode_keyboard(chat_id, current_mode="image")
        return

    if "текст" in normalized:
        user_modes[chat_id] = "text"
        send_message(
            chat_id,
            "Готов болтать в текстовом режиме 💬",
        )
        send_mode_keyboard(chat_id, current_mode="text")
        return

    # по умолчанию режим текстовый
    mode = user_modes.get(chat_id, "text")

    if mode == "image":
        # режим картинок: сначала пытаемся реально сгенерировать картинку
        send_typing(chat_id)
        img_result, err = generate_image_with_gemini(text)

        if img_result is not None:
            image_bytes, mime_type = img_result
            send_photo(chat_id, image_bytes, mime_type)
            return

        # если не получилось: даём промпт
        send_message(
            chat_id,
            "Не получилось сгенерировать картинку через Nano Banana.\n"
            "Сделаю для тебя промпт, который можно вставить в Gemini или другой генератор.",
        )

        prompt_text = (
            "Вот промпт для генерации изображения:\n\n"
            f"{text}\n\n"
            "Скопируй его и вставь в Gemini или другой генератор картинок 💜"
        )
        send_message(chat_id, prompt_text)
        return

    # режим текста: обычный диалог с GPT
    send_typing(chat_id)
    ai_answer = ask_ai(text)
    send_message(chat_id, ai_answer)


def main():
    print("Бот запущен: текст, голос и режим картинок через Nano Banana.")

    # запускаем Flask в отдельном потоке
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

            # /start
            if text and text.startswith("/start"):
                user_modes[chat_id] = "text"
                send_message(
                    chat_id,
                    "Привет: я твой ИИ бот CTRL+ART 💜\n"
                    "Я умею отвечать на текст и голосовые сообщения.\n"
                    "А ещё у меня есть режим картинок с Nano Banana.\n\n"
                    "Ниже есть меню: выбери режим работы.",
                )
                send_mode_keyboard(chat_id, current_mode="text")
                continue

            # голосовые
            if voice:
                send_typing(chat_id)

                file_id = voice["file_id"]
                audio_bytes = download_file(file_id)

                print(
                    "Размер аудио:",
                    0 if audio_bytes is None else len(audio_bytes),
                )

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

            # обычный текст
            if text:
                handle_text(chat_id, text)

        time.sleep(1)


if __name__ == "__main__":
    main()
