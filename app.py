from flask import Flask, jsonify
import requests
import threading
import time
import re
from datetime import datetime, timezone

app = Flask(__name__)

# =========================
# CONFIG
# =========================
APP_VERSION = "sge-am-pm-v5"

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
# LAST GOOD FALLBACKS
# =========================
_last_good_shanghai = {
    "trade_date": "",
    "gold_am_cny_g": 0.0,
    "gold_pm_cny_g": 0.0,
    "gold_spread_cny_g": 0.0,
    "gold_move_pct": 0.0,
    "silver_am_cny_g": 0.0,
    "silver_pm_cny_g": 0.0,
    "silver_effective_cny_g": 0.0,
    "silver_spread_cny_g": 0.0,
    "silver_move_pct": 0.0,
    "source": "SGE Benchmark",
    "ok_gold": False,
    "ok_silver": False,
    "error_gold": None,
    "error_silver": None,
    "gold_mode": "",
    "silver_mode": ""
}


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


def safe_pct(change, base):
    if not base:
        return 0.0
    return (change / base) * 100.0


def safe_fetch(name, func, fallback, errors):
    try:
        return func()
    except Exception as e:
        errors[name] = str(e)
        print(f"[ERROR] {name}: {e}")
        return fallback


# ======================================================
# SGE BENCHMARK PARSER - ROBUST VERSION
# ======================================================
def clean_html_text(text):
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = cleaned.replace("&nbsp;", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def extract_all_sge_rows(text, contract_code):
    cleaned = clean_html_text(text)

    pattern = (
        r"(\d{8})\s+"
        + re.escape(contract_code)
        + r"\s+([0-9]+(?:\.[0-9]+)?)\s+([0-9]+(?:\.[0-9]+)?)"
    )

    matches = re.findall(pattern, cleaned)
    rows = []

    for trade_date, am, pm in matches:
        rows.append({
            "trade_date": trade_date,
            "am": parse_number(am),
            "pm": parse_number(pm),
        })

    return rows


def choose_best_sge_row(rows):
    if not rows:
        return None, "no rows found"

    rows_sorted = sorted(rows, key=lambda x: x["trade_date"], reverse=True)

    for row in rows_sorted:
        if row["pm"] > 0:
            return {
                "trade_date": row["trade_date"],
                "am": row["am"],
                "pm": row["pm"],
                "effective": row["pm"],
                "mode": "pm"
            }, None

    for row in rows_sorted:
        if row["am"] > 0:
            return {
                "trade_date": row["trade_date"],
                "am": row["am"],
                "pm": row["pm"],
                "effective": row["am"],
                "mode": "am_fallback"
            }, None

    return None, "rows found but all AM/PM values are zero"


def fetch_shanghai_benchmark_prices():
    global _last_good_shanghai

    result = {
        "trade_date": "",
        "gold_am_cny_g": 0.0,
        "gold_pm_cny_g": 0.0,
        "gold_spread_cny_g": 0.0,
        "gold_move_pct": 0.0,
        "silver_am_cny_g": 0.0,
        "silver_pm_cny_g": 0.0,
        "silver_effective_cny_g": 0.0,
        "silver_spread_cny_g": 0.0,
        "silver_move_pct": 0.0,
        "source": "SGE Benchmark",
        "ok_gold": False,
        "ok_silver": False,
        "error_gold": None,
        "error_silver": None,
        "gold_mode": "",
        "silver_mode": ""
    }

    # ----------------------
    # GOLD
    # ----------------------
    try:
        r = http_get_with_retry(SGE_GOLD_URL)
        gold_rows = extract_all_sge_rows(r.text, "SHAU")
        chosen_gold, gold_err = choose_best_sge_row(gold_rows)

        if chosen_gold:
            am_g = chosen_gold["am"]
            pm_g = chosen_gold["pm"]
            effective_g = chosen_gold["effective"]
            spread_g = (pm_g - am_g) if (am_g > 0 and pm_g > 0) else 0.0
            move_pct = safe_pct(spread_g, am_g) if (am_g > 0 and pm_g > 0) else 0.0

            result["trade_date"] = chosen_gold["trade_date"]
            result["gold_am_cny_g"] = am_g
            result["gold_pm_cny_g"] = pm_g if pm_g > 0 else effective_g
            result["gold_spread_cny_g"] = spread_g
            result["gold_move_pct"] = move_pct
            result["ok_gold"] = True
            result["gold_mode"] = chosen_gold["mode"]

            if chosen_gold["mode"] == "am_fallback":
                result["error_gold"] = "PM not published yet, using AM"
            else:
                result["error_gold"] = None
        else:
            result["error_gold"] = gold_err or "unknown gold parse failure"

    except Exception as e:
        result["error_gold"] = str(e)

    # ----------------------
    # SILVER
    # ----------------------
    try:
        r = http_get_with_retry(SGE_SILVER_URL)
        silver_rows = extract_all_sge_rows(r.text, "SHAG")
        chosen_silver, silver_err = choose_best_sge_row(silver_rows)

        if chosen_silver:
            am_g = chosen_silver["am"] / 1000.0 if chosen_silver["am"] > 0 else 0.0
            pm_g = chosen_silver["pm"] / 1000.0 if chosen_silver["pm"] > 0 else 0.0
            effective_g = chosen_silver["effective"] / 1000.0 if chosen_silver["effective"] > 0 else 0.0

            spread_g = (pm_g - am_g) if (am_g > 0 and pm_g > 0) else 0.0
            move_pct = safe_pct(spread_g, am_g) if (am_g > 0 and pm_g > 0) else 0.0

            if not result["trade_date"]:
                result["trade_date"] = chosen_silver["trade_date"]

            result["silver_am_cny_g"] = am_g
            result["silver_pm_cny_g"] = pm_g
            result["silver_effective_cny_g"] = effective_g
            result["silver_spread_cny_g"] = spread_g
            result["silver_move_pct"] = move_pct
            result["ok_silver"] = True
            result["silver_mode"] = chosen_silver["mode"]

            if chosen_silver["mode"] == "am_fallback":
                result["error_silver"] = "PM not published yet, using AM"
            else:
                result["error_silver"] = None
        else:
            result["error_silver"] = silver_err or "unknown silver parse failure"

    except Exception as e:
        result["error_silver"] = str(e)

    # ----------------------
    # LAST GOOD FALLBACK
    # ----------------------
    if not result["ok_silver"] and _last_good_shanghai.get("ok_silver"):
        result["silver_am_cny_g"] = _last_good_shanghai.get("silver_am_cny_g", 0.0)
        result["silver_pm_cny_g"] = _last_good_shanghai.get("silver_pm_cny_g", 0.0)
        result["silver_effective_cny_g"] = _last_good_shanghai.get("silver_effective_cny_g", 0.0)
        result["silver_spread_cny_g"] = _last_good_shanghai.get("silver_spread_cny_g", 0.0)
        result["silver_move_pct"] = _last_good_shanghai.get("silver_move_pct", 0.0)

        if not result["trade_date"]:
            result["trade_date"] = _last_good_shanghai.get("trade_date", "")

        result["ok_silver"] = True
        result["silver_mode"] = "last_good_fallback"
        result["error_silver"] = f"Using last good silver data because current fetch failed: {result['error_silver']}"

    if not result["ok_gold"] and _last_good_shanghai.get("ok_gold"):
        result["gold_am_cny_g"] = _last_good_shanghai.get("gold_am_cny_g", 0.0)
        result["gold_pm_cny_g"] = _last_good_shanghai.get("gold_pm_cny_g", 0.0)
        result["gold_spread_cny_g"] = _last_good_shanghai.get("gold_spread_cny_g", 0.0)
        result["gold_move_pct"] = _last_good_shanghai.get("gold_move_pct", 0.0)

        if not result["trade_date"]:
            result["trade_date"] = _last_good_shanghai.get("trade_date", "")

        result["ok_gold"] = True
        result["gold_mode"] = "last_good_fallback"
        result["error_gold"] = f"Using last good gold data because current fetch failed: {result['error_gold']}"

    if result["ok_gold"] or result["ok_silver"]:
        _last_good_shanghai = result.copy()

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

    if not gold_item:
        raise ValueError("ABC gold reference product not found")
    if not silver_item:
        raise ValueError("ABC silver reference product not found")

    gold_weight = parse_number(gold_item.get("itemShopPriceWeightOunces"))
    silver_weight = parse_number(silver_item.get("itemShopPriceWeightOunces"))

    if gold_weight <= 0:
        raise ValueError("ABC gold weight invalid")
    if silver_weight <= 0:
        raise ValueError("ABC silver weight invalid")

    gold_buy = parse_number(gold_item.get("purchasePrice"))
    gold_sell = parse_number(gold_item.get("sellPrice"))

    silver_buy = parse_number(silver_item.get("purchasePrice"))
    silver_sell = parse_number(silver_item.get("sellPrice"))

    return {
        "gold_buy_aud_oz": gold_buy / gold_weight,
        "gold_sell_aud_oz": gold_sell / gold_weight,
        "silver_buy_aud_oz": silver_buy / silver_weight,
        "silver_sell_aud_oz": silver_sell / silver_weight,
        "source": "ABC Bullion"
    }


# ======================================================
# THAILAND SILVER (BOWINS)
# ======================================================
def fetch_thai_silver():
    data = get_json(BOWINS_SILVER_API_URL)

    if isinstance(data, list):
        if not data:
            raise ValueError("Bowins silver response list is empty")
        row = data[0]
    elif isinstance(data, dict):
        row = data
    else:
        raise ValueError("Bowins silver response is not dict or list")

    return {
        "buy_thb": parse_number(row.get("buy")),
        "sell_thb": parse_number(row.get("sell")),
        "change_thb": parse_number(row.get("PREVIOUS_PRICE")),
        "update_time": row.get("created", ""),
        "round": row.get("no", ""),
        "spot_ref": parse_number(row.get("rate_spot")),
        "fx_ref": parse_number(row.get("rate_exchange")),
        "premium_ref": parse_number(row.get("rate_pmdc")),
        "source": "Bowins"
    }


# ======================================================
# BUILD PAYLOAD
# ======================================================
def build_payload():
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

    thai_data = safe_fetch(
        "thai_gold_api",
        lambda: get_json(THAI_GOLD_API_URL),
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
            "silver_sell_aud_oz": 0.0,
            "source": "ABC Bullion"
        },
        errors
    )

    shanghai = safe_fetch(
        "shanghai_benchmark",
        fetch_shanghai_benchmark_prices,
        {
            "trade_date": "",
            "gold_am_cny_g": 0.0,
            "gold_pm_cny_g": 0.0,
            "gold_spread_cny_g": 0.0,
            "gold_move_pct": 0.0,
            "silver_am_cny_g": 0.0,
            "silver_pm_cny_g": 0.0,
            "silver_effective_cny_g": 0.0,
            "silver_spread_cny_g": 0.0,
            "silver_move_pct": 0.0,
            "source": "SGE Benchmark",
            "ok_gold": False,
            "ok_silver": False,
            "error_gold": "fallback",
            "error_silver": "fallback",
            "gold_mode": "",
            "silver_mode": ""
        },
        errors
    )

    thai_silver = safe_fetch(
        "thai_silver_bowins",
        fetch_thai_silver,
        {
            "buy_thb": 0.0,
            "sell_thb": 0.0,
            "change_thb": 0.0,
            "update_time": "",
            "round": "",
            "spot_ref": 0.0,
            "fx_ref": 0.0,
            "premium_ref": 0.0,
            "source": "Bowins"
        },
        errors
    )

    gold_usd = parse_number(gold_data.get("price", 0))
    silver_usd = parse_number(silver_data.get("price", 0))

    rates = fx_data.get("conversion_rates", {})
    usd_aud = parse_number(rates.get("AUD"))
    usd_thb = parse_number(rates.get("THB"))
    usd_cny = parse_number(rates.get("CNY"))

    gold_spot_aud_oz = gold_usd * usd_aud
    silver_spot_aud_oz = silver_usd * usd_aud

    gold_premium_aud = abc["gold_sell_aud_oz"] - gold_spot_aud_oz
    silver_premium_aud = abc["silver_sell_aud_oz"] - silver_spot_aud_oz

    gold_buyback_discount_aud_oz = gold_spot_aud_oz - abc["gold_buy_aud_oz"]
    silver_buyback_discount_aud_oz = silver_spot_aud_oz - abc["silver_buy_aud_oz"]

    gold_ref_cny_g = (gold_usd * usd_cny) / OZ_TO_GRAMS if usd_cny > 0 else 0.0
    silver_ref_cny_g = (silver_usd * usd_cny) / OZ_TO_GRAMS if usd_cny > 0 else 0.0

    china_gold_value = shanghai["gold_pm_cny_g"] if shanghai["gold_pm_cny_g"] > 0 else shanghai["gold_am_cny_g"]
    china_silver_value = shanghai.get("silver_effective_cny_g", 0.0)

    thai_gold_bar_buy = parse_number(
        thai_data.get("response", {})
                 .get("price", {})
                 .get("gold_bar", {})
                 .get("buy", 0)
    )

    thai_gold_bar_sell = parse_number(
        thai_data.get("response", {})
                 .get("price", {})
                 .get("gold_bar", {})
                 .get("sell", 0)
    )

    gold_silver_ratio = round2(gold_usd / silver_usd) if silver_usd > 0 else 0.0

    status = "ok"
    if errors:
        status = "partial"

    payload = {
        "status": status,
        "api_version": APP_VERSION,
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
            "gold_cny_g": round2(china_gold_value),
            "silver_cny_g": round4(china_silver_value),

            "trade_date": shanghai["trade_date"],

            "gold_am_cny_g": round2(shanghai["gold_am_cny_g"]),
            "gold_pm_cny_g": round2(shanghai["gold_pm_cny_g"]),
            "gold_spread_cny_g": round2(shanghai["gold_spread_cny_g"]),
            "gold_move_pct": round2(shanghai["gold_move_pct"]),

            "silver_am_cny_g": round4(shanghai["silver_am_cny_g"]),
            "silver_pm_cny_g": round4(shanghai["silver_pm_cny_g"]),
            "silver_effective_cny_g": round4(shanghai["silver_effective_cny_g"]),
            "silver_spread_cny_g": round4(shanghai["silver_spread_cny_g"]),
            "silver_move_pct": round2(shanghai["silver_move_pct"]),

            "gold_ref_cny_g": round2(gold_ref_cny_g),
            "silver_ref_cny_g": round4(silver_ref_cny_g),

            "source": shanghai["source"]
        },

        "thailand": {
            "gold_bar_buy_thb": round2(thai_gold_bar_buy),
            "gold_bar_sell_thb": round2(thai_gold_bar_sell),
            "spread_thb": round2(thai_gold_bar_sell - thai_gold_bar_buy),

            "silver_buy_thb": round2(thai_silver["buy_thb"]),
            "silver_sell_thb": round2(thai_silver["sell_thb"]),
            "silver_change_thb": round2(thai_silver["change_thb"]),
            "silver_update_time": thai_silver["update_time"],
            "silver_round": thai_silver["round"],

            "silver_spot_ref": round4(thai_silver["spot_ref"]),
            "silver_fx_ref": round4(thai_silver["fx_ref"]),
            "silver_premium_ref": round4(thai_silver["premium_ref"]),
            "silver_source": thai_silver["source"]
        },

        "fx": {
            "usd_aud": round4(usd_aud),
            "usd_thb": round4(usd_thb),
            "usd_cny": round4(usd_cny)
        },

        "indicators": {
            "gold_silver_ratio": gold_silver_ratio
        },

        "debug": {
            "sge_gold_ok": shanghai["ok_gold"],
            "sge_silver_ok": shanghai["ok_silver"],
            "sge_gold_error": shanghai["error_gold"],
            "sge_silver_error": shanghai["error_silver"],
            "sge_gold_mode": shanghai.get("gold_mode", ""),
            "sge_silver_mode": shanghai.get("silver_mode", "")
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
