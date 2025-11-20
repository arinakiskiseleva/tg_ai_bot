import os
import time
import threading
import base64

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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_STT_URL = "https://api.openai.com/v1/audio/transcriptions"

# официальный REST-эндпоинт Nano Banana (Gemini 2.5 Flash Image)
GEMINI_IMAGE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-2.5-flash-image:generateContent"
)

# режимы бота по чатам: "text" или "image"
USER_MODE = {}

MAX_MESSAGE_LENGTH = 3800  # запас до лимита телеги

# -----------------------------
# Вспомогательные функции
# -----------------------------
def split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH):
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
        first = True
        for part in split_message(text):
            payload = {"chat_id": chat_id, "text": part}
            if first and reply_markup is not None:
                payload["reply_markup"] = reply_markup
                first = False

            requests.post(
                f"{TG_API}/sendMessage",
                json=payload,
                timeout=10,
            )
    except Exception as e:
        print("Ошибка send_message:", e)


def send_typing(chat_id):
    try:
        requests.post(
            f"{TG_API}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=10,
        )
    except Exception as e:
        print("Ошибка send_typing:", e)


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


# -----------------------------
# OpenAI: чат и голос
# -----------------------------
def openai_chat(prompt_text: str, max_tokens: int = 600):
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
            timeout=60,
        )
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("Ошибка openai_chat:", e)
        return "Что то пошло не так при обращении к ИИ."


def ask_ai_text_answer(user_text: str):
    prompt = (
        "Ты умный, дружелюбный ассистент для чата в Telegram. "
        "Отвечай по делу, но простым и живым языком. "
        "Старайся отвечать так, чтобы текст влезал примерно в 4000 символов, "
        "но не упоминай никаких ограничений и не говори про количество символов.\n\n"
        f"Вопрос пользователя: {user_text}"
    )
    return openai_chat(prompt_text=prompt, max_tokens=800)


def download_file(file_id):
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
            timeout=120,
        )

        print("STT статус:", r.status_code)
        print("STT ответ:", r.text[:400])

        if r.status_code != 200:
            return None

        result = r.json()
        return result.get("text")
    except Exception as e:
        print("Ошибка transcribe_audio:", e)
        return None


# -----------------------------
# Nano Banana (Gemini) — генерация картинок
# -----------------------------
def generate_image_bytes(prompt: str):
    """Генерация картинки через Nano Banana (Gemini 2.5 Flash Image)."""
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY не задан")
        return None

    try:
        r = requests.post(
            GEMINI_IMAGE_URL,
            headers={
                "x-goog-api-key": GEMINI_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "contents": [
                    {
                        "parts": [
                            {"text": prompt}
                        ]
                    }
                ]
            },
            timeout=120,
        )

        print("Gemini image status:", r.status_code)
        print("Gemini image raw:", r.text[:400])

        if r.status_code != 200:
            return None

        data = r.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return None

        parts = candidates[0].get("content", {}).get("parts", [])
        for part in parts:
            inline_data = (
                part.get("inlineData")
                or part.get("inline_data")
            )
            if inline_data and "data" in inline_data:
                b64 = inline_data["data"]
                try:
                    return base64.b64decode(b64)
                except Exception as e:
                    print("Ошибка декодирования base64:", e)
                    return None

        return None
    except Exception as e:
        print("Ошибка generate_image_bytes:", e)
        return None


def send_image(chat_id, prompt: str):
    img_bytes = generate_image_bytes(prompt)
    if not img_bytes:
        send_message(
            chat_id,
            "Не получилось сгенерировать картинку с Nano Banana. "
            "Проверь, что GEMINI_API_KEY задан в Render и у аккаунта есть доступ к image generation.",
        )
        return

    try:
        files = {"photo": ("image.png", img_bytes, "image/png")}
        r = requests.post(
            f"{TG_API}/sendPhoto",
            data={"chat_id": chat_id},
            files=files,
            timeout=60,
        )
        if r.status_code != 200:
            print("Ошибка sendPhoto:", r.status_code, r.text)
            send_message(chat_id, "Картинку сгенерировала, но не смогла отправить в Telegram.")
    except Exception as e:
        print("Ошибка send_image:", e)
        send_message(chat_id, "Произошла ошибка при отправке картинки.")


# -----------------------------
# Основной цикл бота
# -----------------------------
def main():
    print(
        "Бот запущен: текст, голос и режим картинок через Nano Banana."
    )

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

            if chat_id not in USER_MODE:
                USER_MODE[chat_id] = "text"

            # /start
            if text and text.startswith("/start"):
                USER_MODE[chat_id] = "text"

                keyboard = {
                    "keyboard": [
                        [{"text": "💬 Текст"}, {"text": "🖼 Картинки"}]
                    ],
                    "resize_keyboard": True
                }

                send_message(
                    chat_id,
                    "Привет: я твой ИИ бот CTRL+ART 💜\n"
                    "Я умею отвечать на текст и голосовые сообщения, "
                    "а ещё генерировать картинки через Nano Banana.\n\n"
                    "Ниже есть меню: выбери режим работы.",
                    reply_markup=keyboard,
                )
                continue

            # переключение режимов
            if text in ("💬 Текст", "Текст"):
                USER_MODE[chat_id] = "text"
                send_message(chat_id, "Режим текста включен 💬")
                continue

            if text in ("🖼 Картинки", "Картинки"):
                USER_MODE[chat_id] = "image"
                send_message(
                    chat_id,
                    "Режим картинок включен 🖼\n"
                    "Опиши, что нужно нарисовать, а я сгенерирую изображение с Nano Banana.",
                )
                continue

            mode = USER_MODE.get(chat_id, "text")

            # Голос: всегда расшифровываем и отвечаем текстом
            if voice:
                send_typing(chat_id)

                file_id = voice["file_id"]
                audio_bytes = download_file(file_id)

                print("Размер аудио:", 0 if audio_bytes is None else len(audio_bytes))

                if not audio_bytes:
                    send_message(chat_id, "Не смог скачать голосовое сообщение.")
                    continue

                transcript = transcribe_audio(audio_bytes)
                if not transcript:
                    send_message(chat_id, "Не удалось распознать голос.")
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
                if mode == "image":
                    send_typing(chat_id)
                    send_image(chat_id, text)
                else:
                    send_typing(chat_id)
                    ai_answer = ask_ai_text_answer(text)
                    send_message(chat_id, ai_answer)

        time.sleep(1)


if __name__ == "__main__":
    main()
