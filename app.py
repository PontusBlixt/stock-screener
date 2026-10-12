#!/usr/bin/env python3
"""
Webbsida för screenern
======================

Sidor:
  /               veckans screener-rapport (senaste.html)
  /innehav        dina innehav med säljsignaler – lägg till och ta bort här
  /aktie/<ticker> kursgraf och nyckeltal för en aktie (innehav eller kandidat)
  /arkiv          tidigare veckorapporter

Start:
  ./venv/bin/python app.py

Inställningar via miljövariabler:
  SCREENER_UTMAPP   mappen med rapporter, innehav.csv och kurser.csv (standard /srv/storage/screener)
  SCREENER_PORT     port (standard 8080)
  SCREENER_LOSEN    sätt ett lösenord för att kräva inloggning (användarnamn valfritt)
  SCREENER_DEMO     1 = påhittade kurser när du lägger till innehav (för test utan internet)

Använd bara på ditt hemnätverk (eller via Tailscale). Öppna inte porten i routern.
"""

from __future__ import annotations

import datetime as dt
import html
import math
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, Response, abort, flash, redirect, render_template_string, request, send_file, url_for

import innehav as ih
import screener as sc

UTMAPP = Path(os.environ.get("SCREENER_UTMAPP", sc.KONFIG["utmapp"])).expanduser()
INNEHAV_FIL = UTMAPP / "innehav.csv"
AFFAR_FIL = UTMAPP / "affarer.csv"
KURS_FIL = UTMAPP / "kurser.csv"
LOSEN = os.environ.get("SCREENER_LOSEN", "")
DEMO = os.environ.get("SCREENER_DEMO") == "1"

app = Flask(__name__)
app.secret_key = os.environ.get("SCREENER_HEMLIGHET", "screener-" + str(UTMAPP))


# ---------------------------------------------------------------------------
# Inloggning (valfri)
# ---------------------------------------------------------------------------
@app.before_request
def kontrollera_losen():
    if not LOSEN:
        return None
    auth = request.authorization
    if auth and auth.password == LOSEN:
        return None
    return Response("Inloggning krävs", 401, {"WWW-Authenticate": 'Basic realm="Screener"'})


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
_kurscache: dict = {"mtime": None, "df": pd.DataFrame()}


def kurser() -> pd.DataFrame:
    """Läser kurser.csv, cachat tills filen ändras."""
    if not KURS_FIL.exists():
        return pd.DataFrame()
    m = KURS_FIL.stat().st_mtime
    if _kurscache["mtime"] != m:
        _kurscache["df"] = ih.las_kurser(KURS_FIL)
        _kurscache["mtime"] = m
    return _kurscache["df"]


def hamta(tickers: list[str]) -> dict[str, pd.Series]:
    """Hämtar 2 års stängningskurser för några tickers."""
    data = sc.demodata(tickers, seed=len("".join(tickers))) if DEMO else sc.hamta_kurser(tickers, sc.KONFIG["historik_egna"])
    return {t: df["Close"] for t, df in data.items()}


def uppdatera_kurser(tickers: list[str]) -> list[str]:
    """Hämtar och sparar kurser. Returnerar de tickers som saknade data."""
    nya = hamta(tickers)
    if nya:
        ih.skriv_kurser(KURS_FIL, ih.slå_ihop_kurser(kurser(), nya))
    return [t for t in tickers if t not in nya]


def screener_tabell() -> pd.DataFrame | None:
    return ih.senaste_screener(UTMAPP)


def namn_for(ticker: str) -> str:
    s = screener_tabell()
    if s is not None:
        rad = s[s["ticker"] == ticker]
        if len(rad):
            return str(rad.iloc[0]["namn"])
    return ""


# ---------------------------------------------------------------------------
# Formatering (Jinja-filter)
# ---------------------------------------------------------------------------
def _ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


@app.template_filter("pct")
def f_pct(x, tecken=True):
    if not _ok(x):
        return "–"
    return f"{x * 100:+.1f} %" if tecken else f"{x * 100:.1f} %"


@app.template_filter("kr")
def f_kr(x):
    if not _ok(x):
        return "–"
    return f"{x:,.0f} kr".replace(",", " ")


@app.template_filter("tal")
def f_tal(x, dec=2):
    if not _ok(x):
        return "–"
    return f"{x:,.{dec}f}".replace(",", " ")


@app.template_filter("klass")
def f_klass(x):
    if not _ok(x):
        return ""
    return "pos" if x > 0 else "neg" if x < 0 else ""


@app.template_filter("datum")
def f_datum(d):
    return "" if d is None or pd.isna(d) else pd.Timestamp(d).strftime("%Y-%m-%d")


@app.template_filter("kort")
def f_kort(t):
    return str(t).removesuffix(".ST")


BESLUT_KLASS = {"SÄLJ": "salj", "SÄLJ VID MÅNADSGENOMGÅNG": "manad", "BEVAKA": "bevaka",
                "TRIMMA": "trimma", "BEHÅLL": "behall", "INFO": "info", "SAKNAR DATA": "salj"}


@app.template_filter("bklass")
def f_bklass(b):
    return BESLUT_KLASS.get(b, "info")


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
CSS = """
:root { --bg:#f6f7f9; --kort:#fff; --text:#1c1f24; --muted:#6b7280; --linje:#e5e7eb;
        --pos:#0f7b3f; --neg:#c0352b; --accent:#2b5fd9; --gul:#b7791f; --orange:#c05621;
        --b-salj:#fde2e0; --b-manad:#fdecd8; --b-bevaka:#fdf6d8; --b-trimma:#e5ecfd; --b-behall:#e2f4e8; --b-info:#eceef1; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14161a; --kort:#1d2026; --text:#e6e8eb; --muted:#9aa1ab; --linje:#2c3038;
          --pos:#4cc47f; --neg:#f07167; --accent:#7aa2ff; --gul:#e2b14a; --orange:#f0965a;
          --b-salj:#4a2422; --b-manad:#46301c; --b-bevaka:#3f3818; --b-trimma:#232f4d; --b-behall:#1d3a27; --b-info:#2a2e35; } }
* { box-sizing:border-box }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
a { color:var(--accent); text-decoration:none } a:hover { text-decoration:underline }
nav.topp { background:var(--kort); border-bottom:1px solid var(--linje); }
nav.topp div { max-width:1200px; margin:0 auto; padding:10px 16px; display:flex; gap:20px; align-items:center; flex-wrap:wrap }
nav.topp b { margin-right:8px } nav.topp a.aktiv { font-weight:600; color:var(--text) }
main { max-width:1200px; margin:0 auto; padding:24px 16px 48px }
h1 { margin:0 0 4px; font-size:24px } h2 { margin:0 0 12px; font-size:16px }
.muted { color:var(--muted) } .small { font-size:13px }
.card { background:var(--kort); border:1px solid var(--linje); border-radius:10px; padding:16px; }
.stats { display:flex; flex-wrap:wrap; gap:12px; margin:16px 0 20px }
.stat { background:var(--kort); border:1px solid var(--linje); border-radius:10px; padding:10px 14px; min-width:150px }
.stat b { display:block; font-size:20px }
.wrap { overflow-x:auto; background:var(--kort); border:1px solid var(--linje); border-radius:10px }
table { border-collapse:collapse; width:100%; }
th,td { padding:8px 10px; border-bottom:1px solid var(--linje); text-align:left; vertical-align:top }
th { font-size:12px; color:var(--muted); font-weight:600; white-space:nowrap }
.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap }
.pos { color:var(--pos) } .neg { color:var(--neg) }
.badge { display:inline-block; font-size:12px; font-weight:600; padding:2px 8px; border-radius:999px; white-space:nowrap }
.salj { background:var(--b-salj) } .manad { background:var(--b-manad) } .bevaka { background:var(--b-bevaka) }
.trimma { background:var(--b-trimma) } .behall { background:var(--b-behall) } .info { background:var(--b-info) }
form.rad { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; align-items:end }
label { display:flex; flex-direction:column; gap:4px; font-size:13px; color:var(--muted) }
input, select { font:inherit; padding:7px 9px; border:1px solid var(--linje); border-radius:6px; background:var(--bg); color:var(--text) }
button { font:inherit; padding:8px 14px; border:0; border-radius:6px; background:var(--accent); color:#fff; cursor:pointer }
button.lank { background:none; color:var(--neg); padding:0; font-size:13px }
.flash { padding:10px 14px; border-radius:8px; margin-bottom:16px; background:var(--b-bevaka) }
.flash.fel { background:var(--b-salj) } .flash.ok { background:var(--b-behall) }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:12px; margin-top:16px }
ul.regler { list-style:none; padding:0; margin:0 } ul.regler li { display:flex; gap:10px; padding:6px 0; border-bottom:1px solid var(--linje) }
ul.regler li:last-child { border:0 }
.prick { width:10px; height:10px; border-radius:50%; margin-top:6px; flex:none }
.p-ok { background:var(--pos) } .p-bevaka { background:var(--gul) } .p-brutet { background:var(--neg) } .p-na { background:var(--linje) }
svg.graf { width:100%; height:auto; display:block; min-width:560px }
.grafwrap { overflow-x:auto }
dl.nyckel { display:grid; grid-template-columns:auto auto; gap:6px 16px; margin:0 } dl.nyckel dt { color:var(--muted) } dl.nyckel dd { margin:0; text-align:right; font-variant-numeric:tabular-nums }
footer { margin-top:24px; font-size:12px; color:var(--muted) }
"""

BAS = """<!doctype html><html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{{ titel }} · Screener</title>
<style>{{ css|safe }}</style></head><body>
<nav class="topp"><div><b>📈 Screener</b>
<a href="{{ url_for('rapport') }}" class="{{ 'aktiv' if sida=='rapport' }}">Veckans rapport</a>
<a href="{{ url_for('innehav_sida') }}" class="{{ 'aktiv' if sida=='innehav' }}">Mina innehav</a>
<a href="{{ url_for('utveckling_sida') }}" class="{{ 'aktiv' if sida=='utveckling' }}">Utveckling</a>
<a href="{{ url_for('arkiv') }}" class="{{ 'aktiv' if sida=='arkiv' }}">Arkiv</a></div></nav>
<main>
{% with meddelanden = get_flashed_messages(with_categories=true) %}{% for kat, m in meddelanden %}
<div class="flash {{ kat }}">{{ m }}</div>{% endfor %}{% endwith %}
{{ innehall|safe }}
<footer>Underlag för egna beslut, inte köp- eller säljrekommendationer. Data: Yahoo Finance.</footer>
</main></body></html>"""


def sida(titel: str, flik: str, mall: str, **kw) -> str:
    innehall = render_template_string(mall, **kw)
    return render_template_string(BAS, titel=titel, sida=flik, css=CSS, innehall=innehall)


# ---------------------------------------------------------------------------
# Veckans rapport och arkiv
# ---------------------------------------------------------------------------
NAV_INJEKTION = """<nav style="background:var(--kort);border-bottom:1px solid var(--linje)">
<div style="max-width:1200px;margin:0 auto;padding:10px 16px;display:flex;gap:20px;flex-wrap:wrap">
<b>📈 Screener</b><a href="/" style="color:var(--text);font-weight:600;text-decoration:none">Veckans rapport</a>
<a href="/innehav" style="color:var(--accent);text-decoration:none">Mina innehav</a><a href="/utveckling" style="color:var(--accent);text-decoration:none">Utveckling</a><a href="/arkiv" style="color:var(--accent);text-decoration:none">Arkiv</a></div></nav>"""

LANK_SKRIPT = """<script>
document.querySelectorAll('[data-t]').forEach(function(el){
  var a=document.createElement('a'); a.href='/aktie/'+encodeURIComponent(el.dataset.t);
  a.style.color='inherit'; a.style.textDecoration='none'; el.parentNode.insertBefore(a,el); a.appendChild(el); });
</script>"""


def visa_rapport(fil: Path) -> str:
    text = fil.read_text(encoding="utf-8")
    text = text.replace("<body>", "<body>" + NAV_INJEKTION, 1)
    return text.replace("</body>", LANK_SKRIPT + "</body>", 1)


@app.route("/")
def rapport():
    fil = UTMAPP / "senaste.html"
    if not fil.exists():
        return sida("Rapport", "rapport", "<div class='card'>Ingen rapport ännu. Kör <code>screener.py</code> en gång.</div>")
    return visa_rapport(fil)


@app.route("/arkiv")
def arkiv():
    filer = sorted(UTMAPP.glob("rapport_*.html"), reverse=True)
    mall = """<h1>Arkiv</h1><p class="muted">Alla veckorapporter, nyast först.</p>
<div class="card"><ul>{% for f in filer %}<li><a href="{{ url_for('arkivfil', namn=f.name) }}">{{ f.stem[8:] }}</a>
 <span class="muted small">· <a href="{{ url_for('arkivfil', namn=f.stem + '.md') }}">.md</a></span></li>
{% else %}<li class="muted">Inga rapporter ännu.</li>{% endfor %}</ul></div>"""
    return sida("Arkiv", "arkiv", mall, filer=filer)


@app.route("/arkiv/<namn>")
def arkivfil(namn: str):
    if not re.fullmatch(r"rapport_\d{4}-\d{2}-\d{2}\.(html|md)", namn):
        abort(404)
    fil = UTMAPP / namn
    if not fil.exists():
        abort(404)
    if namn.endswith(".md"):
        return send_file(fil, mimetype="text/plain; charset=utf-8")
    return visa_rapport(fil)


