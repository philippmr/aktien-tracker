"""Tägliche Mail-Zusammenfassung: neue Politiker-Käufe seit dem letzten Lauf.

Welche Trades schon gemeldet wurden, steht in state/seen.json. Diese Datei wird im
GitHub-Actions-Workflow zurück ins Repo committet, damit nichts doppelt kommt.

Zugangsdaten kommen ausschließlich aus Umgebungsvariablen (bzw. GitHub Secrets):
    GMAIL_USER          Absender, z. B. dein.name@gmail.com
    GMAIL_APP_PASSWORD  16-stelliges App-Passwort (nicht dein normales Passwort!)
    MAIL_TO             Empfänger (mehrere mit Komma trennen); Standard = GMAIL_USER
    DASHBOARD_URL       optional: Link zum Dashboard, der in der Mail erscheint
"""
import datetime as dt
import html
import json
import os
import smtplib
from email.message import EmailMessage

import pandas as pd

STATE_FILE = os.path.join("state", "seen.json")
META_FILE = os.path.join("state", "meta.json")        # letzte Tages-/Wochenmail, Sofort-Alarme
SIGNAL_LOG = os.path.join("state", "signals.csv")     # Signale je Tag -> Wochenrückblick
STRONG_SCORE = 50
INSTANT_SCORE = 70   # ab diesem Score kommt sofort eine Extra-Mail
DIGEST_HOUR = 6      # Tagesmail beim ersten Lauf ab dieser Uhrzeit (deutsche Zeit)


def watchlist() -> list[str]:
    """Deine Aktien aus dem GitHub-Secret WATCHLIST, z. B. "AAPL,MSFT,NVDA" (bleibt privat)."""
    return [t.strip().upper() for t in os.environ.get("WATCHLIST", "").split(",") if t.strip()]


def load_meta() -> dict:
    if os.path.exists(META_FILE):
        with open(META_FILE) as f:
            return json.load(f)
    return {}


def save_meta(meta: dict) -> None:
    os.makedirs(os.path.dirname(META_FILE), exist_ok=True)
    with open(META_FILE, "w") as f:
        json.dump(meta, f, indent=1)
NEW_WINDOW_DAYS = 7  # nur Trades, die höchstens so viele Tage vor dem Lauf gemeldet wurden


def trade_key(r) -> str:
    return "|".join(str(r.get(k, "")) for k in
                    ("source", "filing_id", "person", "ticker", "tx_date", "type", "amount_low", "owner"))


def load_seen() -> set[str] | None:
    if not os.path.exists(STATE_FILE):
        return None  # erster Lauf
    with open(STATE_FILE) as f:
        return set(json.load(f)["seen"])


def save_seen(keys: set[str]) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump({"updated": dt.datetime.now().isoformat(timespec="seconds"),
                   "seen": sorted(keys)}, f, indent=0)


def new_trades(current: pd.DataFrame) -> tuple[pd.DataFrame, set[str]]:
    """Gibt (neue Trades, alle aktuellen Keys) zurück. Beim allerersten Lauf gelten nur Trades
    als neu, die in den letzten 3 Tagen gemeldet wurden (sonst käme eine Riesen-Mail)."""
    keys = current.apply(lambda r: trade_key(r.to_dict()), axis=1)
    seen = load_seen()
    if seen is None:
        recent = current["filed"] >= dt.date.today() - dt.timedelta(days=3)
        return current[recent], set(keys)
    # Nur kürzlich gemeldete Trades: verhindert eine Riesen-Mail, wenn eine neue Quelle
    # dazukommt oder ein Lauf länger ausgefallen ist.
    recent = current["filed"] >= dt.date.today() - dt.timedelta(days=NEW_WINDOW_DAYS)
    mask = ~keys.isin(seen) & recent
    return current[mask], seen | set(keys)


# --------------------------------------------------------------------------- Mail-Inhalt
def build_digest(new: pd.DataFrame, ranking: pd.DataFrame, dashboard_url: str | None,
                 chances: list[dict] | None = None) -> tuple[str, str] | None:
    """Wählt die neuen Käufe aus, sortiert nach Score und übergibt an das Newsletter-Layout."""
    from tracker import newsletter

    buys = new[(new["type"] == "Kauf") & new["ticker"].notna() & new["asset_type"].isin(["ST", "OP"])].copy()
    sells = new[new["type"].isin(["Verkauf", "Teilverkauf"])]   # Scans mit unklarer Art nicht mitzählen
    wl = set(watchlist())
    watch_sells = sells[sells["ticker"].isin(wl)]
    chances = chances or []
    if buys.empty and watch_sells.empty and not chances:
        return None  # nichts Neues -> keine Mail

    rk = ranking.set_index("ticker") if len(ranking) else pd.DataFrame()

    def info(t):
        return rk.loc[t].to_dict() if t in rk.index else {}

    def strong(t):
        i = info(t)
        return bool(i) and (i.get("score", 0) >= STRONG_SCORE or bool(i.get("call_options"))
                            or i.get("n_buyers", 0) >= 2)

    buys["_score"] = buys["ticker"].map(lambda t: info(t).get("score", -1))
    buys = buys.sort_values(["_score", "ticker", "tx_date"], ascending=[False, True, False])
    strong_t = [t for t in dict.fromkeys(buys["ticker"]) if strong(t)]
    return newsletter.build(buys, sells, info, strong_t, dashboard_url, watch_sells=watch_sells,
                            watch_buys=buys[buys["ticker"].isin(wl)], chances=chances)


