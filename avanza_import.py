"""
Import av transaktioner från Avanza
===================================

Läser Avanzas CSV-export (Transaktioner → Exportera) och bygger om innehav.csv
och affarer.csv från dina faktiska köp och försäljningar.

Exportformatet (semikolon, decimalkomma):
  Datum;Konto;Typ av transaktion;Värdepapper/beskrivning;Antal;Kurs;Belopp;
  Transaktionsvaluta;Courtage (SEK);Valutakurs;Instrumentvaluta;ISIN;Resultat

Så räknas det:
  - Bara "Köp" och "Sälj" används. Insättningar, utdelningar, skatt m.m. hoppas över.
  - Köpkurs = det du faktiskt betalade per aktie, inklusive courtage, i aktiens valuta.
    Säljkurs = det du faktiskt fick per aktie, efter courtage.
  - Varje köp blir ett eget innehav. Försäljningar dras från de äldsta köpen först (FIFO).
    Avanza och Skatteverket använder genomsnittskurs (GAV) – resultatet per affär kan
    därför skilja sig något från Avanzas, men totalen blir densamma.
  - Exporten innehåller ingen Yahoo-ticker, bara ISIN. Kopplingen ISIN -> ticker
    sparas i isin_tickers.csv och återanvänds vid nästa import.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd

import innehav as ih

HOPPA_OVER = "hoppa över"
IMPORT_TYPER = ih.TYPER + [HOPPA_OVER]

RUBRIKER = {
    "datum": "datum", "konto": "konto", "typ av transaktion": "typ", "transaktionstyp": "typ",
    "värdepapper/beskrivning": "namn", "värdepapper": "namn", "beskrivning": "namn",
    "antal": "antal", "kurs": "kurs", "belopp": "belopp", "transaktionsvaluta": "tvaluta",
    "courtage (sek)": "courtage", "courtage": "courtage", "valutakurs": "valutakurs",
    "instrumentvaluta": "valuta", "valuta": "valuta", "isin": "isin", "resultat": "resultat",
}
TAL = ["antal", "kurs", "belopp", "courtage", "valutakurs", "resultat"]


# ---------------------------------------------------------------------------
# Läsa exporten
# ---------------------------------------------------------------------------
def _avkoda(data: bytes) -> str:
    for kod in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(kod)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _tal(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.replace("\xa0", "", regex=False).str.replace(" ", "", regex=False)
    s = s.str.replace(",", ".", regex=False).replace({"": None, "-": None, "nan": None})
    return pd.to_numeric(s, errors="coerce")


def las_export(data: bytes) -> pd.DataFrame:
    """Läser exportfilen till en tabell med enhetliga kolumnnamn."""
    text = _avkoda(data)
    forsta = text.splitlines()[0] if text else ""
    sep = ";" if forsta.count(";") >= forsta.count(",") else ","
    df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False)
    df.columns = [RUBRIKER.get(c.strip().lower(), c.strip().lower()) for c in df.columns]
    saknas = {"datum", "typ", "antal"} - set(df.columns)
    if saknas:
        raise ValueError("Filen ser inte ut som en Avanza-export (saknar kolumnerna "
                         + ", ".join(sorted(saknas)) + ").")
    for k in ["konto", "namn", "isin", "valuta", "kurs", "belopp", "valutakurs", "courtage"]:
        if k not in df.columns:
            df[k] = ""
    for k in TAL:
        if k in df.columns:
            df[k] = _tal(df[k])
    df["datum"] = pd.to_datetime(df["datum"].str.strip(), errors="coerce")
    for k in ["konto", "namn", "isin", "valuta", "typ"]:
        df[k] = df[k].astype(str).str.strip()
    return df


def transaktioner(df: pd.DataFrame, konton: list[str] | None = None) -> tuple[pd.DataFrame, dict]:
    """Plockar ut köp och sälj. Returnerar (transaktioner, {ignorerad typ: antal})."""
    typ = df["typ"].str.lower()
    ar_kop = typ.str.contains("köp")
    ar_salj = typ.str.contains("sälj")
    ignorerade = df.loc[~(ar_kop | ar_salj), "typ"].replace("", "(tom)").value_counts().to_dict()

    t = df[(ar_kop | ar_salj) & df["datum"].notna() & df["antal"].notna() & (df["antal"] != 0)].copy()
    t["handelse"] = np.where(t["typ"].str.lower().str.contains("köp"), "köp", "sälj")
    if konton:
        t = t[t["konto"].isin(konton)]

    # Instrument utan ISIN identifieras med namnet
    t["nyckel"] = np.where(t["isin"] != "", t["isin"], "NAMN:" + t["namn"])
    t["valuta"] = t["valuta"].replace("", "SEK").str.upper()
    t["antal_abs"] = t["antal"].abs()

    # Växelkurs: från exporten, annars härledd ur belopp/(antal*kurs), annars 1 för SEK
    fx = t["valutakurs"].where(t["valutakurs"] > 0)
    harledd = (t["belopp"].abs() / (t["antal_abs"] * t["kurs"])).where((t["kurs"] > 0) & (t["valuta"] != "SEK"))
    t["fx"] = np.where(t["valuta"] == "SEK", 1.0, fx.fillna(harledd))

    # Faktisk kurs per aktie i aktiens valuta, inklusive courtage
    pris = (t["belopp"].abs() / t["antal_abs"]) / t["fx"]
    t["pris"] = pris.where(pris.notna() & (pris > 0), t["kurs"])
    return t.sort_values(["datum", "handelse"]).reset_index(drop=True), ignorerade


def instrument(t: pd.DataFrame) -> pd.DataFrame:
    """Ett rad per värdepapper med antal köp/sälj och nuvarande antal."""
    if t.empty:
        return pd.DataFrame(columns=["nyckel", "isin", "namn", "valuta", "kop", "salj", "netto", "forsta", "senaste"])
    g = t.groupby("nyckel")
    ut = pd.DataFrame({
        "isin": g["isin"].first(),
        "namn": g["namn"].last(),
        "valuta": g["valuta"].last(),
        "kop": g["handelse"].apply(lambda s: (s == "köp").sum()),
        "salj": g["handelse"].apply(lambda s: (s == "sälj").sum()),
        "netto": g.apply(lambda d: d.loc[d["handelse"] == "köp", "antal_abs"].sum()
                         - d.loc[d["handelse"] == "sälj", "antal_abs"].sum(), include_groups=False),
        "forsta": g["datum"].min(),
        "senaste": g["datum"].max(),
    }).reset_index()
    ut["netto"] = ut["netto"].round(6)
    return ut.sort_values(["netto", "senaste"], ascending=[False, False]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# ISIN -> ticker
# ---------------------------------------------------------------------------
KARTA_KOLUMNER = ["isin", "ticker", "typ", "namn"]


def las_karta(fil: Path) -> dict[str, dict]:
    if not fil.exists():
        return {}
    df = pd.read_csv(fil, dtype=str, keep_default_na=False)
    return {r["isin"]: r for r in df.to_dict("records")}


def skriv_karta(fil: Path, karta: dict[str, dict]) -> None:
    rader = [{k: v.get(k, "") for k in KARTA_KOLUMNER} | {"isin": isin} for isin, v in sorted(karta.items())]
    tmp = fil.with_suffix(".tmp")
    pd.DataFrame(rader, columns=KARTA_KOLUMNER).to_csv(tmp, index=False, quoting=csv.QUOTE_MINIMAL)
    os.replace(tmp, fil)


def isin_fran_tickerlista(tickerfil: Path) -> dict[str, str]:
    """ISIN -> ticker ur tickers_se.csv (finns om den skapats med uppdatera_tickers.py)."""
    if not tickerfil.exists():
        return {}
    df = pd.read_csv(tickerfil, comment="#", dtype=str, keep_default_na=False, skipinitialspace=True)
    if "isin" not in df.columns:
        return {}
    return {r["isin"].strip(): r["ticker"].strip() for r in df.to_dict("records") if r["isin"].strip()}


_SOKCACHE: dict[str, str] = {}


def sok_yahoo(isin: str, valuta: str) -> str:
    """Slår upp en ISIN hos Yahoo Finance. Returnerar tom sträng om inget hittas."""
    if isin in _SOKCACHE:
        return _SOKCACHE[isin]
    citat = []
    try:
        import yfinance as yf
        citat = yf.Search(isin, max_results=8, news_count=0).quotes
    except Exception:
        try:
            url = "https://query2.finance.yahoo.com/v1/finance/search?" + urllib.parse.urlencode(
                {"q": isin, "quotesCount": 8, "newsCount": 0})
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as svar:
                citat = json.load(svar).get("quotes", [])
        except Exception:
            citat = []
    symboler = [c.get("symbol", "") for c in citat if c.get("symbol")]
    val = ""
    if isin.startswith("SE") or valuta == "SEK":
        val = next((s for s in symboler if s.endswith(".ST")), "")
    if not val and valuta == "USD":
        val = next((s for s in symboler if "." not in s), "")
    if not val and symboler:
        val = symboler[0]
    _SOKCACHE[isin] = val
    return val


def foresla(instr: pd.DataFrame, karta: dict, tickerlista: dict, nuvarande: pd.DataFrame,
            sok: bool = True) -> list[dict]:
    """Förslag på ticker och typ per värdepapper."""
    typ_per_ticker = dict(zip(nuvarande["ticker"], nuvarande["typ"])) if len(nuvarande) else {}
    ut = []
    for r in instr.to_dict("records"):
        isin = r["isin"]
        sparad = karta.get(isin) or karta.get(r["nyckel"])
        if sparad:
            ticker, typ, kalla = sparad.get("ticker", ""), sparad.get("typ", "") or "kärna", "sparad"
        else:
            ticker = tickerlista.get(isin, "")
            kalla = "Nasdaq-listan" if ticker else ""
            if not ticker and sok and isin:
                ticker = sok_yahoo(isin, r["valuta"])
                kalla = "Yahoo-sökning" if ticker else "hittades inte"
            typ = typ_per_ticker.get(ticker, "kärna")
        ut.append(r | {"ticker": ticker, "typ": typ, "kalla": kalla or "hittades inte"})
    return ut


# ---------------------------------------------------------------------------
# Bygga innehav och affärer (FIFO)
# ---------------------------------------------------------------------------
def bygg(t: pd.DataFrame, val: dict[str, dict]) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """val: nyckel -> {"ticker", "typ"}. Returnerar (innehav, affärer, varningar)."""
    innehav, affarer, varningar = [], [], []
    for nyckel, d in t.groupby("nyckel", sort=False):
        v = val.get(nyckel, {})
        typ = v.get("typ", "kärna")
        if typ == HOPPA_OVER:
            continue
        ticker = (v.get("ticker") or "").strip().upper() or (d["isin"].iloc[0] or nyckel)
        namn = d["namn"].iloc[-1]
        lotter: deque = deque()
        for r in d.sort_values(["datum", "handelse"]).to_dict("records"):
            if r["handelse"] == "köp":
                lotter.append([r["datum"], r["pris"], r["antal_abs"], r["fx"], r["valuta"]])
                continue
            kvar = r["antal_abs"]
            while kvar > 1e-9 and lotter:
                lot = lotter[0]
                ta = min(kvar, lot[2])
                affarer.append({
                    "ticker": ticker, "typ": typ, "kopdatum": lot[0], "kopkurs": lot[1],
                    "saljdatum": r["datum"], "saljkurs": r["pris"], "antal": ta, "valuta": r["valuta"],
                    "fx_kop": lot[3], "fx_salj": r["fx"],
                    "resultat_sek": ta * (r["pris"] * r["fx"] - lot[1] * lot[3]),
                    "orsak": "import från Avanza", "notering": namn,
                })
                lot[2] -= ta
                kvar -= ta
                if lot[2] <= 1e-9:
                    lotter.popleft()
            if kvar > 1e-6:
                varningar.append(f"{namn}: sålde {kvar:g} fler än exporten visar köpta "
                                 f"({r['datum'].date()}). Exportera hela historiken.")
        for lot in lotter:
            if lot[2] > 1e-9:
                innehav.append({"ticker": ticker, "typ": typ, "kopdatum": lot[0], "kopkurs": lot[1],
                                "antal": round(lot[2], 6), "valuta": lot[4], "notering": namn})
    inn = pd.DataFrame(innehav, columns=ih.KOLUMNER)
    aff = pd.DataFrame(affarer, columns=ih.AFFAR_KOLUMNER)
    return inn, aff, varningar
