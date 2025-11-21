import os
import time
import json
import base64
import threading
import requests
from dotenv import load_dotenv
from flask import Flask

# ----------------- Flask для Render -----------------

app = Flask(__name__)


@app.route("/")
def index():
    return "Bot is running"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# ----------------- Настройки и переменные -----------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

MEMORY_FILE = "memory.json"
memory_lock = threading.Lock()

# Структура памяти:
# {
#   "chat_id": {
#       "history": [ { "role": "user"|"assistant", "content": "..." }, ... ],
#       "summary": "краткое резюме",
#       "tags": ["тег1", "тег2", ...]
#   },
#   ...
# }
memory_data = {}

# Режим чата: основной с памятью или временный
user_modes = {}      # chat_id -> "main" | "temp"
# Роль бота для пользователя
user_roles = {}      # chat_id -> "psychologist" | "smm" | "assistant"

MAX_MESSAGE_LENGTH = 3800  # запас до лимита 4096


# ----------------- Работа с файлом памяти -----------------

def load_memory():
    global memory_data
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # Простейшая защита от старого формата
        fixed = {}
        for chat_id, val in raw.items():
            if isinstance(val, dict):
                history = val.get("history", [])
                summary = val.get("summary", "")
                tags = val.get("tags", [])
            else:
                # старый формат: просто список сообщений
                history = val
                summary = ""
                tags = []
            fixed[chat_id] = {
                "history": history,
                "summary": summary,
                "tags": tags,
            }
        memory_data = fixed
    except FileNotFoundError:
        memory_data = {}
    except Exception as e:
        print("Ошибка чтения memory.json:", e)
        memory_data = {}


def save_memory():
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Ошибка записи memory.json:", e)


def get_memory_entry(chat_id: int):
    user_key = str(chat_id)
    with memory_lock:
        entry = memory_data.get(user_key)
        if not entry:
            entry = {"history": [], "summary": "", "tags": []}
            memory_data[user_key] = entry
    return entry


# ----------------- Телеграм утилиты -----------------

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
        first = True
        for part in split_message(text):
            payload = {
                "chat_id": chat_id,
                "text": part,
            }
            if first and reply_markup is not None:
                payload["reply_markup"] = reply_markup
                first = False
            requests.post(
                f"{TG_API}/sendMessage",
                json=payload,
                timeout=20,
            )
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


def download_file(file_id: str):
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


# ----------------- Системный промпт и роли -----------------

def build_role_instruction(role: str) -> str:
    if role == "psychologist":
        return (
            "Сейчас ты работаешь в роли бережного психолога: помогаешь разбираться в эмоциях, "
            "поддерживаешь, задаешь мягкие вопросы, даешь аккуратные рекомендации и опору. "
            "Не ставишь диагнозы, не назначаешь лечение, не даешь медицинских советов."
        )
    if role == "smm":
        return (
            "Сейчас ты работаешь в роли SMM маркетолога: помогаешь с текстами для соцсетей, "
            "идеями для постов, сторис и рилсов, контент планом, акциями, визуальными идеями. "
            "Давай конкретику: формулировки заголовков, примеры текстов, идеи для рубрик."
        )
    # личный ассистент по умолчанию
    return (
        "Сейчас ты работаешь в роли личного ассистента: помогаешь планировать задачи, "
        "структурировать дела, придумывать чек листы, напоминания, планы и списки. "
        "Помогай делать жизнь пользователя проще и спокойнее."
    )


def build_system_message(chat_id: int):
    role = user_roles.get(chat_id, "assistant")

    base_prompt = (
        "Ты дружелюбный ИИ помощник CTRL+ART для Арины из компании Твой Кадр. "
        "Отвечай по русски, если пользователь пишет по русски. "
        "Пиши как живая подружка: можно немного эмодзи, но без перегруза. "
        "Форматируй текст абзацами и простыми списками без Markdown разметки: "
        "не используй звездочки, решетки, подчеркивания и длинное тире. "
        "Вместо длинного тире используй двоеточие или обычное короткое тире. "
        "Не упоминай лимиты символов или технические детали. "
    )

    role_part = build_role_instruction(role)

    full = base_prompt + " " + role_part
    return {"role": "system", "content": full}


