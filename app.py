#!/usr/bin/env python3
"""
Webbsida för screenern
======================

Sidor:
  /               veckans screener-rapport (senaste.html)
  /innehav        dina innehav med säljsignaler – lägg till och ta bort här
  /aktie/<ticker> kursgraf och nyckeltal för en aktie (innehav eller kandidat)
  /strategier     virtuella strategiportföljer (pappershandel), se strategier.py
  /backtest       resultat från backtest.py
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
import json
import math
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, Response, abort, flash, redirect, render_template_string, request, send_file, url_for

import avanza_import as ai
import ikoner
import innehav as ih
import nyckeltal as nk
import screener as sc
import strategier as st

UTMAPP = Path(os.environ.get("SCREENER_UTMAPP", sc.KONFIG["utmapp"])).expanduser()
INNEHAV_FIL = UTMAPP / "innehav.csv"
AFFAR_FIL = UTMAPP / "affarer.csv"
KARTA_FIL = UTMAPP / "isin_tickers.csv"
IMPORT_FIL = UTMAPP / ".avanza_import.csv"
IMPORT_INST = UTMAPP / ".import_installningar.json"
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
    if not LOSEN or request.path in OPPNA_SOKVAGAR:
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
svg.graf { width:100%; height:auto; display:block }
.graf-liten { display:none }
.grafwrap { overflow-x:auto }
dl.nyckel { display:grid; grid-template-columns:auto auto; gap:6px 16px; margin:0 } dl.nyckel dt { color:var(--muted) } dl.nyckel dd { margin:0; text-align:right; font-variant-numeric:tabular-nums }
footer { margin-top:24px; font-size:12px; color:var(--muted) }
.flikar { display:flex; gap:4px; border-bottom:1px solid var(--linje); margin:8px 0 16px }
.flikar button { background:none; color:var(--muted); border-radius:0; padding:10px 14px; font-weight:600;
                 border-bottom:2px solid transparent; margin-bottom:-1px }
.flikar button.aktiv { color:var(--text); border-bottom-color:var(--accent) }
.stapel { height:8px; background:var(--linje); border-radius:4px; overflow:hidden; min-width:90px }
.stapel div { height:100%; border-radius:4px; background:var(--accent) }
.poangbox { display:flex; align-items:baseline; gap:6px } .poangbox b { font-size:28px }
table.nk td, table.nk th { white-space:nowrap }
.spark { width:120px; height:28px; vertical-align:middle }
"""

# ---------------------------------------------------------------------------
# Mobil och app (PWA)
# ---------------------------------------------------------------------------
# På smala skärmar (< 720 px) flyttas menyn till en bottenrad och alla tabeller
# visas som kort: varje rad blir ett kort, varje cell en rad "rubrik … värde".
# Rubrikerna hämtas automatiskt från tabellhuvudet av MOBIL_JS, så sidornas
# mallar behöver inte ändras.
MOBIL_CSS = """
a.status { margin-left:auto; display:inline-flex; align-items:center; gap:6px; font-size:12px; font-weight:600;
           padding:3px 10px; border-radius:999px; text-decoration:none !important; white-space:nowrap }
a.status.ok { background:var(--b-behall, #e2f4e8); color:var(--pos, #0f7b3f) !important }
a.status.varn { background:var(--b-salj, #fde2e0); color:var(--neg, #c0352b) !important }
@media (prefers-color-scheme: dark) {
  a.status.ok { background:var(--b-behall, #1d3a27) } a.status.varn { background:var(--b-salj, #4a2422) } }
a.status::before { content:""; width:7px; height:7px; border-radius:50%; background:currentColor }
nav.botten { display:none }
@media (max-width: 720px) {
  body { font-size:15px; padding-bottom:calc(68px + env(safe-area-inset-bottom)) }
  nav.topp div { padding:12px 14px; padding-top:calc(12px + env(safe-area-inset-top)) }
  nav.topp a { display:none !important }
  nav.topp a.status { display:inline-flex !important }
  main { padding:16px 12px 24px !important }
  h1 { font-size:21px !important }
  .stats { display:grid !important; grid-template-columns:1fr 1fr; gap:8px !important }
  .stat { min-width:0 !important; padding:10px 12px !important }
  .stat b { font-size:18px !important }
  .grid { grid-template-columns:1fr !important }
  input, select, button { font-size:16px !important }
  input, select { width:100%; min-width:0 }
  .grid > *, form.rad > *, .card { min-width:0 }
  .card[style*="max-width"] { max-width:none !important }
  button { padding:10px 14px }
  form.rad { grid-template-columns:1fr 1fr !important }
  form.rad button { grid-column:1 / -1 }
  .graf-stor { display:none } .graf-liten { display:block }
  /* Tabeller som kort */
  main .wrap { background:none !important; border:0 !important; overflow:visible !important }
  main table { min-width:0 !important; border:0 }
  main table thead { display:none }
  main table tbody, main table tr { display:block }
  main table tr { background:var(--kort); border:1px solid var(--linje); border-radius:10px;
                  margin:0 0 10px; padding:4px 12px 8px }
  main table td { display:flex !important; justify-content:space-between; align-items:baseline; gap:12px;
                  border:0 !important; padding:5px 0 !important; text-align:right !important;
                  background:transparent !important; white-space:normal !important; min-width:0 !important }
  main table td > .v { text-align:right; min-width:0; overflow-wrap:anywhere }
  main table td::before { content:attr(data-label); color:var(--muted); font-size:12px; text-align:left; flex:none }
  main table td.kortrubrik { display:block !important; text-align:left !important; font-size:16px;
                             padding:8px 0 !important; margin-bottom:2px; border-bottom:1px solid var(--linje) !important }
  main table td.kortrubrik::before, main table td.atgard::before { content:none }
  main table td.atgard { justify-content:flex-end; gap:16px; padding-top:8px !important }
  main table td.tom { display:none !important }
  main table tr.urval { background:var(--urval, var(--kort)) }
  nav.botten { display:grid; grid-template-columns:repeat(6,1fr); position:fixed; left:0; right:0; bottom:0; z-index:50;
               background:var(--kort); border-top:1px solid var(--linje);
               padding:6px 4px calc(6px + env(safe-area-inset-bottom)) }
  nav.botten a { display:flex; flex-direction:column; align-items:center; gap:2px; font-size:11px;
                 color:var(--muted) !important; text-decoration:none !important; padding:4px 0 }
  nav.botten a.aktiv { color:var(--accent) !important; font-weight:600 }
  nav.botten svg { width:24px; height:24px }
}
"""

HEAD_EXTRA = """<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" type="image/png" sizes="192x192" href="/ikon-192.png">
<link rel="apple-touch-icon" href="/ikon-apple.png">
<meta name="theme-color" content="#f6f7f9" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#14161a" media="(prefers-color-scheme: dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="Screener">"""

_IKON = {
    "rapport": '<path d="M7 3h7l5 5v13H7z M14 3v5h5 M10 13h6 M10 17h6"/>',
    "innehav": '<path d="M4 8h16v12H4z M9 8V5h6v3 M4 13h16"/>',
    "utveckling": '<path d="M4 19h16 M5 15l4-4 3 3 7-7 M15 7h4v4"/>',
    "strategier": '<path d="M9 3h6 M10 3v6l-5 9.5A1.7 1.7 0 0 0 6.5 21h11a1.7 1.7 0 0 0 1.5-2.5L14 9V3 M7.5 15h9"/>',
    "sok": '<path d="M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13z M15.5 15.5L20 20"/>',
    "arkiv": '<path d="M4 5h16v4H4z M5 9v10h14V9 M10 13h4"/>',
}
_MENY = [("rapport", "/", "Rapport"), ("innehav", "/innehav", "Innehav"), ("utveckling", "/utveckling", "Utveckling"),
         ("strategier", "/strategier", "Strategier"), ("sok", "/sok", "Sök"), ("arkiv", "/arkiv", "Arkiv")]


def botten_nav(aktiv: str) -> str:
    lankar = "".join(
        f'<a href="{url}" class="{"aktiv" if namn == aktiv else ""}">'
        f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{_IKON[namn]}</svg>{etikett}</a>'
        for namn, url, etikett in _MENY)
    return f'<nav class="botten" aria-label="Meny">{lankar}</nav>'


MOBIL_JS = """<script>
(function(){
  // Ge varje tabellcell sin kolumnrubrik, så att tabellen kan visas som kort på mobilen
  document.querySelectorAll('main table').forEach(function(t){
    var rub=[].map.call(t.querySelectorAll('thead th'), function(e){return e.textContent.trim();});
    t.querySelectorAll('tbody tr').forEach(function(tr){
      var c=[].slice.call(tr.children), r=-1;
      c.forEach(function(td,i){
        if(rub[i]!==undefined && !td.hasAttribute('data-label')) td.setAttribute('data-label', rub[i]);
        if(td.querySelector('form,button')) td.classList.add('atgard');
        else if(!td.textContent.trim() && !td.querySelector('input,select,svg')) td.classList.add('tom');
      });
      for(var i=0;i<c.length;i++){ if(!c[i].classList.contains('atgard') && c[i].querySelector('a,b,strong')){ r=i; break; } }
      if(!c.length) return;
      var h=c[r<0?0:r]; h.classList.add('kortrubrik');
      c.forEach(function(td){
        // Rangnummer (#) flyttas in i kortrubriken
        if(td!==h && td.getAttribute('data-label')==='#' && td.textContent.trim()){
          var s=document.createElement('span'); s.className='muted'; s.textContent=td.textContent.trim()+'. ';
          h.insertBefore(s, h.firstChild); td.classList.add('tom'); return; }
        // Håll ihop cellens innehåll (t.ex. "718.95 SEK") till höger om rubriken
        if(td!==h && !td.classList.contains('atgard') && !td.classList.contains('tom')){
          var v=document.createElement('span'); v.className='v';
          while(td.firstChild) v.appendChild(td.firstChild); td.appendChild(v); }
      });
    });
  });
  // Flikar (t.ex. Översikt / Nyckeltal på aktiesidan). #nyckeltal i adressen öppnar den fliken direkt.
  document.querySelectorAll('.flikar').forEach(function(f){
    var knappar=f.querySelectorAll('button[data-vy]'), yta=f.parentNode;
    function visa(v){
      knappar.forEach(function(b){ b.classList.toggle('aktiv', b.dataset.vy===v); });
      yta.querySelectorAll(':scope > .vy').forEach(function(d){ d.hidden = d.dataset.vy!==v; });
      try{ history.replaceState(null,'', v==='oversikt' ? location.pathname+location.search : '#'+v); }catch(e){}
    }
    knappar.forEach(function(b){ b.addEventListener('click', function(){ visa(b.dataset.vy); }); });
    if(location.hash.length>1 && f.querySelector('button[data-vy="'+location.hash.slice(1)+'"]')) visa(location.hash.slice(1));
  });
  if('serviceWorker' in navigator && window.isSecureContext){ navigator.serviceWorker.register('/sw.js').catch(function(){}); }
})();
</script>"""

BAS = """<!doctype html><html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"><title>{{ titel }} · Screener</title>
{{ head_extra|safe }}
<style>{{ css|safe }}{{ mobil_css|safe }}</style></head><body>
<nav class="topp"><div><b>📈 Screener</b>
<a href="{{ url_for('rapport') }}" class="{{ 'aktiv' if sida=='rapport' }}">Veckans rapport</a>
<a href="{{ url_for('innehav_sida') }}" class="{{ 'aktiv' if sida=='innehav' }}">Mina innehav</a>
<a href="{{ url_for('utveckling_sida') }}" class="{{ 'aktiv' if sida=='utveckling' }}">Utveckling</a>
<a href="{{ url_for('strategier_sida') }}" class="{{ 'aktiv' if sida=='strategier' }}">Strategier</a>
<a href="{{ url_for('sok_sida') }}" class="{{ 'aktiv' if sida=='sok' }}">Sök</a>
<a href="{{ url_for('arkiv') }}" class="{{ 'aktiv' if sida=='arkiv' }}">Arkiv</a>{{ status|safe }}</div></nav>
<main>
{% with meddelanden = get_flashed_messages(with_categories=true) %}{% for kat, m in meddelanden %}
<div class="flash {{ kat }}">{{ m }}</div>{% endfor %}{% endwith %}
{{ innehall|safe }}
<footer>Underlag för egna beslut, inte köp- eller säljrekommendationer. Data: Yahoo Finance.</footer>
</main>{{ botten|safe }}{{ mobil_js|safe }}</body></html>"""


