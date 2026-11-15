"""Staatsaufträge (USAspending.gov): Hat eine Firma kurz nach einem Politikerkauf einen
größeren Bundesauftrag bekommen? Das wäre ein Indiz für Insiderwissen.

Wird als Hinweis angezeigt, gibt aber (noch) keine Punkte, weil es nicht im Backtest geprüft ist.
"""
import datetime as dt
import json
import os
import re
import time

import requests

import config

API = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
MIN_AMOUNT = 10_000_000
_STOP = {"INC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "PLC", "HOLDINGS", "HLDGS", "GROUP",
         "THE", "CLASS", "A", "B", "N.V.", "NV", "SA", "AG", "SE", "LLC", "LP", "&"}


def _key_words(name: str) -> list[str]:
    words = [w for w in re.sub(r"[^A-Z0-9& ]", " ", name.upper()).split() if w not in _STOP]
    return words[:2]


def awards_after(ticker: str, company: str, since: dt.date) -> list[dict]:
    """Aufträge an die Firma mit Beginn ab `since` und mind. MIN_AMOUNT $. Ergebnis 24 h gecacht."""
    words = _key_words(company or "")
    if not words or len(" ".join(words)) < 4:
        return []
    d = os.path.join(config.CACHE_DIR, "contracts")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{ticker}_{since}.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 24 * 3600:
        with open(path) as f:
            return json.load(f)
    body = {"filters": {"recipient_search_text": [" ".join(words)],
                        "time_period": [{"start_date": since.isoformat(), "end_date": dt.date.today().isoformat()}],
                        "award_type_codes": ["A", "B", "C", "D"]},
            "fields": ["Award ID", "Recipient Name", "Award Amount", "Start Date", "Awarding Agency", "Description"],
            "sort": "Award Amount", "order": "desc", "limit": 50, "page": 1}
    try:
        r = requests.post(API, json=body, timeout=60, headers={"User-Agent": config.USER_AGENT})
        r.raise_for_status()
        results = r.json().get("results", [])
    except (requests.RequestException, ValueError):
        return []  # Quelle gerade nicht erreichbar -> beim nächsten Lauf erneut
    out = []
    for a in results:
        rec = a.get("Recipient Name") or ""
        start = a.get("Start Date") or ""
        if not rec.upper().startswith(words[0]) or start < since.isoformat():
            continue  # anderer Empfänger mit ähnlichem Namen bzw. alter Vertrag
        if (a.get("Award Amount") or 0) < MIN_AMOUNT:
            continue
        out.append({"amount": a["Award Amount"], "start": start, "agency": a.get("Awarding Agency"),
                    "recipient": rec, "description": (a.get("Description") or "")[:140]})
    with open(path, "w") as f:
        json.dump(out, f)
    return out


def annotate(ranking, trades, top_n: int = 80, log=print):
    """Ergänzt die Top-Aktien im Ranking um Aufträge seit dem ersten Politikerkauf."""
    if ranking.empty:
        return ranking
    ranking = ranking.copy()
    ranking["contracts"] = None
    hits = 0
    for i, r in ranking.head(top_n).iterrows():
        buys = trades[(trades["ticker"] == r["ticker"]) & (trades["type"] == "Kauf")]
        if buys.empty:
            continue
        aw = awards_after(r["ticker"], r["name"], buys["tx_date"].min())
        if aw:
            total = sum(a["amount"] for a in aw)
            agencies = ", ".join(dict.fromkeys(a["agency"] for a in aw if a["agency"]))[:80]
            ranking.at[i, "contracts"] = f"{len(aw)} Aufträge, {total / 1e6:,.0f} Mio $ ({agencies})".replace(",", ".")
            ranking.at[i, "reasons"] = list(r["reasons"]) + [
                f"Hinweis: Nach dem Kauf erhielt die Firma Staatsaufträge über {total / 1e6:,.0f} Mio $".replace(",", ".")]
            hits += 1
    log(f"  Staatsaufträge: {hits} der Top-{top_n} Aktien mit neuen Aufträgen ≥ {MIN_AMOUNT / 1e6:.0f} Mio $ nach dem Kauf")
    return ranking
