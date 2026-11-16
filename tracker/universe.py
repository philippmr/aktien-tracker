"""Aktien-Universum für den Chancen-Screener: USA + Europa ab einer Mindest-Marktkapitalisierung.

Quelle: Yahoo-Finance-Screener (über yfinance). Nur Heimatbörsen in Landeswährung, damit
Doppellistungen (z. B. US-Aktien in Frankfurt) nicht doppelt auftauchen.
"""
import json
import os
import re
import time

import config

MIN_CAP = 2e9
# Region -> (zulässige Börsen, zulässige Bilanzwährungen). Reihenfolge = Priorität bei Doppellistungen.
REGIONS = {
    "us": ({"NYQ", "NMS", "NGM", "NCM", "ASE"}, None),
    # Heimatbörsen zuerst, Xetra und Wien zuletzt: dort sind viele Auslandsfirmen in EUR gelistet
    "fr": ({"PAR"}, {"EUR"}),
    "nl": ({"AMS"}, {"EUR"}),
    "es": ({"MCE"}, {"EUR"}),
    "it": ({"MIL"}, {"EUR"}),
    "be": ({"BRU"}, {"EUR"}),
    "fi": ({"HEL"}, {"EUR"}),
    "ch": ({"EBS"}, {"CHF"}),
    "se": ({"STO"}, {"SEK"}),
    "dk": ({"CPH"}, {"DKK"}),
    "no": ({"OSL"}, {"NOK"}),
    "gb": ({"LSE"}, {"GBP"}),
    "de": ({"GER"}, {"EUR"}),
    "at": ({"VIE"}, {"EUR"}),
}
FIELDS = ["symbol", "shortName", "longName", "exchange", "region", "currency", "financialCurrency", "marketCap",
          "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "forwardPE", "priceToBook", "epsTrailingTwelveMonths",
          "epsForward", "averageAnalystRating", "regularMarketPrice"]


def _norm(name: str) -> str:
    n = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    n = re.sub(r"\b(inc|corp|corporation|ag|se|sa|plc|nv|ltd|group|holding|holdings|the|co|asa|ab|oyj|spa)\b", " ", n)
    return " ".join(n.split())


def _fetch_region(region: str) -> list[dict]:
    import yfinance as yf
    from yfinance import EquityQuery
    out, off = [], 0
    while True:
        q = EquityQuery("and", [EquityQuery("eq", ["region", region]),
                                EquityQuery("gte", ["intradaymarketcap", MIN_CAP])])
        for attempt in range(4):
            try:
                r = yf.screen(q, size=250, offset=off, sortField="intradaymarketcap", sortAsc=False)
                break
            except Exception:
                time.sleep(3 * (attempt + 1))
        else:
            break
        out += r.get("quotes", [])
        off += 250
        if off >= r.get("total", 0) or not r.get("quotes"):
            break
    return out


def load(max_age_h: float = 24, log=print) -> list[dict]:
    """Liste der Aktien mit Basisdaten. Wird einmal am Tag neu geladen."""
    path = os.path.join(config.CACHE_DIR, "universe.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_h * 3600:
        with open(path) as f:
            return json.load(f)
    seen_names, out = set(), []
    for region, (exchanges, currencies) in REGIONS.items():
        rows = _fetch_region(region)
        kept = 0
        for x in rows:
            if x.get("quoteType", "EQUITY") != "EQUITY" or x.get("exchange") not in exchanges:
                continue
            if currencies and x.get("financialCurrency") not in currencies:
                continue  # Auslandsfirma an dieser Börse
            name = _norm(x.get("longName") or x.get("shortName"))
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            out.append({k: x.get(k) for k in FIELDS} | {"region": region})
            kept += 1
        log(f"    {region.upper()}: {kept} Aktien")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f)
    return out