def sida(titel: str, flik: str, mall: str, **kw) -> str:
    innehall = render_template_string(mall, **kw)
    return render_template_string(BAS, titel=titel, sida=flik, css=CSS, innehall=innehall, head_extra=HEAD_EXTRA,
                                  status=status_knapp(),
                                  mobil_css=MOBIL_CSS, mobil_js=MOBIL_JS, botten=botten_nav(flik))


# ---------------------------------------------------------------------------
# App-filer: manifest, ikoner, service worker, offline-sida
# ---------------------------------------------------------------------------
OPPNA_SOKVAGAR = {"/manifest.webmanifest", "/sw.js", "/ikon-192.png", "/ikon-512.png",
                  "/ikon-maskable.png", "/ikon-apple.png", "/offline"}


@app.route("/manifest.webmanifest")
def manifest():
    data = {
        "name": "Momentum-screener", "short_name": "Screener", "lang": "sv",
        "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#f6f7f9", "theme_color": "#2b5fd9",
        "icons": [
            {"src": "/ikon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/ikon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/ikon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Mina innehav", "url": "/innehav"},
            {"name": "Sök", "url": "/sok"},
        ],
    }
    return Response(json.dumps(data, ensure_ascii=False), mimetype="application/manifest+json")


@app.route("/ikon-<namn>.png")
def ikon(namn: str):
    import base64
    if namn not in ikoner.IKONER:
        abort(404)
    return Response(base64.b64decode(ikoner.IKONER[namn]), mimetype="image/png",
                    headers={"Cache-Control": "public, max-age=604800"})


SERVICE_WORKER = """
// Nätet först; utan nät visas senast sparade versionen av sidan, annars en offline-sida.
const CACHE = 'screener-v1';
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(['/offline', '/ikon-192.png'])));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(k => Promise.all(k.filter(n => n !== CACHE).map(n => caches.delete(n)))));
  self.clients.claim();
});
self.addEventListener('fetch', e => {
  const r = e.request;
  if (r.method !== 'GET' || new URL(r.url).origin !== location.origin) return;
  e.respondWith(
    fetch(r).then(svar => {
      if (svar.ok && r.mode === 'navigate') { const kopia = svar.clone(); caches.open(CACHE).then(c => c.put(r, kopia)); }
      return svar;
    }).catch(() => caches.match(r).then(m => m || (r.mode === 'navigate' ? caches.match('/offline') : Response.error())))
  );
});
"""


@app.route("/sw.js")
def service_worker():
    return Response(SERVICE_WORKER, mimetype="application/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.route("/offline")
def offline():
    return sida("Offline", "", """<h1>Ingen anslutning</h1>
<div class="card"><p>Servern går inte att nå just nu. Kontrollera att Tailscale är på i telefonen
och att hemmaservern är igång.</p><p class="muted small">Sidor du öppnat tidigare visas från telefonens minne
när nätet saknas – de kan vara inaktuella.</p>
<button onclick="location.reload()">Försök igen</button></div>""")


# ---------------------------------------------------------------------------
# Status: körs insamlingen som den ska?
# ---------------------------------------------------------------------------
_status_cache: dict = {"tid": 0.0, "rader": None}


def _handelsdagar_bakat(n: int) -> list[dt.date]:
    """De n senaste vardagarna vars data borde finnas (idag räknas efter kl. 19)."""
    nu = dt.datetime.now()
    d = nu.date() if nu.hour >= 19 else nu.date() - dt.timedelta(days=1)
    ut = []
    while len(ut) < n:
        if d.weekday() < 5:
            ut.append(d)
        d -= dt.timedelta(days=1)
    return ut


def status_rader() -> list[dict]:
    """En rad per insamling: namn, senaste datum, ok/varning och förklaring. Cachas en minut."""
    import time
    if _status_cache["rader"] is not None and time.time() - _status_cache["tid"] < 60:
        return _status_cache["rader"]
    rader = []
    dagar = _handelsdagar_bakat(2)

    # Nyckeltal: får missa högst en handelsdag (helgdagar, tillfälligt fel hos Yahoo)
    filer = sorted((UTMAPP / "nyckeltal").glob("????-??-??.csv")) if (UTMAPP / "nyckeltal").exists() else []
    if filer:
        senast = dt.date.fromisoformat(filer[-1].stem)
        ok = senast >= dagar[-1]
        rader.append({"namn": "Nyckeltal", "datum": senast, "ok": ok,
                      "text": f"{len(filer)} dagar sparade" if ok else
                      f"inget sparat sedan {senast} – kontrollera cron och nyckeltal_logg.txt"})
    else:
        rader.append({"namn": "Nyckeltal", "datum": None, "ok": False, "text": "ingen insamling har körts än"})

    # Veckorapporten: körs lördagar, varning om äldre än 8 dagar
    filer = sorted(UTMAPP.glob("screener_*.csv"))
    if filer:
        senast = dt.date.fromisoformat(filer[-1].stem.removeprefix("screener_"))
        ok = (dt.date.today() - senast).days <= 8
        rader.append({"namn": "Veckorapport", "datum": senast, "ok": ok,
                      "text": f"{len(filer)} rapporter" if ok else f"ingen ny rapport sedan {senast} – kontrollera cron och logg.txt"})
    else:
        rader.append({"namn": "Veckorapport", "datum": None, "ok": False, "text": "screener.py har inte körts än"})

    # Kurser (uppdateras av screenern och knappen Uppdatera kurser)
    k = kurser()
    if len(k):
        senast = k.index.max().date()
        ok = (dt.date.today() - senast).days <= 9
        rader.append({"namn": "Kurser", "datum": senast, "ok": ok,
                      "text": "" if ok else "gamla kurser – kör screenern eller Uppdatera kurser"})
    # Strategierna: beslutas vid veckokörningen, daglig stopp varje vardag
    try:
        tillst = {sid: st.las_tillstand(st.mapp(UTMAPP, sid)) for sid in st.STRATEGIER}
        startade = {sid: t for sid, t in tillst.items() if t}
        if startade:
            senast = max(t["senaste_rapport"] for t in startade.values())
            ok = (dt.date.today() - dt.date.fromisoformat(senast)).days <= 8
            rader.append({"namn": "Strategier", "datum": dt.date.fromisoformat(senast), "ok": ok,
                          "text": f"{len(startade)} av {len(st.STRATEGIER)} startade" if ok else
                          f"inga beslut sedan {senast} – kontrollera logg.txt"})
            for sid, t in startade.items():
                if st.inst(sid)["dagligt"]:
                    dag = t.get("senaste_dag")
                    ok = bool(dag) and dt.date.fromisoformat(dag) >= dagar[-1]
                    rader.append({"namn": st.STRATEGIER[sid]["namn"], "datum": dt.date.fromisoformat(dag) if dag else None,
                                  "ok": ok or not dag and (dt.date.today() - dt.date.fromisoformat(t["start"])).days <= 3,
                                  "text": "daglig kontroll" if ok else
                                  ("väntar på första dagliga körningen" if not dag else
                                   f"ingen daglig kontroll sedan {dag} – kontrollera cron och strategier_logg.txt")})
    except Exception:
        pass
    _status_cache.update(tid=time.time(), rader=rader)
    return rader


def status_knapp() -> str:
    try:
        rader = status_rader()
    except Exception:
        return ""
    fel = [r for r in rader if not r["ok"]]
    if not fel:
        return '<a class="status ok" href="/status" title="All insamling är i fas">Data</a>'
    return f'<a class="status varn" href="/status" title="{html.escape(fel[0]["text"])}">{html.escape(fel[0]["namn"])}</a>'


STATUS_MALL = """
<h1>Status</h1>
<p class="muted">Om insamlingen körs som den ska. Nyckeltalen får missa en handelsdag (t.ex. helgdag) innan de flaggas.</p>
<div class="wrap"><table>
<thead><tr><th>Insamling</th><th>Status</th><th>Senast</th><th>Kommentar</th></tr></thead>
<tbody>{% for r in rader %}<tr><td><b>{{ r.namn }}</b></td>
<td><span class="badge {{ 'behall' if r.ok else 'salj' }}">{{ 'OK' if r.ok else 'KOLLA' }}</span></td>
<td>{{ r.datum or '–' }}</td><td class="small">{{ r.text }}</td></tr>{% endfor %}</tbody></table></div>
<div class="card" style="margin-top:16px"><h2>Felsöka på servern</h2>
<pre class="small" style="white-space:pre-wrap;margin:0">crontab -l
tail -30 {{ utmapp }}/nyckeltal_logg.txt
tail -30 {{ utmapp }}/logg.txt
tail -30 {{ utmapp }}/strategier_logg.txt
timedatectl | grep "Time zone"</pre></div>
"""


@app.route("/status")
def status_sida():
    _status_cache["rader"] = None
    return sida("Status", "", STATUS_MALL, rader=status_rader(), utmapp=UTMAPP)


# ---------------------------------------------------------------------------
# Veckans rapport och arkiv
# ---------------------------------------------------------------------------
NAV_INJEKTION = """<nav class="topp" style="background:var(--kort);border-bottom:1px solid var(--linje)">
<div style="max-width:1200px;margin:0 auto;padding:10px 16px;display:flex;gap:20px;flex-wrap:wrap">
<b>📈 Screener</b><a href="/" style="color:var(--text);font-weight:600;text-decoration:none">Veckans rapport</a>
<a href="/innehav" style="color:var(--accent);text-decoration:none">Mina innehav</a><a href="/utveckling" style="color:var(--accent);text-decoration:none">Utveckling</a><a href="/strategier" style="color:var(--accent);text-decoration:none">Strategier</a><a href="/sok" style="color:var(--accent);text-decoration:none">Sök</a><a href="/arkiv" style="color:var(--accent);text-decoration:none">Arkiv</a></div></nav>"""

LANK_SKRIPT = """<script>
document.querySelectorAll('[data-t]').forEach(function(el){
  var a=document.createElement('a'); a.href='/aktie/'+encodeURIComponent(el.dataset.t);
  a.style.color='inherit'; a.style.textDecoration='none'; el.parentNode.insertBefore(a,el); a.appendChild(el); });
</script>"""


def visa_rapport(fil: Path) -> str:
    text = fil.read_text(encoding="utf-8")
    text = text.replace("initial-scale=1\">", "initial-scale=1, viewport-fit=cover\">", 1)
    text = text.replace("</head>", HEAD_EXTRA + "<style>" + MOBIL_CSS + "</style></head>", 1)
    text = text.replace("<body>", "<body>" + NAV_INJEKTION.replace("</div></nav>", status_knapp() + "</div></nav>", 1), 1)
    return text.replace("</body>", LANK_SKRIPT + botten_nav("rapport") + MOBIL_JS + "</body>", 1)


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
<div class="card" style="grid-column:1/-1;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">
<div><h2 style="margin:0">Importera från Avanza</h2>
<span class="small muted">Läser in alla köp och försäljningar från Avanzas exportfil – rekommenderat i stället för att mata in för hand.</span></div>
<a href="{{ url_for('import_sida') }}"><button type="button">Importera transaktioner</button></a>
</div>
<div class="card" style="grid-column:1/-1" id="lagg-till">
<h2>Lägg till innehav</h2>
<form class="rad" method="post" action="{{ url_for('lagg_till') }}">
<label>Ticker<input name="ticker" required placeholder="VOLV-B.ST eller ADCT" value="{{ request.args.get('ticker', '') }}"></label>
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
def graf_jamfor(port: pd.Series, index: pd.Series | None, indexnamn: str, B: int = 860) -> str:
    """Två linjer i procent: portföljen och index, med nollinje."""
    H, VM, HM, TM, BM = (300, 56, 16, 14, 28) if B >= 600 else (280, 62, 10, 14, 28)
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
                     f'{v * 100:+.0f}{"%" if B < 600 else " %"}</text>')
        v += steg
    dagar = (port.index[-1] - port.index[0]).days
    frekv = "MS" if dagar > 75 else "W-MON"
    fmt = "%b %y" if dagar > 75 else "%d %b"
    etiketter = pd.date_range(port.index[0], port.index[-1], freq=frekv)
    maxant = 12 if B >= 600 else 5
    if len(etiketter) > maxant:
        etiketter = etiketter[::math.ceil(len(etiketter) / maxant)]
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
            graf = dubbel(graf_jamfor, u["avkastning"], idx, ih.INDEXNAMN.get(ih.JAMFORELSEINDEX, ih.JAMFORELSEINDEX))

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
# Import från Avanza
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Strategier – virtuella portföljer (pappershandel), se strategier.py
# ---------------------------------------------------------------------------
STRAT_FARG = {"momentum_v1": "var(--accent)", "b": "#e0812f", "c": "#2a9d6f", "kvalitet": "#8a63d2",
              "topp10": "#c9a227", "utan_urval": "#3fa7c9", "daglig_stopp": "#d4566a", "slump": "#9aa0a6",
              "egen": "#b0489c"}


