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

FX_API_KEY = os.environ.get("FX_API_KEY", "").strip()
FX_API_URL = f"https://v6.exchangerate-api.com/v6/{FX_API_KEY}/latest/USD" if FX_API_KEY else ""

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PapaDukeMetalsAPI/4.0; +https://papaduke-metals-api.onrender.com)"
}

JSON_TIMEOUT = 12
CACHE_TTL_SECONDS = 55

# =========================
# CACHE
# =========================
_cache_lock = threading.Lock()
_cache_payload = None
_cache_time = 0.0

session = requests.Session()
session.headers.update(HTTP_HEADERS)

# =========================
# HELPERS
# =========================
def round2(value):
    return round(float(value), 2)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_number(value):
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).replace(",", "").replace("$", "").strip()
    if s in ("", "/", "-", "null", "None"):
        return 0.0
    return float(s)


def get_json(url, timeout=JSON_TIMEOUT):
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def find_product_by_name(products, product_name):
    for item in products:
        if str(item.get("itemName", "")).strip() == product_name:
            return item
    return None


def get_cached_payload_age():
    with _cache_lock:
        if _cache_payload is None:
            return None
        return time.time() - _cache_time


def get_cached_payload():
    with _cache_lock:
        if _cache_payload is None:
            return None
        return _cache_payload


def set_cached_payload(payload):
    global _cache_payload, _cache_time
    with _cache_lock:
        _cache_payload = payload
        _cache_time = time.time()


def safe_pct(numerator, denominator):
    if not denominator:
        return 0.0
    return (numerator / denominator) * 100.0


# =========================
# SHANGHAI / SGE
# =========================
def fetch_shanghai_local_prices():
    """
    Real Shanghai benchmark from official SGE text endpoints.

    Gold:
    https://en.sge.com.cn/data_BenchmarkPrice_Daily
    Example row:
    20260309 SHAU 1138.44 /

    Silver:
    https://en.sge.com.cn/data/data_silver_daily
    Example row:
    20260309 SHAG 20497 0

    Returns:
    {
        "gold_cny_g": float,
        "silver_cny_g": float,
        "source": "SGE Benchmark",
        "gold_contract": "SHAU",
        "silver_contract": "SHAG"
    }
    """

    result = {
        "gold_cny_g": 0.0,
        "silver_cny_g": 0.0,
        "source": "SGE Benchmark",
        "gold_contract": "SHAU",
        "silver_contract": "SHAG"
    }

    # GOLD: SHAU, unit CNY/g
    try:
        gold_url = "https://en.sge.com.cn/data_BenchmarkPrice_Daily"
        r = session.get(gold_url, timeout=JSON_TIMEOUT)
        r.raise_for_status()

        for line in r.text.splitlines():
            parts = line.strip().split()

            # Valid row like:
            # 20260309 SHAU 1138.44 /
            if len(parts) >= 3 and parts[1] == "SHAU":
                gold_am = parse_number(parts[2])
                if gold_am > 0:
                    result["gold_cny_g"] = gold_am
                    break

    except Exception as e:
        print("SGE gold parser error:", e)

    # SILVER: SHAG, source unit CNY/kg -> convert to CNY/g
    try:
        silver_url = "https://en.sge.com.cn/data/data_silver_daily"
        r = session.get(silver_url, timeout=JSON_TIMEOUT)
        r.raise_for_status()

        for line in r.text.splitlines():
            parts = line.strip().split()

            # Valid row like:
            # 20260309 SHAG 20497 0
            if len(parts) >= 3 and parts[1] == "SHAG":
                silver_am_kg = parse_number(parts[2])
                if silver_am_kg > 0:
                    result["silver_cny_g"] = silver_am_kg / 1000.0
                    break

    except Exception as e:
        print("SGE silver parser error:", e)

    return result


