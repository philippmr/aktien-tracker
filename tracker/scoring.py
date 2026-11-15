"""Scoring: Politiker-Signal + Fundamentaldaten -> Gesamt-Score je Aktie.

Alle Punkte-Schwellen stehen hier zentral, damit du sie leicht anpassen kannst.
"""
import datetime as dt
import math

import pandas as pd

import config

MANAGED_FILING_SIZE = 15
# Rohpunkte -> 0-100. Ein einzelner kleiner Kauf landet bei ~20, ein Kauf mit Ausschuss-Bezug
# und gutem Track Record bei ~60-70, mehrere starke Käufer erreichen 100.
SCALE = 2.0  # ab so vielen Trades in einer Meldung: vermutlich Vermögensverwalter


def _log_points(value, lo, hi, max_pts):
    """0 Punkte bei `lo`, max_pts bei `hi`, logarithmisch dazwischen."""
    if not value or value <= lo:
        return 0.0
    return min(max_pts, max_pts * math.log(value / lo) / math.log(hi / lo))


def _mid(row) -> float:
    lo, hi = row.get("amount_low"), row.get("amount_high")
    lo = 0 if lo is None or pd.isna(lo) else lo
    hi = lo if hi is None or pd.isna(hi) else hi
    return (lo + hi) / 2


def is_reinvest(row) -> bool:
    return "reinvest" in str(row.get("description") or "").lower()


def is_call(row) -> bool:
    txt = f"{row.get('asset', '')} {row.get('description') or ''}".lower()
    return row.get("asset_type") == "OP" and "call" in txt


def is_put(row) -> bool:
    txt = f"{row.get('asset', '')} {row.get('description') or ''}".lower()
    return row.get("asset_type") == "OP" and "put" in txt


# --------------------------------------------------------------------------- Politiker-Signal
class PoliticianSignals:
    """Bewertet einzelne Käufe mit Kontext aus Historie, Ausschüssen und Trefferquote."""

    def __init__(self, history: pd.DataFrame, track: pd.DataFrame, committees):
        self.track = track
        self.committees = committees
        h = history.copy()
        h["mid"] = h.apply(_mid, axis=1)
        self.median_size = h.groupby("person")["mid"].median().to_dict()
        # (Person, Ticker) -> frühestes Datum, an dem die Person die Aktie gehandelt hat
        self.first_seen = h.dropna(subset=["ticker"]).groupby(["person", "ticker"])["tx_date"].min().to_dict()
        # Meldedaten früherer Käufe je (Person, Ticker) -> Wiederholungskäufe erkennen
        b = h[(h["type"] == "Kauf") & h["ticker"].notna() & h["filed"].notna()]
        self.buy_filed = b.groupby(["person", "ticker"])["filed"].apply(lambda x: sorted(set(x))).to_dict()

    def trade(self, row, sector, industry) -> tuple[float, list[str], dict]:
        """Punkte für EINEN Kauf + Begründungen + Merkmale fürs Dashboard."""
        # Gewichte aus dem Backtest 2023–2026 (python run.py --backtest). Was dort keine
        # Vorhersagekraft hatte, gibt nur noch einen Hinweis, aber keine Punkte mehr.
        why, flags = [], {}
        pts = 10.0
        mid = _mid(row)
        pts += _log_points(mid, 8_000, 1_000_000, 25)          # Betrag: stärkstes Einzelsignal
        if mid >= 50_000:
            pts += 10
            why.append(f"großer Betrag ({row['amount_low']:,.0f}–{row['amount_high']:,.0f} $)".replace(",", "."))
        if is_call(row):
            pts += 8                                              # positiv, aber wenige Fälle
            why.append("kauft Call-Optionen (Wette auf steigende Kurse)")
            flags["call"] = True
        if row["source"] == "Exekutive":
            pts += 5                                              # Regierungskäufe liefen leicht besser
        # Wiederholungskauf: dieselbe Person hat dieselbe Aktie schon in den 180 Tagen davor gekauft.
        # Im Backtest stabil positiv: +1 Punkt nach 30 Tagen, +5 Punkte nach 365 Tagen.
        earlier = [f for f in self.buy_filed.get((row["person"], row["ticker"]), [])
                   if row.get("filed") and 0 < (row["filed"] - f).days <= 180]
        if earlier:
            pts += 8
            why.append(f"{row['person']} kauft zum wiederholten Mal nach (Überzeugung)")
            flags["repeat"] = True
        delay = (row["filed"] - row["tx_date"]).days if row.get("filed") and row.get("tx_date") else None
        if delay is not None and delay <= 14:
            pts += 4                                              # schnell gemeldet = Signal noch frisch
            flags["fast"] = True

        # Nur noch Hinweise (im Backtest ohne oder mit negativem Effekt)
        first = self.first_seen.get((row["person"], row["ticker"]))
        if first is not None and first >= row["tx_date"]:
            flags["first_buy"] = True
        usual = self.median_size.get(row["person"])
        if usual and mid > 2.5 * usual and mid >= 15_000:
            why.append(f"{row['person']}: {mid / usual:.0f}× größer als sonst üblich")
            flags["unusual_size"] = True

        info = None
        if row["source"] == "Exekutive":
            from tracker.committees import agency_relevant
            rel = agency_relevant(row.get("role"), sector, industry)
            if rel:
                why.append(f"Hinweis: {row['person']} ist zuständig ({rel})")
                flags["committee"] = [rel]
        elif self.committees is not None:
            chamber = "sen" if row["source"] == "Senat" else "rep"
            info = self.committees.match(row["person"], row.get("state_district"), chamber)
        if info:
            flags["party"] = info["party"][:1]
            rel = self.committees.relevant_committees(info["bioguide"], sector, industry)
            if rel:
                why.append(f"Hinweis: {row['person']} sitzt im zuständigen Ausschuss ({', '.join(rel)})")
                flags["committee"] = rel

        if row["person"] in self.track.index:
            tr = self.track.loc[row["person"]]
            if tr["n"] >= config.TRACK_RECORD_MIN_TRADES:
                flags["track"] = (int(tr["n"]), float(tr["hit_rate"]), float(tr["avg_excess"]))

        # Sammelmeldung -> meist Vermögensverwalter; leichter Abschlag, damit Großdepots
        # (z. B. des Präsidenten) mit Hunderten Trades das Ranking nicht fluten
        if (row.get("filing_size") or 0) >= MANAGED_FILING_SIZE:
            pts *= 0.7
            flags["managed"] = True
        return pts, why, flags


