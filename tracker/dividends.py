"""Dividenden-Kaufzone: solide Dividendenaktien, die gerade günstiger sind als üblich.

Idee (Helmut Jonen / Dividenden-Value): Qualitäts-Dividendenzahler kaufen, wenn ihre Dividendenrendite
deutlich ÜBER dem eigenen 5-Jahres-Schnitt liegt – die Aktie ist dann günstiger als sonst –,
aber nur, solange die Dividende sicher aussieht (nicht gekürzt, vom Gewinn bzw. Free Cashflow gedeckt).
Kein Renditeversprechen: Das ist eine Ideenliste für ein ruhiges Einkommens-Depot.
"""
import datetime as dt
import os
import time

import numpy as np
import pandas as pd
import yfinance as yf

import config

# Dividenden-Aristokraten / Basiskonsum USA, Qualitäts-Dividendenzahler Europa
UNIVERSE = [
    # USA – Basiskonsum
    "KO", "PEP", "PG", "CL", "KMB", "CLX", "CHD", "GIS", "SJM", "KHC", "HRL", "MKC", "SYY", "MO", "PM", "KVUE",
    "WMT", "TGT", "COST",
    # USA – Gesundheit
    "JNJ", "ABBV", "ABT", "MDT", "BDX", "CAH", "MRK", "PFE",
    # USA – Industrie/Material
    "MMM", "ITW", "EMR", "DOV", "GPC", "SWK", "SHW", "APD", "ECL", "PPG", "NUE", "LIN", "CAT", "GD", "NDSN",
    "PNR", "CTAS", "ADP", "UNP",
    # USA – Finanzen
    "AFL", "CB", "CINF", "TROW", "BEN", "BRO", "JPM",
    # USA – Konsum, Energie, Versorger, Immobilien, Telekom, Tech
    "MCD", "LOW", "HD", "XOM", "CVX", "ED", "NEE", "ATO", "SO", "DUK", "O", "ESS", "FRT", "VZ", "IBM", "MSFT", "AAPL",
    "TXN", "CSCO",
    # Deutschland
    "ALV.DE", "MUV2.DE", "HNR1.DE", "BAS.DE", "MBG.DE", "SIE.DE", "DTE.DE", "EOAN.DE", "HEN3.DE", "BEI.DE",
    "DHL.DE", "SAP.DE", "BMW.DE", "DBK.DE", "BAYN.DE",
    # Europa
    "NESN.SW", "NOVN.SW", "ZURN.SW", "ULVR.L", "DGE.L", "BATS.L", "OR.PA", "SAN.PA", "BN.PA", "AI.PA",
    "TTE.PA", "ASML.AS",
]

ZONE_REL = 1.20      # Dividendenrendite ≥ 120 % des eigenen 5-Jahres-Medians -> Kaufzone
NEAR_REL = 1.10      # ab 110 % -> "nah dran"
CUT_RATIO = 0.90     # Dividende < 90 % des Vorjahres -> gekürzt
MAX_PAYOUT = 1.00    # mehr Dividende als Gewinn ...
STATUS_LABEL = {"zone": "Kaufzone", "near": "Nah dran", "normal": "Normal bewertet", "cut": "Dividende gekürzt",
                "risk": "Dividende wackelig"}


def _dividends(ticker: str) -> pd.Series:
    from tracker.fundamentals import yf_symbol
    d = os.path.join(config.CACHE_DIR, "dividends")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, ticker.replace("/", "_") + ".pkl")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 20 * 3600:
        return pd.read_pickle(path)
    s = pd.Series(dtype=float)
    for _ in range(3):
        try:
            s = yf.Ticker(yf_symbol(ticker)).dividends
            break
        except Exception:
            time.sleep(2)
    s = s.copy()
    if len(s):
        s.index = pd.to_datetime(s.index).tz_localize(None)
    s.to_pickle(path)
    return s


def _prices(tickers) -> pd.DataFrame:
    path = os.path.join(config.CACHE_DIR, "dividend_prices.pkl")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 20 * 3600:
        p = pd.read_pickle(path)
        if set(tickers) <= set(p.columns):
            return p
    start = dt.date.today() - dt.timedelta(days=6 * 366)
    # nicht dividendenbereinigt: passend zu den gezahlten Dividenden
    df = yf.download(list(tickers), start=str(start), progress=False, auto_adjust=False, threads=True)
    p = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]].set_axis(list(tickers), axis=1)
    p.to_pickle(path)
    return p


def annual_dividend(divs: pd.Series, when: pd.Timestamp) -> float | None:
    """Dividende der letzten 12 Monate bis `when`. Jährliche Zahler (Europa): die letzte Zahlung,
    damit eine um ein paar Tage verschobene Zahlung nicht als Kürzung oder Verdopplung zählt."""
    past = divs[divs.index <= when]
    if past.empty or (when - past.index[-1]).days > 400:
        return None
    last3 = past[past.index > when - pd.Timedelta(days=3 * 365)]
    n = max(1, round(len(last3) / 3))          # Zahlungen pro Jahr (1, 2, 4, 12)
    if len(past) < n:
        return None
    return float(past.iloc[-n:].sum())          # die letzten n Zahlungen = eine Jahresdividende


