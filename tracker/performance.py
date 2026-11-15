"""Kurse laden und Trefferquote je Politiker berechnen.

Für jeden früheren Aktienkauf eines Politikers: Rendite über HORIZON_DAYS ab Transaktionstag,
verglichen mit dem S&P 500 (SPY) im selben Zeitraum ("Überrendite").
"""
import datetime as dt
import os
import time

import pandas as pd
import yfinance as yf

import config

BENCHMARK = "SPY"


def _yf(t: str) -> str:
    return t.replace(".", "-")


def load_prices(tickers, start: dt.date, log=print) -> pd.DataFrame:
    """Tägliche Schlusskurse (Spalten = Ticker). Wird pro Tag gecacht."""
    tickers = sorted(set(tickers) | {BENCHMARK})
    os.makedirs(config.CACHE_DIR, exist_ok=True)
    path = os.path.join(config.CACHE_DIR, "prices.pkl")
    cached = pd.DataFrame()
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 20 * 3600:
        cached = pd.read_pickle(path)
        if len(cached) and cached.index.min().date() > start:
            cached = pd.DataFrame()  # zu kurzer Zeitraum im Cache
    missing = [t for t in tickers if t not in cached.columns]
    if missing:
        log(f"  Kurse laden für {len(missing)} Ticker ...")
        frames = []
        for i in range(0, len(missing), 150):
            chunk = missing[i:i + 150]
            df = yf.download([_yf(t) for t in chunk], start=str(start), progress=False,
                             auto_adjust=True, threads=True)
            if df.empty:
                continue
            close = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]].set_axis([_yf(chunk[0])], axis=1)
            close = close.rename(columns={_yf(t): t for t in chunk})
            frames.append(close)
        if frames:
            new = pd.concat(frames, axis=1)
            cached = pd.concat([cached, new], axis=1) if len(cached) else new
            cached = cached.loc[:, ~cached.columns.duplicated()]
            # leere Spalten (unbekannte Ticker) behalten, damit sie nicht jedes Mal neu geladen werden
            cached = cached.reindex(columns=list(cached.columns) + [t for t in missing if t not in cached.columns])
            cached.to_pickle(path)
    return cached


def _price_on(series: pd.Series, d: dt.date):
    s = series[series.index.date >= d].dropna()
    return (s.index[0].date(), s.iloc[0]) if len(s) else (None, None)


def trade_returns(buys: pd.DataFrame, prices: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Fügt je Kauf die Rendite über `horizon` Tage und die Überrendite vs. SPY hinzu."""
    spy = prices[BENCHMARK]
    rows = []
    for idx, b in buys.iterrows():
        t = b["ticker"]
        if t not in prices.columns or prices[t].dropna().empty:
            continue
        d0, p0 = _price_on(prices[t], b["tx_date"])
        d1, p1 = _price_on(prices[t], b["tx_date"] + dt.timedelta(days=horizon))
        if p0 is None or p1 is None or d1 <= d0:
            continue
        _, s0 = _price_on(spy, d0)
        _, s1 = _price_on(spy, d1)
        r = p1 / p0 - 1
        rows.append((idx, r, r - (s1 / s0 - 1)))
    out = pd.DataFrame(rows, columns=["idx", "ret", "excess"]).set_index("idx")
    return buys.join(out, how="inner")


def track_records(history: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Je Politiker: Anzahl bewerteter Käufe, Trefferquote (schlägt SPY), Ø-Überrendite."""
    horizon = config.TRACK_RECORD_HORIZON_DAYS
    cutoff = dt.date.today() - dt.timedelta(days=horizon)
    buys = history[(history["type"] == "Kauf") & (history["asset_type"] == "ST") &
                   history["ticker"].notna() & (history["tx_date"] <= cutoff)]
    # Dividenden-Wiederanlage ist kein bewusster Kauf
    buys = buys[~buys["description"].fillna("").str.contains("reinvest", case=False)]
    r = trade_returns(buys, prices, horizon)
    if r.empty:
        return pd.DataFrame(columns=["n", "hit_rate", "avg_excess"])
    # Gleiche Aktie am selben Tag mehrfach (mehrere Depots) nur einmal zählen
    r = r.drop_duplicates(["person", "ticker", "tx_date"])
    g = r.groupby("person")
    return pd.DataFrame({
        "n": g.size(),
        "hit_rate": g["excess"].apply(lambda x: (x > 0).mean()),
        "avg_excess": g["excess"].mean(),
        "median_excess": g["excess"].median(),
    })
