import os
import time
import json
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

# -------------------- ENV --------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

TEXT_MODEL = "gpt-5.1"  # как ты просила

MAX_MESSAGE_LENGTH = 3800  # небольшой запас до лимита телеги

# -------------------- Память --------------------
MEMORY_FILE = "memory.json"

def load_memory():
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_memory(mem):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# основной чат хранится на диске, временный только в оперативке
main_history = load_memory()   # {chat_id: [messages]}
temp_history = {}              # {chat_id: [messages]}
chat_mode = {}                 # {chat_id: "temp" или "main"}

# -------------------- Утилиты --------------------
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
        for part in split_message(text):
            payload = {
                "chat_id": chat_id,
                "text": part,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
            # reply_markup только в первом сообщении, иначе телега ругается
            if reply_markup is not None:
                payload["reply_markup"] = reply_markup
                reply_markup = None
            requests.post(f"{TG_API}/sendMessage", json=payload, timeout=15)
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

# -------------------- История и режимы --------------------
SYSTEM_PROMPT = (
    "Ты дружелюбный ИИ помощник. Отвечай по-русски, живо и понятно. "
    "Делай ответы достаточно короткими, чтобы они помещались в одно сообщение Telegram. "
    "Не упоминай лимиты, символы или токены. "
    "Используй простое Markdown форматирование: **жирный**, _курсив_, обычные списки. "
    "Не используй заголовки с решётками и сложную разметку."
)

def get_history(chat_id):
    mode = chat_mode.get(str(chat_id), "temp")
    if mode == "main":
        return main_history.get(str(chat_id), [])
    return temp_history.get(str(chat_id), [])

def set_history(chat_id, hist):
    mode = chat_mode.get(str(chat_id), "temp")
    if mode == "main":
        main_history[str(chat_id)] = hist
        save_memory(main_history)
    else:
        temp_history[str(chat_id)] = hist

def ask_ai(chat_id, user_text):
    hist = get_history(chat_id)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + hist + [
        {"role": "user", "content": user_text}
    ]

    try:
        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": TEXT_MODEL,
                "messages": messages,
                "max_tokens": 600,
            },
            timeout=60,
        )
        data = r.json()
        answer = data["choices"][0]["message"]["content"]

        # сохраняем историю
        hist.append({"role": "user", "content": user_text})
        hist.append({"role": "assistant", "content": answer})

        # ограничиваем длину истории
        if len(hist) > 20:
            hist = hist[-20:]

        set_history(chat_id, hist)
        return answer
    except Exception as e:
        print("Ошибка ask_ai:", e)
        return "Что-то пошло не так при обращении к ИИ. Попробуй ещё раз."

# -------------------- Клавиатура --------------------
def main_menu_keyboard():
    return {
        "keyboard": [
            [{"text": "⚡ Временный чат"}, {"text": "💾 Основной чат"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }

# -------------------- Основной цикл --------------------
def main():
    print("Бот запущен: текстовый чат, режимы временный и основной с памятью.")

    offset = None

    while True:
        updates = get_updates(offset)

        for upd in updates:
            offset = upd["update_id"] + 1

            message = upd.get("message")
            if not message:
                continue

            chat_id = message["chat"]["id"]
            text = message.get("text")

            # команда /start
            if text and text.startswith("/start"):
                chat_mode[str(chat_id)] = "temp"
                send_message(
                    chat_id,
                    "Привет! Я твой ИИ бот CTRL+ART 💜\n"
                    "Выбери режим:\n"
                    "⚡ Временный чат: без памяти.\n"
                    "💾 Основной чат: с умной памятью.",
                    reply_markup=main_menu_keyboard(),
                )
                continue

            # переключение на временный чат
            if text == "⚡ Временный чат":
                chat_mode[str(chat_id)] = "temp"
                send_message(chat_id, "Ок: включен временный чат. Память не сохраняю ⚡")
                continue

            # переключение на основной чат
            if text == "💾 Основной чат":
                chat_mode[str(chat_id)] = "main"
                if str(chat_id) not in main_history:
                    main_history[str(chat_id)] = []
                    save_memory(main_history)
                send_message(chat_id, "Ок: включен основной чат. Буду помнить важное 💾")
                continue

            # обычный текст
            if text:
                send_typing(chat_id)
                answer = ask_ai(chat_id, text)
                send_message(chat_id, answer)

        time.sleep(1)

# -------------------- Старт --------------------
if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    main()
