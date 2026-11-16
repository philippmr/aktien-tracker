"""Chancen-Screener: sucht in USA + Europa nach vier Arten von Einstiegs-Chancen.

1. Turnaround             – stark abgestürzt, die Zahlen drehen, der Kurs stabilisiert sich
2. Qualität im Rücksetzer – sehr gute Firma, gerade deutlich korrigiert, Langfristtrend intakt
3. Wachstum fair bewertet – starkes, beschleunigtes Wachstum, gemessen daran nicht teuer
4. Ausbruch / Momentum    – läuft nach einer ruhigen Phase mit Rückenwind auf neue Hochs

Jede Strategie liefert einen Score 0–100 plus Begründungen. Die Regeln stehen in STRATEGIES
und werden per Backtest (tracker/screener_backtest.py) überprüft.
"""
import datetime as dt
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

import config
from tracker import fundamentals, universe

STRATEGIES = {
    "turnaround": {"label": "Turnaround", "horizon": "6–18 Monate"},
    "quality_dip": {"label": "Qualität im Rücksetzer", "horizon": "1–3 Jahre"},
    "garp": {"label": "Wachstum fair bewertet", "horizon": "1–3 Jahre"},
    "breakout": {"label": "Ausbruch / Momentum", "horizon": "Wochen bis Monate"},
}
MIN_SHOW = 60  # ab diesem Score gilt eine Aktie als Chance


