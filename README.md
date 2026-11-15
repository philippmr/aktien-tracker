# Capitol Radar – Politiker-Insider-Tracker

Screent die Aktienkäufe von US-Kongressmitgliedern und Regierungsmitgliedern, bewertet jede gekaufte Aktie
mit einem **im Backtest geprüften Politiker-Signal** plus **Fundamentaldaten** und verschickt Mails:
Tagesübersicht, Sofort-Alarm bei sehr starken Signalen, Wochenrückblick. Dazu ein Online-Dashboard.

## Datenquellen (alle kostenlos)

| Quelle | Was |
|---|---|
| House Clerk (disclosures-clerk.house.gov) | Periodic Transaction Reports der Abgeordneten (PDF), Historie ab 2023; Papiermeldungen per OCR |
| Senate eFD (efdsearch.senate.gov) | Periodic Transaction Reports der Senator/innen (von Cloud-Servern aus blockiert, siehe unten) |
| Office of Government Ethics (extapps2.oge.gov) | Transaktionsberichte (278-T) der Exekutive: Präsident, Minister, Fed, Behördenchefs |
| USAspending.gov | Staatsaufträge an Firmen nach einem Politikerkauf |
| unitedstates/congress-legislators (GitHub) | Ausschüsse, Partei, Wahlkreis |
| SEC (company_tickers.json) | Zuordnung Firmenname → Ticker |
| Yahoo Finance (yfinance) | Kurse und Fundamentaldaten |

## Was du bekommst

| | Wann |
|---|---|
| **Tagesmail** mit allen neuen Käufen, 4 Kacheln, Top-Signal, Watchlist-Warnung | einmal täglich, erster Lauf ab 6 Uhr |
| **⚡ Sofort-Alarm** bei neu gemeldeten Käufen mit Score ≥ 70 | bis zu alle 3 Stunden |
| **Wochenrückblick**: Wie liefen die starken Signale seit ihrer Meldung? | montags |
| **❗ Fehler-Mail**, wenn ein Lauf scheitert (z. B. Formatänderung einer Behörde) | sofort |
| **Online-Dashboard** mit Ranking, Politiker-Trefferquoten, Feed und Backtest-Seite | immer aktuell |

## Einrichtung (GitHub Actions)

1. **Gmail-App-Passwort** unter <https://myaccount.google.com/apppasswords> anlegen (Bestätigung in zwei Schritten muss aktiv sein).
2. **Secrets** unter *Settings → Secrets and variables → Actions*:

   | Name | Wert |
   |---|---|
   | `GMAIL_USER` | Gmail-Adresse (Absender) |
   | `GMAIL_APP_PASSWORD` | App-Passwort |
   | `MAIL_TO` | Empfänger (optional, mehrere mit Komma) |
   | `WATCHLIST` | optional: deine Aktien, z. B. `AAPL,MSFT,NVDA`. Bei Politiker-Verkäufen kommt eine Warnung. Bleibt privat. |

3. **Dashboard online (GitHub Pages):** Repo öffentlich stellen (*Settings → General → Danger Zone → Change visibility*),
   dann *Settings → Pages → Source: GitHub Actions*. Adresse: `https://<user>.github.io/<repo>/`.
   Im Code und im Dashboard stehen keine Zugangsdaten und keine Watchlist.
4. **Testen:** *Actions → Capitol Radar → Run workflow*, Haken bei „Beispiel-Mail“ setzen.

Der Workflow läuft danach alle 3 Stunden. Vor jedem Lauf prüfen Tests (`tests/`), ob die Parser noch funktionieren.

## Lokal

```bash
pip install -r requirements.txt
```

```bash
python run.py                  # Dashboard nach output/dashboard.html
python run.py --email-preview  # Mail als output/mail_preview.html, ohne Versand
python run.py --backtest       # Backtest neu rechnen -> output/backtest.html
python -m pytest -q            # Tests
```

Für die Papiermeldungen muss Tesseract installiert sein (`apt install tesseract-ocr` bzw. `brew install tesseract`).

## Senat über den eigenen Rechner

Die Senatsseite blockt Cloud-Server, auch GitHub. Deshalb gibt es `senate_sync.py`: Es läuft auf deinem Rechner,
lädt die Senats-Meldungen, speichert sie in `state/senate_trades.csv` und pusht die Datei. Der Workflow liest sie
bei jedem Lauf mit ein. Läuft das Skript ein paar Tage nicht, fehlen nur die neuesten Senats-Trades.

**Einmalig einrichten:**

```bash
git clone https://github.com/philippmr/aktien-tracker.git
cd aktien-tracker
pip install -r requirements.txt
python senate_sync.py --no-push   # Test: lädt nur, pusht nicht
python senate_sync.py             # lädt und pusht
```

**Täglich automatisch:**

- **Mac/Linux** (`crontab -e`, täglich 6:40 Uhr):
  ```
  40 6 * * * cd /PFAD/ZU/aktien-tracker && /usr/bin/env python3 senate_sync.py >> senate.log 2>&1
  ```
- **Windows** (Aufgabenplanung → Einfache Aufgabe erstellen → täglich 6:40 → Programm starten):
  Programm `python`, Argumente `senate_sync.py`, Starten in `C:\PFAD\ZU\aktien-tracker`.