# ---------------------------------------------------------------------------
# Innehav
# ---------------------------------------------------------------------------
INNEHAV_MALL = """
<h1>Mina innehav</h1>
<p class="muted">Säljreglerna gäller innehav av typen <b>momentum</b>. Kärna och övrigt visas som information.
Kurser från {{ kursdatum or '–' }}.</p>

<div class="stats">
<div class="stat" style="border-color:var(--accent)"><span class="muted small">Totalt innehav</span><b>{{ total.varde|kr }}</b>
<span class="small {{ total.resultat|klass }}">{{ total.resultat_kr|kr }} ({{ total.resultat|pct }})</span>
<div class="small"><a href="{{ url_for('utveckling_sida') }}">Utveckling och historik →</a></div></div>
{% for typ, s in summa.items() if s.antal %}
<div class="stat"><span class="muted small">{{ typ|capitalize }} ({{ s.antal }})</span><b>{{ s.varde|kr }}</b>
<span class="small {{ s.resultat|klass }}">{{ s.resultat|pct }}</span></div>
{% endfor %}
{% for b, n in beslut_antal.items() %}<div class="stat"><span class="muted small">{{ b|capitalize }}</span><b><span class="badge {{ b|bklass }}">{{ n }}</span></b></div>{% endfor %}
</div>

{% if analys %}
<div class="wrap"><table style="min-width:1100px">
<thead><tr><th>Aktie</th><th>Typ</th><th>Beslut</th><th class="num">Antal</th><th class="num">Köpkurs</th>
<th class="num">Kurs</th><th class="num">Resultat</th><th class="num">Värde</th><th class="num">Andel</th>
<th class="num">Från högsta</th><th class="num">Till stopp</th><th class="num">Över MA200</th><th class="num">Plats</th>
<th style="min-width:260px">Orsak</th><th></th></tr></thead><tbody>
{% for a in analys %}
<tr>
<td style="min-width:110px"><a style="white-space:nowrap" href="{{ url_for('aktie', ticker=a.ticker) }}"><b>{{ a.ticker|kort }}</b></a>
 <div class="muted small">{{ a.kopdatum|datum }}{% if a.notering %} · {{ a.notering }}{% endif %}</div></td>
<td class="small">{{ a.typ }}</td>
<td><span class="badge {{ a.beslut|bklass }}">{{ a.beslut }}</span></td>
{% if a.saknar_data %}<td colspan="10" class="muted">Ingen kursdata – klicka "Uppdatera kurser" eller kontrollera tickern.</td>
{% else %}
<td class="num">{{ a.antal|tal(0) }}</td><td class="num">{{ a.kopkurs|tal }}</td><td class="num">{{ a.kurs|tal }} <span class="muted small">{{ a.valuta }}</span></td>
<td class="num {{ a.resultat|klass }}">{{ a.resultat|pct }}</td><td class="num">{{ a.varde_sek|kr }}</td>
<td class="num">{{ a.andel|pct(false) if a.typ == 'momentum' else '' }}</td>
<td class="num {{ a.fran_hogsta|klass }}">{{ a.fran_hogsta|pct }}</td><td class="num">{{ a.till_stopp|pct }}</td>
<td class="num {{ a.over_ma200|klass }}">{{ a.over_ma200|pct }}</td>
<td class="num">{% if a.plats %}{{ a.plats }}/{{ a.n_godkanda }}{% elif a.i_screener %}<span class="neg">ej godk.</span>{% else %}–{% endif %}</td>
<td class="small">{{ (a.signaler + a.manad + a.bevaka + (['för stor andel – trimma'] if a.trimma else []))|join('; ') }}</td>
{% endif %}
<td style="white-space:nowrap"><a href="{{ url_for('salj_sida', idx=loop.index0) }}"><b>Sälj</b></a>
<form method="post" action="{{ url_for('ta_bort', idx=loop.index0) }}" style="display:inline;margin-left:8px"
 onsubmit="return confirm('Ta bort {{ a.ticker }} helt, utan att spara som affär? Använd Sälj om du sålt aktien.')">
<button class="lank" type="submit" title="För felinmatningar">Ta bort</button></form></td>
</tr>{% endfor %}
</tbody></table></div>
{% else %}<div class="card muted">Inga innehav ännu – lägg till ditt första nedan.</div>{% endif %}

<div class="grid">
<div class="card" style="grid-column:1/-1">
<h2>Lägg till innehav</h2>
<form class="rad" method="post" action="{{ url_for('lagg_till') }}">
<label>Ticker<input name="ticker" required placeholder="VOLV-B.ST eller ADCT"></label>
<label>Typ<select name="typ">{% for t in typer %}<option>{{ t }}</option>{% endfor %}</select></label>
<label>Köpdatum<input type="date" name="kopdatum" value="{{ idag }}" required></label>
<label>Köpkurs<input name="kopkurs" required inputmode="decimal" placeholder="123,45"></label>
<label>Antal<input name="antal" required inputmode="decimal"></label>
<label>Valuta<input name="valuta" placeholder="gissas"></label>
<label>Notering<input name="notering"></label>
<button type="submit">Lägg till</button>
</form>
<p class="muted small">Svenska aktier skrivs med .ST, t.ex. <code>SWED-A.ST</code>. Amerikanska aktier skrivs utan suffix, t.ex. <code>ADCT</code>.
Köpkursen anges i aktiens egen valuta. Flera köp av samma aktie läggs in som separata rader.</p>
</div>
<div class="card">
<h2>Uppdatera kurser</h2>
<p class="small muted">Hämtar dagens kurser för dina innehav. Rankningen uppdateras bara vid veckokörningen.</p>
<form method="post" action="{{ url_for('uppdatera') }}"><button type="submit">Uppdatera kurser</button></form>
</div>
<div class="card">
<h2>Säljreglerna</h2>
<ul class="small" style="margin:0;padding-left:18px">
<li><b>Sälj</b> om kursen hamnar under MA200, om MA50 korsar under MA200, eller om kursen fallit {{ (regler.STOPP_PROCENT * 100)|round|int }} % från högsta sedan köp.</li>
<li><b>Sälj vid månadsgenomgång</b> om aktien hamnat utanför topp {{ regler.SALJ_RANK }} i screenern.</li>
<li><b>Trimma</b> om en position är större än 1,5 gånger en jämn fördelning (minst {{ (regler.MAX_ANDEL * 100)|round|int }} %).</li>
</ul></div>
</div>
"""


def analysera_allt():
    df = ih.las_innehav(INNEHAV_FIL)
    return df, ih.analysera(df, kurser(), screener_tabell())