def graf_flera(serier: list[tuple[str, pd.Series, str, float]], B: int = 860) -> str:
    """Flera linjer i procent med nollinje. serier = [(namn, serie, färg, linjebredd)]."""
    serier = [(n, s.dropna(), f, w) for n, s, f, w in serier if s is not None and len(s.dropna())]
    if not serier:
        return ""
    H, VM, HM, TM, BM = (300, 56, 16, 14, 28) if B >= 600 else (280, 62, 10, 14, 28)
    alla = pd.concat([s for _, s, _, _ in serier])
    lo, hi = min(0.0, float(alla.min())), max(0.0, float(alla.max()))
    pad = (hi - lo) * 0.08 or 0.01
    lo, hi = lo - pad, hi + pad
    t0 = min(s.index[0] for _, s, _, _ in serier).value
    t1 = max(s.index[-1] for _, s, _, _ in serier).value
    if t1 == t0:
        return "<p class='muted'>Grafen visas när strategin funnits i minst två handelsdagar.</p>"

    def x(t):
        return VM + (pd.Timestamp(t).value - t0) / (t1 - t0) * (B - VM - HM)

    def y(v):
        return TM + (hi - v) / (hi - lo) * (H - TM - BM)

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
                     f'{v * 100:+.0f}{"%" if B < 600 else " %"}</text>')
        v += steg
    forsta, sista = pd.Timestamp(t0), pd.Timestamp(t1)
    dagar = (sista - forsta).days
    etiketter = pd.date_range(forsta, sista, freq="MS" if dagar > 75 else "W-MON")
    maxant = 12 if B >= 600 else 5
    if len(etiketter) > maxant:
        etiketter = etiketter[::math.ceil(len(etiketter) / maxant)]
    for t in etiketter:
        delar.append(f'<text x="{x(t):.1f}" y="{H - 8}" text-anchor="middle" font-size="11" fill="var(--muted)">'
                     f'{t.strftime("%b %y" if dagar > 75 else "%d %b")}</text>')
    for _, s, farg, bredd in serier:
        p = [(x(i), y(v)) for i, v in s.items()]
        if len(p) >= 2:
            streck = ' stroke-dasharray="5 4"' if farg == "var(--muted)" else ""  # index streckat
            delar.append(f'<path d="M{" L".join(f"{a:.1f},{b:.1f}" for a, b in p)}" fill="none" stroke="{farg}" '
                         f'stroke-width="{bredd}" stroke-linejoin="round"{streck}/>')
        delar.append(f'<circle cx="{p[-1][0]:.1f}" cy="{p[-1][1]:.1f}" r="3.5" fill="{farg}"/>')
    forklaring = " ".join(f'<span style="white-space:nowrap;margin-right:12px"><span style="color:{f}">'
                          f'{"╍" if f == "var(--muted)" else "━"}</span> {html.escape(n)}</span>' for n, _, f, _ in serier)
    return (f'<div class="grafwrap"><svg class="graf" viewBox="0 0 {B} {H}" role="img" aria-label="Strategiernas utveckling">'
            f'{"".join(delar)}</svg></div><p class="small muted" style="margin:6px 0 0">{forklaring}</p>')


def strat_mapp_demo() -> bool:
    return DEMO and st.rotmapp(UTMAPP, True).exists()


def _indexserie(k: pd.DataFrame, fran: pd.Timestamp) -> pd.Series | None:
    if ih.JAMFORELSEINDEX not in k.columns:
        return None
    s = k[ih.JAMFORELSEINDEX].ffill()
    s = s[s.index >= fran].dropna()
    return s / s.iloc[0] - 1 if len(s) else None


def _egen_serie(k: pd.DataFrame, fran: pd.Timestamp) -> pd.Series | None:
    """Din egen momentumdel, omräknad så att den startar på 0 samma dag som strategierna."""
    try:
        u = ih.utveckling(ih.las_innehav(INNEHAV_FIL), ih.las_affarer(AFFAR_FIL), k, ["momentum"])
    except Exception:
        return None
    if len(u) < 2:
        return None
    fore = u[u.index <= fran]
    bas = 1 + (fore["avkastning"].iloc[-1] if len(fore) else 0.0)
    u = u[u.index >= fran]
    return (1 + u["avkastning"]) / bas - 1 if len(u) else None


STRATEGIER_MALL = """
<div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap">
<h1>Strategier</h1><a href="{{ url_for('backtest_sida') }}"><b>Backtest – hur hade det gått tidigare? →</b></a></div>
<p class="muted">Låtsasportföljer som följer varje strategi exakt, utan riktiga pengar. Varje lördag efter
veckorapporten säljer de det reglerna säger sälj och köper gröna rader (köpläge) tills de är fulla
({{ max }} aktier om inget annat anges).
Start {{ start|kr }} per strategi, lika vikt, courtage {{ (courtage * 100)|round(2) }} % (minst {{ ct_min|round|int }} kr)
och {{ (spread * 100)|round(2) }} % spread på varje affär.</p>

{% if not startade %}
<div class="card">Ingen strategi har startat än. De startar automatiskt vid nästa veckokörning,
eller direkt med <code>./venv/bin/python strategier.py</code>.</div>
{% else %}
<div class="card" style="margin:16px 0">{{ graf|safe }}</div>
<div class="wrap"><table style="min-width:900px">
<thead><tr><th>Strategi</th><th class="num">Värde</th><th class="num">Avkastning</th><th class="num">Mot index</th>
<th class="num">Innehav</th><th class="num">Kassa</th><th class="num">Affärer</th><th class="num">Träffsäkerhet</th>
<th class="num">Courtage</th><th>Startad</th></tr></thead><tbody>
{% for r in rader %}<tr>
<td style="min-width:200px"><a href="{{ url_for('strategi_sida', sid=r.id) }}"><b><span style="color:{{ r.farg }}">●</span> {{ r.namn }}</b></a>
<div class="muted small">{{ r.kort }}</div></td>
{% if r.startad %}
<td class="num">{{ r.varde|kr }}</td><td class="num {{ r.avkastning|klass }}">{{ r.avkastning|pct }}</td>
<td class="num {{ r.mot_index|klass }}">{{ r.mot_index|pct }}</td>
<td class="num">{{ r.antal }}/{{ r.max_innehav }}</td><td class="num">{{ r.kassa|kr }}</td><td class="num">{{ r.affarer }}</td>
<td class="num">{{ r.stat.traffsakerhet|pct(false) if r.stat.antal else '–' }}</td><td class="num">{{ r.courtage|kr }}</td>
<td class="small">{{ r.tillstand.start }}</td>
{% else %}<td colspan="9" class="muted small">{{ r.vantar }}</td>{% endif %}
</tr>{% endfor %}
{% if egen_avk is not none %}<tr><td><a href="{{ url_for('utveckling_sida') }}"><b><span style="color:{{ farg_egen }}">●</span> Din momentumportfölj</b></a>
<div class="muted small">Dina riktiga affärer, samma period</div></td>
<td class="num">–</td><td class="num {{ egen_avk|klass }}">{{ egen_avk|pct }}</td><td class="num {{ egen_mot|klass }}">{{ egen_mot|pct }}</td>
<td colspan="6"></td></tr>{% endif %}
<tr><td><b><span style="color:var(--muted)">●</span> {{ indexnamn }}</b><div class="muted small">Jämförelseindex</div></td>
<td class="num">–</td><td class="num {{ index_avk|klass }}">{{ index_avk|pct }}</td><td colspan="7"></td></tr>
</tbody></table></div>
<p class="small muted">Avkastningen räknas från varje strategis startdag och index från den tidigaste starten.
Strategierna får utdelningar (räknas som återinvesterade, samma kurser som Utveckling-sidan), medan
{{ indexnamn }} är ett prisindex utan utdelningar – index ser därför runt 3 procentenheter per år sämre ut än det är.
Jämförelsen mellan strategierna är helt rättvis. Det krävs flera månader innan skillnaderna betyder något.</p>
{% endif %}
"""

STRATEGI_MALL = """
{% if bt %}<p class="small"><a href="{{ url_for('backtest_sida', kor=bt.namn) }}">← Backtest {{ bt.fran }} – {{ bt.till }}</a></p>
<div class="flash fel" style="margin-bottom:12px">Backtest – vad som hade hänt om boten startat {{ bt.fran }}. Inte riktig pappershandel.</div>
{% else %}<p class="small"><a href="{{ url_for('strategier_sida') }}">← Alla strategier</a></p>{% endif %}
<h1><span style="color:{{ farg }}">●</span> {{ s.namn }}</h1>
<p class="muted">{{ s.kort }}. Startad {{ t.start }} · senaste beslut {{ t.senaste_rapport }}
{% if t.senaste_nyckeltal %} · nyckeltal från {{ t.senaste_nyckeltal }}{% endif %}.{% if not bt %} Nästa beslut vid veckokörningen på lördag.{% endif %}
{% if s.dagligt %}Stopp och trendregler kontrolleras dessutom varje vardag – senast {{ t.senaste_dag or 'inte än' }}.{% endif %}</p>

<div class="stats">
<div class="stat" style="border-color:{{ farg }}"><span class="muted small">Värde</span><b>{{ varde|kr }}</b>
<span class="small {{ avk|klass }}">{{ avk|pct }} sedan start</span></div>
<div class="stat"><span class="muted small">Mot {{ indexnamn }}</span><b class="{{ mot_index|klass }}">{{ mot_index|pct }}</b>
<span class="small muted">index {{ index_avk|pct }}</span></div>
<div class="stat"><span class="muted small">Innehav</span><b>{{ analys|length }}/{{ max }}</b><span class="small muted">kassa {{ t.kassa|kr }}</span></div>
<div class="stat"><span class="muted small">Avslutade affärer</span><b>{{ stat.antal }}</b>
<span class="small muted">{% if stat.antal %}{{ stat.traffsakerhet|pct(false) }} med vinst{% endif %}</span></div>
{% if stat.antal %}<div class="stat"><span class="muted small">Snittvinst / snittförlust</span>
<b><span class="pos">{{ stat.snittvinst|pct }}</span> <span class="neg">{{ stat.snittforlust|pct }}</span></b></div>{% endif %}
<div class="stat"><span class="muted small">Courtage + spread</span><b>{{ kostnad|kr }}</b></div>
</div>

<div class="card" style="margin:16px 0">{{ graf|safe }}</div>

<h2>Innehav</h2>
{% if analys %}
<div class="wrap"><table style="min-width:960px">
<thead><tr><th>Aktie</th><th>Beslut</th><th class="num">Antal</th><th class="num">Köpkurs</th><th class="num">Kurs</th>
<th class="num">Resultat</th><th class="num">Värde</th><th class="num">Från högsta</th><th class="num">Till stopp</th>
<th class="num">Plats</th><th style="min-width:220px">Orsak</th></tr></thead><tbody>
{% for a in analys %}<tr>
<td><a style="white-space:nowrap" href="{{ url_for('aktie', ticker=a.ticker) }}"><b>{{ a.ticker|kort }}</b></a>
<div class="muted small">{{ a.kopdatum|datum }}</div></td>
<td><span class="badge {{ a.beslut|bklass }}">{{ a.beslut }}</span></td>
<td class="num">{{ a.antal|tal(0) }}</td><td class="num">{{ a.kopkurs|tal }}</td><td class="num">{{ a.kurs|tal }}</td>
<td class="num {{ a.resultat|klass }}">{{ a.resultat|pct }}</td><td class="num">{{ a.varde_sek|kr }}</td>
<td class="num {{ a.fran_hogsta|klass }}">{{ a.fran_hogsta|pct }}</td><td class="num">{{ a.till_stopp|pct }}</td>
<td class="num">{% if a.plats %}{{ a.plats }}{% elif a.i_screener %}<span class="neg">ej godk.</span>{% else %}–{% endif %}</td>
<td class="small">{{ (a.signaler + a.manad + a.bevaka + (['för stor andel – trimmas'] if a.trimma else []))|join('; ') }}</td>
</tr>{% endfor %}</tbody></table></div>
<p class="small muted">{% if bt %}Innehaven när backtestet slutade, med beslutet som gällde då.{% else %}Beslutet visar läget med dagens kurser. Boten agerar bara vid veckokörningen, precis som du.{% endif %}</p>
{% else %}<div class="card muted">Inga innehav – inga gröna rader fanns vid senaste beslutet. Kassan väntar till nästa vecka.</div>{% endif %}

<h2>Affärslogg</h2>
{% if logg %}
<div class="wrap"><table style="min-width:860px">
<thead><tr><th>Datum</th><th>Handling</th><th>Aktie</th><th class="num">Antal</th><th class="num">Kurs</th>
<th class="num">Belopp</th><th class="num">Courtage</th><th class="num">Resultat</th><th>Orsak</th></tr></thead><tbody>
{% for r in logg %}<tr>
<td class="small">{{ r.datum|datum }}</td>
<td><span class="badge {{ 'behall' if r.handling == 'KÖP' else 'salj' }}">{{ r.handling }}</span></td>
<td><a href="{{ url_for('aktie', ticker=r.ticker) }}"><b>{{ r.ticker|kort }}</b></a></td>
<td class="num">{{ r.antal|tal(0) }}</td><td class="num">{{ r.kurs|tal }}</td><td class="num">{{ r.brutto|kr }}</td>
<td class="num">{{ r.courtage|tal }}</td><td class="num {{ r.resultat|klass }}">{{ r.resultat|pct if r.resultat is not none else '' }}</td>
<td class="small">{{ r.orsak }}</td></tr>{% endfor %}</tbody></table></div>
{% else %}<div class="card muted">Inga affärer ännu.</div>{% endif %}
"""


def _vantar_text(sid: str) -> str:
    if st.inst(sid)["nyckeltal"]:
        return "Startar vid nästa veckokörning (kräver nyckeltal från senaste veckan)."
    return "Startar vid nästa veckokörning."


@app.route("/strategier")
def strategier_sida():
    demo = strat_mapp_demo()
    k = kurser()
    rader = st.oversikt(UTMAPP, k, demo)
    startade = [r for r in rader if r["startad"] and len(r["serie"])]
    graf = ""
    egen_avk = egen_mot = index_avk = None
    if startade:
        fran = min(r["serie"].index[0] for r in startade)
        idx = _indexserie(k, fran)
        egen = _egen_serie(k, fran)
        index_avk = float(idx.iloc[-1]) if idx is not None else None
        for r in rader:
            r["farg"] = STRAT_FARG.get(r["id"], "var(--accent)")
            r["vantar"] = _vantar_text(r["id"])
            if r["startad"] and len(r["serie"]):
                i2 = _indexserie(k, r["serie"].index[0])
                r["mot_index"] = r["avkastning"] - float(i2.iloc[-1]) if i2 is not None else None
        if egen is not None:
            egen_avk = float(egen.iloc[-1])
            egen_mot = egen_avk - index_avk if index_avk is not None else None
        serier = [(ih.INDEXNAMN.get(ih.JAMFORELSEINDEX, ih.JAMFORELSEINDEX), idx, "var(--muted)", 1.5)]
        if egen is not None:
            serier.append(("Din momentumportfölj", egen, STRAT_FARG["egen"], 1.8))
        serier += [(r["namn"], r["serie"]["avkastning"], r["farg"], 2.2) for r in startade]
        graf = dubbel(graf_flera, serier)
    else:
        for r in rader:
            r["farg"] = STRAT_FARG.get(r["id"], "var(--accent)")
            r["vantar"] = _vantar_text(r["id"])
    return sida("Strategier", "strategier", STRATEGIER_MALL, rader=rader, startade=bool(startade), graf=graf,
                egen_avk=egen_avk, egen_mot=egen_mot, index_avk=index_avk, farg_egen=STRAT_FARG["egen"],
                indexnamn=ih.INDEXNAMN.get(ih.JAMFORELSEINDEX, ih.JAMFORELSEINDEX), max=st.MAX_INNEHAV,
                start=st.STARTKAPITAL, courtage=st.COURTAGE_PROCENT, ct_min=st.COURTAGE_MINIMUM, spread=st.SPREAD)


@app.route("/strategi/<sid>")
def strategi_sida(sid: str):
    if sid not in st.STRATEGIER:
        abort(404)
    demo = strat_mapp_demo()
    if st.las_tillstand(st.mapp(UTMAPP, sid, demo)) is None:
        flash(f"{st.STRATEGIER[sid]['namn']} har inte startat än.", "fel")
        return redirect(url_for("strategier_sida"))
    tab = screener_tabell()
    nyck = None
    if st.inst(sid)["nyckeltal"]:
        try:
            nyck = st.las_nyckeltal(UTMAPP, dt.date.today())
        except Exception:
            nyck = None
    return _strategi_vy(UTMAPP, kurser(), sid, tab, nyck, demo)


def _strategi_vy(bas: Path, k: pd.DataFrame, sid: str, tab, nyck, demo: bool = False, bt: dict | None = None) -> str:
    S = st.inst(sid)
    m = st.mapp(bas, sid, demo)
    t = st.las_tillstand(m)
    inn = ih.las_innehav(m / "innehav.csv")
    rank = None
    try:
        if tab is not None:
            if "bolag" not in tab.columns:
                tab["bolag"] = tab["ticker"].map(sc.bolagsnyckel)
            rank = st.rangordna(sid, tab, nyck)
    except Exception:
        rank = None
    analys = ih.analysera(inn, k, rank, {"STOPP_PROCENT": S["stopp"], "SALJ_RANK": S["salj_rank"] or 10**9}) if len(inn) else []
    v = st.vardeserie(bas, sid, k, demo)
    idx = _indexserie(k, v.index[0]) if len(v) else None
    avk = float(v["avkastning"].iloc[-1]) if len(v) else 0.0
    index_avk = float(idx.iloc[-1]) if idx is not None else None
    farg = STRAT_FARG.get(sid, "var(--accent)")
    indexnamn = ih.INDEXNAMN.get(ih.JAMFORELSEINDEX, ih.JAMFORELSEINDEX)
    graf = dubbel(graf_flera, [(indexnamn, idx, "var(--muted)", 1.5),
                               (S["namn"], v["avkastning"] if len(v) else None, farg, 2.2)])
    logg = st.las_logg(m)
    avsl = ih.las_affarer(m / "avslutade.csv")
    # Resultat per försäljning (inklusive kostnader), matchat mot avslutade affärer i samma ordning
    res = list(avsl["saljkurs"] / avsl["kopkurs"] - 1)
    rader = []
    for r in logg.to_dict("records"):
        r["resultat"] = res.pop(0) if r["handling"] == "SÄLJ" and res else None
        rader.append(r)
    kostnad = float(logg["courtage"].sum() + (logg["brutto"] * st.SPREAD / (1 + st.SPREAD)).sum()) if len(logg) else 0.0
    return sida(S["namn"], "strategier", STRATEGI_MALL, s=S, t=t, farg=farg, bt=bt,
                varde=float(v["varde"].iloc[-1]) if len(v) else t["kassa"], avk=avk, index_avk=index_avk,
                mot_index=avk - index_avk if index_avk is not None else None, indexnamn=indexnamn,
                analys=analys, max=S["max_innehav"], stat=ih.affarsstatistik(avsl), graf=graf,
                logg=list(reversed(rader)), kostnad=kostnad)


# ---------------------------------------------------------------------------
# Backtest – resultat från backtest.py
# ---------------------------------------------------------------------------
BACKTEST_MAPP = UTMAPP / "backtest"
_bt_cache: dict[str, tuple[float, pd.DataFrame]] = {}


def backtest_korningar() -> list[dict]:
    if not BACKTEST_MAPP.exists():
        return []
    ut = []
    for d in BACKTEST_MAPP.iterdir():
        f = d / "info.json"
        if d.is_dir() and f.exists():
            try:
                ut.append({"namn": d.name, **json.loads(f.read_text(encoding="utf-8"))})
            except Exception:
                pass
    return sorted(ut, key=lambda x: (x.get("demo", False), x["manader"]))


def bt_kurser(namn: str) -> pd.DataFrame:
    f = BACKTEST_MAPP / namn / "kurser.csv"
    mt = f.stat().st_mtime
    if namn not in _bt_cache or _bt_cache[namn][0] != mt:
        _bt_cache[namn] = (mt, ih.las_kurser(f))
    return _bt_cache[namn][1]


