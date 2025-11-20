import os
import time
import base64
import threading

import requests
from dotenv import load_dotenv
from flask import Flask

# Flask: простой веб хелсчек для Render
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Запускаем Flask в отдельном потоке
threading.Thread(target=run_web, daemon=True).start()

# Загружаем переменные окружения
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # для картинок

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_STT_URL = "https://api.openai.com/v1/audio/transcriptions"

# Gemini: эндпоинт для генерации картинок
if GEMINI_API_KEY:
    GEMINI_IMAGE_URL = (
        "https://generativelanguage.googleapis.com/v1beta/"
        f"models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    )
else:
    GEMINI_IMAGE_URL = None

# Режимы бота
MODE_TEXT = "text"
MODE_IMAGE = "image"

# Память по пользователям: какой режим включен
user_modes = {}

# Клавиатура меню
MAIN_KEYBOARD = {
    "keyboard": [
        [
            {"text": "🤖 Текстовый режим"},
            {"text": "🎨 Картинки Gemini"},
        ]
    ],
    "resize_keyboard": True,
}

MAX_MESSAGE_LENGTH = 3800  # запас до лимита телеги 4096 символов


def get_updates(offset=None):
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


def split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH):
    """Режем длинный текст на кусочки, стараемся по строкам или пробелам."""
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
    """Отправляем текст: если он длинный, шлем несколько сообщений."""
    try:
        parts = split_message(text)
        for i, part in enumerate(parts):
            payload = {"chat_id": chat_id, "text": part}
            if i == 0 and reply_markup is not None:
                payload["reply_markup"] = reply_markup

            requests.post(
                f"{TG_API}/sendMessage",
                json=payload,
                timeout=10,
            )
    except Exception as e:
        print("Ошибка send_message:", e)


def send_photo(chat_id, image_bytes, caption=None):
    """Отправка картинки в Telegram."""
    try:
        files = {
            "photo": ("image.png", image_bytes, "image/png"),
        }
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption

        r = requests.post(
            f"{TG_API}/sendPhoto",
            data=data,
            files=files,
            timeout=60,
        )
        if r.status_code != 200:
            print("Ошибка send_photo:", r.status_code, r.text)
    except Exception as e:
        print("Ошибка send_photo:", e)


def send_typing(chat_id):
    """Показываем: бот печатает."""
    try:
        requests.post(
            f"{TG_API}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=10,
        )
    except Exception as e:
        print("Ошибка send_typing:", e)


def ask_ai(text):
    """Текстовый ответ от OpenAI: просим влезть в лимит Телеграма."""
    try:
        prompt = (
            text
            + "\n\nПожалуйста: сделай ответ, который целиком уместится "
            "в 4000 символов сообщения в Telegram."
        )

        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 800,
            },
            timeout=60,
        )
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("Ошибка ask_ai:", e)
        return "Что то пошло не так при обращении к ИИ."


def generate_image_with_gemini(prompt: str):
    """Генерация картинки через Google Gemini: возвращаем байты PNG."""
    if not GEMINI_IMAGE_URL:
        print("GEMINI_API_KEY не задан: не могу генерировать картинки")
        return None

    try:
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                # Важно: по актуальной доке этот параметр говорит модели
                # вернуть картинку в виде PNG в виде base64
                "responseMimeType": "image/png",
            },
        }

        r = requests.post(GEMINI_IMAGE_URL, json=payload, timeout=90)
        if r.status_code != 200:
            print("Ошибка Gemini:", r.status_code, r.text)
            return None

        data = r.json()
        try:
            inline_data = (
                data["candidates"][0]["content"]["parts"][0]["inlineData"]
            )
            img_b64 = inline_data["data"]
        except Exception as e:
            print("Ошибка разбора ответа Gemini:", e, data)
            return None

        return base64.b64decode(img_b64)
    except Exception as e:
        print("Ошибка generate_image_with_gemini:", e)
        return None


def download_file(file_id):
    """Скачиваем голосовое по file_id и возвращаем байты."""
    try:
        r = requests.get(
            f"{TG_API}/getFile",
            params={"file_id": file_id},
            timeout=20,
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
        print("STT ответ:", r.text[:500])

        if r.status_code != 200:
            return None

        result = r.json()
        return result.get("text")
    except Exception as e:
        print("Ошибка transcribe_audio:", e)
        return None


def set_mode(chat_id, mode):
    """Сохраняем режим пользователя."""
    if mode not in (MODE_TEXT, MODE_IMAGE):
        return
    user_modes[chat_id] = mode
    print(f"Режим для {chat_id}: {mode}")


def get_mode(chat_id):
    """Получаем режим пользователя: по умолчанию текст."""
    return user_modes.get(chat_id, MODE_TEXT)


def handle_text_message(chat_id, text):
    """Обработка текстовых сообщений с учетом режима и меню."""
    lowered = text.lower().strip()

    # Команды и кнопки меню
    if lowered.startswith("/start"):
        set_mode(chat_id, MODE_TEXT)
        send_message(
            chat_id,
            "Привет: я твой ИИ бот 🤖💜\n"
            "Я умею отвечать на текст и голосовые сообщения.\n\n"
            "Снизу появится меню: можешь выбрать режим: "
            "текст или генерация картинок Gemini.",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    if lowered in ("меню", "/menu"):
        send_message(
            chat_id,
            "Выбери режим работы:",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    if "текстовый режим" in lowered:
        set_mode(chat_id, MODE_TEXT)
        send_message(chat_id, "Режим текста включен: пиши что угодно 💬")
        return

    if "картинки gemini" in lowered or "картинки" == lowered:
        set_mode(chat_id, MODE_IMAGE)
        send_message(
            chat_id,
            "Режим картинок включен 🎨\n"
            "Напиши, что нарисовать: бот попробует сгенерировать изображение через Gemini.",
        )
        return

    # Дальше: смотрим режим
    mode = get_mode(chat_id)

    if mode == MODE_IMAGE:
        # Генерация картинок
        send_typing(chat_id)
        img_bytes = generate_image_with_gemini(text)

        if not img_bytes:
            send_message(
                chat_id,
                "Не получилось сгенерировать картинку 😢\n"
                "Проверь лог сервера или настройки Gemini API.",
            )
            return

        send_photo(
            chat_id,
            img_bytes,
            caption="Вот картинка по твоему описанию 🎨",
        )
    else:
        # Обычный текстовый режим
        send_typing(chat_id)
        ai_answer = ask_ai(text)
        send_message(chat_id, ai_answer)


def main():
    print("Бот запущен: принимает текст и голос, показывает typing.")

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

            # Голосовое
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

            # Обычный текст
            if text:
                handle_text_message(chat_id, text)

        time.sleep(1)


if __name__ == "__main__":
    main()
