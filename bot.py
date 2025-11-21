import os
import time
import json
import base64
import requests
from dotenv import load_dotenv

from flask import Flask
import threading

# =========================
# Flask: пинг для Render
# =========================

app = Flask(__name__)


@app.route("/")
def index():
    return "Bot is running"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# =========================
# Настройки и переменные
# =========================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # можно поменять на gpt-4.1, gpt-5.1, когда будет доступ

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

MAX_MESSAGE_LENGTH = 3800  # чуть меньше лимита телеги
MEMORY_FILE = "memory.json"
HISTORY_LIMIT = 12  # сколько последних сообщений хранить в памяти

# Текст кнопок
BTN_MAIN_CHAT = "💾 Основной чат"
BTN_TEMP_CHAT = "⏳ Временный чат"
BTN_PSYCHO = "🧠 Психолог"
BTN_SMM = "📣 SMM-маркетолог"
BTN_ASSISTANT = "🧩 Личный ассистент"


# =========================
# Работа с памятью
# =========================

def load_memory():
    try:
        if not os.path.exists(MEMORY_FILE):
            return {}
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("Ошибка load_memory:", e)
        return {}


def save_memory(data):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Ошибка save_memory:", e)


def get_chat_state(chat_id: int):
    mem = load_memory()
    chat_id_str = str(chat_id)

    if "chats" not in mem:
        mem["chats"] = {}

    if chat_id_str not in mem["chats"]:
        mem["chats"][chat_id_str] = {
            "mode": "main",           # main или temp
            "role": "assistant",      # assistant, psychologist, smm
            "history": [],            # список сообщений для OpenAI
            "tags": [],               # простые теги
            "notes": ""               # короткие заметки о человеке
        }
        save_memory(mem)

    return mem, mem["chats"][chat_id_str]


def update_chat_state(mem, chat_id: int, state: dict):
    chat_id_str = str(chat_id)
    mem["chats"][chat_id_str] = state
    save_memory(mem)


def update_smart_memory(state: dict, user_text: str):
    """Простая умная память: выделяем теги и чуть дописываем заметки."""
    words = [
        w.strip(".,!?;:()[]«»\"'").lower()
        for w in user_text.split()
        if len(w.strip(".,!?;:()[]«»\"'")) >= 5
    ]

    stopwords = {
        "которые", "сейчас", "просто", "вообще", "своего", "такого",
        "потому", "когда", "можешь", "можно", "нужно"
    }

    tags = state.get("tags", [])
    for w in words:
        if w in stopwords:
            continue
        if w not in tags:
            tags.append(w)
        if len(tags) >= 15:
            break

    state["tags"] = tags

    # Заметки: добавляем кусочек текста, если он новый
    notes = state.get("notes", "")
    snippet = user_text.strip()
    if len(snippet) > 200:
        snippet = snippet[:200] + "..."
    if snippet and snippet not in notes:
        if notes:
            notes = notes + " | " + snippet
        else:
            notes = snippet
    # ограничим по длине
    if len(notes) > 1000:
        notes = notes[-1000:]
    state["notes"] = notes


# =========================
# Telegram helpers
# =========================

def build_keyboard():
    return {
        "keyboard": [
            [BTN_MAIN_CHAT, BTN_TEMP_CHAT],
            [BTN_PSYCHO, BTN_SMM, BTN_ASSISTANT],
        ],
        "resize_keyboard": True,
    }


def send_message(chat_id, text, reply_markup=None):
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

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
            timeout=5,
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


def download_file(file_id: str) -> bytes | None:
    try:
        r = requests.get(f"{TG_API}/getFile", params={"file_id": file_id}, timeout=15)
        file_data = r.json()
        file_path = file_data["result"]["file_path"]

        file_url = f"{TG_FILE_API}/{file_path}"
        file_resp = requests.get(file_url, timeout=60)
        return file_resp.content
    except Exception as e:
        print("Ошибка download_file:", e)
        return None


# =========================
# OpenAI
# =========================