# =========================
# ABC API LOGIC
# =========================
def fetch_abc_reference_prices():
    """
    Use ABC Bullion JSON product API.

    Reference products:
    - Gold: 1oz ABC Gold Cast Bar 9999
    - Silver: 10oz ABC Silver Cast Bar 9995
    """

    gold_products = get_json(ABC_GOLD_PRODUCTS_URL)
    silver_products = get_json(ABC_SILVER_PRODUCTS_URL)

    gold_ref_product = "1oz ABC Gold Cast Bar 9999"
    silver_ref_product = "10oz ABC Silver Cast Bar 9995"

    gold_item = find_product_by_name(gold_products, gold_ref_product)
    silver_item = find_product_by_name(silver_products, silver_ref_product)

    if not gold_item:
        raise ValueError(f"ABC gold reference product not found: {gold_ref_product}")
    if not silver_item:
        raise ValueError(f"ABC silver reference product not found: {silver_ref_product}")

    gold_weight_oz = parse_number(gold_item.get("itemShopPriceWeightOunces", 0))
    silver_weight_oz = parse_number(silver_item.get("itemShopPriceWeightOunces", 0))

    if gold_weight_oz <= 0:
        raise ValueError("ABC gold reference product has invalid ounce weight")
    if silver_weight_oz <= 0:
        raise ValueError("ABC silver reference product has invalid ounce weight")

    gold_buy_total = parse_number(gold_item.get("purchasePrice", 0))
    gold_sell_total = parse_number(gold_item.get("sellPrice", 0))

    silver_buy_total = parse_number(silver_item.get("purchasePrice", 0))
    silver_sell_total = parse_number(silver_item.get("sellPrice", 0))

    if gold_buy_total <= 0 or gold_sell_total <= 0:
        raise ValueError("ABC gold reference pricing missing or invalid")
    if silver_buy_total <= 0 or silver_sell_total <= 0:
        raise ValueError("ABC silver reference pricing missing or invalid")

    gold_buy_aud_oz = gold_buy_total / gold_weight_oz
    gold_sell_aud_oz = gold_sell_total / gold_weight_oz

    silver_buy_aud_oz = silver_buy_total / silver_weight_oz
    silver_sell_aud_oz = silver_sell_total / silver_weight_oz

    return {
        "source": "ABC Bullion API",
        "abc_page_time": "",

        "gold_ref_product": gold_ref_product,
        "gold_ref_category": gold_item.get("categoryName", ""),
        "gold_ref_weight_oz": round2(gold_weight_oz),
        "gold_buy_aud_oz": round2(gold_buy_aud_oz),
        "gold_sell_aud_oz": round2(gold_sell_aud_oz),

        "silver_ref_product": silver_ref_product,
        "silver_ref_category": silver_item.get("categoryName", ""),
        "silver_ref_weight_oz": round2(silver_weight_oz),
        "silver_buy_aud_oz": round2(silver_buy_aud_oz),
        "silver_sell_aud_oz": round2(silver_sell_aud_oz),
    }


