from flask import Flask, jsonify
import requests
import os
import re
import threading
import time
from datetime import datetime, timezone

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

app = Flask(__name__)

# =========================
# CONFIG
# =========================
GOLD_API_BASE = "https://api.gold-api.com/price"
THAI_GOLD_API_URL = "https://api.chnwt.dev/thai-gold-api/latest"
ABC_FULL_PRICE_URL = "https://www.abcbullion.com.au/products-pricing/full-price-list"

OZ_TO_GRAMS = 31.1034768

FX_API_KEY = os.environ.get("FX_API_KEY")
FX_API_URL = f"https://v6.exchangerate-api.com/v6/{FX_API_KEY}/latest/USD"

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PapaDukeMetalsAPI/1.0; +https://papaduke-metals-api.onrender.com)"
}

JSON_TIMEOUT = 10
HTML_TIMEOUT = 12

CACHE_TTL_SECONDS = 55
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
    return float(str(value).replace(",", "").replace("$", "").strip())


def get_json(url, timeout=JSON_TIMEOUT):
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def get_text(url, timeout=HTML_TIMEOUT):
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def extract_product_prices(text, product_name):
    pattern = re.compile(
        re.escape(product_name) + r"\s*\$([\d,]+\.\d+)\s*\$([\d,]+\.\d+)",
        re.IGNORECASE | re.DOTALL
    )
    match = pattern.search(text)
    if not match:
        raise ValueError(f"Could not find product row: {product_name}")
    sell = parse_number(match.group(1))
    buy = parse_number(match.group(2))
    return sell, buy


def html_to_text(html):
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)
    else:
        text = re.sub(r"<script.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text


# =========================
# ABC SCRAPER
# =========================
def fetch_abc_prices():
    """
    Scrape ABC Bullion full product price list and calculate
    Australian reference premiums.

    Spot source:
    - Top black header:
      BUY GOLD 7379.99/oz
      BUY SILVER 123.46/oz

    Reference products:
    - Gold: 1oz ABC Gold Cast Bar 9999
    - Silver: 10oz ABC Silver Cast Bar 9995
    """
    html = get_text(ABC_FULL_PRICE_URL, timeout=HTML_TIMEOUT)
    text = html_to_text(html)

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)

    # Spot prices from top header
    gold_spot_match = re.search(r"BUY GOLD\s*([\d,]+\.\d+)/oz", text, re.IGNORECASE)
    silver_spot_match = re.search(r"BUY SILVER\s*([\d,]+\.\d+)/oz", text, re.IGNORECASE)

    if not gold_spot_match or not silver_spot_match:
        raise ValueError("Could not parse ABC header spot prices")

    gold_spot_aud_oz = parse_number(gold_spot_match.group(1))
    silver_spot_aud_oz = parse_number(silver_spot_match.group(1))

    # Reference products
    gold_ref_product = "1oz ABC Gold Cast Bar 9999"
    silver_ref_product = "10oz ABC Silver Cast Bar 9995"

    gold_sell_total, gold_buy_total = extract_product_prices(text, gold_ref_product)
    silver_sell_total, silver_buy_total = extract_product_prices(text, silver_ref_product)

    # Gold already 1 oz
    gold_sell_aud_oz = gold_sell_total
    gold_buy_aud_oz = gold_buy_total

    # Silver is 10 oz total -> convert to per oz
    silver_sell_aud_oz = silver_sell_total / 10.0
    silver_buy_aud_oz = silver_buy_total / 10.0

    # Premium logic
    gold_premium_aud_oz = gold_sell_aud_oz - gold_spot_aud_oz
    gold_spread_aud_oz = gold_sell_aud_oz - gold_buy_aud_oz
    gold_buyback_discount_aud_oz = gold_spot_aud_oz - gold_buy_aud_oz
    gold_premium_pct = (gold_premium_aud_oz / gold_spot_aud_oz * 100.0) if gold_spot_aud_oz else 0.0

    silver_premium_aud_oz = silver_sell_aud_oz - silver_spot_aud_oz
    silver_spread_aud_oz = silver_sell_aud_oz - silver_buy_aud_oz
    silver_buyback_discount_aud_oz = silver_spot_aud_oz - silver_buy_aud_oz
    silver_premium_pct = (silver_premium_aud_oz / silver_spot_aud_oz * 100.0) if silver_spot_aud_oz else 0.0

    live_price_list_time = ""
    time_match = re.search(
        r"Live Price List\s*(\d{2}\s+\w{3}\s+\d{4}\s+\d{2}:\d{2})",
        text,
        re.IGNORECASE
    )
    if time_match:
        live_price_list_time = time_match.group(1)

    return {
        "source": "ABC Bullion",
        "page_time": live_price_list_time,

        "gold_ref_product": gold_ref_product,
        "gold_spot_aud_oz": round2(gold_spot_aud_oz),
        "gold_sell_aud_oz": round2(gold_sell_aud_oz),
        "gold_buy_aud_oz": round2(gold_buy_aud_oz),
        "gold_premium_aud_oz": round2(gold_premium_aud_oz),
        "gold_spread_aud_oz": round2(gold_spread_aud_oz),
        "gold_buyback_discount_aud_oz": round2(gold_buyback_discount_aud_oz),
        "gold_premium_pct": round2(gold_premium_pct),

        "silver_ref_product": silver_ref_product,
        "silver_spot_aud_oz": round2(silver_spot_aud_oz),
        "silver_sell_aud_oz": round2(silver_sell_aud_oz),
        "silver_buy_aud_oz": round2(silver_buy_aud_oz),
        "silver_premium_aud_oz": round2(silver_premium_aud_oz),
        "silver_spread_aud_oz": round2(silver_spread_aud_oz),
        "silver_buyback_discount_aud_oz": round2(silver_buyback_discount_aud_oz),
        "silver_premium_pct": round2(silver_premium_pct)
    }


