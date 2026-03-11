from flask import Flask, jsonify
import requests
import threading
import time
import re
import os
from datetime import datetime, timezone

app = Flask(__name__)

# =========================
# CONFIG
# =========================
APP_VERSION = "sge-am-pm-v6"

GOLD_API_BASE = "https://api.gold-api.com/price"
THAI_GOLD_API_URL = "https://api.chnwt.dev/thai-gold-api/latest"

ABC_GOLD_PRODUCTS_URL = "https://new-api.abcbullion.com.au/api/products?parentCategory=gold"
ABC_SILVER_PRODUCTS_URL = "https://new-api.abcbullion.com.au/api/products?parentCategory=silver"

SGE_GOLD_URL = "https://en.sge.com.cn/data_BenchmarkPrice_Daily"
SGE_SILVER_URL = "https://en.sge.com.cn/data/data_silver_daily"

BOWINS_SILVER_API_URL = "https://besserver.dyndns.org/ipn/response_silverbar.php"

OZ_TO_GRAMS = 31.1034768

FX_API_KEY = "d97e47cfb06d344fb3fffdc1"
FX_API_URL = f"https://v6.exchangerate-api.com/v6/{FX_API_KEY}/latest/USD"

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (PapaDukeMetalsAPI)"
}

JSON_TIMEOUT = 12
CACHE_TTL_SECONDS = 55
HTTP_RETRIES = 3
HTTP_BACKOFF_SECONDS = 1.5

session = requests.Session()
session.headers.update(HTTP_HEADERS)

# =========================
# CACHE
# =========================
_cache_lock = threading.Lock()
_cache_payload = None
_cache_time = 0.0

# =========================
# LAST GOOD FX
# =========================
_last_good_fx = {
    "usd_aud": 0.0,
    "usd_thb": 0.0,
    "usd_cny": 0.0,
    "ok": False
}

# =========================
# UTILS
# =========================

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

    s = str(value).replace(",", "").replace("$", "").strip()
    if not s:
        return 0.0

    try:
        return float(s)
    except Exception:
        return 0.0


def http_get_with_retry(url, timeout=JSON_TIMEOUT, retries=HTTP_RETRIES, backoff=HTTP_BACKOFF_SECONDS):
    last_error = None

    for attempt in range(retries):
        try:
            r = session.get(url, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))

    raise last_error


def get_json(url):
    r = http_get_with_retry(url)
    return r.json()


def safe_fetch(name, func, fallback, errors):
    try:
        print(f"[FETCH_START] {name}")
        result = func()
        print(f"[FETCH_OK] {name}")
        return result
    except Exception as e:
        errors[name] = str(e)
        print(f"[ERROR] {name}: {e}")
        return fallback


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

    if not gold_item or not silver_item:
        raise ValueError("ABC reference products not found")

    gold_weight = parse_number(gold_item.get("itemShopPriceWeightOunces"))
    silver_weight = parse_number(silver_item.get("itemShopPriceWeightOunces"))

    gold_buy = parse_number(gold_item.get("purchasePrice"))
    gold_sell = parse_number(gold_item.get("sellPrice"))

    silver_buy = parse_number(silver_item.get("purchasePrice"))
    silver_sell = parse_number(silver_item.get("sellPrice"))

    return {
        "gold_buy_aud_oz": gold_buy / gold_weight,
        "gold_sell_aud_oz": gold_sell / gold_weight,
        "silver_buy_aud_oz": silver_buy / silver_weight,
        "silver_sell_aud_oz": silver_sell / silver_weight
    }


# ======================================================
# BUILD PAYLOAD
# ======================================================

def build_payload():

    global _last_good_fx

    errors = {}

    gold_data = safe_fetch(
        "gold_api_xau",
        lambda: get_json(f"{GOLD_API_BASE}/XAU"),
        {},
        errors
    )

    silver_data = safe_fetch(
        "gold_api_xag",
        lambda: get_json(f"{GOLD_API_BASE}/XAG"),
        {},
        errors
    )

    fx_data = safe_fetch(
        "fx_api",
        lambda: get_json(FX_API_URL),
        {},
        errors
    )

    abc = safe_fetch(
        "abc_reference",
        fetch_abc_reference_prices,
        {
            "gold_buy_aud_oz": 0.0,
            "gold_sell_aud_oz": 0.0,
            "silver_buy_aud_oz": 0.0,
            "silver_sell_aud_oz": 0.0
        },
        errors
    )

    gold_usd = parse_number(gold_data.get("price", 0))
    silver_usd = parse_number(silver_data.get("price", 0))

    # =========================
    # FX WITH FALLBACK
    # =========================

    rates = fx_data.get("conversion_rates", {})

    usd_aud = parse_number(rates.get("AUD"))
    usd_thb = parse_number(rates.get("THB"))
    usd_cny = parse_number(rates.get("CNY"))

    if usd_aud > 0 and usd_thb > 0 and usd_cny > 0:

        _last_good_fx["usd_aud"] = usd_aud
        _last_good_fx["usd_thb"] = usd_thb
        _last_good_fx["usd_cny"] = usd_cny
        _last_good_fx["ok"] = True

        fx_mode = "live"

    elif _last_good_fx["ok"]:

        usd_aud = _last_good_fx["usd_aud"]
        usd_thb = _last_good_fx["usd_thb"]
        usd_cny = _last_good_fx["usd_cny"]

        fx_mode = "fallback_last_good"

    else:

        usd_aud = 0
        usd_thb = 0
        usd_cny = 0

        fx_mode = "no_fx_available"

    # =========================

    gold_spot_aud_oz = gold_usd * usd_aud
    silver_spot_aud_oz = silver_usd * usd_aud

    payload = {

        "status": "ok" if not errors else "partial",
        "api_version": APP_VERSION,
        "updated_at": now_iso(),

        "usa": {
            "gold_usd_oz": round2(gold_usd),
            "silver_usd_oz": round2(silver_usd)
        },

        "australia": {
            "gold_spot_aud_oz": round2(gold_spot_aud_oz),
            "silver_spot_aud_oz": round2(silver_spot_aud_oz),

            "gold_buy_aud_oz": round2(abc["gold_buy_aud_oz"]),
            "gold_sell_aud_oz": round2(abc["gold_sell_aud_oz"]),

            "silver_buy_aud_oz": round2(abc["silver_buy_aud_oz"]),
            "silver_sell_aud_oz": round2(abc["silver_sell_aud_oz"])
        },

        "fx": {
            "usd_aud": round4(usd_aud),
            "usd_thb": round4(usd_thb),
            "usd_cny": round4(usd_cny),
            "mode": fx_mode
        },

        "errors": errors
    }

    return payload


# ======================================================
# ROUTES
# ======================================================

@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "api_version": APP_VERSION
    })


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "PapaDuke Metals API",
        "version": APP_VERSION,
        "time": now_iso()
    })


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


# ======================================================
# RUN
# ======================================================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )
