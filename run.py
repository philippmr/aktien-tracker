"""Politiker-Insider-Tracker: Aktienkäufe von US-Kongressmitgliedern und Regierungsmitgliedern screenen,
mit Ausschuss-Bezug, Trefferquote und Fundamentaldaten bewerten.

Aufruf:
    python run.py                     # Standard aus config.py
    python run.py --days 30           # nur die letzten 30 Tage gemeldeter Trades ranken
    python run.py --email             # zusätzlich Mail mit neuen Trades seit dem letzten Lauf
    python run.py --email-preview     # Mail nur als HTML-Vorschau erzeugen
"""
import argparse
import datetime as dt
import os
import time

import pandas as pd

import config
from tracker import alerts, fundamentals, performance, portfolio, report, scoring
from tracker.committees import Committees
from tracker.sources import house, oge, senate

COLUMNS = ["source", "person", "role", "state_district", "filed", "url", "filing_id", "filing_size",
           "ticker", "asset_type", "asset", "owner", "type", "tx_date", "amount_low", "amount_high",
           "description"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=config.CONGRESS_LOOKBACK_DAYS)
    ap.add_argument("--email", action="store_true",
                    help="Mail mit neuen Trades seit dem letzten Lauf verschicken (Gmail-Daten aus Umgebungsvariablen)")
    ap.add_argument("--auto", action="store_true",
                    help="Für den Zeitplan (alle 3 h): Sofort-Alarm, Tagesmail ab 6 Uhr, Wochenrückblick montags")
    ap.add_argument("--screener", action="store_true", help="Chancen-Screener neu rechnen, auch wenn er heute schon lief")
    ap.add_argument("--backtest", action="store_true",
                    help="Backtest rechnen und output/backtest.html schreiben (dauert einige Minuten)")
    ap.add_argument("--email-test", action="store_true",
                    help="Beispiel-Mail mit allen Käufen der letzten 10 Tage senden (ändert den Status nicht)")
    ap.add_argument("--email-preview", action="store_true",
                    help="Mail nur als output/mail_preview.html erzeugen, nichts verschicken, Status nicht speichern")
    args = ap.parse_args()
    t0 = time.time()
    today = dt.date.today()
    history_days = (today - dt.date(config.HISTORY_START_YEAR, 1, 1)).days

    print("1/5 Politiker-Trades laden (inkl. Historie seit", config.HISTORY_START_YEAR, ")")
    trades = house.fetch(history_days)
    senate_csv = senate.from_csv(history_days)   # vom eigenen Rechner (senate_sync.py)
    trades += senate_csv
    if not senate_csv:
        try:
            trades += senate.fetch(history_days)
        except Exception as e:  # Senats-Seite ist fragil, soll den Lauf nicht abbrechen
            print(f"  Senat: Fehler ({e}), übersprungen")
    try:
        trades += oge.fetch(history_days)
    except Exception as e:
        print(f"  OGE: Fehler ({e}), übersprungen")
    hist = pd.DataFrame(trades).reindex(columns=COLUMNS)
    hist = hist[hist["tx_date"].notna()]
    since = today - dt.timedelta(days=args.days)
    current = hist[hist["filed"] >= since]
    print(f"  {len(hist)} Trades gesamt, {len(current)} in den letzten {args.days} Tagen gemeldet")

    print("2/5 Ausschüsse laden")
    try:
        committees = Committees()
    except Exception as e:
        print(f"  Ausschussdaten nicht verfügbar ({e}), ohne Ausschuss-Bezug weiter")
        committees = None

    print("3/5 Trefferquote je Politiker berechnen")
    hist_buys = hist[(hist["type"] == "Kauf") & (hist["asset_type"] == "ST") & hist["ticker"].notna()]
    cur_tickers = sorted(current.loc[current["ticker"].notna() &
                                     current["asset_type"].isin(["ST", "OP"]), "ticker"].unique())
    prices = performance.load_prices(set(hist_buys["ticker"]) | set(cur_tickers),
                                     dt.date(config.HISTORY_START_YEAR, 1, 1))
    track = performance.track_records(hist, prices)
    good = track[track["n"] >= config.TRACK_RECORD_MIN_TRADES]
    print(f"  {len(good)} Politiker mit ≥{config.TRACK_RECORD_MIN_TRADES} bewerteten Käufen")

    if args.backtest:
        from tracker import backtest
        print("Backtest")
        bt_tickers = sorted(set(hist.loc[(hist["type"] == "Kauf") & hist["ticker"].notna(), "ticker"]))
        bt_fund = fundamentals.load(bt_tickers)
        ev = backtest.build_events(hist, prices, bt_fund, committees)
        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        with open(os.path.join(config.OUTPUT_DIR, "backtest.html"), "w", encoding="utf-8") as f:
            f.write(backtest.report_html(ev))
        print(f"  {len(ev)} Ereignisse ausgewertet -> output/backtest.html")
        for depot in ("trade", "halten"):
            sim = portfolio.simulate(ev, prices, depot, bt_fund)
            print(f"  Simulation {portfolio.DEPOTS[depot]['label']}: {sim['n']} Käufe, "
                  f"Ø Überrendite {sim.get('excess', 0) * 100:+.2f} %")

    print(f"4/5 Fundamentaldaten ({len(cur_tickers)} Aktien) & Scoring")
    fund = fundamentals.load(cur_tickers)
    sig = scoring.PoliticianSignals(hist, track, committees)
    ranking = scoring.rank(current, fund, prices, sig)
    try:
        from tracker import contracts
        ranking = contracts.annotate(ranking, current)
    except Exception as e:  # Zusatzinfo, soll den Lauf nie abbrechen
        print(f"  Staatsaufträge: übersprungen ({e})")

    print("5/5 Dashboard schreiben")
    leaderboard = good.sort_values("avg_excess", ascending=False).reset_index()
    stats = {
        "Gemeldete Trades": len(current),
        "Davon Käufe": int((current["type"] == "Kauf").sum()),
        "Politiker aktiv": current["person"].nunique(),
        "Bewertete Aktien": len(ranking),
        "Zeitraum": f"letzte {args.days} Tage",
    }
    # Top-Trader: großes Volumen + guter Track Record (ab Meldung, 1 Jahr)
    tops_df = performance.top_traders(hist, prices)
    tops = set(tops_df.loc[tops_df["is_top"], "person"]) if len(tops_df) else set()
    print(f"  Top-Trader: {', '.join(sorted(tops)) or 'keine'}")
    if args.backtest and tops:
        sim = portfolio.simulate_trades(hist, prices, tops)
        print(f"  Simulation Top-Trader-Depot: {sim['n']} Käufe, Ø Überrendite {sim.get('excess', 0) * 100:+.2f} %")

    depots = {}
    for depot, cfg in portfolio.DEPOTS.items():
        # nur der Zeitplan-Lauf führt die Musterdepots fort
        if depot == "top":
            pf = portfolio.update_from_trades(current, prices, tops) if args.auto else portfolio._load(depot)
        else:
            pf = portfolio.update(ranking, prices, depot) if args.auto else portfolio._load(depot)
        depots[depot] = {"label": cfg["label"], "min": cfg["min"], "hold": cfg["hold"],
                         "live": portfolio.summary(pf, prices), "sim": portfolio.load_sim(depot)}
    chances = {}
    if config.SCREENER_ENABLED or args.screener:
        print("Chancen-Screener (USA + Europa, einmal täglich)")
        from tracker import screener
        pol = set(current.loc[current["type"] == "Kauf", "ticker"].dropna())
        try:
            chances = screener.daily(pol, force=args.screener)
        except Exception as e:  # Screener-Fehler sollen Politiker-Teil und Mails nicht verhindern
            print(f"  Screener: Fehler ({e}), übersprungen")
    print("Dividenden-Kaufzone & Crash-Check")
    div, crash_st = None, []
    try:
        from tracker import dividends
        div = dividends.scan()
    except Exception as e:  # Zusatzmodul, soll den Lauf nie abbrechen
        print(f"  Dividenden: Fehler ({e}), übersprungen")
    try:
        from tracker import crash
        crash_st = crash.status()
        print("  " + " · ".join(f"{r['name']} {r['drawdown'] * 100:.1f} %" for r in crash_st))
    except Exception as e:
        print(f"  Crash-Check: Fehler ({e}), übersprungen")
    path = report.write(ranking, current, leaderboard, stats, depots, chances,
                        top_traders=tops_df.to_dict("records") if len(tops_df) else [],
                        dividends=div.to_dict("records") if div is not None and len(div) else [], crash=crash_st)
    if args.auto:
        print("Automatik (Sofort-Alarm / Tagesmail / Wochenrückblick)")
        alerts.auto(current, ranking, path, prices, chances, top_traders=tops, div=div, crash_st=crash_st)
    elif args.email_test:
        print("Test-Mail")
        alerts.run(current, ranking, path, test=True, chances=chances)
    elif args.email or args.email_preview:
        print("Mail-Zusammenfassung")
        alerts.run(current, ranking, path, dry_run=args.email_preview, chances=chances)
    print(f"\nFertig in {time.time() - t0:.0f}s -> {path}")
    if len(ranking):
        cols = ["ticker", "name", "score", "insider_score", "fundamental_score", "n_buyers", "committee"]
        print(ranking[cols].head(15).to_string(index=False, max_colwidth=40))


if __name__ == "__main__":
    main()
