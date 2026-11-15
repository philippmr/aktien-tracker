"""HTML-Newsletter für die tägliche Mail.

Mail-HTML ist eigen: nur Tabellen-Layout und Inline-Styles, damit es in Gmail, Apple Mail und
Outlook gleich aussieht. Dunkelmodus über einen <style>-Block (Apple Mail, iOS, viele Android-Clients).
"""
import datetime as dt
import html

import pandas as pd

BRAND = "Capitol Radar"
TAGLINE = "Was Washington kauft – bewertet und gefiltert"

# Farbpalette
INK, MUTED, LINE, BG, CARD = "#0f172a", "#64748b", "#e2e8f0", "#f1f5f9", "#ffffff"
NAVY, NAVY2, GOLD = "#0b1f44", "#1d3d7a", "#f5b301"
GOOD, MID, LOW = "#16a34a", "#d97706", "#94a3b8"
FONT = "font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica Neue,Arial,sans-serif"

STYLE = """
<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">
<style>
  @media (prefers-color-scheme: dark) {
    .page { background:#0b0f17 !important; }
    .card { background:#151b27 !important; border-color:#263042 !important; }
    .ink { color:#e5e9f0 !important; } .mut { color:#94a3b8 !important; }
    .line { border-color:#263042 !important; } .tile { background:#151b27 !important; border-color:#263042 !important; }
    .track { background:#263042 !important; } .chip { background:#1e2636 !important; color:#cbd5e1 !important; }
  }
  @media (max-width:480px) { .h1 { font-size:24px !important; } .big { font-size:30px !important; } }
</style>"""

e = html.escape


def _score_color(s):
    return GOOD if s >= 60 else MID if s >= 45 else LOW


def _money(v):
    if v >= 1e6:
        return f"{v / 1e6:.1f}".rstrip("0").rstrip(".") + " Mio $"
    return f"{v / 1e3:.0f} Tsd $"


def _span(lo, hi):
    lo = 0 if lo is None or pd.isna(lo) else lo
    hi = lo if hi is None or pd.isna(hi) else hi
    if not lo:
        return "–"
    if hi > 5e7 and lo > 5e7:
        return "über 50 Mio $"
    a, b = _money(lo), _money(hi)
    if a.split()[-2:] == b.split()[-2:]:  # gleiche Einheit -> "1–15 Tsd $"
        return f"{a.split()[0]}–{b}"
    return f"{a} – {b}"


def _bar(score, width="100%"):
    """Score-Balken in Prozent-Breite, damit er auf schmalen Handys nicht überläuft."""
    pct = max(3, min(100, int(score)))
    return (f"<table role='presentation' cellspacing='0' cellpadding='0' width='{width}' style='width:{width};max-width:100%'><tr>"
            f"<td width='{pct}%' height='6' bgcolor='{_score_color(score)}' style='border-radius:3px;font-size:0;line-height:0'>&nbsp;</td>"
            f"<td class='track' width='{100 - pct}%' height='6' bgcolor='{LINE}' style='font-size:0;line-height:0'>&nbsp;</td>"
            f"</tr></table>")


def _chip(text):
    return (f"<span class='chip' style='display:inline-block;background:{BG};color:#334155;border-radius:999px;"
            f"padding:3px 9px;font-size:12px;margin:0 4px 4px 0'>{text}</span>")


def _section(title, kicker=""):
    k = f"<div class='mut' style='font-size:11px;letter-spacing:1.2px;text-transform:uppercase;color:{MUTED};font-weight:700'>{kicker}</div>" if kicker else ""
    return (f"<tr><td style='padding:28px 24px 10px'>{k}"
            f"<div class='ink' style='font-size:19px;font-weight:800;color:{INK};margin-top:2px'>{title}</div></td></tr>")


