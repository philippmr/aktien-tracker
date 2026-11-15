# Politiker-Insider-Tracker

Screent die Aktienkäufe von US-Kongressmitgliedern und Regierungsmitgliedern und bewertet jede gekaufte Aktie nach
**Ausschuss-Bezug**, **Trefferquote des Politikers**, **ungewöhnlichen Trades** und **Fundamentaldaten**.
Ergebnis ist ein interaktives HTML-Dashboard plus CSV-Dateien.

## Datenquellen (alle kostenlos)

| Quelle | Was |
|---|---|
| House Clerk (disclosures-clerk.house.gov) | Periodic Transaction Reports der Abgeordneten (PDF), Historie ab 2023 |
| Senate eFD (efdsearch.senate.gov) | Periodic Transaction Reports der Senator/innen |
| Office of Government Ethics (extapps2.oge.gov) | Transaktionsberichte (Formular 278-T) der Exekutive: Präsident, Minister, Fed-Gouverneure, Behördenchefs |
| unitedstates/congress-legislators (GitHub) | Wer sitzt in welchem Ausschuss, Partei, Wahlkreis |
| Yahoo Finance (yfinance) | Kurse (für Trefferquote) und Fundamentaldaten |

## Installation & Start

```bash
pip install -r requirements.txt
```

```bash
python run.py
```

Danach `output/dashboard.html` im Browser öffnen.

Der erste Lauf lädt die gesamte Historie seit 2023 (rund 1.700 PDF-Meldungen) und dauert entsprechend.
Alles landet in `cache/`, Folgeläufe brauchen nur wenige Minuten.

Tägliche Ausführung per Cron (werktags 7:15 Uhr):

```
15 7 * * 1-5 cd /pfad/zu/insider-tracker && python run.py >> run.log 2>&1
```

## Automatisch: täglicher Mail-Report über GitHub Actions

Der Workflow `.github/workflows/daily.yml` läuft jeden Morgen (05:23 UTC, also 06:23 Uhr im Winter
und 07:23 Uhr im Sommer). Er lädt neue Meldungen, bewertet sie und schickt dir eine Mail mit allen
**neuen Käufen seit dem letzten Lauf**: starke Signale oben, das komplette Dashboard als Anhang.
Welche Trades schon gemeldet wurden, merkt er sich in `state/seen.json` (wird automatisch committet).

**Einmalige Einrichtung**

1. **Gmail-App-Passwort erstellen.** Dafür muss die Bestätigung in zwei Schritten aktiv sein.
   Unter <https://myaccount.google.com/apppasswords> ein App-Passwort anlegen (Name z. B. „Insider-Tracker“).
   Du erhältst 16 Zeichen.
2. **Secrets im GitHub-Repo eintragen** unter *Settings → Secrets and variables → Actions → New repository secret*:

   | Name | Wert |
   |---|---|
   | `GMAIL_USER` | deine Gmail-Adresse (Absender) |
   | `GMAIL_APP_PASSWORD` | das App-Passwort aus Schritt 1 |
   | `MAIL_TO` | Empfänger (optional, mehrere mit Komma; Standard = `GMAIL_USER`) |

3. **Ersten Lauf manuell starten:** *Actions → Täglicher Politiker-Trade-Report → Run workflow*.
   Der erste Lauf lädt die Historie und dauert 30–40 Minuten. Danach dauert ein Lauf wenige Minuten.

Das Dashboard steckt als Anhang in jeder Mail. Zusätzlich liegt es 14 Tage lang unter *Actions → Lauf → Artifacts*.

Die Mail lokal testen, ohne etwas zu verschicken:

```bash
python run.py --email-preview   # erzeugt output/mail_preview.html
```

## Scoring

**Gesamt-Score = 60 % Politiker-Score + 40 % Fundamental-Score** (Gewichte in `config.py`).

**Politiker-Score (0–100).** Jeder Kauf bekommt Punkte:

