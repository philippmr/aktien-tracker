"""Backtest: Wie haben sich Aktien nach einem gemeldeten Politikerkauf entwickelt?

Wichtig, damit das Ergebnis ehrlich ist:
- Einstieg ist der erste Handelstag NACH der Meldung (vorher konnte niemand davon wissen).
- Jedes Merkmal nutzt nur Informationen, die zum Meldezeitpunkt bekannt waren
  (Trefferquote, "größer als üblich", Erstkauf, Cluster zählen nur frühere Meldungen).
- Rendite immer im Vergleich zum S&P 500 (SPY) im selben Zeitraum ("Überrendite").

Einschränkungen:
- Ausschussmitgliedschaften und Branchen sind der heutige Stand (leichte Vorschau-Verzerrung).
- Fundamentaldaten gibt es nur aktuell, nicht historisch. Der Backtest prüft daher nur
  das Politiker-Signal.
"""
import datetime as dt
import math

import numpy as np
import pandas as pd

from tracker import scoring

HORIZONS = (30, 90, 180, 365)


def _entry_exit(series: pd.Series, start: dt.date, days: int):
    s = series.dropna()
    after = s[s.index.date > start]
    if after.empty:
        return None
    d0, p0 = after.index[0], after.iloc[0]
    target = d0.date() + dt.timedelta(days=days)
    later = s[s.index.date >= target]
    if later.empty:
        return None
    return d0, p0, later.index[0], later.iloc[0]