@app.route("/innehav")
def innehav_sida():
    df, analys = analysera_allt()
    beslut_antal = {}
    for a in analys:
        if a["typ"] == "momentum":
            beslut_antal[a["beslut"]] = beslut_antal.get(a["beslut"], 0) + 1
    k = kurser()
    kursdatum = k.index.max().date() if len(k) else None
    varde = sum(a["varde_sek"] for a in analys if _ok(a.get("varde_sek")))
    ansk = sum(a["anskaffning_sek"] for a in analys if _ok(a.get("varde_sek")) and _ok(a.get("anskaffning_sek")))
    total = {"varde": varde, "resultat_kr": varde - ansk, "resultat": varde / ansk - 1 if ansk else None}
    return sida("Mina innehav", "innehav", INNEHAV_MALL, analys=analys, summa=ih.sammanfattning(analys),
                beslut_antal=beslut_antal, total=total, typer=ih.TYPER, idag=dt.date.today().isoformat(),
                regler=ih.REGLER, kursdatum=kursdatum)


def _tal(s: str) -> float:
    return float(s.replace(" ", "").replace(",", "."))


@app.route("/innehav/lagg-till", methods=["POST"])
def lagg_till():
    f = request.form
    ticker = f.get("ticker", "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-=^]{1,20}", ticker):
        flash("Ogiltig ticker.", "fel")
        return redirect(url_for("innehav_sida"))
    try:
        kopkurs, antal = _tal(f["kopkurs"]), _tal(f["antal"])
        kopdatum = pd.to_datetime(f["kopdatum"])
    except (ValueError, KeyError):
        flash("Köpkurs, antal och datum måste vara ifyllda med siffror.", "fel")
        return redirect(url_for("innehav_sida"))
    typ = f.get("typ", "momentum") if f.get("typ") in ih.TYPER else "momentum"
    valuta = f.get("valuta", "").strip().upper() or ih.gissa_valuta(ticker)

    # Hämta kurser för aktien (och valutan) direkt, så att sidan kan visa den
    behov = [ticker] + ([ih.fx_ticker(valuta)] if ih.fx_ticker(valuta) else [])
    if ih.JAMFORELSEINDEX not in kurser().columns:
        behov.append(ih.JAMFORELSEINDEX)
    try:
        saknas = uppdatera_kurser(behov)
    except Exception as e:  # nätverksfel m.m.
        flash(f"Kunde inte hämta kurser: {e}", "fel")
        return redirect(url_for("innehav_sida"))
    if ticker in saknas:
        tips = " Svenska aktier slutar på .ST, t.ex. VOLV-B.ST." if not ticker.endswith(".ST") and valuta == "SEK" else ""
        flash(f"Hittade ingen kursdata för {ticker} hos Yahoo. Kontrollera tickern.{tips}", "fel")
        return redirect(url_for("innehav_sida"))

    df = ih.las_innehav(INNEHAV_FIL)
    ny = pd.DataFrame([{"ticker": ticker, "typ": typ, "kopdatum": kopdatum, "kopkurs": kopkurs,
                        "antal": antal, "valuta": valuta, "notering": f.get("notering", "").strip()}])
    df = pd.concat([df, ny], ignore_index=True) if len(df) else ny
    ih.skriv_innehav(INNEHAV_FIL, df)
    flash(f"{ticker} tillagd.", "ok")
    return redirect(url_for("innehav_sida"))


@app.route("/innehav/ta-bort/<int:idx>", methods=["POST"])
def ta_bort(idx: int):
    df = ih.las_innehav(INNEHAV_FIL)
    if 0 <= idx < len(df):
        t = df.iloc[idx]["ticker"]
        ih.skriv_innehav(INNEHAV_FIL, df.drop(index=idx).reset_index(drop=True))
        flash(f"{t} borttagen.", "ok")
    return redirect(url_for("innehav_sida"))


@app.route("/innehav/uppdatera", methods=["POST"])
def uppdatera():
    df = ih.las_innehav(INNEHAV_FIL)
    behov = list(dict.fromkeys(list(df["ticker"]) + [x for x in (ih.fx_ticker(v) for v in df["valuta"]) if x]
                               + [ih.JAMFORELSEINDEX]))
    if len(behov) <= 1:
        flash("Inga innehav att uppdatera.", "fel")
        return redirect(url_for("innehav_sida"))
    try:
        saknas = uppdatera_kurser(behov)
    except Exception as e:
        flash(f"Kunde inte hämta kurser: {e}", "fel")
        return redirect(url_for("innehav_sida"))
    flash("Kurserna uppdaterade." + (f" Saknade data: {', '.join(saknas)}" if saknas else ""),
          "fel" if saknas else "ok")
    return redirect(url_for("innehav_sida"))


# ---------------------------------------------------------------------------
# Sälj
# ---------------------------------------------------------------------------
SALJ_MALL = """
<p class="small"><a href="{{ url_for('innehav_sida') }}">← Mina innehav</a></p>
<h1>Sälj {{ h.ticker|kort }}</h1>
<p class="muted">Köpt {{ h.kopdatum|datum }} · {{ h.antal|tal(0) }} st à {{ h.kopkurs|tal }} {{ h.valuta }} · senaste kurs {{ kurs|tal }}</p>
<div class="card" style="max-width:640px">
<form class="rad" method="post">
<label>Säljdatum<input type="date" name="saljdatum" value="{{ idag }}" required></label>
<label>Säljkurs ({{ h.valuta }})<input name="saljkurs" value="{{ kurs_txt }}" required inputmode="decimal"></label>
<label>Antal<input name="antal" value="{{ antal_txt }}" required inputmode="decimal"></label>
<label>Orsak<select name="orsak">{% for o in orsaker %}<option {{ 'selected' if o == forslag }}>{{ o }}</option>{% endfor %}</select></label>
<button type="submit">Registrera försäljning</button>
</form>
<p class="small muted">Säljer du färre aktier än du äger ligger resten kvar som innehav. Ange kursen du faktiskt fick (Avanza visar den under avslutade affärer).</p>
{% if signaler %}<p class="small">Aktuella signaler: {{ signaler }}</p>{% endif %}
</div>
"""


def _forslag_orsak(a: dict) -> str:
    if any("stopp" in s for s in a.get("signaler", [])):
        return "släpande stopp"
    if a.get("signaler"):
        return "trendregel (MA200/MA50)"
    if a.get("manad"):
        return "utanför topp 40"
    if a.get("trimma"):
        return "trimning"
    return "eget beslut"