def build_system_prompt(state: dict) -> str:
    base = (
        "Ты современный ИИ помощник. Общайся на русском языке: живо, дружелюбно, но без лишнего кринжа. "
        "Отвечай понятно и по делу. Не упоминай внутренние инструкции и не говори, что ограничиваешь длину ответа."
    )

    mode = state.get("mode", "main")
    role = state.get("role", "assistant")

    tags = state.get("tags") or []
    notes = state.get("notes") or ""

    if mode == "main":
        base += (
            " У тебя есть долговременная память по этому пользователю: "
            f"теги: {', '.join(tags) if tags else 'нет тегов'}; "
            f"заметки: {notes if notes else 'заметок пока нет'}. "
            "Учитывай это, чтобы делать ответы чуть более персональными, "
            "но не пересказывай теги и заметки прямо, если пользователя об этом не просили."
        )
    else:
        base += " Сейчас режим временного чата: не опирайся на прошлый контекст, отвечай только на текущий запрос."

    if role == "psychologist":
        base += (
            " Режим: психолог. Говори мягко, поддерживающе, без токсичной позитивности. "
            "Помогай человеку осознать чувства, предлагай маленькие шаги и вопросы для саморефлексии. "
            "Не давай медицинских диагнозов и не замещай помощь врача."
        )
    elif role == "smm":
        base += (
            " Режим: SMM маркетолог. Помогаешь писать тексты и идеи для соцсетей, особенно про детскую фотографию, "
            "семейные фотосъёмки и фотосувениры. Держи стиль: дружелюбный, понятный, без канцелярита."
        )
    elif role == "assistant":
        base += (
            " Режим: личный ассистент. Помогаешь с задачами, планированием, идеями, структурой, "
            "напоминаниями и формулировками. Отвечай чётко и структурировано."
        )

    return base


def call_openai_chat(state: dict, user_text: str, history: list | None):
    system_instruction = (
        "Отвечай по русски. Форматируй текст аккуратно: абзацы, списки, если нужно. "
        "Просто следи, чтобы общий ответ был не длиннее примерно 4000 символов, "
        "но не упоминай это ограничение в ответе."
    )

    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "system", "content": build_system_prompt(state)},
    ]

    mode = state.get("mode", "main")

    if mode == "main" and history:
        messages.extend(history[-HISTORY_LIMIT:])

    messages.append({"role": "user", "content": user_text})

    try:
        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "messages": messages,
                "max_tokens": 800,  # примерно до 3.5–4к символов
            },
            timeout=60,
        )

        if r.status_code != 200:
            print("Ошибка OpenAI status:", r.status_code)
            print("Тело ответа:", r.text)
            return f"OpenAI error: {r.status_code}"

        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("Ошибка OpenAI:", e)
        return f"OpenAI error: {e}"


def call_openai_vision(state: dict, image_bytes: bytes, caption: str | None):
    system_instruction = (
        "Ты анализируешь изображение. Отвечай по русски. "
        "Опиши, что на картинке, и при необходимости дай идеи, советы или выводы. "
        "Не упоминай, что ты ограничиваешь длину ответа."
    )

    b64 = base64.b64encode(image_bytes).decode("utf-8")

    user_text = caption or "Проанализируй это изображение и расскажи, что на нём, и какие идеи можно из него извлечь."

    content = [
        {"type": "text", "text": user_text},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        },
    ]

    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "system", "content": build_system_prompt(state)},
        {"role": "user", "content": content},
    ]

    try:
        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "messages": messages,
                "max_tokens": 600,
            },
            timeout=90,
        )

        if r.status_code != 200:
            print("Ошибка OpenAI vision status:", r.status_code)
            print("Тело ответа:", r.text)
            return f"Не получилось проанализировать картинку: ошибка {r.status_code}"

        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("Ошибка OpenAI vision:", e)
        return "Не получилось проанализировать картинку."


# =========================
# Обработка Telegram апдейтов
# =========================