# =========================
# CORE PAYLOAD BUILDER
# =========================
def build_payload():
    if not FX_API_KEY:
        raise ValueError("FX_API_KEY is not set")

    gold_url = f"{GOLD_API_BASE}/XAU"
    silver_url = f"{GOLD_API_BASE}/XAG"

    print("DEBUG gold_url:", gold_url)
    print("DEBUG silver_url:", silver_url)
    print("DEBUG fx_url:", FX_API_URL)
    print("DEBUG thai_url:", THAI_GOLD_API_URL)
    print("DEBUG abc_url:", ABC_FULL_PRICE_URL)

    gold_data = get_json(gold_url)
    print("DEBUG gold_data:", gold_data)

    silver_data = get_json(silver_url)
    print("DEBUG silver_data:", silver_data)

    fx_data = get_json(FX_API_URL)
    print("DEBUG fx_data:", fx_data)

    thai_data = get_json(THAI_GOLD_API_URL)
    print("DEBUG thai_data:", thai_data)

    abc_data = None
    abc_error = None
    try:
        abc_data = fetch_abc_prices()
        print("DEBUG abc_data:", abc_data)
    except Exception as abc_exc:
        abc_error = str(abc_exc)
        print("DEBUG abc scrape failed:", abc_error)

    gold_usd = parse_number(gold_data["price"])
    silver_usd = parse_number(silver_data["price"])

    fx_rates = fx_data.get("conversion_rates", {})
    usd_aud = parse_number(fx_rates.get("AUD", 0))
    usd_thb = parse_number(fx_rates.get("THB", 0))
    usd_cny = parse_number(fx_rates.get("CNY", 0))

    if not usd_aud or not usd_thb or not usd_cny:
        raise ValueError("FX data missing AUD/THB/CNY")

    fallback_gold_aud = gold_usd * usd_aud
    fallback_silver_aud = silver_usd * usd_aud

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

    australia_payload = {
        "source": "ABC Bullion" if abc_data else "fallback-converted-spot",
        "abc_page_time": abc_data["page_time"] if abc_data else "",
        "gold_ref_product": abc_data["gold_ref_product"] if abc_data else "",
        "silver_ref_product": abc_data["silver_ref_product"] if abc_data else "",

        # Legacy fields for ESP32 compatibility
        "gold_aud_oz": round2(abc_data["gold_spot_aud_oz"] if abc_data else fallback_gold_aud),
        "silver_aud_oz": round2(abc_data["silver_spot_aud_oz"] if abc_data else fallback_silver_aud),
        "gold_premium_aud": round2(abc_data["gold_premium_aud_oz"] if abc_data else 0),
        "silver_premium_aud": round2(abc_data["silver_premium_aud_oz"] if abc_data else 0),

        # Explicit fields
        "gold_spot_aud_oz": round2(abc_data["gold_spot_aud_oz"] if abc_data else fallback_gold_aud),
        "gold_sell_aud_oz": round2(abc_data["gold_sell_aud_oz"] if abc_data else 0),
        "gold_buy_aud_oz": round2(abc_data["gold_buy_aud_oz"] if abc_data else 0),
        "gold_spread_aud_oz": round2(abc_data["gold_spread_aud_oz"] if abc_data else 0),
        "gold_buyback_discount_aud_oz": round2(abc_data["gold_buyback_discount_aud_oz"] if abc_data else 0),
        "gold_premium_pct": round2(abc_data["gold_premium_pct"] if abc_data else 0),

        "silver_spot_aud_oz": round2(abc_data["silver_spot_aud_oz"] if abc_data else fallback_silver_aud),
        "silver_sell_aud_oz": round2(abc_data["silver_sell_aud_oz"] if abc_data else 0),
        "silver_buy_aud_oz": round2(abc_data["silver_buy_aud_oz"] if abc_data else 0),
        "silver_spread_aud_oz": round2(abc_data["silver_spread_aud_oz"] if abc_data else 0),
        "silver_buyback_discount_aud_oz": round2(abc_data["silver_buyback_discount_aud_oz"] if abc_data else 0),
        "silver_premium_pct": round2(abc_data["silver_premium_pct"] if abc_data else 0)
    }

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
        },
        "debug": {
            "abc_scrape_ok": abc_data is not None,
            "abc_error": abc_error,
            "cache_hit": False
        }
    }

    return payload


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
