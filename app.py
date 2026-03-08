def fetch_abc_prices():
    """
    Scrape ABC Bullion full product price list and calculate
    Australian reference premiums.

    Chosen reference products:
    - Gold: 1oz ABC Gold Cast Bar 9999
    - Silver: 10oz ABC Silver Cast Bar 9995
    """
    html = get_text(ABC_FULL_PRICE_URL, timeout=HTML_TIMEOUT)
    text = html_to_text(html)

    # Normalize whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)

    # Extract all spot values in order they appear
    spot_matches = re.findall(r"SPOT PRICE PER TROY OUNCE\s*\$([\d,]+\.\d+)", text, re.IGNORECASE)

    if len(spot_matches) < 2:
        raise ValueError("Could not parse ABC spot prices")

    # ABC page order is gold first, silver second
    gold_spot_aud_oz = parse_number(spot_matches[0])
    silver_spot_aud_oz = parse_number(spot_matches[1])

    gold_ref_product = "1oz ABC Gold Cast Bar 9999"
    silver_ref_product = "10oz ABC Silver Cast Bar 9995"

    gold_sell_total, gold_buy_total = extract_product_prices(text, gold_ref_product)
    silver_sell_total, silver_buy_total = extract_product_prices(text, silver_ref_product)

    gold_sell_aud_oz = gold_sell_total
    gold_buy_aud_oz = gold_buy_total

    silver_sell_aud_oz = silver_sell_total / 10.0
    silver_buy_aud_oz = silver_buy_total / 10.0

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
