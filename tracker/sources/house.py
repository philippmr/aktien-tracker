"""Periodic Transaction Reports (PTR) des US-Repräsentantenhauses.

Quelle: https://disclosures-clerk.house.gov
- Jahres-ZIP mit XML-Index aller Meldungen (FilingType "P" = PTR)
- Pro Meldung ein PDF. Elektronisch eingereichte PDFs enthalten Text und werden geparst.
  Eingescannte Papiermeldungen (DocID beginnt meist mit 8/9) werden übersprungen.
"""
import datetime as dt
import io
import json
import os
import re
import xml.etree.ElementTree as ET
import zipfile

import pdfplumber

from tracker import http

BASE = "https://disclosures-clerk.house.gov/public_disc"

TX_LINE = re.compile(
    r"^(?:(?P<owner>SP|JT|DC)\s+)?(?P<asset>.*?)\s*"
    r"(?P<type>P|S \(partial\)|S|E)\s+"
    r"(?P<date>\d{2}/\d{2}/\d{4})\s+(?P<notif>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<amount>\$[\d,]+.*)$"
)
STATUS_LINE = re.compile(r"^F\s*S\s*:")
DESC_LINE = re.compile(r"^D\s*:\s*")          # Beschreibung, z. B. "Purchased 20 call options ..."
DESC_STOP = re.compile(r"CERTIFY|Digitally|knowledge and belief|asset-type-codes|^I\s+[A-Z]\s|^Yes|^L\s*:|^\*")
SUBFIELD = re.compile(r"^[A-Z]\s*[A-Z]?\s*:")  # andere Unterfelder (S O:, C:, ...)
TICKER = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)\s*\[(\w{2})\]")
ASSET_TYPE = re.compile(r"\[(\w{2})\]")
MONEY = re.compile(r"\$([\d,]+)")
TYPE_MAP = {"P": "Kauf", "S": "Verkauf", "S (partial)": "Teilverkauf", "E": "Tausch"}


def _index(year: int) -> list[dict]:
    raw = http.get(f"{BASE}/financial-pdfs/{year}FD.zip", cache="house", max_age_h=6)
    if not raw:
        return []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        xml = z.read(f"{year}FD.xml")
    out = []
    for m in ET.fromstring(xml):
        if m.findtext("FilingType") != "P":
            continue
        name = clean_name(m.findtext("First"), m.findtext("Last"))
        out.append({
            "name": name,
            "district": m.findtext("StateDst") or "",
            "filed": dt.datetime.strptime(m.findtext("FilingDate"), "%m/%d/%Y").date(),
            "doc_id": m.findtext("DocID"),
            "year": int(m.findtext("Year")),
        })
    return out


def _parse_pdf(raw: bytes) -> list[dict]:
    try:
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:
        return []
    text = text.replace("\x00", "")
    lines = [l.strip() for l in text.splitlines()]

    rows, cur, last = [], None, None
    for line in lines:
        m = TX_LINE.match(line)
        if m:
            if cur:
                rows.append(cur)
            cur = {**m.groupdict(), "extra": [], "desc": ""}
            last = None
            continue
        if cur is None:
            # Zeilen nach dem Statusfeld: Beschreibung ("D: ...") gehört zur letzten Transaktion
            if last is not None and DESC_LINE.match(line):
                last["desc"] = DESC_LINE.sub("", line).strip()
            elif last is not None and last["desc"] and not SUBFIELD.match(line) \
                    and len(last["desc"]) < 250 and not DESC_STOP.search(line):
                last["desc"] += " " + line
            elif last is not None and last["desc"]:
                last = None  # Beschreibung abgeschlossen
            continue
        if (STATUS_LINE.match(line) or line.startswith("* For the complete")
                or line.startswith("ID Owner Asset")):
            rows.append(cur)
            last, cur = cur, None
        else:
            cur["extra"].append(line)
    if cur:
        rows.append(cur)

    out = []
    for r in rows:
        block = " ".join([r["asset"], *r["extra"], r["amount"]])
        full_asset = " ".join([r["asset"], *r["extra"]])
        t = TICKER.search(full_asset)
        at = ASSET_TYPE.search(full_asset)
        amounts = [int(a.replace(",", "")) for a in MONEY.findall(r["amount"] + " " + " ".join(r["extra"]))]
        amounts = [a for a in amounts if a >= 1000]
        lo = amounts[0] if amounts else None
        hi = amounts[1] if len(amounts) > 1 else lo
        name = re.sub(r"\s*\[\w{2}\]", "", full_asset)
        name = re.sub(r"\$[\d,]+", "", name).strip(" -")
        out.append({
            "ticker": t.group(1) if t else None,
            "asset_type": at.group(1) if at else None,  # ST=Aktie, OP=Option, GS=Staatsanleihe ...
            "asset": name[:120],
            "owner": {"SP": "Ehepartner", "JT": "Gemeinsam", "DC": "Kind"}.get(r["owner"] or "", "Selbst"),
            "type": TYPE_MAP.get(r["type"], r["type"]),
            "tx_date": dt.datetime.strptime(r["date"], "%m/%d/%Y").date(),
            "amount_low": lo,
            "amount_high": hi,
            "description": r["desc"][:300],
            "_raw": block[:300],
        })
    return out


PARSER_VERSION = 3  # erhöhen, wenn sich _parse_pdf ändert -> Cache wird neu aufgebaut


SCAN_VERSION = 2
SCAN_MAX_AGE_DAYS = 400   # ältere Papiermeldungen nicht per OCR auslesen (spart Zeit)
_SCAN_LINE = re.compile(r"^\W*(?:JT|SP|DC)?\W*(.+)$")
_SCAN_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")


