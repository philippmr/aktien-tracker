"""Historische Fundamentaldaten aus den SEC-Pflichtmeldungen (XBRL "companyfacts").

Damit lassen sich Fundamentaldaten im Backtest so bewerten, wie sie ZUM MELDEZEITPUNKT
öffentlich bekannt waren (jede Zahl hat ein "filed"-Datum). Quelle: https://data.sec.gov
Nur US-Firmen, die bei der SEC berichten (ausländische Firmen wie TSM oft nicht).
"""
import datetime as dt
import json
import os

import config
from tracker import http

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "net_income": ["NetIncomeLoss"],
    "op_income": ["OperatingIncomeLoss"],
    "liabilities": ["Liabilities"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "debt_lt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "debt_st": ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"],
    "shares": ["EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"],
}
VERSION = 2   # erhöhen, wenn sich TAGS ändern -> Cache wird neu aufgebaut
DURATION = {"revenue", "net_income", "op_income", "ocf", "capex"}   # Zeitraum-Werte, Rest Stichtag


def _cik_map() -> dict[str, int]:
    raw = http.get(TICKERS_URL, cache="oge", max_age_h=24 * 7)
    return {v["ticker"].upper(): int(v["cik_str"]) for v in json.loads(raw).values()} if raw else {}


def _compact(facts: dict) -> dict:
    """Nur die benötigten Kennzahlen: Liste (start, end, val, filed) je Kennzahl."""
    gaap = dict(facts.get("facts", {}).get("us-gaap", {}))
    gaap.update(facts.get("facts", {}).get("dei", {}))   # Aktienanzahl steht im "dei"-Bereich
    out = {}
    for key, tags in TAGS.items():
        # Firmen wechseln über die Jahre das Feld (z. B. "Revenues" -> "RevenueFromContract...").
        # Alle Felder zusammenführen; je Zeitraum gilt das Feld mit der höchsten Priorität.
        best = {}
        for prio, tag in enumerate(tags):
            units = gaap.get(tag, {}).get("units", {})
            for u in units.get("USD", []) or units.get("shares", []):
                if u.get("form") not in ("10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F"):
                    continue
                k = (u.get("start"), u["end"], u["filed"])
                if k not in best or prio < best[k][0]:
                    best[k] = (prio, (u.get("start"), u["end"], u["val"], u["filed"]))
        out[key] = [v[1] for v in best.values()]
    return out


def load(ticker: str, cik_map: dict | None = None) -> dict | None:
    d = os.path.join(config.CACHE_DIR, "secfacts")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{ticker.replace('/', '_')}_v{VERSION}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    cik = (cik_map or _cik_map()).get(ticker.upper().replace(".", "-"))
    if not cik:
        return None
    raw = http.get(FACTS_URL.format(cik=cik))
    data = _compact(json.loads(raw)) if raw else None
    with open(path, "w") as f:
        json.dump(data, f)
    return data


def _days(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def at(data: dict | None, when: dt.date) -> dict:
    """Kennzahlen, wie sie am Tag `when` aus den bis dahin eingereichten Berichten bekannt waren."""
    if not data:
        return {}
    w = when.isoformat()
    known = {k: [r for r in v if r[3] <= w] for k, v in data.items()}

    def annual(key, back=0):
        rows = [r for r in known.get(key, []) if r[0] and 350 <= _days(r[0], r[1]) <= 380]
        rows = sorted({r[1]: r for r in rows}.values(), key=lambda r: r[1])  # je Periodenende einmal
        return rows[-1 - back][2] if len(rows) > back else None

    def quarter_yoy(key):
        rows = [r for r in known.get(key, []) if r[0] and 80 <= _days(r[0], r[1]) <= 100]
        by_end = {r[1]: r[2] for r in rows}
        if not by_end:
            return None
        last = max(by_end)
        target = dt.date.fromisoformat(last) - dt.timedelta(days=365)
        prev = [e for e in by_end if abs((dt.date.fromisoformat(e) - target).days) <= 20]
        return (by_end[last] / by_end[prev[0]] - 1) if prev and by_end[prev[0]] else None

    def latest(key):
        rows = sorted(known.get(key, []), key=lambda r: r[1])
        return rows[-1][2] if rows else None

    def quarters(key):
        rows = [r for r in known.get(key, []) if r[0] and 80 <= _days(r[0], r[1]) <= 100]
        return dict(sorted({r[1]: r[2] for r in rows}.items()))

    def yoy_at(series: dict, idx: int):
        ends = list(series)
        if len(ends) <= idx:
            return None
        e = ends[-1 - idx]
        target = dt.date.fromisoformat(e) - dt.timedelta(days=365)
        prev = [x for x in ends if abs((dt.date.fromisoformat(x) - target).days) <= 20]
        return (series[e] / series[prev[0]] - 1) if prev and series[prev[0]] else None

    rq, oq = quarters("revenue"), quarters("op_income")
    common = [e for e in rq if e in oq and rq[e]]
    opm_q = {e: oq[e] / rq[e] for e in common}
    opm_ends = list(opm_q)

    rev, rev_prev = annual("revenue"), annual("revenue", 1)
    ni, op, ocf, capex = annual("net_income"), annual("op_income"), annual("ocf"), annual("capex")
    liab, eq = latest("liabilities"), latest("equity")
    out = {
        "rev_growth": (rev / rev_prev - 1) if rev and rev_prev and rev_prev > 0 else None,
        "rev_growth_q": quarter_yoy("revenue"),
        "net_margin": (ni / rev) if ni is not None and rev else None,
        "op_margin": (op / rev) if op is not None and rev else None,
        "profitable": (ni > 0) if ni is not None else None,
        "fcf_positive": ((ocf - (capex or 0)) > 0) if ocf is not None else None,
        "liab_to_equity": (liab / eq) if liab is not None and eq and eq > 0 else None,
        "rev_growth_q_prev": yoy_at(rq, 1),
        "opm_last": opm_q[opm_ends[-1]] if opm_ends else None,
        "opm_year_ago": (opm_q[opm_ends[-5]] if len(opm_ends) >= 5 else None),
        "cash": latest("cash"),
        "debt": (latest("debt_lt") or 0) + (latest("debt_st") or 0) if latest("debt_lt") is not None else None,
        "shares": latest("shares"),
        "net_income": ni, "revenue_annual": rev, "fcf": (ocf - (capex or 0)) if ocf is not None else None,
        "equity": eq, "op_income_annual": op,
    }
    return out
