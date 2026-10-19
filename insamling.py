#!/usr/bin/env python3
"""
Insamling av data som inte går att få tillbaka i efterhand
===========================================================

Tre källor, sparade som råfiler i datamappen så att de kan analyseras senare:

  insyn/ÅÅÅÅ-MM.csv          Insynsaffärer från Finansinspektionens insynsregister – alla köp och
                             försäljningar av personer i ledande ställning, en fil per publiceringsmånad.
                             Historik sedan juli 2016 hämtas med --historik, sedan uppdateras de två
                             senaste månaderna varje dag (korrigeringar kommer ibland i efterhand).
  blankning/ÅÅÅÅ-MM-DD_*.xlsx  Blankningsregistret: FI:s Excelfiler med aktuella, historiska och
                             aggregerade korta positioner. Aktuella och aggregerade sparas varje dag
                             (de skrivs över av FI). Historiska hämtas en gång i veckan.
  rapporter/historik.csv     Rapportdatum med analytikernas förväntade vinst per aktie, utfall och
                             överraskning från Yahoo (yfinance get_earnings_dates). Uppdateras en gång
                             i veckan. Varje hämtning sparas också som rapporter/ÅÅÅÅ-MM-DD.csv.

Inget av detta påverkar veckorapporten, säljreglerna eller strategierna – det är bara insamling.

Körning:
  ./venv/bin/python insamling.py --historik   # första gången: insyn sedan 2016 + rapporthistorik (tar en stund)
  ./venv/bin/python insamling.py              # dagligen (cron), hämtar bara det nya
  ./venv/bin/python insamling.py --bara insyn # en källa: insyn, blankning eller rapporter

Cron (vardagar 19:15, efter nyckeltal och daglig stopp):
  15 19 * * 1-5 cd ~/screener && ./venv/bin/python insamling.py >> /srv/storage/screener/insamling_logg.txt 2>&1
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

import screener as sc

MAPP = Path(__file__).resolve().parent

INSYN_URL = ("https://marknadssok.fi.se/Publiceringsklient/sv-SE/Search/Search?SearchFunctionType=Insyn"
             "&Utgivare=&PersonILedandeSt%C3%A4llningNamn=&Transaktionsdatum.From=&Transaktionsdatum.To="
             "&Publiceringsdatum.From={fran}&Publiceringsdatum.To={till}&button=export")
INSYN_START = dt.date(2016, 7, 1)          # EU:s marknadsmissbruksförordning (MAR) började gälla
BLANKNING_SIDA = "https://www.fi.se/sv/vara-register/blankningsregistret/"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
           "Accept-Encoding": "identity"}
RAPPORTER_INTERVALL_DAGAR = 6              # rapportdatum ändras sällan – hämta en gång i veckan


def _hamta(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as svar:
        return svar.read()


def _skriv_atomiskt(fil: Path, data: bytes) -> None:
    fil.parent.mkdir(parents=True, exist_ok=True)
    tmp = fil.with_name(fil.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, fil)


# ---------------------------------------------------------------------------
# INSYNSREGISTRET
# ---------------------------------------------------------------------------
def tolka_insyn(data: bytes) -> pd.DataFrame:
    """FI:s export är semikolonseparerad text i UTF-16 med rubrikrad."""
    for kod in ("utf-16", "utf-16-le", "utf-8-sig"):
        try:
            text = data.decode(kod)
            if "Publiceringsdatum" in text[:500] or not text.strip():
                break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("okänt format på svaret från FI")
    if not text.strip():
        return pd.DataFrame()
    return pd.read_csv(io.StringIO(text), sep=";", dtype=str, keep_default_na=False)


def hamta_insyn_manad(manad: dt.date) -> pd.DataFrame:
    fran = manad.replace(day=1)
    till = (fran + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    return tolka_insyn(_hamta(INSYN_URL.format(fran=fran.isoformat(), till=till.isoformat()), timeout=120))


def insyn(utmapp: Path, historik: bool) -> str:
    m = utmapp / "insyn"
    m.mkdir(parents=True, exist_ok=True)
    idag = dt.date.today()
    manader = []
    d = INSYN_START if historik else (idag.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    while d <= idag:
        manader.append(d)
        d = (d + dt.timedelta(days=32)).replace(day=1)
    nya = fel = rader = 0
    for i, manad in enumerate(manader):
        fil = m / f"{manad:%Y-%m}.csv"
        aktuell = manad >= (idag.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
        if fil.exists() and not aktuell:
            continue  # avslutade månader hämtas bara en gång
        try:
            df = hamta_insyn_manad(manad)
            if len(df) == 0 and not aktuell:
                print(f"  insyn {manad:%Y-%m}: inga rader", file=sys.stderr)
            _skriv_atomiskt(fil, df.to_csv(index=False).encode("utf-8"))
            nya += 1
            rader += len(df)
        except Exception as e:
            fel += 1
            print(f"  insyn {manad:%Y-%m}: FEL {type(e).__name__}: {e}", file=sys.stderr)
        if i < len(manader) - 1:
            time.sleep(random.uniform(1.5, 3.0))
        if historik and nya and nya % 12 == 0:
            print(f"  insyn: {nya} månader hämtade ...", file=sys.stderr)
    return f"insyn: {nya} månadsfiler uppdaterade, {rader} affärer, {fel} fel"


# ---------------------------------------------------------------------------
# BLANKNINGSREGISTRET
# ---------------------------------------------------------------------------
def blankningslankar(html: str) -> dict[str, str]:
    """Letar upp länkarna till Excelfilerna på FI:s sida. {namn: url}"""
    ut = {}
    for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.S | re.I):
        t = re.sub(r"<[^>]+>", "", text).strip().lower()
        h = href.lower()
        namn = ("aktuella" if "aktuell" in t or "aktuell" in h else
                "historiska" if "histori" in t or "hist" in h else
                "aggregerade" if "aggreg" in t or "aggreg" in h else None)
        if namn and (any(x in h for x in (".xls", ".ods", "file", "get", "download", "export")) or "position" in t):
            ut.setdefault(namn, urllib.parse.urljoin(BLANKNING_SIDA, href.replace("&amp;", "&")))
    return ut


def blankning(utmapp: Path) -> str:
    m = utmapp / "blankning"
    m.mkdir(parents=True, exist_ok=True)
    idag = dt.date.today()
    html = _hamta(BLANKNING_SIDA).decode("utf-8", errors="replace")
    lankar = blankningslankar(html)
    if not lankar:
        return "blankning: FEL – hittade inga Excellänkar på FI:s sida (sidan kan ha ändrats)"
    ut = []
    for namn, url in lankar.items():
        if namn == "historiska" and idag.weekday() != 0 and any(m.glob("*_historiska.*")):
            continue  # historiken hämtas på måndagar (och första gången)
        try:
            data = _hamta(url, timeout=120)
            typ = ".ods" if data[:2] == b"PK" and b"opendocument" in data[:200] else ".xlsx" if data[:2] == b"PK" else ".xls"
            if data[:1] == b"<":
                ut.append(f"{namn}: FEL – fick en webbsida i stället för en fil ({url})")
                continue
            _skriv_atomiskt(m / f"{idag}_{namn}{typ}", data)
            ut.append(f"{namn} {len(data) // 1024} kB")
        except Exception as e:
            ut.append(f"{namn}: FEL {type(e).__name__}: {e}")
        time.sleep(1)
    return "blankning: " + ", ".join(ut)


# ---------------------------------------------------------------------------
# RAPPORTDATUM OCH ÖVERRASKNINGAR (Yahoo)
# ---------------------------------------------------------------------------
def rapporter(utmapp: Path, historik: bool, tickerfil: Path) -> str:
    m = utmapp / "rapporter"
    m.mkdir(parents=True, exist_ok=True)
    idag = dt.date.today()
    senaste = sorted(m.glob("????-??-??.csv"))
    if senaste and not historik and (idag - dt.date.fromisoformat(senaste[-1].stem)).days < RAPPORTER_INTERVALL_DAGAR:
        return f"rapporter: hämtade senast {senaste[-1].stem}, nästa om några dagar"
    import yfinance as yf
    tickers = list(sc.las_tickers(tickerfil)["ticker"])
    rader, utan = [], 0
    for i, t in enumerate(tickers, 1):
        try:
            df = yf.Ticker(t).get_earnings_dates(limit=40 if historik else 12)
        except Exception:
            df = None
        if df is None or len(df) == 0:
            utan += 1
        else:
            df = df.reset_index()
            df.columns = [str(c) for c in df.columns]
            for r in df.itertuples(index=False):
                d = dict(zip(df.columns, r))
                datum = pd.Timestamp(d.get("Earnings Date"))
                rader.append({"ticker": t, "rapportdatum": datum.tz_localize(None) if datum.tzinfo else datum,
                              "eps_forvantad": d.get("EPS Estimate"), "eps_utfall": d.get("Reported EPS"),
                              "overraskning_pct": d.get("Surprise(%)"), "hamtad": idag.isoformat()})
        time.sleep(random.uniform(0.6, 1.2))
        if i % 50 == 0:
            print(f"  rapporter {i}/{len(tickers)}", file=sys.stderr)
    ny = pd.DataFrame(rader)
    _skriv_atomiskt(m / f"{idag}.csv", ny.to_csv(index=False).encode("utf-8"))
    # Sammanslagen historik: senaste hämtningen gäller för samma ticker och datum (utfall fylls i efter rapporten)
    hist_fil = m / "historik.csv"
    alla = pd.concat([pd.read_csv(hist_fil), ny], ignore_index=True) if hist_fil.exists() else ny
    if len(alla):
        alla["rapportdatum"] = pd.to_datetime(alla["rapportdatum"])
        alla["dag"] = alla["rapportdatum"].dt.date
        alla = alla.sort_values("hamtad").drop_duplicates(["ticker", "dag"], keep="last").drop(columns="dag")
        alla = alla.sort_values(["ticker", "rapportdatum"])
        _skriv_atomiskt(hist_fil, alla.to_csv(index=False).encode("utf-8"))
    med = len(tickers) - utan
    return (f"rapporter: {len(ny)} rapporter för {med} av {len(tickers)} aktier"
            + (f", {len(alla)} i historiken" if len(alla) else ""))


# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description="Insamling av insynsaffärer, blankning och rapportdatum")
    p.add_argument("--utmapp", default=os.environ.get("SCREENER_UTMAPP", sc.KONFIG["utmapp"]))
    p.add_argument("--tickers", default=str(MAPP / "tickers_se.csv"))
    p.add_argument("--historik", action="store_true", help="hämta all historik (första gången)")
    p.add_argument("--bara", choices=["insyn", "blankning", "rapporter"], help="bara en källa")
    a = p.parse_args()
    utmapp = Path(a.utmapp).expanduser()
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M} insamling{' (historik)' if a.historik else ''}")
    kallor = {"insyn": lambda: insyn(utmapp, a.historik),
              "blankning": lambda: blankning(utmapp),
              "rapporter": lambda: rapporter(utmapp, a.historik, Path(a.tickers))}
    fel = 0
    for namn, funk in kallor.items():
        if a.bara and namn != a.bara:
            continue
        try:  # en källa som felar får inte stoppa de andra
            rad = funk()
        except Exception as e:
            rad = f"{namn}: FEL {type(e).__name__}: {e}"
        fel += "FEL" in rad
        print("  " + rad)
    return 1 if fel else 0


if __name__ == "__main__":
    sys.exit(main())
