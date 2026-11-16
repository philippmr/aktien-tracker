"""Backtest des Chancen-Screeners (nur USA, weil es nur dort kostenlose historische Bilanzdaten gibt).

Ablauf: An jedem Monatsanfang 2023–heute werden alle US-Aktien (ab 2 Mrd $ Börsenwert zum Stichtag)
mit genau den Daten bewertet, die an diesem Tag bekannt waren (Kurse bis zum Stichtag,
SEC-Berichte mit Einreichungsdatum vor dem Stichtag). Danach wird gemessen, wie die gefundenen
Aktien in 30/90/365 Tagen gelaufen sind – im Vergleich zum S&P 500 (SPY) und zum Nasdaq-100 (QQQ).
"""
import datetime as dt
import math
import os
import time

import numpy as np
import pandas as pd

import config
from tracker import screener, secfacts

HORIZONS = (30, 90, 365)
HOLD = {"turnaround": 365, "quality_dip": 365, "garp": 365, "breakout": 90}


def load_prices(tickers, start="2021-06-01", log=print):
    import yfinance as yf
    path = os.path.join(config.CACHE_DIR, "screener_bt_prices.pkl")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 6 * 24 * 3600:
        return pd.read_pickle(path)
    closes, vols = [], []
    allt = sorted(set(tickers) | {"SPY", "QQQ"})
    log(f"  Kurse seit {start} für {len(allt)} Aktien laden ...")
    for i in range(0, len(allt), 200):
        df = yf.download(allt[i:i + 200], start=start, auto_adjust=True, progress=False, threads=True)
        if not df.empty:
            closes.append(df["Close"])
            vols.append(df["Volume"])
    close = pd.concat(closes, axis=1).sort_index()
    vol = pd.concat(vols, axis=1).sort_index()
    close, vol = close.loc[:, ~close.columns.duplicated()], vol.loc[:, ~vol.columns.duplicated()]
    pd.to_pickle((close, vol), path)
    return close, vol


def _fund_at(fa: dict, price: float) -> tuple[dict, dict, float | None]:
    """SEC-Kennzahlen zum Stichtag -> dieselben Felder, die der Live-Screener von Yahoo bekommt."""
    g = lambda k: fa.get(k)
    shares = g("shares")
    mcap = shares * price if shares and price == price else None
    ni, rev, eq = g("net_income"), g("revenue_annual"), g("equity")
    cash, debt = g("cash"), g("debt")
    ev = (mcap + (debt or 0) - (cash or 0)) if mcap else None
    f = {
        "revenueGrowth": g("rev_growth_q"), "operatingMargins": g("op_margin"),
        "freeCashflow": g("fcf"), "returnOnEquity": (ni / eq) if ni is not None and eq and eq > 0 else None,
        "totalCash": cash, "totalDebt": debt, "ebitda": g("op_income_annual"),
        "forwardPE": (mcap / ni) if mcap and ni and ni > 0 else None,      # historisch nur das KGV (trailing)
        "enterpriseToRevenue": (ev / rev) if ev and rev and rev > 0 else None,
        "trailingPegRatio": None, "marketCap": mcap,
    }
    q = {"rev_yoy": g("rev_growth_q"), "rev_yoy_prev": g("rev_growth_q_prev"),
         "opm_last": g("opm_last"), "opm_year_ago": g("opm_year_ago")}
    return f, q, mcap


def build_signals(tickers: list[str], log=print) -> pd.DataFrame:
    close, vol = load_prices(tickers, log=log)
    spy, qqq = close["SPY"], close["QQQ"]
    stocks = [t for t in tickers if t in close.columns and t not in ("SPY", "QQQ")]
    cik = secfacts._cik_map()
    facts = {t: secfacts.load(t, cik) for t in stocks}
    stocks = [t for t in stocks if facts.get(t)]
    log(f"  {len(stocks)} US-Aktien mit Kursen und SEC-Daten")

    months = pd.date_range("2023-02-01", dt.date.today() - dt.timedelta(days=31), freq="MS")
    rows = []
    for m in months:
        day = close.index[close.index >= m][0]
        feats = screener.price_features(close[stocks], vol[stocks], spy, asof=day)
        feats = feats[feats["days"] >= 200]
        for t, p in feats.iterrows():
            fa = secfacts.at(facts[t], day.date())
            f, q, mcap = _fund_at(fa, p["price"])
            if not mcap or mcap < 2e9:
                continue
            pd_ = p.to_dict()
            for key, scorer in screener.SCORERS.items():
                sc, _ = scorer(pd_, f, q)
                if sc > 0:
                    rows.append({"date": day, "ticker": t, "strategy": key, "score": sc, "mcap": mcap})
        log(f"    {day.date()}: {sum(1 for r in rows if r['date'] == day)} Signale")
    sig = pd.DataFrame(rows)

    # Renditen ab dem nächsten Handelstag
    def fwd(series, d, h):
        s = series.dropna()
        a = s[s.index > d]
        if a.empty:
            return np.nan
        b = s[s.index >= a.index[0] + pd.Timedelta(days=h)]
        return b.iloc[0] / a.iloc[0] - 1 if len(b) else np.nan
    for h in HORIZONS:
        bench_s = {d: fwd(spy, d, h) for d in sig["date"].unique()}
        bench_q = {d: fwd(qqq, d, h) for d in sig["date"].unique()}
        r = [fwd(close[t], d, h) for t, d in zip(sig["ticker"], sig["date"])]
        sig[f"ret{h}"] = r
        sig[f"ex{h}"] = (sig[f"ret{h}"] - sig["date"].map(bench_s)).clip(-1, 3)
        sig[f"exq{h}"] = (sig[f"ret{h}"] - sig["date"].map(bench_q)).clip(-1, 3)
    return sig


def summarize(sig: pd.DataFrame, threshold: float = screener.MIN_SHOW) -> pd.DataFrame:
    s = sig[sig["score"] >= threshold].copy()
    s["year"] = s["date"].dt.year
    out = []
    for key, g in s.groupby("strategy"):
        row = {"strategy": key, "label": screener.STRATEGIES[key]["label"], "signals": len(g),
               "per_month": len(g) / g["date"].nunique()}
        for h in HORIZONS:
            x = g[f"ex{h}"].dropna()
            row[f"ex{h}"], row[f"hit{h}"] = x.mean(), (x > 0).mean()
            row[f"exq{h}"] = g[f"exq{h}"].dropna().mean()
            row[f"ex{h}_train"] = g.loc[g["year"] <= 2024, f"ex{h}"].mean()
            row[f"ex{h}_test"] = g.loc[g["year"] >= 2025, f"ex{h}"].mean()
        out.append(row)
    return pd.DataFrame(out)
