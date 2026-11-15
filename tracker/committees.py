"""Ausschussmitgliedschaften der Kongressmitglieder und Zuordnung Ausschuss -> Branchen.

Quelle: https://github.com/unitedstates/congress-legislators (gemeinfrei, laufend gepflegt)
"""
import re
import unicodedata

import yaml

from tracker import http

RAW = "https://raw.githubusercontent.com/unitedstates/congress-legislators/main"

# Ausschuss (Kürzel ohne H/S-Präfix-Unterschied) -> (Yahoo-Sektoren, Schlagworte in Yahoo-Branchen)
# Bewusst eng gefasst: Nur wo ein Ausschuss echten Einfluss auf Aufträge, Regulierung oder
# Subventionen einer Branche hat.
COMMITTEE_SECTORS: dict[str, tuple[list[str], list[str]]] = {
    # Agriculture
    "HSAG": ([], ["farm", "agricultur", "food", "tobacco", "fertiliz"]),
    "SSAF": ([], ["farm", "agricultur", "food", "tobacco", "fertiliz"]),
    # Armed Services
    "HSAS": ([], ["aerospace", "defense", "information technology services", "shipbuild"]),
    "SSAS": ([], ["aerospace", "defense", "information technology services", "shipbuild"]),
    # Financial Services / Banking
    "HSBA": (["Financial Services", "Real Estate"], []),
    "SSBK": (["Financial Services", "Real Estate"], []),
    # Energy & Commerce (House) - sehr breit
    "HSIF": (["Energy", "Utilities", "Healthcare", "Communication Services"],
             ["software", "semiconductor", "internet"]),
    # Natural Resources / Energy
    "HSII": (["Energy"], ["gold", "silver", "copper", "metals", "mining", "uranium", "coal", "lumber"]),
    "SSEG": (["Energy", "Utilities"], ["gold", "copper", "metals", "mining", "uranium", "coal"]),
    # Transportation / Infrastructure
    "HSPW": ([], ["airline", "railroad", "trucking", "freight", "engineering & construction",
                  "building materials", "infrastructure", "auto", "marine shipping"]),
    "SSEV": (["Utilities"], ["engineering & construction", "waste", "building materials", "pollution"]),
    # Science, Space & Technology
    "HSSY": (["Technology"], ["aerospace", "defense"]),
    # Commerce, Science & Transportation (Senate)
    "SSCM": (["Communication Services", "Technology"],
             ["airline", "railroad", "trucking", "freight", "auto", "aerospace"]),
    # Health / Medicare
    "SSHR": (["Healthcare"], ["education"]),
    "HSWM": ([], ["healthcare plans", "medical care", "insurance"]),
    "SSFI": ([], ["healthcare plans", "medical care", "drug manufacturers", "insurance"]),
    "HSVR": (["Healthcare"], []),
    "SSVA": (["Healthcare"], []),
    "SPAG": (["Healthcare"], []),
    "HSED": ([], ["education"]),
    # Sicherheit / Geheimdienste
    "HSHM": ([], ["aerospace", "defense", "security", "software—infrastructure", "information technology services"]),
    "SSGA": ([], ["aerospace", "defense", "security", "software—infrastructure", "information technology services"]),
    "HLIG": ([], ["aerospace", "defense", "software—infrastructure", "information technology services", "communication equipment"]),
    "SLIN": ([], ["aerospace", "defense", "software—infrastructure", "information technology services", "communication equipment"]),
    # Haushalt: verteilt Geld, v. a. Rüstung und Behörden-IT
    "HSAP": ([], ["aerospace", "defense", "information technology services"]),
    "SSAP": ([], ["aerospace", "defense", "information technology services"]),
    # China-Ausschuss
    "HSZS": ([], ["semiconductor", "rare", "metals", "mining"]),
}


# Behörde / Amt (Exekutive) -> (Yahoo-Sektoren, Branchen-Schlagworte). Schlüssel = Teilstring
# der OGE-Angaben "agency"/"title" (Kleinschreibung).
AGENCY_SECTORS: dict[str, tuple[list[str], list[str]]] = {
    # Präsident / Vizepräsident bewusst NICHT pauschal: Ihre Depots werden von Verwaltern geführt
    # (Hunderte Trades pro Bericht); ein Pauschal-Bonus würde das Ranking verzerren.
    "energy": (["Energy", "Utilities"], ["uranium", "coal", "solar"]),
    "interior": (["Energy"], ["gold", "copper", "metals", "mining", "uranium", "coal", "lumber"]),
    "treasury": (["Financial Services"], []),
    "federal reserve": (["Financial Services", "Real Estate"], []),
    "commerce": (["Technology", "Industrials", "Communication Services"], ["semiconductor", "steel", "aluminum"]),
    "defense": ([], ["aerospace", "defense", "information technology services", "shipbuild"]),
    "army": ([], ["aerospace", "defense"]), "navy": ([], ["aerospace", "defense", "shipbuild"]),
    "air force": ([], ["aerospace", "defense"]),
    "transportation": ([], ["airline", "railroad", "trucking", "freight", "auto", "engineering & construction"]),
    "federal aviation": ([], ["airline", "aerospace"]),
    "federal highway": ([], ["engineering & construction", "building materials", "infrastructure"]),
    "health and human": (["Healthcare"], []), "food and drug": (["Healthcare"], []),
    "medicare": (["Healthcare"], []), "national institutes of health": (["Healthcare"], []),
    "agriculture": ([], ["farm", "agricultur", "food", "fertiliz"]),
    "environmental protection": (["Utilities", "Energy"], ["waste", "pollution", "chemical"]),
    "federal communications": (["Communication Services"], []),
    "securities and exchange": (["Financial Services"], []),
    "housing and urban": (["Real Estate"], ["homebuild", "building"]),
    "science and technology": (["Technology"], []),
    "nasa": ([], ["aerospace"]), "national aeronautics": ([], ["aerospace"]),
    "veterans": (["Healthcare"], []), "education": ([], ["education"]),
}


