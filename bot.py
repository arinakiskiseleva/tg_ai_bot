import os
import time
import json
import re
import base64
import threading
import requests
from dotenv import load_dotenv
from flask import Flask

# -------------------- Flask keep-alive for Render --------------------
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# -------------------- Load env --------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_IMG_URL = "https://api.openai.com/v1/images/generations"

MODEL_TEXT_VISION = "gpt-4o-mini"
MODEL_IMAGE = "gpt-image-1"

MAX_TG_CHARS = 4000

# -------------------- Memory (JSON) --------------------
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

# Структура:
# memory_store[user_id] = {
#   "facts": [{"text":"...", "tags":["..."], "topic":"..."}],
#   "history": [{"role":"user","content":"..."},{"role":"assistant","content":"..."}]
# }

def get_user_block(user_id: str):
    if user_id not in memory_store:
        memory_store[user_id] = {"facts": [], "history": []}
    return memory_store[user_id]

def dedup_facts(facts):
    seen = set()
    out = []
    for f in facts:
        key = f.get("text","").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(f)
    return out

def extract_memory_items(user_text: str, ai_text: str):
    """
    Умная память: просим модель вытащить важные факты о пользователе.
    Возвращаем список фактов.
    """
    prompt = (
        "Ты помощник, который извлекает ЗНАЧИМЫЕ факты о пользователе из диалога.\n"
        "Верни JSON строго в таком виде:\n"
        '{ "facts": [ { "text": "...", "tags": ["..."], "topic": "..." } ] }\n'
        "Правила:\n"
        "1) Добавляй только факты, которые полезны в будущем (предпочтения, цели, проекты, важные данные).\n"
        "2) Не добавляй случайные мелочи.\n"
        "3) tags: 1-3 коротких тега.\n"
        "4) topic: одна тема.\n"
        "5) Если нечего сохранять: верни facts: [].\n\n"
        f"Сообщение пользователя:\n{user_text}\n\n"
        f"Ответ ассистента:\n{ai_text}\n"
    )

    try:
        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL_TEXT_VISION,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 220,
                "temperature": 0.2,
            },
            timeout=30
        )
        data = r.json()
        raw = data["choices"][0]["message"]["content"]
        raw = raw.strip()

        # Иногда модель заворачивает в ```json ... ```
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        obj = json.loads(raw)
        return obj.get("facts", [])
    except Exception as e:
        print("Ошибка extract_memory_items:", e)
        return []

# -------------------- Telegram helpers --------------------
def send_typing(chat_id):
    try:
        requests.post(
            f"{TG_API}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=10,
        )
    except Exception as e:
        print("Ошибка send_typing:", e)

def send_message(chat_id, text, reply_markup=None):
    """
    Шлём HTML, чтобы не было ** и ###.
    """
    try:
        if text is None:
            text = ""
        text = str(text)

        # На всякий случай обрезаем в жёсткий максимум телеги
        if len(text) > MAX_TG_CHARS:
            text = text[:MAX_TG_CHARS - 3] + "..."

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        requests.post(f"{TG_API}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        print("Ошибка send_message:", e)

def send_photo(chat_id, image_bytes, caption=None):
    try:
        files = {"photo": ("image.png", image_bytes)}
        data = {"chat_id": chat_id}
        if caption:
            # caption тоже HTML
            if len(caption) > 900:
                caption = caption[:900] + "..."
            data["caption"] = caption
            data["parse_mode"] = "HTML"
        requests.post(f"{TG_API}/sendPhoto", data=data, files=files, timeout=60)
    except Exception as e:
        print("Ошибка send_photo:", e)

def get_updates(offset=None):
    params = {"timeout": 25}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(f"{TG_API}/getUpdates", params=params, timeout=35)
        data = r.json()
        return data.get("result", [])
    except Exception as e:
        print("Ошибка get_updates:", e)
        return []

def download_tg_file(file_id):
    try:
        r = requests.get(f"{TG_API}/getFile", params={"file_id": file_id}, timeout=20)
        file_path = r.json()["result"]["file_path"]
        file_url = f"{TG_FILE_API}/{file_path}"
        file_resp = requests.get(file_url, timeout=30)
        return file_resp.content, file_url
    except Exception as e:
        print("Ошибка download_tg_file:", e)
        return None, None

# -------------------- OpenAI text + vision --------------------
SYSTEM_PROMPT = (
    "Ты дружелюбный помощник. Отвечай по-русски.\n"
    "Ответ должен быть полезным и тёплым.\n"
    "Длина ответа: максимум 4000 символов.\n"
    "ВАЖНО: не упоминай никаких лимитов и не говори про 4000 символов.\n"
    "Форматируй ответ только HTML тегами Telegram: <b>, <i>, <u>, <s>, <code>, <pre>.\n"
    "Не используй markdown: никаких **, ###, ```.\n"
)

def ask_ai_text(messages):
    try:
        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL_TEXT_VISION,
                "messages": messages,
                "max_tokens": 700,
                "temperature": 0.7,
            },
            timeout=40
        )
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("Ошибка ask_ai_text:", e)
        return "Что то пошло не так при обращении к ИИ 😢"

def ask_ai_with_image(user_text, image_url):
    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": user_text or "Опиши, что на изображении и что важно."},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]}
        ]

        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL_TEXT_VISION,
                "messages": messages,
                "max_tokens": 700,
                "temperature": 0.6,
            },
            timeout=60
        )
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("Ошибка ask_ai_with_image:", e)
        return "Не получилось проанализировать картинку 😿"