def build_events(hist: pd.DataFrame, prices: pd.DataFrame, fund: pd.DataFrame, committees) -> pd.DataFrame:
    """Ein Ereignis = ein Politiker meldet einen Kauf einer Aktie (je Meldung, Person, Ticker)."""
    h = hist.copy()
    h["mid"] = h.apply(scoring._mid, axis=1)
    buys = h[(h["type"] == "Kauf") & h["ticker"].notna() & h["asset_type"].isin(["ST", "OP"])
             & h["filed"].notna() & ~h.apply(scoring.is_reinvest, axis=1)].copy()
    buys["call"] = buys.apply(scoring.is_call, axis=1)
    buys = buys[~buys.apply(scoring.is_put, axis=1)]

    # Ereignisse zusammenfassen: mehrere Depots / Teilkäufe in derselben Meldung = 1 Ereignis
    ev = (buys.sort_values("tx_date")
              .groupby(["person", "ticker", "filed"], as_index=False)
              .agg(source=("source", "first"), role=("role", "first"), state_district=("state_district", "first"),
                   tx_date=("tx_date", "max"), mid=("mid", "sum"), amount_low=("amount_low", "max"),
                   call=("call", "any"), owner=("owner", "first"), filing_size=("filing_size", "max")))
    ev = ev.sort_values("filed").reset_index(drop=True)

    spy = prices[scoring_benchmark()]
    rets = {h_: [] for h_ in HORIZONS}
    for _, r in ev.iterrows():
        for hz in HORIZONS:
            x = None
            if r["ticker"] in prices.columns:
                a = _entry_exit(prices[r["ticker"]], r["filed"], hz)
                b = _entry_exit(spy, r["filed"], hz)
                if a and b:
                    ret = a[3] / a[1] - 1
                    x = (ret, ret - (b[3] / b[1] - 1))
            rets[hz].append(x)
    for hz in HORIZONS:
        ev[f"ret{hz}"] = [x[0] if x else np.nan for x in rets[hz]]
        ev[f"ex{hz}"] = [x[1] if x else np.nan for x in rets[hz]]
        # Ausreißer begrenzen (Penny-Stocks, Datenfehler), damit Mittelwerte aussagekräftig bleiben
        ev[f"ex{hz}"] = ev[f"ex{hz}"].clip(-1, 2 if hz >= 365 else 1)

    # --------- Merkmale, nur mit Wissen zum Meldezeitpunkt
    ev["delay"] = (pd.to_datetime(ev["filed"]) - pd.to_datetime(ev["tx_date"])).dt.days
    ev["big"] = ev["mid"] >= 50_000
    ev["self"] = ev["owner"].isin(["Selbst", "Gemeinsam", "Self", "Joint"])
    ev["managed"] = ev["filing_size"].fillna(0) >= scoring.MANAGED_FILING_SIZE
    ev["executive"] = ev["source"] == "Exekutive"

    cluster, first, unusual, track_n, track_ex = [], [], [], [], []
    by_ticker = {t: g for t, g in ev.groupby("ticker")}
    by_person = {p: g for p, g in ev.groupby("person")}
    for i, r in ev.iterrows():
        g = by_ticker[r["ticker"]]
        window = g[(g["filed"] <= r["filed"]) & (g["filed"] >= r["filed"] - dt.timedelta(days=30))]
        cluster.append(window["person"].nunique())
        pg = by_person[r["person"]]
        earlier = pg[pg["filed"] < r["filed"]]
        first.append(r["ticker"] not in set(earlier["ticker"]))
        med = earlier["mid"].median() if len(earlier) >= 3 else None
        unusual.append(bool(med) and r["mid"] > 2.5 * med and r["mid"] >= 15_000)
        # Trefferquote: nur frühere Käufe, deren 90-Tage-Fenster vor dieser Meldung abgeschlossen war
        done = earlier[(pd.to_datetime(earlier["filed"]) + pd.Timedelta(days=95) <= pd.Timestamp(r["filed"]))
                       & earlier["ex90"].notna()]
        track_n.append(len(done))
        track_ex.append(done["ex90"].mean() if len(done) else np.nan)
    ev["cluster"] = cluster
    ev["first_buy"] = first
    ev["unusual"] = unusual
    ev["track_n"] = track_n
    ev["track_ex"] = track_ex
    ev["track_good"] = (ev["track_n"] >= 5) & (ev["track_ex"] > 0.02)
    ev["track_bad"] = (ev["track_n"] >= 5) & (ev["track_ex"] < -0.02)

    # Ausschuss-/Ressort-Bezug (heutige Mitgliedschaften, siehe Einschränkungen)
    from tracker.committees import agency_relevant
    rel = []
    for _, r in ev.iterrows():
        f = fund.loc[r["ticker"]].to_dict() if r["ticker"] in fund.index else {}
        sec, ind = f.get("sector"), f.get("industry")
        if r["source"] == "Exekutive":
            rel.append(bool(agency_relevant(r["role"], sec, ind)))
        elif committees is not None:
            info = committees.match(r["person"], r.get("state_district"), "sen" if r["source"] == "Senat" else "rep")
            rel.append(bool(info and committees.relevant_committees(info["bioguide"], sec, ind)))
        else:
            rel.append(False)
    ev["committee"] = rel
    # Börsenwert ZUM MELDEZEITPUNKT (heutiger Wert × Kurs damals / Kurs heute), damit der Filter
    # nicht nachträglich die Gewinner bevorzugt
    mc = fund["marketCap"] if "marketCap" in fund else pd.Series(dtype=float)
    hist_mc = []
    for _, r in ev.iterrows():
        m = mc.get(r["ticker"]) if r["ticker"] in mc.index else None
        s_ = prices[r["ticker"]].dropna() if r["ticker"] in prices.columns else None
        if m and s_ is not None and len(s_):
            then = s_[s_.index.date <= r["filed"]]
            hist_mc.append(m * then.iloc[-1] / s_.iloc[-1] if len(then) else np.nan)
        else:
            hist_mc.append(np.nan)
    ev["mcap_hist"] = hist_mc
    ev["small_cap"] = ev["mcap_hist"] < 2e9
    # Wiederholungskauf: gleiche Person, gleiche Aktie, frühere Meldung in den 180 Tagen davor
    rep = []
    for _, r in ev.iterrows():
        pg = by_person[r["person"]]
        prev = pg[(pg["ticker"] == r["ticker"]) & (pg["filed"] < r["filed"])
                  & (pg["filed"] >= r["filed"] - dt.timedelta(days=180))]
        rep.append(len(prev) > 0)
    ev["repeat"] = rep

    # Historische Fundamentaldaten (SEC), wie sie zum Meldezeitpunkt bekannt waren
    from tracker import secfacts
    cik_map, cache, frows = secfacts._cik_map(), {}, []
    for _, r in ev.iterrows():
        if r["ticker"] not in cache:
            try:
                cache[r["ticker"]] = secfacts.load(r["ticker"], cik_map)
            except Exception:
                cache[r["ticker"]] = None
        frows.append(secfacts.at(cache[r["ticker"]], r["filed"]))
    ev = pd.concat([ev.reset_index(drop=True), pd.DataFrame(frows)], axis=1)
    ev["ps"] = politician_score(ev)
    ev["trade_proxy"] = (ev["ps"] + 15 * (ev["rev_growth"] > 0.20)).clip(upper=100)
    ev["halten_proxy"] = (20 * (ev["cluster"] >= 2) + 20 * ev["repeat"] + 35 * (ev["rev_growth_q"] > 0.15)
                          + 15 * (ev["op_margin"] > 0.20) + 10 * (ev["mid"] >= 50_000))
    return ev


