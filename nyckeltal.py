#!/usr/bin/env python3
"""
Daglig insamling av nyckeltal
=============================

Sparar värderings- och lönsamhetsnyckeltal för alla aktier i tickers_se.csv
plus dina innehav, en gång per dag. Syftet är att bygga upp en egen historik
som Yahoo inte lämnar ut i efterhand – så att vi senare kan testa om billiga
aktier faktiskt gått bättre.

Lagring:  <utmapp>/nyckeltal/ÅÅÅÅ-MM-DD.csv  (en fil per dag, en rad per aktie)
          --demo sparar i <utmapp>/nyckeltal_demo/ och rör aldrig den riktiga historiken.
          Körs skriptet flera gånger samma dag skrivs dagens fil om.
          Läs in all historik med  las_historik()  (används av backtestet).

Körning:
  ./venv/bin/python nyckeltal.py            # hämta dagens nyckeltal
  ./venv/bin/python nyckeltal.py --demo     # testa utan internet
  ./venv/bin/python nyckeltal.py --tickers VOLV-B.ST,ADCT   # bara några aktier

Cron (vardagar kl. 18:15, efter börsens stängning):
  15 18 * * 1-5 cd ~/screener && ./venv/bin/python nyckeltal.py >> <utmapp>/nyckeltal/logg.txt 2>&1

Hämtningen tar några minuter för ~400 aktier eftersom varje aktie är ett eget
anrop hos Yahoo, med en kort paus emellan för att inte bli strypt.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys
import time
from pathlib import Path

import pandas as pd

import innehav as ih
import screener as sc

# Fält från Yahoo (Ticker.info) som sparas, och deras namn i våra filer.
FALT = {
    # Storlek och pris
    "currentPrice": "kurs",
    "marketCap": "borsvarde",              # aktiens pris × antal aktier
    "enterpriseValue": "bolagsvarde",      # börsvärde + skulder − kassa (EV)
    "sharesOutstanding": "antal_aktier",
    # Värdering
    "trailingPE": "pe",                    # kurs / vinst per aktie, senaste 12 mån
    "forwardPE": "pe_prognos",
    "priceToBook": "pb",                   # kurs / eget kapital per aktie
    "priceToSalesTrailing12Months": "ps",  # börsvärde / omsättning
    "enterpriseToRevenue": "ev_sales",     # bolagsvärde / omsättning
    "enterpriseToEbitda": "ev_ebitda",     # bolagsvärde / rörelseresultat före avskrivningar
    "dividendYield": "direktavkastning",
    # Underliggande siffror (senaste 12 mån), så att kvoterna kan räknas om och kontrolleras
    "totalRevenue": "omsattning",
    "ebitda": "ebitda",
    "netIncomeToCommon": "nettoresultat",
    "freeCashflow": "fritt_kassaflode",
    "totalDebt": "skulder",
    "totalCash": "kassa",
    "bookValue": "eget_kapital_per_aktie",
    # Lönsamhet, tillväxt och risk
    "profitMargins": "vinstmarginal",
    "operatingMargins": "rorelsemarginal",
    "returnOnEquity": "roe",
    "debtToEquity": "skuld_ek",
    "revenueGrowth": "omsattningstillvaxt",
    "earningsGrowth": "vinsttillvaxt",
    "beta": "beta",
    # Klassning – behövs för att jämföra inom sektor
    "sector": "sektor",
    "industry": "bransch",
    "currency": "valuta",                  # aktiens handelsvaluta
    "financialCurrency": "rapportvaluta",  # valutan i bolagets rapporter (kan skilja!)
}
TEXTFALT = {"sektor", "bransch", "valuta", "rapportvaluta"}
# fx_rapport: växelkurs från rapportvaluta till handelsvaluta (1 om samma).
# Yahoo anger bolagsvärde/börsvärde i handelsvaluta (SEK) men omsättning, EBITDA och
# resultat i rapportvaluta (t.ex. USD för ABB) – och räknar sina färdiga kvoter utan
# att växla. Kvoterna nedan (_k) är därför omräknade och de som ska användas.
KORR = ["ev_k", "ev_sales_k", "ev_ebitda_k", "ev_ebit_k", "ps_k", "pb_k", "ev_yahoo_kvot"]
KOLUMNER = ["datum", "ticker"] + list(FALT.values()) + ["fx_rapport"] + KORR

PAUS = (0.4, 0.9)       # sekunder mellan anrop (slumpad)
FORSOK = 3              # antal försök per aktie


def mapp(utmapp: Path, demo: bool = False) -> Path:
    m = utmapp / ("nyckeltal_demo" if demo else "nyckeltal")
    m.mkdir(parents=True, exist_ok=True)
    return m


def hamta_en(ticker: str) -> dict | None:
    """Hämtar nyckeltal för en aktie. None om Yahoo inte har något."""
    import yfinance as yf

    for forsok in range(FORSOK):
        try:
            info = yf.Ticker(ticker).info or {}
            if not info or (info.get("quoteType") is None and info.get("marketCap") is None):
                return None
            rad = {namn: info.get(falt) for falt, namn in FALT.items()}
            if rad["kurs"] is None:
                rad["kurs"] = info.get("regularMarketPrice") or info.get("previousClose")
            return rad
        except Exception as e:  # nätverksfel, strypning (429) m.m.
            if forsok == FORSOK - 1:
                print(f"  {ticker}: gav upp efter {FORSOK} försök ({type(e).__name__}: {e})", file=sys.stderr)
                return None
            time.sleep(5 * (forsok + 1))
    return None


def demo_en(ticker: str) -> dict:
    """Påhittade men rimliga nyckeltal, för test utan internet."""
    r = random.Random(ticker)
    oms = r.uniform(5e8, 5e10)
    marg = r.uniform(-0.05, 0.25)
    ev = oms * r.uniform(0.5, 4)
    return {
        "kurs": r.uniform(20, 400), "borsvarde": ev * 0.9, "bolagsvarde": ev, "antal_aktier": ev / 150,
        "pe": r.uniform(6, 45) if marg > 0 else None, "pe_prognos": r.uniform(6, 35), "pb": r.uniform(0.6, 8),
        "ps": ev * 0.9 / oms, "ev_sales": ev / oms, "ev_ebitda": ev / (oms * (marg + 0.05)) if marg > -0.04 else None,
        "direktavkastning": r.uniform(0, 0.06), "omsattning": oms, "ebitda": oms * (marg + 0.05),
        "nettoresultat": oms * marg, "fritt_kassaflode": oms * marg * 0.8, "skulder": ev * 0.2, "kassa": ev * 0.1,
        "eget_kapital_per_aktie": r.uniform(10, 200), "vinstmarginal": marg, "rorelsemarginal": marg + 0.03,
        "roe": r.uniform(-0.1, 0.3), "skuld_ek": r.uniform(0, 150), "omsattningstillvaxt": r.uniform(-0.1, 0.3),
        "vinsttillvaxt": r.uniform(-0.5, 0.6), "beta": r.uniform(0.5, 1.8),
        "sektor": r.choice(["Industrials", "Technology", "Financial Services", "Healthcare", "Real Estate",
                            "Consumer Cyclical", "Basic Materials"]),
        "bransch": "Demo", "valuta": "SEK", "rapportvaluta": r.choice(["SEK", "SEK", "SEK", "EUR"]),
    }


def _senaste(symbol: str) -> float:
    """Senaste stängningskurs för en valutasymbol hos Yahoo, NaN om den saknas (utan felutskrifter)."""
    import contextlib
    import io as _io
    import logging
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    try:
        with contextlib.redirect_stderr(_io.StringIO()), contextlib.redirect_stdout(_io.StringIO()):
            s = yf.download(symbol, period="10d", progress=False, auto_adjust=True)["Close"].dropna()
        return float(s.iloc[-1].squeeze()) if len(s) else float("nan")
    except Exception:
        return float("nan")


def hamta_fx(par: set[tuple[str, str]], demo: bool = False) -> dict[tuple[str, str], float]:
    """Växelkurser (från, till) -> kurs, t.ex. ('USD','SEK') -> 10.4.
    Saknar Yahoo direktkursen (t.ex. PLNSEK=X) prövas omvänd kurs och sedan via euro."""
    ut = {}
    for fran, till in par:
        if fran == till:
            ut[(fran, till)] = 1.0
            continue
        if demo:
            ut[(fran, till)] = {"EUR": 11.2, "USD": 10.4}.get(fran, float("nan"))
            continue
        kurs = _senaste(f"{fran}{till}=X")
        if kurs != kurs:  # NaN
            omvand = _senaste(f"{till}{fran}=X")
            kurs = 1 / omvand if omvand == omvand and omvand else float("nan")
        if kurs != kurs and "EUR" not in (fran, till):
            eur_till, eur_fran = _senaste(f"EUR{till}=X"), _senaste(f"EUR{fran}=X")
            if eur_till == eur_till and eur_fran == eur_fran and eur_fran:
                kurs = eur_till / eur_fran
        if kurs != kurs:
            print(f"  växelkurs {fran}→{till} saknas – EV-kvoterna blir tomma för de bolagen", file=sys.stderr)
        ut[(fran, till)] = kurs
    return ut


def korrigera(df: pd.DataFrame) -> pd.DataFrame:
    """Räknar om värderingarna från de råa siffrorna, i handelsvaluta (SEK).

    Två fel i Yahoos färdiga kvoter rättas här:
      1. Valuta: bolagsvärde och kurs är i SEK, men omsättning, resultat, skulder,
         kassa och eget kapital är i rapportvaluta (t.ex. USD för ABB). Yahoo växlar inte.
      2. Bolagsvärde: Yahoos enterpriseValue är ibland helt fel (t.ex. räknat i fel valuta).
         Vi räknar därför själva: EV = börsvärde + (skulder − kassa) × växelkurs.

    Körs både när filen sparas och när historiken läses in, så att en rättelse här
    automatiskt gäller även för redan sparade dagar.
    Negativt EBIT, EBITDA eller bolagsvärde ger tomt värde – kvoter på negativa tal går inte att jämföra.
    För banker och fastighetsbolag är EV-kvoter missvisande; använd pb_k för dem."""
    df = df.copy()
    fx = pd.to_numeric(df.get("fx_rapport"), errors="coerce")
    oms = df["omsattning"] * fx
    ebitda = df["ebitda"] * fx
    ebit = df["rorelsemarginal"] * oms
    nettoskuld = (df["skulder"].fillna(0) - df["kassa"].fillna(0)) * fx
    df["ev_k"] = df["borsvarde"] + nettoskuld
    ev = df["ev_k"].where(df["ev_k"] > 0)
    df["ev_sales_k"] = ev / oms.where(oms > 0)
    df["ev_ebitda_k"] = ev / ebitda.where(ebitda > 0)
    df["ev_ebit_k"] = ev / ebit.where(ebit > 0)
    df["ps_k"] = df["borsvarde"] / oms.where(oms > 0)
    ek = df["eget_kapital_per_aktie"] * fx
    df["pb_k"] = df["kurs"] / ek.where(ek > 0)
    # Kontrollvärde: hur mycket Yahoos bolagsvärde avviker från vårt (1.0 = samma)
    df["ev_yahoo_kvot"] = df["bolagsvarde"] / df["ev_k"].where(df["ev_k"] != 0)
    return df


def tickers_att_hamta(tickerfil: Path, utmapp: Path) -> list[str]:
    lista = list(sc.las_tickers(tickerfil)["ticker"]) if tickerfil.exists() else []
    egna = list(ih.las_innehav(utmapp / "innehav.csv")["ticker"])
    return list(dict.fromkeys(lista + egna))


def samla(tickers: list[str], utmapp: Path, demo: bool = False) -> Path:
    idag = dt.date.today().isoformat()
    rader, saknas = [], []
    start = time.time()
    for i, t in enumerate(tickers, 1):
        rad = demo_en(t) if demo else hamta_en(t)
        if rad is None:
            saknas.append(t)
        else:
            rader.append({"datum": idag, "ticker": t, **rad})
        if not demo:
            time.sleep(random.uniform(*PAUS))
        if i % 50 == 0:
            print(f"  {i}/{len(tickers)} ({time.time() - start:.0f} s)", file=sys.stderr)

    df = pd.DataFrame(rader)
    if len(df):
        rv = df["rapportvaluta"].fillna(df["valuta"]).fillna("SEK")
        hv = df["valuta"].fillna("SEK")
        kurser = hamta_fx(set(zip(rv, hv)), demo)
        df["fx_rapport"] = [kurser.get((a, b), float("nan")) for a, b in zip(rv, hv)]
        df = korrigera(df)
    df = df.reindex(columns=KOLUMNER)
    fil = mapp(utmapp, demo) / f"{idag}.csv"
    tmp = fil.with_suffix(".tmp")
    df.to_csv(tmp, index=False, float_format="%.6g")
    os.replace(tmp, fil)

    print(("DEMO – påhittad data, sparad separat. " if demo else "") + f"{idag}: sparade {len(df)} aktier till {fil} på {time.time() - start:.0f} s"
          + (f". Saknade data: {len(saknas)} ({', '.join(saknas[:15])}{' …' if len(saknas) > 15 else ''})"
             if saknas else ""), file=sys.stderr)
    # Kvalitetskontroll: hur stor andel har värderingsmått?
    if len(df):
        tackning = {k: df[k].notna().mean() for k in ("ev_ebit_k", "ev_ebitda_k", "ev_sales_k", "pe", "pb_k", "sektor")}
        print("  Täckning: " + ", ".join(f"{k} {v * 100:.0f} %" for k, v in tackning.items()), file=sys.stderr)
    return fil


def las_historik(utmapp: Path) -> pd.DataFrame:
    """All sparad historik som en tabell (datum, ticker, nyckeltal …)."""
    filer = sorted(mapp(utmapp).glob("????-??-??.csv"))
    if not filer:
        return pd.DataFrame(columns=KOLUMNER)
    df = pd.concat((pd.read_csv(f) for f in filer), ignore_index=True)
    df["datum"] = pd.to_datetime(df["datum"])
    # Räkna alltid om från råvärdena, så att senaste versionen av korrigera() gäller hela historiken
    if "fx_rapport" in df.columns:
        df = korrigera(df)
    return df


def main() -> int:
    p = argparse.ArgumentParser(description="Spara dagens nyckeltal")
    p.add_argument("--utmapp", default=sc.KONFIG["utmapp"])
    p.add_argument("--tickers", default="", help="kommaseparerade tickers (annars hela listan + innehav)")
    p.add_argument("--demo", action="store_true", help="påhittad data, ingen internet")
    a = p.parse_args()

    utmapp = Path(a.utmapp).expanduser()
    tickers = ([t.strip().upper() for t in a.tickers.split(",") if t.strip()] if a.tickers
               else tickers_att_hamta(sc.MAPP / "tickers_se.csv", utmapp))
    if not tickers:
        print("Inga tickers att hämta.", file=sys.stderr)
        return 1
    print(f"Hämtar nyckeltal för {len(tickers)} aktier ...", file=sys.stderr)
    samla(tickers, utmapp, a.demo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