# -------------------- OpenAI image generation --------------------
IMG_PREFIXES = ["/img ", "/image ", "картинка:", "нарисуй:", "сгенерируй картинку:"]

def is_image_request(text: str):
    if not text:
        return False
    t = text.strip().lower()
    return any(t.startswith(p) for p in IMG_PREFIXES)

def strip_image_prefix(text: str):
    t = text.strip()
    low = t.lower()
    for p in IMG_PREFIXES:
        if low.startswith(p):
            return t[len(p):].strip()
    return t

def generate_image(prompt: str):
    try:
        r = requests.post(
            OPENAI_IMG_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL_IMAGE,
                "prompt": prompt,
                "size": "1024x1024",
                "response_format": "b64_json",
            },
            timeout=90
        )
        data = r.json()
        b64 = data["data"][0]["b64_json"]
        return base64.b64decode(b64)
    except Exception as e:
        print("Ошибка generate_image:", e)
        return None

# -------------------- Modes --------------------
MODE_TEMP = "temp"
MODE_MAIN = "main"

user_modes = {}  # user_id -> mode

def set_mode(user_id, mode):
    user_modes[user_id] = mode

def get_mode(user_id):
    return user_modes.get(user_id, MODE_TEMP)

def main_menu():
    return {
        "keyboard": [
            [{"text": "⏳ Временный чат"}, {"text": "💾 Основной чат"}]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }

# -------------------- Main loop --------------------
def main():
    print("Бот запущен: текст, vision, gpt-image, 2 режима памяти.")

    offset = None

    while True:
        updates = get_updates(offset)

        for upd in updates:
            offset = upd["update_id"] + 1

            message = upd.get("message")
            if not message:
                continue

            chat_id = message["chat"]["id"]
            user_id = str(message["from"]["id"])

            text = message.get("text")
            photos = message.get("photo")

            # /start
            if text and text.startswith("/start"):
                set_mode(user_id, MODE_TEMP)
                send_message(
                    chat_id,
                    "Привет, зая 💜\n"
                    "Я твой ИИ бот. Выбери режим:\n"
                    "⏳ Временный чат: без памяти.\n"
                    "💾 Основной чат: с умной памятью.",
                    reply_markup=main_menu()
                )
                continue

            # Переключение режима меню
            if text == "⏳ Временный чат":
                set_mode(user_id, MODE_TEMP)
                send_message(chat_id, "Ок, включила временный чат: память не сохраняю 💜", reply_markup=main_menu())
                continue

            if text == "💾 Основной чат":
                set_mode(user_id, MODE_MAIN)
                send_message(chat_id, "Ок, включила основной чат: буду помнить важное 💾💜", reply_markup=main_menu())
                continue

            mode = get_mode(user_id)

            # Если пришло фото: анализируем
            if photos:
                send_typing(chat_id)
                best = photos[-1]
                file_id = best["file_id"]
                _, file_url = download_tg_file(file_id)
                if not file_url:
                    send_message(chat_id, "Не смогла скачать картинку 😿")
                    continue

                user_caption = message.get("caption") or ""
                ai_answer = ask_ai_with_image(user_caption, file_url)

                # В main режиме пишем в историю + память
                if mode == MODE_MAIN:
                    block = get_user_block(user_id)
                    block["history"].append({"role": "user", "content": f"[image] {user_caption}".strip()})
                    block["history"].append({"role": "assistant", "content": ai_answer})

                    facts = extract_memory_items(user_caption, ai_answer)
                    block["facts"].extend(facts)
                    block["facts"] = dedup_facts(block["facts"])[-80:]

                    block["history"] = block["history"][-20:]
                    save_memory(memory_store)

                send_message(chat_id, ai_answer, reply_markup=main_menu())
                continue

            # Если попросили картинку
            if text and is_image_request(text):
                prompt = strip_image_prefix(text)
                if not prompt:
                    send_message(chat_id, "Опиши, что рисовать, и я сделаю 💜", reply_markup=main_menu())
                    continue

                send_typing(chat_id)
                img_bytes = generate_image(prompt)

                if not img_bytes:
                    send_message(chat_id, "Не получилось сгенерировать картинку 😿", reply_markup=main_menu())
                    continue

                send_photo(chat_id, img_bytes, caption="Готово 💜",)
                continue

            # Обычный текст
            if text:
                send_typing(chat_id)

                messages = [{"role": "system", "content": SYSTEM_PROMPT}]

                if mode == MODE_MAIN:
                    block = get_user_block(user_id)

                    # Память фактов в системный контекст
                    if block["facts"]:
                        facts_text = "\n".join(
                            [f"- {f['text']} (теги: {', '.join(f.get('tags', []))}, тема: {f.get('topic','')})"
                             for f in block["facts"][-25:]]
                        )
                        messages.append({
                            "role": "system",
                            "content": "Вот что ты уже знаешь про пользователя:\n" + facts_text
                        })

                    # История диалога
                    messages.extend(block["history"])

                messages.append({"role": "user", "content": text})

                ai_answer = ask_ai_text(messages)

                if mode == MODE_MAIN:
                    block = get_user_block(user_id)
                    block["history"].append({"role": "user", "content": text})
                    block["history"].append({"role": "assistant", "content": ai_answer})

                    facts = extract_memory_items(text, ai_answer)
                    block["facts"].extend(facts)
                    block["facts"] = dedup_facts(block["facts"])[-80:]

                    block["history"] = block["history"][-20:]
                    save_memory(memory_store)

                send_message(chat_id, ai_answer, reply_markup=main_menu())

        time.sleep(1)

# -------------------- Entrypoint --------------------
if __name__ == "__main__":
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    main()
