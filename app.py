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
MODEL = "claude-sonnet-4-5"

# Опорный бенчмарк по похожим командам (можно менять)
BENCHMARK = 63
# Телеграм для кнопки в отчете
CONTACT_TG = "https://t.me/eykudrina"

QUESTIONS = [
    "Как вы оцениваете свой уровень энергии и мотивации на работе прямо сейчас?",
    "Насколько четко вы понимаете свои задачи и приоритеты на ближайшее время?",
    "Как вы оцениваете свою текущую загруженность?",
    "Как вы оцениваете атмосферу и отношения внутри вашей команды?",
    "Получаете ли вы достаточно обратной связи от руководителя?",
    "Насколько вы понимаете, как ваша работа влияет на результат всей компании?",
    "Есть ли у вас все необходимое для качественного выполнения задач?",
    "Как вы оцениваете свои перспективы роста в компании?",
    "Насколько вы удовлетворены своей работой в целом прямо сейчас?",
    "Рассматриваете ли вы сейчас другие предложения о работе?",
    "Что для вас важнее всего в работе прямо сейчас?",
]

# ---------- СЧЕТ БАЛЛОВ ----------

def scale_to_100(v):
    try:
        return max(0, min(100, int(round(float(v))) * 10))
    except (ValueError, TypeError):
        return None

def by_keyword(value, table, default=None):
    """Подбираем балл по ключевому слову в ответе. Устойчиво к ё и тире."""
    v = (value or "")
    for kw, score in table:
        if kw.lower() in v.lower():
            return score
    return default

LOAD_MAP = [("оптимальн", 100), ("недогруж", 70), ("держусь", 45), ("перегруж", 15)]
FEEDBACK_MAP = [("регулярно", 100), ("иногда", 60), ("редко", 30), ("практически нет", 5)]
RESOURCE_MAP = [("все есть", 100), ("всё есть", 100), ("мелкие пробелы", 70),
                ("не хватает инструментов", 35), ("существенно не хватает", 10)]

# риск ухода из вопроса 10: метка -> (балл "остаюсь", уровень риска)
def stay_score(value):
    return by_keyword(value, [("устраивает", 100), ("пассивно", 65),
                              ("несколько интересных", 30), ("активно", 5)], default=60)

def risk_level(value):
    v = (value or "").lower()
    if "активно" in v or "несколько интересных" in v:
        return "high"
    if "пассивно" in v:
        return "medium"
    return "low"

def motivation_type(value):
    return by_keyword(value, [("рост", "Рост"), ("стабиль", "Стабильность"),
                              ("признан", "Признание"), ("смысл", "Смысл")], default="Смысл")

# направления для метрик: (индекс ответа, название, тип)
DIMENSIONS = [
    (0, "Энергия и мотивация", "scale", None),
    (1, "Ясность задач и приоритетов", "scale", None),
    (2, "Адекватность нагрузки", "choice", LOAD_MAP),
    (3, "Атмосфера в команде", "scale", None),
    (4, "Обратная связь от руководителя", "choice", FEEDBACK_MAP),
    (5, "Понимание вклада в результат", "scale", None),
    (6, "Ресурсы для работы", "choice", RESOURCE_MAP),
    (7, "Перспектива роста", "scale", None),
]

def dim_score(answers, idx, dtype, table):
    if idx >= len(answers):
        return None
    if dtype == "scale":
        return scale_to_100(answers[idx])
    return by_keyword(answers[idx], table)

def zone(v):
    if v < 50:
        return "red"
    if v < 66:
        return "amber"
    return "green"

def avg(lst):
    lst = [x for x in lst if x is not None]
    return round(sum(lst) / len(lst)) if lst else 0

