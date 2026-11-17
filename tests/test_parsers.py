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


# --------------------------------------------------------------------------- Senat (Datei vom eigenen Rechner)
def test_senate_csv_roundtrip(tmp_path, monkeypatch):
    from tracker.sources import senate
    monkeypatch.setattr(senate, "CSV_FILE", str(tmp_path / "senate_trades.csv"))
    t = {"source": "Senat", "person": "Jane Doe", "role": "Senator/in", "state_district": None,
         "filed": dt.date.today(), "url": "https://efdsearch.senate.gov/x", "filing_id": "abc",
         "filing_size": 2, "description": "", "ticker": "MSFT", "asset_type": "ST",
         "asset": "Microsoft Corp", "owner": "Self", "type": "Kauf",
         "tx_date": dt.date.today() - dt.timedelta(days=5), "amount_low": 15001, "amount_high": 50000}
    assert senate.save_csv([t]) == 1
    assert senate.save_csv([t]) == 1          # gleicher Trade erneut -> kein Duplikat
    rows = senate.from_csv(30, log=lambda *a: None)
    assert len(rows) == 1 and rows[0]["ticker"] == "MSFT" and rows[0]["type"] == "Kauf"
    assert rows[0]["filed"] == dt.date.today()
    assert float(rows[0]["amount_low"]) == 15001


# --------------------------------------------------------------------------- SEC-Kennzahlen (historisch)
def test_secfacts_only_uses_known_reports():
    from tracker import secfacts
    data = {  # (start, end, val, filed)
        "revenue": [("2023-01-01", "2023-12-31", 100, "2024-02-15"), ("2022-01-01", "2022-12-31", 80, "2023-02-15"),
                    ("2024-01-01", "2024-12-31", 150, "2025-02-15"),
                    ("2024-04-01", "2024-06-30", 40, "2024-08-01"), ("2023-04-01", "2023-06-30", 30, "2023-08-01")],
        "net_income": [("2023-01-01", "2023-12-31", 20, "2024-02-15")],
        "op_income": [("2023-01-01", "2023-12-31", 25, "2024-02-15")],
        "liabilities": [(None, "2023-12-31", 50, "2024-02-15")], "equity": [(None, "2023-12-31", 100, "2024-02-15")],
        "ocf": [("2023-01-01", "2023-12-31", 30, "2024-02-15")], "capex": [("2023-01-01", "2023-12-31", 10, "2024-02-15")],
    }
    f = secfacts.at(data, dt.date(2024, 9, 1))      # Jahresbericht 2024 ist noch NICHT veröffentlicht
    assert f["rev_growth"] == pytest.approx(0.25)    # 100 vs 80
    assert f["rev_growth_q"] == pytest.approx(40 / 30 - 1)
    assert f["op_margin"] == pytest.approx(0.25) and f["fcf_positive"] and f["liab_to_equity"] == 0.5
    assert secfacts.at(data, dt.date(2025, 3, 1))["rev_growth"] == pytest.approx(0.5)  # jetzt bekannt


# --------------------------------------------------------------------------- Dividenden & Crash
def _divs(dates, amounts):
    return pd.Series(amounts, index=pd.to_datetime(dates))


def test_annual_dividend_quarterly_and_annual():
    from tracker import dividends
    q = _divs(["2023-03-01", "2023-06-01", "2023-09-01", "2023-12-01", "2024-03-01", "2024-06-01",
               "2024-09-01", "2024-12-01", "2025-03-01", "2025-06-01", "2025-09-01", "2025-12-03"], [1.0] * 8 + [1.1] * 4)
    assert dividends.annual_dividend(q, pd.Timestamp("2026-01-15")) == pytest.approx(4.4)
    a = _divs(["2023-05-10", "2024-05-08", "2025-05-12"], [9.0, 10.0, 11.5])
    assert dividends.annual_dividend(a, pd.Timestamp("2026-04-30")) == 11.5   # Zahlung 2026 noch nicht da -> keine Kürzung
    assert dividends.annual_dividend(a, pd.Timestamp("2027-08-01")) is None   # über ein Jahr nichts gezahlt
    assert dividends.growth_years(a) >= 2


def test_dividend_zone_and_cut():
    from tracker import dividends
    idx = pd.date_range("2020-01-01", "2026-06-30", freq="B")
    price = pd.Series(100.0, index=idx)
    price.iloc[-60:] = 75.0     # Kurs fällt um 25 % -> Rendite +33 %
    divs = _divs(pd.date_range("2019-03-01", "2026-06-01", freq="QS-MAR"), 1.0)
    r = dividends.analyse_one("X", price, divs, {"payoutRatio": 0.5})
    assert r["status"] == "zone" and r["rel"] == pytest.approx(4 / 75 / 0.04, rel=0.02)
    cut = divs.copy()
    cut.iloc[-4:] = 0.5
    assert dividends.analyse_one("X", price, cut, {"payoutRatio": 0.5})["status"] == "cut"
    assert dividends.analyse_one("X", price, divs, {"payoutRatio": 1.5, "freeCashflow": 1, "sharesOutstanding": 10,
                                                    "currency": "USD", "financialCurrency": "USD"})["status"] == "risk"


def test_crash_levels_once_and_reset():
    from tracker import crash
    meta = {}
    st = lambda dd: [{"name": "S&P 500", "drawdown": dd, "level": max([l for l in crash.LEVELS if -dd * 100 >= l], default=0)}]
    assert crash.new_alerts(st(-0.10), meta) == []
    assert len(crash.new_alerts(st(-0.16), meta)) == 1
    assert crash.new_alerts(st(-0.18), meta) == []          # gleiche Stufe nicht nochmal
    assert len(crash.new_alerts(st(-0.27), meta)) == 1
    crash.new_alerts(st(-0.03), meta)                       # Erholung -> zurückgesetzt
    assert len(crash.new_alerts(st(-0.16), meta)) == 1


def test_dividend_and_crash_mail_render():
    row = {"ticker": "PEP", "name": "PepsiCo", "yield": 0.04, "yield_avg5": 0.029, "rel": 1.37, "growth_years": 27,
           "payout": 0.73, "fcf_coverage": 1.1, "price": 135.0, "currency": "USD", "fair_price": 185.0, "from_high": -0.2,
           "status": "zone"}
    df = pd.DataFrame([row])
    subj, body = newsletter.dividend_zone(df, df, "https://example.org")
    assert "PEP" in subj and "+37 %" in body
    s2, b2 = newsletter.crash_mail([{"name": "S&P 500", "drawdown": -0.26, "level": 25}],
                                   [{"name": "S&P 500", "drawdown": -0.26}])
    assert "-26" in s2 or "−26" in s2


def test_big_buys_only():
    df = pd.DataFrame({"type": ["Kauf", "Kauf", "Verkauf", "Kauf"],
                       "amount_low": [1001, 100001, 1001, 1000001]})
    out = scoring.big_buys_only(df, 100_000)
    assert list(out["amount_low"]) == [100001, 1001, 1000001]
