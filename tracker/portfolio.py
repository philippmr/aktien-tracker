"""Musterdepots: Folgen den Signalen des Trackers nach festen Regeln und messen das Ergebnis
gegen den S&P 500. So zeigt sich ab heute in Echtzeit, ob das System funktioniert.

Zwei Depots (siehe DEPOTS):
- Trade-Depot:  Trade-Score >= 70, Haltedauer 30 Tage (kurzfristige Trades)
- Halte-Depot:  Halte-Score >= 70, Haltedauer 365 Tage (Buy & Hold)

Kauf jeweils für POSITION_USD zum ersten Schlusskurs NACH dem Signaltag (so wie du es selbst
kaufen könntest), Verkauf nach der Haltedauer. Vergleich: derselbe Betrag am selben Tag in SPY.
Zustand liegt in state/portfolio_<depot>.csv (wird mit dem Workflow committet).
"""
import datetime as dt
import json
import math
import os

import pandas as pd

# Zwei Depots, Regeln aus dem Backtest:
DEPOTS = {
    "trade":  {"label": "Trade-Depot", "score": "trade_score", "min": 70, "hold": 30,
               "file": os.path.join("state", "portfolio_trade.csv"),
               "sim": os.path.join("state", "portfolio_trade_sim.json")},
    "halten": {"label": "Halte-Depot", "score": "hold_score", "min": 70, "hold": 365,
               "file": os.path.join("state", "portfolio_halten.csv"),
               "sim": os.path.join("state", "portfolio_halten_sim.json")},
}
# Depot, das nicht nach Score, sondern jedem Kauf der Top-Trader folgt (siehe update_from_trades)
DEPOTS["top"] = {"label": "Top-Trader-Depot", "score": None, "min": 0, "hold": 365,
                 "file": os.path.join("state", "portfolio_top.csv"),
                 "sim": os.path.join("state", "portfolio_top_sim.json")}
POSITION_USD = 1000
COLS = ["ticker", "name", "signal_date", "score", "entry_date", "entry_price", "spy_entry",
        "exit_date", "exit_price", "spy_exit", "status"]


def _load(depot: str) -> pd.DataFrame:
    f = DEPOTS[depot]["file"]
    if os.path.exists(f):
        return pd.read_csv(f, dtype={"ticker": str})
    return pd.DataFrame(columns=COLS)


def _close_after(s: pd.Series, day: dt.date):
    """Erster Schlusskurs streng nach `day`."""
    s = s.dropna()
    a = s[s.index.date > day]
    return (a.index[0].date(), float(a.iloc[0])) if len(a) else (None, None)


def _close_on_or_after(s: pd.Series, day: dt.date):
    s = s.dropna()
    a = s[s.index.date >= day]
    return (a.index[0].date(), float(a.iloc[0])) if len(a) else (None, None)


