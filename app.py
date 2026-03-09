from flask import Flask, jsonify
import requests
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

SGE_GOLD_URL = "https://en.sge.com.cn/data_BenchmarkPrice_Daily"
SGE_SILVER_URL = "https://en.sge.com.cn/data/data_silver_daily"

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


def round4(v):
    return round(float(v), 4)


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


def safe_pct(change, base):
    if not base:
        return 0.0
    return (change / base) * 100.0


# ======================================================
# SGE BENCHMARK PARSER
# ======================================================
def _extract_first_contract_row(text, contract_code):
    """
    Expected raw rows like:
    20260309 SHAU 1138.44 1137.97
    20260309 SHAG 20497 21410

    Returns (trade_date, am, pm) or (None, 0.0, 0.0)
    """
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        parts = line.split()
        if len(parts) < 4:
            continue

        if parts[1] != contract_code:
            continue

        trade_date = parts[0]
        am = parse_number(parts[2])
        pm = parse_number(parts[3])

        if am > 0 and pm > 0:
            return trade_date, am, pm

    return None, 0.0, 0.0


def fetch_shanghai_benchmark_prices():
    result = {
        "trade_date": "",
        "gold_am_cny_g": 0.0,
        "gold_pm_cny_g": 0.0,
        "gold_spread_cny_g": 0.0,
        "gold_move_pct": 0.0,
        "silver_am_cny_g": 0.0,
        "silver_pm_cny_g": 0.0,
        "silver_spread_cny_g": 0.0,
        "silver_move_pct": 0.0,
        "source": "SGE Benchmark"
    }

    # -----------------------
    # GOLD (already CNY/g)
    # -----------------------
    try:
        r = session.get(SGE_GOLD_URL, timeout=JSON_TIMEOUT)
        r.raise_for_status()

        trade_date, am, pm = _extract_first_contract_row(r.text, "SHAU")
        if am > 0 and pm > 0:
            spread = pm - am
            result["trade_date"] = trade_date or ""
            result["gold_am_cny_g"] = am
            result["gold_pm_cny_g"] = pm
            result["gold_spread_cny_g"] = spread
            result["gold_move_pct"] = safe_pct(spread, am)

    except Exception as e:
        print("SGE GOLD ERROR:", e)

    # -----------------------
    # SILVER (SGE gives CNY/kg -> convert to CNY/g)
    # -----------------------
    try:
        r = session.get(SGE_SILVER_URL, timeout=JSON_TIMEOUT)
        r.raise_for_status()

        trade_date, am_kg, pm_kg = _extract_first_contract_row(r.text, "SHAG")
        if am_kg > 0 and pm_kg > 0:
            am_g = am_kg / 1000.0
            pm_g = pm_kg / 1000.0
            spread_g = pm_g - am_g

            if not result["trade_date"]:
                result["trade_date"] = trade_date or ""

            result["silver_am_cny_g"] = am_g
            result["silver_pm_cny_g"] = pm_g
            result["silver_spread_cny_g"] = spread_g
            result["silver_move_pct"] = safe_pct(spread_g, am_g)

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
    shanghai = fetch_shanghai_benchmark_prices()

    gold_usd = parse_number(gold_data["price"])
    silver_usd = parse_number(silver_data["price"])

    usd_aud = parse_number(fx_data["conversion_rates"]["AUD"])
    usd_thb = parse_number(fx_data["conversion_rates"]["THB"])
    usd_cny = parse_number(fx_data["conversion_rates"]["CNY"])

    # Australia spot reference
    gold_spot_aud_oz = gold_usd * usd_aud
    silver_spot_aud_oz = silver_usd * usd_aud

    gold_premium_aud = abc["gold_sell_aud_oz"] - gold_spot_aud_oz
    silver_premium_aud = abc["silver_sell_aud_oz"] - silver_spot_aud_oz

    gold_buyback_discount_aud_oz = gold_spot_aud_oz - abc["gold_buy_aud_oz"]
    silver_buyback_discount_aud_oz = silver_spot_aud_oz - abc["silver_buy_aud_oz"]

    # China world reference kept for future comparison/debug
    gold_ref_cny_g = (gold_usd * usd_cny) / OZ_TO_GRAMS
    silver_ref_cny_g = (silver_usd * usd_cny) / OZ_TO_GRAMS

    china_gold_pm = shanghai["gold_pm_cny_g"]
    china_silver_pm = shanghai["silver_pm_cny_g"]

    payload = {
        "status": "ok",
        "updated_at": now_iso(),

        "usa": {
            "gold_usd_oz": round2(gold_usd),
            "silver_usd_oz": round2(silver_usd)
        },

        "australia": {
            "abc_page_time": "",
            "gold_aud_oz": round2(gold_spot_aud_oz),
            "gold_spot_aud_oz": round2(gold_spot_aud_oz),
            "gold_buy_aud_oz": round2(abc["gold_buy_aud_oz"]),
            "gold_sell_aud_oz": round2(abc["gold_sell_aud_oz"]),
            "gold_buyback_discount_aud_oz": round2(gold_buyback_discount_aud_oz),
            "gold_premium_aud": round2(gold_premium_aud),
            "gold_premium_pct": round2(safe_pct(gold_premium_aud, gold_spot_aud_oz)),
            "gold_spread_aud_oz": round2(abc["gold_sell_aud_oz"] - abc["gold_buy_aud_oz"]),
            "gold_ref_category": "ABC Bullion Gold",
            "gold_ref_product": "1oz ABC Gold Cast Bar 9999",
            "gold_ref_weight_oz": 1.0,

            "silver_aud_oz": round2(silver_spot_aud_oz),
            "silver_spot_aud_oz": round2(silver_spot_aud_oz),
            "silver_buy_aud_oz": round2(abc["silver_buy_aud_oz"]),
            "silver_sell_aud_oz": round2(abc["silver_sell_aud_oz"]),
            "silver_buyback_discount_aud_oz": round2(silver_buyback_discount_aud_oz),
            "silver_premium_aud": round2(silver_premium_aud),
            "silver_premium_pct": round2(safe_pct(silver_premium_aud, silver_spot_aud_oz)),
            "silver_spread_aud_oz": round2(abc["silver_sell_aud_oz"] - abc["silver_buy_aud_oz"]),
            "silver_ref_category": "ABC Bullion Silver",
            "silver_ref_product": "10oz ABC Silver Cast Bar 9995",
            "silver_ref_weight_oz": 10.0,

            "source": "ABC Bullion API"
        },

        "china": {
            # keep latest price aliases for compatibility
            "gold_cny_g": round2(china_gold_pm),
            "silver_cny_g": round2(china_silver_pm),

            # new proper SGE AM/PM model
            "trade_date": shanghai["trade_date"],
            "gold_am_cny_g": round2(shanghai["gold_am_cny_g"]),
            "gold_pm_cny_g": round2(shanghai["gold_pm_cny_g"]),
            "gold_spread_cny_g": round2(shanghai["gold_spread_cny_g"]),
            "gold_move_pct": round2(shanghai["gold_move_pct"]),

            "silver_am_cny_g": round4(shanghai["silver_am_cny_g"]),
            "silver_pm_cny_g": round4(shanghai["silver_pm_cny_g"]),
            "silver_spread_cny_g": round4(shanghai["silver_spread_cny_g"]),
            "silver_move_pct": round2(shanghai["silver_move_pct"]),

            # world reference kept for later if you want premium-vs-world page
            "gold_ref_cny_g": round2(gold_ref_cny_g),
            "silver_ref_cny_g": round4(silver_ref_cny_g),

            "source": shanghai["source"]
        },

        "thailand": {
            "gold_bar_buy_thb": round2(parse_number(thai_data.get("buy_price", 0))),
            "gold_bar_sell_thb": round2(parse_number(thai_data.get("sell_price", 0))),
            "spread_thb": round2(
                parse_number(thai_data.get("sell_price", 0)) -
                parse_number(thai_data.get("buy_price", 0))
            )
        },

        "fx": {
            "usd_aud": round4(usd_aud),
            "usd_thb": round4(usd_thb),
            "usd_cny": round4(usd_cny)
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

    with _cache_lock:
        if _cache_payload and (time.time() - _cache_time < CACHE_TTL_SECONDS):
            return jsonify(_cache_payload)

        payload = build_payload()
        _cache_payload = payload
        _cache_time = time.time()

    return jsonify(payload)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
