from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "service": "PapaDuke Metals API",
        "status": "running",
        "message": "root ok"
    })

@app.route("/api/v1/prices")
def prices():
    return jsonify({
        "status": "ok",
        "gold_usd": 2300,
        "silver_usd": 28,
        "usd_aud": 1.53
    })

@app.route("/ping")
def ping():
    return "pong", 200