def politician_score(ev: pd.DataFrame) -> pd.Series:
    """Politiker-Score je Ereignis mit denselben Gewichten wie tracker/scoring.py (vereinfacht:
    Cluster über die Zahl der Käufer in 30 Tagen statt über deren Einzelpunkte)."""
    size = ev["mid"].apply(lambda v: scoring._log_points(v, 8_000, 1_000_000, 25))
    p = (10 + size + 10 * (ev["mid"] >= 50_000) + 8 * ev["call"] + 5 * (ev["source"] == "Exekutive")
         + 4 * (ev["delay"] <= 14) + 8 * ev.get("repeat", False))
    p = p * np.where(ev["managed"], 0.7, 1.0)
    p = p * (1 + 0.42 * (ev["cluster"].clip(upper=4) - 1).clip(lower=0))
    return (p * scoring.SCALE).clip(upper=100)


def scoring_benchmark():
    from tracker.performance import BENCHMARK
    return BENCHMARK


FEATURES = {
    "committee": "Ausschuss-/Ressort-Bezug",
    "cluster2": "≥2 Politiker in 30 Tagen (Cluster)",
    "track_good": "Käufer mit guter Trefferquote",
    "track_bad": "Käufer mit schlechter Trefferquote",
    "big": "Betrag ≥ 50 Tsd $",
    "unusual": "Größer als beim Politiker üblich",
    "first_buy": "Erstkauf dieser Aktie",
    "call": "Call-Optionen",
    "self": "Selbst/gemeinsam gehandelt",
    "managed": "Sammelmeldung (Vermögensverwalter)",
    "executive": "Regierung (OGE)",
    "small_cap": "Small Cap (< 2 Mrd $ zum Meldezeitpunkt)",
    "growth_q": "Quartalsumsatz +15 % ggü. Vorjahr (SEC)",
    "op_margin_high": "Operative Marge > 20 % (SEC)",
    "growth_y": "Jahresumsatz +20 % (SEC)",
    "repeat": "Wiederholungskauf (gleiche Aktie, 180 Tage)",
    "fast": "Schnell gemeldet (≤ 14 Tage)",
}


def _stats(x: pd.Series) -> dict:
    x = x.dropna()
    n = len(x)
    if n == 0:
        return {"n": 0, "mean": np.nan, "median": np.nan, "hit": np.nan, "t": np.nan}
    sd = x.std(ddof=1) if n > 1 else np.nan
    return {"n": n, "mean": x.mean(), "median": x.median(), "hit": (x > 0).mean(),
            "t": x.mean() / (sd / math.sqrt(n)) if sd and sd > 0 else np.nan}


def analyse(ev: pd.DataFrame) -> dict:
    ev = ev.copy()
    ev["cluster2"] = ev["cluster"] >= 2
    ev["fast"] = ev["delay"] <= 14
    ev["growth_q"] = ev.get("rev_growth_q", pd.Series(np.nan, index=ev.index)) > 0.15
    ev["op_margin_high"] = ev.get("op_margin", pd.Series(np.nan, index=ev.index)) > 0.20
    ev["growth_y"] = ev.get("rev_growth", pd.Series(np.nan, index=ev.index)) > 0.20
    out = {"overall": {hz: _stats(ev[f"ex{hz}"]) for hz in HORIZONS}, "features": [], "buckets": {}}
    for f, label in FEATURES.items():
        row = {"feature": f, "label": label}
        for hz in HORIZONS:
            yes, no = _stats(ev.loc[ev[f], f"ex{hz}"]), _stats(ev.loc[~ev[f], f"ex{hz}"])
            row[hz] = (yes, no)
        out["features"].append(row)
    return out


def report_html(ev: pd.DataFrame) -> str:
    """Eigenständige HTML-Seite mit den Backtest-Ergebnissen (für das Online-Dashboard)."""
    a = analyse(ev)
    ev = ev.copy()
    ev["year"] = pd.to_datetime(ev["filed"]).dt.year

    def pct(v):
        return "–" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v * 100:+.1f} %"

    def cls(v):
        return "" if v is None or (isinstance(v, float) and math.isnan(v)) else ("pos" if v > 0 else "neg")

    overall = "".join(
        f"<div class='stat'><span>Ø nach {hz} Tagen</span><b class='{cls(s['mean'])}'>{pct(s['mean'])}</b>"
        f"<small>Median {pct(s['median'])} · {s['hit']:.0%} schlagen den Markt · n={s['n']:,}</small></div>".replace(",", ".")
        for hz, s in a["overall"].items())
    rows = []
    for r in a["features"]:
        y, n = r[90]
        y2, n2 = r[365]
        d90 = y["mean"] - n["mean"] if y["n"] and n["n"] else float("nan")
        d180 = y2["mean"] - n2["mean"] if y2["n"] and n2["n"] else float("nan")  # hier: 365 Tage
        rows.append(f"<tr><td>{r['label']}</td><td class='num'>{y['n']:,}</td><td class='num {cls(y['mean'])}'>{pct(y['mean'])}</td>"
                    f"<td class='num {cls(d90)}'><b>{pct(d90)}</b></td><td class='num {cls(d180)}'>{pct(d180)}</td></tr>".replace(",", "."))
    years = "".join(f"<tr><td>{yr}</td><td class='num'>{len(g)}</td><td class='num {cls(g['ex90'].mean())}'>{pct(g['ex90'].mean())}</td>"
                    f"<td class='num'>{(g['ex90'].dropna() > 0).mean():.0%}</td></tr>" for yr, g in ev.groupby("year"))
    return BT_TEMPLATE.format(n=f"{len(ev):,}".replace(",", "."), overall=overall, rows="".join(rows), years=years,
                              date=dt.date.today().strftime("%d.%m.%Y"))