def agency_relevant(role: str, sector: str | None, industry: str | None) -> str | None:
    """Gibt das zuständige Amt zurück, wenn die Aktie in dessen Zuständigkeit fällt."""
    txt = lambda v: v if isinstance(v, str) else ""  # fehlende Werte kommen als NaN (float)
    r, sector, industry = txt(role).lower(), txt(sector), txt(industry).lower()
    for key, (sectors, kws) in AGENCY_SECTORS.items():
        if key in r and (sector in sectors or any(k in industry for k in kws)):
            return role
    return None


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


class Committees:
    def __init__(self):
        load = lambda f: yaml.safe_load(http.get(f"{RAW}/{f}", cache="legislators", max_age_h=24))
        self.legislators = load("legislators-current.yaml")
        committees = load("committees-current.yaml")
        membership = load("committee-membership-current.yaml")

        self.committee_names = {c["thomas_id"]: c["name"] for c in committees}
        for c in committees:  # Unterausschüsse
            for sc in c.get("subcommittees", []):
                self.committee_names[c["thomas_id"] + sc["thomas_id"]] = f"{c['name']}: {sc['name']}"

        # bioguide -> [(Ausschuss-ID, Titel)]
        self.member_of: dict[str, list[tuple[str, str]]] = {}
        for cid, members in membership.items():
            for m in members:
                self.member_of.setdefault(m["bioguide"], []).append((cid, m.get("title", "")))

        # Indizes für die Zuordnung Name/Wahlkreis -> bioguide
        self.by_district: dict[str, dict] = {}
        self.senators: list[dict] = []
        self.by_bioguide: dict[str, dict] = {}
        for leg in self.legislators:
            term = leg["terms"][-1]
            info = {
                "bioguide": leg["id"]["bioguide"],
                "name": leg["name"].get("official_full") or f"{leg['name']['first']} {leg['name']['last']}",
                "first": _norm(leg["name"]["first"]), "last": _norm(leg["name"]["last"]),
                "nick": _norm(leg["name"].get("nickname", "")),
                "party": term.get("party", ""), "state": term["state"], "chamber": term["type"],
            }
            self.by_bioguide[info["bioguide"]] = info
            if term["type"] == "rep":
                d = term.get("district", 0)
                self.by_district[f"{term['state']}{int(d):02d}"] = info
            else:
                self.senators.append(info)

    # ------------------------------------------------------------------ Zuordnung
    def match(self, name: str, state_district: str | None = None, chamber: str = "rep") -> dict | None:
        if chamber == "rep" and state_district:
            sd = state_district.strip().upper()
            key = sd[:2] + (f"{int(sd[2:]):02d}" if sd[2:].isdigit() else "00")
            info = self.by_district.get(key)
            if info and info["last"] in _norm(name):
                return info
            if info and len(sd) > 2:  # Wahlkreis passt, Name evtl. anders geschrieben
                return info
        pool = self.senators if chamber == "sen" else list(self.by_bioguide.values())
        n = _norm(name)
        cands = [p for p in pool if p["last"] and re.search(rf"\b{re.escape(p['last'])}\b", n)]
        if len(cands) > 1:
            cands = [p for p in cands if p["first"].split()[0] in n or (p["nick"] and p["nick"] in n)] or cands
        return cands[0] if len(cands) == 1 else None

    # ------------------------------------------------------------------ Relevanz
    def relevant_committees(self, bioguide: str, sector: str | None, industry: str | None) -> list[str]:
        """Ausschüsse dieses Politikers, die für Sektor/Branche der Aktie zuständig sind."""
        out = []
        txt = lambda v: v if isinstance(v, str) else ""
        sector, industry = txt(sector), txt(industry).lower()
        for cid, title in self.member_of.get(bioguide, []):
            if len(cid) != 4 or cid not in COMMITTEE_SECTORS:
                continue  # nur Hauptausschüsse
            sectors, kws = COMMITTEE_SECTORS[cid]
            if sector in sectors or any(k in industry for k in kws):
                label = self.committee_names.get(cid, cid)
                label = re.sub(r"^(House|Senate) (Permanent Select |Select |Special )?Committee on (the )?", "", label)
                out.append(f"{label}{' (' + title + ')' if title else ''}")
        return out

    def is_leader(self, bioguide: str, cid_label: str) -> bool:
        return "(Chair" in cid_label or "(Ranking" in cid_label