@app.route("/innehav/salj/<int:idx>", methods=["GET", "POST"])
def salj_sida(idx: int):
    df, analys = analysera_allt()
    if not 0 <= idx < len(df):
        abort(404)
    h = df.iloc[idx]
    a = analys[idx]
    if request.method == "POST":
        f = request.form
        try:
            saljkurs, antal = _tal(f["saljkurs"]), _tal(f["antal"])
            saljdatum = pd.to_datetime(f["saljdatum"])
        except (ValueError, KeyError):
            flash("Säljkurs, antal och datum måste vara ifyllda med siffror.", "fel")
            return redirect(url_for("salj_sida", idx=idx))
        if antal <= 0 or saljkurs <= 0:
            flash("Antal och kurs måste vara större än noll.", "fel")
            return redirect(url_for("salj_sida", idx=idx))
        if pd.notna(h["kopdatum"]) and saljdatum < h["kopdatum"]:
            flash("Säljdatum kan inte vara före köpdatum.", "fel")
            return redirect(url_for("salj_sida", idx=idx))
        orsak = f.get("orsak", "annat") if f.get("orsak") in ih.SALJORSAKER else "annat"
        nya, affar = ih.salj(df, idx, saljdatum, saljkurs, antal, orsak, kurser())
        aff = ih.las_affarer(AFFAR_FIL)
        aff = pd.concat([aff, pd.DataFrame([affar])], ignore_index=True) if len(aff) else pd.DataFrame([affar])
        ih.skriv_affarer(AFFAR_FIL, aff)          # spara affären först ...
        ih.skriv_innehav(INNEHAV_FIL, nya)        # ... sedan det som är kvar
        r = affar["saljkurs"] / affar["kopkurs"] - 1
        flash(f"Sålde {affar['antal']:g} {h['ticker']} – {r * 100:+.1f} % ({affar['resultat_sek']:,.0f} kr)."
              .replace(",", " "), "ok")
        return redirect(url_for("innehav_sida"))

    kurs = a.get("kurs", h["kopkurs"])
    return sida(f"Sälj {h['ticker']}", "innehav", SALJ_MALL, h=h, kurs=kurs,
                kurs_txt=f"{kurs:.2f}".replace(".", ","), antal_txt=f"{h['antal']:g}",
                idag=dt.date.today().isoformat(), orsaker=ih.SALJORSAKER, forslag=_forslag_orsak(a),
                signaler="; ".join(a.get("signaler", []) + a.get("manad", [])))


# ---------------------------------------------------------------------------
# Utveckling och historik
# ---------------------------------------------------------------------------
def graf_jamfor(port: pd.Series, index: pd.Series | None, indexnamn: str) -> str:
    """Två linjer i procent: portföljen och index, med nollinje."""
    B, H, VM, HM, TM, BM = 860, 300, 56, 16, 14, 28
    serier = [port] + ([index] if index is not None else [])
    lo = min(0.0, *(float(s.min()) for s in serier))
    hi = max(0.0, *(float(s.max()) for s in serier))
    pad = (hi - lo) * 0.08 or 0.01
    lo, hi = lo - pad, hi + pad
    t0, t1 = port.index[0].value, port.index[-1].value
    if t1 == t0:
        return "<p class='muted'>För kort period för en graf ännu – kom tillbaka om några dagar.</p>"

    def x(t):
        return VM + (pd.Timestamp(t).value - t0) / (t1 - t0) * (B - VM - HM)

    def y(v):
        return TM + (hi - v) / (hi - lo) * (H - TM - BM)

    def linje(s, farg, bredd):
        p = [(x(i), y(v)) for i, v in s.dropna().items()]
        if len(p) < 2:
            return ""
        return (f'<path d="M{" L".join(f"{a:.1f},{b:.1f}" for a, b in p)}" fill="none" stroke="{farg}" '
                f'stroke-width="{bredd}" stroke-linejoin="round"/>')

    delar = []
    spann = hi - lo
    steg = 10 ** math.floor(math.log10(spann / 4))
    for m in (1, 2, 5, 10):
        if spann / (steg * m) <= 6:
            steg *= m
            break
    v = math.ceil(lo / steg) * steg
    while v <= hi:
        delar.append(f'<line x1="{VM}" x2="{B - HM}" y1="{y(v):.1f}" y2="{y(v):.1f}" '
                     f'stroke="var(--{"muted" if abs(v) < 1e-12 else "linje"})"/>'
                     f'<text x="{VM - 8}" y="{y(v) + 4:.1f}" text-anchor="end" font-size="11" fill="var(--muted)">'
                     f'{v * 100:+.0f} %</text>')
        v += steg
    dagar = (port.index[-1] - port.index[0]).days
    frekv = "MS" if dagar > 75 else "W-MON"
    fmt = "%b %y" if dagar > 75 else "%d %b"
    etiketter = pd.date_range(port.index[0], port.index[-1], freq=frekv)
    if len(etiketter) > 12:
        etiketter = etiketter[::math.ceil(len(etiketter) / 12)]
    for t in etiketter:
        delar.append(f'<text x="{x(t):.1f}" y="{H - 8}" text-anchor="middle" font-size="11" fill="var(--muted)">'
                     f'{t.strftime(fmt)}</text>')
    if index is not None:
        delar.append(linje(index, "var(--muted)", 1.5))
    delar.append(linje(port, "var(--accent)", 2.2))
    for s, farg in ([(index, "var(--muted)")] if index is not None else []) + [(port, "var(--accent)")]:
        sista = s.dropna()
        if len(sista):
            delar.append(f'<circle cx="{x(sista.index[-1]):.1f}" cy="{y(sista.iloc[-1]):.1f}" r="3.5" fill="{farg}"/>')
    return (f'<div class="grafwrap"><svg class="graf" viewBox="0 0 {B} {H}" role="img" aria-label="Utveckling mot index">{"".join(delar)}</svg></div>'
            f'<p class="small muted" style="margin:6px 0 0"><span style="color:var(--accent)">━</span> din portfölj '
            + (f'<span style="color:var(--muted)">━</span> {html.escape(indexnamn)}' if index is not None else "")
            + '</p>')


PERIODER = {"allt": "Sedan start", "ar": "I år", "12m": "12 mån", "3m": "3 mån", "1m": "1 mån"}