def insider_score(grp: pd.DataFrame, sig: PoliticianSignals, sector, industry):
    today = dt.date.today()
    buys = grp[(grp["type"] == "Kauf") & ~grp.apply(is_put, axis=1) & ~grp.apply(is_reinvest, axis=1)]
    sells = grp[grp["type"].isin(["Verkauf", "Teilverkauf"]) | grp.apply(is_put, axis=1)]
    if buys.empty:
        return 0.0, [], {}

    per_person, reasons, flags = {}, [], {"committee": set(), "call": False, "unusual_size": False,
                                          "first_buy": False, "managed_only": True, "track": None}
    for _, row in buys.iterrows():
        pts, why, f = sig.trade(row, sector, industry)
        p = row["person"]
        if pts > per_person.get(p, (0,))[0]:
            per_person[p] = (pts, why)
        flags["committee"].update(f.get("committee", []))
        flags["call"] |= bool(f.get("call"))
        flags["repeat"] = flags.get("repeat", False) or bool(f.get("repeat"))
        flags["unusual_size"] |= bool(f.get("unusual_size"))
        flags["first_buy"] |= bool(f.get("first_buy"))
        flags["managed_only"] &= bool(f.get("managed"))
        if f.get("track") and (flags["track"] is None or f["track"][2] > flags["track"][2]):
            flags["track"] = f["track"]

    # Mehrere kaufende Politiker verstärken sich, mit abnehmendem Zusatznutzen
    ranked = sorted(per_person.values(), key=lambda x: -x[0])
    weights = [1.0, 0.7, 0.5, 0.4, 0.3]
    score = sum(p * (weights[i] if i < len(weights) else 0.2) for i, (p, _) in enumerate(ranked))
    n = len(per_person)
    if n >= 2:
        reasons.append(f"{n} Politiker kaufen (Cluster)")
    for _, why in ranked:
        reasons += why

    # Verkäufe anderer Politiker: Im Backtest lagen Aktien nach großen Verkäufen (≥ 50 Tsd $)
    # 90 Tage später im Schnitt 1,8 % hinter dem Markt, nach kleinen kaum.
    other = sells[~sells["person"].isin(per_person)]
    if len(other):
        size = other.assign(mid=other.apply(_mid, axis=1)).groupby("person")["mid"].sum()
        big = int((size >= 50_000).sum())
        small = len(size) - big
        score -= 8 * big + 3 * small
        reasons.append(f"{len(size)} andere(r) Politiker verkauft" + (f", davon {big} groß (≥ 50 Tsd $)" if big else ""))
    if flags["managed_only"]:
        reasons.append("nur Sammelmeldungen (vermutlich Vermögensverwalter)")

    score *= SCALE
    age = (today - buys["tx_date"].max()).days
    score *= 1.0 if age <= 21 else 0.85 if age <= 45 else 0.7

    # doppelte Begründungen entfernen, Reihenfolge behalten
    reasons = list(dict.fromkeys(reasons))
    flags["committee"] = sorted(flags["committee"])
    return max(0.0, min(100.0, score)), reasons, flags