Der Rechner muss dafür an sein; verpasste Tage holt das Skript beim nächsten Lauf nach (es lädt immer 120 Tage).

## Musterdepots

Das Tool folgt seinen eigenen Signalen nach festen Regeln, in zwei Depots: Das **Trade-Depot** kauft jede Aktie, die neu
einen Trade-Score ≥ 70 erreicht, und verkauft nach 30 Tagen. Das **Halte-Depot** kauft ab Halte-Score ≥ 70 und hält 365 Tage.
Gekauft wird jeweils für 1.000 $ zum nächsten Schlusskurs. Zum Vergleich wird derselbe Betrag am selben Tag in den
S&P 500 gesteckt. Wertkurve und Positionen stehen im Dashboard (Tab „Musterdepot“) und im Wochenrückblick.
Daneben zeigt eine Simulation 2023 bis heute, wie diese Regeln in der Vergangenheit abgeschnitten hätten.

## Backtest und Scoring

`python run.py --backtest` wertet alle gemeldeten Käufe seit 2023 aus: Einstieg am ersten Handelstag **nach der Meldung**,
Rendite nach 30/90/180 Tagen im Vergleich zum S&P 500. Jedes Merkmal nutzt nur Wissen, das zum Meldezeitpunkt verfügbar war.

**Ergebnis (Stand 11/2026, 6.380 Käufe):**
- Blind nachkaufen lohnt sich nicht: im Schnitt −0,7 % nach 90 Tagen und −1,8 % nach 180 Tagen gegenüber dem Markt.
- Positiv waren **große Beträge (≥ 50 Tsd $)**, **Cluster (≥ 2 Politiker)**, **Regierungskäufe** und **schnell gemeldete Käufe**,
  stabil in beiden Zeiträumen (2023/24 und 2025/26).
- Kein oder negativer Effekt: **Ausschuss-Bezug**, Trefferquote, Erstkauf, „größer als üblich“. Diese sind nur noch Hinweise.
- **Small Caps** liefen deutlich schlechter (−7 % nach 90 Tagen) und sind ausgeschlossen (Marktkapitalisierung ≥ 2 Mrd $).
- Mit den neuen Gewichten schlugen die Top-20 % der Signale im Testzeitraum 2025/26 den Markt im Schnitt um +2,8 % (90 Tage),
  der Rest lag bei −0,9 %.

### Zwei Scores: Trade und Halten

Weil sich kurzfristige Trades und Buy & Hold unterschiedlich verhalten, bekommt jede Aktie zwei Scores (0–100).
Beide stammen aus dem Backtest mit historischen Bilanzdaten der SEC, also so, wie die Zahlen zum Meldezeitpunkt bekannt waren.

| | Trade-Score (30 Tage) | Halte-Score (365 Tage) |
|---|---|---|
| Bestandteile | Politiker-Signal: Betrag, Cluster, Wiederholungskauf, Regierung, schnelle Meldung, Calls; Bonus bei Umsatzwachstum > 20 % | Cluster (20), Wiederholungskauf (20), Quartalsumsatz +15 % (35), operative Marge > 20 % (15), Betrag ≥ 50 Tsd $ (10) |
| Score ≥ 70 im Backtest | Ø +2,8 % ggü. Markt in 30 Tagen, 58 % schlagen den Markt | Ø +15 % ggü. Markt nach 1 Jahr, 58 % schlagen den Markt |
| Stabil? | 2023/24: +2,8 % · 2025/26: +2,8 % | 2023/24: +18 % · 2025/26: +13 % |
| Musterdepot | Trade-Depot, Haltedauer 30 Tage | Halte-Depot, Haltedauer 365 Tage |

Der angezeigte **Score** ist der höhere von beiden; Tags „Trade“ / „Halten“ zeigen, wofür sich eine Aktie eignet.
Ausschuss-Bezug, Trefferquote, Trend, Profitabilität und Verschuldung hatten keinen stabilen Effekt und sind nur Hinweise.
Die bisherige Fundamental-Bewertung (Yahoo) wird weiter angezeigt, fließt aber nicht mehr in den Score ein.

**Wichtig:** 2023–2026 war ein von Tech- und Wachstumswerten getriebener Markt. Ob die Muster in anderen Phasen tragen,
zeigen erst die Musterdepots.

## Grenzen

- Beträge sind nur Spannen; Meldungen kommen bis zu 45 Tage verspätet.
- **Senat:** nur über `senate_sync.py` auf deinem Rechner (siehe oben). Das Skript ist nicht gegen die Live-Seite
  getestet, weil sie Cloud-Server blockt. Wenn es beim ersten Lauf Fehler zeigt, schick mir die Ausgabe.
- **Papiermeldungen:** OCR erkennt die Aktien, aber nicht zuverlässig Kauf/Verkauf und Betrag. Sie erscheinen im
  Feed als „Unklar (Scan)“ und zählen nicht für Score und Mail.
- **OGE:** nur direkt verlinkte Berichte; die des Präsidenten sind Scans (OCR), im Zweifel gilt das PDF.
- Ausschuss-Daten sind der heutige Stand.
- Vergangene Ergebnisse garantieren nichts. **Keine Anlageberatung.**