def compute(all_answers):
    n = len(all_answers)
    dims = []
    for idx, name, dtype, table in DIMENSIONS:
        scores = [dim_score(a, idx, dtype, table) for a in all_answers]
        scores = [s for s in scores if s is not None]
        a = avg(scores)
        counts = {"red": 0, "amber": 0, "green": 0}
        for s in scores:
            counts[zone(s)] += 1
        dims.append({"name": name, "avg": a, "counts": counts, "scores": scores})

    index = avg([d["avg"] for d in dims])

    # eSAT из вопроса 9 (индекс 8)
    esat_scores = [scale_to_100(a[8]) for a in all_answers if len(a) > 8]
    esat_scores = [s for s in esat_scores if s is not None]
    esat = avg(esat_scores)

    # риск ухода из вопроса 10 (индекс 9)
    risk = {"high": 0, "medium": 0, "low": 0}
    for a in all_answers:
        if len(a) > 9:
            risk[risk_level(a[9])] += 1

    # мотивация из вопроса 11 (индекс 10)
    mot = {"Рост": 0, "Смысл": 0, "Признание": 0, "Стабильность": 0}
    for a in all_answers:
        if len(a) > 10:
            mot[motivation_type(a[10])] += 1

    # мотивация против состояния: рост против перспективы (вопрос 8, индекс 7)
    growth_total, growth_no_persp = 0, 0
    for a in all_answers:
        if len(a) > 10 and motivation_type(a[10]) == "Рост":
            growth_total += 1
            persp = scale_to_100(a[7]) if len(a) > 7 else None
            if persp is not None and persp < 50:
                growth_no_persp += 1

    return {
        "n": n, "index": index, "esat": esat, "dims": dims, "risk": risk,
        "mot": mot, "growth_total": growth_total, "growth_no_persp": growth_no_persp,
    }

# ---------- НАРРАТИВ (сигналы, дорожная карта, лид) ----------

def fallback_narrative(m):
    dims_sorted = sorted(m["dims"], key=lambda d: d["avg"])
    worst = dims_sorted[0]["name"].lower()
    second = dims_sorted[1]["name"].lower()
    best = dims_sorted[-1]["name"].lower()
    return {
        "lead": "Команда держится, но есть направления, которые тянут вниз. Запас прочности пока есть, важно не упустить момент.",
        "signals": [
            {"zone": "red", "title": "Самая слабая зона: " + worst,
             "text": "Это направление набрало меньше всего баллов. Именно отсюда чаще всего начинается тихий отток сильных людей."},
            {"zone": "amber", "title": "Под вопросом: " + second,
             "text": "Второе по слабости направление. Если не заняться им сейчас, оно перейдет в красную зону."},
            {"zone": "green", "title": "Сильная сторона: " + best,
             "text": "Здесь команда чувствует себя увереннее всего. На этом фундаменте стоит строить изменения."},
        ],
        "roadmap": [
            {"title": "Начать с самой красной зоны", "text": "Сфокусироваться на направлении с худшими баллами, оно дает самый быстрый эффект."},
            {"title": "Вернуть регулярную обратную связь", "text": "Короткие встречи один на один, чтобы люди понимали, что у них получается и куда расти."},
            {"title": "Снять перегруз с ключевых людей", "text": "Перераспределить нагрузку, пока самые сильные не выгорели и не ушли."},
            {"title": "Дать траекторию роста", "text": "Показать перспективу тем, кто мотивирован развитием, это дешевле, чем замена на рынке."},
        ],
    }

def generate_narrative(m):
    dims_txt = "\n".join(f"- {d['name']}: {d['avg']} из 100 (красная зона: {d['counts']['red']}, "
                         f"желтая: {d['counts']['amber']}, зеленая: {d['counts']['green']})" for d in m["dims"])
    mot_txt = ", ".join(f"{k} {v}" for k, v in m["mot"].items() if v)
    prompt = f"""Ты эксперт по HR и командной аналитике. По цифрам опроса напиши краткий нарратив для отчета собственнику.

Команда: {m['n']} человек. Общий индекс здоровья: {m['index']} из 100. eSAT (удовлетворенность): {m['esat']} из 100.
В зоне высокого риска ухода: {m['risk']['high']} человек, среднего: {m['risk']['medium']}.
Мотивация: {mot_txt}.
Баллы по направлениям:
{dims_txt}

Верни СТРОГО валидный JSON без пояснений и без markdown:
{{
 "lead": "одно-два предложения, что общий индекс значит для бизнеса",
 "signals": [
   {{"zone":"red","title":"короткий заголовок","text":"1-2 предложения"}},
   {{"zone":"amber","title":"...","text":"..."}},
   {{"zone":"green","title":"...","text":"..."}}
 ],
 "roadmap": [
   {{"title":"короткое действие","text":"1 предложение, что делать"}},
   {{"title":"...","text":"..."}},
   {{"title":"...","text":"..."}},
   {{"title":"...","text":"..."}}
 ]
}}

Правила: опирайся на реальные цифры выше. Самое слабое направление в красный сигнал, самое сильное в зеленый. Не используй букву ё и длинное тире. Будь конкретным и честным."""
    try:
        resp = client.messages.create(model=MODEL, max_tokens=1200,
                                       messages=[{"role": "user", "content": prompt}])
        text = resp.content[0].text.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        data = json.loads(text)
        assert data.get("signals") and data.get("roadmap") and data.get("lead")
        return data
    except Exception as e:
        print(f"narrative fallback: {e}")
        return fallback_narrative(m)

