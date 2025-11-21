import os
import time
import threading
import requests
from dotenv import load_dotenv
from flask import Flask

# ----------------------- Flask для Render -----------------------

app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ----------------------- Настройки и токены ----------------------

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

# Лимит телеги 4096, оставим запас
MAX_MESSAGE_LENGTH = 3800

# Кнопки меню
BTN_MAIN = "💾 Основной чат"
BTN_TEMP = "⏳ Временный чат"
BTN_PSY = "🧠 Психолог"
BTN_SMM = "📣 SMM-маркетолог"
BTN_ASSIST = "📝 Личный ассистент"

# ----------------------- Память по пользователям -----------------

# структура:
# user_state[chat_id] = {
#     "mode": "main" | "temp",
#     "role": "default" | "psychologist" | "smm" | "assistant",
#     "history": [ {"role": "user"/"assistant", "content": "..."} ]
# }
user_state = {}


def get_user_state(chat_id: int):
    if chat_id not in user_state:
        user_state[chat_id] = {
            "mode": "main",
            "role": "default",
            "history": [],
        }
    return user_state[chat_id]

# ----------------------- Вспомогательные функции -----------------


def get_main_keyboard():
    """Клавиатура с режимами."""
    return {
        "keyboard": [
            [
                {"text": BTN_MAIN},
                {"text": BTN_TEMP},
            ],
            [
                {"text": BTN_PSY},
                {"text": BTN_SMM},
                {"text": BTN_ASSIST},
            ],
        ],
        "resize_keyboard": True,
    }


def clean_markdown(text: str) -> str:
    """
    Убираем заголовки вида ###, ##, # и превращаем их в *жирный текст*,
    чтобы в Телеге не торчали решётки, а всё выглядело аккуратно.
    """
    lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("### "):
            title = stripped[4:]
            lines.append(f"*{title}*")
        elif stripped.startswith("## "):
            title = stripped[3:]
            lines.append(f"*{title}*")
        elif stripped.startswith("# "):
            title = stripped[2:]
            lines.append(f"*{title}*")
        else:
            lines.append(line)
    return "\n".join(lines)


def split_message(text: str, max_len: int = MAX_MESSAGE_LENGTH):
    """
    Делим длинный текст на несколько сообщений, стараясь резать по строкам/пробелам.
    """
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
        text = clean_markdown(text)
        for part in split_message(text):
            payload = {
                "chat_id": chat_id,
                "text": part,
                "parse_mode": "Markdown",  # простой markdown без v2
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
    """Показываем 'бот печатает...'."""
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


# ----------------------- OpenAI: генерация ответа -----------------


def build_system_prompt(state):
    base_parts = [
        "Ты дружелюбный ИИ-бот CTRL+ART AI в Телеграме.",
        "Отвечай на русском языке.",
        "Пиши живо и понятно, без канцелярита и без грубостей.",
        "Укладывайся примерно в 3500 символов, чтобы сообщение помещалось в ограничения Телеграма. Не упоминай это ограничение в ответе.",
        "Используй простое оформление в стиле Telegram Markdown: списки через '-', нумерованные пункты '1.', 2., и выделение важного *жирным* или _курсивом_.",
        "Не используй заголовки с символами '#' или '###', просто делай строку с *жирным текстом* вместо них.",
    ]

    role = state.get("role", "default")

    if role == "psychologist":
        base_parts.append(
            "Сейчас ты в режиме психолога: мягко поддерживаешь, задаёшь уточняющие вопросы, "
            "помогаешь взглянуть на ситуацию с разных сторон. Не даёшь медицинских диагнозов "
            "и не обещаешь чудес. Поощряешь заботу о себе и при необходимости рекомендуешь обратиться "
            "к живому специалисту."
        )
    elif role == "smm":
        base_parts.append(
            "Сейчас ты в режиме SMM-маркетолога: помогаешь с текстами для соцсетей, сторис, "
            "прогревами, продающими и прогревающими постами. Пишешь просто и понятно, учитываешь "
            "целевую аудиторию и тон бренда."
        )
    elif role == "assistant":
        base_parts.append(
            "Сейчас ты в режиме личного ассистента: помогаешь с планированием, задачами, списками дел, "
            "структурой проектов и организацией дня. Отвечаешь чётко и структурированно."
        )
    else:
        base_parts.append(
            "Ты универсальный помощник: можешь и поддержать, и подсказать по делам, и помочь с текстами."
        )

    return " ".join(base_parts)


def ask_ai(chat_id: int, user_text: str) -> str:
    state = get_user_state(chat_id)
    system_prompt = build_system_prompt(state)

    messages = [
        {"role": "system", "content": system_prompt},
    ]

    # В основном чате добавляем кусочек истории
    if state["mode"] == "main":
        history = state.get("history", [])
        # Берём последние 6 сообщений (user+assistant)
        messages.extend(history[-6:])

    messages.append({"role": "user", "content": user_text})

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
                # не слишком большой, чтобы не словить лишние токены
                "max_tokens": 800,
            },
            timeout=60,
        )
        data = r.json()
        answer = data["choices"][0]["message"]["content"]

        # Обновляем историю только в основном чате
        if state["mode"] == "main":
            state["history"].append({"role": "user", "content": user_text})
            state["history"].append({"role": "assistant", "content": answer})
            # Ограничим историю, чтобы не разрасталась
            if len(state["history"]) > 40:
                state["history"] = state["history"][-40:]

        return answer
    except Exception as e:
        print("Ошибка ask_ai:", e)
        return "Что-то пошло не так при обращении к ИИ. Попробуй ещё раз чуть позже."


