from flask import Flask, jsonify
import requests
import os
import re
from datetime import datetime, timezone
from bs4 import BeautifulSoup

app = Flask(__name__)

GOLD_API_BASE = "https://api.gold-api.com/price"
THAI_GOLD_API_URL = "https://api.chnwt.dev/thai-gold-api/latest"
ABC_FULL_PRICE_URL = "https://www.abcbullion.com.au/products-pricing/full-price-list"

OZ_TO_GRAMS = 31.1034768

FX_API_KEY = os.environ.get("FX_API_KEY")
FX_API_URL = f"https://v6.exchangerate-api.com/v6/{FX_API_KEY}/latest/USD"

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; PapaDukeMetalsAPI/1.0; +https://papaduke-metals-api.onrender.com)"
}


def round2(value):
    return round(float(value), 2)


def get_json(url):
    response = requests.get(url, timeout=15, headers=HTTP_HEADERS)
    response.raise_for_status()
    return response.json()


def get_text(url):
    response = requests.get(url, timeout=20, headers=HTTP_HEADERS)
    response.raise_for_status()
    return response.text


def parse_number(value):
    if value is None:
        return 0.0
    return float(str(value).replace(",", "").replace("$", "").strip())


def extract_between(text, start_pattern, end_pattern):
    pattern = re.compile(start_pattern + r"(.*?)" + end_pattern, re.IGNORECASE | re.DOTALL)
    match = pattern.search(text)
    return match.group(1) if match else ""


def extract_money_after(label, text):
    pattern = re.compile(re.escape(label) + r"\s*\$([\d,]+\.\d+)", re.IGNORECASE | re.DOTALL)
    match = pattern.search(text)
    if not match:
        return 0.0
    return parse_number(match.group(1))


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


def fetch_abc_prices():
    """
    Scrape ABC Bullion full product price list and calculate
    real Australian reference premiums.

    Chosen reference products:
    - Gold: 1oz ABC Gold Cast Bar 9999
    - Silver: 10oz ABC Silver Cast Bar 9995
    """
    html = get_text(ABC_FULL_PRICE_URL)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)

    # Make whitespace easier to parse
    text = re.sub(r"[ \t]+", " ", text)

    gold_section = extract_between(
        text,
        r"\bgold\b\s*SPOT PRICE PER TROY OUNCE",
        r"\bsilver\b\s*SPOT PRICE PER TROY OUNCE"
    )
    silver_section = extract_between(
        text,
        r"\bsilver\b\s*SPOT PRICE PER TROY OUNCE",
        r"\bplatinum\b\s*SPOT PRICE PER TROY OUNCE"
    )

    if not gold_section or not silver_section:
        raise ValueError("Could not isolate ABC gold/silver sections")

    # Spot prices from ABC page
    gold_spot_aud_oz = extract_money_after("SPOT PRICE PER TROY OUNCE", gold_section)
    silver_spot_aud_oz = extract_money_after("SPOT PRICE PER TROY OUNCE", silver_section)

    if not gold_spot_aud_oz or not silver_spot_aud_oz:
        raise ValueError("Could not parse ABC spot prices")

    # Reference products
    gold_ref_product = "1oz ABC Gold Cast Bar 9999"
    silver_ref_product = "10oz ABC Silver Cast Bar 9995"

    gold_sell_total, gold_buy_total = extract_product_prices(gold_section, gold_ref_product)
    silver_sell_total, silver_buy_total = extract_product_prices(silver_section, silver_ref_product)

    # Convert chosen products to per-oz where needed
    gold_sell_aud_oz = gold_sell_total          # already 1 oz
    gold_buy_aud_oz = gold_buy_total            # already 1 oz

    silver_sell_aud_oz = silver_sell_total / 10.0
    silver_buy_aud_oz = silver_buy_total / 10.0

    # Premium and spread logic
    gold_premium_aud_oz = gold_sell_aud_oz - gold_spot_aud_oz
    gold_spread_aud_oz = gold_sell_aud_oz - gold_buy_aud_oz
    gold_buyback_discount_aud_oz = gold_spot_aud_oz - gold_buy_aud_oz
    gold_premium_pct = (gold_premium_aud_oz / gold_spot_aud_oz * 100.0) if gold_spot_aud_oz else 0.0

    silver_premium_aud_oz = silver_sell_aud_oz - silver_spot_aud_oz
    silver_spread_aud_oz = silver_sell_aud_oz - silver_buy_aud_oz
    silver_buyback_discount_aud_oz = silver_spot_aud_oz - silver_buy_aud_oz
    silver_premium_pct = (silver_premium_aud_oz / silver_spot_aud_oz * 100.0) if silver_spot_aud_oz else 0.0

    # Try to grab live timestamp shown on page
    live_price_list_time = ""
    time_match = re.search(r"Live Price List\s*(\d{2}\s+\w{3}\s+\d{4}\s+\d{2}:\d{2})", text, re.IGNORECASE)
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
        print("DEBUG abc_url:", ABC_FULL_PRICE_URL)

        gold_data = get_json(gold_url)
        silver_data = get_json(silver_url)
        fx_data = get_json(FX_API_URL)
        thai_data = get_json(THAI_GOLD_API_URL)

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
            return jsonify({
                "status": "error",
                "message": "FX data missing AUD/THB/CNY",
                "fx_data": fx_data
            }), 500

        # Fallback converted spot if ABC scrape fails
        fallback_gold_aud = gold_usd * usd_aud
        fallback_silver_aud = silver_usd * usd_aud

        # China remains reference conversion
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

        # Australia: use ABC live data if available
        australia_payload = {
            "source": "ABC Bullion" if abc_data else "fallback-converted-spot",
            "abc_page_time": abc_data["page_time"] if abc_data else "",
            "gold_ref_product": abc_data["gold_ref_product"] if abc_data else "",
            "silver_ref_product": abc_data["silver_ref_product"] if abc_data else "",

            # Keep legacy fields for ESP32 compatibility
            "gold_aud_oz": round2(abc_data["gold_spot_aud_oz"] if abc_data else fallback_gold_aud),
            "silver_aud_oz": round2(abc_data["silver_spot_aud_oz"] if abc_data else fallback_silver_aud),
            "gold_premium_aud": round2(abc_data["gold_premium_aud_oz"] if abc_data else 0),
            "silver_premium_aud": round2(abc_data["silver_premium_aud_oz"] if abc_data else 0),

            # New explicit fields
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
            "updated_at": datetime.now(timezone.utc).isoformat(),
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
                "abc_error": abc_error
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