def update(ranking: pd.DataFrame, prices: pd.DataFrame, depot: str, log=print) -> pd.DataFrame:
    """Neue Signale aufnehmen, Einstiege/Ausstiege mit Kursen füllen, speichern."""
    cfg = DEPOTS[depot]
    MIN_SCORE, HOLD_DAYS = cfg["min"], cfg["hold"]
    pf = _load(depot)
    today = dt.date.today()
    spy = prices["SPY"]
    held = set(pf.loc[pf["status"].isin(["offen", "wartet"]), "ticker"])

    # 1) neue Signale
    if len(ranking):
        new = ranking[(ranking[cfg["score"]] >= MIN_SCORE) & ~ranking["ticker"].isin(held)]
        rows = [{"ticker": r["ticker"], "name": r["name"], "signal_date": today.isoformat(), "score": r[cfg["score"]],
                 "status": "wartet"} for _, r in new.iterrows()]
        if rows:
            pf = pd.concat([pf, pd.DataFrame(rows)], ignore_index=True)
            log(f"  {cfg['label']}: {len(rows)} neue Signale ({', '.join(new['ticker'])})")

    # 2) Einstieg zum ersten Schlusskurs nach dem Signal
    for i, r in pf[pf["status"] == "wartet"].iterrows():
        if r["ticker"] not in prices.columns:
            continue
        sig = dt.date.fromisoformat(str(r["signal_date"]))
        d, p = _close_after(prices[r["ticker"]], sig)
        _, sp = _close_after(spy, sig)
        if d and sp:
            pf.loc[i, ["entry_date", "entry_price", "spy_entry", "status"]] = [d.isoformat(), p, sp, "offen"]

    # 3) Ausstieg nach HOLD_DAYS
    for i, r in pf[pf["status"] == "offen"].iterrows():
        entry = dt.date.fromisoformat(str(r["entry_date"]))
        if (today - entry).days < HOLD_DAYS or r["ticker"] not in prices.columns:
            continue
        d, p = _close_on_or_after(prices[r["ticker"]], entry + dt.timedelta(days=HOLD_DAYS))
        _, sp = _close_on_or_after(spy, entry + dt.timedelta(days=HOLD_DAYS))
        if d and sp:
            pf.loc[i, ["exit_date", "exit_price", "spy_exit", "status"]] = [d.isoformat(), p, sp, "geschlossen"]

    os.makedirs(os.path.dirname(cfg["file"]), exist_ok=True)
    pf[COLS].to_csv(cfg["file"], index=False)
    return pf


def summary(pf: pd.DataFrame, prices: pd.DataFrame) -> dict:
    """Kennzahlen + Wertkurve für Dashboard und Wochenmail."""
    pf = pf[pf["status"].isin(["offen", "geschlossen"])].copy()
    if pf.empty:
        return {"positions": [], "curve": [], "n": 0}
    spy = prices["SPY"].dropna()
    pos = []
    for r in pf.itertuples():
        s = prices[r.ticker].dropna() if r.ticker in prices.columns else pd.Series(dtype=float)
        last = float(r.exit_price) if r.status == "geschlossen" else (float(s.iloc[-1]) if len(s) else float(r.entry_price))
        spy_last = float(r.spy_exit) if r.status == "geschlossen" else float(spy.iloc[-1])
        ret, spy_ret = last / float(r.entry_price) - 1, spy_last / float(r.spy_entry) - 1
        pos.append({"ticker": r.ticker, "name": r.name, "score": (float(r.score) if r.score is not None and r.score == r.score else None), "entry_date": r.entry_date,
                    "exit_date": r.exit_date if isinstance(r.exit_date, str) else None, "status": r.status,
                    "ret": ret, "spy_ret": spy_ret, "excess": ret - spy_ret})
    pos_df = pd.DataFrame(pos)
    curve = _curve(pf, prices)
    invested = len(pos_df) * POSITION_USD
    return {
        "n": len(pos_df), "open": int((pos_df["status"] == "offen").sum()),
        "invested": invested,
        "ret": float(pos_df["ret"].mean()), "spy_ret": float(pos_df["spy_ret"].mean()),
        "excess": float(pos_df["excess"].mean()), "hit": float((pos_df["excess"] > 0).mean()),
        "positions": pos_df.sort_values("entry_date", ascending=False).to_dict("records"),
        "curve": curve,
    }


