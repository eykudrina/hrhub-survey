import os
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic

app = Flask(__name__)
CORS(app)

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "526437687")

QUESTIONS = [
    "Как бы вы оценили свой уровень энергии и мотивации на работе?",
    "Насколько вы понимаете свои задачи и приоритеты?",
    "Как вы оцениваете свою загруженность?",
    "Насколько вы довольны атмосферой в команде?",
    "Получаете ли вы достаточно обратной связи от руководителя?",
    "Насколько вы понимаете, как ваша работа влияет на результат компании?",
    "Есть ли у вас ресурсы и инструменты для качественного выполнения задач?",
    "Как вы оцениваете свои перспективы роста в компании?",
    "Насколько вы удовлетворены своей работой в целом?",
    "Рассматриваете ли вы другие предложения о работе?",
    "Что для вас важнее всего в работе прямо сейчас?"
]

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    r = requests.post(url, json=payload)
    print(f"TG status: {r.status_code}, TG body: {r.text}")
    return r

def generate_manager_report(answers):
    """Аналитический отчёт для руководителя в Telegram"""
    answers_text = ""
    for i, (q, a) in enumerate(zip(QUESTIONS, answers)):
        answers_text += f"{i+1}. {q}\nОтвет: {a}\n\n"

    prompt = f"""Ты — эксперт по HR и командной аналитике. Проанализируй ответы сотрудника на опрос здоровья команды.

Ответы сотрудника:
{answers_text}

Составь краткий аналитический отчёт для руководителя компании. Структура:

🔢 ИНДЕКС ЗДОРОВЬЯ: [число от 0 до 100]

📊 КЛЮЧЕВЫЕ МЕТРИКИ:
• Вовлечённость: [низкая/средняя/высокая]
• Загруженность: [низкая/нормальная/высокая/критическая]
• Ясность задач: [низкая/средняя/высокая]
• Климат: [напряжённый/нейтральный/позитивный]
• Риск ухода: [низкий/средний/высокий]

⚡ ГЛАВНЫЙ СИГНАЛ:
[1-2 предложения — самое важное что нужно знать руководителю]

✅ ПРИОРИТЕТНОЕ ДЕЙСТВИЕ:
[Конкретное действие для руководителя на этой неделе]

Будь конкретным и честным. Не смягчай проблемы."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

def generate_employee_feedback(answers):
    """Персональный фидбек для сотрудника — тёплый, без цифр, советы в интересах компании"""
    answers_text = ""
    for i, (q, a) in enumerate(zip(QUESTIONS, answers)):
        answers_text += f"{i+1}. {q}\nОтвет: {a}\n\n"

    prompt = f"""Ты — опытный HR-консультант и бизнес-психолог. Сотрудник прошёл анонимный опрос здоровья команды.

Ответы сотрудника:
{answers_text}

Напиши персональный фидбек ДЛЯ СОТРУДНИКА. Правила:

1. Тон — тёплый, уважительный, живой. Не корпоративный и не психотерапевтический.
2. БЕЗ цифр, баллов, индексов, слов "риск", "показатель", "метрика".
3. Структура ровно такая:

[Одна фраза благодарности — живая, не казённая]

[2-3 предложения отражения состояния — что ты видишь в его ответах, что он сейчас переживает. Точно и по-человечески.]

[Один конкретный практический совет. ВАЖНО: совет должен помочь сотруднику работать эффективнее и приносить больше пользы команде — не про отдых и восстановление, а про действие. Например: обсудить приоритеты с руководителем, предложить решение конкретной проблемы, взять на себя инициативу в чём-то конкретном.]

[Финальная фраза: его ответы помогут сделать команду лучше. Анонимность гарантирована.]

Весь текст — не более 120 слов. Никаких заголовков и маркеров списка."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

@app.route("/submit", methods=["POST"])
def submit():
    try:
        data = request.get_json()
        answers = data.get("answers", [])

        if len(answers) < 10:
            return jsonify({"error": "Недостаточно ответов"}), 400

        # Отчёт для руководителя → Telegram
        manager_report = generate_manager_report(answers)
        send_telegram(f"📋 <b>Новый ответ на опрос команды</b>\n\n{manager_report}")

        # Фидбек для сотрудника → возвращаем в браузер
        employee_feedback = generate_employee_feedback(answers)

        return jsonify({
            "status": "ok",
            "feedback": employee_feedback
        })

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
