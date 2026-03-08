from flask import Flask, jsonify
import requests
import os
from datetime import datetime, timezone

app = Flask(__name__)

GOLD_API_BASE = "https://api.gold-api.com/price"
THAI_GOLD_API_URL = "https://api.chnwt.dev/thai-gold-api/latest"
OZ_TO_GRAMS = 31.1034768

FX_API_KEY = os.environ.get("FX_API_KEY")
FX_API_URL = f"https://v6.exchangerate-api.com/v6/{FX_API_KEY}/latest/USD"


def round2(value):
    return round(float(value), 2)


def get_json(url):
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.json()


def parse_number(value):
    if value is None:
        return 0.0
    return float(str(value).replace(",", "").strip())


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
        if not FX_API_KEY:
            return jsonify({
                "status": "error",
                "message": "FX_API_KEY is not set"
            }), 500

        gold_url = f"{GOLD_API_BASE}/XAU"
        silver_url = f"{GOLD_API_BASE}/XAG"

        print("DEBUG gold_url:", gold_url)
        print("DEBUG silver_url:", silver_url)
        print("DEBUG fx_url:", FX_API_URL)
        print("DEBUG thai_url:", THAI_GOLD_API_URL)

        gold_data = get_json(gold_url)
        print("DEBUG gold_data:", gold_data)

        silver_data = get_json(silver_url)
        print("DEBUG silver_data:", silver_data)

        fx_data = get_json(FX_API_URL)
        print("DEBUG fx_data:", fx_data)

        thai_data = get_json(THAI_GOLD_API_URL)
        print("DEBUG thai_data:", thai_data)

        gold_usd = parse_number(gold_data["price"])
        silver_usd = parse_number(silver_data["price"])

        fx_rates = fx_data.get("conversion_rates", {})
        usd_aud = parse_number(fx_rates.get("AUD", 0))
        usd_thb = parse_number(fx_rates.get("THB", 0))
        usd_cny = parse_number(fx_rates.get("CNY", 0))

        if not usd_aud or not usd_thb or not usd_cny:
            return jsonify({
                "status": "error",
                "message": "FX data missing AUD/THB/CNY",
                "fx_data": fx_data
            }), 500

        gold_aud = gold_usd * usd_aud
        silver_aud = silver_usd * usd_aud

        gold_cny_g = (gold_usd * usd_cny) / OZ_TO_GRAMS
        silver_cny_g = (silver_usd * usd_cny) / OZ_TO_GRAMS

        thai_gold_bar_buy_raw = (
            thai_data.get("response", {})
            .get("price", {})
            .get("gold_bar", {})
            .get("buy")
        ) or (
            thai_data.get("price", {})
            .get("gold_bar", {})
            .get("buy")
        ) or 0

        thai_gold_bar_sell_raw = (
            thai_data.get("response", {})
            .get("price", {})
            .get("gold_bar", {})
            .get("sell")
        ) or (
            thai_data.get("price", {})
            .get("gold_bar", {})
            .get("sell")
        ) or 0

        thai_gold_bar_buy = parse_number(thai_gold_bar_buy_raw)
        thai_gold_bar_sell = parse_number(thai_gold_bar_sell_raw)

        payload = {
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
        }

        return jsonify(payload)

    except Exception as e:
        print("DEBUG exception:", repr(e))
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