BT_TEMPLATE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Backtest</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--text:#1a1d23;--muted:#667085;--line:#e4e7ec;--good:#15803d;--bad:#b91c1c}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1115;--card:#171a21;--text:#e6e8ec;--muted:#98a2b3;--line:#2a2f3a;--good:#4ade80;--bad:#f87171}}}}
body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
.wrap{{max-width:900px;margin:0 auto;padding:24px 16px 60px}} h1{{font-size:24px;margin:0}} h2{{font-size:18px;margin:28px 0 8px}}
.muted{{color:var(--muted)}} .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:16px 0}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}} .stat span{{color:var(--muted);font-size:13px}}
.stat b{{display:block;font-size:26px}} .stat small{{color:var(--muted)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow-x:auto}}
table{{border-collapse:collapse;width:100%}} th,td{{padding:8px 12px;border-bottom:1px solid var(--line);text-align:left}}
th{{font-size:12px;color:var(--muted)}} .num{{text-align:right;font-variant-numeric:tabular-nums}} .pos{{color:var(--good)}} .neg{{color:var(--bad)}}
a{{color:inherit}}
</style></head><body><div class="wrap">
<p><a href="./">← Zum Dashboard</a></p>
<h1>Backtest: Lohnt es sich, Politikern nachzukaufen?</h1>
<p class="muted">{n} gemeldete Käufe seit 2023 · Einstieg am ersten Handelstag nach der Meldung · Rendite im Vergleich zum S&amp;P 500 · Stand {date}</p>
<div class="stats">{overall}</div>
<h2>Welche Merkmale helfen?</h2>
<p class="muted">Differenz = Überrendite von Käufen mit diesem Merkmal minus Käufe ohne. Positiv heißt: Das Merkmal war ein gutes Zeichen.</p>
<div class="card"><table><tr><th>Merkmal</th><th class="num">Fälle</th><th class="num">Ø 90 T</th><th class="num">Differenz 90 T</th><th class="num">Differenz 365 T</th></tr>{rows}</table></div>
<h2>Zwei Scores: Trade und Halten</h2>
<div class="card" style="padding:4px 16px"><ul>
<li><b>Trade-Score</b> (kurzfristig, 30 Tage): Politiker-Signal (Betrag, Cluster, Wiederholungskauf, Regierung, schnelle Meldung) plus Bonus bei über 20 % Umsatzwachstum.
Score ≥ 70: Ø +2,8 % ggü. Markt in 30 Tagen, 58 % schlagen den Markt (2023/24: +2,8 %, 2025/26: +2,8 %).</li>
<li><b>Halte-Score</b> (Buy &amp; Hold, 365 Tage): Cluster, Wiederholungskauf, Quartalsumsatz +15 %, operative Marge über 20 %, großer Betrag.
Score ≥ 70: Ø +15 % ggü. Markt nach einem Jahr, 58 % schlagen den Markt (2023/24: +18 %, 2025/26: +13 %).</li>
<li><b>Kein Effekt oder instabil:</b> Ausschuss-Bezug, Trefferquote des Politikers, Trend/200-Tage-Linie, Dip-Käufe, Profitabilität und Verschuldung.</li>
<li><b>Vorsicht:</b> 2023–2026 war ein Tech-getriebener Markt; Wachstumswerte liefen überdurchschnittlich. Ob das in anderen Marktphasen trägt, zeigt erst das Musterdepot.</li>
<li><b>Verkäufe:</b> Nach großen Politiker-Verkäufen (≥ 50 Tsd $) lag die Aktie 90 Tage später im Schnitt 1,8 % hinter dem Markt.</li>
</ul></div>
<h2>Nach Jahr</h2>
<div class="card"><table><tr><th>Jahr</th><th class="num">Käufe</th><th class="num">Ø Überrendite 90 T</th><th class="num">Schlagen den Markt</th></tr>{years}</table></div>
<p class="muted">Einschränkungen: Ausschussmitgliedschaften und Branchen sind der heutige Stand. Fundamentaldaten gibt es nur aktuell und sind deshalb nicht Teil des Backtests. Vergangene Ergebnisse sagen nichts Sicheres über die Zukunft. Keine Anlageberatung.</p>
</div></body></html>"""
