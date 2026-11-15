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
STRONG_SCORE = 50
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
def _money(v):
    if v is None or pd.isna(v):
        return "–"
    return f"{v / 1e6:.1f} Mio $" if v >= 1e6 else f"{v / 1e3:.0f} Tsd $"


def _range(r):
    lo, hi = r.get("amount_low"), r.get("amount_high")
    if lo is None or pd.isna(lo):
        return "–"
    return _money(lo) if not hi or pd.isna(hi) or hi == lo else f"{_money(lo)} – {_money(hi)}"


def build_digest(new: pd.DataFrame, ranking: pd.DataFrame, dashboard_url: str | None) -> tuple[str, str] | None:
    buys = new[(new["type"] == "Kauf") & new["ticker"].notna() & new["asset_type"].isin(["ST", "OP"])]
    sells = new[new["type"] != "Kauf"]
    if buys.empty:
        return None  # nur Verkäufe -> keine Mail

    rk = ranking.set_index("ticker") if len(ranking) else pd.DataFrame()
    def info(t):
        return rk.loc[t].to_dict() if t in rk.index else {}

    def strong(t):
        i = info(t)
        return bool(i) and (i.get("score", 0) >= STRONG_SCORE or i.get("has_committee") or
                            i.get("call_options") or i.get("n_buyers", 0) >= 2)

    tickers = list(dict.fromkeys(buys["ticker"]))
    strong_t = sorted([t for t in tickers if strong(t)], key=lambda t: -info(t).get("score", 0))
    other_t = sorted([t for t in tickers if t not in strong_t], key=lambda t: -info(t).get("score", 0))

    e = html.escape
    css = ("font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1a1d23;"
           "max-width:720px;margin:0 auto;padding:16px")
    td = "padding:6px 8px;border-bottom:1px solid #e4e7ec;font-size:13px;vertical-align:top"
    th = td + ";color:#667085;font-size:12px;text-align:left"

    def stock_block(t):
        i = info(t)
        rows = buys[buys["ticker"] == t]
        def who(r):
            if r.get("source") == "Exekutive":  # Amt dazuschreiben, z. B. "President"
                return (f"{e(str(r['person']))}<br><span style='color:#667085;font-size:11px'>"
                        f"{e(str(r.get('role') or ''))}</span>")
            return e(str(r["person"]))

        trades = "".join(
            f"<tr><td style='{td}'>{who(r)}</td><td style='{td}'>{r['tx_date']:%d.%m.%Y}</td>"
            f"<td style='{td}'>{e(str(r.get('owner') or ''))}</td><td style='{td}'>{_range(r)}"
            f"{' (Option)' if r['asset_type'] == 'OP' else ''}</td>"
            f"<td style='{td}'><a href='{e(str(r['url']))}'>PDF</a></td></tr>" for _, r in rows.iterrows())
        reasons = "".join(f"<li>{e(x)}</li>" for x in (i.get("reasons") or [])[:5])
        score = (f"<b>Score {i['score']:.0f}</b> · Politiker {i['insider_score']:.0f} · "
                 f"Fundamental {i['fundamental_score']:.0f}") if i else "nicht bewertet (z. B. zu klein oder ETF)"
        name = e(str(i.get("name") or rows["asset"].iloc[0]))
        reasons_html = (f"<ul style='margin:4px 0 8px;padding-left:18px;font-size:13px'>{reasons}</ul>"
                        if reasons else "")
        return (f"<div style='margin:14px 0;padding:12px;border:1px solid #e4e7ec;border-radius:10px'>"
                f"<div style='font-size:16px'><b style='color:#2563eb'>{e(t)}</b> {name}</div>"
                f"<div style='color:#667085;font-size:13px;margin:2px 0 6px'>{score}"
                f"{' · ' + e(str(i.get('sector'))) if i.get('sector') else ''}</div>"
                f"{reasons_html}"
                f"<table style='border-collapse:collapse;width:100%'><tr><th style='{th}'>Politiker</th>"
                f"<th style='{th}'>Trade-Datum</th><th style='{th}'>Eigentümer</th><th style='{th}'>Betrag</th>"
                f"<th style='{th}'></th></tr>{trades}</table></div>")

    parts = [f"<div style='{css}'>",
             f"<h2 style='margin:0 0 4px'>Neue Politiker-Trades</h2>",
             f"<div style='color:#667085;margin-bottom:12px'>{dt.date.today():%d.%m.%Y} · "
             f"{len(buys)} neue Käufe in {len(tickers)} Aktien · {len(sells)} Verkäufe</div>"]
    if dashboard_url:
        parts.append(f"<p><a href='{e(dashboard_url)}'>Zum Dashboard</a></p>")
    if strong_t:
        parts.append("<h3 style='margin:18px 0 4px'>⭐ Starke Signale</h3>")
        parts += [stock_block(t) for t in strong_t]
    if other_t:
        parts.append("<h3 style='margin:18px 0 4px'>Weitere neue Käufe</h3>")
        parts += [stock_block(t) for t in other_t[:25]]
        if len(other_t) > 25:
            parts.append(f"<p style='color:#667085'>… und {len(other_t) - 25} weitere im Dashboard.</p>")
    if len(sells):
        top_sells = sells[sells["ticker"].notna()].groupby("ticker")["person"].nunique().sort_values(ascending=False)
        if len(top_sells):
            parts.append("<h3 style='margin:18px 0 4px'>Verkäufe</h3><p style='font-size:13px'>" +
                         ", ".join(f"{e(t)} ({n})" for t, n in top_sells.head(20).items()) + "</p>")
    parts.append("<p style='color:#667085;font-size:12px;margin-top:20px'>Keine Anlageberatung. "
                 "Das komplette Dashboard hängt als HTML-Datei an.</p></div>")

    subject = f"Politiker-Trades {dt.date.today():%d.%m.}: {len(buys)} Käufe"
    if strong_t:
        subject += f", ⭐ {', '.join(strong_t[:4])}"
    return subject, "".join(parts)


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


def run(current: pd.DataFrame, ranking: pd.DataFrame, dashboard_path: str, *, dry_run=False, log=print):
    new, keys = new_trades(current)
    digest = build_digest(new, ranking, os.environ.get("DASHBOARD_URL"))
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
