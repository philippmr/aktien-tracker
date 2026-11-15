"""Erzeugt ein eigenständiges HTML-Dashboard plus CSV-Dateien."""
import datetime as dt
import json
import math
import os

import pandas as pd

import config


def _clean(v):
    if isinstance(v, (dt.date, dt.datetime, pd.Timestamp)):
        return v.isoformat()[:10]
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if hasattr(v, "item"):  # numpy-Typen
        return _clean(v.item())
    if v is pd.NA or v is pd.NaT:
        return None
    return v


def _records(df: pd.DataFrame) -> list[dict]:
    return [{k: _clean(v) for k, v in r.items()} for r in df.to_dict("records")]


def write(ranking: pd.DataFrame, trades: pd.DataFrame, leaderboard: pd.DataFrame, stats: dict) -> str:
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    stamp = dt.date.today().isoformat()

    trades = trades.sort_values(["filed", "tx_date"], ascending=False)
    ranking.to_csv(os.path.join(config.OUTPUT_DIR, f"ranking_{stamp}.csv"), index=False)
    trades.to_csv(os.path.join(config.OUTPUT_DIR, f"trades_{stamp}.csv"), index=False)
    leaderboard.to_csv(os.path.join(config.OUTPUT_DIR, f"politiker_{stamp}.csv"), index=False)

    top = ranking.head(config.TOP_N)
    tickers = set(top["ticker"]) if len(top) else set()
    cols = ["source", "person", "role", "filed", "url", "ticker", "asset_type", "asset", "owner",
            "type", "tx_date", "amount_low", "amount_high", "description", "filing_size"]
    by_ticker = {t: _records(g[cols]) for t, g in trades[trades["ticker"].isin(tickers)].groupby("ticker")}
    feed = trades[trades["ticker"].notna() & trades["asset_type"].isin(["ST", "OP"])][cols]
    payload = {
        "generated": dt.datetime.now().strftime("%d.%m.%Y %H:%M"),
        "stats": stats,
        "horizon": config.TRACK_RECORD_HORIZON_DAYS,
        "ranking": _records(top),
        "trades_by_ticker": by_ticker,
        "feed": _records(feed.head(3000)),
        "leaderboard": _records(leaderboard),
    }
    html = TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    path = os.path.join(config.OUTPUT_DIR, "dashboard.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


TEMPLATE = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Politiker-Insider-Tracker</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--text:#1a1d23;--muted:#667085;--line:#e4e7ec;--accent:#2563eb;
--good:#15803d;--bad:#b91c1c;--chip:#eef2ff;--warn:#fff4e5;--warntext:#9a5b00}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1115;--card:#171a21;--text:#e6e8ec;
--muted:#98a2b3;--line:#2a2f3a;--accent:#6c9cff;--good:#4ade80;--bad:#f87171;--chip:#1e2640;--warn:#3a2a10;--warntext:#fbbf24}}
:root[data-theme=dark]{--bg:#0f1115;--card:#171a21;--text:#e6e8ec;--muted:#98a2b3;--line:#2a2f3a;
--accent:#6c9cff;--good:#4ade80;--bad:#f87171;--chip:#1e2640;--warn:#3a2a10;--warntext:#fbbf24}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1440px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:22px;margin:0 0 4px}.sub{color:var(--muted);margin-bottom:16px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:16px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:120px}
.stat b{display:block;font-size:20px}.stat span{color:var(--muted);font-size:12px}
.tabs{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap}.tab{padding:7px 14px;border-radius:8px;border:1px solid var(--line);
background:var(--card);color:var(--text);cursor:pointer;font:inherit}.tab.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;align-items:center}
.filters input,.filters select{padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--text);font:inherit}
.filters label{display:flex;gap:5px;align-items:center;color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow-x:auto}
table{border-collapse:collapse;width:100%;min-width:900px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{position:sticky;top:0;background:var(--card);font-size:12px;color:var(--muted);cursor:pointer;user-select:none}
th:hover{color:var(--text)}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr.row{cursor:pointer}tr.row:hover td{background:var(--chip)}
.tk{font-weight:700;color:var(--accent)}.nm{color:var(--muted);max-width:200px;overflow:hidden;text-overflow:ellipsis}
.score{display:inline-flex;align-items:center;gap:6px}.bar{width:56px;height:6px;border-radius:3px;background:var(--line);overflow:hidden;display:inline-block}
.bar i{display:block;height:100%;background:var(--accent)}
.pos{color:var(--good)}.neg{color:var(--bad)}
.chip{display:inline-block;padding:1px 8px;border-radius:10px;background:var(--chip);font-size:12px;margin:2px 4px 2px 0}
.tag{display:inline-block;padding:0 6px;border-radius:6px;font-size:11px;margin-right:3px;background:var(--chip)}
.tag.c{background:var(--warn);color:var(--warntext)}
.detail td{background:var(--bg);white-space:normal}
.dgrid{display:grid;grid-template-columns:minmax(240px,1fr) 2fr;gap:18px;padding:6px 2px}
@media(max-width:800px){.dgrid{grid-template-columns:1fr}}
.pill{display:flex;align-items:center;gap:8px;margin:4px 0}.pill span:first-child{width:80px;color:var(--muted);font-size:12px}
.pill .bar{width:140px}.mini{min-width:0}.mini td,.mini th{padding:4px 8px;font-size:12px}
.why li{margin:3px 0}.why{padding-left:18px;margin:6px 0}
a{color:var(--accent)}.note{color:var(--muted);font-size:12px;margin-top:14px}
.themebtn{float:right;border:1px solid var(--line);background:var(--card);color:var(--text);border-radius:8px;padding:5px 10px;cursor:pointer;font:inherit}
.desc{color:var(--muted);font-size:11px;max-width:320px;white-space:normal}
</style>
</head>
<body>
<div class="wrap">
<button class="themebtn" onclick="toggleTheme()">◐ Theme</button>
<h1>Politiker-Insider-Tracker</h1>
<div class="sub">Aktienkäufe von US-Kongress und Regierung · Ausschuss-/Ressort-Bezug · Trefferquote · Fundamentaldaten · Stand <span id="gen"></span> · <a href="backtest.html">Backtest: Wie gut ist der Score?</a></div>
<div class="stats" id="stats"></div>
<div class="tabs"><button class="tab on" data-t="rank">Aktien-Ranking</button><button class="tab" data-t="pol">Politiker-Trefferquote</button><button class="tab" data-t="feed">Alle Trades</button></div>

<div id="rank">
 <div class="filters">
  <input id="q" placeholder="Ticker, Name oder Politiker">
  <select id="sector"><option value="">Alle Sektoren</option></select>
  <select id="minscore"><option value="0">Score ≥ 0</option><option value="50">≥ 50</option><option value="60">≥ 60</option><option value="70">≥ 70</option></select>
  <label><input type="checkbox" id="fcom"> nur Ausschuss-/Ressort-Bezug</label>
  <label><input type="checkbox" id="fclu"> ≥ 2 Politiker</label>
  <label><input type="checkbox" id="fcall"> Call-Optionen</label>
  <label><input type="checkbox" id="fcon"> Staatsauftrag nach Kauf</label>
  <label><input type="checkbox" id="fman"> Verwalter-Depots ausblenden</label>
 </div>
 <div class="card"><table id="rt"><thead></thead><tbody></tbody></table></div>
</div>

<div id="pol" hidden>
 <div class="filters"><input id="pq" placeholder="Politiker suchen">
  <span class="note" style="margin:0">Überrendite = Rendite eines Kaufs über <span id="hz"></span> Tage minus S&amp;P 500 (SPY) im selben Zeitraum. Nur Politiker mit genug bewerteten Käufen.</span></div>
 <div class="card"><table id="pt"><thead></thead><tbody></tbody></table></div>
</div>

<div id="feed" hidden>
 <div class="filters">
  <input id="fq" placeholder="Ticker / Politiker suchen">
  <select id="fsrc"><option value="">Alle Quellen</option><option>House</option><option>Senat</option><option>Exekutive</option></select>
  <select id="ftype"><option value="">Käufe & Verkäufe</option><option>Kauf</option><option value="sell">Verkäufe</option></select>
 </div>
 <div class="card"><table id="ft"><thead></thead><tbody></tbody></table></div>
</div>

<p class="note">Keine Anlageberatung. Beträge werden nur als Spanne gemeldet; Trades erscheinen bis zu 45 Tage verspätet.
„Verwalter-Depot“ = Meldung mit sehr vielen Trades, meist von einem Vermögensverwalter ausgeführt (schwaches Signal).
Fundamentaldaten von Yahoo Finance, Analysten-Rating: 1 = Strong Buy, 5 = Sell.</p>
</div>
<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
const fmtPct = v => v==null ? "–" : `<span class="${v>=0?'pos':'neg'}">${v>0?"+":""}${(v*100).toFixed(1)} %</span>`;
const fmtMoney = v => { if(v==null||v===0) return "–"; const a=Math.abs(v);
  return a>=1e12?(v/1e12).toFixed(2)+" Bio $":a>=1e9?(v/1e9).toFixed(1)+" Mrd $":a>=1e6?(v/1e6).toFixed(1)+" Mio $":a>=1e3?(v/1e3).toFixed(0)+" Tsd $":v.toFixed(0)+" $"; };
const fmtRange = (lo,hi) => lo==null ? "–" : (hi&&hi!==lo ? fmtMoney(lo)+" – "+fmtMoney(hi) : fmtMoney(lo));
const fmtNum = (v,d=1) => v==null ? "–" : v.toFixed(d);
const fmtDate = s => s ? s.split("-").reverse().join(".") : "–";
const esc = s => String(s??"").replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const bar = v => `<span class="score"><span class="bar"><i style="width:${Math.max(0,Math.min(100,v||0))}%"></i></span>${fmtNum(v,0)}</span>`;

function toggleTheme(){const r=document.documentElement;const dark=r.dataset.theme?r.dataset.theme==="dark":matchMedia("(prefers-color-scheme: dark)").matches;
 r.dataset.theme=dark?"light":"dark";try{localStorage.setItem("theme",r.dataset.theme)}catch(e){}}
try{const t=localStorage.getItem("theme");if(t)document.documentElement.dataset.theme=t}catch(e){}

$("#gen").textContent = D.generated; $("#hz").textContent = D.horizon;
$("#stats").innerHTML = Object.entries(D.stats).map(([k,v])=>`<div class="stat"><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join("");
document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>{document.querySelectorAll(".tab").forEach(x=>x.classList.toggle("on",x===b));
 ["rank","pol","feed"].forEach(id=>$("#"+id).hidden=b.dataset.t!==id);});

// ------------------------------------------------------------- Aktien-Ranking
const tags = r => (r.contracts?'<span class="tag c">Staatsauftrag</span>':'')+(r.has_committee?'<span class="tag">Zuständig</span>':'')+(r.call_options?'<span class="tag c">Calls</span>':'')+
  (r.unusual_size?'<span class="tag">groß</span>':'')+(r.n_buyers>=2?'<span class="tag">Cluster</span>':'')+(r.managed_only?'<span class="tag">Verwalter</span>':'');
const RC = [
 ["#","_rank",r=>r._rank,1],["Ticker","ticker",r=>`<span class="tk">${esc(r.ticker)}</span>`],
 ["Name","name",r=>`<div class="nm" title="${esc(r.name)}">${esc(r.name)}</div>`],
 ["Signale","has_committee",r=>tags(r)],
 ["Score","score",r=>bar(r.score),1],["Politiker","insider_score",r=>fmtNum(r.insider_score,0),1],
 ["Fundamental","fundamental_score",r=>fmtNum(r.fundamental_score,0),1],
 ["Käufer","n_buyers",r=>r.n_buyers,1],["Volumen (Mitte)","volume_mid",r=>fmtMoney(r.volume_mid),1],
 ["Bester Track Record","best_avg_excess",r=>r.best_avg_excess==null?"–":fmtPct(r.best_avg_excess)+` <span class="nm">(${(r.best_hit_rate*100).toFixed(0)} % Treffer)</span>`,1],
 ["Letzter Kauf","last_buy",r=>fmtDate(r.last_buy)],["Seit 1. Kauf","perf_since_first_buy",r=>fmtPct(r.perf_since_first_buy),1],
 ["Sektor","sector",r=>esc(r.sector||"–")],["Marktkap.","market_cap",r=>fmtMoney(r.market_cap),1],
 ["KGV fwd","forward_pe",r=>fmtNum(r.forward_pe),1],["Analysten-Ziel","analyst_upside",r=>fmtPct(r.analyst_upside),1],
];
D.ranking.forEach((r,i)=>r._rank=i+1);
let rSort={k:"score",dir:-1}, open=new Set();
[...new Set(D.ranking.map(r=>r.sector).filter(Boolean))].sort().forEach(s=>$("#sector").insertAdjacentHTML("beforeend",`<option>${esc(s)}</option>`));
$("#rt thead").innerHTML="<tr>"+RC.map(c=>`<th class="${c[3]?'num':''}" data-k="${c[1]}">${c[0]}</th>`).join("")+"</tr>";
$("#rt thead").onclick=e=>{const k=e.target.dataset.k;if(!k)return;rSort={k,dir:rSort.k===k?-rSort.dir:-1};renderRank();};

function tradeRows(tr){return tr.map(t=>`<tr><td>${fmtDate(t.tx_date)}</td><td>${fmtDate(t.filed)}</td><td>${esc(t.person)}</td><td>${esc(t.role||"")}</td>
  <td>${esc(t.owner||"")}</td><td class="${t.type==='Kauf'?'pos':'neg'}">${esc(t.type)}${t.asset_type==="OP"?" (Option)":""}</td>
  <td class="num">${fmtRange(t.amount_low,t.amount_high)}</td><td class="desc">${esc(t.description||"")}${(t.filing_size||0)>=15?' <span class="tag">Verwalter</span>':''}</td>
  <td><a href="${esc(t.url)}" target="_blank" rel="noopener">Meldung</a></td></tr>`).join("");}

function detail(r){
 const tr=(D.trades_by_ticker[r.ticker]||[]);
 const pills=["Bewertung","Qualität","Wachstum","Bilanz"].map(p=>{const v=r["p_"+p];
  return `<div class="pill"><span>${p}</span><span class="bar"><i style="width:${v==null?0:v*4}%"></i></span><span>${v==null?"k. A.":fmtNum(v,0)+" / 25"}</span></div>`}).join("");
 const facts=[["Branche",esc(r.industry||"–")],["PEG",fmtNum(r.peg,2)],["ROE",fmtPct(r.roe)],["Umsatzwachstum",fmtPct(r.rev_growth)],
  ["Verschuldung (D/E)",r.debt_to_equity==null?"–":fmtNum(r.debt_to_equity,0)+" %"],["Analysten-Rating",fmtNum(r.analyst_rating,2)],
  ["Abstand 52W-Hoch",fmtPct(r.off_52w_high)],["Über 200-Tage-Linie",r.above_200d==null?"–":r.above_200d?"ja":"nein"],
  ["Kurs",r.price==null?"–":r.price.toFixed(2)+" $"],["Staatsaufträge seit Kauf",esc(r.contracts||"keine ≥ 10 Mio $")]].map(([k,v])=>`<span class="chip">${k}: ${v}</span>`).join("");
 return `<tr class="detail"><td colspan="${RC.length}"><div class="dgrid"><div>
  <b>Warum?</b><ul class="why">${(r.reasons||[]).map(x=>`<li>${esc(x)}</li>`).join("")||"<li>–</li>"}</ul>
  <b>Fundamental</b>${pills}<div style="margin-top:8px">${facts}</div>
  <div style="margin-top:8px"><a href="https://finance.yahoo.com/quote/${encodeURIComponent(r.ticker)}" target="_blank" rel="noopener">Yahoo Finance</a></div></div>
  <div><b>Transaktionen</b><table class="mini"><thead><tr><th>Trade</th><th>Gemeldet</th><th>Politiker</th><th>Amt</th><th>Eigentümer</th><th>Art</th><th class="num">Betrag</th><th>Beschreibung</th><th></th></tr></thead>
  <tbody>${tradeRows(tr)}</tbody></table></div></div></td></tr>`;
}

function renderRank(){
 const q=$("#q").value.toLowerCase(), sec=$("#sector").value, ms=+$("#minscore").value;
 const fc=$("#fcom").checked, fl=$("#fclu").checked, fo=$("#fcall").checked, fm=$("#fman").checked;
 let rows=D.ranking.filter(r=>(!q||(r.ticker+" "+r.name+" "+r.buyers).toLowerCase().includes(q))&&(!sec||r.sector===sec)&&r.score>=ms&&
  (!fc||r.has_committee)&&(!fl||r.n_buyers>=2)&&(!fo||r.call_options)&&(!$("#fcon").checked||r.contracts)&&(!fm||!r.managed_only));
 const k=rSort.k; rows.sort((a,b)=>{const x=a[k],y=b[k];if(x==null)return 1;if(y==null)return -1;return (x>y?1:x<y?-1:0)*rSort.dir;});
 $("#rt tbody").innerHTML=rows.map(r=>`<tr class="row" data-t="${esc(r.ticker)}">${RC.map(c=>`<td class="${c[3]?'num':''}">${c[2](r)}</td>`).join("")}</tr>`+(open.has(r.ticker)?detail(r):"")).join("")
  || `<tr><td colspan="${RC.length}">Keine Treffer</td></tr>`;
}
$("#rt tbody").onclick=e=>{if(e.target.closest("a"))return;const tr=e.target.closest("tr.row");if(!tr)return;const t=tr.dataset.t;open.has(t)?open.delete(t):open.add(t);renderRank();};
["#q","#sector","#minscore","#fcom","#fclu","#fcall","#fcon","#fman"].forEach(s=>$(s).oninput=renderRank);
renderRank();

// ------------------------------------------------------------- Politiker
const PC=[["Politiker","person",r=>esc(r.person)],["Bewertete Käufe","n",r=>r.n,1],["Trefferquote","hit_rate",r=>(r.hit_rate*100).toFixed(0)+" %",1],
 ["Ø Überrendite","avg_excess",r=>fmtPct(r.avg_excess),1],["Median Überrendite","median_excess",r=>fmtPct(r.median_excess),1]];
let pSort={k:"avg_excess",dir:-1};
$("#pt thead").innerHTML="<tr>"+PC.map(c=>`<th class="${c[3]?'num':''}" data-k="${c[1]}">${c[0]}</th>`).join("")+"</tr>";
$("#pt thead").onclick=e=>{const k=e.target.dataset.k;if(!k)return;pSort={k,dir:pSort.k===k?-pSort.dir:-1};renderPol();};
function renderPol(){const q=$("#pq").value.toLowerCase();
 const rows=D.leaderboard.filter(r=>!q||r.person.toLowerCase().includes(q)).sort((a,b)=>(a[pSort.k]>b[pSort.k]?1:-1)*pSort.dir);
 $("#pt tbody").innerHTML=rows.map(r=>`<tr>${PC.map(c=>`<td class="${c[3]?'num':''}">${c[2](r)}</td>`).join("")}</tr>`).join("");}
$("#pq").oninput=renderPol; renderPol();

// ------------------------------------------------------------- Feed
$("#ft thead").innerHTML="<tr><th>Trade</th><th>Gemeldet</th><th>Amt</th><th>Ticker</th><th>Asset</th><th>Politiker</th><th>Eigentümer</th><th>Art</th><th class='num'>Betrag</th><th>Beschreibung</th><th></th></tr>";
function renderFeed(){
 const q=$("#fq").value.toLowerCase(), s=$("#fsrc").value, t=$("#ftype").value;
 const rows=D.feed.filter(r=>(!q||((r.ticker||"")+" "+r.person).toLowerCase().includes(q))&&(!s||r.source===s)&&
   (!t||(t==="sell"?r.type!=="Kauf":r.type===t))).slice(0,800);
 $("#ft tbody").innerHTML=rows.map(r=>`<tr><td>${fmtDate(r.tx_date)}</td><td>${fmtDate(r.filed)}</td><td>${esc(r.source)}</td>
  <td class="tk">${esc(r.ticker)}</td><td><div class="nm" title="${esc(r.asset)}">${esc(r.asset)}</div></td><td>${esc(r.person)}</td><td>${esc(r.owner||"")}</td>
  <td class="${r.type==='Kauf'?'pos':'neg'}">${esc(r.type)}${r.asset_type==="OP"?" (Option)":""}</td>
  <td class="num">${fmtRange(r.amount_low,r.amount_high)}</td><td class="desc">${esc(r.description||"")}</td>
  <td><a href="${esc(r.url)}" target="_blank" rel="noopener">Meldung</a></td></tr>`).join("");
}
["#fq","#fsrc","#ftype"].forEach(s=>$(s).oninput=renderFeed);
renderFeed();
</script>
</body>
</html>
"""