| Signal | Punkte |
|---|---|
| Basis | 10 |
| Betrag (logarithmisch, 8 Tsd. bis 500 Tsd. $) | bis 15 |
| **Ausschuss-Bezug**: Politiker sitzt im Ausschuss, der für die Branche zuständig ist | +20 (Vorsitz +5) |
| **Trefferquote**: Ø-Überrendite früherer Käufe vs. S&P 500 über 90 Tage | −10 bis +15 |
| Call-Optionen gekauft | +12 |
| Betrag ≥ 2,5× größer als bei diesem Politiker üblich | +6 |
| Erstkauf dieser Aktie | +5 |
| Selbst/gemeinsam gehandelt (nicht Ehepartner/Kind) | +3 |
| Sammelmeldung mit ≥ 15 Trades (meist Vermögensverwalter) | × 0,5 |

Dividenden-Wiederanlagen zählen nicht. Kaufen mehrere Politiker dieselbe Aktie, addieren sich ihre Punkte
mit abnehmendem Gewicht (1 / 0,7 / 0,5 …). Verkäufe anderer Politiker geben Abzug, ältere Käufe werden abgewertet.

Die Zuordnung Ausschuss → Branche steht in `tracker/committees.py` (`COMMITTEE_SECTORS`), alle Punkte in
`tracker/scoring.py`.

**Fundamental-Score (0–100)** aus vier Säulen à 25 Punkten: Bewertung (KGV, PEG, EV/EBITDA),
Qualität (ROE, Margen, Free Cashflow), Wachstum (Umsatz, Gewinn), Bilanz (Verschuldung, Liquidität, Netto-Cash).

## Dashboard

- **Aktien-Ranking**: sortier- und filterbar (z. B. „nur Ausschuss-Bezug“, „Call-Optionen“). Ein Klick auf eine Zeile zeigt Begründung, Fundamentaldaten und alle Trades mit Link zur Originalmeldung.
- **Politiker-Trefferquote**: wer mit seinen Käufen den Markt historisch geschlagen hat.
- **Alle Trades**: chronologischer Feed.

## Senat

Die Senatsdatenbank verlangt die Zustimmung zu ihren Nutzungsbedingungen (u. a. keine kommerzielle Nutzung).
`SENATE_ACCEPT_TERMS = True` in `config.py` bedeutet, dass du zugestimmt hast.
Hinweis: Von Cloud-Servern aus blockt die Seite die Datenabfrage (HTTP 503). Von einem normalen Internetanschluss aus sollte sie funktionieren.

## Exekutive (OGE)

- Geladen werden nur Berichte, die das OGE direkt als PDF verlinkt (rund 8 pro Monat, u. a. Präsident, Minister, Fed).
  Viele andere Amtsträger müssen einzeln per Formular angefordert werden und fehlen deshalb.
- Die Berichte des Präsidenten sind eingescannt. Ihr Text wird per OCR gelesen, Firmennamen werden über die
  SEC-Tickerliste zugeordnet. Einzelne Daten oder Beträge können deshalb falsch erkannt werden; im Zweifel gilt das PDF.
- Für Amtsträger gibt es analog zum Ausschuss einen **Ressort-Bezug** (z. B. Energieminister kauft Energieaktien),
  Zuordnung in `AGENCY_SECTORS` in `tracker/committees.py`.
- Präsident und Vizepräsident bekommen keinen pauschalen Ressort-Bonus. Ihre Depots werden von Verwaltern geführt
  und gelten als Sammelmeldungen (schwaches Signal).

## Grenzen

- Beträge sind nur Spannen; Meldungen kommen bis zu 45 Tage verspätet.
- Eingescannte Papiermeldungen werden übersprungen.
- Ausschuss-Daten sind der aktuelle Stand; die Trefferquote berücksichtigt auch Ex-Abgeordnete nur über den Namen.
- Keine Anlageberatung.