# ----------------------------- Main loop --------------------------


def handle_mode_buttons(chat_id: int, text: str):
    """Обрабатываем нажатия на кнопки режима/роли."""
    state = get_user_state(chat_id)

    if "Основной чат" in text:
        state["mode"] = "main"
        send_message(
            chat_id,
            "Режим памяти: основной чат с умной памятью включён 💾🧠",
            reply_markup=get_main_keyboard(),
        )
        return True

    if "Временный чат" in text:
        state["mode"] = "temp"
        state["history"] = []
        send_message(
            chat_id,
            "Режим памяти: временный чат без сохранения включён ⏳",
            reply_markup=get_main_keyboard(),
        )
        return True

    if "Психолог" in text:
        state["role"] = "psychologist"
        send_message(
            chat_id,
            "Режим: психолог. Можно выговориться, я поддержу и помогу посмотреть на ситуацию мягко 🧠",
            reply_markup=get_main_keyboard(),
        )
        return True

    if "SMM" in text or "SMM-маркетолог" in text or "смм" in text.lower():
        state["role"] = "smm"
        send_message(
            chat_id,
            "Режим: SMM-маркетолог. Помогу с текстами, идеями для постов и сторис, прогревами и подачей 📣",
            reply_markup=get_main_keyboard(),
        )
        return True

    if "Личный ассистент" in text:
        state["role"] = "assistant"
        send_message(
            chat_id,
            "Режим: личный ассистент. Помогу с планами, задачами и организацией 📝",
            reply_markup=get_main_keyboard(),
        )
        return True

    return False


def main():
    print("Бот запущен: текст, память и режимы психолог / SMM / ассистент.")

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

            state = get_user_state(chat_id)

            # /start
            if text and text.startswith("/start"):
                welcome = (
                    "Привет: я твой ИИ-бот CTRL+ART 💜\n\n"
                    "Я умею отвечать на текстовые сообщения и запоминать контекст в основном чате.\n"
                    "Ниже есть меню с режимами: выбери, как мы сейчас работаем."
                )
                send_message(
                    chat_id,
                    welcome,
                    reply_markup=get_main_keyboard(),
                )
                continue

            # Кнопки режимов
            if text and handle_mode_buttons(chat_id, text):
                continue

            # Если нет текста (фото, стикер и т.п.) — пока просто отвечаем
            if not text:
                send_message(chat_id, "Пока я понимаю только текстовые сообщения 😊")
                continue

            # Обычный текст: спрашиваем ИИ
            send_typing(chat_id)
            ai_answer = ask_ai(chat_id, text)
            send_message(chat_id, ai_answer)

        time.sleep(1)


if __name__ == "__main__":
    # Запускаем Flask в отдельном потоке для Render
    web_thread = threading.Thread(target=run_web)
    web_thread.daemon = True
    web_thread.start()

    main()