def build_messages_for_chat(chat_id: int, mode: str, user_content):
    """
    user_content:
      текст: строка
      картинка: список объектов формата content для Chat Completions
    """
    messages = [build_system_message(chat_id)]

    if mode == "main":
        entry = get_memory_entry(chat_id)
        summary = entry.get("summary") or ""
        tags = entry.get("tags") or []
        history = entry.get("history") or []

        if summary or tags:
            tags_part = ", ".join(tags) if tags else ""
            extra = "Краткое резюме прошлых диалогов с пользователем: " + summary
            if tags_part:
                extra += f"\nКлючевые темы: {tags_part}"
            messages.append({"role": "system", "content": extra})

        # добавляем историю
        messages.extend(history)

    # текущее сообщение пользователя
    messages.append({"role": "user", "content": user_content})
    return messages


# ----------------- Вызов OpenAI -----------------

def call_openai_chat(messages):
    try:
        r = requests.post(
            OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-5.1-chat-latest",
                "messages": messages,
                "max_tokens": 800,
            },
            timeout=60,
        )
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("Ошибка call_openai_chat:", e)
        return "Что то пошло не так при обращении к ИИ."


def update_summary_and_tags(chat_id: int):
    """
    Пересчитываем краткое резюме и теги по истории диалога.
    """
    user_key = str(chat_id)
    with memory_lock:
        entry = memory_data.get(user_key)
        if not entry:
            return
        history = entry.get("history", []).copy()

    if not history:
        return

    # Берем последние 20 сообщений
    last_msgs = history[-20:]
    text_parts = []
    for msg in last_msgs:
        prefix = "Пользователь" if msg["role"] == "user" else "Ассистент"
        text_parts.append(f"{prefix}: {msg['content']}")
    dialog_text = "\n".join(text_parts)

    prompt = (
        "Проанализируй диалог ниже и сделай два пункта.\n"
        "1: Краткое резюме в два четыре предложения: что за пользователь, чем занимается, что важно.\n"
        "2: Строка с тегами вида: Теги: тег1, тег2, тег3. "
        "Теги короткие по русски: максимум шесть штук, без хештегов.\n\n"
        "Диалог:\n" + dialog_text
    )

    messages = [
        {"role": "system", "content": "Ты помогаешь структурировать диалог и выделять ключевые темы."},
        {"role": "user", "content": prompt},
    ]

    result = call_openai_chat(messages)
    summary = result.strip()
    tags_list = []

    # Пытаемся вытащить строку с тегами
    lower = result.lower()
    idx = lower.rfind("теги:")
    if idx != -1:
        summary_part = result[:idx].strip()
        tags_part = result[idx + len("теги:") :].strip()
        summary = summary_part
        tags_raw = tags_part.split(",")
        tags_list = [t.strip() for t in tags_raw if t.strip()]

    with memory_lock:
        entry = memory_data.get(user_key, {"history": [], "summary": "", "tags": []})
        entry["summary"] = summary
        entry["tags"] = tags_list
        memory_data[user_key] = entry
        save_memory()


def handle_main_chat(chat_id: int, user_text: str) -> str:
    mode = "main"
    messages = build_messages_for_chat(chat_id, mode, user_text)
    answer = call_openai_chat(messages)

    user_key = str(chat_id)
    with memory_lock:
        entry = memory_data.get(user_key)
        if not entry:
            entry = {"history": [], "summary": "", "tags": []}
        history = entry.get("history", [])
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": answer})

        # ограничиваем историю
        max_turns = 15
        if len(history) > max_turns * 2:
            history = history[-max_turns * 2 :]

        entry["history"] = history
        memory_data[user_key] = entry
        save_memory()

        # иногда обновляем резюме и теги
        if len(history) % 10 == 0 or not entry.get("summary"):
            # запускаем обновление в отдельном потоке чтобы не тормозить ответ
            threading.Thread(target=update_summary_and_tags, args=(chat_id,), daemon=True).start()

    return answer


def handle_temp_chat(chat_id: int, user_text: str) -> str:
    mode = "temp"
    messages = build_messages_for_chat(chat_id, mode, user_text)
    answer = call_openai_chat(messages)
    return answer


def handle_image(chat_id: int, caption: str, img_bytes: bytes) -> str:
    """
    Анализ картинки. В основном чате учитываем память, в временном нет.
    """
    mode = user_modes.get(chat_id, "main")

    try:
        b64 = base64.b64encode(img_bytes).decode("utf-8")
    except Exception as e:
        print("Ошибка base64:", e)
        return "Не получилось обработать картинку."

    data_url = f"data:image/jpeg;base64,{b64}"

    user_content = [
        {"type": "text", "text": caption},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]

    messages = build_messages_for_chat(chat_id, mode, user_content)
    answer = call_openai_chat(messages)

    if mode == "main":
        user_key = str(chat_id)
        short_caption = caption + " [пользователь прислал картинку]"
        with memory_lock:
            entry = memory_data.get(user_key)
            if not entry:
                entry = {"history": [], "summary": "", "tags": []}
            history = entry.get("history", [])
            history.append({"role": "user", "content": short_caption})
            history.append({"role": "assistant", "content": answer})

            max_turns = 15
            if len(history) > max_turns * 2:
                history = history[-max_turns * 2 :]

            entry["history"] = history
            memory_data[user_key] = entry
            save_memory()

            if len(history) % 10 == 0 or not entry.get("summary"):
                threading.Thread(target=update_summary_and_tags, args=(chat_id,), daemon=True).start()

    return answer