# --------------------------------------------------------------------------- Kurse
def load_prices(tickers: list[str], days: int = 800, log=print) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Tägliche Schlusskurse und Volumen (Spalten = Ticker), einmal pro Tag neu geladen."""
    import yfinance as yf
    path = os.path.join(config.CACHE_DIR, "screener_prices.pkl")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 20 * 3600:
        close, vol = pd.read_pickle(path)
        if set(tickers) <= set(close.columns):
            return close[tickers], vol[tickers]
    start = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    closes, vols = [], []
    log(f"  Kurse für {len(tickers)} Aktien laden ...")
    for i in range(0, len(tickers), 200):
        chunk = tickers[i:i + 200]
        for attempt in range(3):
            try:
                df = yf.download(chunk, start=start, auto_adjust=True, progress=False, threads=True)
                break
            except Exception:
                time.sleep(5)
        else:
            continue
        if df.empty:
            continue
        closes.append(df["Close"])
        vols.append(df["Volume"])
    close = pd.concat(closes, axis=1).sort_index()
    vol = pd.concat(vols, axis=1).sort_index()
    close = close.loc[:, ~close.columns.duplicated()]
    vol = vol.loc[:, ~vol.columns.duplicated()]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pd.to_pickle((close, vol), path)
    return close, vol


def price_features(close: pd.DataFrame, vol: pd.DataFrame, bench: pd.Series | None = None,
                   asof: pd.Timestamp | None = None) -> pd.DataFrame:
    """Kursmerkmale je Aktie zum Stichtag `asof` (Standard: letzter Handelstag)."""
    if asof is not None:
        close, vol = close[close.index <= asof], vol[vol.index <= asof]
        if bench is not None:
            bench = bench[bench.index <= asof]
    c = close.ffill()
    last = c.iloc[-1]
    hi52 = c.tail(252).max()
    lo52 = c.tail(252).min()
    hi2y = c.tail(504).max()
    ma50 = c.tail(50).mean()
    ma200 = c.tail(200).mean()
    ma200_prev = c.iloc[:-21].tail(200).mean() if len(c) > 221 else ma200
    r = lambda n: last / c.iloc[-n - 1] - 1 if len(c) > n else pd.Series(np.nan, index=c.columns)
    b = (lambda n: float(bench.iloc[-1] / bench.iloc[-n - 1] - 1) if bench is not None and len(bench) > n else 0.0)
    # Basis: Schwankungsbreite der 60 Handelstage vor den letzten 10
    base = c.iloc[-70:-10] if len(c) > 70 else c
    base_range = base.max() / base.min() - 1
    v = vol.ffill()
    vol_ratio = v.tail(10).mean() / v.tail(100).mean()
    delta = c.diff().tail(15)
    gain, loss = delta.clip(lower=0).mean(), (-delta.clip(upper=0)).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    return pd.DataFrame({
        "price": last, "dd52": last / hi52 - 1, "dd2y": last / hi2y - 1, "up_from_low": last / lo52 - 1,
        "above_ma50": last > ma50, "above_ma200": last > ma200, "ma50_vs_ma200": ma50 / ma200 - 1,
        "ma200_rising": ma200 > ma200_prev, "ma200_dist": last / ma200 - 1,
        "rs3": r(63) - b(63), "rs6": r(126) - b(126), "rs12": r(252) - b(252),
        "base_range": base_range, "vol_ratio": vol_ratio, "rsi": rsi,
        "days": c.notna().sum(),
    })


# --------------------------------------------------------------------------- Quartalszahlen
def quarterly_trend(ticker: str) -> dict:
    """Umsatz-/Margentrend aus den letzten Quartalen (Yahoo), 7 Tage gecacht."""
    import yfinance as yf
    d = os.path.join(config.CACHE_DIR, "quarterly")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{ticker.replace('/', '_')}.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 7 * 24 * 3600:
        with open(path) as f:
            return json.load(f)
    out = {}
    try:
        q = yf.Ticker(fundamentals.yf_symbol(ticker)).quarterly_income_stmt
        if q is not None and not q.empty:
            q = q.loc[:, sorted(q.columns, reverse=True)]  # neuestes Quartal zuerst
            rev = q.loc["Total Revenue"] if "Total Revenue" in q.index else None
            op = q.loc["Operating Income"] if "Operating Income" in q.index else None
            if rev is not None:
                rev = rev.astype(float)
                if len(rev) >= 5 and rev.iloc[4]:
                    out["rev_yoy"] = rev.iloc[0] / rev.iloc[4] - 1
                if len(rev) >= 6 and rev.iloc[5]:
                    out["rev_yoy_prev"] = rev.iloc[1] / rev.iloc[5] - 1
                if len(rev) >= 2 and rev.iloc[1]:
                    out["rev_qoq"] = rev.iloc[0] / rev.iloc[1] - 1
            if rev is not None and op is not None:
                op = op.astype(float)
                m = (op / rev).replace([np.inf, -np.inf], np.nan)
                out["opm_last"] = m.iloc[0]
                if len(m) >= 5:
                    out["opm_year_ago"] = m.iloc[4]
                elif len(m) >= 2:
                    out["opm_year_ago"] = m.iloc[-1]
    except Exception:
        pass
    out = {k: (None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)) for k, v in out.items()}
    with open(path, "w") as f:
        json.dump(out, f)
    return out


# --------------------------------------------------------------------------- Strategien
def _num(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else x


def score_turnaround(p: dict, f: dict, q: dict) -> tuple[float, list[str]]:
    if not (p["dd2y"] <= -0.40):
        return 0, []
    pts, why = 10, [f"{-p['dd2y']:.0%} unter dem 2-Jahres-Hoch"]
    ry, ryp = _num(q.get("rev_yoy")), _num(q.get("rev_yoy_prev"))
    if ry is not None and ryp is not None and ry > ryp:
        pts += 20
        why.append(f"Umsatztrend dreht: {ryp:+.0%} → {ry:+.0%} ggü. Vorjahr")
        if ry > 0 >= ryp:
            pts += 10
            why.append("Umsatz wieder im Plus")
    om, omp = _num(q.get("opm_last")), _num(q.get("opm_year_ago"))
    if om is not None and omp is not None and om > omp + 0.02:
        pts += 20
        why.append(f"Marge verbessert: {omp:.0%} → {om:.0%}")
    cash, debt, ebitda = _num(f.get("totalCash")), _num(f.get("totalDebt")), _num(f.get("ebitda"))
    if cash is not None and debt is not None:
        if cash >= debt:
            pts += 15
            why.append("mehr Cash als Schulden")
        elif ebitda and ebitda > 0 and (debt - cash) / ebitda < 3:
            pts += 10
            why.append(f"Verschuldung tragbar ({(debt - cash) / ebitda:.1f}× EBITDA)")
    if (_num(f.get("freeCashflow")) or 0) > 0:
        pts += 10
        why.append("Free Cashflow positiv")
    if p["above_ma50"] and p["up_from_low"] > 0.15:
        pts += 15
        why.append(f"Kurs stabilisiert (+{p['up_from_low']:.0%} vom Tief, über 50-Tage-Linie)")
    return min(100, pts), why


def score_quality_dip(p: dict, f: dict, q: dict) -> tuple[float, list[str]]:
    om, rg, roe = _num(f.get("operatingMargins")), _num(f.get("revenueGrowth")), _num(f.get("returnOnEquity"))
    fcf = _num(f.get("freeCashflow"))
    quality = (om or 0) > 0.15 and (rg or -1) > 0.05 and (fcf or 0) > 0
    if not (quality and -0.35 <= p["dd52"] <= -0.12):
        return 0, []
    pts, why = 20, [f"Qualität: Marge {om:.0%}, Umsatz {rg:+.0%}, FCF positiv",
                    f"{-p['dd52']:.0%} unter dem 52-Wochen-Hoch"]
    if (roe or 0) > 0.15:
        pts += 10
        why.append(f"Eigenkapitalrendite {roe:.0%}")
    if om > 0.25:
        pts += 10
    if rg > 0.10:
        pts += 10
    if p["ma200_rising"] and p["ma200_dist"] > -0.10:
        pts += 20
        why.append("Langfristtrend intakt (200-Tage-Linie steigt)")
    if p["rsi"] < 35:
        pts += 15
        why.append(f"kurzfristig überverkauft (RSI {p['rsi']:.0f})")
    pe = _num(f.get("forwardPE"))
    if pe and 0 < pe < 25:
        pts += 15
        why.append(f"Bewertung moderat (KGV {pe:.0f})")
    return min(100, pts), why


def score_garp(p: dict, f: dict, q: dict) -> tuple[float, list[str]]:
    rg = _num(f.get("revenueGrowth"))
    if rg is None or rg < 0.20:
        return 0, []
    pts, why = 10, [f"Umsatz {rg:+.0%} ggü. Vorjahresquartal"]
    pts += min(25, (rg - 0.20) * 100)
    ry, ryp = _num(q.get("rev_yoy")), _num(q.get("rev_yoy_prev"))
    if ry is not None and ryp is not None and ry > ryp:
        pts += 15
        why.append("Wachstum beschleunigt sich")
    evs, peg = _num(f.get("enterpriseToRevenue")), _num(f.get("trailingPegRatio"))
    if evs is not None and evs > 0 and evs / (rg * 100) < 0.25:
        pts += 20
        why.append(f"günstig fürs Wachstum (EV/Umsatz {evs:.1f} bei {rg:.0%} Wachstum)")
    elif peg and 0 < peg < 1.5:
        pts += 20
        why.append(f"PEG {peg:.1f}")
    om = _num(f.get("operatingMargins"))
    if om is not None and om > 0.10:
        pts += 15
        why.append(f"profitabel (Marge {om:.0%})")
    if p["above_ma200"]:
        pts += 10
    return min(100, pts), why


def score_breakout(p: dict, f: dict, q: dict) -> tuple[float, list[str]]:
    if not (p["dd52"] >= -0.03 and p["rs6"] > 0.10):
        return 0, []
    pts, why = 20, [f"nahe am 52-Wochen-Hoch, 6 Monate {p['rs6']:+.0%} besser als der Markt"]
    if p["base_range"] < 0.25:
        pts += 20
        why.append(f"vorher ruhige Basis (Schwankung {p['base_range']:.0%})")
    if p["vol_ratio"] > 1.5:
        pts += 20
        why.append(f"Volumen {p['vol_ratio']:.1f}× über Schnitt")
    if p["rs6"] > 0.30:
        pts += 15
    rg = _num(f.get("revenueGrowth"))
    if rg is not None and rg > 0.15:
        pts += 15
        why.append(f"Umsatz {rg:+.0%}")
    if p["ma50_vs_ma200"] > 0 and p["above_ma50"]:
        pts += 10
    return min(100, pts), why


SCORERS = {"turnaround": score_turnaround, "quality_dip": score_quality_dip,
           "garp": score_garp, "breakout": score_breakout}


# --------------------------------------------------------------------------- Lauf
def run(politician_tickers: set[str] | None = None, log=print) -> pd.DataFrame:
    """Kompletter Screener-Lauf. Ergebnis: eine Zeile je (Aktie, Strategie) mit Score >= MIN_SHOW."""
    uni = universe.load(log=log)
    meta = {u["symbol"]: u for u in uni}
    tickers = sorted(meta)
    close, vol = load_prices(tickers, log=log)
    bench = close["SPY"] if "SPY" in close.columns else None
    if bench is None:
        import yfinance as yf
        bench = yf.download("SPY", period="3y", auto_adjust=True, progress=False)["Close"].squeeze()
    feats = price_features(close, vol, bench)
    feats = feats[feats["days"] >= 200]  # mindestens ~1 Jahr Kurshistorie

    log(f"  Kennzahlen für {len(feats)} Aktien laden ...")
    fund = fundamentals.load(list(feats.index), log=lambda *a: None, workers=6)

    # Quartalstrend nur für Aktien, bei denen er zählt (Turnaround- und Wachstums-Kandidaten)
    need = set(feats.index[feats["dd2y"] <= -0.40]) | set(fund.index[fund["revenueGrowth"].fillna(0) >= 0.20])
    with ThreadPoolExecutor(max_workers=6) as ex:
        qmap = dict(zip(sorted(need), ex.map(quarterly_trend, sorted(need))))

    rows = []
    for t, p in feats.iterrows():
        f = fund.loc[t].to_dict() if t in fund.index else {}
        q = qmap.get(t, {})
        m = meta.get(t, {})
        for key, scorer in SCORERS.items():
            sc, why = scorer(p.to_dict(), f, q)
            if sc < MIN_SHOW:
                continue
            if politician_tickers and t.split(".")[0] in politician_tickers and m.get("region") == "us":
                sc = min(100, sc + 10)
                why = why + ["Bonus: US-Politiker haben kürzlich gekauft"]
            rows.append({
                "ticker": t, "name": f.get("shortName") or m.get("shortName") or t, "region": m.get("region"),
                "currency": m.get("currency"), "sector": f.get("sector"), "industry": f.get("industry"),
                "strategy": key, "strategy_label": STRATEGIES[key]["label"], "horizon": STRATEGIES[key]["horizon"],
                "score": round(float(sc), 1), "reasons": why,
                "price": float(p["price"]), "market_cap": f.get("marketCap") or m.get("marketCap"),
                "dd52": float(p["dd52"]), "dd2y": float(p["dd2y"]), "rs6": _num(float(p["rs6"])),
                "rev_growth": _num(f.get("revenueGrowth")), "op_margin": _num(f.get("operatingMargins")),
                "forward_pe": _num(f.get("forwardPE")), "backtested": m.get("region") == "us",
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    log(f"  Chancen: {len(df)} Treffer ({', '.join(f'{STRATEGIES[k]['label']} {n}' for k, n in df['strategy'].value_counts().items())})")
    return df.sort_values("score", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- täglicher Lauf + Status
STATE = os.path.join("state", "screener.json")
SEEN = os.path.join("state", "screener_seen.json")
BT_STATE = os.path.join("state", "screener_backtest.json")


def daily(politician_tickers: set[str] | None = None, force: bool = False, log=print) -> dict:
    """Läuft einmal pro Tag (sonst gespeichertes Ergebnis). Markiert Chancen, die neu sind."""
    today = dt.date.today().isoformat()
    if not force and os.path.exists(STATE):
        with open(STATE) as f:
            st = json.load(f)
        if st.get("date") == today:
            return st
    df = run(politician_tickers, log=log)
    seen = set(json.load(open(SEEN))) if os.path.exists(SEEN) else set()
    first = not seen
    rows = []
    for r in (df.to_dict("records") if len(df) else []):
        key = f"{r['ticker']}|{r['strategy']}"
        r["is_new"] = (key not in seen) and not first
        seen.add(key)
        rows.append(r)
    stats = json.load(open(BT_STATE)) if os.path.exists(BT_STATE) else {}
    st = {"date": today, "rows": rows, "stats": stats}
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(st, f, default=lambda o: None if isinstance(o, float) and math.isnan(o) else str(o))
    with open(SEEN, "w") as f:
        json.dump(sorted(seen), f)
    return st