UTVECKLING_MALL = """
<h1>Utveckling</h1>
<p class="muted">Tidsviktad avkastning: köp och försäljningar flyttar inte kurvan, bara hur aktierna rört sig.
Samma metod som fonder jämförs med.</p>

<div class="card" style="margin:16px 0">
<div style="display:flex;flex-wrap:wrap;gap:16px;justify-content:space-between;align-items:center;margin-bottom:12px">
<div class="small">Visa:
{% for d, namn in delar.items() %}<a href="{{ url_for('utveckling_sida', del_=d, period=period) }}"
 style="margin-left:8px;{{ 'font-weight:700;color:var(--text)' if d == del_ }}">{{ namn }}</a>{% endfor %}</div>
<div class="small">{% for p, namn in perioder.items() %}<a href="{{ url_for('utveckling_sida', del_=del_, period=p) }}"
 style="margin-left:10px;{{ 'font-weight:700;color:var(--text)' if p == period }}">{{ namn }}</a>{% endfor %}</div>
</div>
{% if graf %}
<div class="stats" style="margin-top:0">
<div class="stat"><span class="muted small">Din portfölj</span><b class="{{ port_avk|klass }}">{{ port_avk|pct }}</b></div>
<div class="stat"><span class="muted small">{{ indexnamn }}</span><b class="{{ index_avk|klass }}">{{ index_avk|pct }}</b></div>
<div class="stat"><span class="muted small">Mot index</span><b class="{{ skillnad|klass }}">{{ skillnad|pct }}</b>
<span class="small muted">procentenheter</span></div>
<div class="stat"><span class="muted small">Värde idag</span><b>{{ varde|kr }}</b></div>
</div>
{{ graf|safe }}
{% if not index_finns %}<p class="small neg">Indexdata saknas ({{ indexticker }}). Klicka "Uppdatera kurser" under Mina innehav,
eller ändra JAMFORELSEINDEX i innehav.py till ^OMX.</p>{% endif %}
{% else %}<p class="muted">Ingen utveckling att visa ännu för "{{ delar[del_] }}". Lägg till innehav med köpdatum och köpkurs.</p>{% endif %}
</div>

<h2>Avslutade affärer</h2>
{% if stat.antal %}
<div class="stats" style="margin-top:0">
<div class="stat"><span class="muted small">Affärer</span><b>{{ stat.antal }}</b></div>
<div class="stat"><span class="muted small">Träffsäkerhet</span><b>{{ stat.traffsakerhet|pct(false) }}</b>
<span class="small muted">{{ stat.vinnare }} med vinst</span></div>
<div class="stat"><span class="muted small">Snittvinst</span><b class="pos">{{ stat.snittvinst|pct }}</b></div>
<div class="stat"><span class="muted small">Snittförlust</span><b class="neg">{{ stat.snittforlust|pct }}</b></div>
<div class="stat"><span class="muted small">Realiserat</span><b class="{{ stat.resultat_sek|klass }}">{{ stat.resultat_sek|kr }}</b></div>
<div class="stat"><span class="muted small">Snitt ägd</span><b>{{ stat.snitt_dagar|tal(0) }} dagar</b></div>
</div>
<div class="wrap"><table style="min-width:960px">
<thead><tr><th>Aktie</th><th>Typ</th><th>Köpt</th><th>Sålt</th><th class="num">Dagar</th><th class="num">Antal</th>
<th class="num">Köpkurs</th><th class="num">Säljkurs</th><th class="num">Resultat</th><th class="num">Resultat kr</th>
<th>Orsak</th><th></th></tr></thead><tbody>
{% for a in affarer %}<tr>
<td><a href="{{ url_for('aktie', ticker=a.ticker) }}"><b>{{ a.ticker|kort }}</b></a></td><td class="small">{{ a.typ }}</td>
<td class="small">{{ a.kopdatum|datum }}</td><td class="small">{{ a.saljdatum|datum }}</td><td class="num">{{ a.dagar }}</td>
<td class="num">{{ a.antal|tal(0) }}</td><td class="num">{{ a.kopkurs|tal }}</td><td class="num">{{ a.saljkurs|tal }} <span class="muted small">{{ a.valuta }}</span></td>
<td class="num {{ a.r|klass }}">{{ a.r|pct }}</td><td class="num {{ a.resultat_sek|klass }}">{{ a.resultat_sek|kr }}</td>
<td class="small">{{ a.orsak }}</td>
<td><form method="post" action="{{ url_for('ta_bort_affar', idx=a.idx) }}" onsubmit="return confirm('Ta bort affären? Använd bara för felinmatningar.')">
<button class="lank" type="submit">Ta bort</button></form></td>
</tr>{% endfor %}</tbody></table></div>
{% else %}<div class="card muted">Inga avslutade affärer ännu för "{{ delar[del_] }}". Använd <b>Sälj</b> under Mina innehav när du säljer.</div>{% endif %}
"""


def _periodstart(period: str, sista: pd.Timestamp) -> pd.Timestamp | None:
    return {"ar": pd.Timestamp(sista.year, 1, 1), "12m": sista - pd.DateOffset(months=12),
            "3m": sista - pd.DateOffset(months=3), "1m": sista - pd.DateOffset(months=1)}.get(period)


@app.route("/utveckling")
def utveckling_sida():
    inn = ih.las_innehav(INNEHAV_FIL)
    aff = ih.las_affarer(AFFAR_FIL)
    k = kurser()
    har_mom = (inn["typ"] == "momentum").any() or (aff["typ"] == "momentum").any()
    delar = {"momentum": "Momentum", "alla": "Hela portföljen", "kärna": "Kärna"}
    del_ = request.args.get("del_") or ("momentum" if har_mom else "alla")
    del_ = del_ if del_ in delar else "alla"
    period = request.args.get("period", "allt")
    period = period if period in PERIODER else "allt"
    typer = None if del_ == "alla" else [del_]

    u = ih.utveckling(inn, aff, k, typer)
    graf = None
    port_avk = index_avk = skillnad = varde = None
    index_finns = ih.JAMFORELSEINDEX in k.columns and k[ih.JAMFORELSEINDEX].notna().any()
    if len(u) >= 1:
        ps = _periodstart(period, u.index[-1])
        if ps is not None and ps > u.index[0]:
            fore = u[u.index < ps]
            bas = 1 + (fore["avkastning"].iloc[-1] if len(fore) else 0.0)
            u = u[u.index >= ps].copy()
            u["avkastning"] = (1 + u["avkastning"]) / bas - 1
        if len(u):
            idx = ih.indexutveckling(k, u.index) if index_finns else None
            port_avk = float(u["avkastning"].iloc[-1])
            index_avk = float(idx.iloc[-1]) if idx is not None and pd.notna(idx.iloc[-1]) else None
            skillnad = port_avk - index_avk if index_avk is not None else None
            varde = float(u["varde_sek"].iloc[-1])
            graf = graf_jamfor(u["avkastning"], idx, ih.INDEXNAMN.get(ih.JAMFORELSEINDEX, ih.JAMFORELSEINDEX))

    vis = aff.copy()
    vis["idx"] = range(len(vis))
    if typer:
        vis = vis[vis["typ"].isin(typer)]
    stat = ih.affarsstatistik(vis)
    rader = []
    for a in vis.sort_values("saljdatum", ascending=False).to_dict("records"):
        a["r"] = a["saljkurs"] / a["kopkurs"] - 1 if a["kopkurs"] else None
        a["dagar"] = (a["saljdatum"] - a["kopdatum"]).days if pd.notna(a["kopdatum"]) and pd.notna(a["saljdatum"]) else ""
        rader.append(a)
    return sida("Utveckling", "utveckling", UTVECKLING_MALL, delar=delar, del_=del_, period=period,
                perioder=PERIODER, graf=graf, port_avk=port_avk, index_avk=index_avk, skillnad=skillnad,
                varde=varde, index_finns=index_finns, indexticker=ih.JAMFORELSEINDEX,
                indexnamn=ih.INDEXNAMN.get(ih.JAMFORELSEINDEX, ih.JAMFORELSEINDEX), stat=stat, affarer=rader)