# =========================
# CORE PAYLOAD BUILDER
# =========================
def build_payload():
    if not FX_API_KEY:
        raise ValueError("FX_API_KEY is not set")

    gold_url = f"{GOLD_API_BASE}/XAU"
    silver_url = f"{GOLD_API_BASE}/XAG"

    gold_data = get_json(gold_url)
    silver_data = get_json(silver_url)
    fx_data = get_json(FX_API_URL)
    thai_data = get_json(THAI_GOLD_API_URL)

    abc_data = None
    abc_error = None
    try:
        abc_data = fetch_abc_reference_prices()
    except Exception as abc_exc:
        abc_error = str(abc_exc)

    shanghai_data = None
    shanghai_error = None
    try:
        shanghai_data = fetch_shanghai_local_prices()
    except Exception as sh_exc:
        shanghai_error = str(sh_exc)

    gold_usd = parse_number(gold_data.get("price"))
    silver_usd = parse_number(silver_data.get("price"))

    fx_rates = fx_data.get("conversion_rates", {}) or fx_data.get("rates", {})
    usd_aud = parse_number(fx_rates.get("AUD", 0))
    usd_thb = parse_number(fx_rates.get("THB", 0))
    usd_cny = parse_number(fx_rates.get("CNY", 0))

    if not usd_aud or not usd_thb or not usd_cny:
        raise ValueError("FX data missing AUD/THB/CNY")

    # =========================
    # WORLD IMPLIED REFERENCE
    # =========================
    gold_spot_aud_oz = gold_usd * usd_aud
    silver_spot_aud_oz = silver_usd * usd_aud

    gold_ref_cny_g = (gold_usd * usd_cny) / OZ_TO_GRAMS
    silver_ref_cny_g = (silver_usd * usd_cny) / OZ_TO_GRAMS

    # =========================
    # THAILAND
    # =========================
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

    # =========================
    # AUSTRALIA
    # =========================
    if abc_data:
        gold_buy_aud_oz = abc_data["gold_buy_aud_oz"]
        gold_sell_aud_oz = abc_data["gold_sell_aud_oz"]
        silver_buy_aud_oz = abc_data["silver_buy_aud_oz"]
        silver_sell_aud_oz = abc_data["silver_sell_aud_oz"]

        gold_premium_aud_oz = gold_sell_aud_oz - gold_spot_aud_oz
        gold_spread_aud_oz = gold_sell_aud_oz - gold_buy_aud_oz
        gold_buyback_discount_aud_oz = gold_spot_aud_oz - gold_buy_aud_oz
        gold_premium_pct = safe_pct(gold_premium_aud_oz, gold_spot_aud_oz)

        silver_premium_aud_oz = silver_sell_aud_oz - silver_spot_aud_oz
        silver_spread_aud_oz = silver_sell_aud_oz - silver_buy_aud_oz
        silver_buyback_discount_aud_oz = silver_spot_aud_oz - silver_buy_aud_oz
        silver_premium_pct = safe_pct(silver_premium_aud_oz, silver_spot_aud_oz)

        australia_payload = {
            "source": abc_data["source"],
            "abc_page_time": abc_data["abc_page_time"],

            "gold_ref_product": abc_data["gold_ref_product"],
            "gold_ref_category": abc_data["gold_ref_category"],
            "gold_ref_weight_oz": abc_data["gold_ref_weight_oz"],

            "silver_ref_product": abc_data["silver_ref_product"],
            "silver_ref_category": abc_data["silver_ref_category"],
            "silver_ref_weight_oz": abc_data["silver_ref_weight_oz"],

            # Legacy fields used by ESP32
            "gold_aud_oz": round2(gold_spot_aud_oz),
            "silver_aud_oz": round2(silver_spot_aud_oz),
            "gold_premium_aud": round2(gold_premium_aud_oz),
            "silver_premium_aud": round2(silver_premium_aud_oz),

            # Explicit fields
            "gold_spot_aud_oz": round2(gold_spot_aud_oz),
            "gold_sell_aud_oz": round2(gold_sell_aud_oz),
            "gold_buy_aud_oz": round2(gold_buy_aud_oz),
            "gold_spread_aud_oz": round2(gold_spread_aud_oz),
            "gold_buyback_discount_aud_oz": round2(gold_buyback_discount_aud_oz),
            "gold_premium_pct": round2(gold_premium_pct),

            "silver_spot_aud_oz": round2(silver_spot_aud_oz),
            "silver_sell_aud_oz": round2(silver_sell_aud_oz),
            "silver_buy_aud_oz": round2(silver_buy_aud_oz),
            "silver_spread_aud_oz": round2(silver_spread_aud_oz),
            "silver_buyback_discount_aud_oz": round2(silver_buyback_discount_aud_oz),
            "silver_premium_pct": round2(silver_premium_pct),
        }
    else:
        australia_payload = {
            "source": "fallback-converted-spot",
            "abc_page_time": "",
            "gold_ref_product": "",
            "gold_ref_category": "",
            "gold_ref_weight_oz": 0,
            "silver_ref_product": "",
            "silver_ref_category": "",
            "silver_ref_weight_oz": 0,

            # Legacy fields used by ESP32
            "gold_aud_oz": round2(gold_spot_aud_oz),
            "silver_aud_oz": round2(silver_spot_aud_oz),
            "gold_premium_aud": 0.0,
            "silver_premium_aud": 0.0,

            # Explicit fields
            "gold_spot_aud_oz": round2(gold_spot_aud_oz),
            "gold_sell_aud_oz": 0.0,
            "gold_buy_aud_oz": 0.0,
            "gold_spread_aud_oz": 0.0,
            "gold_buyback_discount_aud_oz": 0.0,
            "gold_premium_pct": 0.0,

            "silver_spot_aud_oz": round2(silver_spot_aud_oz),
            "silver_sell_aud_oz": 0.0,
            "silver_buy_aud_oz": 0.0,
            "silver_spread_aud_oz": 0.0,
            "silver_buyback_discount_aud_oz": 0.0,
            "silver_premium_pct": 0.0,
        }

    # =========================
    # CHINA / SHANGHAI
    # =========================
    sh_gold_local = 0.0
    sh_silver_local = 0.0
    china_source = "world-implied-usd-cny"
    china_local_available = False

    if shanghai_data:
        sh_gold_local = parse_number(shanghai_data.get("gold_cny_g", 0))
        sh_silver_local = parse_number(shanghai_data.get("silver_cny_g", 0))
        china_source = shanghai_data.get("source", "SGE Benchmark")
        china_local_available = (sh_gold_local > 0 or sh_silver_local > 0)

    gold_cny_g = sh_gold_local if sh_gold_local > 0 else gold_ref_cny_g
    silver_cny_g = sh_silver_local if sh_silver_local > 0 else silver_ref_cny_g

    gold_spread_cny_g = gold_cny_g - gold_ref_cny_g
    silver_spread_cny_g = silver_cny_g - silver_ref_cny_g

    gold_spread_pct = safe_pct(gold_spread_cny_g, gold_ref_cny_g)
    silver_spread_pct = safe_pct(silver_spread_cny_g, silver_ref_cny_g)

    payload = {
        "status": "ok",
        "updated_at": now_iso(),
        "usa": {
            "gold_usd_oz": round2(gold_usd),
            "silver_usd_oz": round2(silver_usd)
        },
        "australia": australia_payload,
        "thailand": {
            "gold_bar_buy_thb": round2(thai_gold_bar_buy),
            "gold_bar_sell_thb": round2(thai_gold_bar_sell),
            "spread_thb": round2(abs(thai_gold_bar_sell - thai_gold_bar_buy))
        },
        "china": {
            "gold_cny_g": round2(gold_cny_g),
            "silver_cny_g": round2(silver_cny_g),

            "gold_ref_cny_g": round2(gold_ref_cny_g),
            "silver_ref_cny_g": round2(silver_ref_cny_g),

            "gold_spread_cny_g": round2(gold_spread_cny_g),
            "silver_spread_cny_g": round2(silver_spread_cny_g),
            "gold_spread_pct": round2(gold_spread_pct),
            "silver_spread_pct": round2(silver_spread_pct),

            # compatibility fields
            "gold_premium_cny_g": 0,
            "silver_premium_cny_g": 0,

            "source": china_source,
            "local_available": china_local_available
        },
        "fx": {
            "usd_aud": round2(usd_aud),
            "usd_thb": round2(usd_thb),
            "usd_cny": round2(usd_cny)
        },
        "indicators": {
            "gold_silver_ratio": round2(gold_usd / silver_usd) if silver_usd else None
        },
        "debug": {
            "abc_api_ok": abc_data is not None,
            "abc_error": abc_error,
            "shanghai_api_ok": shanghai_data is not None,
            "shanghai_error": shanghai_error,
            "cache_hit": False
        }
    }

    return payload