# ---------- РЕНДЕР HTML ----------

MONTHS = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
          "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

def C(v):
    return "var(--red)" if v < 50 else "var(--amber)" if v < 66 else "var(--green)"

def render(m, narrative, company, total):
    n = m["n"]
    idx = m["index"]
    now = datetime.now()
    date_str = f"{MONTHS[now.month]} {now.year}"
    total_str = f"{n} из {total}" if total else f"{n}"

    if idx < 50:
        zlabel, ztitle = "Зона риска", "Команда в красной зоне"
    elif idx < 66:
        zlabel, ztitle = "Внимание", "Команда в желтой зоне"
    else:
        zlabel, ztitle = "Здоровье", "Команда в зеленой зоне"

    bench_txt = (f"У похожих команд индекс обычно около {BENCHMARK}. "
                 f"{'Ниже нормы' if idx < BENCHMARK - 3 else 'Близко к норме' if idx <= BENCHMARK + 3 else 'Выше нормы'}.")

    # карточки
    stats = f"""
      <div class="stat"><div class="big" style="color:var(--red)">{m['risk']['high']} из {n}</div>
        <div class="lab">В зоне высокого риска ухода</div>
        <div class="sub">Активно ищут или рассматривают предложения. Еще {m['risk']['medium']} пассивно следят за рынком.</div></div>
      <div class="stat"><div class="big" style="color:{C(m['esat'])}">{m['esat']}</div>
        <div class="lab">eSAT, удовлетворенность</div>
        <div class="sub">Индекс из 100 по прямому вопросу об удовлетворенности работой.</div></div>
      <div class="stat"><div class="big" style="color:{C(idx)}">{idx}</div>
        <div class="lab">Индекс против нормы</div>
        <div class="sub">{bench_txt}</div></div>"""

    # метрики
    metrics = ""
    for d in m["dims"]:
        metrics += (f'<div class="metric"><div class="metric-head"><span class="name">{d["name"]}</span>'
                    f'<span class="val" style="color:{C(d["avg"])}">{d["avg"]}</span></div>'
                    f'<div class="track"><div class="fill" style="width:{d["avg"]}%;background:{C(d["avg"])}"></div></div></div>')

    # распределение: 4 худших + 1 лучший
    ds = sorted(m["dims"], key=lambda x: x["avg"])
    pick = ds[:4] + [ds[-1]]
    dist = ""
    for d in pick:
        c = d["counts"]
        tot = max(1, c["red"] + c["amber"] + c["green"])
        wr, wa, wg = c["red"] / tot * 100, c["amber"] / tot * 100, c["green"] / tot * 100
        seg = ""
        if c["red"]:
            seg += f'<span style="width:{wr:.1f}%;background:var(--red)">{c["red"]}</span>'
        if c["amber"]:
            seg += f'<span style="width:{wa:.1f}%;background:var(--amber)">{c["amber"]}</span>'
        if c["green"]:
            seg += f'<span style="width:{wg:.1f}%;background:var(--green)">{c["green"]}</span>'
        dist += f'<div class="dist"><div class="name">{d["name"]}</div><div class="drow">{seg}</div></div>'

    # мотивация
    mot_colors = {"Рост": "var(--orange)", "Смысл": "#C9520A", "Признание": "var(--amber)", "Стабильность": "#9A6B3A"}
    mot_items = [(k, v) for k, v in m["mot"].items() if v]
    mot_items.sort(key=lambda x: -x[1])
    motbar, motlegend = "", ""
    for k, v in mot_items:
        pct = round(v / max(1, n) * 100)
        motbar += f'<span style="width:{pct}%;background:{mot_colors[k]}">{k} {pct}%</span>'
        motlegend += (f'<div class="mot-item"><span class="sw" style="background:{mot_colors[k]}"></span>'
                      f'<b>{pct}%</b>&nbsp;<span class="lab">{k}</span></div>')

    # callout мотивация против состояния
    if m["growth_total"] and m["growth_no_persp"]:
        callout = (f'<div class="callout"><h4>Мотивация против состояния</h4>'
                   f'<p>Из {m["growth_total"]} человек, мотивированных ростом, {m["growth_no_persp"]} не видят для себя '
                   f'перспективы в компании. Это главная зона риска: вы рискуете потерять именно тех, кто хотел развиваться.</p></div>')
    elif m["growth_total"]:
        callout = (f'<div class="callout"><h4>Мотивация против состояния</h4>'
                   f'<p>{m["growth_total"]} человек мотивированы ростом, и они видят для себя перспективу. '
                   f'Это ваш ресурс: дайте им развитие и понятные критерии, и они потянут команду вперед.</p></div>')
    else:
        callout = ""

    signals = ""
    zc = {"red": "var(--red)", "amber": "var(--amber)", "green": "var(--green)"}
    for s in narrative["signals"]:
        signals += (f'<div class="signal"><span class="tag" style="background:{zc.get(s["zone"],"var(--amber)")}"></span>'
                    f'<div><h4>{s["title"]}</h4><p>{s["text"]}</p></div></div>')

    roadmap = ""
    for i, r in enumerate(narrative["roadmap"], 1):
        roadmap += (f'<div class="step"><div class="no">{i}</div><div><h4>{r["title"]}</h4>'
                    f'<p>{r["text"]}</p></div></div>')

    html = TEMPLATE
    repl = {
        "COMPANY": company, "DATE": date_str, "TOTAL": total_str, "N": str(n),
        "INDEX": str(idx), "INDEX_COLOR": C(idx), "ZLABEL": zlabel, "ZTITLE": ztitle,
        "LEAD": narrative["lead"], "STATS": stats, "METRICS": metrics, "DIST": dist,
        "MOTBAR": motbar, "MOTLEGEND": motlegend, "CALLOUT": callout,
        "SIGNALS": signals, "ROADMAP": roadmap, "TG": CONTACT_TG,
    }
    for k, v in repl.items():
        html = html.replace("{{" + k + "}}", str(v))
    return html