def growth_years(divs: pd.Series) -> int:
    """Jahre in Folge ohne Senkung der Jahresdividende (abgeschlossene Kalenderjahre)."""
    yearly = divs.groupby(divs.index.year).sum()
    yearly = yearly[yearly.index < dt.date.today().year]
    n = 0
    vals = yearly.values[::-1]
    for a, b in zip(vals, vals[1:]):
        if a >= b * 0.99:
            n += 1
        else:
            break
    return n


def analyse_one(ticker: str, price: pd.Series, divs: pd.Series, info: dict) -> dict | None:
    price = price.dropna()
    if price.empty or divs.empty:
        return None
    now = price.index[-1]
    d_now = annual_dividend(divs, now)
    if not d_now:
        return None
    # Renditeverlauf: Monatsende der letzten 5 Jahre (ohne die letzten 3 Monate)
    months = pd.date_range(now - pd.DateOffset(years=5), now - pd.DateOffset(months=3), freq="ME")
    hist = []
    for m in months:
        p = price[:m]
        dm = annual_dividend(divs, m)
        if len(p) and dm:
            hist.append(dm / p.iloc[-1])
    if len(hist) < 30:
        return None
    y_now = d_now / price.iloc[-1]
    y_avg = float(np.median(hist))
    d_year_ago = annual_dividend(divs, now - pd.Timedelta(days=365))
    change_1y = (d_now / d_year_ago - 1) if d_year_ago else None
    cut = d_year_ago is not None and d_now < CUT_RATIO * d_year_ago

    payout = info.get("payoutRatio")
    fcf, shares = info.get("freeCashflow"), info.get("sharesOutstanding")
    same_ccy = (info.get("currency") or "") == (info.get("financialCurrency") or info.get("currency") or "")
    coverage = (fcf / (d_now * shares)) if fcf and shares and same_ccy and d_now else None
    reit = (info.get("sector") or "") == "Real Estate"
    # wackelig: mehr als den Gewinn ausgeschüttet UND nicht vom Free Cashflow gedeckt (Immobilien ausgenommen).
    # Einzeln sind beide Kennzahlen von Yahoo zu verrauscht (Sonderabschreibungen, Versorger-Investitionen).
    risky = not reit and payout is not None and payout > MAX_PAYOUT and (coverage is None or coverage < 1.0)

    rel = y_now / y_avg
    status = ("cut" if cut else "risk" if risky else "zone" if rel >= ZONE_REL else "near" if rel >= NEAR_REL else "normal")
    hi52 = price[price.index > now - pd.Timedelta(days=365)].max()
    return {
        "ticker": ticker, "name": str(info.get("longName") or info.get("shortName") or ticker).strip() if (info.get("longName") or info.get("shortName")) else ticker,
        "sector": info.get("sector"), "currency": info.get("currency"),
        "price": float(price.iloc[-1]), "yield": y_now, "yield_avg5": y_avg, "rel": rel,
        "fair_price": d_now / y_avg,     # Kurs, bei dem die Rendite wieder dem 5-J-Schnitt entspräche
        "div_change_1y": change_1y, "growth_years": growth_years(divs),
        "payout": payout, "fcf_coverage": coverage, "from_high": float(price.iloc[-1] / hi52 - 1),
        "pe": info.get("trailingPE"), "status": status, "status_label": STATUS_LABEL[status],
    }


def scan(tickers=UNIVERSE, log=print) -> pd.DataFrame:
    from tracker import fundamentals
    log(f"  Dividenden-Kaufzone: {len(tickers)} Aktien prüfen ...")
    from tracker.fundamentals import yf_symbol
    prices = _prices([yf_symbol(t) for t in tickers]).rename(columns={yf_symbol(t): t for t in tickers})
    info = fundamentals.load(list(tickers), log=lambda *_: None).to_dict("index")
    rows = []
    for t in tickers:
        if t not in prices.columns:
            continue
        try:
            r = analyse_one(t, prices[t], _dividends(t), info.get(t, {}))
        except Exception as ex:  # einzelne Aktie soll den Lauf nicht abbrechen
            log(f"    {t}: übersprungen ({ex})")
            continue
        if r:
            rows.append(r)
    df = pd.DataFrame(rows)
    if len(df):
        order = {"zone": 0, "near": 1, "normal": 2, "risk": 3, "cut": 4}
        df = df.sort_values(["status", "rel"], key=lambda c: c.map(order) if c.name == "status" else -c).reset_index(drop=True)
        log(f"  {int((df['status'] == 'zone').sum())} in der Kaufzone, {int((df['status'] == 'near').sum())} nah dran")
    return df


ZONE_FILE = os.path.join("state", "dividend_zone.json")


def new_in_zone(df: pd.DataFrame) -> pd.DataFrame:
    """Aktien, die seit dem letzten Check NEU in die Kaufzone gerutscht sind. Merkt sich den Stand."""
    import json
    zone = df[df["status"] == "zone"] if len(df) else df
    prev = set()
    if os.path.exists(ZONE_FILE):
        with open(ZONE_FILE) as f:
            prev = set(json.load(f))
    os.makedirs("state", exist_ok=True)
    with open(ZONE_FILE, "w") as f:
        json.dump(sorted(zone["ticker"]) if len(zone) else [], f)
    return zone[~zone["ticker"].isin(prev)] if len(zone) else zone