@app.route("/utveckling/ta-bort/<int:idx>", methods=["POST"])
def ta_bort_affar(idx: int):
    aff = ih.las_affarer(AFFAR_FIL)
    if 0 <= idx < len(aff):
        t = aff.iloc[idx]["ticker"]
        ih.skriv_affarer(AFFAR_FIL, aff.drop(index=idx).reset_index(drop=True))
        flash(f"Affären i {t} borttagen.", "ok")
    return redirect(url_for("utveckling_sida"))


# ---------------------------------------------------------------------------
# Aktiesida med graf
# ---------------------------------------------------------------------------
def graf_svg(s: pd.Series, kopdatum=None, kopkurs=None, stopp=None) -> str:
    """Kursgraf som inbäddad SVG: kurs, MA50, MA200, köpkurs och stoppnivå."""
    ma50 = s.rolling(50).mean()
    ma200 = s.rolling(200).mean()
    start = s.index[-1] - pd.Timedelta(days=365)
    if kopdatum is not None and pd.notna(kopdatum):
        start = min(start, kopdatum - pd.Timedelta(days=60))
    s, ma50, ma200 = s[s.index >= start], ma50[ma50.index >= start], ma200[ma200.index >= start]
    if len(s) < 2:
        return "<p class='muted'>För lite data för en graf.</p>"

    B, H, VM, HM, TM, BM = 860, 320, 56, 16, 12, 28
    varden = [s.min(), s.max()] + [v for v in (ma50.min(), ma50.max(), ma200.min(), ma200.max(), kopkurs, stopp)
                                    if v is not None and pd.notna(v)]
    lo, hi = min(varden), max(varden)
    pad = (hi - lo) * 0.06 or 1
    lo, hi = lo - pad, hi + pad
    t0, t1 = s.index[0].value, s.index[-1].value

    def x(t):
        return VM + (pd.Timestamp(t).value - t0) / (t1 - t0) * (B - VM - HM)

    def y(v):
        return TM + (hi - v) / (hi - lo) * (H - TM - BM)

    def linje(serie, farg, bredd, streck=""):
        p = [(x(t), y(v)) for t, v in serie.dropna().items()]
        if len(p) < 2:
            return ""
        d = "M" + " L".join(f"{a:.1f},{b:.1f}" for a, b in p)
        return f'<path d="{d}" fill="none" stroke="{farg}" stroke-width="{bredd}" {streck} stroke-linejoin="round"/>'

    delar = []
    # Rutnät och y-axel
    steg = 10 ** math.floor(math.log10((hi - lo) / 4))
    for m in (1, 2, 5, 10):
        if (hi - lo) / (steg * m) <= 6:
            steg *= m
            break
    v = math.ceil(lo / steg) * steg
    while v <= hi:
        yy = y(v)
        delar.append(f'<line x1="{VM}" x2="{B - HM}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="var(--linje)"/>'
                     f'<text x="{VM - 8}" y="{yy + 4:.1f}" text-anchor="end" font-size="11" fill="var(--muted)">{v:g}</text>')
        v += steg
    # Månadsetiketter
    for t in pd.date_range(s.index[0], s.index[-1], freq="MS"):
        delar.append(f'<text x="{x(t):.1f}" y="{H - 8}" text-anchor="middle" font-size="11" fill="var(--muted)">'
                     f'{t.strftime("%b %y")}</text>')
    # Köpkurs, stopp, köpdatum
    if kopkurs is not None and pd.notna(kopkurs):
        delar.append(f'<line x1="{VM}" x2="{B - HM}" y1="{y(kopkurs):.1f}" y2="{y(kopkurs):.1f}" stroke="var(--pos)" stroke-dasharray="5 4"/>'
                     f'<text x="{B - HM - 4}" y="{y(kopkurs) - 5:.1f}" text-anchor="end" font-size="11" fill="var(--pos)">köpkurs</text>')
    if stopp is not None and pd.notna(stopp):
        delar.append(f'<line x1="{VM}" x2="{B - HM}" y1="{y(stopp):.1f}" y2="{y(stopp):.1f}" stroke="var(--neg)" stroke-dasharray="5 4"/>'
                     f'<text x="{B - HM - 4}" y="{y(stopp) + 14:.1f}" text-anchor="end" font-size="11" fill="var(--neg)">stopp</text>')
    if kopdatum is not None and pd.notna(kopdatum) and s.index[0] <= kopdatum <= s.index[-1]:
        delar.append(f'<line x1="{x(kopdatum):.1f}" x2="{x(kopdatum):.1f}" y1="{TM}" y2="{H - BM}" stroke="var(--muted)" stroke-dasharray="2 3"/>')
    delar.append(linje(ma200, "var(--orange)", 1.5))
    delar.append(linje(ma50, "var(--gul)", 1.5))
    delar.append(linje(s, "var(--accent)", 2))
    return (f'<div class="grafwrap"><svg class="graf" viewBox="0 0 {B} {H}" role="img" aria-label="Kursgraf">{"".join(delar)}</svg></div>'
            '<p class="small muted" style="margin:6px 0 0"><span style="color:var(--accent)">━</span> kurs '
            '<span style="color:var(--gul)">━</span> MA50 <span style="color:var(--orange)">━</span> MA200 '
            '<span style="color:var(--pos)">┅</span> köpkurs <span style="color:var(--neg)">┅</span> stopp</p>')


