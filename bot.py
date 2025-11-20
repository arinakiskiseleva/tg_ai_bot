import os
import time
import re
import threading

import requests
from dotenv import load_dotenv
from flask import Flask

# Flask: чтобы Render видел открытый порт
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_STT_URL = "https://api.openai.com/v1/audio/transcriptions"

# запас до лимита Телеги 4096 символов
MAX_MESSAGE_LENGTH = 3800


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
    Делим длинный текст на несколько сообщений, стараемся резать по строкам или пробелам.
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


def clean_markdown(text: str) -> str:
    """
    Примерно убираем маркдаун: #, **жирный**, `код` и т.п.,
    чтобы в Телеге не торчали лишние символы.
    """
    if not text:
        return ""

    # убираем заголовки типа "### Текст"
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)

    # убираем жирный/курсив **такой** *такой* __такой__
    text = re.sub(r"(\*{1,3}|_{1,3})(.+?)(\*{1,3}|_{1,3})", r"\2", text)

    # убираем обратные кавычки
    text = text.replace("`", "")

    return text


def send_message(chat_id, text):
    try:
        for part in split_message(text):
            requests.post(
                f"{TG_API}/sendMessage",
                json={"chat_id": chat_id, "text": part},
                timeout=15,
            )
    except Exception as e:
        print("Ошибка send_message:", e)


def send_typing(chat_id):
    """Показываем что бот печатает."""
    try:
        requests.post(
            f"{TG_API}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=10,
        )
    except Exception as e:
        print("Ошибка send_typing:", e)


def ask_ai(user_text):
    """Отправляем текст в OpenAI и получаем ответ без форматирования."""
    try:
        prompt = (
            "Отвечай простым текстом: без форматирования, без маркдауна, "
            "не используй символы *, #, ` и подобные. "
            "Пиши по шагам, но обычным текстом.\n\n"
            f"Запрос пользователя:\n{user_text}"
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
                "max_tokens": 900,
            },
            timeout=60,
        )
        data = r.json()
        ai_text = data["choices"][0]["message"]["content"]
        ai_text = clean_markdown(ai_text)
        return ai_text
    except Exception as e:
        print("Ошибка ask_ai:", e)
        return "Что то пошло не так при обращении к ИИ."


def download_file(file_id):
    """Скачиваем голосовое по file_id и возвращаем байты."""
    try:
        r = requests.get(f"{TG_API}/getFile", params={"file_id": file_id}, timeout=30)
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

            chat_id = message["chat"]["id"]
            text = message.get("text")
            voice = message.get("voice")

            print("Сообщение:", chat_id, "text:", text, "voice:", bool(voice))

            # /start
            if text and text.startswith("/start"):
                send_message(
                    chat_id,
                    "Привет: я твой ИИ бот 🤖💜\n"
                    "Я умею отвечать на текст и голосовые сообщения.",
                )
                continue

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
                send_typing(chat_id)
                ai_answer = ask_ai(text)
                send_message(chat_id, ai_answer)

        time.sleep(1)


def run_bot_with_flask():
    """
    Запускаем Flask в отдельном потоке
    и параллельно крутим основной цикл бота.
    """
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    main()


if __name__ == "__main__":
    run_bot_with_flask()
