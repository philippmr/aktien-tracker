"""Periodic Transaction Reports (OGE Form 278-T) der Exekutive.

Quelle: Office of Government Ethics, "Officials' Individual Disclosures Search Collection"
(https://extapps2.oge.gov). Enthält Präsident, Vizepräsident, Minister, Fed-Gouverneure,
Behördenchefs u. a.

- Nur Berichte, die das OGE direkt als PDF verlinkt, werden geladen. Für viele andere
  Amtsträger muss man die Berichte einzeln per Formular anfordern; die fehlen hier.
- Es gibt zwei PDF-Formate: elektronisch erstellte (sauberer Text, oft mit Ticker) und
  eingescannte (z. B. die des Präsidenten) mit OCR-Text. Beim OCR-Text ist die Erkennung
  fehleranfälliger; Firmennamen werden über die SEC-Tickerliste zugeordnet.
"""
import datetime as dt
import difflib
import io
import json
import os
import re
import time

import pdfplumber
import requests

import config
from tracker import http

API = "https://extapps2.oge.gov/201/Presiden.nsf/API.xsp/v3/rest"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
PARSER_VERSION = 3

# Gesetzlich festgelegte Betragsspannen -> (untere, obere) Grenze
TIERS = [(1_001, 15_000), (15_001, 50_000), (50_001, 100_000), (100_001, 250_000),
         (250_001, 500_000), (500_001, 1_000_000), (1_000_001, 5_000_000),
         (5_000_001, 25_000_000), (25_000_001, 50_000_000), (50_000_001, 50_000_001)]

ROW = re.compile(
    r"^(?P<n>\d{1,3})\s+(?P<asset>.+?)\s+(?:See Endnote\s+)?"
    r"(?P<type>[A-Za-z|!1I]{0,3}(?:urch|rchas|ale\b|alo\b|ales\b|xchang)[A-Za-z]*)\s+"
    r"(?P<date>\S{4,12})\s+(?P<notif>[YVN][a-z0-9]{1,2})\s+(?P<amt>.*)$")


# --------------------------------------------------------------------------- Liste der Berichte
LIST_CACHE = os.path.join(config.CACHE_DIR, "oge", "reports.json")


def _get_page(params: dict) -> list[dict]:
    """Eine Seite der OGE-Liste; der Server bricht Verbindungen gelegentlich ab -> Wiederholen."""
    last = None
    for attempt in range(5):
        try:
            r = requests.get(API, params=params, timeout=90, headers={"User-Agent": config.USER_AGENT})
            r.raise_for_status()
            return r.json().get("data", [])
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise last


def _list_reports(since: dt.date, log=print) -> list[dict]:
    """Liste der direkt verlinkten 278-T-Berichte. Fällt der Abruf aus, wird die zuletzt
    erfolgreich geladene Liste verwendet."""
    try:
        reports = _list_reports_live(since)
    except Exception as e:
        if not os.path.exists(LIST_CACHE):
            raise
        log(f"  OGE: Liste gerade nicht abrufbar ({e}), nutze letzte gespeicherte Liste")
        with open(LIST_CACHE) as f:
            reports = json.load(f)
        for r in reports:
            r["filed"] = dt.date.fromisoformat(r["filed"])
        return [r for r in reports if r["filed"] >= since]
    os.makedirs(os.path.dirname(LIST_CACHE), exist_ok=True)
    with open(LIST_CACHE, "w") as f:
        json.dump(reports, f, default=str)
    return reports


def _list_reports_live(since: dt.date) -> list[dict]:
    cols = ["docDate", "title", "type", "name", "agency", "level"]
    out, start = [], 0
    while True:
        p = {"draw": 1, "start": start, "length": 100, "order[0][column]": 0, "order[0][dir]": "desc",
             "search[value]": "", "search[regex]": "false"}
        for i, c in enumerate(cols):
            p.update({f"columns[{i}][data]": c, f"columns[{i}][searchable]": "true",
                      f"columns[{i}][orderable]": "true", f"columns[{i}][search][regex]": "false",
                      f"columns[{i}][search][value]": "278 Transaction" if c == "type" else ""})
        data = _get_page(p)
        for d in data:
            m = re.search(r"href='([^']+\$FILE[^']+)'", d.get("type", ""))
            if m:  # nur direkt verlinkte PDFs
                out.append({"name": d["name"], "title": d.get("title", ""), "agency": d.get("agency", ""),
                            "filed": dt.date.fromisoformat(d["docDate"][:10]), "url": m.group(1)})
        start += 100
        if not data or data[-1]["docDate"][:10] < since.isoformat():
            break
    return [r for r in out if r["filed"] >= since]


# --------------------------------------------------------------------------- Ticker-Zuordnung
_names: dict[str, str] | None = None
_STOP = {"INC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "PLC", "HLDGS", "HOLDINGS", "GROUP",
         "THE", "CL", "CLASS", "A", "B", "COM", "COMMON", "STOCK", "SHS", "NEW", "DEL", "LLC", "SA", "NV", "AG"}


def _norm(s: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]", " ", s.upper())
    return " ".join(w for w in s.split() if w not in _STOP)


def _ticker_index() -> dict[str, str]:
    global _names
    if _names is None:
        raw = http.get(SEC_TICKERS, cache="oge", max_age_h=24 * 7)
        _names = {}
        if raw:
            for v in json.loads(raw).values():
                key = _norm(v["title"])
                _names.setdefault(key, v["ticker"])
                _names.setdefault(key.replace(" ", ""), v["ticker"])
    return _names


