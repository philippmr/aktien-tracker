"""Crash-Alarm: Mail, wenn ein großer Index 15 / 25 / 35 % unter seinem Allzeithoch steht.

So wie Helmut Jonen es macht: In großen Rücksetzern nachkaufen statt in Panik verkaufen.
Jede Stufe wird nur einmal gemeldet; zurückgesetzt wird erst, wenn der Index wieder
weniger als 5 % unter dem Hoch steht.
"""
import pandas as pd
import yfinance as yf

INDICES = {"S&P 500": "^GSPC", "MSCI World": "URTH", "Nasdaq 100": "^NDX", "DAX": "^GDAXI"}
LEVELS = [15, 25, 35]
RESET = 5


def status() -> list[dict]:
    df = yf.download(list(INDICES.values()), start="1985-01-01", progress=False, auto_adjust=True, threads=True)["Close"]
    out = []
    for name, sym in INDICES.items():
        s = df[sym].dropna() if sym in df.columns else pd.Series(dtype=float)
        if s.empty:
            continue
        ath = s.max()
        dd = float(s.iloc[-1] / ath - 1)
        out.append({"name": name, "symbol": sym, "price": float(s.iloc[-1]), "ath": float(ath),
                    "ath_date": s.idxmax().date().isoformat(), "drawdown": dd,
                    "level": max([lv for lv in LEVELS if -dd * 100 >= lv], default=0)})
    return out


def new_alerts(st: list[dict], meta: dict) -> list[dict]:
    """Gibt Indizes zurück, die eine neue (tiefere) Stufe erreicht haben, und merkt sich den Stand in meta."""
    seen = meta.setdefault("crash", {})
    hits = []
    for r in st:
        prev = seen.get(r["name"], 0)
        if -r["drawdown"] * 100 < RESET:
            seen[r["name"]] = 0
        elif r["level"] > prev:
            seen[r["name"]] = r["level"]
            hits.append(r)
    return hits