def regelstatus(a: dict) -> list[tuple[str, str, str]]:
    """[(prickklass, regel, förklaring)] för detaljsidan."""
    r = ih.REGLER
    ut = []
    if pd.notna(a["ma200"]):
        st = "brutet" if a["over_ma200"] < 0 else "bevaka" if a["over_ma200"] < r["BEVAKA_MA200_MARGINAL"] else "ok"
        ut.append((st, "Över MA200", f"{a['over_ma200'] * 100:+.1f} % (MA200 {a['ma200']:.2f})"))
        st = "brutet" if a["ma50"] < a["ma200"] else "ok"
        ut.append((st, "MA50 över MA200", f"MA50 {a['ma50']:.2f} · MA200 {a['ma200']:.2f}"))
    else:
        ut.append(("na", "Trend", "för kort historik för MA200"))
    st = "brutet" if a["till_stopp"] <= 0 else "bevaka" if a["till_stopp"] < r["BEVAKA_STOPP_MARGINAL"] else "ok"
    ut.append((st, f"Släpande stopp ({r['STOPP_PROCENT'] * 100:.0f} %)",
               f"stopp {a['stopp']:.2f}, högsta sedan köp {a['hogsta']:.2f}, {a['till_stopp'] * 100:+.1f} % kvar"))
    if a["i_screener"]:
        if not a["godkand"]:
            ut.append(("brutet", f"Inom topp {r['SALJ_RANK']}", "klarar inte screenerns filter"))
        else:
            st = "brutet" if a["plats"] > r["SALJ_RANK"] else "bevaka" if a["plats"] > r["SALJ_RANK"] - 10 else "ok"
            ut.append((st, f"Inom topp {r['SALJ_RANK']}", f"plats {a['plats']} av {a['n_godkanda']}"))
    else:
        ut.append(("na", f"Inom topp {r['SALJ_RANK']}", "ingår inte i den svenska screenern"))
    if a["typ"] == "momentum" and _ok(a.get("andel")):
        st = "bevaka" if a["trimma"] else "ok"
        ut.append((st, "Positionsstorlek", f"{a['andel'] * 100:.1f} % av momentumdelen (gräns {a['trimgrans'] * 100:.0f} %)"))
    return ut


AKTIE_MALL = """
<p class="small"><a href="{{ url_for('innehav_sida') }}">← Mina innehav</a> · <a href="{{ url_for('rapport') }}">Veckans rapport</a></p>
<h1>{{ ticker|kort }} <span class="muted" style="font-weight:400">{{ namn }}</span></h1>
<p class="muted">Senaste kurs {{ sista|tal }} ({{ datum }})</p>

<div class="card">{{ graf|safe }}</div>

{% for a in poster %}
<div class="grid">
<div class="card">
<h2>Innehav köpt {{ a.kopdatum|datum }} <span class="badge {{ a.beslut|bklass }}">{{ a.beslut }}</span></h2>
<dl class="nyckel">
<dt>Typ</dt><dd>{{ a.typ }}</dd>
<dt>Antal × köpkurs</dt><dd>{{ a.antal|tal(0) }} × {{ a.kopkurs|tal }} {{ a.valuta }}</dd>
<dt>Resultat</dt><dd class="{{ a.resultat|klass }}">{{ a.resultat|pct }}</dd>
<dt>Värde</dt><dd>{{ a.varde_sek|kr }}</dd>
<dt>Ägd</dt><dd>{{ a.dagar }} dagar</dd>
<dt>Högsta sedan köp</dt><dd>{{ a.hogsta|tal }}</dd>
<dt>Från högsta</dt><dd class="{{ a.fran_hogsta|klass }}">{{ a.fran_hogsta|pct }}</dd>
</dl>
{% if a.notering %}<p class="small muted">{{ a.notering }}</p>{% endif %}
</div>
<div class="card">
<h2>Säljreglerna{% if a.typ != 'momentum' %} <span class="muted small">(information – {{ a.typ }})</span>{% endif %}</h2>
<ul class="regler">{% for st, regel, txt in a.regler %}
<li><span class="prick p-{{ st }}"></span><div><b>{{ regel }}</b><div class="small muted">{{ txt }}</div></div></li>{% endfor %}
</ul></div>
</div>
{% endfor %}

<div class="grid">
<div class="card"><h2>Momentum och risk</h2>
<dl class="nyckel">
<dt>12 mån exkl. senaste</dt><dd class="{{ m.mom_12_1|klass }}">{{ m.mom_12_1|pct }}</dd>
<dt>6 mån</dt><dd class="{{ m.mom_6m|klass }}">{{ m.mom_6m|pct }}</dd>
<dt>1 mån</dt><dd class="{{ m.mom_1m|klass }}">{{ m.mom_1m|pct }}</dd>
<dt>Över MA200</dt><dd class="{{ m.over_ma200|klass }}">{{ m.over_ma200|pct }}</dd>
<dt>Volatilitet (3 mån, årlig)</dt><dd>{{ m.volatilitet|pct(false) }}</dd>
</dl></div>
<div class="card"><h2>I screenern</h2>
{% if s %}<dl class="nyckel">
<dt>Klarar filtren</dt><dd>{{ 'ja' if s.godkand else 'nej' }}</dd>
{% if s.godkand %}<dt>Plats</dt><dd>{{ s.plats|tal(0) }} av {{ n_godk }}</dd><dt>Poäng</dt><dd>{{ s.poang|tal(0) }}</dd>
<dt>Urvalsregler</dt><dd>{{ '✓' if s.urval else s.urval_orsak }}</dd>{% else %}<dt>Orsak</dt><dd>{{ s.orsak }}</dd>{% endif %}
<dt>Omsättning/dag</dt><dd>{{ (s.omsattning_sek / 1e6)|tal(0) }} MSEK</dd>
</dl>{% else %}<p class="muted small">Ingår inte i den svenska screenern.</p>{% endif %}
</div></div>
"""


@app.route("/aktie/<ticker>")
def aktie(ticker: str):
    ticker = ticker.upper()
    k = kurser()
    if ticker not in k.columns or k[ticker].dropna().empty:
        flash(f"Ingen kursdata för {ticker}.", "fel")
        return redirect(url_for("innehav_sida"))
    s = k[ticker].dropna()

    df, analys = analysera_allt()
    poster = [a for a in analys if a["ticker"] == ticker and not a["saknar_data"]]
    for a in poster:
        a["regler"] = regelstatus(a)

    # Nyckeltal för aktien, oavsett om den ägs
    m = ih.analysera(pd.DataFrame([{"ticker": ticker, "typ": "övrigt", "kopdatum": s.index[-1], "kopkurs": s.iloc[-1],
                                    "antal": 0, "valuta": "SEK", "notering": ""}]), k, None)[0]

    st = screener_tabell()
    srad = None
    n_godk = 0
    if st is not None:
        n_godk = int(st["godkand"].sum())
        r = st[st["ticker"] == ticker]
        srad = r.iloc[0].to_dict() if len(r) else None

    forsta = poster[0] if poster else None
    graf = graf_svg(s, forsta["kopdatum"] if forsta else None, forsta["kopkurs"] if forsta else None,
                    forsta["stopp"] if forsta and forsta["typ"] == "momentum" else None)
    return sida(ticker, "innehav", AKTIE_MALL, ticker=ticker, namn=namn_for(ticker), sista=s.iloc[-1],
                datum=s.index[-1].date(), graf=graf, poster=poster, m=m, s=srad, n_godk=n_godk)


if __name__ == "__main__":
    port = int(os.environ.get("SCREENER_PORT", "8080"))
    print(f"Screener-webben: http://0.0.0.0:{port}  (data: {UTMAPP})")
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=4)
    except ImportError:
        app.run(host="0.0.0.0", port=port)