def match_ticker(asset: str) -> str | None:
    m = re.search(r"\(([A-Z][A-Z.\-]{0,5})\)", asset)
    if m:
        return m.group(1)
    if re.search(r"\b(ETF|FUND|TRUST|BOND|NOTE|TREAS|BILL|DUE|REV|LP|L\.P|LLC|%)\b", asset.upper()):
        return None  # Fonds, Anleihen, Beteiligungen: keine Einzelaktie
    idx = _ticker_index()
    key = _norm(asset)
    if not key:
        return None
    if key in idx:
        return idx[key]
    compact = key.replace(" ", "")
    if compact in idx:
        return idx[compact]
    # OCR-Fehler / abgekürzte Namen: ähnlichster Eintrag, nur bei hoher Übereinstimmung
    best = difflib.get_close_matches(compact, [k for k in idx if " " not in k and k[:1] == compact[:1]],
                                     n=1, cutoff=0.88)
    return idx[best[0]] if best else None


# --------------------------------------------------------------------------- PDF-Parser
def _ocr_digits(s: str) -> str:
    return s.translate(str.maketrans({"O": "0", "o": "0", "D": "0", "l": "1", "I": "1", "|": "1"}))


def _amount(s: str):
    s = s.strip()
    if re.search(r"over\s*\$?\s*50", s, re.I):
        return 50_000_001, None
    s = re.sub(r"^[Ss$]", "", _ocr_digits(s))          # EIN führendes $ (OCR liest es als S/s)
    first = re.split(r"[-•~]", s, maxsplit=1)[0]
    digits = re.sub(r"\D", "", first.replace("S", "5").replace("s", "5"))
    if not digits:
        return None, None
    for lo, hi in TIERS:
        if digits == str(lo):
            return lo, hi
    # unscharf: gleiche Stellenzahl und gleiche erste Ziffer
    cands = [(lo, hi) for lo, hi in TIERS if len(str(lo)) == len(digits) and str(lo)[0] == digits[0]]
    return cands[0] if len(cands) == 1 else (None, None)


def _date(s: str, fallback: dt.date) -> dt.date:
    s = _ocr_digits(s).replace("?", "2")
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        try:
            return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    # OCR: "8/312026" -> 8/31/2026, "8/1017026" -> 8/10/2026; Jahr aus dem Meldedatum
    m = re.match(r"(\d{1,2})/(\d{1,2})", re.sub(r"[^\d/]", "", s))
    if m:
        for year in (fallback.year, fallback.year - 1):
            try:
                d = dt.date(year, int(m.group(1)), int(m.group(2)))
                if d <= fallback:
                    return d
            except ValueError:
                continue
    return fallback


def _type(s: str) -> str | None:
    s = s.lower()
    if "urch" in s or "rchas" in s:
        return "Kauf"
    if "xchang" in s:
        return "Tausch"
    if s.endswith(("ale", "alo", "ales")):
        return "Verkauf"
    return None


def parse_pdf(raw: bytes, filed: dt.date) -> list[dict]:
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            lines = [l.strip() for pg in pdf.pages for l in (pg.extract_text() or "").splitlines()]
    except Exception:
        return []
    out = []
    for i, line in enumerate(lines):
        m = ROW.match(line)
        if not m:
            continue
        typ = _type(m.group("type"))
        if not typ:
            continue
        amt = m.group("amt")
        if re.search(r"-\s*$", amt) and i + 1 < len(lines):  # Obergrenze in der nächsten Zeile
            amt += " " + lines[i + 1]
        lo, hi = _amount(amt)
        asset = re.sub(r"\s+", " ", m.group("asset")).strip(" -")
        out.append({
            "asset": asset[:120],
            "type": typ,
            "tx_date": _date(m.group("date"), filed),
            "amount_low": lo,
            "amount_high": hi,
        })
    return out


# --------------------------------------------------------------------------- Abruf
def _parsed(rep: dict) -> list[dict]:
    path = http.cache_path(rep["url"] + f"#v{PARSER_VERSION}", "oge_parsed", ".json")
    if os.path.exists(path):
        with open(path) as f:
            rows = json.load(f)
        for r in rows:
            r["tx_date"] = dt.date.fromisoformat(r["tx_date"])
        return rows
    raw = http.get(rep["url"], cache="oge_pdf", suffix=".pdf")
    rows = parse_pdf(raw, rep["filed"]) if raw else []
    if raw is not None:
        with open(path, "w") as f:
            json.dump(rows, f, default=str)
    return rows


def _display_name(name: str) -> str:
    last, _, first = name.partition(",")
    first = first.strip().split(" ")[0] if first.strip() else ""
    return f"{first} {last.strip()}".strip()


def fetch(lookback_days: int, log=print) -> list[dict]:
    since = dt.date.today() - dt.timedelta(days=lookback_days)
    try:
        reports = _list_reports(since, log)
    except Exception as e:
        log(f"  OGE: Liste nicht abrufbar ({e})")
        return []
    trades, unmatched = [], 0
    for rep in reports:
        rows = _parsed(rep)
        for r in rows:
            ticker = match_ticker(r["asset"])
            if ticker is None:
                unmatched += 1
            trades.append({
                "source": "Exekutive",
                "person": _display_name(rep["name"]),
                "role": f"{rep['title']}, {rep['agency']}"[:90],
                "state_district": None,
                "filed": rep["filed"],
                "url": rep["url"],
                "filing_id": rep["url"].rsplit("/", 2)[-2] if "/$FILE/" in rep["url"] else rep["url"],
                "filing_size": len(rows),
                "ticker": ticker,
                "asset_type": "ST" if ticker else "OT",
                "owner": "",
                "description": "",
                **r,
            })
    n_stock = sum(1 for t in trades if t["ticker"])
    log(f"  OGE: {len(reports)} Berichte seit {since}, {len(trades)} Transaktionen, "
        f"davon {n_stock} Aktien zugeordnet ({unmatched} Anleihen/Fonds/Beteiligungen/unbekannt)")
    return trades