# ----------------- Обработка команд и режимов -----------------

def handle_start(chat_id: int):
    user_modes[chat_id] = "main"
    user_roles[chat_id] = "assistant"

    keyboard = {
        "keyboard": [
            [{"text": "🧠 Основной чат"}, {"text": "⚡ Временный чат"}],
            [{"text": "🪄 Психолог"}, {"text": "📣 SMM маркетолог"}, {"text": "🤝 Личный ассистент"}],
        ],
        "resize_keyboard": True,
    }

    text = (
        "Привет: я твой ИИ бот CTRL+ART 💜\n\n"
        "Режимы памяти:\n"
        "🧠 Основной чат: я помню контекст, темы и твои предпочтения, сохраняю краткое резюме и теги.\n"
        "⚡ Временный чат: отвечаю только на текущее сообщение, без сохранения памяти.\n\n"
        "Роли:\n"
        "🪄 Психолог: поддержка, эмоции, разбор переживаний.\n"
        "📣 SMM маркетолог: тексты для соцсетей, контент, идеи постов.\n"
        "🤝 Личный ассистент: планы, списки, организация дел.\n\n"
        "Выбери режим и роль кнопками ниже и просто пиши свои запросы."
    )

    send_message(chat_id, text, reply_markup=keyboard)


def handle_mode_switch(chat_id: int, text: str) -> bool:
    """
    Возвращает True если это был переключатель режима и мы его обработали.
    """
    if text == "🧠 Основной чат":
        user_modes[chat_id] = "main"
        send_message(chat_id, "Основной чат включен: я буду помнить наши разговоры и темы 💜")
        return True

    if text == "⚡ Временный чат":
        user_modes[chat_id] = "temp"
        send_message(chat_id, "Временный чат включен: отвечаю только на текущее сообщение ⚡")
        return True

    if text == "🪄 Психолог":
        user_roles[chat_id] = "psychologist"
        send_message(chat_id, "Режим: психолог. Можно выговориться, я поддержу и помогу посмотреть на ситуацию мягко 🪄")
        return True

    if text == "📣 SMM маркетолог":
        user_roles[chat_id] = "smm"
        send_message(chat_id, "Режим: SMM маркетолог. Готова помочь с текстами, идеями и контентом 📣")
        return True

    if text == "🤝 Личный ассистент":
        user_roles[chat_id] = "assistant"
        send_message(chat_id, "Режим: личный ассистент. Помогу разложить дела по полочкам 🤝")
        return True

    return False


# ----------------- Основной цикл бота -----------------

def main():
    print("Бот запущен: основная память, временный чат, роли и анализ картинок.")
    load_memory()

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
            photos = message.get("photo")

            # Инициализация режимов по умолчанию
            if chat_id not in user_modes:
                user_modes[chat_id] = "main"
            if chat_id not in user_roles:
                user_roles[chat_id] = "assistant"

            # /start
            if text and text.startswith("/start"):
                handle_start(chat_id)
                continue

            # Переключение режимов и ролей
            if text and handle_mode_switch(chat_id, text):
                continue

            # Картинка
            if photos:
                send_typing(chat_id)
                file_id = photos[-1]["file_id"]
                img_bytes = download_file(file_id)
                if not img_bytes:
                    send_message(chat_id, "Не получилось скачать картинку.")
                    continue

                caption = text or "Опиши эту картинку и дай комментарии."
                answer = handle_image(chat_id, caption, img_bytes)
                send_message(chat_id, answer)
                continue

            # Обычный текст
            if text:
                send_typing(chat_id)
                mode = user_modes.get(chat_id, "main")
                if mode == "temp":
                    answer = handle_temp_chat(chat_id, text)
                else:
                    answer = handle_main_chat(chat_id, text)
                send_message(chat_id, answer)
                continue

        time.sleep(1)


# ----------------- Точка входа -----------------

if __name__ == "__main__":
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    main()