# --------------------------------------------------------------------------- Fundamental
def _tier(v, tiers, higher_is_better=True):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    for thr, p in tiers:
        if (v >= thr) if higher_is_better else (v <= thr):
            return p
    return 0


def _pillar(items):
    got = [(p, m) for p, m in items if p is not None]
    if not got:
        return None
    return 25 * sum(p for p, _ in got) / sum(m for _, m in got)


def fundamental_score(f: dict):
    g = lambda k: (None if f.get(k) is None or (isinstance(f.get(k), float) and math.isnan(f.get(k))) else f.get(k))
    pe = g("forwardPE")
    valuation = _pillar([
        ((_tier(pe, [(12, 10), (18, 7), (25, 4), (40, 1)], False) if pe > 0 else 0) if pe is not None else None, 10),
        (_tier(g("trailingPegRatio"), [(1, 8), (1.5, 5), (2.5, 2)], False) if (g("trailingPegRatio") or 0) > 0 else None, 8),
        (_tier(g("enterpriseToEbitda"), [(10, 7), (15, 4), (25, 1)], False) if (g("enterpriseToEbitda") or 0) > 0 else None, 7),
    ])
    quality = _pillar([
        (_tier(g("returnOnEquity"), [(0.20, 8), (0.12, 5), (0.05, 2)]), 8),
        (_tier(g("operatingMargins"), [(0.20, 7), (0.10, 4), (0.0, 1)]), 7),
        ((6 if g("freeCashflow") > 0 else 0) if g("freeCashflow") is not None else None, 6),
        (_tier(g("grossMargins"), [(0.40, 4), (0.25, 2)]), 4),
    ])
    growth = _pillar([
        (_tier(g("revenueGrowth"), [(0.20, 13), (0.10, 9), (0.03, 5), (0.0, 2)]), 13),
        (_tier(g("earningsGrowth"), [(0.25, 12), (0.10, 8), (0.0, 4)]), 12),
    ])
    net_cash = None
    if g("totalCash") is not None and g("totalDebt") is not None:
        net_cash = 8 if g("totalCash") >= g("totalDebt") else 0
    health = _pillar([
        (_tier(g("debtToEquity"), [(50, 10), (100, 6), (200, 2)], False), 10),
        (_tier(g("currentRatio"), [(1.5, 7), (1.0, 4)]), 7),
        (net_cash, 8),
    ])
    pillars = {"Bewertung": valuation, "Qualität": quality, "Wachstum": growth, "Bilanz": health}
    return sum(v if v is not None else 12.5 for v in pillars.values()), pillars


