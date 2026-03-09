from flask import Flask, jsonify
import requests
import os
import threading
import time
from datetime import datetime, timezone

app = Flask(__name__)

# =========================
# CONFIG
# =========================
GOLD_API_BASE = "https://api.gold-api.com/price"
THAI_GOLD_API_URL = "https://api.chnwt.dev/thai-gold-api/latest"

ABC_GOLD_PRODUCTS_URL = "https://new-api.abcbullion.com.au/api/products?parentCategory=gold"
ABC_SILVER_PRODUCTS_URL = "https://new-api.abcbullion.com.au/api/products?parentCategory=silver"

OZ_TO_GRAMS = 31.1034768

FX_API_KEY = "d97e47cfb06d344fb3fffdc1"
FX_API_URL = f"https://v6.exchangerate-api.com/v6/{FX_API_KEY}/latest/USD"

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (PapaDukeMetalsAPI)"
}

JSON_TIMEOUT = 12
CACHE_TTL_SECONDS = 55

session = requests.Session()
session.headers.update(HTTP_HEADERS)

# =========================
# CACHE
# =========================
_cache_lock = threading.Lock()
_cache_payload = None
_cache_time = 0.0


def round2(v):
    return round(float(v), 2)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_number(value):
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return float(str(value).replace(",", "").replace("$", "").strip())


def get_json(url):
    r = session.get(url, timeout=JSON_TIMEOUT)
    r.raise_for_status()
    return r.json()


# ======================================================
# ROBUST SHANGHAI BENCHMARK PARSER
# ======================================================
def fetch_shanghai_local_prices():

    result = {
        "gold_cny_g": 0.0,
        "silver_cny_g": 0.0,
        "source": "SGE Benchmark"
    }

    # -----------------------
    # GOLD
    # -----------------------
    try:

        url = "https://en.sge.com.cn/data_BenchmarkPrice_Daily"

        r = session.get(url, timeout=JSON_TIMEOUT)
        r.raise_for_status()

        lines = r.text.splitlines()

        for line in lines:

            parts = line.strip().split()

            if len(parts) >= 3 and parts[1] == "SHAU":

                price = parse_number(parts[2])

                if price > 0:
                    result["gold_cny_g"] = price
                    break

    except Exception as e:
        print("SGE GOLD ERROR:", e)

    # -----------------------
    # SILVER
    # -----------------------
    try:

        url = "https://en.sge.com.cn/data/data_silver_daily"

        r = session.get(url, timeout=JSON_TIMEOUT)
        r.raise_for_status()

        lines = r.text.splitlines()

        for line in lines:

            parts = line.strip().split()

            if len(parts) >= 3 and parts[1] == "SHAG":

                price_kg = parse_number(parts[2])

                if price_kg > 0:
                    result["silver_cny_g"] = price_kg / 1000.0
                    break

    except Exception as e:
        print("SGE SILVER ERROR:", e)

    return result


# ======================================================
# ABC BULLION
# ======================================================
def find_product_by_name(products, name):
    for p in products:
        if p.get("itemName") == name:
            return p
    return None


def fetch_abc_reference_prices():

    gold_products = get_json(ABC_GOLD_PRODUCTS_URL)
    silver_products = get_json(ABC_SILVER_PRODUCTS_URL)

    gold_item = find_product_by_name(gold_products, "1oz ABC Gold Cast Bar 9999")
    silver_item = find_product_by_name(silver_products, "10oz ABC Silver Cast Bar 9995")

    gold_weight = parse_number(gold_item["itemShopPriceWeightOunces"])
    silver_weight = parse_number(silver_item["itemShopPriceWeightOunces"])

    gold_buy = parse_number(gold_item["purchasePrice"])
    gold_sell = parse_number(gold_item["sellPrice"])

    silver_buy = parse_number(silver_item["purchasePrice"])
    silver_sell = parse_number(silver_item["sellPrice"])

    return {

        "gold_buy_aud_oz": gold_buy / gold_weight,
        "gold_sell_aud_oz": gold_sell / gold_weight,

        "silver_buy_aud_oz": silver_buy / silver_weight,
        "silver_sell_aud_oz": silver_sell / silver_weight,

        "source": "ABC Bullion"
    }


# ======================================================
# BUILD PAYLOAD
# ======================================================
def build_payload():

    gold_data = get_json(f"{GOLD_API_BASE}/XAU")
    silver_data = get_json(f"{GOLD_API_BASE}/XAG")

    fx_data = get_json(FX_API_URL)
    thai_data = get_json(THAI_GOLD_API_URL)

    abc = fetch_abc_reference_prices()
    shanghai = fetch_shanghai_local_prices()

    gold_usd = parse_number(gold_data["price"])
    silver_usd = parse_number(silver_data["price"])

    usd_aud = parse_number(fx_data["conversion_rates"]["AUD"])
    usd_thb = parse_number(fx_data["conversion_rates"]["THB"])
    usd_cny = parse_number(fx_data["conversion_rates"]["CNY"])

    # -----------------------
    # WORLD REF
    # -----------------------

    gold_ref_cny_g = (gold_usd * usd_cny) / OZ_TO_GRAMS
    silver_ref_cny_g = (silver_usd * usd_cny) / OZ_TO_GRAMS

    gold_cny_g = shanghai["gold_cny_g"]
    silver_cny_g = shanghai["silver_cny_g"]

    gold_spread = gold_cny_g - gold_ref_cny_g
    silver_spread = silver_cny_g - silver_ref_cny_g

    payload = {

        "status": "ok",
        "updated_at": now_iso(),

        "usa": {
            "gold_usd_oz": round2(gold_usd),
            "silver_usd_oz": round2(silver_usd)
        },

        "china": {

            "gold_cny_g": round2(gold_cny_g),
            "silver_cny_g": round2(silver_cny_g),

            "gold_ref_cny_g": round2(gold_ref_cny_g),
            "silver_ref_cny_g": round2(silver_ref_cny_g),

            "gold_spread_cny_g": round2(gold_spread),
            "silver_spread_cny_g": round2(silver_spread),

            "gold_spread_pct": round2((gold_spread / gold_ref_cny_g) * 100),
            "silver_spread_pct": round2((silver_spread / silver_ref_cny_g) * 100),

            "source": shanghai["source"]
        },

        "fx": {
            "usd_aud": usd_aud,
            "usd_thb": usd_thb,
            "usd_cny": usd_cny
        },

        "indicators": {
            "gold_silver_ratio": round2(gold_usd / silver_usd)
        }
    }

    return payload


# ======================================================
# ROUTES
# ======================================================
@app.route("/")
def home():
    return jsonify({"status": "ok"})


@app.route("/api/v1/prices")
def prices():

    global _cache_payload, _cache_time

    if _cache_payload and (time.time() - _cache_time < CACHE_TTL_SECONDS):
        return jsonify(_cache_payload)

    payload = build_payload()

    _cache_payload = payload
    _cache_time = time.time()

    return jsonify(payload)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
