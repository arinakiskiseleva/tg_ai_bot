import os
import time
import base64
import threading
import requests
from dotenv import load_dotenv
from flask import Flask

# ---------- Flask для Render (проверка, что сервис живой) ----------

app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ---------- Загрузка настроек ----------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
TEXT_MODEL = "gpt-4o-mini"

# Лимит телеги 4096, берём запас
MAX_MESSAGE_LENGTH = 3800

# Режимы чата
MODE_TEMP = "temp"
MODE_MAIN = "main"

# Память по пользователям
chat_modes = {}       # chat_id -> MODE_TEMP / MODE_MAIN
chat_histories = {}   # chat_id -> list[{"role":...,"content":...}]

# ---------- Вспомогательные функции ----------

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
    """Аккуратно режем длинный текст по строкам/пробелам."""
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
    """Убираем типичное markdown-оформление, чтобы не было ** и ###."""
    if not isinstance(text, str):
        return text

    # Простые замены
    for token in ["**", "__", "```", "`"]:
        text = text.replace(token, "")

    # Убираем ведущие # в заголовках
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.lstrip()
        while stripped.startswith("#"):
            stripped = stripped[1:].lstrip()
        cleaned.append(stripped)
    return "\n".join(cleaned).strip()


def send_message(chat_id, text):
    try:
        for part in split_message(text):
            requests.post(
                f"{TG_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": part,
                    # без parse_mode, чтобы не было проблем с форматированием
                },
                timeout=10,
            )
    except Exception as e:
        print("Ошибка send_message:", e)


def send_typing(chat_id):
    try:
        requests.post(
            f"{TG_API}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=5,
        )
    except Exception as e:
        print("Ошибка send_typing:", e)


def send_start_menu(chat_id):
    """Приветствие и клавиатура с режимами."""
    text = (
        "Привет, я твой ИИ бот CTRL+ART 💜\n\n"
        "Выбери режим:\n"
        "⚡ Временный чат: без памяти.\n"
        "💾 Основной чат: с умной памятью."
    )

    keyboard = {
        "keyboard": [
            [{"text": "⚡ Временный чат"}],
            [{"text": "💾 Основной чат"}],
        ],
        "resize_keyboard": True,
    }

    try:
        requests.post(
            f"{TG_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "reply_markup": keyboard,
            },
            timeout=10,
        )
    except Exception as e:
        print("Ошибка send_start_menu:", e)


def download_file(file_id):
    """Скачиваем файл по file_id (для картинок)."""
    try:
        r = requests.get(f"{TG_API}/getFile", params={"file_id": file_id}, timeout=10)
        file_data = r.json()
        file_path = file_data["result"]["file_path"]

        file_url = f"{TG_FILE_API}/{file_path}"
        file_resp = requests.get(file_url, timeout=20)
        return file_resp.content
    except Exception as e:
        print("Ошибка download_file:", e)
        return None


SYSTEM_PROMPT = (
    "Ты дружелюбный, но нейтральный помощник. Отвечай на русском языке.\n"
    "Давай ответы структурированно, по делу, без лишней воды.\n"
    "Ответ должен помещаться в пределах примерно 4000 символов, "
    "но не упоминай это ограничение в тексте."
)


def ask_ai(user_text: str, mode: str, chat_id=None, image_bytes: bytes | None = None) -> str:
    """Запрос к GPT-4o-mini. Умеет анализировать картинку, если image_bytes не None."""
    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Добавляем историю для основного режима
        if mode == MODE_MAIN and chat_id in chat_histories:
            messages.extend(chat_histories[chat_id])

        # Формируем контент пользователя
        user_content = []

        if user_text:
            user_content.append({"type": "text", "text": user_text})

        if image_bytes:
            b64 = base64.b64encode(image_bytes).decode("utf-8")
            data_url = f"data:image/jpeg;base64,{b64}"
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                }
            )

        if not user_content:
            user_content = [{"type": "text", "text": "Просто ответь дружелюбно."}]

        # Если только текст: можно отправить строкой, иначе массивом
        if len(user_content) == 1 and user_content[0]["type"] == "text":
            user_message = {"role": "user", "content": user_content[0]["text"]}
        else:
            user_message = {"role": "user", "content": user_content}

        messages.append(user_message)

        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": TEXT_MODEL,
                "messages": messages,
                "max_tokens": 1000,
            },
            timeout=60,
        )

        data = r.json()
        print("AI status:", r.status_code)
        if r.status_code != 200:
            print("AI error body:", data)
            return "Что-то пошло не так при обращении к ИИ. Попробуй ещё раз."

        ai_content = data["choices"][0]["message"]["content"]

        # Обновляем память только в основном режиме
        if mode == MODE_MAIN and chat_id is not None:
            history = chat_histories.get(chat_id, [])
            history.append(user_message)
            history.append({"role": "assistant", "content": ai_content})
            # ограничиваем историю, чтобы не росла бесконечно
            chat_histories[chat_id] = history[-12:]

        return clean_markdown(ai_content)

    except Exception as e:
        print("Ошибка ask_ai:", e)
        return "Что-то пошло не так при обращении к ИИ. Попробуй ещё раз."


def get_mode(chat_id):
    """Текущий режим пользователя (по умолчанию временный)."""
    return chat_modes.get(chat_id, MODE_TEMP)


def set_mode(chat_id, mode: str):
    chat_modes[chat_id] = mode


# ---------- Главный цикл ----------

def main():
    print("Бот запущен: текст, анализ картинок, два режима памяти.")

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
            text = message.get("text") or message.get("caption")
            photos = message.get("photo")

            # Команда /start
            if text and text.startswith("/start"):
                set_mode(chat_id, MODE_TEMP)
                # обнуляем историю при новом старте
                chat_histories.pop(chat_id, None)
                send_start_menu(chat_id)
                continue

            # Нажатие на кнопки меню
            if text == "⚡ Временный чат":
                set_mode(chat_id, MODE_TEMP)
                chat_histories.pop(chat_id, None)  # очищаем память
                send_message(chat_id, "Ок, включён временный чат без памяти ⚡")
                continue

            if text == "💾 Основной чат":
                set_mode(chat_id, MODE_MAIN)
                send_message(chat_id, "Ок, включила основной чат: буду помнить важное 💾")
                continue

            mode = get_mode(chat_id)

            # Обработка картинок
            image_bytes = None
            if photos:
                # берём самую большую версию
                file_id = photos[-1]["file_id"]
                image_bytes = download_file(file_id)
                if not text:
                    text = "Проанализируй это изображение и расскажи, что на нём изображено."

            # Ничего содержательного
            if not text and not image_bytes:
                send_message(chat_id, "Отправь текст или картинку, и я отвечу.")
                continue

            # Обычный запрос
            send_typing(chat_id)
            ai_answer = ask_ai(text, mode=mode, chat_id=chat_id, image_bytes=image_bytes)
            send_message(chat_id, ai_answer)

        time.sleep(1)


if __name__ == "__main__":
    # web-сервер для Render в отдельном потоке
    threading.Thread(target=run_web, daemon=True).start()
    # основной цикл бота
    main()