# --------------------------------------------------------------------------- Ranking
def rank(trades: pd.DataFrame, fund: pd.DataFrame, prices: pd.DataFrame, sig: PoliticianSignals) -> pd.DataFrame:
    stocks = trades[trades["ticker"].notna() & trades["asset_type"].isin(["ST", "OP"])]
    rows = []
    for ticker, grp in stocks.groupby("ticker"):
        f = fund.loc[ticker].to_dict() if ticker in fund.index else {}
        if f.get("quoteType") not in (None, "EQUITY"):
            continue  # ETFs, Fonds
        mcap = f.get("marketCap")
        if not mcap or (isinstance(mcap, float) and math.isnan(mcap)) or mcap < config.MIN_MARKET_CAP:
            continue
        ins, why, flags = insider_score(grp, sig, f.get("sector"), f.get("industry"))
        if ins <= 0:
            continue
        fs, pillars = fundamental_score(f)
        buys = grp[grp["type"] == "Kauf"]
        first_buy = buys["tx_date"].min()

        perf = None
        if ticker in prices.columns:
            s = prices[ticker].dropna()
            s = s[s.index.date >= first_buy]
            if len(s) > 1:
                perf = s.iloc[-1] / s.iloc[0] - 1

        # ---- Zwei Scores aus dem Backtest (2023–2026, stabil in 2023/24 und 2025/26):
        # Trade (30 Tage):   Politiker-Signal + starkes Umsatzwachstum
        # Halten (365 Tage): Cluster, Wiederholungskauf, Quartalsumsatz-Wachstum, operative Marge, großer Betrag
        g_ = lambda k: f.get(k) if isinstance(f.get(k), (int, float)) and not math.isnan(f.get(k)) else None
        rev_g, op_m = g_("revenueGrowth"), g_("operatingMargins")
        big = bool((buys.apply(_mid, axis=1) >= 50_000).any())
        cluster = buys["person"].nunique() >= 2
        trade_score = min(100.0, ins + (15 if rev_g is not None and rev_g > 0.20 else 0))
        hold_score = (20 * cluster + 20 * bool(flags.get("repeat")) + 35 * (rev_g is not None and rev_g > 0.15)
                      + 15 * (op_m is not None and op_m > 0.20) + 10 * big)
        if rev_g is not None and rev_g > 0.15:
            why = why + [f"Umsatz wächst {rev_g:.0%} ggü. Vorjahresquartal"]
        if op_m is not None and op_m > 0.20:
            why = why + [f"hohe operative Marge ({op_m:.0%})"]

        price, tgt = f.get("currentPrice"), f.get("targetMeanPrice")
        tr = flags.get("track")
        rows.append({
            "ticker": ticker,
            "name": f.get("shortName") or grp["asset"].iloc[0],
            "sector": f.get("sector"),
            "industry": f.get("industry"),
            "score": round(max(trade_score, hold_score), 1),
            "trade_score": round(trade_score, 1),
            "hold_score": round(float(hold_score), 1),
            "horizon": "Trade" if trade_score >= hold_score else "Halten",
            "repeat": bool(flags.get("repeat")),
            "insider_score": round(ins, 1),
            "fundamental_score": round(fs, 1),
            **{f"p_{k}": (round(v, 1) if v is not None else None) for k, v in pillars.items()},
            "n_buyers": buys["person"].nunique(),
            "buyers": ", ".join(sorted(buys["person"].unique())),
            "n_sellers": grp[grp["type"] != "Kauf"]["person"].nunique(),
            "volume_mid": float(buys.apply(_mid, axis=1).sum()),
            "committee": "; ".join(flags.get("committee", [])),
            "has_committee": bool(flags.get("committee")),
            "call_options": flags.get("call", False),
            "unusual_size": flags.get("unusual_size", False),
            "first_buy": flags.get("first_buy", False),
            "managed_only": flags.get("managed_only", False),
            "best_hit_rate": tr[1] if tr else None,
            "best_avg_excess": tr[2] if tr else None,
            "last_buy": buys["tx_date"].max(),
            "last_filed": buys["filed"].max() if "filed" in buys and buys["filed"].notna().any() else None,
            "perf_since_first_buy": perf,
            "market_cap": mcap,
            "price": price,
            "forward_pe": f.get("forwardPE"),
            "peg": f.get("trailingPegRatio"),
            "rev_growth": f.get("revenueGrowth"),
            "roe": f.get("returnOnEquity"),
            "debt_to_equity": f.get("debtToEquity"),
            "analyst_rating": f.get("recommendationMean"),
            "analyst_upside": (tgt / price - 1) if price and tgt else None,
            "off_52w_high": (price / f["fiftyTwoWeekHigh"] - 1) if price and f.get("fiftyTwoWeekHigh") else None,
            "above_200d": (price > f["twoHundredDayAverage"]) if price and f.get("twoHundredDayAverage") else None,
            "reasons": why,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # Gleichstand (z. B. mehrere mit 100) nach dem Politiker-Signal auflösen
    return df.sort_values(["score", "insider_score"], ascending=False).reset_index(drop=True)
