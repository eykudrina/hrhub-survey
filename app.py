import os
import json
import sqlite3
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic
from datetime import datetime

app = Flask(__name__)
CORS(app)

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "526437687")

DB_PATH = os.environ.get("DB_PATH", "/app/survey.db")

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

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS responses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_code TEXT NOT NULL,
        answers TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''')
    conn.commit()
    conn.close()

init_db()

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    r = requests.post(url, json=payload)
    print(f"TG status: {r.status_code}")
    return r

def generate_manager_report(answers, company_code=""):
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

[Один конкретный практический совет. ВАЖНО: совет должен помочь сотруднику работать эффективнее и приносить больше пользы команде — не про отдых и восстановление, а про действие.]

[Финальная фраза: его ответы помогут сделать команду лучше. Анонимность гарантирована.]

Весь текст — не более 120 слов. Никаких заголовков и маркеров списка."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

def generate_team_report(all_answers, company_code):
    all_text = ""
    for idx, answers in enumerate(all_answers, 1):
        all_text += f"--- Сотрудник {idx} ---\n"
        for i, (q, a) in enumerate(zip(QUESTIONS, answers)):
            all_text += f"{i+1}. {q}\nОтвет: {a}\n"
        all_text += "\n"

    prompt = f"""Ты — эксперт по HR и командной аналитике. Проанализируй результаты анонимного опроса здоровья команды компании {company_code}.

Всего прошли опрос: {len(all_answers)} человек.

Ответы всех сотрудников:
{all_text}

Составь подробный командный отчёт для руководителя. Структура:

🏢 КОМАНДНЫЙ ОТЧЁТ — {company_code.upper()}
Участников: {len(all_answers)}

━━━━━━━━━━━━━━━━━━━━
🔢 ИНДЕКС ЗДОРОВЬЯ КОМАНДЫ: [число от 0 до 100]
[Одна фраза — что это означает для бизнеса]

━━━━━━━━━━━━━━━━━━━━
📊 МЕТРИКИ КОМАНДЫ:
• Средняя вовлечённость: [низкая/средняя/высокая] — [краткий комментарий]
• Загруженность: [распределение — сколько % в норме, сколько перегружены]
• Ясность задач: [низкая/средняя/высокая] — [краткий комментарий]
• Климат в команде: [напряжённый/нейтральный/позитивный] — [краткий комментарий]
• Риск потери людей: [низкий/средний/высокий] — [сколько % рассматривают уход]

━━━━━━━━━━━━━━━━━━━━
🎯 МОТИВАЦИЯ КОМАНДЫ:
[Разбивка по типам мотивации — рост/стабильность/признание/смысл]

━━━━━━━━━━━━━━━━━━━━
🚨 ГЛАВНЫЕ СИГНАЛЫ:
1. [Самая острая проблема]
2. [Второй важный сигнал]
3. [Третий сигнал]

━━━━━━━━━━━━━━━━━━━━
✅ ПРИОРИТЕТНЫЕ ДЕЙСТВИЯ ДЛЯ РУКОВОДИТЕЛЯ:
1. [Конкретное действие — на этой неделе]
2. [Конкретное действие — в течение месяца]
3. [Конкретное действие — стратегически]

━━━━━━━━━━━━━━━━━━━━
💡 ВЫВОД:
[2-3 предложения — общая оценка ситуации и главный фокус]

Будь конкретным. Называй реальные проблемы. Не смягчай."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

@app.route("/submit", methods=["POST"])
def submit():
    try:
        data = request.get_json()
        answers = data.get("answers", [])
        company_code = data.get("company_code", "default").strip().lower()

        if len(answers) < 10:
            return jsonify({"error": "Недостаточно ответов"}), 400

        # Сохраняем в БД
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO responses (company_code, answers, created_at) VALUES (?, ?, ?)",
                  (company_code, json.dumps(answers, ensure_ascii=False), datetime.now().isoformat()))
        conn.commit()

        # Считаем сколько ответов по этой компании
        c.execute("SELECT COUNT(*) FROM responses WHERE company_code=?", (company_code,))
        count = c.fetchone()[0]
        conn.close()

        # Отчёт для руководителя → Telegram
        manager_report = generate_manager_report(answers, company_code)
        send_telegram(f"📋 <b>Новый ответ [{company_code}] ({count} всего)</b>\n\n{manager_report}")

        # Фидбек для сотрудника → браузер
        employee_feedback = generate_employee_feedback(answers)

        return jsonify({"status": "ok", "feedback": employee_feedback, "count": count})

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/report/<company_code>", methods=["GET"])
def team_report(company_code):
    try:
        company_code = company_code.strip().lower()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT answers FROM responses WHERE company_code=? ORDER BY created_at", (company_code,))
        rows = c.fetchall()
        conn.close()

        if not rows:
            return jsonify({"error": f"Нет ответов для компании '{company_code}'"}), 404

        all_answers = [json.loads(row[0]) for row in rows]
        report = generate_team_report(all_answers, company_code)

        resp_data = json.dumps({"status": "ok", "company": company_code, "count": len(all_answers), "report": report}, ensure_ascii=False)
        return app.response_class(resp_data, content_type="application/json; charset=utf-8")

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