BACKTEST_MALL = """
<p class="small"><a href="{{ url_for('strategier_sida') }}">← Strategier (framåt, på riktigt)</a></p>
<h1>Backtest</h1>
<p class="muted">Vad som hade hänt om strategierna startat tidigare. Samma kod som låtsasportföljerna: varje lördag i
perioden räknas topplistan fram med bara de kurser som fanns då, och boten fattar sina beslut.</p>
{% if korningar %}
<div class="small" style="margin:8px 0 14px">Period:
{% for b in korningar %}<a href="{{ url_for('backtest_sida', kor=b.namn) }}" style="margin-left:10px;{{ 'font-weight:700;color:var(--text)' if b.namn == bt.namn }}">{{ b.manader }} mån{{ ' (demo)' if b.demo }}</a>{% endfor %}</div>
<div class="card" style="margin-bottom:16px;border-color:var(--varn-kant, #d9822b)">
<b>Läs med försiktighet.</b>
<ul class="small" style="margin:6px 0 0;padding-left:18px">
<li><b>Överlevnadsfel:</b> bara bolag som är noterade idag ingår. Bolag som gått i konkurs eller avnoterats saknas,
så alla strategier ser något bättre ut än de hade varit.</li>
<li><b>En period är ett utfall.</b> Jämför flera längder – en strategi som vinner i alla är mer trovärdig än en som vinner stort i en.</li>
<li><b>Jämför med slumpportföljen</b>, inte bara index. Ändra inte strategierna efter backtestet – det är framåttestet som avgör.</li>
<li>{{ bt.hoppade|join(', ') }} kräver historiska nyckeltal och kan inte testas bakåt.</li>
</ul></div>
<p class="small muted">{{ bt.fran }} – {{ bt.till }} · {{ bt.veckor }} veckor · {{ bt.aktier }} aktier · kördes {{ bt.skapad|replace('T', ' ') }}</p>
<div class="card" style="margin:12px 0 16px">{{ graf|safe }}</div>
<div class="wrap"><table style="min-width:860px">
<thead><tr><th>Strategi</th><th class="num">Värde</th><th class="num">Avkastning</th><th class="num">Mot index</th>
<th class="num">Mot slump</th><th class="num">Affärer</th><th class="num">Träffsäkerhet</th><th class="num">Courtage</th>
<th class="num">Största nedgång</th></tr></thead><tbody>
{% for r in rader %}<tr>
<td style="min-width:200px"><a href="{{ url_for('backtest_strategi', kor=bt.namn, sid=r.id) }}"><b><span style="color:{{ r.farg }}">●</span> {{ r.namn }}</b></a>
<div class="muted small">{{ r.kort }}</div></td>
<td class="num">{{ r.varde|kr }}</td><td class="num {{ r.avkastning|klass }}">{{ r.avkastning|pct }}</td>
<td class="num {{ r.mot_index|klass }}">{{ r.mot_index|pct }}</td><td class="num {{ r.mot_slump|klass }}">{{ r.mot_slump|pct if r.id != 'slump' else '–' }}</td>
<td class="num">{{ r.affarer }}</td><td class="num">{{ r.stat.traffsakerhet|pct(false) if r.stat.antal else '–' }}</td>
<td class="num">{{ r.courtage|kr }}</td><td class="num neg">{{ r.nedgang|pct }}</td>
</tr>{% endfor %}
<tr><td><b><span style="color:var(--muted)">●</span> {{ indexnamn }}</b><div class="muted small">Prisindex, utan utdelningar</div></td>
<td class="num">–</td><td class="num {{ index_avk|klass }}">{{ index_avk|pct }}</td><td colspan="6"></td></tr>
</tbody></table></div>
<p class="small muted">Största nedgång = största fall från en tidigare topp under perioden – hur ont det hade gjort på vägen.</p>
{% endif %}
<div class="card" style="margin-top:16px"><h2>Köra ett backtest</h2>
<p class="small muted" style="margin-top:0">På servern (första gången hämtas 5 års kurser, några minuter):</p>
<pre class="small" style="white-space:pre-wrap;margin:0">cd ~/screener
./venv/bin/python backtest.py               # senaste 6 månaderna
./venv/bin/python backtest.py --manader 12  # senaste året
./venv/bin/python backtest.py --manader 36  # senaste 3 åren (ca 10 minuter)</pre></div>
"""


def _nedgang(serie: pd.Series) -> float | None:
    v = serie.dropna()
    return float((v / v.cummax() - 1).min()) if len(v) else None


@app.route("/backtest")
@app.route("/backtest/<kor>")
def backtest_sida(kor: str | None = None):
    korningar = backtest_korningar()
    if not korningar:
        return sida("Backtest", "strategier", BACKTEST_MALL, korningar=[], bt=None)
    vald = next((b for b in korningar if b["namn"] == kor), None) or next(
        (b for b in korningar if b["manader"] == 6 and not b.get("demo")), korningar[0])
    bas = BACKTEST_MAPP / vald["namn"]
    k = bt_kurser(vald["namn"])
    rader = [r for r in st.oversikt(bas, k) if r["startad"] and len(r["serie"])]
    indexnamn = ih.INDEXNAMN.get(ih.JAMFORELSEINDEX, ih.JAMFORELSEINDEX)
    graf, index_avk = "", None
    if rader:
        fran = min(r["serie"].index[0] for r in rader)
        idx = _indexserie(k, fran)
        index_avk = float(idx.iloc[-1]) if idx is not None else None
        slump = next((r["avkastning"] for r in rader if r["id"] == "slump"), None)
        for r in rader:
            r["farg"] = STRAT_FARG.get(r["id"], "var(--accent)")
            r["mot_index"] = r["avkastning"] - index_avk if index_avk is not None else None
            r["mot_slump"] = r["avkastning"] - slump if slump is not None else None
            r["nedgang"] = _nedgang(r["serie"]["varde"])
        rader.sort(key=lambda r: -r["avkastning"])
        graf = dubbel(graf_flera, [(indexnamn, idx, "var(--muted)", 1.5)]
                      + [(r["namn"], r["serie"]["avkastning"], r["farg"], 2.2) for r in rader])
    vald = {**vald, "hoppade": [st.STRATEGIER[s]["namn"] for s in vald.get("hoppade_over", []) if s in st.STRATEGIER]}
    return sida("Backtest", "strategier", BACKTEST_MALL, korningar=korningar, bt=vald, rader=rader, graf=graf,
                index_avk=index_avk, indexnamn=indexnamn)


@app.route("/backtest/<kor>/<sid>")
def backtest_strategi(kor: str, sid: str):
    korningar = {b["namn"]: b for b in backtest_korningar()}
    if kor not in korningar or sid not in st.STRATEGIER:
        abort(404)
    bas = BACKTEST_MAPP / kor
    if st.las_tillstand(st.mapp(bas, sid)) is None:
        abort(404)
    tab = ih.senaste_screener(bas)
    return _strategi_vy(bas, bt_kurser(kor), sid, tab, None, bt=korningar[kor])


IMPORT_MALL = """
<p class="small"><a href="{{ url_for('innehav_sida') }}">← Mina innehav</a></p>
<h1>Importera från Avanza</h1>
<div class="grid">
<div class="card">
<h2>1. Exportera från Avanza</h2>
<ol class="small" style="margin:0;padding-left:18px">
<li>Logga in på avanza.se och gå till <b>Transaktioner</b> (under Min ekonomi).</li>
<li>Välj <b>hela perioden</b>, från ditt första köp till idag, och alla konton.</li>
<li>Klicka <b>Exportera transaktioner</b>. Du får en .csv-fil.</li>
</ol>
<p class="small muted">Ta alltid med hela historiken. Saknas gamla köp kan försäljningar inte matchas.</p>
</div>
<div class="card">
<h2>2. Ladda upp filen</h2>
<form method="post" enctype="multipart/form-data">
<input type="file" name="fil" accept=".csv,text/csv" required style="width:100%;margin-bottom:12px">
<button type="submit">Läs in och granska</button>
</form>
<p class="small muted">Inget sparas förrän du har granskat och bekräftat. {{ antal_kopplingar }} sparade kopplingar ISIN → ticker återanvänds.</p>
</div>
</div>
"""

GRANSKA_MALL = """
<p class="small"><a href="{{ url_for('import_sida') }}">← Ladda upp en annan fil</a></p>
<h1>Granska importen</h1>
<p class="muted">{{ antal_trans }} köp och försäljningar i {{ instr|length }} värdepapper, från {{ forsta }} till {{ senaste }}.
{% if ignorerade %}Hoppar över: {% for k, n in ignorerade.items() %}{{ k }} ({{ n }}){{ ', ' if not loop.last }}{% endfor %}.{% endif %}</p>

<form method="post" action="{{ url_for('import_bekrafta') }}">
<div class="card" style="margin-bottom:16px">
<h2>Konton</h2>
{% for k in konton %}<label style="flex-direction:row;display:inline-flex;align-items:center;gap:6px;margin-right:18px;color:var(--text)">
<input type="checkbox" name="konto" value="{{ k }}" {{ 'checked' if k in valda_konton }}> {{ k }}</label>{% endfor %}
<p class="small muted" style="margin-bottom:0">Bocka bara i de konton som ska räknas, till exempel ditt ISK.</p>
</div>

<div class="wrap"><table style="min-width:900px">
<thead><tr><th>Värdepapper</th><th class="num">Köp</th><th class="num">Sälj</th><th class="num">Äger nu</th>
<th>Yahoo-ticker</th><th>Typ</th><th>Källa</th></tr></thead><tbody>
{% for r in instr %}
<tr{% if r.kalla == 'hittades inte' and r.typ != 'hoppa över' %} style="background:var(--b-bevaka)"{% endif %}>
<td><b>{{ r.namn }}</b><div class="muted small">{{ r.isin or '–' }} · {{ r.valuta }}</div></td>
<td class="num">{{ r.kop }}</td><td class="num">{{ r.salj }}</td>
<td class="num">{% if r.netto > 0 %}<b>{{ r.netto|tal(0 if r.netto == r.netto|round else 4) }}</b>{% else %}<span class="muted">0</span>{% endif %}</td>
<td><input type="hidden" name="nyckel_{{ loop.index0 }}" value="{{ r.nyckel }}">
<input name="ticker_{{ loop.index0 }}" value="{{ r.ticker }}" placeholder="t.ex. VOLV-B.ST" style="width:140px"></td>
<td><select name="typ_{{ loop.index0 }}">{% for t in typer %}<option {{ 'selected' if t == r.typ }}>{{ t }}</option>{% endfor %}</select></td>
<td class="small muted">{{ r.kalla }}</td>
</tr>{% endfor %}
</tbody></table></div>
<input type="hidden" name="antal" value="{{ instr|length }}">

<div class="card" style="margin-top:16px">
<p class="small" style="margin-top:0">Gula rader saknar ticker. Fyll i den (sök på finance.yahoo.com, svenska aktier slutar på <code>.ST</code>)
eller välj <b>hoppa över</b>, till exempel för fonder. Värdepapper utan ticker importeras ändå till historiken men får ingen kursdata.</p>
<p class="small"><b>Importen ersätter</b> dina nuvarande {{ n_innehav }} innehav och {{ n_affarer }} avslutade affärer.
En säkerhetskopia sparas först. Dina val av ticker och typ sparas till nästa import.</p>
<button type="submit">Importera</button>
</div>
</form>
"""


BACKUP_MAPP = UTMAPP / "backup"
BEHALL_BACKUPER = 10  # antal säkerhetskopior per fil som sparas


def sakerhetskopiera() -> None:
    """Kopierar innehav.csv och affarer.csv till backup/ och behåller bara de senaste."""
    BACKUP_MAPP.mkdir(exist_ok=True)
    stampel = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    for fil in (INNEHAV_FIL, AFFAR_FIL):
        if not fil.exists():
            continue
        (BACKUP_MAPP / f"{fil.stem}_fore_import_{stampel}.csv").write_bytes(fil.read_bytes())
        gamla = sorted(BACKUP_MAPP.glob(f"{fil.stem}_fore_import_*.csv"))[:-BEHALL_BACKUPER]
        for g in gamla:
            g.unlink(missing_ok=True)


