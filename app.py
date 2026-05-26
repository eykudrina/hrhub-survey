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

    overload_options = ["Ne zagruzhen", "V norme", "Nemnogo peregruzhen", "Seryoznyy peregruz", "Na predele"]
    recognition_options = ["Da regulyarno", "Inogda no redko", "Pochti nikogda", "Mne ne vazhno"]
    obstacles_options = ["Neponyatnye zadachi", "Konflikty v komande", "Slishkom mnogo zadach", "Ne khvataet instrumentov", "Net motivatsii", "Net prepyatstviy"]
    leave_options = ["Net vse ustraivaet", "Inogda myasli", "Dumayu ob etom chasto", "Aktivno ishchu"]

    summary = (
        "Anonimnyy opros sotrudnika kompanii " + str(company) + ":\n"
        "Energiya (1-10): " + str(answers.get("energy", "?")) + "\n"
        "Yasnost zadach (1-10): " + str(answers.get("clarity", "?")) + "\n"
        "Zagruzka: " + str(overload_options[answers.get("overload", 1)] if isinstance(answers.get("overload"), int) else "?") + "\n"
        "Kommunikatsiya (1-10): " + str(answers.get("communication", "?")) + "\n"
        "Priznaniye: " + str(recognition_options[answers.get("recognition", 0)] if isinstance(answers.get("recognition"), int) else "?") + "\n"
        "Prepyatstviye: " + str(obstacles_options[answers.get("obstacles", 5)] if isinstance(answers.get("obstacles"), int) else "?") + "\n"
        "Podderzhka rukovoditelya (1-10): " + str(answers.get("manager", "?")) + "\n"
        "Risk ukhoda: " + str(leave_options[answers.get("leave_risk", 0)] if isinstance(answers.get("leave_risk"), int) else "?") + "\n"
        "Effektivnost komandy (1-10): " + str(answers.get("team_result", "?")) + "\n"
        "Kommentariy: " + str(answers.get("open_comment", "net"))
    )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # === АНАЛИЗ ДЛЯ РУКОВОДИТЕЛЯ ===
    manager_system = (
        "Ty HR-analitik HR HUB. Analiziruy anonimnyy opros sotrudnika. "
        "Otvechay TOLKO JSON bez markdown i bez tройных кавычек: "
        "{\"health_score\": chislo 0-100, "
        "\"zone\": \"red ili yellow ili green\", "
        "\"metrics\": ["
        "{\"name\": \"Energiya i motivatsiya\", \"score\": 0-100, \"comment\": \"1 predlozheniye\"},"
        "{\"name\": \"Zagruzka\", \"score\": 0-100, \"comment\": \"1 predlozheniye\"},"
        "{\"name\": \"Yasnost zadach\", \"score\": 0-100, \"comment\": \"1 predlozheniye\"},"
        "{\"name\": \"Klimat v komande\", \"score\": 0-100, \"comment\": \"1 predlozheniye\"},"
        "{\"name\": \"Risk ukhoda\", \"score\": 0-100, \"comment\": \"1 predlozheniye\"}"
        "], "
        "\"insights\": [\"vyvod1\", \"vyvod2\", \"vyvod3\"], "
        "\"priority_action\": \"1-2 predlozheniya chto sdelat SEYCHAS\"} "
        "Bez imen, bez diagnozov."
    )

    manager_response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        system=manager_system,
        messages=[{"role": "user", "content": summary}]
    )

    raw = manager_response.content[0].text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    manager_result = json.loads(raw)

    # === АНАЛИЗ ДЛЯ СОТРУДНИКА ===
    employee_system = (
        "Ty drug-psykholog, kotoryy razgovarivaet s chelovekom posle ego otrytogo otveta na opros o sostoyanii na rabote. "
        "Tvoya zadacha: dat chelovekuощущение что ego uslышали, ponyali i podderzhali. "
        "Govori тепло, prosto, bez HR-zhargona i bez tsifr. "
        "Otvechay TOLKO JSON bez markdown: "
        "{\"greeting\": \"1-2 predlozheniya: priznat sostoyanie cheloveka chelovecheski, bez otsenki\", "
        "\"zones\": ["
        "{\"title\": \"nazvanie zony (napr: Energiya)\", \"signal\": \"chto zamecheno prosто i teplo\", \"benchmark\": \"eto norma ili net - kak drug skazal by\", \"advice\": \"1 konkretniy sovуet chto mozhet pomoch\"},"
        "{\"title\": \"nazvanie zony 2\", \"signal\": \"...\", \"benchmark\": \"...\", \"advice\": \"...\"},"
        "{\"title\": \"nazvanie zony 3\", \"signal\": \"...\", \"benchmark\": \"...\", \"advice\": \"...\"}"
        "], "
        "\"closing\": \"teploe zaklyucheniye: 1-2 predlozheniya podderzhki i nadezhdy\"} "
        "Pishi TOLKO pro zony gde est problema ili napryazheniye. Esli vsyo khorosho - pishi 1-2 zony s podderzhkoy. "
        "Yazyk: russkiy, razgovorniy, kak drug a ne spetsialist."
    )

    employee_response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        system=employee_system,
        messages=[{"role": "user", "content": summary}]
    )

    raw_emp = employee_response.content[0].text.strip()
    raw_emp = raw_emp.replace("```json", "").replace("```", "").strip()
    employee_result = json.loads(raw_emp)

    # === ОТПРАВКА РУКОВОДИТЕЛЮ В TELEGRAM ===
    try:
        zone_map = {"red": "Krasnaya", "yellow": "Zhyoltaya", "green": "Zelonaya"}
        zone_label = zone_map.get(manager_result.get("zone", "yellow"), "Zhyoltaya")
        zone_emoji = {"red": "red_circle", "yellow": "yellow_circle", "green": "green_circle"}.get(manager_result.get("zone", "yellow"), "yellow_circle")

        msg = "Novyy opros HR HUB\n\n"
        msg += "Kompaniya: " + str(company) + "\n"
        msg += "Indeks zdorovya: " + str(manager_result.get("health_score", 0)) + "/100\n"
        msg += "Zona: " + zone_label + "\n\n"
        msg += "METRIKI:\n"
        for m in manager_result.get("metrics", []):
            msg += m.get("name", "") + ": " + str(m.get("score", 0)) + "/100 — " + m.get("comment", "") + "\n"
        msg += "\nVYVODY:\n"
        for i, ins in enumerate(manager_result.get("insights", []), 1):
            msg += str(i) + ". " + str(ins) + "\n"
        msg += "\nPRIORITET: " + str(manager_result.get("priority_action", ""))

        tg_resp = requests.post(
            "https://api.telegram.org/bot" + str(TELEGRAM_BOT_TOKEN) + "/sendMessage",
            json={"chat_id": str(TELEGRAM_CHAT_ID), "text": msg[:4000]}
        )
        print("TG status: " + str(tg_resp.status_code), flush=True)
        print("TG body: " + tg_resp.text[:200], flush=True)
    except Exception as e:
        print("TG error: " + str(e), flush=True)
        traceback.print_exc()

    # Возвращаем оба результата
    final = {
        "manager": manager_result,
        "employee": employee_result
    }

    resp = jsonify(final)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
