import os
import time
import json
import threading
import requests
from dotenv import load_dotenv
from flask import Flask

# ======================
# Настройки
# ======================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.1-mini")  # модель текст + картинки

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

# кнопки меню
BTN_MAIN_CHAT = "💬 Основной чат"
BTN_TEMP_CHAT = "⏳ Временный чат"
BTN_PSYCHO = "🪷 Психолог"
BTN_SMM = "📣 SMM маркетолог"
BTN_ASSISTANT = "🧠 Личный ассистент"

# лимит Телеграма 4096, берем запас
MAX_MESSAGE_LENGTH = 3800

# файл для долгой памяти
MEMORY_FILE = "memory.json"

# ======================
# Flask для Render
# ======================

app = Flask(__name__)


@app.route("/")
def index():
    return "Bot is running"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# ======================
# Работа с памятью
# ======================

def load_memory_from_file():
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_memory_to_file():
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Ошибка сохранения памяти:", e)


memory = load_memory_from_file()


def get_chat_state(chat_id: int):
    """Получаем или создаем блок памяти для конкретного чата."""
    cid = str(chat_id)
    if cid not in memory:
        memory[cid] = {
            "persona": "assistant",        # assistant | psychologist | smm
            "memory_mode": "main",         # main | temp
            "history": [],                 # последние сообщения для контекста
            "profile": "",                 # умная сводка о пользователе
            "last_profile_update": 0.0     # время последнего обновления профиля
        }
    return memory[cid]


def update_profile_from_history(chat_id: int):
    """
    Обновляем умную сводку о пользователе:
    короткое резюме + теги по содержанию.
    Делаем это не чаще, чем раз в несколько минут и только при длинном диалоге.
    """
    state = get_chat_state(chat_id)
    history = state.get("history", [])

    if len(history) < 10:
        return

    now = time.time()
    if now - state.get("last_profile_update", 0) < 300:
        return

    # собираем текст диалога
    dialog_text = ""
    for msg in history[-20:]:
        role = "Пользователь" if msg["role"] == "user" else "Бот"
        dialog_text += f"{role}: {msg['content']}\n"

    system_prompt = (
        "Ты помощник, который пишет краткую умную память о пользователе по диалогу.\n"
        "1: Выдели кто он, чем занимается, его интересы и цели.\n"
        "2: В конце добавь строку вида: теги: слово1, слово2, слово3.\n"
        "3: Пиши по русски, максимум 120 слов."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": dialog_text},
    ]

    summary = call_openai(messages)

    if summary:
        state["profile"] = summary
        state["last_profile_update"] = now
        save_memory_to_file()


# ======================
# Вспомогательные функции
# ======================

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
            }
            if reply_markup is not None:
                payload["reply_markup"] = reply_markup

            requests.post(
                f"{TG_API}/sendMessage",
                json=payload,
                timeout=15,
            )
            # разметку достаточно добавить только к первому куску
            reply_markup = None
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


def build_menu_keyboard():
    return {
        "keyboard": [
            [BTN_MAIN_CHAT, BTN_TEMP_CHAT],
            [BTN_ASSISTANT, BTN_SMM, BTN_PSYCHO],
        ],
        "resize_keyboard": True,
    }


def send_menu(chat_id, extra_text=None):
    text = (
        "Выбери режим работы:\n"
        f"{BTN_MAIN_CHAT}: бот помнит контекст и сохраняет умную память.\n"
        f"{BTN_TEMP_CHAT}: разовый диалог без сохранения.\n\n"
        f"{BTN_ASSISTANT}: обычный умный помощник.\n"
        f"{BTN_SMM}: консультации по контенту и маркетингу.\n"
        f"{BTN_PSYCHO}: мягкая поддержка и разговор по душам."
    )
    if extra_text:
        text = extra_text + "\n\n" + text

    send_message(chat_id, text, reply_markup=build_menu_keyboard())


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


def call_openai(messages):
    """
    Универсальный вызов OpenAI: принимает список messages.
    Подходит и для текста и для картинок (когда content: массив).
    """
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
                "max_tokens": 700,
            },
            timeout=80,
        )
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print("Ошибка OpenAI:", e)
        try:
            print("Ответ OpenAI:", r.text)  # type: ignore
        except Exception:
            pass
        return "Что то пошло не так при обращении к ИИ."


def build_system_prompt(chat_id: int, persona: str):
    base = (
        "Ты ИИ помощник CTRL+ART AI для Арины. Отвечай всегда по русски, "
        "дружелюбно и по делу. Если можно: давай конкретные шаги и примеры."
    )

    if persona == "psychologist":
        base += (
            "\nСейчас ты работаешь как поддерживающий психолог: слушаешь, "
            "задаешь мягкие вопросы, помогаешь увидеть варианты. "
            "Не ставь диагнозы и не обещай вылечить, если речь о тяжелом состоянии: "
            "в этом случае мягко рекомендуй обратиться к специалисту."
        )
    elif persona == "smm":
        base += (
            "\nСейчас ты работаешь как опытный SMM маркетолог для фото бизнеса. "
            "Помогай с текстами, идеями постов, прогревами, воронками, анализом "
            "целевой аудитории. Учитывай, что бизнес связан с детской и семейной "
            "фотосъемкой, магнитами, печатью фото."
        )
    else:
        base += (
            "\nСейчас ты работаешь как личный ассистент: помогаешь планировать дела, "
            "структурировать задачи, напоминать про важное, продумывать шаги."
        )

    # добавляем умную память, если она есть
    state = get_chat_state(chat_id)
    profile = state.get("profile")
    if profile:
        base += (
            "\n\nНиже краткая сводка о пользователе и тегах. "
            "Используй ее, чтобы отвечать более лично, но не цитируй дословно:\n"
            f"{profile}"
        )

    return base