def build(buys: pd.DataFrame, sells: pd.DataFrame, info, strong_t: list[str], dashboard_url=None) -> tuple[str, str]:
    """buys: neue Käufe (mit Spalte _score); info(ticker) -> Ranking-Zeile als dict."""
    today = dt.date.today()
    tickers = list(dict.fromkeys(buys["ticker"]))
    top = tickers[0]
    ti = info(top)
    n_people = buys["person"].nunique()
    gov = buys.loc[buys["source"] == "Exekutive", "person"].nunique()
    vol_lo = buys["amount_low"].fillna(0).sum()
    vol_hi = buys["amount_high"].fillna(buys["amount_low"]).fillna(0).sum()

    rows = []

    # ---------------------------------------------------------------- Kopf
    rows.append(
        f"<tr><td bgcolor='{NAVY}' style='background:{NAVY};background-image:linear-gradient(135deg,{NAVY} 0%,{NAVY2} 100%);"
        f"padding:26px 24px 24px;border-radius:16px 16px 0 0'>"
        f"<table role='presentation' width='100%' cellspacing='0' cellpadding='0'><tr>"
        f"<td style='font-size:13px;font-weight:800;letter-spacing:2px;color:{GOLD}'>★ {BRAND.upper()}</td>"
        f"<td align='right' style='font-size:12px;color:#c7d2fe'>{today:%d.%m.%Y}</td></tr></table>"
        f"<div class='h1' style='font-size:28px;line-height:1.2;font-weight:800;color:#ffffff;margin-top:14px'>"
        f"{len(buys)} neue Käufe aus Washington</div>"
        f"<div style='font-size:14px;line-height:1.5;color:#c7d2fe;margin-top:6px'>"
        f"{n_people} {'Politiker' if n_people != 1 else 'Politiker'} haben {len(tickers)} Aktien gekauft"
        f"{f' – darunter {gov} Regierungsmitglied' + ('er' if gov > 1 else '') if gov else ''}. "
        f"Top-Signal heute: <b style='color:#ffffff'>{e(top)}</b>.</div></td></tr>")

    # ---------------------------------------------------------------- 4 Kacheln
    def tile(label, value, sub, color=INK):
        return (f"<td class='tile' width='50%' valign='top' style='background:{CARD};border:1px solid {LINE};"
                f"border-radius:14px;padding:14px 16px'>"
                f"<div class='mut' style='font-size:11px;letter-spacing:1px;text-transform:uppercase;color:{MUTED};font-weight:700'>{label}</div>"
                f"<div class='big ink' style='font-size:34px;line-height:1.15;font-weight:800;color:{color};margin-top:4px'>{value}</div>"
                f"<div class='mut' style='font-size:12px;color:{MUTED};margin-top:2px'>{sub}</div></td>")

    vol_big = (f"{vol_hi / 1e6:.1f}".replace(".", ",") + " Mio") if vol_hi >= 1e6 else f"{vol_hi / 1e3:.0f} Tsd"
    t = [tile("Neue Käufe", len(buys), f"in {len(tickers)} Aktien"),
         tile("Starke Signale", len(strong_t), e(" · ".join(strong_t[:3])) or "heute keine", GOLD if strong_t else INK),
         tile("Käufer", n_people, f"davon {gov} Regierung" if gov else "Kongress"),
         tile("Volumen", vol_big, f"$ maximal · Spanne {_span(vol_lo, vol_hi)}")]
    gap = "<td width='10' style='width:10px;font-size:0'>&nbsp;</td>"
    rows.append(
        f"<tr><td style='padding:18px 24px 0'><table role='presentation' width='100%' cellspacing='0' cellpadding='0'>"
        f"<tr>{t[0]}{gap}{t[1]}</tr><tr><td colspan='3' height='10' style='font-size:0;line-height:10px'>&nbsp;</td></tr>"
        f"<tr>{t[2]}{gap}{t[3]}</tr></table></td></tr>")

    # ---------------------------------------------------------------- Top-Signal des Tages
    if ti:
        tb = buys[buys["ticker"] == top]
        buyers = ", ".join(dict.fromkeys(tb["person"]))
        reasons = "".join(
            f"<tr><td valign='top' style='color:{GOLD};font-size:14px;padding:3px 8px 3px 0'>●</td>"
            f"<td class='ink' style='font-size:14px;line-height:1.45;color:{INK};padding:3px 0'>{e(x)}</td></tr>"
            for x in (ti.get("reasons") or [])[:4])
        ins_txt, fun_txt = f"{ti['insider_score']:.0f}", f"{ti['fundamental_score']:.0f}"
        perf = ti.get("perf_since_first_buy")
        perf_txt = "–" if perf is None or pd.isna(perf) else f"{perf * 100:+.1f} %"

        def kpi(label, val):
            return (f"<td width='25%' valign='top' style='padding:10px 4px 0'>"
                    f"<div class='mut' style='font-size:11px;color:{MUTED}'>{label}</div>"
                    f"<div class='ink' style='font-size:16px;font-weight:700;color:{INK}'>{val}</div></td>")

        rows.append(_section("Top-Signal des Tages", "Im Fokus"))
        rows.append(
            f"<tr><td style='padding:0 24px'><table role='presentation' width='100%' cellspacing='0' cellpadding='0' "
            f"class='card' style='background:{CARD};border:1px solid {LINE};border-radius:16px'>"
            f"<tr><td style='padding:18px 18px 16px'>"
            f"<table role='presentation' width='100%' cellspacing='0' cellpadding='0'><tr>"
            f"<td valign='top'><div style='font-size:30px;font-weight:900;color:{NAVY2};letter-spacing:-0.5px' class='ink'>{e(top)}</div>"
            f"<div class='mut' style='font-size:14px;color:{MUTED}'>{e(str(ti.get('name', '')))}"
            f"{' · ' + e(str(ti.get('sector'))) if isinstance(ti.get('sector'), str) else ''}</div></td>"
            f"<td align='right' valign='top'><div style='font-size:34px;font-weight:900;color:{_score_color(ti['score'])}'>"
            f"{ti['score']:.0f}</div><div class='mut' style='font-size:11px;color:{MUTED}'>SCORE / 100</div></td>"
            f"</tr></table>"
            f"<div style='margin:10px 0 12px'>{_bar(ti['score'])}</div>"
            f"<table role='presentation' cellspacing='0' cellpadding='0'>{reasons}</table>"
            f"<table role='presentation' width='100%' cellspacing='0' cellpadding='0' class='line' "
            f"style='border-top:1px solid {LINE};margin-top:12px'><tr>"
            f"{kpi('Politiker', ins_txt)}{kpi('Fundamental', fun_txt)}"
            f"{kpi('Käufer', ti.get('n_buyers', '–'))}{kpi('Seit Kauf', perf_txt)}</tr></table>"
            f"<div class='mut' style='font-size:12px;color:{MUTED};margin-top:12px'>Neu gekauft von: {e(buyers)}</div>"
            f"</td></tr></table></td></tr>")

    # ---------------------------------------------------------------- Starke Signale
    others = [x for x in strong_t if x != top][:6]
    if others:
        rows.append(_section("Weitere starke Signale", "Watchlist"))
        items = []
        for x in others:
            i = info(x)
            why = " · ".join(e(r) for r in (i.get("reasons") or [])[:2])
            items.append(
                f"<tr><td class='line' style='padding:14px 0;border-bottom:1px solid {LINE}'>"
                f"<table role='presentation' width='100%' cellspacing='0' cellpadding='0'><tr>"
                f"<td valign='top'><span class='ink' style='font-size:17px;font-weight:800;color:{INK}'>{e(x)}</span>"
                f"<span class='mut' style='font-size:13px;color:{MUTED}'>&nbsp; {e(str(i.get('name', ''))[:30])}</span></td>"
                f"<td align='right' valign='top' style='font-size:17px;font-weight:800;color:{_score_color(i.get('score', 0))};white-space:nowrap'>"
                f"{i.get('score', 0):.0f}</td></tr></table>"
                f"<div style='margin:6px 0 6px'>{_bar(i.get('score', 0), '60%')}</div>"
                f"<div class='mut' style='font-size:13px;line-height:1.45;color:{MUTED}'>{why}</div></td></tr>")
        rows.append(f"<tr><td style='padding:0 24px'><table role='presentation' width='100%' cellspacing='0' "
                    f"cellpadding='0'>{''.join(items)}</table></td></tr>")

    # ---------------------------------------------------------------- Alle Käufe (je Aktie gruppiert)
    rows.append(_section("Alle neuen Käufe", f"{len(tickers)} Aktien"))
    th = f"class='mut line' style='font-size:11px;color:{MUTED};font-weight:700;text-align:left;padding:0 6px 8px 0;border-bottom:1px solid {LINE}'"
    lines = []
    for x in tickers[:60]:
        g = buys[buys["ticker"] == x]
        i = info(x)
        sc = i.get("score")
        people = []
        for p, pg in g.groupby("person", sort=False):
            label = e(p)
            if (pg["source"] == "Exekutive").any():
                label += f" <span class='mut' style='color:{MUTED}'>({e(str(pg['role'].iloc[0]).split(',')[0])})</span>"
            if (pg["asset_type"] == "OP").any():
                label += " <b style='color:#b45309'>Call</b>"
            people.append(label)
        lo = g["amount_low"].fillna(0).sum()
        hi = g["amount_high"].fillna(g["amount_low"]).fillna(0).sum()
        score_html = (f"<span style='display:inline-block;min-width:26px;text-align:center;border-radius:6px;padding:1px 5px;"
                      f"font-size:12px;font-weight:800;color:#ffffff;background:{_score_color(sc)}'>{sc:.0f}</span>"
                      if sc is not None and not pd.isna(sc) else f"<span class='mut' style='color:{MUTED};font-size:12px'>–</span>")
        td = f"class='line' style='padding:10px 6px 10px 0;border-bottom:1px solid {LINE};vertical-align:top"
        lines.append(
            f"<tr><td {td}'><div class='ink' style='font-size:15px;font-weight:800;color:{INK}'>{e(x)}</div>"
            f"<div class='mut' style='font-size:11px;color:{MUTED}'>{e(str(i.get('name') or g['asset'].iloc[0])[:22])}</div></td>"
            f"<td {td};font-size:13px'><span class='ink' style='color:{INK}'>{'<br>'.join(people)}</span>"
            f"<div class='mut' style='font-size:11px;color:{MUTED}'>{g['tx_date'].max():%d.%m.}"
            f"{' · ' + str(len(g)) + ' Trades' if len(g) > 1 else ''}</div></td>"
            f"<td {td};text-align:right;white-space:nowrap'><div class='ink' style='font-size:13px;color:{INK}'>{_span(lo, hi)}</div>"
            f"<div style='margin-top:4px'>{score_html}</div></td></tr>")
    more = (f"<div class='mut' style='font-size:12px;color:{MUTED};padding-top:10px'>… und {len(tickers) - 60} weitere im Dashboard.</div>"
            if len(tickers) > 60 else "")
    rows.append(f"<tr><td style='padding:0 24px'><table role='presentation' width='100%' cellspacing='0' cellpadding='0'>"
                f"<tr><th {th}>Aktie</th><th {th}>Käufer</th><th {th} align='right' style='text-align:right'>Betrag · Score</th></tr>"
                f"{''.join(lines)}</table>{more}</td></tr>")

    # ---------------------------------------------------------------- Wer kauft?
    top_people = buys.groupby("person").agg(n=("ticker", "nunique"), src=("source", "first"),
                                            role=("role", "first")).sort_values("n", ascending=False).head(5)
    if len(top_people) > 1:
        rows.append(_section("Wer kauft?", "Aktivste Käufer"))
        chips = "".join(_chip(f"<b>{e(p)}</b> · {r.n} {'Aktie' if r.n == 1 else 'Aktien'}") for p, r in top_people.iterrows())
        rows.append(f"<tr><td style='padding:0 24px'>{chips}</td></tr>")

    # ---------------------------------------------------------------- Verkäufe
    sold = sells[sells["ticker"].notna()]["ticker"].value_counts()
    if len(sold):
        rows.append(_section("Verkäufe", f"{len(sells)} Trades"))
        rows.append(f"<tr><td style='padding:0 24px'>"
                    + "".join(_chip(f"<b>{e(t)}</b>{f' ×{n}' if n > 1 else ''}") for t, n in sold.head(24).items())
                    + "</td></tr>")

    # ---------------------------------------------------------------- Fuß
    link = (f"<a href='{e(dashboard_url)}' style='color:{NAVY2};font-weight:700'>Dashboard öffnen →</a><br>"
            if dashboard_url else "<b>Das komplette Dashboard hängt als Datei an.</b><br>")
    rows.append(
        f"<tr><td style='padding:30px 24px 26px'><div class='line' style='border-top:1px solid {LINE};padding-top:16px'>"
        f"<div class='mut' style='font-size:12px;line-height:1.6;color:{MUTED}'>{link}"
        f"Score 0–100: 60 % Politiker-Signal (Ausschuss-/Ressort-Bezug, Trefferquote, Betrag, Cluster) "
        f"und 40 % Fundamentaldaten. Trades werden bis zu 45 Tage verspätet gemeldet; Beträge sind gesetzliche Spannen.<br>"
        f"Quellen: House Clerk, Senate eFD, Office of Government Ethics, Yahoo Finance.<br>"
        f"<b>Keine Anlageberatung.</b></div></div></td></tr>")

    body = (f"{STYLE}<div class='page' style='background:{BG};padding:14px 0;margin:0;{FONT}'>"
            f"<table role='presentation' align='center' width='100%' cellspacing='0' cellpadding='0' "
            f"class='card' style='max-width:600px;margin:0 auto;background:{CARD};border-radius:16px;"
            f"border:1px solid {LINE}'>{''.join(rows)}</table></div>")

    subject = f"{BRAND} · {len(buys)} neue Käufe"
    subject += f" · Top: {top}" + (f" ({ti['score']:.0f})" if ti else "")
    return subject, body
