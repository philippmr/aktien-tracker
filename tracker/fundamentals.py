"""Fundamentaldaten und Kurse über yfinance (Yahoo Finance), mit Tages-Cache."""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf

import config

FIELDS = [
    "shortName", "sector", "industry", "quoteType", "marketCap", "currentPrice",
    "trailingPE", "forwardPE", "trailingPegRatio", "priceToBook", "enterpriseToEbitda",
    "priceToSalesTrailing12Months", "returnOnEquity", "returnOnAssets", "profitMargins",
    "operatingMargins", "grossMargins", "revenueGrowth", "earningsGrowth", "debtToEquity",
    "currentRatio", "freeCashflow", "operatingCashflow", "totalCash", "totalDebt",
    "dividendYield", "recommendationMean", "numberOfAnalystOpinions", "targetMeanPrice",
    "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "fiftyDayAverage", "twoHundredDayAverage", "beta",
    "heldPercentInsiders", "shortPercentOfFloat",
    "enterpriseToRevenue", "ebitda", "totalRevenue", "earningsQuarterlyGrowth", "returnOnEquity",
    "currency", "financialCurrency", "longName",
    "payoutRatio", "dividendRate", "sharesOutstanding", "fiveYearAvgDividendYield",
]


def yf_symbol(t: str) -> str:
    """US-Aktienklassen 'BRK.B' -> Yahoo 'BRK-B'; Börsenkürzel wie 'SIE.DE' bleiben."""
    import re
    if t.endswith((".L", ".F", ".V")):   # London, Frankfurt, TSX Venture
        return t
    return t.replace(".", "-") if re.fullmatch(r"[A-Z]+\.[A-Z]", t) else t


def _one(ticker: str) -> dict:
    d = os.path.join(config.CACHE_DIR, "fundamentals")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{ticker.replace('/', '_')}.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 20 * 3600:
        with open(path) as f:
            return json.load(f)
    info = {}
    for attempt in range(3):
        try:
            info = yf.Ticker(yf_symbol(ticker)).info or {}
            break
        except Exception:
            time.sleep(2 + attempt * 3)
    out = {k: info.get(k) for k in FIELDS}
    out["ticker"] = ticker
    if info:
        with open(path, "w") as f:
            json.dump(out, f)
    return out


def load(tickers: list[str], log=print, workers: int = 4) -> pd.DataFrame:
    tickers = sorted(set(tickers))
    log(f"  Fundamentaldaten für {len(tickers)} Ticker laden ...")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(_one, tickers))
    return pd.DataFrame(rows).set_index("ticker")


def price_history(tickers: list[str], start) -> pd.DataFrame:
    """Schlusskurse (Spalten = Ticker) ab `start`."""
    if not tickers:
        return pd.DataFrame()
    yt = {t: yf_symbol(t) for t in tickers}
    df = yf.download(list(yt.values()), start=str(start), progress=False,
                     auto_adjust=True, threads=True)
    if df.empty:
        return pd.DataFrame()
    close = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]].rename(
        columns={"Close": list(yt.values())[0]})
    close = close.rename(columns={v: k for k, v in yt.items()})
    return close
