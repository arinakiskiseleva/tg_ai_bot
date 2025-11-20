import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_STT_URL = "https://api.openai.com/v1/audio/transcriptions"


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


def send_message(chat_id, text):
    try:
        requests.post(
            f"{TG_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
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
                "messages": [{"role": "user", "content": text}],
                "max_tokens": 300,
            },
        )
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("Ошибка ask_ai:", e)
        return "Что то пошло не так при обращении к ИИ."


def download_file(file_id):
    """Скачиваем голосовое по file_id и возвращаем байты."""
    try:
        # 1: узнаём путь к файлу
        r = requests.get(f"{TG_API}/getFile", params={"file_id": file_id})
        file_data = r.json()
        file_path = file_data["result"]["file_path"]

        # 2: качаем файл
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
                    "Я умею отвечать на текст и голосовые сообщения."
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


if __name__ == "__main__":
    main()