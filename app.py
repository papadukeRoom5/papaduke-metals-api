from flask import Flask, jsonify

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "service": "PapaDuke Metals API",
        "status": "running"
    })

@app.route("/api/v1/prices")
def prices():
    return jsonify({
        "gold_usd": 2300,
        "silver_usd": 28,
        "usd_aud": 1.53
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
