from flask import Flask, jsonify
import requests
from datetime import datetime, timezone

app = Flask(__name__)

GOLD_API_BASE = "https://api.gold-api.com/price"
FX_API_URL = "https://api.exchangerate.host/latest?base=USD&symbols=AUD,THB,CNY"
THAI_GOLD_API_URL = "https://api.chnwt.dev/thai-gold-api/latest"

def round2(value):
    return round(float(value), 2)

def get_json(url):
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()

@app.route("/")
def home():
    return jsonify({
        "service": "PapaDuke Metals API",
        "status": "running",
        "message": "root ok"
    })

@app.route("/ping")
def ping():
    return "pong", 200

@app.route("/api/v1/prices")
def prices():
    try:
        gold_data = get_json(f"{GOLD_API_BASE}/XAU")
        silver_data = get_json(f"{GOLD_API_BASE}/XAG")
        fx_data = get_json(FX_API_URL)
        thai_data = get_json(THAI_GOLD_API_URL)

        gold_usd = float(gold_data["price"])
        silver_usd = float(silver_data["price"])

        usd_aud = float(fx_data["rates"]["AUD"])
        usd_thb = float(fx_data["rates"]["THB"])
        usd_cny = float(fx_data["rates"]["CNY"])

        gold_aud = gold_usd * usd_aud
        silver_aud = silver_usd * usd_aud

        gold_cny_g = (gold_usd * usd_cny) / 31.1034768
        silver_cny_g = (silver_usd * usd_cny) / 31.1034768

        thai_gold_bar_buy = (
            thai_data.get("response", {})
            .get("price", {})
            .get("gold_bar", {})
            .get("buy")
        ) or (
            thai_data.get("price", {})
            .get("gold_bar", {})
            .get("buy")
        ) or 0

        thai_gold_bar_sell = (
            thai_data.get("response", {})
            .get("price", {})
            .get("gold_bar", {})
            .get("sell")
        ) or (
            thai_data.get("price", {})
            .get("gold_bar", {})
            .get("sell")
        ) or 0

        thai_gold_bar_buy = float(thai_gold_bar_buy)
        thai_gold_bar_sell = float(thai_gold_bar_sell)

        return jsonify({
            "status": "ok",
            "updated_at": datetime.now(timezone.utc).isoformat(),

            "usa": {
                "gold_usd_oz": round2(gold_usd),
                "silver_usd_oz": round2(silver_usd)
            },

            "australia": {
                "gold_aud_oz": round2(gold_aud),
                "silver_aud_oz": round2(silver_aud),
                "gold_premium_aud": 0,
                "silver_premium_aud": 0
            },

            "thailand": {
                "gold_bar_buy_thb": round2(thai_gold_bar_buy),
                "gold_bar_sell_thb": round2(thai_gold_bar_sell),
                "spread_thb": round2(abs(thai_gold_bar_sell - thai_gold_bar_buy))
            },

            "china": {
                "gold_cny_g": round2(gold_cny_g),
                "silver_cny_g": round2(silver_cny_g),
                "gold_premium_cny_g": 0,
                "silver_premium_cny_g": 0
            },

            "fx": {
                "usd_aud": round2(usd_aud),
                "usd_thb": round2(usd_thb),
                "usd_cny": round2(usd_cny)
            },

            "indicators": {
                "gold_silver_ratio": round2(gold_usd / silver_usd) if silver_usd else None
            }
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
