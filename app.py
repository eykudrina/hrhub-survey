from flask import Flask
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route("/")
def index():
    return "OK"

@app.route("/analyze", methods=["POST", "OPTIONS"])
def analyze():
    return {"status": "ok"}
