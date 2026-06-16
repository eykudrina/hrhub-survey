from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import anthropic
import os
import json
import requests
import traceback

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

@app.route("/")
def index():
    return "HR HUB Survey API is running"

@app.route("/analyze", methods=["OPTIONS"])
def analyze_options():
    response = Response()
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.json
    answers = data.get("answers", {})
    company = data.get("company", "Ne ukazana")

    summary = (
        "Anonimnyj opros sotrudnika kompanii " + company + ":\n"
        "Energija (1-10): " + str(answers.get("energy", "ne otvetil")) + "\n"
        "Jasnost zadach (1-10): " + str(answers.get("clarity", "ne otvetil")) + "\n"
        "Zagruzka: " + str(answers.get("overload", "ne otvetil")) + "\n"
        "Kommunikacija (1-10): " + str(answers.get("communication", "ne otvetil")) + "\n"
        "Priznanije: " + str(answers.get("recognition", "ne otvetil")) + "\n"
        "Prepjatstvije: " + str(answers.get("obstacles", "ne otvetil")) + "\n"
        "Podderzhka rukovoditelja (1-10): " + str(answers.get("manager", "ne otvetil")) + "\n"
        "Risk ukhoda: " + str(answers.get("leave_risk", "ne otvetil")) + "\n"
        "Effektivnost komandy (1-10): " + str(answers.get("team_result", "ne otvetil")) + "\n"
        "Kommentarij: " + str(answers.get("open_comment", "ne ostavil"))
    )

    system_prompt = (
        "Ty HR-analitik HR HUB. Analizirujesh anonimnyj opros sotrudnika. "
        "Otvechaj TOLKO JSON bez markdown: "
        '{"health_score": chislo ot 0 do 100, '
        '"zone": "red ili yellow ili green", '
        '"metrics": ['
        '{"name": "Energija i motivacija", "score": 0-100, "comment": "1 predlozhenije"}, '
        '{"name": "Zagruzka", "score": 0-100, "comment": "1 predlozhenije"}, '
        '{"name": "Jasnost zadach", "score": 0-100, "comment": "1 predlozhenije"}, '
        '{"name": "Klimat v komande", "score": 0-100, "comment": "1 predlozhenije"}, '
        '{"name": "Risk ukhoda", "score": 0-100, "comment": "1 predlozhenije"}'
        '], '
        '"insights": ["vyvod 1", "vyvod 2", "vyvod 3"], '
        '"priority_action": "samoje vazhnoje 1-2 predlozhenija"} '
        "Bez imjon, bez diagnozov."
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    ai_response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        system=system_prompt,
        messages=[{"role": "user", "content": summary}]
    )

    raw = ai_response.content[0].text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    result = json.loads(raw)

    try:
        zone_map = {"red": "Krasnaja", "yellow": "Zholtaja", "green": "Zeljonaja"}
        zone_label = zone_map.get(result.get("zone", "yellow"), "Zholtaja")
        msg = "Novyj opros HR HUB\n\nKompanija: " + company + "\nIndeks: " + str(result.get("health_score", 0)) + "/100\nZona: " + zone_label + "\n\n"
        for m in result.get("metrics", []):
            msg += m["name"] + ": " + str(m["score"]) + "/100\n"
        msg += "\nPrioritet: " + str(result.get("priority_action", ""))
        tg_resp = requests.post(
            "https://api.telegram.org/bot" + str(TELEGRAM_BOT_TOKEN) + "/sendMessage",
            json={"chat_id": str(TELEGRAM_CHAT_ID), "text": msg[:4000]}
        )
        print("TG status: " + str(tg_resp.status_code), flush=True)
        print("TG body: " + tg_resp.text, flush=True)
    except Exception as e:
        print("TG error: " + str(e), flush=True)
        traceback.print_exc()

    resp = jsonify(result)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