# ---------- TELEGRAM ----------

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})
    except Exception as e:
        print(f"TG error: {e}")

def generate_employee_feedback(answers):
    answers_text = "".join(f"{i+1}. {q}\nОтвет: {a}\n\n" for i, (q, a) in enumerate(zip(QUESTIONS, answers)))
    prompt = f"""Ты опытный HR-консультант и бизнес-психолог. Сотрудник прошел анонимный опрос.

Ответы:
{answers_text}

Напиши персональный фидбек ДЛЯ СОТРУДНИКА. Тон теплый, живой, не корпоративный. Без цифр и слова риск.
Структура: фраза благодарности; 2-3 предложения отражения состояния; один конкретный совет про действие, а не про отдых; финальная фраза, что ответы помогут команде, анонимность гарантирована. Не более 120 слов, без заголовков и списков. Не используй букву ё."""
    resp = client.messages.create(model=MODEL, max_tokens=500, messages=[{"role": "user", "content": prompt}])
    return resp.content[0].text

# ---------- DB ----------

def init_db():
    d = os.path.dirname(DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS responses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, company_code TEXT NOT NULL,
        answers TEXT NOT NULL, created_at TEXT NOT NULL)''')
    conn.commit()
    conn.close()

init_db()

# ---------- ROUTES ----------

@app.route("/submit", methods=["POST"])
def submit():
    try:
        data = request.get_json()
        answers = data.get("answers", [])
        company_code = data.get("company_code", "default").strip().lower()
        if len(answers) < 10:
            return jsonify({"error": "Недостаточно ответов"}), 400

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO responses (company_code, answers, created_at) VALUES (?, ?, ?)",
                  (company_code, json.dumps(answers, ensure_ascii=False), datetime.now().isoformat()))
        conn.commit()
        c.execute("SELECT COUNT(*) FROM responses WHERE company_code=?", (company_code,))
        count = c.fetchone()[0]
        conn.close()

        send_telegram(f"📋 <b>Новый ответ [{company_code}]</b>\nВсего по компании: {count}\n"
                      f"Отчет: https://hrhub-survey-production-e84f.up.railway.app/report/{company_code}")

        feedback = generate_employee_feedback(answers)
        return jsonify({"status": "ok", "feedback": feedback, "count": count})
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/report/<company_code>", methods=["GET"])
def team_report(company_code):
    company_code = company_code.strip().lower()
    total = request.args.get("total", type=int)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT answers FROM responses WHERE company_code=? ORDER BY created_at", (company_code,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        page = NO_DATA.replace("{{COMPANY}}", company_code)
        return app.response_class(page, content_type="text/html; charset=utf-8", status=200)

    all_answers = [json.loads(r[0]) for r in rows]
    m = compute(all_answers)
    narrative = generate_narrative(m)
    html = render(m, narrative, company_code.upper(), total)
    return app.response_class(html, content_type="text/html; charset=utf-8")

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

# ---------- ШАБЛОН ----------

NO_DATA = """<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Отчет HR HUB</title>
<style>body{font-family:Arial,sans-serif;background:#EFEAE3;color:#1A1814;display:flex;min-height:100vh;
align-items:center;justify-content:center;text-align:center;padding:24px}
.box{background:#fff;border-radius:16px;padding:40px;max-width:420px}
h1{color:#E8600A;font-size:20px;margin-bottom:12px}p{color:#6E675E;line-height:1.5}</style></head>
<body><div class="box"><h1>HR HUB</h1><p>По компании «{{COMPANY}}» пока нет ответов. Как только сотрудники
пройдут опрос, отчет соберется автоматически.</p></div></body></html>"""

TEMPLATE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Заключение по здоровью команды · HR HUB</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root{--ink:#1A1814;--muted:#6E675E;--line:#ECE7E0;--paper:#FFFFFF;--panel:#FBF8F4;
    --orange:#E8600A;--red:#E0454A;--amber:#F0A92B;--green:#3BA46B;
    --shadow:0 1px 2px rgba(26,24,20,.04),0 10px 30px rgba(26,24,20,.06);}
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Manrope',-apple-system,'Segoe UI',Arial,sans-serif;background:#EFEAE3;color:var(--ink);line-height:1.55;padding:32px 16px 64px}
  .sheet{max-width:840px;margin:0 auto;background:var(--paper);border-radius:20px;overflow:hidden;box-shadow:var(--shadow)}
  @media(max-width:640px){body{padding:16px 8px 40px}}
  .top{background:var(--ink);color:#fff;padding:38px 56px 34px;position:relative;overflow:hidden}
  @media(max-width:640px){.top{padding:30px 24px}}
  .top:after{content:"";position:absolute;right:-60px;top:-60px;width:220px;height:220px;border-radius:50%;background:radial-gradient(circle,rgba(232,96,10,.55),transparent 70%)}
  .brand{display:flex;align-items:center;gap:10px;font-weight:800;letter-spacing:.04em;font-size:15px;position:relative;z-index:1}
  .brand .dot{width:11px;height:11px;border-radius:50%;background:var(--orange)}
  .eyebrow{color:var(--orange);font-weight:700;font-size:12px;letter-spacing:.16em;text-transform:uppercase;margin-top:30px;position:relative;z-index:1}
  .top h1{font-size:30px;line-height:1.15;font-weight:800;margin-top:8px;position:relative;z-index:1}
  @media(max-width:640px){.top h1{font-size:24px}}
  .meta{display:flex;flex-wrap:wrap;gap:8px 28px;margin-top:22px;position:relative;z-index:1}
  .meta div{font-size:13px;color:#C9C2B8}
  .meta b{display:block;color:#fff;font-size:15px;font-weight:700}
  .sec{padding:44px 56px;border-top:1px solid var(--line)}
  @media(max-width:640px){.sec{padding:34px 24px}}
  .sec-eyebrow{color:var(--orange);font-weight:700;font-size:12px;letter-spacing:.14em;text-transform:uppercase;margin-bottom:6px}
  .sec h2{font-size:22px;font-weight:800;letter-spacing:-.01em}
  .sec p.lead{color:var(--muted);margin-top:10px;font-size:15px;max-width:60ch}
  .light-wrap{margin-top:26px}
  .index-row{display:flex;align-items:flex-end;gap:18px;flex-wrap:wrap}
  .index-num{font-size:64px;font-weight:800;line-height:.9;letter-spacing:-.03em}
  .index-of{font-size:18px;color:var(--muted);font-weight:600;padding-bottom:8px}
  .zone-pill{margin-left:auto;align-self:center;font-weight:700;font-size:13px;padding:8px 16px;border-radius:999px;background:rgba(240,169,43,.15);color:#B97A0A}
  @media(max-width:640px){.zone-pill{margin-left:0}}
  .bar{position:relative;height:18px;border-radius:999px;margin-top:26px;background:linear-gradient(90deg,var(--red) 0%,var(--red) 33%,var(--amber) 38%,var(--amber) 66%,var(--green) 72%,var(--green) 100%)}
  .marker{position:absolute;top:-9px;width:4px;height:36px;background:var(--ink);border-radius:3px;transform:translateX(-50%);box-shadow:0 0 0 4px #fff}
  .marker:before{content:"";position:absolute;top:-10px;left:50%;transform:translateX(-50%);width:11px;height:11px;border-radius:50%;background:var(--ink)}
  .scale{display:flex;justify-content:space-between;margin-top:14px;font-size:12px;font-weight:600;color:var(--muted)}
  .scale span:nth-child(1){color:var(--red)}.scale span:nth-child(2){color:#B97A0A}.scale span:nth-child(3){color:var(--green)}
  .stats{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:26px}
  @media(max-width:640px){.stats{grid-template-columns:1fr}}
  .stat{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px 22px}
  .stat .big{font-size:34px;font-weight:800;line-height:1;letter-spacing:-.02em}
  .stat .lab{font-weight:700;font-size:14px;margin-top:10px}
  .stat .sub{color:var(--muted);font-size:13px;margin-top:6px}
  .metric{margin-top:18px}.metric:first-of-type{margin-top:24px}
  .metric-head{display:flex;justify-content:space-between;align-items:baseline;font-size:14px}
  .metric-head .name{font-weight:600}.metric-head .val{font-weight:800;font-variant-numeric:tabular-nums}
  .track{height:8px;border-radius:999px;background:#F0ECE6;margin-top:7px;overflow:hidden}
  .fill{height:100%;border-radius:999px}
  .dist{margin-top:18px}.dist:first-of-type{margin-top:26px}
  .dist .name{font-size:14px;font-weight:600;margin-bottom:7px}
  .dist .drow{display:flex;height:30px;border-radius:8px;overflow:hidden}
  .dist .drow span{display:flex;align-items:center;justify-content:center;color:#fff;font-size:12.5px;font-weight:700;min-width:0}
  .dist-key{display:flex;gap:20px;margin-top:22px;font-size:12.5px;color:var(--muted);flex-wrap:wrap}
  .dist-key i{font-style:normal;display:inline-flex;align-items:center;gap:7px}
  .dist-key i:before{content:"";width:10px;height:10px;border-radius:3px}
  .dist-key .r:before{background:var(--red)}.dist-key .a:before{background:var(--amber)}.dist-key .g:before{background:var(--green)}
  .mot-bar{display:flex;height:46px;border-radius:12px;overflow:hidden;margin-top:24px}
  .mot-bar span{display:inline-flex;align-items:center;justify-content:center;color:#fff;font-size:13px;font-weight:700}
  .mot-legend{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-top:22px}
  .mot-item{display:flex;align-items:center;gap:10px;font-size:14px}
  .mot-item .sw{width:12px;height:12px;border-radius:3px;flex:none}.mot-item b{font-weight:800}.mot-item .lab{color:var(--muted)}
  .callout{margin-top:26px;background:var(--panel);border-left:4px solid var(--orange);border-radius:0 12px 12px 0;padding:22px 24px}
  .callout h4{font-size:15px;font-weight:800;color:var(--orange);margin-bottom:6px}.callout p{font-size:14.5px}
  .signal{display:flex;gap:16px;margin-top:20px}.signal:first-of-type{margin-top:26px}
  .signal .tag{flex:none;width:10px;height:10px;border-radius:50%;margin-top:8px}
  .signal h4{font-size:15px;font-weight:700;margin-bottom:3px}.signal p{color:var(--muted);font-size:14px}
  .step{display:flex;gap:18px;padding:20px 0;border-bottom:1px solid var(--line)}.step:last-child{border-bottom:0}
  .step .no{flex:none;width:34px;height:34px;border-radius:10px;background:var(--ink);color:#fff;font-weight:800;display:flex;align-items:center;justify-content:center;font-size:15px}
  .step h4{font-size:16px;font-weight:700}.step p{color:var(--muted);font-size:14px;margin-top:3px}
  .offer{background:var(--ink);color:#fff;padding:46px 56px;position:relative;overflow:hidden}
  @media(max-width:640px){.offer{padding:36px 24px}}
  .offer:before{content:"";position:absolute;left:-80px;bottom:-80px;width:260px;height:260px;border-radius:50%;background:radial-gradient(circle,rgba(232,96,10,.4),transparent 70%)}
  .offer .sec-eyebrow{position:relative;z-index:1}
  .offer h2{font-size:24px;font-weight:800;position:relative;z-index:1}
  .offer .intro{color:#D7D0C6;margin-top:12px;font-size:15.5px;max-width:62ch;position:relative;z-index:1}
  .offer .intro b{color:#fff;font-weight:700}
  .incl{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:rgba(255,255,255,.08);border-radius:14px;overflow:hidden;margin-top:28px;position:relative;z-index:1}
  @media(max-width:640px){.incl{grid-template-columns:1fr}}
  .incl div{background:#211E19;padding:20px 22px}
  .incl h4{font-size:15px;font-weight:700;margin-bottom:5px;color:#fff}.incl h4 i{color:var(--orange);font-style:normal;margin-right:8px}
  .incl p{color:#B7B0A6;font-size:13.5px}
  .cta{margin-top:30px;position:relative;z-index:1}.cta p{font-size:16px;font-weight:600;margin-bottom:16px}
  .cta a{display:inline-block;background:var(--orange);color:#fff;text-decoration:none;font-weight:800;font-size:15px;padding:15px 30px;border-radius:12px}
  .foot{padding:28px 56px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;font-size:13px;color:var(--muted)}
  @media(max-width:640px){.foot{padding:24px}}.foot b{color:var(--ink)}
  .printbtn{position:fixed;top:18px;right:18px;z-index:50;background:var(--ink);color:#fff;border:0;font-family:inherit;font-weight:700;font-size:14px;padding:12px 20px;border-radius:11px;cursor:pointer;box-shadow:var(--shadow)}
  @media print{body{background:#fff;padding:0}.sheet{box-shadow:none;border-radius:0;max-width:none}.printbtn{display:none}
    .top,.offer{-webkit-print-color-adjust:exact;print-color-adjust:exact}.sec,.offer{page-break-inside:avoid}}
</style></head>
<body>
<button class="printbtn" onclick="window.print()">Сохранить PDF</button>
<div class="sheet">
  <div class="top">
    <div class="brand"><span class="dot"></span>HR HUB</div>
    <div class="eyebrow">Заключение по здоровью команды</div>
    <h1>{{COMPANY}}</h1>
    <div class="meta">
      <div>Дата<b>{{DATE}}</b></div>
      <div>Прошли опрос<b>{{TOTAL}}</b></div>
      <div>Формат<b>Анонимно</b></div>
    </div>
  </div>

  <div class="sec">
    <div class="sec-eyebrow">Общий индекс</div>
    <h2>{{ZTITLE}}</h2>
    <p class="lead">{{LEAD}}</p>
    <div class="light-wrap">
      <div class="index-row">
        <div class="index-num" style="color:{{INDEX_COLOR}}">{{INDEX}}</div>
        <div class="index-of">/ 100</div>
        <div class="zone-pill">{{ZLABEL}}</div>
      </div>
      <div class="bar"><div class="marker" style="left:{{INDEX}}%"></div></div>
      <div class="scale"><span>Риск</span><span>Внимание</span><span>Здоровье</span></div>
    </div>
  </div>

  <div class="sec">
    <div class="sec-eyebrow">Ключевые цифры</div>
    <h2>На что собственнику смотреть первым</h2>
    <div class="stats">{{STATS}}</div>
  </div>

  <div class="sec">
    <div class="sec-eyebrow">Баллы по направлениям</div>
    <h2>Из чего складывается индекс</h2>
    {{METRICS}}
    <p class="lead" style="font-size:13px;margin-top:24px">Индекс считается по восьми направлениям опроса, каждое приведено к шкале от 0 до 100. Ответы обрабатываются обезличенно: в отчете видна только общая картина по команде, вычислить ответы конкретного человека нельзя.</p>
  </div>

  <div class="sec">
    <div class="sec-eyebrow">Распределение по людям</div>
    <h2>Среднее прячет крайности</h2>
    <p class="lead">Одна цифра усредняет всех. Здесь видно, сколько человек реально сидит в каждой зоне по самым слабым направлениям и по самому сильному.</p>
    {{DIST}}
    <div class="dist-key"><i class="r">Красная зона</i><i class="a">Внимание</i><i class="g">Здоровье</i></div>
  </div>

  <div class="sec">
    <div class="sec-eyebrow">Что движет командой</div>
    <h2>Карта мотивации</h2>
    <div class="mot-bar">{{MOTBAR}}</div>
    <div class="mot-legend">{{MOTLEGEND}}</div>
    {{CALLOUT}}
  </div>

  <div class="sec">
    <div class="sec-eyebrow">Главные сигналы</div>
    <h2>На что смотреть в первую очередь</h2>
    {{SIGNALS}}
  </div>

  <div class="sec">
    <div class="sec-eyebrow">Дорожная карта</div>
    <h2>Что делать дальше, по шагам</h2>
    {{ROADMAP}}
  </div>

  <div class="offer">
    <div class="sec-eyebrow">Что дальше</div>
    <h2>Могу довести команду до этого результата</h2>
    <p class="intro">Этот отчет показывает, где болит. Но сам по себе он ничего не меняет. Если вы хотите не просто увидеть картину, а собрать команду, которая реально тянет бизнес к цели, <b>я беру это на себя и веду за руку до результата.</b></p>
    <div class="incl">
      <div><h4><i>01</i>Оценка компетенций каждого</h4><p>Провожу индивидуальные встречи с каждым сотрудником. По итогам видно его реальные компетенции, сильные стороны и риски, а не то, что написано в должности. И самое важное, на своем ли он месте.</p></div>
      <div><h4><i>02</i>Постановка цели команды</h4><p>Помогаю сформулировать, к чему идем и что считается результатом, чтобы все тянули в одну сторону.</p></div>
      <div><h4><i>03</i>Поэтапный план по людям</h4><p>По каждому человеку решение: кого дорастить, кого переместить, кого нанять, а с кем честнее расстаться.</p></div>
      <div><h4><i>04</i>Сопровождение до результата</h4><p>Веду внедрение вместе с вами, пока команда не выйдет на цель.</p></div>
    </div>
    <div class="cta">
      <p>Если откликается, давайте разберем вашу ситуацию лично.</p>
      <a href="{{TG}}" target="_blank" rel="noopener">Написать Евгении</a>
    </div>
  </div>

  <div class="foot">
    <div><b>Евгения Кудрина</b> · HR HUB</div>
    <div>hrhub.team · @eykudrina</div>
  </div>
</div>
</body></html>"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
