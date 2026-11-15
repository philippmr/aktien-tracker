"""Periodic Transaction Reports des US-Senats (efdsearch.senate.gov).

ACHTUNG: Die Seite verlangt vor jedem Zugriff die Zustimmung zu Nutzungsbedingungen.
Dieses Modul läuft nur, wenn du in config.py SENATE_ACCEPT_TERMS = True setzt.
Damit stimmst DU diesen Bedingungen zu, das Skript klickt sie nur in deinem Namen.

Hinweis: Dieses Modul konnte bei der Entwicklung nicht gegen die Live-Seite getestet werden.
Elektronische Meldungen sind HTML-Tabellen, Papiermeldungen (Scans) werden übersprungen.
"""
import datetime as dt
import io
import os
import re
import time

import requests

import config
from tracker import http

BASE = "https://efdsearch.senate.gov"
TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,6}$")
MONEY = re.compile(r"\$([\d,]+)")


def fetch(lookback_days: int, log=print) -> list[dict]:
    if not config.SENATE_ACCEPT_TERMS:
        log("  Senat: übersprungen (SENATE_ACCEPT_TERMS = False in config.py)")
        return []
    import pandas as pd  # nur hier gebraucht

    s = requests.Session()
    s.headers["User-Agent"] = config.USER_AGENT
    r = s.get(f"{BASE}/search/home/", timeout=30)
    tok = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r.text)
    if not tok:
        log("  Senat: Startseite hat sich geändert, kein CSRF-Token gefunden")
        return []
    s.post(f"{BASE}/search/home/", timeout=30,
           data={"prohibition_agreement": "1", "csrfmiddlewaretoken": tok.group(1)},
           headers={"Referer": f"{BASE}/search/home/"})
    csrf = s.cookies.get("csrftoken")

    since = dt.date.today() - dt.timedelta(days=lookback_days)
    reports, start = [], 0
    while True:
        r = s.post(f"{BASE}/search/report/data/", timeout=30, data={
            "start": start, "length": 100, "report_types": "[11]", "filer_types": "[]",
            "submitted_start_date": f"{since:%m/%d/%Y} 00:00:00", "submitted_end_date": "",
            "candidate_state": "", "senator_state": "", "office_id": "",
            "first_name": "", "last_name": "", "csrfmiddlewaretoken": csrf,
        }, headers={"Referer": f"{BASE}/search/", "X-CSRFToken": csrf})
        if r.status_code != 200:
            log(f"  Senat: Suche antwortet mit HTTP {r.status_code}. Die Seite blockt manche Netze "
                "(z. B. Cloud-Server); von einem normalen Internetanschluss aus sollte es gehen.")
            return []
        data = r.json().get("data", [])
        reports += data
        if len(data) < 100:
            break
        start += 100

    trades, skipped = [], 0
    for first, last, _full, link_html, filed in reports:
        m = re.search(r'href="([^"]+)"', link_html)
        if not m or "/ptr/" not in m.group(1):
            skipped += 1  # Papiermeldung
            continue
        url = BASE + m.group(1)
        cache = http.cache_path(url, "senate_ptr", ".html")
        if os.path.exists(cache):
            html = open(cache, encoding="utf-8").read()
        else:
            time.sleep(0.5)
            resp = s.get(url, timeout=30)
            if resp.status_code != 200:
                skipped += 1
                continue
            html = resp.text
            with open(cache, "w", encoding="utf-8") as f:
                f.write(html)
        try:
            tables = pd.read_html(io.StringIO(html))
        except ValueError:
            skipped += 1
            continue
        t = tables[0]
        t.columns = [str(c).strip().lower() for c in t.columns]
        for _, row in t.iterrows():
            ticker = str(row.get("ticker", "")).strip()
            amounts = [int(a.replace(",", "")) for a in MONEY.findall(str(row.get("amount", "")))]
            typ = str(row.get("type", ""))
            trades.append({
                "source": "Senat",
                "person": f"{first} {last}".strip(),
                "role": "Senator/in",
                "state_district": None,
                "filing_id": url.rstrip("/").rsplit("/", 1)[-1],
                "filing_size": len(t),
                "description": str(row.get("comment", "")) if str(row.get("comment", "")) not in ("--", "nan") else "",
                "filed": dt.datetime.strptime(filed, "%m/%d/%Y").date(),
                "url": url,
                "ticker": ticker if TICKER.match(ticker) else None,
                "asset_type": ("OP" if "option" in str(row.get("asset type", "")).lower()
                               else "ST" if "stock" in str(row.get("asset type", "")).lower() else "OT"),
                "asset": str(row.get("asset name", ""))[:120],
                "owner": str(row.get("owner", "")),
                "type": "Kauf" if "purchase" in typ.lower() else
                        ("Teilverkauf" if "partial" in typ.lower() else
                         ("Verkauf" if "sale" in typ.lower() else typ)),
                "tx_date": dt.datetime.strptime(str(row.get("transaction date")), "%m/%d/%Y").date(),
                "amount_low": amounts[0] if amounts else None,
                "amount_high": amounts[-1] if amounts else None,
            })
    log(f"  Senat: {len(reports)} Meldungen, {len(trades)} Transaktionen, {skipped} Scans übersprungen")
    return trades
