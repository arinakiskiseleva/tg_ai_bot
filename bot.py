import os
import time
import json
import base64
import requests
from dotenv import load_dotenv
from flask import Flask
import threading

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_IMAGE_URL = "https://api.openai.com/v1/images/generations"

# -------------------- Flask чтобы Render видел порт --------------------
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

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
    except Exception as e:
        print("Ошибка save_memory:", e)

memory_store = load_memory()

def get_user_memory(user_id):
    return memory_store.get(str(user_id), {"facts": []})

def set_user_memory(user_id, data):
    memory_store[str(user_id)] = data
    save_memory(memory_store)

# -------------------- Режимы чата --------------------
MODE_TEMP = "temp"
MODE_MAIN = "main"

user_modes = {}  # user_id: MODE_TEMP or MODE_MAIN

def get_mode(user_id):
    return user_modes.get(user_id, MODE_TEMP)

def set_mode(user_id, mode):
    user_modes[user_id] = mode

# -------------------- Telegram helpers --------------------
def tg_request(method, payload=None, files=None, timeout=20):
    url = f"{TG_API}/{method}"
    return requests.post(url, json=payload, files=files, timeout=timeout)

def send_message(chat_id, text, reply_markup=None):
    # Отправляем HTML, чтобы без звездочек и решеток
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        tg_request("sendMessage", payload=payload, timeout=15)
    except Exception as e:
        print("Ошибка send_message:", e)

def send_typing(chat_id):
    try:
        tg_request("sendChatAction", payload={"chat_id": chat_id, "action": "typing"})
    except Exception as e:
        print("Ошибка send_typing:", e)

def send_photo(chat_id, image_bytes, caption=None):
    files = {"photo": ("image.png", image_bytes)}
    payload = {"chat_id": chat_id}
    if caption:
        payload["caption"] = caption
        payload["parse_mode"] = "HTML"
    try:
        tg_request("sendPhoto", payload=payload, files=files, timeout=60)
    except Exception as e:
        print("Ошибка send_photo:", e)

def main_menu():
    return {
        "keyboard": [
            [{"text": "⚡ Временный чат"}, {"text": "💾 Основной чат"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }

# -------------------- OpenAI helpers --------------------
SYSTEM_STYLE = (
    "Ты дружелюбный помощник для Telegram. "
    "Пиши просто и тепло, без фамильярностей типа 'зая'. "
    "Отвечай на русском. "
    "Форматируй ответ только Telegram HTML тегами: "
    "<b>, <i>, <u>, <s>, <code>, <pre>. "
    "Не используй Markdown символы вроде ###, **, __. "
    "Если нужно выделить заголовок: используй <b>Заголовок</b> на отдельной строке. "
    "Длина ответа: максимум около 3800 символов. "
    "Никогда не упоминай лимит символов пользователю."
)

def ask_ai_text(user_text, user_id):
    mode = get_mode(user_id)

    mem_block = ""
    if mode == MODE_MAIN:
        mem = get_user_memory(user_id)
        if mem["facts"]:
            facts_txt = "\n".join(
                [f"- {f['text']} (теги: {', '.join(f.get('tags', []))})" for f in mem["facts"]]
            )
            mem_block = f"Вот важные факты о пользователе:\n{facts_txt}\n"

    messages = [
        {"role": "system", "content": SYSTEM_STYLE + ("\n" + mem_block if mem_block else "")},
        {"role": "user", "content": user_text},
    ]

    try:
        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": messages,
                "max_tokens": 800,
            },
            timeout=60,
        )
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("Ошибка ask_ai_text:", e)
        return "Что то пошло не так при обращении к ИИ 😢"

def extract_memory_facts(user_text, ai_text):
    prompt = (
        "Вытащи из диалога только то, что стоит запомнить надолго: "
        "предпочтения, роли, задачи, важные факты. "
        "Верни строго JSON массив. "
        "Формат элемента: {\"text\": \"факт\", \"tags\": [\"тег1\",\"тег2\"]}. "
        "Если нечего запоминать: верни []."
        "\n\nПользователь:\n" + user_text +
        "\n\nАссистент:\n" + ai_text
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
                "messages": [{"role": "system", "content": prompt}],
                "max_tokens": 250,
            },
            timeout=45,
        )
        data = r.json()
        raw = data["choices"][0]["message"]["content"]
        return json.loads(raw)
    except Exception as e:
        print("Ошибка extract_memory_facts:", e)
        return []