def _curve(pf: pd.DataFrame, prices: pd.DataFrame) -> list[dict]:
    """Depotwert vs. SPY-Vergleichsdepot je Handelstag (eingesetztes Kapital = Summe der Käufe)."""
    spy = prices["SPY"].dropna()
    start = pd.Timestamp(min(pf["entry_date"]))
    days = spy.index[spy.index >= start]
    if len(days) == 0:
        return []
    val = pd.Series(0.0, index=days)
    bench = pd.Series(0.0, index=days)
    cash_in = pd.Series(0.0, index=days)
    for r in pf.itertuples():
        e = pd.Timestamp(r.entry_date)
        x = pd.Timestamp(r.exit_date) if isinstance(r.exit_date, str) else None
        if r.ticker not in prices.columns:
            continue
        # fehlende Kurse (Feiertage, Datenlücken, Delisting) mit dem letzten bekannten Kurs füllen
        s = prices[r.ticker].reindex(days).ffill().fillna(float(r.entry_price))
        sh, ssh = POSITION_USD / float(r.entry_price), POSITION_USD / float(r.spy_entry)
        live = (days >= e) & ((days <= x) if x is not None else True)
        after = (days > x) if x is not None else pd.Series(False, index=days).values
        val += pd.Series(live, index=days) * s * sh
        bench += pd.Series(live, index=days) * spy.reindex(days).ffill().bfill() * ssh
        if x is not None:
            val += pd.Series(after, index=days) * float(r.exit_price) * sh
            bench += pd.Series(after, index=days) * float(r.spy_exit) * ssh
        cash_in += pd.Series(days >= e, index=days) * POSITION_USD
    out = []
    step = max(1, len(days) // 120)  # höchstens ~120 Punkte für die Grafik
    for d in list(days[::step]) + ([days[-1]] if (len(days) - 1) % step else []):
        if cash_in[d] > 0:
            out.append({"date": d.date().isoformat(), "depot": round(float(val[d] / cash_in[d] - 1), 4),
                        "spy": round(float(bench[d] / cash_in[d] - 1), 4)})
    return out


def simulate(events: pd.DataFrame, prices: pd.DataFrame, depot: str,
             fund: pd.DataFrame | None = None) -> dict:
    """Simulation 2023–heute mit denselben Regeln auf Basis des Backtests (Politiker-Score ohne
    Fundamentaldaten, weil es keine historischen gibt). Nur als Orientierung.
    Wie im Live-Ranking nur echte Aktien (keine Fonds/ETFs) ab config.MIN_MARKET_CAP."""
    import config
    cfg = DEPOTS[depot]
    min_score, HOLD_DAYS = cfg["min"], cfg["hold"]
    score_col = f"{depot}_proxy"
    ok = None
    if fund is not None and len(fund):
        f = fund.reindex(events["ticker"].unique())
        ok = set(f[(f["quoteType"] == "EQUITY") & (f["marketCap"] >= config.MIN_MARKET_CAP)].index)
    ev = events[(events[score_col] >= min_score) & ~events["small_cap"].fillna(False)]
    if ok is not None:
        ev = ev[ev["ticker"].isin(ok)]
    ev = ev.sort_values("filed")
    rows, held_until = [], {}
    spy = prices["SPY"]
    for r in ev.itertuples():
        if r.ticker not in prices.columns or held_until.get(r.ticker, dt.date.min) >= r.filed:
            continue
        d, p = _close_after(prices[r.ticker], r.filed)
        _, sp = _close_after(spy, r.filed)
        if not d:
            continue
        xd, xp = _close_on_or_after(prices[r.ticker], d + dt.timedelta(days=HOLD_DAYS))
        _, xsp = _close_on_or_after(spy, d + dt.timedelta(days=HOLD_DAYS))
        rows.append({"ticker": r.ticker, "name": r.ticker, "signal_date": r.filed.isoformat(), "score": getattr(r, score_col),
                     "entry_date": d.isoformat(), "entry_price": p, "spy_entry": sp,
                     "exit_date": xd.isoformat() if xd else None, "exit_price": xp, "spy_exit": xsp,
                     "status": "geschlossen" if xd else "offen"})
        held_until[r.ticker] = d + dt.timedelta(days=HOLD_DAYS)
    pf = pd.DataFrame(rows, columns=COLS)
    res = summary(pf, prices)
    res["positions"] = res["positions"][:200]
    os.makedirs(os.path.dirname(cfg["sim"]), exist_ok=True)
    with open(cfg["sim"], "w") as f:
        json.dump(res, f, default=lambda o: None if isinstance(o, float) and math.isnan(o) else str(o))
    return res


def load_sim(depot: str) -> dict | None:
    f = DEPOTS[depot]["sim"]
    if os.path.exists(f):
        with open(f) as fh:
            return json.load(fh)
    return None


def update_from_trades(trades: pd.DataFrame, prices: pd.DataFrame, persons: set[str], log=print) -> pd.DataFrame:
    """Top-Trader-Depot: kauft jeden neu gemeldeten Kauf der Top-Trader nach (Ticker nur einmal gleichzeitig)."""
    depot = "top"
    cfg = DEPOTS[depot]
    pf = _load(depot)
    today = dt.date.today()
    first_run = pf.empty
    held = set(pf.loc[pf["status"].isin(["offen", "wartet"]), "ticker"])
    known = set(zip(pf["ticker"], pf["signal_date"].astype(str)))
    b = trades[(trades["type"] == "Kauf") & trades["person"].isin(persons) & trades["ticker"].notna()
               & trades["asset_type"].isin(["ST", "OP"])]
    # beim ersten Lauf nur Meldungen der letzten 7 Tage übernehmen, sonst gilt "ab heute"
    b = b[b["filed"] >= today - dt.timedelta(days=7 if first_run else 30)]
    rows = []
    for _, r in b.sort_values("filed").iterrows():
        key = (r["ticker"], r["filed"].isoformat())
        if r["ticker"] in held or key in known:
            continue
        rows.append({"ticker": r["ticker"], "name": f"{r['ticker']} ({r['person']})",
                     "signal_date": r["filed"].isoformat(), "score": None, "status": "wartet"})
        held.add(r["ticker"])
    if rows:
        pf = pd.concat([pf, pd.DataFrame(rows)], ignore_index=True)
        log(f"  {cfg['label']}: {len(rows)} neue Käufe ({', '.join(x['ticker'] for x in rows)})")
    os.makedirs(os.path.dirname(cfg["file"]), exist_ok=True)
    pf[COLS].to_csv(cfg["file"], index=False)
    # Einstieg/Ausstieg wie bei den anderen Depots (Ranking leer -> keine Score-Signale)
    return update(pd.DataFrame(), prices, depot, log)


def simulate_trades(history: pd.DataFrame, prices: pd.DataFrame, persons: set[str]) -> dict:
    """Simulation 2023–heute: jeden Kauf der (heutigen) Top-Trader am Tag nach der Meldung
    nachkaufen, 1 Jahr halten. Achtung: Die Auswahl der Top-Trader kennt ihr späteres Ergebnis
    (Rückschau-Effekt) – die Simulation ist deshalb zu optimistisch und nur zur Orientierung."""
    cfg = DEPOTS["top"]
    b = history[(history["type"] == "Kauf") & history["person"].isin(persons) & history["ticker"].notna()
                & history["asset_type"].isin(["ST", "OP"]) & history["filed"].notna()].sort_values("filed")
    rows, held_until, spy = [], {}, prices["SPY"]
    for r in b.itertuples():
        if r.ticker not in prices.columns or held_until.get(r.ticker, dt.date.min) >= r.filed:
            continue
        d, p = _close_after(prices[r.ticker], r.filed)
        _, sp = _close_after(spy, r.filed)
        if not d or not sp:
            continue
        xd, xp = _close_on_or_after(prices[r.ticker], d + dt.timedelta(days=cfg["hold"]))
        _, xsp = _close_on_or_after(spy, d + dt.timedelta(days=cfg["hold"]))
        rows.append({"ticker": r.ticker, "name": f"{r.ticker} ({r.person})", "signal_date": r.filed.isoformat(),
                     "score": None, "entry_date": d.isoformat(), "entry_price": p, "spy_entry": sp,
                     "exit_date": xd.isoformat() if xd else None, "exit_price": xp, "spy_exit": xsp,
                     "status": "geschlossen" if xd else "offen"})
        held_until[r.ticker] = d + dt.timedelta(days=cfg["hold"])
    res = summary(pd.DataFrame(rows, columns=COLS), prices)
    res["positions"] = res.get("positions", [])[:200]
    with open(cfg["sim"], "w") as f:
        json.dump(res, f, default=lambda o: None if isinstance(o, float) and math.isnan(o) else str(o))
    return res