# =========================
# ROUTES
# =========================
@app.route("/")
def home():
    age = get_cached_payload_age()
    return jsonify({
        "service": "PapaDuke Metals API",
        "status": "running",
        "message": "root ok",
        "cache_age_seconds": round(age, 2) if age is not None else None
    })


@app.route("/ping")
def ping():
    return "pong", 200


@app.route("/api/v1/prices")
def prices():
    try:
        cached = get_cached_payload()
        age = get_cached_payload_age()

        if cached is not None and age is not None and age < CACHE_TTL_SECONDS:
            cached_copy = dict(cached)
            cached_copy["debug"] = dict(cached.get("debug", {}))
            cached_copy["debug"]["cache_hit"] = True
            cached_copy["debug"]["cache_age_seconds"] = round(age, 2)
            return jsonify(cached_copy)

        payload = build_payload()
        set_cached_payload(payload)
        payload["debug"]["cache_age_seconds"] = 0
        return jsonify(payload)

    except Exception as e:
        print("DEBUG exception:", repr(e))

        cached = get_cached_payload()
        age = get_cached_payload_age()

        if cached is not None:
            cached_copy = dict(cached)
            cached_copy["debug"] = dict(cached.get("debug", {}))
            cached_copy["debug"]["cache_hit"] = True
            cached_copy["debug"]["cache_stale_served"] = True
            cached_copy["debug"]["cache_age_seconds"] = round(age, 2) if age is not None else None
            cached_copy["debug"]["last_refresh_error"] = str(e)
            return jsonify(cached_copy), 200

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
