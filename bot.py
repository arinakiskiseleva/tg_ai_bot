import os
import time
import threading
import re
import requests
from dotenv import load_dotenv
from flask import Flask

# Flask app для Render, чтобы был живой веб-сервер
app = Flask(__name__)


@app.route("/")
def index():
    return "Bot is running"


def run_web():
    """Запускаем маленький веб сервер на порту из переменной окружения."""
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# Загружаем переменные окружения
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_STT_URL = "https://api.openai.com/v1/audio/transcriptions"

# Максимальная длина сообщения в телеге: 4096
TELEGRAM_LIMIT = 4096
SAFE_LIMIT = 3900  # чуть меньше, чтобы точно влезло


def get_updates(offset=None):
    params = {"timeout": 20}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(f"{TG_API}/getUpdates", params=params, timeout=30)
        data = r.json()
        return data.get("result", [])
    except Exception as e:
        print("Error in get_updates:", e)
        return []


def clean_markdown(text: str) -> str:
    """
    Убираем маркдаун: ###, **жирный**, списки и служебные символы.
    """
    if not text:
        return ""

    text = str(text)

    # убираем заголовки вида "### Текст"
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)

    # убираем маркеры списков в начале строк: "- ", "* ", "1. "
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)

    # убираем обрамление жирного/курсива **текст**, *текст*, __текст__
    text = re.sub(r"(\*{1,3}|_{1,3})(.+?)(\*{1,3}|_{1,3})", r"\2", text)

    # убираем бэктики
    text = text.replace("`", "")

    # убираем одиночные служебные символы
    text = re.sub(r"[#*_]", "", text)

    return text


def send_message(chat_id, text):
    """Отправляем одно сообщение: без разбиения на части."""
    try:
        if text is None:
            text = ""

        text = str(text)

        # если вдруг ИИ выдал слишком длинный текст: аккуратно подрежем в конец
        if len(text) > TELEGRAM_LIMIT:
            text = text[:SAFE_LIMIT] + "\n\n(Конец ответа обрезан: не поместился целиком в одно сообщение Telegram)"

        requests.post(
            f"{TG_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=20,
        )
    except Exception as e:
        print("Error in send_message:", e)


def send_typing(chat_id):
    """Показываем статус: бот печатает."""
    try:
        requests.post(
            f"{TG_API}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=10,
        )
    except Exception as e:
        print("Error in send_typing:", e)


def ask_ai(user_text):
    """
    Шлём текст в OpenAI и забираем ответ.
    Просим модель писать без маркдауна и укладываться в ~3500–3800 символов,
    чтобы точно влезть в одно сообщение Telegram.
    """
    try:
        system_prompt = (
            "Ты телеграм-бот помощник. Отвечай на русском языке. "
            "Пиши обычным текстом без форматирования: "
            "не используй маркдаун, не используй символы *, #, `, "
            "не делай списки с тире или звездочками. "
            "Если нужно перечислить шаги: пиши каждый шаг с новой строки, "
            "например 'Шаг 1:', 'Шаг 2:' и так далее. "
            "Очень важно: твой полный ответ (включая все строки) "
            "должен уместиться примерно в 3500 символов, максимум 3800 символов. "
            "Если вопрос большой, сокращай детали, но сохраняй суть ответа."
        )

        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                "max_tokens": 900,  # примерно под 3000–3500 символов
            },
            timeout=60,
        )
        data = r.json()
        ai_text = data["choices"][0]["message"]["content"]
        ai_text = clean_markdown(ai_text)

        # контроль длины на всякий
        if len(ai_text) > TELEGRAM_LIMIT:
            ai_text = ai_text[:SAFE_LIMIT] + "\n\n(Ответ немного сокращён, чтобы поместиться в Telegram.)"

        return ai_text
    except Exception as e:
        print("Error in ask_ai:", e)
        return "Что то пошло не так при обращении к ИИ."


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
        print("Error in download_file:", e)
        return None


def transcribe_audio(audio_bytes):
    """Отправляем аудио в Whisper и получаем текст."""
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

        print("STT status:", r.status_code)
        print("STT response:", r.text)

        if r.status_code != 200:
            return None

        result = r.json()
        return result.get("text")
    except Exception as e:
        print("Error in transcribe_audio:", e)
        return None


def bot_loop():
    """Основной цикл бота на long polling."""
    print("Bot started: принимает текст и голос, показывает typing.")

    offset = None

    while True:
        updates = get_updates(offset)

        for upd in updates:
            offset = upd["update_id"] + 1
            print("Update:", upd)

            message = upd.get("message")
            if not message:
                continue

            chat_id = message["chat"]["id"]
            text = message.get("text")
            voice = message.get("voice")

            print("Message:", chat_id, "text:", text, "voice:", bool(voice))

            # команда /start
            if text and text.startswith("/start"):
                send_message(
                    chat_id,
                    "Привет: я твой ИИ-бот. Могу отвечать на текст и голосовые сообщения.",
                )
                continue

            # голосовые
            if voice:
                send_typing(chat_id)

                file_id = voice["file_id"]
                audio_bytes = download_file(file_id)

                print("Audio size:", 0 if audio_bytes is None else len(audio_bytes))

                if not audio_bytes:
                    send_message(chat_id, "Не получилось скачать голосовое сообщение.")
                    continue

                transcript = transcribe_audio(audio_bytes)
                if not transcript:
                    send_message(chat_id, "Не получилось распознать голос.")
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
                send_typing(chat_id)
                ai_answer = ask_ai(text)
                send_message(chat_id, ai_answer)

        time.sleep(1)


def start_bot_thread():
    """Стартуем бота в отдельном потоке, чтобы Flask мог крутиться параллельно."""
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()


if __name__ == "__main__":
    # запускаем бота в фоне
    start_bot_thread()
    # и веб сервер для Render
    run_web()