def _import_installningar() -> dict:
    try:
        return json.loads(IMPORT_INST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@app.route("/import", methods=["GET", "POST"])
def import_sida():
    if request.method == "POST":
        fil = request.files.get("fil")
        data = fil.read() if fil else b""
        if not data:
            flash("Välj en fil att ladda upp.", "fel")
            return redirect(url_for("import_sida"))
        try:
            df = ai.las_export(data)
        except Exception as e:
            flash(f"Kunde inte läsa filen: {e}", "fel")
            return redirect(url_for("import_sida"))
        if df.empty:
            flash("Filen innehåller inga transaktioner.", "fel")
            return redirect(url_for("import_sida"))
        IMPORT_FIL.write_bytes(data)
        return redirect(url_for("import_granska"))
    return sida("Importera", "innehav", IMPORT_MALL, antal_kopplingar=len(ai.las_karta(KARTA_FIL)))


@app.route("/import/granska")
def import_granska():
    if not IMPORT_FIL.exists():
        return redirect(url_for("import_sida"))
    df = ai.las_export(IMPORT_FIL.read_bytes())
    t, ignorerade = ai.transaktioner(df)
    if t.empty:
        flash("Hittade inga köp eller försäljningar i filen.", "fel")
        return redirect(url_for("import_sida"))
    instr = ai.instrument(t)
    karta = ai.las_karta(KARTA_FIL)
    tickerlista = ai.isin_fran_tickerlista(sc.MAPP / "tickers_se.csv")

    # Slå upp okända ISIN hos Yahoo parallellt (bara första gången – sedan sparas de)
    if not DEMO:
        okanda = [(r["isin"], r["valuta"]) for r in instr.to_dict("records")
                  if r["isin"] and r["isin"] not in karta and r["isin"] not in tickerlista]
        if okanda:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=6) as ex:
                list(ex.map(lambda a: ai.sok_yahoo(*a), okanda))
    forslag = ai.foresla(instr, karta, tickerlista, ih.las_innehav(INNEHAV_FIL), sok=not DEMO)

    konton = sorted(k for k in df["konto"].unique() if k)
    sparade = _import_installningar().get("konton")
    valda = [k for k in konton if k in sparade] if sparade else konton
    return sida("Granska import", "innehav", GRANSKA_MALL, instr=forslag, typer=ai.IMPORT_TYPER,
                antal_trans=len(t), ignorerade=ignorerade, konton=konton, valda_konton=valda or konton,
                forsta=t["datum"].min().date(), senaste=t["datum"].max().date(),
                n_innehav=len(ih.las_innehav(INNEHAV_FIL)), n_affarer=len(ih.las_affarer(AFFAR_FIL)))


@app.route("/import/bekrafta", methods=["POST"])
def import_bekrafta():
    if not IMPORT_FIL.exists():
        return redirect(url_for("import_sida"))
    f = request.form
    konton = f.getlist("konto")
    if not konton:
        flash("Välj minst ett konto.", "fel")
        return redirect(url_for("import_granska"))

    df = ai.las_export(IMPORT_FIL.read_bytes())
    t, _ = ai.transaktioner(df, konton)
    alla, _ = ai.transaktioner(df)
    namn_per_nyckel = dict(zip(alla["nyckel"], alla["namn"]))
    val = {}
    for i in range(int(f.get("antal", 0))):
        nyckel = f.get(f"nyckel_{i}", "")
        if not nyckel:
            continue
        ticker = f.get(f"ticker_{i}", "").strip().upper()
        if ticker and not re.fullmatch(r"[A-Z0-9.\-=^]{1,20}", ticker):
            flash(f"Ogiltig ticker: {ticker}", "fel")
            return redirect(url_for("import_granska"))
        typ = f.get(f"typ_{i}", "kärna")
        val[nyckel] = {"ticker": ticker, "typ": typ if typ in ai.IMPORT_TYPER else "kärna"}

    inn, aff, varningar = ai.bygg(t, val)

    # Säkerhetskopia, sedan skriv
    sakerhetskopiera()
    ih.skriv_affarer(AFFAR_FIL, aff)
    ih.skriv_innehav(INNEHAV_FIL, inn)

    karta = ai.las_karta(KARTA_FIL)
    for nyckel, v in val.items():
        karta[nyckel] = {"ticker": v["ticker"], "typ": v["typ"], "namn": namn_per_nyckel.get(nyckel, "")}
    ai.skriv_karta(KARTA_FIL, karta)
    IMPORT_INST.write_text(json.dumps({"konton": konton}, ensure_ascii=False), encoding="utf-8")

    # Hämta kurser för allt som importerades
    valutor = list(inn["valuta"]) + list(aff["valuta"])
    behov = list(dict.fromkeys(
        [x for x in list(inn["ticker"]) + list(aff["ticker"]) if re.fullmatch(r"[A-Z0-9.\-=^]{1,20}", x)
         and not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", x)]
        + [x for x in (ih.fx_ticker(v) for v in valutor) if x] + [ih.JAMFORELSEINDEX]))
    saknas = []
    try:
        saknas = uppdatera_kurser(behov) if behov else []
    except Exception as e:
        varningar.append(f"Kunde inte hämta kurser just nu ({e}). Klicka Uppdatera kurser senare.")
    IMPORT_FIL.unlink(missing_ok=True)

    flash(f"Importerade {len(inn)} köp som du fortfarande äger och {len(aff)} avslutade affärer. "
          f"Tidigare innehav sparades i backup/.", "ok")
    if saknas:
        flash("Ingen kursdata för: " + ", ".join(saknas) + ". Kontrollera tickern via en ny import.", "fel")
    for v in varningar:
        flash(v, "fel")
    return redirect(url_for("innehav_sida"))


# ---------------------------------------------------------------------------
# Aktiesida med graf
# ---------------------------------------------------------------------------
def graf_svg(s: pd.Series, kopdatum=None, kopkurs=None, stopp=None, B: int = 860) -> str:
    """Kursgraf som inbäddad SVG: kurs, MA50, MA200, köpkurs och stoppnivå."""
    ma50 = s.rolling(50).mean()
    ma200 = s.rolling(200).mean()
    start = s.index[-1] - pd.Timedelta(days=365)
    if kopdatum is not None and pd.notna(kopdatum):
        start = min(start, kopdatum - pd.Timedelta(days=60))
    s, ma50, ma200 = s[s.index >= start], ma50[ma50.index >= start], ma200[ma200.index >= start]
    if len(s) < 2:
        return "<p class='muted'>För lite data för en graf.</p>"

    H, VM, HM, TM, BM = (320, 56, 16, 12, 28) if B >= 600 else (300, 44, 10, 12, 28)
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
    manader = pd.date_range(s.index[0], s.index[-1], freq="MS")
    for t in manader[::max(1, math.ceil(len(manader) / (12 if B >= 600 else 5)))]:
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
            '<span style="color:var(--gul)">━</span> MA50 <span style="color:var(--orange)">━</span> MA200'
            + (' <span style="color:var(--pos)">┅</span> köpkurs' if kopkurs is not None and pd.notna(kopkurs) else '')
            + (' <span style="color:var(--neg)">┅</span> stopp' if stopp is not None and pd.notna(stopp) else '')
            + '</p>')


def dubbel(funk, *args, **kw) -> str:
    """Ritar grafen två gånger: bred (dator) och smal (mobil). CSS visar rätt version."""
    return (f'<div class="graf-stor">{funk(*args, **kw)}</div>'
            f'<div class="graf-liten">{funk(*args, B=420, **kw)}</div>')


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


# ---------------------------------------------------------------------------
# Nyckeltalsvy (fliken "Nyckeltal" på aktiesidan och i sökningen)
# ---------------------------------------------------------------------------
_nk_cache: dict = {"nyckel": None, "hist": None, "senaste": None}


def nk_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(all historik, senaste dagen med poäng). Läses om bara när en dagsfil tillkommit eller ändrats."""
    mapp = UTMAPP / "nyckeltal"
    filer = sorted(mapp.glob("????-??-??.csv")) if mapp.exists() else []
    nyckel = tuple((f.name, f.stat().st_mtime) for f in filer)
    if nyckel != _nk_cache["nyckel"]:
        hist = nk.las_historik(UTMAPP) if filer else pd.DataFrame()
        senaste = pd.DataFrame()
        if len(hist) and "fx_rapport" in hist.columns:
            senaste = nk.poang(hist[hist["datum"] == hist["datum"].max()])
        _nk_cache.update(nyckel=nyckel, hist=hist, senaste=senaste)
    return _nk_cache["hist"], _nk_cache["senaste"]


def _f(x, fmt="{:.1f}", efter=""):
    try:
        if x is None or pd.isna(x):
            return "–"
        return fmt.format(float(x)) + efter
    except (TypeError, ValueError):
        return "–"


def skala(x, faktor):
    """x × faktor, eller None om x saknas (NaN, None, text)."""
    try:
        return None if x is None or pd.isna(x) else float(x) * faktor
    except (TypeError, ValueError):
        return None


def _sparklinje(s: pd.Series) -> str:
    s = s.dropna()
    if len(s) < 5:
        return ""
    lo, hi = float(s.min()), float(s.max())
    if hi == lo:
        hi = lo + 1
    p = [(i / (len(s) - 1) * 118 + 1, 26 - (v - lo) / (hi - lo) * 24) for i, v in enumerate(s)]
    return (f'<svg class="spark" viewBox="0 0 120 28"><path d="M{" L".join(f"{a:.1f},{b:.1f}" for a, b in p)}" '
            f'fill="none" stroke="var(--accent)" stroke-width="1.5"/></svg>')


NK_MALL = """
{% if not rad %}
<div class="card"><p>Inga nyckeltal för {{ ticker|kort }} ännu.</p>
<p class="small muted">Nyckeltal samlas varje vardag kl. 18:30 för alla aktier i tickerlistan och dina innehav
(<code>nyckeltal.py</code>). {% if n_dagar %}Senaste insamlingen: {{ senaste_datum }}.{% else %}Ingen insamling har körts än.{% endif %}</p></div>
{% else %}
<p class="small muted" style="margin-top:0">Från {{ rad.datum|datum }} · {{ n_dagar }} dagar sparade sedan {{ forsta_datum }} ·
<b>Under utvärdering</b> – påverkar inte köp- eller säljreglerna.</p>

<div class="stats">
<div class="stat"><span class="muted small">Värdering</span>
<div class="poangbox"><b>{{ _f(rad.varde_poang, "{:.0f}") }}</b><span class="muted">/ 100</span></div>
<div class="stapel"><div style="width:{{ _f(rad.varde_poang, "{:.0f}")|replace("–", "0") }}%"></div></div>
<span class="small muted">billigare än {{ _f(rad.varde_poang, "{:.0f}") }} % av {{ grupp }}</span></div>
<div class="stat"><span class="muted small">Kvalitet</span>
<div class="poangbox"><b>{{ _f(rad.kvalitet_poang, "{:.0f}") }}</b><span class="muted">/ 100</span></div>
<div class="stapel"><div style="width:{{ _f(rad.kvalitet_poang, "{:.0f}")|replace("–", "0") }}%"></div></div>
<span class="small muted">lönsammare än {{ _f(rad.kvalitet_poang, "{:.0f}") }} % av {{ grupp }}</span></div>
<div class="stat"><span class="muted small">Rapport</span><b style="font-size:18px">{{ rapport_text }}</b>
<span class="small muted">{{ rapport_under }}</span></div>
<div class="stat"><span class="muted small">Analytiker</span><b style="font-size:18px">{{ _f(rad.antal_analytiker, "{:.0f}", " st") }}</b>
<span class="small muted">{% if uppsida is not none %}riktkurs {{ _f(rad.riktkurs, "{:.0f}") }} ({{ _f(x100(uppsida), "{:+.0f}", " %") }}){% else %}ingen riktkurs{% endif %}</span></div>
</div>

{% for rubrik, rader, forklaring in sektioner %}
<h2 style="margin-top:20px">{{ rubrik }}</h2>
<div class="wrap"><table class="nk">
<thead><tr><th>Mått</th><th class="num">{{ ticker|kort }}</th><th class="num">Median {{ grupp }}</th><th>Läge i gruppen</th><th>Senaste dagarna</th></tr></thead>
<tbody>{% for namn, varde, median, pct, ord, spark in rader %}
<tr><td><b>{{ namn }}</b></td><td class="num">{{ varde }}</td><td class="num muted">{{ median }}</td>
<td>{% if pct is not none %}<div style="display:flex;gap:8px;align-items:center"><div class="stapel" style="flex:1"><div style="width:{{ pct }}%"></div></div>
<span class="small muted" style="white-space:nowrap">{{ ord }} än {{ pct|round|int }} %</span></div>{% endif %}</td>
<td>{{ spark|safe }}</td></tr>{% endfor %}</tbody></table></div>
{% if forklaring %}<p class="small muted">{{ forklaring }}</p>{% endif %}
{% endfor %}

