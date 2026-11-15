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
def build_digest(new: pd.DataFrame, ranking: pd.DataFrame, dashboard_url: str | None) -> tuple[str, str] | None:
    """Wählt die neuen Käufe aus, sortiert nach Score und übergibt an das Newsletter-Layout."""
    from tracker import newsletter

    buys = new[(new["type"] == "Kauf") & new["ticker"].notna() & new["asset_type"].isin(["ST", "OP"])].copy()
    sells = new[new["type"] != "Kauf"]
    if buys.empty:
        return None  # nur Verkäufe -> keine Mail

    rk = ranking.set_index("ticker") if len(ranking) else pd.DataFrame()

    def info(t):
        return rk.loc[t].to_dict() if t in rk.index else {}

    def strong(t):
        i = info(t)
        return bool(i) and (i.get("score", 0) >= STRONG_SCORE or bool(i.get("has_committee")) or
                            bool(i.get("call_options")) or i.get("n_buyers", 0) >= 2)

    buys["_score"] = buys["ticker"].map(lambda t: info(t).get("score", -1))
    buys = buys.sort_values(["_score", "ticker", "tx_date"], ascending=[False, True, False])
    strong_t = [t for t in dict.fromkeys(buys["ticker"]) if strong(t)]
    return newsletter.build(buys, sells, info, strong_t, dashboard_url)


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