def update_user_memory(user_id, new_facts):
    if not new_facts:
        return
    mem = get_user_memory(user_id)
    existing = mem.get("facts", [])

    # простая дедупликация по text
    texts = {f["text"] for f in existing}
    for f in new_facts:
        if f.get("text") and f["text"] not in texts:
            existing.append({"text": f["text"], "tags": f.get("tags", [])})

    # ограничим память сверху
    mem["facts"] = existing[-50:]
    set_user_memory(user_id, mem)

def generate_image(prompt_text):
    try:
        r = requests.post(
            OPENAI_IMAGE_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-image-1",
                "prompt": prompt_text,
                "size": "1024x1024"
            },
            timeout=120,
        )
        data = r.json()

        # images/generations сейчас возвращает base64
        b64 = data["data"][0].get("b64_json")
        if not b64:
            return None
        return base64.b64decode(b64)
    except Exception as e:
        print("Ошибка generate_image:", e)
        return None

def analyze_image(image_bytes, user_text=None):
    # Telegram vision через chat completions
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    content = [{"type": "text", "text": (user_text or "Опиши что на фото и что важно заметить")}]
    content.append({
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
    })

    messages = [
        {"role": "system", "content": SYSTEM_STYLE},
        {"role": "user", "content": content}
    ]

    try:
        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": messages,
                "max_tokens": 800,
            },
            timeout=60,
        )
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("Ошибка analyze_image:", e)
        return "Не получилось проанализировать изображение 😢"

# -------------------- Telegram file download --------------------
def download_file(file_id):
    try:
        r = requests.get(f"{TG_API}/getFile", params={"file_id": file_id}, timeout=20)
        file_data = r.json()
        file_path = file_data["result"]["file_path"]
        file_url = f"{TG_FILE_API}/{file_path}"
        file_resp = requests.get(file_url, timeout=30)
        return file_resp.content
    except Exception as e:
        print("Ошибка download_file:", e)
        return None

# -------------------- Main loop --------------------
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

def handle_text(chat_id, user_id, text):
    # кнопки меню
    if text == "⚡ Временный чат":
        set_mode(user_id, MODE_TEMP)
        send_message(
            chat_id,
            "Ок: включила временный чат. Здесь не сохраняю память ⚡",
            reply_markup=main_menu()
        )
        return

    if text == "💾 Основной чат":
        set_mode(user_id, MODE_MAIN)
        send_message(
            chat_id,
            "Ок: включила основной чат. Буду помнить важное 💾💜",
            reply_markup=main_menu()
        )
        return

    # генерация картинок
    if text.lower().startswith("/img"):
        prompt_text = text[4:].strip()
        if not prompt_text:
            send_message(chat_id, "Напиши после /img что нужно нарисовать 🙂")
            return
        send_typing(chat_id)
        img_bytes = generate_image(prompt_text)
        if not img_bytes:
            send_message(chat_id, "Не получилось сгенерировать картинку 😢 Попробуй переформулировать.")
            return
        send_photo(chat_id, img_bytes, caption="Готово 📸")
        return

    # обычный чат
    send_typing(chat_id)
    ai_answer = ask_ai_text(text, user_id)

    if get_mode(user_id) == MODE_MAIN:
        new_facts = extract_memory_facts(text, ai_answer)
        update_user_memory(user_id, new_facts)

    send_message(chat_id, ai_answer)

def main():
    print("Bot started: text chat with temp and main memory, plus image gen and analysis.")

    offset = None
    while True:
        updates = get_updates(offset)

        for upd in updates:
            offset = upd["update_id"] + 1
            message = upd.get("message")
            if not message:
                continue

            chat_id = message["chat"]["id"]
            user_id = message["from"]["id"]

            text = message.get("text")
            photo = message.get("photo")

            # /start
            if text and text.startswith("/start"):
                send_message(
                    chat_id,
                    "Привет! Я твой ИИ бот CTRL+ART 💜\n"
                    "Выбери режим:",
                    reply_markup=main_menu()
                )
                continue

            # фото: анализ
            if photo:
                # берём самое большое
                file_id = photo[-1]["file_id"]
                img_bytes = download_file(file_id)
                if not img_bytes:
                    send_message(chat_id, "Не смогла скачать фото 😢")
                    continue
                send_typing(chat_id)
                ai_answer = analyze_image(img_bytes, "Проанализируй фото и ответь на русском.")
                send_message(chat_id, ai_answer)
                continue

            # текст
            if text:
                handle_text(chat_id, user_id, text)

        time.sleep(1)

if __name__ == "__main__":
    # поднимаем веб для Render
    threading.Thread(target=run_web, daemon=True).start()
    main()
