import os
import time
import json
import base64
import sqlite3
import threading
import requests
from dotenv import load_dotenv
from flask import Flask

# --------------------
# Flask для Render (чтобы порт был открыт)
# --------------------
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --------------------
# ENV
# --------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Текстовая модель. В API нет названия "5.1" как у ChatGPT в приложении,
# поэтому ставим актуальную API модель. Если хочешь другую, задай OPENAI_MODEL в Render.
MODEL_TEXT = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
MODEL_VISION = os.getenv("OPENAI_VISION_MODEL", MODEL_TEXT)

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_IMG_URL = "https://api.openai.com/v1/images/generations"

MAX_TELEGRAM_LEN = 4000

# --------------------
# Память SQLite (только для основного чата)
# --------------------
DB_PATH = "memory.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            chat_id INTEGER,
            item TEXT,
            ts INTEGER
        )
    """)
    conn.commit()
    conn.close()

def add_memory(chat_id: int, text: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO memories (chat_id, item, ts) VALUES (?, ?, ?)",
        (chat_id, text, int(time.time()))
    )
    conn.commit()
    conn.close()

def get_memories(chat_id: int, limit: int = 30):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT item FROM memories WHERE chat_id=? ORDER BY ts DESC LIMIT ?",
        (chat_id, limit)
    )
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows][::-1]

init_db()

# --------------------
# Сервисные функции Telegram
# --------------------
def send_message(chat_id, text):
    """
    Отправка текста с HTML форматированием.
    Модель просим писать HTML, поэтому ### и ** не прилетают.
    """
    try:
        # телега иногда ругается на слишком длинное сообщение,
        # но мы просим модель укладываться в лимит.
        payload = {
            "chat_id": chat_id,
            "text": text[:MAX_TELEGRAM_LEN],
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        requests.post(f"{TG_API}/sendMessage", json=payload, timeout=20)
    except Exception as e:
        print("send_message error:", e)

def send_typing(chat_id):
    try:
        requests.post(
            f"{TG_API}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=10
        )
    except Exception as e:
        print("send_typing error:", e)

def send_menu(chat_id):
    keyboard = {
        "keyboard": [
            [{"text": "⚡ Временный чат"}, {"text": "💾 Основной чат"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }
    send_message(
        chat_id,
        "Привет! Я твой ИИ бот CTRL+ART 💜\n"
        "Выбери режим:\n"
        "⚡ Временный чат: без памяти\n"
        "💾 Основной чат: с умной памятью"
    )
    try:
        requests.post(
            f"{TG_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "Выбери режим кнопкой ниже 👇",
                "reply_markup": keyboard
            },
            timeout=20
        )
    except Exception as e:
        print("send_menu error:", e)

# --------------------
# Работа с файлами Telegram (для фото)
# --------------------
def download_telegram_file(file_id: str):
    try:
        r = requests.get(f"{TG_API}/getFile", params={"file_id": file_id}, timeout=20)
        file_path = r.json()["result"]["file_path"]
        file_url = f"{TG_FILE_API}/{file_path}"
        file_resp = requests.get(file_url, timeout=30)
        return file_resp.content
    except Exception as e:
        print("download_telegram_file error:", e)
        return None

# --------------------
# OpenAI: чат
# --------------------
SYSTEM_PROMPT_BASE = (
    "Ты дружелюбный помощник. "
    "Отвечай на русском. "
    "Форматирование делай ТОЛЬКО в HTML, используй простые теги: "
    "<b>, <i>, <code>, <pre>, <ul>, <ol>, <li>, <br>. "
    "НЕ используй Markdown символы вроде ###, **, __, ```.\n"
    f"Ответ делай так, чтобы он уместился максимум в {MAX_TELEGRAM_LEN} символов, "
    "но НЕ упоминай про лимиты в тексте."
)

def ask_ai(user_text: str, memories: list | None = None):
    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT_BASE}]
        if memories:
            mem_block = "\n".join(f"- {m}" for m in memories)
            messages.append({
                "role": "system",
                "content": f"Важные факты о пользователе:\n{mem_block}"
            })
        messages.append({"role": "user", "content": user_text})

        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL_TEXT,
                "messages": messages,
                "max_tokens": 900,
            },
            timeout=60
        )
        data = r.json()
        if "error" in data:
            print("OpenAI error:", data["error"])
            return "Что то пошло не так при обращении к ИИ 😢"
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("ask_ai error:", e)
        return "Что то пошло не так при обращении к ИИ 😢"

# --------------------
# OpenAI: анализ фото
# --------------------
def analyze_image(image_bytes: bytes, user_text: str = ""):
    try:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_BASE},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Проанализируй фото. {user_text}".strip()},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                ]
            }
        ]

        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL_VISION,
                "messages": messages,
                "max_tokens": 900
            },
            timeout=60
        )
        data = r.json()
        if "error" in data:
            print("Vision error:", data["error"])
            return "Не получилось проанализировать картинку 😢 Проверь, что у ключа есть доступ к Vision."
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("analyze_image error:", e)
        return "Не получилось проанализировать картинку 😢"

# --------------------
# OpenAI: генерация картинки
# --------------------
def generate_image(prompt: str):
    """
    Возвращает bytes картинки (jpg/png).
    """
    try:
        r = requests.post(
            OPENAI_IMG_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-image-1",
                "prompt": prompt,
                "size": "1024x1024"
            },
            timeout=120
        )
        data = r.json()
        if "error" in data:
            print("Image gen error:", data["error"])
            return None

        item = data["data"][0]

        # чаще всего приходит b64_json
        if "b64_json" in item and item["b64_json"]:
            return base64.b64decode(item["b64_json"])

        # иногда приходит url
        if "url" in item and item["url"]:
            img_resp = requests.get(item["url"], timeout=60)
            return img_resp.content

        return None
    except Exception as e:
        print("generate_image error:", e)
        return None

def send_photo(chat_id, image_bytes: bytes, caption: str = ""):
    try:
        files = {"photo": ("image.png", image_bytes)}
        data = {"chat_id": chat_id, "caption": caption[:1024]}
        requests.post(f"{TG_API}/sendPhoto", files=files, data=data, timeout=60)
    except Exception as e:
        print("send_photo error:", e)

# --------------------
# Автоопределение запроса на картинку
# --------------------
IMAGE_TRIGGERS = [
    "сгенерируй", "генерируй", "нарисуй", "сделай картинку", "сделай изображение",
    "создай изображение", "создай картинку", "хочу картинку", "хочу изображение",
    "изобрази", "картинк", "изображен"
]

def is_image_request(text: str):
    if not text:
        return False
    t = text.lower()
    # отсекаем вопросы "умеешь ли" чтобы не срабатывало на болтовню
    if "умеешь" in t and ("картинк" in t or "изображен" in t):
        return False
    return any(tr in t for tr in IMAGE_TRIGGERS)

# --------------------
# Режимы чата
# --------------------
user_modes = {}  # chat_id -> "temp" | "main"

def set_mode(chat_id, mode):
    user_modes[chat_id] = mode

def get_mode(chat_id):
    return user_modes.get(chat_id, "temp")

# --------------------
# Updates loop
# --------------------
def get_updates(offset=None):
    params = {"timeout": 20}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(f"{TG_API}/getUpdates", params=params, timeout=25)
        data = r.json()
        return data.get("result", [])
    except Exception as e:
        print("get_updates error:", e)
        return []

def main():
    print("Bot started: text and images, two modes (temp/main).")

    # Запускаем Flask в отдельном потоке
    threading.Thread(target=run_web, daemon=True).start()

    offset = None

    while True:
        updates = get_updates(offset)

        for upd in updates:
            offset = upd["update_id"] + 1
            message = upd.get("message")
            if not message:
                continue

            chat_id = message["chat"]["id"]
            text = message.get("text", "")
            photos = message.get("photo")

            # /start
            if text.startswith("/start"):
                set_mode(chat_id, "temp")
                send_menu(chat_id)
                continue

            # Кнопки режима
            if text == "⚡ Временный чат":
                set_mode(chat_id, "temp")
                send_message(chat_id, "Ок: включила временный чат. Память не сохраняю ⚡")
                continue

            if text == "💾 Основной чат":
                set_mode(chat_id, "main")
                send_message(chat_id, "Ок: включила основной чат. Буду помнить важное 💾💜")
                continue

            mode = get_mode(chat_id)

            # Если прислали фото: анализ
            if photos:
                send_typing(chat_id)
                best = photos[-1]
                file_id = best["file_id"]
                img_bytes = download_telegram_file(file_id)
                if not img_bytes:
                    send_message(chat_id, "Не смогла скачать картинку 😢")
                    continue

                answer = analyze_image(img_bytes, user_text=text)
                send_message(chat_id, answer)

                # память только в основном
                if mode == "main" and text:
                    add_memory(chat_id, f"Пользователь прислал фото и написал: {text}")
                continue

            # Авто генерация картинки по тексту
            if text and is_image_request(text):
                send_typing(chat_id)
                img = generate_image(text)
                if not img:
                    send_message(
                        chat_id,
                        "Не удалось сгенерировать картинку 😢 "
                        "Проверь, что ключ OpenAI с доступом к Images и включён биллинг."
                    )
                    continue

                send_photo(chat_id, img, caption="Готово 💜")

                if mode == "main":
                    add_memory(chat_id, f"Запрос на картинку: {text}")
                continue

            # Обычный текстовый ответ
            if text:
                send_typing(chat_id)
                memories = get_memories(chat_id) if mode == "main" else None
                answer = ask_ai(text, memories=memories)
                send_message(chat_id, answer)

                if mode == "main":
                    add_memory(chat_id, f"Пользователь: {text}")
                continue

        time.sleep(1)

if __name__ == "__main__":
    main()