<div class="grid">
<div class="card"><h2>Analytiker</h2><dl class="nyckel">
<dt>Vinst per aktie, senaste 12 mån</dt><dd>{{ _f(rad.vinst_per_aktie, "{:.2f}") }}</dd>
<dt>Vinst per aktie, prognos</dt><dd>{{ _f(rad.vinst_per_aktie_prognos, "{:.2f}") }}</dd>
<dt>Förväntad vinstförändring</dt><dd class="{{ vinstforandring|klass }}">{{ _f(x100(vinstforandring), "{:+.0f}", " %") }}</dd>
<dt>Prognosändring sedan {{ revision_fran or "–" }}</dt><dd class="{{ revision|klass }}">{{ _f(x100(revision), "{:+.1f}", " %") }}</dd>
<dt>Rekommendation</dt><dd>{{ rek_text }}</dd>
</dl><p class="small muted">Prognosändringen (revideringen) blir meningsfull när några veckors historik finns. Stigande prognoser är en av de starkaste kända signalerna.</p></div>
<div class="card"><h2>Ägande och skuld</h2><dl class="nyckel">
<dt>Insiders</dt><dd>{{ _f(x100(rad.andel_insiders), "{:.0f}", " %") }}</dd>
<dt>Institutioner</dt><dd>{{ _f(x100(rad.andel_institutioner), "{:.0f}", " %") }}</dd>
<dt>Skuld / eget kapital</dt><dd>{{ _f(rad.skuld_ek, "{:.0f}", " %") }}</dd>
<dt>Direktavkastning</dt><dd>{{ _f(rad.direktavkastning, "{:.1f}", " %") }}</dd>
<dt>Börsvärde</dt><dd>{{ _f(skala(rad.borsvarde, 1e-9), "{:.1f}", " mdr kr") }}</dd>
{% if rad.rapportvaluta and rad.rapportvaluta != rad.valuta %}<dt>Rapportvaluta</dt><dd>{{ rad.rapportvaluta }} (omräknat)</dd>{% endif %}
</dl></div>
</div>
<p class="small muted">Jämförelsegrupp: {{ grupp }} ({{ grupp_n }} bolag). {{ metod }}</p>
{% endif %}
"""


def nyckeltal_vy(ticker: str) -> str:
    """Nyckeltalsfliken. Ett fel här får aldrig fälla resten av aktiesidan."""
    try:
        return _nyckeltal_vy(ticker)
    except Exception:
        import traceback
        app.logger.error("Nyckeltalsvyn för %s misslyckades:\n%s", ticker, traceback.format_exc())
        return ('<div class="flash fel">Nyckeltalen kunde inte visas just nu. '
                'Detaljer finns i <code>journalctl -u screener-web</code>.</div>')


def _nyckeltal_vy(ticker: str) -> str:
    hist, senaste = nk_data()
    kw = dict(ticker=ticker, rad=None, n_dagar=hist["datum"].nunique() if len(hist) else 0, _f=_f,
              x100=lambda x: skala(x, 100), skala=skala,
              senaste_datum=hist["datum"].max().date() if len(hist) else None)
    if len(senaste) == 0 or ticker not in set(senaste["ticker"]):
        return render_template_string(NK_MALL, **kw)
    rad = senaste[senaste["ticker"] == ticker].iloc[0].to_dict()
    for k in nk.KOLUMNER:  # äldre dagsfiler saknar fält som lagts till senare – visa dem som "–"
        rad.setdefault(k, None)
    grupp = rad["grupp"] if isinstance(rad["grupp"], str) else (rad["sektor"] or "marknaden")
    jmf = senaste[senaste["grupp"] == rad["grupp"]] if isinstance(rad["grupp"], str) else senaste
    egen = hist[hist["ticker"] == ticker].sort_values("datum")

    def med(k):
        return jmf[k].median() if k in jmf else np.nan

    def r(namn, k, fmt, pct_k=None, ord="billigare", efter=""):
        pct = rad.get(pct_k) if pct_k else None
        pct = None if pct is None or pd.isna(pct) else float(pct)
        return (namn, _f(rad.get(k), fmt, efter), _f(med(k), fmt, efter), pct, ord,
                _sparklinje(egen[k]) if k in egen else "")

    if rad["finans"]:
        vard = [r("P/B", "pb_k", "{:.2f}", "pct_b_p"), r("P/E", "pe", "{:.1f}", "pct_e_p")]
        kval = [r("ROE", "roe", "{:.1%}", "pct_roe", "bättre"),
                r("Rörelsemarginal", "rorelsemarginal", "{:.1%}")]
        metod = "Banker, finans och fastighet värderas med eget kapital mot kurs (P/B), eftersom EV-mått blir missvisande för dem."
    else:
        vard = [r("EV/EBIT", "ev_ebit_k", "{:.1f}", "pct_ebit_ev"), r("EV/Sales", "ev_sales_k", "{:.2f}", "pct_sales_ev"),
                r("P/E", "pe", "{:.1f}", "pct_e_p"), r("EV/EBITDA", "ev_ebitda_k", "{:.1f}"), r("P/B", "pb_k", "{:.2f}")]
        kval = [r("ROA", "roa", "{:.1%}", "pct_roa", "bättre"),
                r("Bruttomarginal", "bruttomarginal", "{:.1%}", "pct_bruttomarginal", "bättre"),
                r("Rörelsemarginal", "rorelsemarginal", "{:.1%}", "pct_rorelsemarginal", "bättre"),
                r("ROE", "roe", "{:.1%}")]
        metod = ("Värderingspoängen är snittet av percentilen för EBIT/EV, försäljning/EV och vinst/börsvärde; "
                 "kvalitetspoängen av ROA, brutto- och rörelsemarginal. Negativt resultat ger låg poäng i stället för att räknas bort.")
    tillv = [r("Omsättningstillväxt", "omsattningstillvaxt", "{:+.1%}"), r("Vinsttillväxt", "vinsttillvaxt", "{:+.1%}")]

    # Rapportdatum
    rapport_text, rapport_under = "–", ""
    idag = pd.Timestamp.today().normalize()
    nasta = pd.to_datetime(rad.get("nasta_rapport"), errors="coerce")
    senast = pd.to_datetime(rad.get("senaste_kvartal"), errors="coerce")
    if pd.notna(nasta) and nasta >= idag:
        dagar = (nasta - idag).days
        rapport_text = nasta.strftime("%Y-%m-%d")
        rapport_under = "idag" if dagar == 0 else f"om {dagar} dagar" + (" – räkna med större svängningar" if dagar <= 7 else "")
    elif pd.notna(senast):
        rapport_text, rapport_under = senast.strftime("%Y-%m-%d"), "senaste rapporterade kvartal"

    def tal(x):
        return None if x is None or pd.isna(x) else float(x)

    eps, eps_p, kurs, rikt = (tal(rad.get(k)) for k in ("vinst_per_aktie", "vinst_per_aktie_prognos", "kurs", "riktkurs"))
    revision = revision_fran = None
    if "vinst_per_aktie_prognos" in egen:
        e = egen.dropna(subset=["vinst_per_aktie_prognos"])
        e = e[e["datum"] >= e["datum"].max() - pd.Timedelta(days=90)] if len(e) else e
        if len(e) >= 2 and e["vinst_per_aktie_prognos"].iloc[0]:
            revision = e["vinst_per_aktie_prognos"].iloc[-1] / abs(e["vinst_per_aktie_prognos"].iloc[0]) - 1
            revision_fran = e["datum"].iloc[0].date()
    rek = tal(rad.get("rekommendation"))
    rek_text = "–" if rek is None else f"{rek:.1f} – " + ("köp" if rek < 2.5 else "behåll" if rek < 3.5 else "sälj")

    kw.update(rad=rad, grupp=grupp, grupp_n=int(rad["grupp_n"]) if pd.notna(rad.get("grupp_n")) else len(jmf),
              forsta_datum=egen["datum"].min().date() if len(egen) else None, metod=metod,
              sektioner=[("Värdering", vard, "Lägre multipel = billigare. Stapeln visar hur billig aktien är jämfört med gruppen."),
                         ("Lönsamhet och kvalitet", kval, "Skydd mot värdefällor: billigt och lönsamt är bättre än billigt och olönsamt."),
                         ("Tillväxt", tillv, "")],
              rapport_text=rapport_text, rapport_under=rapport_under,
              uppsida=(rikt / kurs - 1) if rikt and kurs else None,
              vinstforandring=(eps_p / abs(eps) - 1) if eps and eps_p is not None else None,
              revision=revision, revision_fran=revision_fran, rek_text=rek_text)
    return render_template_string(NK_MALL, **kw)


AKTIE_MALL = """
<p class="small"><a href="{{ url_for('innehav_sida') }}">← Mina innehav</a> · <a href="{{ url_for('rapport') }}">Veckans rapport</a></p>
<h1>{{ ticker|kort }} <span class="muted" style="font-weight:400">{{ namn }}</span></h1>
<p class="muted">Senaste kurs {{ sista|tal }} ({{ datum }})</p>
<div>
<div class="flikar"><button data-vy="oversikt" class="aktiv">Översikt</button><button data-vy="nyckeltal">Nyckeltal</button></div>
<div class="vy" data-vy="nyckeltal" hidden>{{ nkvy|safe }}</div>
<div class="vy" data-vy="oversikt">
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
</div></div>
"""


@app.route("/aktie/<ticker>")
def aktie(ticker: str):
    ticker = ticker.upper()
    k = kurser()
    if ticker not in k.columns or k[ticker].dropna().empty:
        return redirect(url_for("sok_sida", q=ticker))
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
    graf = dubbel(graf_svg, s, forsta["kopdatum"] if forsta else None, forsta["kopkurs"] if forsta else None,
                    forsta["stopp"] if forsta and forsta["typ"] == "momentum" else None)
    return sida(ticker, "innehav", AKTIE_MALL, ticker=ticker, namn=namn_for(ticker), sista=s.iloc[-1],
                datum=s.index[-1].date(), graf=graf, poster=poster, m=m, s=srad, n_godk=n_godk,
                nkvy=nyckeltal_vy(ticker))


# ---------------------------------------------------------------------------
# Sök: kör köpreglerna på valfri aktie
# ---------------------------------------------------------------------------
SOK_HISTORIK = UTMAPP / ".sok_historik.json"
_tickerlista_cache: dict = {"mtime": None, "df": None}


def tickerlista() -> pd.DataFrame:
    fil = sc.MAPP / "tickers_se.csv"
    if not fil.exists():
        return pd.DataFrame(columns=["ticker", "namn"])
    m = fil.stat().st_mtime
    if _tickerlista_cache["mtime"] != m:
        _tickerlista_cache["df"] = sc.las_tickers(fil)
        _tickerlista_cache["mtime"] = m
    return _tickerlista_cache["df"]


def tolka_sokning(q: str) -> tuple[str | None, list[tuple[str, str]]]:
    """Gör om det du skrev till en Yahoo-ticker. Returnerar (ticker, förslag vid flera träffar)."""
    q = q.strip()
    if not q:
        return None, []
    lista = tickerlista()
    stor = q.upper()
    norm = re.sub(r"\s+", "-", stor)
    tickers = set(lista["ticker"])
    for kand in (norm, norm + ".ST", norm.replace(".ST", "") + ".ST"):
        if kand in tickers:
            return kand, []
    # Namnsökning i den svenska listan
    if len(q) >= 2 and len(lista):
        t = lista[lista["namn"].str.upper().str.contains(re.escape(stor), na=False)
                  | lista["ticker"].str.upper().str.startswith(norm)]
        if len(t) == 1:
            return t.iloc[0]["ticker"], []
        if len(t) > 1:
            return None, list(zip(t["ticker"], t["namn"]))[:15]
    # Annars: ta tickern som den är (t.ex. amerikanska aktier som ADCT eller ^OMX)
    if re.fullmatch(r"[A-Z0-9.\-=^]{1,20}", norm):
        return norm, []
    return None, []


def spara_sokning(ticker: str, beslut: str) -> None:
    try:
        hist = json.loads(SOK_HISTORIK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        hist = []
    hist = [h for h in hist if h.get("ticker") != ticker]
    hist.insert(0, {"ticker": ticker, "beslut": beslut, "tid": dt.datetime.now().strftime("%Y-%m-%d %H:%M")})
    try:
        SOK_HISTORIK.write_text(json.dumps(hist[:12], ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def las_sokhistorik() -> list[dict]:
    try:
        return json.loads(SOK_HISTORIK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


SOK_MALL = """
<h1>Sök</h1>
<p class="muted">Hämtar färska kurser och kör samma köpregler som screenern. Platsen räknas mot senaste veckorapporten
{%- if rapportdatum %} ({{ rapportdatum }}){% endif %}.</p>