def _parse_scan(raw: bytes, filed: dt.date) -> list[dict]:
    """Eingescannte Papiermeldung per OCR (Tesseract) lesen.

    Die Formulare haben Ankreuzfelder für Kauf/Verkauf und Betrag, die OCR nicht zuverlässig
    erkennt. Deshalb werden nur die Aktien erfasst, mit Art "Unklar (Scan)". Sie erscheinen im
    Dashboard, zählen aber nicht für Score oder Mail."""
    try:
        import pypdfium2 as pdfium
        import pytesseract
    except ImportError:
        return []
    from tracker.sources.oge import match_ticker
    out, seen = [], set()
    try:
        pdf = pdfium.PdfDocument(raw)
        for i in range(min(len(pdf), 6)):
            text = pytesseract.image_to_string(pdf[i].render(scale=300 / 72).to_pil())
            for line in text.splitlines():
                m = _SCAN_LINE.match(line.strip())
                cand = re.sub(r"[|\[\]]", " ", m.group(1) if m else "").strip()
                if len(cand) < 4 or "example" in cand.lower() or "mega corp" in cand.lower():
                    continue
                if not re.search(r"\b(INC|CORP|CO|LTD|PLC|HLDGS|HOLDINGS|GROUP|CLASS|COMMON|ETF|TR)\b", cand.upper()):
                    continue
                cand = re.sub(r"\s+(\$|\d{1,2}/).*$", "", cand)     # Datums-/Betragsspalten abschneiden
                cand = re.sub(r"\bING\b", "INC", cand)              # typischer OCR-Fehler
                ticker = match_ticker(cand)
                if not ticker or ticker in seen:
                    continue
                seen.add(ticker)
                d = _SCAN_DATE.search(line)
                tx = filed
                if d:
                    y = int(d.group(3)); y = y + 2000 if y < 100 else y
                    try:
                        tx = min(dt.date(y, int(d.group(1)), int(d.group(2))), filed)
                    except ValueError:
                        pass
                out.append({"ticker": ticker, "asset_type": "ST", "asset": cand[:120], "owner": "",
                            "type": "Unklar (Scan)", "tx_date": tx, "amount_low": None, "amount_high": None,
                            "description": "Papiermeldung, per Texterkennung gelesen – Art und Betrag im PDF prüfen"})
    except Exception:
        return out
    return out


def _parsed(url: str, filed: dt.date | None = None) -> list[dict]:
    """PDF laden und parsen; Ergebnis als JSON cachen (PDFs ändern sich nicht)."""
    path = http.cache_path(url + f"#v{PARSER_VERSION}", "house_parsed", ".json")
    if os.path.exists(path):
        with open(path) as f:
            rows = json.load(f)
    else:
        raw = http.get(url, cache="house_pdf", suffix=".pdf")
        rows = _parse_pdf(raw) if raw else []
        if raw is not None:
            with open(path, "w") as f:
                json.dump(rows, f, default=str)
    if not rows and filed and (dt.date.today() - filed).days <= SCAN_MAX_AGE_DAYS:
        scan_path = http.cache_path(url + f"#scan{SCAN_VERSION}", "house_parsed", ".json")
        if os.path.exists(scan_path):
            with open(scan_path) as f:
                rows = json.load(f)
        else:
            raw = http.get(url, cache="house_pdf", suffix=".pdf")
            rows = _parse_scan(raw, filed) if raw else []
            if raw is not None and _ocr_available():
                with open(scan_path, "w") as f:
                    json.dump(rows, f, default=str)
    for r in rows:
        if isinstance(r["tx_date"], str):
            r["tx_date"] = dt.date.fromisoformat(r["tx_date"])
    return rows


def _ocr_available() -> bool:
    import shutil
    return shutil.which("tesseract") is not None


TITLES = {"mr", "mrs", "ms", "dr", "hon", "jr", "sr", "ii", "iii", "iv"}


def clean_name(first: str, last: str) -> str:
    """'John J Mr' + 'McGuire' -> 'John McGuire'; 'Scott Scott' + 'Franklin' -> 'Scott Franklin'.
    Einheitliche Namen sind wichtig, damit die Trefferquote je Politiker nicht zerfällt."""
    firsts = [w for w in re.split(r"[\s,]+", first or "") if w and w.lower().strip(".") not in TITLES
              and len(w.strip(".")) > 1]
    lasts = [w for w in re.split(r"[\s,]+", last or "") if w and w.lower().strip(".") not in TITLES]
    return " ".join(([firsts[0]] if firsts else []) + lasts).strip()


def fetch(lookback_days: int, log=print) -> list[dict]:
    today = dt.date.today()
    since = today - dt.timedelta(days=lookback_days)
    filings = []
    for y in range(since.year, today.year + 1):
        filings += _index(y)
    filings = [f for f in filings if f["filed"] >= since]
    log(f"  House: {len(filings)} PTR-Meldungen seit {since}")

    trades, skipped, scanned = [], 0, 0
    for f in filings:
        url = f"{BASE}/ptr-pdfs/{f['year']}/{f['doc_id']}.pdf"
        rows = _parsed(url, f["filed"])
        if not rows:
            skipped += 1  # Scan / Papiermeldung oder Fehler
            continue
        if rows[0].get("type") == "Unklar (Scan)":
            scanned += 1
        for r in rows:
            trades.append({
                "filing_id": f["doc_id"],
                "filing_size": len(rows),  # viele Trades in einer Meldung -> meist Vermögensverwalter
                "state_district": f["district"],
                "source": "House",
                "person": f["name"],
                "role": f"Abgeordnete/r {f['district']}",
                "filed": f["filed"],
                "url": url,
                **r,
            })
    log(f"  House: {len(trades)} Transaktionen geparst, davon {scanned} Papiermeldungen per OCR; "
        f"{skipped} Meldungen nicht lesbar")
    return trades