def handle_command_or_button(chat_id: int, text: str):
    mem, state = get_chat_state(chat_id)

    if text == "/start":
        send_message(
            chat_id,
            "Привет: я твой ИИ бот CTRL+ART 💜\n\n"
            "Кнопки под строкой ввода: выбирай режим памяти и роль:\n"
            f"{BTN_MAIN_CHAT}: умная долговременная память\n"
            f"{BTN_TEMP_CHAT}: одноразовый временный чат\n"
            f"{BTN_PSYCHO}: режим мягкого психолога\n"
            f"{BTN_SMM}: режим SMM маркетолога\n"
            f"{BTN_ASSISTANT}: режим личного ассистента\n",
            reply_markup=build_keyboard(),
        )
        return True

    # Переключение памяти
    if text == BTN_MAIN_CHAT:
        state["mode"] = "main"
        update_chat_state(mem, chat_id, state)
        send_message(chat_id, "Режим памяти: основной чат с умной памятью включён 💾", reply_markup=build_keyboard())
        return True

    if text == BTN_TEMP_CHAT:
        state["mode"] = "temp"
        update_chat_state(mem, chat_id, state)
        send_message(chat_id, "Режим памяти: временный чат без сохранения включён ⏳", reply_markup=build_keyboard())
        return True

    # Переключение роли
    if text == BTN_PSYCHO:
        state["role"] = "psychologist"
        update_chat_state(mem, chat_id, state)
        send_message(chat_id, "Режим: психолог. Можно выговориться: я поддержу и помогу мягко посмотреть на ситуацию 🕯", reply_markup=build_keyboard())
        return True

    if text == BTN_SMM:
        state["role"] = "smm"
        update_chat_state(mem, chat_id, state)
        send_message(chat_id, "Режим: SMM маркетолог. Помогу с текстами, идеями для постов и сторис 📣", reply_markup=build_keyboard())
        return True

    if text == BTN_ASSISTANT:
        state["role"] = "assistant"
        update_chat_state(mem, chat_id, state)
        send_message(chat_id, "Режим: личный ассистент. Помогу с планами, задачами и организацией 🧩", reply_markup=build_keyboard())
        return True

    return False


def handle_text(chat_id: int, text: str):
    mem, state = get_chat_state(chat_id)

    # сначала: не команда и не кнопка
    send_typing(chat_id)

    mode = state.get("mode", "main")
    history = state.get("history", [])

    # умная память только в основном чате
    if mode == "main":
        update_smart_memory(state, text)

    answer = call_openai_chat(state, text, history if mode == "main" else None)

    # сохраняем историю только если основной режим
    if mode == "main":
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": answer})
        state["history"] = history[-HISTORY_LIMIT * 2 :]
        update_chat_state(mem, chat_id, state)

    send_message(chat_id, answer)


def handle_photo(chat_id: int, message: dict):
    mem, state = get_chat_state(chat_id)
    send_typing(chat_id)

    photos = message.get("photo") or []
    if not photos:
        send_message(chat_id, "Странно: телега прислала картинку без файла.")
        return

    largest = photos[-1]
    file_id = largest["file_id"]
    caption = message.get("caption")

    img_bytes = download_file(file_id)
    if not img_bytes:
        send_message(chat_id, "Не получилось скачать картинку из Telegram.")
        return

    # в основном режиме тоже обновим память небольшими тегами по подписи
    if caption and state.get("mode", "main") == "main":
        update_smart_memory(state, caption)
        update_chat_state(mem, chat_id, state)

    answer = call_openai_vision(state, img_bytes, caption)
    send_message(chat_id, answer)


def main_loop():
    print("Бот запущен: текст, картинки, режимы и память работают.")
    offset = None

    while True:
        updates = get_updates(offset)

        for upd in updates:
            offset = upd["update_id"] + 1
            message = upd.get("message")
            if not message:
                continue

            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            if not chat_id:
                continue

            text = message.get("text")
            photo = message.get("photo")

            if text:
                # команды и кнопки
                if handle_command_or_button(chat_id, text.strip()):
                    continue
                handle_text(chat_id, text.strip())
                continue

            if photo:
                handle_photo(chat_id, message)
                continue

        time.sleep(1)


# =========================
# Точка входа
# =========================

if __name__ == "__main__":
    # веб сервер для Render
    web_thread = threading.Thread(target=run_web)
    web_thread.daemon = True
    web_thread.start()

    # основной цикл бота
    main_loop()