def send(subject: str, body_html: str, attachment: str | None = None) -> None:
    user = os.environ["GMAIL_USER"]
    pw = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
    to = [a.strip() for a in os.environ.get("MAIL_TO", user).split(",") if a.strip()]
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(to)
    msg.set_content("Diese Mail braucht ein HTML-fähiges Mailprogramm.")
    msg.add_alternative(body_html, subtype="html")
    if attachment and os.path.exists(attachment):
        with open(attachment, "rb") as f:
            msg.add_attachment(f.read(), maintype="text", subtype="html",
                               filename=f"dashboard_{dt.date.today()}.html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(user, pw)
        s.send_message(msg)


def new_chances(chances: dict | None, limit: int = 12) -> list[dict]:
    """Neue Chancen von heute (noch nie gemeldet), beste zuerst."""
    rows = [r for r in (chances or {}).get("rows", []) if r.get("is_new")]
    return sorted(rows, key=lambda r: -r["score"])[:limit]


def run(current: pd.DataFrame, ranking: pd.DataFrame, dashboard_path: str, *, dry_run=False,
        test=False, log=print, chances: dict | None = None):
    """test=True: Beispiel-Mail mit allen Käufen der letzten 10 Tage, Status bleibt unverändert."""
    if test:
        new = current[current["filed"] >= dt.date.today() - dt.timedelta(days=10)]
        top = sorted((chances or {}).get("rows", []), key=lambda r: -r["score"])[:8]
        digest = build_digest(new, ranking, os.environ.get("DASHBOARD_URL"), top)
        if digest is None:
            log("  Test-Mail: keine Käufe in den letzten 10 Tagen")
            return
        subject = "[Test] " + digest[0]
        if dry_run:
            log(f"  Test-Mail (Vorschau): {subject}")
            return
        send(subject, digest[1], attachment=dashboard_path)
        log(f"  Test-Mail verschickt: {subject}")
        return
    new, keys = new_trades(current)
    digest = build_digest(new, ranking, os.environ.get("DASHBOARD_URL"), new_chances(chances))
    if digest is None:
        log("  Mail: keine neuen Trades, nichts verschickt")
    elif not dry_run and not (os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD")):
        # Ohne Zugangsdaten abbrechen, statt die Trades als "gemeldet" zu markieren
        raise SystemExit("GMAIL_USER / GMAIL_APP_PASSWORD fehlen - Mail nicht verschickt. "
                         "In GitHub unter Settings -> Secrets and variables -> Actions eintragen.")
    elif dry_run:
        path = os.path.join(os.path.dirname(dashboard_path), "mail_preview.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(digest[1])
        log(f"  Mail (Vorschau, nicht verschickt): {digest[0]} -> {path}")
    else:
        send(*digest, attachment=dashboard_path)
        log(f"  Mail verschickt: {digest[0]}")
    if not dry_run:
        save_seen(keys)


# --------------------------------------------------------------------------- Sofort-Alarm
def instant(current: pd.DataFrame, ranking: pd.DataFrame, meta: dict, log=print) -> None:
    """Extra-Mail, sobald ein neu gemeldeter Kauf einen Score >= INSTANT_SCORE hat."""
    from tracker import newsletter
    if ranking.empty:
        return
    hot = set(ranking.loc[ranking["score"] >= INSTANT_SCORE, "ticker"])
    recent = current[(current["filed"] >= dt.date.today() - dt.timedelta(days=NEW_WINDOW_DAYS))
                     & (current["type"] == "Kauf") & current["ticker"].isin(hot)]
    keys = recent.apply(lambda r: trade_key(r.to_dict()), axis=1) if len(recent) else pd.Series(dtype=str)
    done = set(meta.get("alerted", []))
    first_run = "alerted" not in meta
    new = recent[~keys.isin(done)] if len(recent) else recent
    meta["alerted"] = sorted(done | set(keys))[-5000:]
    if first_run or new.empty:
        return  # beim allerersten Lauf nur merken, nicht alarmieren
    subject, body = build_digest(new, ranking, os.environ.get("DASHBOARD_URL"))
    top = ", ".join(dict.fromkeys(new["ticker"]))
    send(f"⚡ Sofort-Alarm: {top} (Score ≥ {INSTANT_SCORE})", body)
    log(f"  Sofort-Alarm verschickt: {top}")


# --------------------------------------------------------------------------- Wochenrückblick
def log_signals(ranking: pd.DataFrame) -> None:
    """Merkt sich täglich die starken Signale mit Kurs, um sie später auszuwerten."""
    if ranking.empty:
        return
    today = dt.date.today().isoformat()
    top = ranking[ranking["score"] >= STRONG_SCORE][["ticker", "name", "score", "price"]].copy()
    top.insert(0, "date", today)
    old = pd.read_csv(SIGNAL_LOG) if os.path.exists(SIGNAL_LOG) else pd.DataFrame(columns=top.columns)
    if today in set(old["date"].astype(str)):
        return
    # pro Ticker nur die ERSTE Signalmeldung behalten (ab dann zählt die Performance)
    combined = pd.concat([old, top[~top["ticker"].isin(set(old["ticker"]))]], ignore_index=True)
    os.makedirs(os.path.dirname(SIGNAL_LOG), exist_ok=True)
    combined.to_csv(SIGNAL_LOG, index=False)


def weekly_review(prices_now: dict, spy_hist: pd.Series, pf: dict | None = None) -> tuple[str, str] | None:
    """Wie haben sich die starken Signale seit ihrer Meldung entwickelt (vs. S&P 500)?"""
    from tracker import newsletter
    if not os.path.exists(SIGNAL_LOG):
        return newsletter.weekly(pd.DataFrame(), pf) if pf and any(v.get("n") for v in pf.values()) else None
    log_ = pd.read_csv(SIGNAL_LOG, parse_dates=["date"])
    log_ = log_[log_["date"] >= pd.Timestamp.today() - pd.Timedelta(days=180)]
    rows = []
    for _, r in log_.iterrows():
        now = prices_now.get(r["ticker"])
        if not now or not r["price"] or pd.isna(r["price"]):
            continue
        spy0 = spy_hist[spy_hist.index >= r["date"]]
        spy_ret = (spy_hist.iloc[-1] / spy0.iloc[0] - 1) if len(spy0) else 0
        ret = now / r["price"] - 1
        rows.append({**r.to_dict(), "ret": ret, "excess": ret - spy_ret,
                     "days": (pd.Timestamp.today() - r["date"]).days})
    if not rows and not (pf and any(v.get("n") for v in pf.values())):
        return None
    return newsletter.weekly(pd.DataFrame(rows), pf)


# --------------------------------------------------------------------------- Fehler-Mail
def send_failure(run_url: str) -> None:
    from tracker import newsletter
    send("❗ Capitol Radar: Lauf fehlgeschlagen", newsletter.failure(run_url))


# --------------------------------------------------------------------------- Automatik
def instant_top(current: pd.DataFrame, ranking: pd.DataFrame, tops: set[str], meta: dict, log=print) -> None:
    """Sofort-Mail bei JEDEM neu gemeldeten Kauf eines Top-Traders (unabhängig vom Score)."""
    if not tops:
        return
    recent = current[(current["filed"] >= dt.date.today() - dt.timedelta(days=NEW_WINDOW_DAYS))
                     & (current["type"] == "Kauf") & current["person"].isin(tops) & current["ticker"].notna()]
    keys = recent.apply(lambda r: trade_key(r.to_dict()), axis=1) if len(recent) else pd.Series(dtype=str)
    done = set(meta.get("alerted_top", []))
    first_run = "alerted_top" not in meta
    new = recent[~keys.isin(done)] if len(recent) else recent
    meta["alerted_top"] = sorted(done | set(keys))[-5000:]
    if first_run or new.empty:
        return  # beim allerersten Lauf nur merken
    digest = build_digest(new, ranking, os.environ.get("DASHBOARD_URL"))
    if not digest:
        return
    who = ", ".join(dict.fromkeys(new["person"]))
    what = ", ".join(dict.fromkeys(new["ticker"]))
    send(f"⭐ Top-Trader kauft: {who} – {what}", digest[1])
    log(f"  Top-Trader-Alarm verschickt: {who} – {what}")


def auto(current, ranking, dashboard_path, prices, chances=None, top_traders=None, log=print) -> None:
    """Wird bei jedem (3-stündlichen) Lauf aufgerufen und entscheidet, was verschickt wird."""
    if not (os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD")):
        raise SystemExit("GMAIL_USER / GMAIL_APP_PASSWORD fehlen - in den GitHub-Secrets eintragen.")
    from zoneinfo import ZoneInfo
    now = dt.datetime.now(ZoneInfo("Europe/Berlin"))
    today = now.date().isoformat()
    meta = load_meta()

    instant_top(current, ranking, top_traders or set(), meta, log)
    instant(current, ranking, meta, log)
    log_signals(ranking)

    if now.hour >= DIGEST_HOUR and meta.get("last_digest") != today:
        run(current, ranking, dashboard_path, log=log, chances=chances)   # Tagesmail
        meta["last_digest"] = today

    if now.weekday() == 0 and now.hour >= DIGEST_HOUR and meta.get("last_weekly") != today:
        last = {t: prices[t].dropna().iloc[-1] for t in prices.columns if prices[t].notna().any()}
        from tracker import portfolio
        depots = {cfg["label"]: portfolio.summary(portfolio._load(d), prices) for d, cfg in portfolio.DEPOTS.items()}
        review = weekly_review(last, prices["SPY"].dropna(), depots)
        if review:
            send(*review)
            log("  Wochenrückblick verschickt")
        meta["last_weekly"] = today
    save_meta(meta)


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3 and sys.argv[1] == "--failure":
        send_failure(sys.argv[2])
