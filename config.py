"""Einstellungen für den Politiker-Insider-Tracker. Hier anpassen."""

# User-Agent für Abrufe bei Behörden. Bitte eigenen Namen + Kontakt eintragen.
USER_AGENT = "InsiderTracker research contact@example.org"

# Zeitfenster
CONGRESS_LOOKBACK_DAYS = 60   # Trades, die in diesem Zeitraum GEMELDET wurden, kommen ins Ranking
HISTORY_START_YEAR = 2023     # ab hier wird die Historie für die Trefferquote geladen

# Senat: Die Senats-Datenbank (efdsearch.senate.gov) verlangt die Zustimmung zu ihren
# Nutzungsbedingungen (u. a. keine kommerzielle Nutzung). Du hast zugestimmt; das Skript
# akzeptiert sie in deinem Namen. Auf False setzen, um den Senat abzuschalten.
SENATE_ACCEPT_TERMS = True

# Trefferquote je Politiker
TRACK_RECORD_HORIZON_DAYS = 90   # Rendite eines Kaufs wird über so viele Tage gemessen
TRACK_RECORD_MIN_TRADES = 5      # erst ab so vielen bewerteten Käufen zählt die Trefferquote

# Filter
MIN_MARKET_CAP = 2_000_000_000   # Small Caps raus: liefen im Backtest deutlich schlechter (−5 bis −9 % ggü. Markt)

# Gewichtung im Gesamt-Score (Summe = 1)
WEIGHT_INSIDER = 0.6
WEIGHT_FUNDAMENTAL = 0.4

TOP_N = 150                      # so viele Aktien landen im Dashboard

CACHE_DIR = "cache"
OUTPUT_DIR = "output"

# Chancen-Screener (USA + Europa). Im Backtest ohne verlässlichen Vorsprung -> standardmäßig aus.
SCREENER_ENABLED = False

# Top-Trader: nur Politiker mit großem Volumen UND gutem Track Record (ab Meldung, 1 Jahr)
TOP_MIN_BUY_VOLUME = 2_000_000   # Kaufvolumen seit HISTORY_START_YEAR (Mitte der Spannen)
TOP_MIN_EXCESS = 0.05            # Ø Vorsprung ggü. S&P 500 nach 1 Jahr ab Meldung
TOP_MIN_TRADES = 8               # mindestens so viele bewertete Käufe
TOP_MIN_HIT_RATE = 0.45          # Anteil der Käufe, die den Markt schlagen
TOP_MIN_AVG_BUY = 50_000         # Ø Kaufbetrag: nur Trader, die mit großen Beträgen kaufen