def prepare_user_text_with_limit(text: str) -> str:
    """
    Добавляем скрытую инструкцию про лимит Телеграма.
    Пользователь это не видит, но модель учитывает.
    """
    return (
        text.strip()
        + "\n\nВажное требование: ответ должен целиком помещаться в одно сообщение "
        "Telegram до 4000 символов. Не упоминай это ограничение и не говори про лимиты, "
        "просто делай ответ достаточно компактным и по сути."
    )


# ======================
# Обработка сообщений
# ======================

def handle_text_message(chat_id: int, text: str):
    state = get_chat_state(chat_id)
    persona = state["persona"]
    memory_mode = state["memory_mode"]

    # обработка команд меню
    if text == "/start":
        send_message(
            chat_id,
            "Привет: я твой ИИ бот CTRL+ART 💜\n"
            "Я умею отвечать на текст и анализировать картинки.\n"
            "Ниже есть меню: выбери режим работы.",
            reply_markup=build_menu_keyboard(),
        )
        return

    # переключение памяти
    if text == BTN_MAIN_CHAT:
        state["memory_mode"] = "main"
        save_memory_to_file()
        send_message(chat_id, "Режим памяти: основной чат с умной памятью включен 💾")
        return

    if text == BTN_TEMP_CHAT:
        state["memory_mode"] = "temp"
        save_memory_to_file()
        send_message(chat_id, "Режим памяти: временный чат без сохранения включен 🧹")
        return

    # переключение роли
    if text == BTN_PSYCHO:
        state["persona"] = "psychologist"
        save_memory_to_file()
        send_message(
            chat_id,
            "Режим: психолог. Можно выговориться, я поддержу и помогу посмотреть "
            "на ситуацию мягко 🌿",
        )
        return

    if text == BTN_SMM:
        state["persona"] = "smm"
        save_memory_to_file()
        send_message(
            chat_id,
            "Режим: SMM маркетолог. Задавай вопросы про контент, тексты и продвижение 📣",
        )
        return

    if text == BTN_ASSISTANT:
        state["persona"] = "assistant"
        save_memory_to_file()
        send_message(
            chat_id,
            "Режим: личный ассистент. Помогу с планами, задачами и организацией 🧠",
        )
        return

    # обычный запрос
    send_typing(chat_id)

    user_text_for_model = prepare_user_text_with_limit(text)
    system_prompt = build_system_prompt(chat_id, persona)

    messages = [
        {"role": "system", "content": system_prompt},
    ]

    if memory_mode == "main":
        # добавляем историю
        messages += get_chat_state(chat_id)["history"]
        messages.append({"role": "user", "content": user_text_for_model})
        answer = call_openai(messages)

        # сохраняем в историю и память
        state["history"].append({"role": "user", "content": text})
        state["history"].append({"role": "assistant", "content": answer})
        # ограничиваем длину истории
        state["history"] = state["history"][-40:]
        save_memory_to_file()
        update_profile_from_history(chat_id)
    else:
        # временный диалог: без истории
        messages.append({"role": "user", "content": user_text_for_model})
        answer = call_openai(messages)

    send_message(chat_id, answer, reply_markup=build_menu_keyboard())


def get_file_url(file_id: str) -> str:
    """Получаем прямую ссылку на файл Телеграма."""
    try:
        r = requests.get(
            f"{TG_API}/getFile",
            params={"file_id": file_id},
            timeout=20,
        )
        data = r.json()
        file_path = data["result"]["file_path"]
        return f"{TG_FILE_API}/{file_path}"
    except Exception as e:
        print("Ошибка get_file_url:", e)
        return ""


def handle_photo_message(chat_id: int, message: dict):
    state = get_chat_state(chat_id)
    persona = state["persona"]
    memory_mode = state["memory_mode"]

    photos = message.get("photo", [])
    if not photos:
        return

    # берем самую большую версию
    file_id = photos[-1]["file_id"]
    image_url = get_file_url(file_id)
    if not image_url:
        send_message(chat_id, "Не получилось получить файл изображения.")
        return

    caption = message.get("caption") or ""
    user_request = caption.strip() or "Проанализируй и опиши это изображение."

    send_typing(chat_id)

    system_prompt = build_system_prompt(chat_id, persona)
    user_text_for_model = prepare_user_text_with_limit(user_request)

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text_for_model},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        },
    ]

    answer = call_openai(messages)

    # в основной памяти фиксируем только факт картинки и подпись
    if memory_mode == "main":
        state["history"].append(
            {
                "role": "user",
                "content": f"[картинка] {caption or 'без подписи'}",
            }
        )
        state["history"].append({"role": "assistant", "content": answer})
        state["history"] = state["history"][-40:]
        save_memory_to_file()
        update_profile_from_history(chat_id)

    send_message(chat_id, answer, reply_markup=build_menu_keyboard())


# ======================
# Основной цикл бота
# ======================

def main():
    print("Бот запущен: текст, картинки и режимы психолог / SMM / ассистент.")
    offset = None

    while True:
        updates = get_updates(offset)

        for upd in updates:
            try:
                offset = upd["update_id"] + 1
                message = upd.get("message") or upd.get("edited_message")
                if not message:
                    continue

                chat_id = message["chat"]["id"]
                text = message.get("text")
                photo = message.get("photo")

                if photo:
                    handle_photo_message(chat_id, message)
                    continue

                if text:
                    handle_text_message(chat_id, text)
                    continue

            except Exception as e:
                print("Ошибка обработки апдейта:", e)

        time.sleep(1)


if __name__ == "__main__":
    # запускаем бота в отдельном потоке и Flask сервер для Render
    bot_thread = threading.Thread(target=main, daemon=True)
    bot_thread.start()
    run_web()
