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

## Musterdepot

Das Tool folgt seinen eigenen Signalen nach festen Regeln: Jede Aktie, die neu mit Score ≥ 60 auftaucht, wird für 1.000 $
zum nächsten Schlusskurs „gekauft“ und nach 90 Tagen verkauft. Zum Vergleich wird derselbe Betrag am selben Tag in den
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

**Politiker-Score (0–100), je Kauf:**

| Signal | Punkte |
|---|---|
| Basis | 10 |
| Betrag (logarithmisch, 8 Tsd. bis 1 Mio. $) | bis 25 |
| Betrag ≥ 50 Tsd $ | +10 |
| Call-Optionen | +8 |
| Regierungsmitglied (OGE) | +5 |
| Innerhalb von 14 Tagen gemeldet | +4 |
| Sammelmeldung (≥ 15 Trades, meist Vermögensverwalter) | × 0,7 |

Mehrere Käufer addieren sich mit abnehmendem Gewicht (1 / 0,7 / 0,5 …), Verkäufe anderer Politiker geben Abzug,
ältere Käufe werden abgewertet. **Gesamt-Score = 60 % Politiker + 40 % Fundamentaldaten** (Bewertung, Qualität,
Wachstum, Bilanz; nicht im Backtest prüfbar, weil es keine historischen Werte gibt).

## Grenzen

- Beträge sind nur Spannen; Meldungen kommen bis zu 45 Tage verspätet.
- **Senat:** nur über `senate_sync.py` auf deinem Rechner (siehe oben). Das Skript ist nicht gegen die Live-Seite
  getestet, weil sie Cloud-Server blockt. Wenn es beim ersten Lauf Fehler zeigt, schick mir die Ausgabe.
- **Papiermeldungen:** OCR erkennt die Aktien, aber nicht zuverlässig Kauf/Verkauf und Betrag. Sie erscheinen im
  Feed als „Unklar (Scan)“ und zählen nicht für Score und Mail.
- **OGE:** nur direkt verlinkte Berichte; die des Präsidenten sind Scans (OCR), im Zweifel gilt das PDF.
- Ausschuss-Daten sind der heutige Stand.
- Vergangene Ergebnisse garantieren nichts. **Keine Anlageberatung.**
