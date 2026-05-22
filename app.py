from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import anthropic
import os
import json
import requests

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

@app.route("/")
def index():
    return "HR HUB Survey API is running"

@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.json
    answers = data.get("answers", {})
    company = data.get("company", "Не указана")

    summary = f"""Анонимный опрос сотрудника компании {company}:
Энергия (1-10): {answers.get('energy', 'не ответил')}
Ясность задач (1-10): {answers.get('clarity', 'не ответил')}
Загрузка: {answers.get('overload', 'не ответил')}
Коммуникация (1-10): {answers.get('communication', 'не ответил')}
Признание: {answers.get('recognition', 'не ответил')}
Препятствие: {answers.get('obstacles', 'не ответил')}
Поддержка руководителя (1-10): {answers.get('manager', 'не ответил')}
Риск ухода: {answers.get('leave_risk', 'не ответил')}
Эффективность команды (1-10): {answers.get('team_result', 'не ответил')}
Комментарий: {answers.get('open_comment', 'не оставил')}"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system="""Ты HR-аналитик HR HUB. Анализируешь анонимный опрос сотрудника.
Отвечай ТОЛЬКО JSON без markdown:
{
  "health_score": число от 0 до 100,
  "zone": "red или yellow или green",
  "metrics": [
    {"name": "Энергия и мотивация", "score": 0-100, "comment": "1 предложение"},
    {"name": "Загрузка", "score": 0-100, "comment": "1 предложение"},
    {"name": "Ясность задач", "score": 0-100, "comment": "1 предложение"},
    {"name": "Климат в команде", "score": 0-100, "comment": "1 предложение"},
    {"name": "Риск ухода", "score": 0-100, "comment": "1 предложение"}
  ],
  "insights": ["вывод 1", "вывод 2", "вывод 3"],
  "priority_action": "самое важное — 1-2 предложения"
}
Без имён, без диагнозов.""",
        messages=[{"role": "user", "content": summary}]
    )

    raw = response.content[0].text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    result = json.loads(raw)

    try:
        zone_label = {"red": "Красная", "yellow": "Желтая", "green": "Зеленая"}.get(result.get("zone", "yellow"), "Желтая")
        msg = f"Новый опрос HR HUB\n\nКомпания: {company}\nИндекс: {result.get('health_score', 0)}/100\nЗона: {zone_label}\n\n"
        for m in result.get("metrics", []):
            msg += f"{m['name']}: {m['score']}/100\n"
        msg += f"\nПриоритет: {result.get('priority_action', '')}"
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                     json={"chat_id": TELEGRAM_CHAT_ID, "text": msg[:4000]})
    except Exception as e:
        print(f"TG error: {e}")

    return jsonify(result)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
