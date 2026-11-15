"""Senats-Daten auf DEINEM Rechner laden und ins GitHub-Repo pushen.

Warum: efdsearch.senate.gov blockt Cloud-Server (auch GitHub Actions), von einem normalen
Internetanschluss aus funktioniert die Seite. Dieses Skript lädt deshalb die Senats-Meldungen
lokal, speichert sie in state/senate_trades.csv und pusht die Datei. Der Workflow liest sie
bei jedem Lauf mit ein.

Voraussetzung: Python 3.10+, git, ein Klon dieses Repos, in dem `git push` funktioniert.

    pip install -r requirements.txt
    python senate_sync.py            # lädt, speichert und pusht
    python senate_sync.py --no-push  # nur laden und speichern (zum Testen)

Mit SENATE_ACCEPT_TERMS = True in config.py stimmst du den Nutzungsbedingungen der
Senatsseite zu; das Skript akzeptiert sie in deinem Namen.
"""
import argparse
import datetime as dt
import subprocess
import sys

import config
from tracker.sources import senate


def git(*args):
    return subprocess.run(["git", *args], check=False, capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--days", type=int, default=None,
                    help="wie weit zurück (Standard: beim ersten Mal seit HISTORY_START_YEAR, danach 120 Tage)")
    args = ap.parse_args()

    if not args.no_push:
        r = git("pull", "--rebase", "-q")
        if r.returncode:
            print("git pull fehlgeschlagen:\n" + r.stderr)
            sys.exit(1)

    first = not __import__("os").path.exists(senate.CSV_FILE)
    days = args.days or ((dt.date.today() - dt.date(config.HISTORY_START_YEAR, 1, 1)).days if first else 120)
    print(f"Lade Senats-Meldungen der letzten {days} Tage ...")
    trades = senate.fetch(days)
    if not trades:
        print("Keine Senats-Trades geladen. Ist die Seite erreichbar? (Ausgabe oben prüfen)")
        sys.exit(1)
    total = senate.save_csv(trades)
    stocks = sum(1 for t in trades if t.get("ticker"))
    print(f"{len(trades)} Transaktionen geladen ({stocks} mit Ticker), {total} insgesamt in {senate.CSV_FILE}")

    if args.no_push:
        return
    git("add", senate.CSV_FILE)
    if git("diff", "--cached", "--quiet").returncode == 0:
        print("Keine neuen Senats-Trades – nichts zu pushen.")
        return
    git("commit", "-q", "-m", f"Senat: Trades aktualisiert {dt.date.today()}")
    r = git("push", "-q")
    print("Gepusht." if r.returncode == 0 else "Push fehlgeschlagen:\n" + r.stderr)


if __name__ == "__main__":
    main()
