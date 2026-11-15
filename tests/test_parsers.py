"""Tests für die Parser. Laufen vor jedem Report im Workflow: Ändert eine Behörde ihr
PDF-Format, schlägt ein Test fehl und du bekommst eine Fehler-Mail statt stiller Datenlücken.

Starten:  python -m pytest -q
"""
import datetime as dt
import os

import pandas as pd
import pytest

from tracker import newsletter, scoring
from tracker.sources import house, oge

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


# --------------------------------------------------------------------------- House
def test_house_ptr_electronic():
    rows = house._parse_pdf(_read("house_ptr_taylor.pdf"))
    assert len(rows) == 19
    tickers = {r["ticker"] for r in rows}
    assert {"GOOGL", "AVGO", "LRCX", "T", "V"} <= tickers
    googl = next(r for r in rows if r["ticker"] == "GOOGL")
    assert googl["type"] == "Kauf"
    assert googl["tx_date"] == dt.date(2026, 11, 3)
    assert (googl["amount_low"], googl["amount_high"]) == (1001, 15000)
    assert all(r["asset_type"] == "ST" for r in rows)


def test_house_dividend_reinvestment_description():
    rows = house._parse_pdf(_read("house_ptr_doggett.pdf"))
    assert rows[0]["ticker"] == "KO"
    assert "reinvestment" in rows[0]["description"].lower()
    assert scoring.is_reinvest(rows[0])


@pytest.mark.parametrize("first,last,expected", [
    ("John J Mr", "McGuire", "John McGuire"),
    ("Scott Scott", "Franklin", "Scott Franklin"),
    ("Mark Dr", "Green", "Mark Green"),
    ("Rudy C.", "Yakym", "Rudy Yakym"),
])
def test_house_clean_name(first, last, expected):
    assert house.clean_name(first, last) == expected


# --------------------------------------------------------------------------- OGE (Exekutive)
def test_oge_electronic_report():
    rows = oge.parse_pdf(_read("oge_kupor.pdf"), dt.date(2026, 10, 24))
    assert [(r["type"], r["amount_low"]) for r in rows] == [("Verkauf", 250001), ("Verkauf", 1000001)]
    assert oge.match_ticker(rows[0]["asset"]) == "ABNB"   # Ticker steht in Klammern
    assert oge.match_ticker(rows[1]["asset"]) == "OKTA"


def test_oge_scanned_report_ocr():
    rows = oge.parse_pdf(_read("oge_trump_scan.pdf"), dt.date(2026, 10, 8))
    msft = next(r for r in rows if r["asset"].startswith("MICROSOFT"))
    assert msft["type"] == "Kauf"
    assert msft["tx_date"] == dt.date(2026, 8, 10)
    assert (msft["amount_low"], msft["amount_high"]) == (1_000_001, 5_000_000)


def test_oge_name_to_ticker_needs_sec_list():
    if not oge._ticker_index():
        pytest.skip("SEC-Tickerliste nicht erreichbar")
    assert oge.match_ticker("MICROSOFT CORP") == "MSFT"
    assert oge.match_ticker("NVIDIA CORP") == "NVDA"
    assert oge.match_ticker("ISHARES U.S. TREASURY BOND ETF") is None   # Fonds, keine Aktie
    assert oge.match_ticker("TEXAS TRANSN COMMN 5% DUE 08/15/40") is None  # Anleihe


@pytest.mark.parametrize("raw,expected", [
    ("$1,000,001 - $5,000,000", (1_000_001, 5_000_000)),
    ("S1 ODO 001 -$5,000.DOO", (1_000_001, 5_000_000)),   # OCR-Fehler
    ("sso 001 -$100 000", (50_001, 100_000)),
    ("$15.001 -SS0.000", (15_001, 50_000)),
    ("Over $50,000,000", (50_000_001, None)),
])
def test_oge_amount_ocr(raw, expected):
    assert oge._amount(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("8/31/2026", dt.date(2026, 8, 31)),
    ("8/312026", dt.date(2026, 8, 31)),
    ("8/10/?026", dt.date(2026, 8, 10)),
])
def test_oge_date_ocr(raw, expected):
    assert oge._date(raw, dt.date(2026, 10, 8)) == expected


# --------------------------------------------------------------------------- Scoring & Mail
def test_fundamental_score_range():
    strong = {"forwardPE": 11, "trailingPegRatio": 0.8, "enterpriseToEbitda": 8, "returnOnEquity": 0.3,
              "operatingMargins": 0.3, "freeCashflow": 1e9, "grossMargins": 0.6, "revenueGrowth": 0.25,
              "earningsGrowth": 0.3, "debtToEquity": 20, "currentRatio": 2, "totalCash": 2e9, "totalDebt": 1e9}
    weak = {"forwardPE": -5, "returnOnEquity": -0.2, "operatingMargins": -0.1, "freeCashflow": -1e8,
            "revenueGrowth": -0.1, "earningsGrowth": -0.5, "debtToEquity": 400, "currentRatio": 0.5,
            "totalCash": 1e8, "totalDebt": 1e9}
    assert scoring.fundamental_score(strong)[0] == pytest.approx(100)
    assert scoring.fundamental_score(weak)[0] < 20


def test_newsletter_renders():
    buys = pd.DataFrame([{
        "ticker": "NVDA", "person": "Jane Doe", "source": "House", "role": "Abgeordnete/r CA01",
        "asset": "NVIDIA Corp (NVDA)", "asset_type": "ST", "type": "Kauf", "tx_date": dt.date(2026, 11, 1),
        "amount_low": 15001, "amount_high": 50000, "_score": 70}])
    info = lambda t: {"score": 70, "insider_score": 60, "fundamental_score": 85, "name": "NVIDIA",
                      "sector": "Technology", "reasons": ["Testgrund"], "n_buyers": 1}
    subject, body = newsletter.build(buys, buys.iloc[0:0], info, ["NVDA"])
    assert "NVDA" in subject and "Testgrund" in body and "15–50 Tsd $" in body