<form method="get" class="card" style="display:flex;gap:10px;flex-wrap:wrap;align-items:end;margin-bottom:16px">
<label style="flex:1;min-width:220px">Ticker eller bolagsnamn
<input name="q" value="{{ q }}" list="tickers" autofocus placeholder="t.ex. Volvo, VOLV-B.ST, ADCT"></label>
<datalist id="tickers">{% for t, n in alla %}<option value="{{ t }}">{{ n }}</option>{% endfor %}</datalist>
<button type="submit">Analysera</button>
</form>

{% if forslag %}
<div class="card"><h2>Flera träffar – välj en</h2>
<ul>{% for t, n in forslag %}<li><a href="{{ url_for('sok_sida', q=t) }}"><b>{{ t|kort }}</b></a> <span class="muted">{{ n }}</span></li>{% endfor %}</ul></div>
{% endif %}

{% if fel %}<div class="flash fel">{{ fel }}</div>{% endif %}

{% if r %}
<div class="card" style="margin-bottom:16px;display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap">
<div><h2 style="margin:0;font-size:22px">{{ r.ticker|kort }} <span class="muted" style="font-weight:400">{{ r.namn }}</span></h2>
<div class="muted small">Kurs {{ r.n.kurs|tal }} {{ r.valuta }} · {{ r.n.datum }}{% if r.agd %} · <a href="{{ url_for('aktie', ticker=r.ticker) }}">du äger den – se säljreglerna</a>{% endif %}</div></div>
<div style="text-align:right"><span class="badge {{ r.klass }}" style="font-size:16px;padding:6px 14px">{{ r.b.beslut }}</span>
<div class="small muted" style="margin-top:6px">{{ r.forklaring }}</div></div>
</div>

<div>
<div class="flikar"><button data-vy="oversikt" class="aktiv">Översikt</button><button data-vy="nyckeltal">Nyckeltal</button></div>
<div class="vy" data-vy="nyckeltal" hidden>{{ r.nkvy|safe }}</div>
<div class="vy" data-vy="oversikt">
<div class="card">{{ r.graf|safe }}</div>

<div class="grid">
<div class="card"><h2>Köpreglerna</h2>
<ul class="regler">{% for st, regel, txt in r.b.regler %}
<li><span class="prick p-{{ st }}"></span><div><b>{{ regel }}</b><div class="small muted">{{ txt }}</div></div></li>{% endfor %}
</ul></div>
<div class="card"><h2>Nyckeltal</h2>
<dl class="nyckel">
<dt>12 mån exkl. senaste</dt><dd class="{{ r.n.mom_12_1|klass }}">{{ r.n.mom_12_1|pct }}</dd>
<dt>6 mån</dt><dd class="{{ r.n.mom_6m|klass }}">{{ r.n.mom_6m|pct }}</dd>
<dt>3 mån</dt><dd class="{{ r.n.mom_3m|klass }}">{{ r.n.mom_3m|pct }}</dd>
<dt>1 mån</dt><dd class="{{ r.n.mom_1m|klass }}">{{ r.n.mom_1m|pct }}</dd>
<dt>Från 52v-högsta</dt><dd class="{{ r.n.fran_52v_hogsta|klass }}">{{ r.n.fran_52v_hogsta|pct }}</dd>
<dt>Volatilitet (3 mån, årlig)</dt><dd>{{ r.n.volatilitet|pct(false) }}</dd>
<dt>Omsättning/dag</dt><dd>{{ (r.n.omsattning_sek / 1e6)|tal(1) }} MSEK</dd>
{% if r.veckoplats %}<dt>Plats i veckorapporten</dt><dd>{{ r.veckoplats }}</dd>{% endif %}
</dl></div>
<div class="card"><h2>Om du köper idag</h2>
<dl class="nyckel">
<dt>Släpande stopp startar på</dt><dd>{{ r.stopp|tal }} {{ r.valuta }}</dd>
<dt>Avstånd till MA200</dt><dd>{{ r.n.avstand_ma200|pct }}</dd>
<dt>Sälj vid månadsgenomgång om</dt><dd>plats > {{ salj_rank }}</dd>
</dl>
<p class="small muted">Stoppen flyttas upp när kursen stiger, {{ (stopp_pct * 100)|round|int }} % under högsta stängning sedan köp.</p>
{% if not r.agd %}<a href="{{ url_for('innehav_sida', ticker=r.ticker) }}#lagg-till" class="small">Lägg till som innehav →</a>{% endif %}
</div>
</div>
</div></div>
<p class="small muted">Kontrollera alltid nyheter och kommande rapportdatum innan köp. Regler tillämpade på kursdata, inte en köprekommendation.</p>
{% endif %}

{% if historik and not r %}
<div class="card" style="margin-top:16px"><h2>Senaste sökningar</h2>
<table><tbody>{% for h in historik %}
<tr><td style="white-space:nowrap"><a href="{{ url_for('sok_sida', q=h.ticker) }}"><b>{{ h.ticker|kort }}</b></a></td>
<td><span class="badge {{ klass_for(h.beslut) }}">{{ h.beslut }}</span></td><td class="small muted num">{{ h.tid }}</td></tr>
{% endfor %}</tbody></table></div>
{% endif %}
"""

BESLUT_SOK = {"KÖPLÄGE": "behall", "BEVAKA": "bevaka", "INTE KÖPLÄGE": "salj"}


def _forklara(b: dict) -> str:
    K = sc.KONFIG
    if b["beslut"] == "KÖPLÄGE":
        return f"Klarar filtren, topp {K['topp_n']} och urvalsreglerna."
    if b["beslut"] == "BEVAKA":
        if b["plats"] and b["plats"] > K["topp_n"]:
            return f"Godkänd men plats {b['plats']} – inte topp {K['topp_n']} ännu."
        if not b["urval"]:
            return "Topp " + str(K["topp_n"]) + " men bryter en urvalsregel."
        return "Klarar filtren – rankning saknas."
    if not b["godkand"]:
        return "Klarar inte trend- eller omsättningsfiltret."
    return f"Plats {b['plats']} – utanför topp {ih.REGLER['SALJ_RANK']}."


@app.route("/sok")
def sok_sida():
    q = request.args.get("q", "").strip()
    lista = tickerlista()
    alla = list(zip(lista["ticker"], lista["namn"]))
    kw = dict(q=q, alla=alla, forslag=[], fel=None, r=None, historik=las_sokhistorik(),
              salj_rank=ih.REGLER["SALJ_RANK"], stopp_pct=ih.REGLER["STOPP_PROCENT"],
              klass_for=lambda b: BESLUT_SOK.get(b, "info"), rapportdatum=None)
    st = screener_tabell()
    filer = sorted(UTMAPP.glob("screener_*.csv"))
    if filer:
        kw["rapportdatum"] = filer[-1].stem.removeprefix("screener_")
    if not q:
        return sida("Sök", "sok", SOK_MALL, **kw)
    try:
        return _sok(q, st, alla, kw)
    except Exception as e:  # visa felet på sidan i stället för "Internal Server Error"
        import traceback
        app.logger.error("Sökning '%s' misslyckades:\n%s", q, traceback.format_exc())
        kw["fel"] = (f"Något gick fel vid analysen av \"{q}\": {type(e).__name__}: {e}. "
                     "Detaljer finns i journalctl -u screener-web.")
        kw["r"] = None
        return sida("Sök", "sok", SOK_MALL, **kw)


def _sok(q: str, st, alla, kw):
    ticker, forslag = tolka_sokning(q)
    if forslag:
        kw["forslag"] = forslag
        return sida("Sök", "sok", SOK_MALL, **kw)
    if not ticker:
        kw["fel"] = f"Hittade ingen aktie som matchar \"{q}\"."
        return sida("Sök", "sok", SOK_MALL, **kw)

    valuta = ih.gissa_valuta(ticker)
    fxt = ih.fx_ticker(valuta)
    try:
        hamtat = (sc.demodata([ticker] + ([fxt] if fxt else []), seed=len(ticker)) if DEMO
                  else sc.hamta_kurser([ticker] + ([fxt] if fxt else []), "2y"))
    except Exception as e:
        kw["fel"] = f"Kunde inte hämta kurser: {e}"
        return sida("Sök", "sok", SOK_MALL, **kw)
    if ticker not in hamtat:
        tips = " Svenska aktier slutar på .ST, t.ex. VOLV-B.ST." if "." not in ticker else ""
        kw["fel"] = f"Yahoo har ingen kursdata för {ticker}.{tips}"
        return sida("Sök", "sok", SOK_MALL, **kw)

    df = hamtat[ticker]
    n = sc.berakna_nyckeltal(df)
    if n is None:
        kw["fel"] = (f"{ticker} har bara {len(df)} handelsdagar. Momentum kräver minst ett års historik "
                     f"({sc.KONFIG['min_dagar']} dagar), så den kan inte bedömas ännu.")
        return sida("Sök", "sok", SOK_MALL, **kw)
    if fxt:  # omsättningen räknas om till kronor
        fx = float(hamtat[fxt]["Close"].dropna().iloc[-1]) if fxt in hamtat else np.nan
        n["omsattning_sek"] = n["omsattning_sek"] * fx if pd.notna(fx) else n["omsattning_sek"]

    b = sc.bedom_en(ticker, n, st, ih.REGLER["SALJ_RANK"])
    veckoplats = None
    if st is not None and "plats" in st.columns:
        rad = st[st["ticker"] == ticker]
        if len(rad):
            p0 = pd.to_numeric(pd.Series([rad.iloc[0].get("plats")]), errors="coerce").iloc[0]
            godk = st["godkand"].astype(str).str.lower().isin(["true", "1"]).sum()
            veckoplats = f"{int(p0)} av {int(godk)}" if pd.notna(p0) else "ej godkänd"
    namn = dict(alla).get(ticker, "") or namn_for(ticker)
    agd = ticker in set(ih.las_innehav(INNEHAV_FIL)["ticker"])
    stopp = n["kurs"] * (1 - ih.REGLER["STOPP_PROCENT"])
    spara_sokning(ticker, b["beslut"])
    kw["r"] = {"ticker": ticker, "namn": namn, "valuta": valuta, "n": n, "b": b, "agd": agd,
               "klass": BESLUT_SOK[b["beslut"]], "forklaring": _forklara(b), "veckoplats": veckoplats,
               "stopp": stopp, "graf": dubbel(graf_svg, df["Close"], stopp=stopp), "nkvy": nyckeltal_vy(ticker)}
    return sida(f"Sök {ticker}", "sok", SOK_MALL, **kw)


if __name__ == "__main__":
    port = int(os.environ.get("SCREENER_PORT", "8080"))
    print(f"Screener-webben: http://0.0.0.0:{port}  (data: {UTMAPP})")
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=4)
    except ImportError:
        app.run(host="0.0.0.0", port=port)
