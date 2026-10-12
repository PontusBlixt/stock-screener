"""
Innehav och säljregler
======================

Delas av screener.py (veckokörningen) och app.py (webbsidan).

innehav.csv ligger i utmappen (/srv/storage/screener) så att den kan redigeras
både från webbsidan och direkt från Windows. Kolumner:
    ticker    Yahoo-ticker, t.ex. SWED-A.ST eller ADCT
    typ       momentum | kärna | övrigt
    kopdatum  ÅÅÅÅ-MM-DD
    kopkurs   pris per aktie i aktiens egen valuta
    antal     antal aktier
    valuta    SEK, USD, EUR ... (gissas från tickern om tom)
    notering  fri text

kurser.csv ligger i samma mapp: dagliga stängningskurser, en kolumn per ticker.
Den skrivs av screenern varje vecka och kompletteras av webbsidan när du
lägger till ett nytt innehav.

SÄLJREGLER (gäller typ "momentum"; för övriga visas signalerna bara som information)
  Hårda, kollas varje vecka -> SÄLJ
    1. Kursen under 200-dagars medelvärde
    2. 50-dagars medelvärde under 200-dagars
    3. Släpande stopp: kursen har fallit STOPP_PROCENT från högsta stängning sedan köp
  Månadsgenomgång -> SÄLJ VID MÅNADSGENOMGÅNG
    4. Utanför topp SALJ_RANK i screenern (bufferten mot köpgränsen topp 20)
  Bevaka -> BEVAKA
    - inom BEVAKA_STOPP_MARGINAL från stoppen
    - mindre än BEVAKA_MA200_MARGINAL över 200-dagars medelvärde
    - på plats (SALJ_RANK - 9) till SALJ_RANK
  Trimma -> TRIMMA (ersätter BEHÅLL/BEVAKA)
    - positionen är större än 1,5 gånger en jämn fördelning av momentumdelen,
      dock aldrig en gräns under MAX_ANDEL (med 10 innehav: 15 %, med 3: 50 %)
"""

from __future__ import annotations

import csv
import datetime as dt
import os
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# SÄLJREGLER – ändra här
# ---------------------------------------------------------------------------
REGLER = {
    "STOPP_PROCENT": 0.20,          # släpande stopp: -20 % från högsta sedan köp
    "SALJ_RANK": 40,                # sälj vid månadsgenomgång om plats > 40
    "BEVAKA_STOPP_MARGINAL": 0.05,  # bevaka när kursen är inom 5 % från stoppnivån
    "BEVAKA_MA200_MARGINAL": 0.03,  # bevaka när kursen är mindre än 3 % över MA200
    "MAX_ANDEL": 0.15,              # lägsta trimgräns (se TRIMMA nedan)
}

TYPER = ["momentum", "kärna", "övrigt"]
KOLUMNER = ["ticker", "typ", "kopdatum", "kopkurs", "antal", "valuta", "notering"]


# ---------------------------------------------------------------------------
# FILER
# ---------------------------------------------------------------------------
def gissa_valuta(ticker: str) -> str:
    t = ticker.upper()
    if t.endswith(".ST"):
        return "SEK"
    if t.endswith((".OL",)):
        return "NOK"
    if t.endswith((".CO",)):
        return "DKK"
    if t.endswith((".HE", ".DE", ".PA", ".AS")):
        return "EUR"
    return "USD"


def fx_ticker(valuta: str) -> str | None:
    """Yahoo-ticker för växelkursen till SEK, t.ex. USD -> 'USDSEK=X'."""
    return None if valuta == "SEK" else f"{valuta}SEK=X"


def las_innehav(fil: Path) -> pd.DataFrame:
    if not fil.exists():
        return pd.DataFrame(columns=KOLUMNER)
    df = pd.read_csv(fil, dtype=str, keep_default_na=False)
    for k in KOLUMNER:
        if k not in df.columns:
            df[k] = ""
    df = df[KOLUMNER].copy()
    df["ticker"] = df["ticker"].str.strip().str.upper()
    df = df[df["ticker"] != ""]
    df["typ"] = df["typ"].str.strip().str.lower().replace("", "momentum")
    df["valuta"] = [v.strip().upper() or gissa_valuta(t) for t, v in zip(df["ticker"], df["valuta"])]
    df["kopkurs"] = pd.to_numeric(df["kopkurs"].str.replace(",", "."), errors="coerce")
    df["antal"] = pd.to_numeric(df["antal"].str.replace(",", "."), errors="coerce")
    df["kopdatum"] = pd.to_datetime(df["kopdatum"], errors="coerce")
    return df.reset_index(drop=True)


def skriv_innehav(fil: Path, df: pd.DataFrame) -> None:
    ut = df[KOLUMNER].copy()
    ut["kopdatum"] = pd.to_datetime(ut["kopdatum"]).dt.strftime("%Y-%m-%d")
    tmp = fil.with_suffix(".tmp")
    ut.to_csv(tmp, index=False, quoting=csv.QUOTE_MINIMAL)
    os.replace(tmp, fil)


def las_kurser(fil: Path) -> pd.DataFrame:
    if not fil.exists():
        return pd.DataFrame()
    return pd.read_csv(fil, index_col=0, parse_dates=True)


def skriv_kurser(fil: Path, kurser: pd.DataFrame) -> None:
    tmp = fil.with_suffix(".tmp")
    kurser.sort_index().to_csv(tmp, float_format="%.6g")
    os.replace(tmp, fil)


def slå_ihop_kurser(gamla: pd.DataFrame, nya: dict[str, pd.Series]) -> pd.DataFrame:
    """Lägger till/ersätter kolumner i kurstabellen."""
    if not nya:
        return gamla
    ny_df = pd.DataFrame(nya)
    if gamla.empty:
        return ny_df.sort_index()
    gamla = gamla.drop(columns=[c for c in ny_df.columns if c in gamla.columns])
    return gamla.join(ny_df, how="outer").sort_index()


def senaste_screener(utmapp: Path) -> pd.DataFrame | None:
    filer = sorted(utmapp.glob("screener_*.csv"))
    return pd.read_csv(filer[-1]) if filer else None


# ---------------------------------------------------------------------------
# ANALYS
# ---------------------------------------------------------------------------
def _avk(s: pd.Series, bak: int, hoppa: int = 0) -> float:
    if len(s) <= bak:
        return np.nan
    return s.iloc[-1 - hoppa] / s.iloc[-1 - bak] - 1


def analysera(innehav: pd.DataFrame, kurser: pd.DataFrame, screener: pd.DataFrame | None) -> list[dict]:
    """Räknar fram nyckeltal och säljsignaler för varje innehav."""
    r = REGLER
    rank = {}
    if screener is not None and len(screener):
        for row in screener.itertuples():
            rank[row.ticker] = row
    n_godkanda = int(screener["godkand"].sum()) if screener is not None and len(screener) else 0

    ut = []
    for h in innehav.itertuples():
        a = {"ticker": h.ticker, "typ": h.typ, "kopdatum": h.kopdatum, "kopkurs": h.kopkurs,
             "antal": h.antal, "valuta": h.valuta, "notering": h.notering,
             "saknar_data": False, "signaler": [], "bevaka": [], "manad": []}

        if h.ticker not in kurser.columns or kurser[h.ticker].dropna().empty:
            a["saknar_data"] = True
            a["beslut"] = "SAKNAR DATA"
            ut.append(a)
            continue

        s = kurser[h.ticker].dropna()
        sista = float(s.iloc[-1])
        a["kurs"] = sista
        a["datum"] = s.index[-1].date()

        # Växelkurs
        fxt = fx_ticker(h.valuta)
        fx = 1.0
        if fxt:
            fx = float(kurser[fxt].dropna().iloc[-1]) if fxt in kurser.columns and kurser[fxt].notna().any() else np.nan
        a["fx"] = fx

        # Resultat
        a["resultat"] = sista / h.kopkurs - 1 if h.kopkurs and h.kopkurs > 0 else np.nan
        a["varde"] = sista * h.antal if pd.notna(h.antal) else np.nan
        a["varde_sek"] = a["varde"] * fx
        a["anskaffning_sek"] = h.kopkurs * h.antal * fx if pd.notna(h.antal) and pd.notna(h.kopkurs) else np.nan
        a["dagar"] = (s.index[-1] - h.kopdatum).days if pd.notna(h.kopdatum) else None

        # Högsta sedan köp och släpande stopp
        sedan = s[s.index >= h.kopdatum] if pd.notna(h.kopdatum) else s.iloc[-1:]
        hogsta = max(float(sedan.max()) if len(sedan) else sista, h.kopkurs if pd.notna(h.kopkurs) else 0)
        a["hogsta"] = hogsta
        a["fran_hogsta"] = sista / hogsta - 1
        a["stopp"] = hogsta * (1 - r["STOPP_PROCENT"])
        a["till_stopp"] = sista / a["stopp"] - 1  # hur långt kvar ned till stoppen

        # Trend
        a["ma50"] = float(s.rolling(50).mean().iloc[-1]) if len(s) >= 50 else np.nan
        a["ma200"] = float(s.rolling(200).mean().iloc[-1]) if len(s) >= 200 else np.nan
        a["over_ma200"] = sista / a["ma200"] - 1 if pd.notna(a["ma200"]) else np.nan

        # Momentum och risk
        a["mom_12_1"] = _avk(s, 252, 21)
        a["mom_6m"] = _avk(s, 126)
        a["mom_1m"] = _avk(s, 21)
        a["volatilitet"] = s.pct_change().iloc[-63:].std() * np.sqrt(252) if len(s) > 63 else np.nan

        # Rankning i screenern
        rad = rank.get(h.ticker)
        a["i_screener"] = rad is not None
        a["godkand"] = bool(rad.godkand) if rad is not None else None
        a["plats"] = int(rad.plats) if rad is not None and pd.notna(rad.plats) else None
        a["poang"] = float(rad.poang) if rad is not None and pd.notna(rad.poang) else None
        a["n_godkanda"] = n_godkanda

        # --- Signaler ---
        if pd.notna(a["ma200"]) and sista < a["ma200"]:
            a["signaler"].append("kursen under MA200")
        if pd.notna(a["ma50"]) and pd.notna(a["ma200"]) and a["ma50"] < a["ma200"]:
            a["signaler"].append("MA50 under MA200")
        if sista <= a["stopp"]:
            a["signaler"].append(f"stoppen nådd ({a['fran_hogsta'] * 100:.0f} % från högsta sedan köp)")

        if a["i_screener"]:
            if not a["godkand"]:
                a["manad"].append("klarar inte screenerns filter")
            elif a["plats"] and a["plats"] > r["SALJ_RANK"]:
                a["manad"].append(f"plats {a['plats']}, utanför topp {r['SALJ_RANK']}")
            elif a["plats"] and a["plats"] > r["SALJ_RANK"] - 10:
                a["bevaka"].append(f"plats {a['plats']}, nära gränsen topp {r['SALJ_RANK']}")

        if not a["signaler"]:
            if a["till_stopp"] < r["BEVAKA_STOPP_MARGINAL"]:
                a["bevaka"].append(f"{a['till_stopp'] * 100:.1f} % ovanför stoppen")
            if pd.notna(a["over_ma200"]) and 0 <= a["over_ma200"] < r["BEVAKA_MA200_MARGINAL"]:
                a["bevaka"].append(f"bara {a['over_ma200'] * 100:.1f} % över MA200")
        if pd.isna(a["ma200"]):
            a["bevaka"].append("för kort historik för MA200")

        ut.append(a)

    # Trimma: andel av momentumdelen
    mom = [a for a in ut if a["typ"] == "momentum" and pd.notna(a.get("varde_sek", np.nan))]
    total = sum(a["varde_sek"] for a in mom)
    mom_id = {id(a) for a in mom}
    # Gränsen: 1,5 gånger en jämn fördelning, men aldrig under MAX_ANDEL.
    # Med 3 innehav blir gränsen 50 %, med 10 innehav 15 %.
    trimgrans = max(r["MAX_ANDEL"], 1.5 / len(mom)) if mom else 1.0
    for a in ut:
        ar_mom = id(a) in mom_id and total > 0
        a["andel"] = a["varde_sek"] / total if ar_mom else np.nan
        a["trimma"] = bool(ar_mom and a["andel"] > trimgrans)
        a["trimgrans"] = trimgrans

    # Beslut
    for a in ut:
        if a["saknar_data"]:
            continue
        if a["signaler"]:
            b = "SÄLJ"
        elif a["manad"]:
            b = "SÄLJ VID MÅNADSGENOMGÅNG"
        elif a["bevaka"]:
            b = "BEVAKA"
        else:
            b = "BEHÅLL"
        if b in ("BEHÅLL", "BEVAKA") and a["trimma"]:
            b = "TRIMMA"
        if a["typ"] != "momentum":
            b = "INFO"  # kärna/övrigt styrs inte av momentumreglerna
        a["beslut"] = b
    return ut


def sammanfattning(analys: list[dict]) -> dict:
    """Totalvärden per typ i SEK."""
    s = {}
    for typ in TYPER:
        rader = [a for a in analys if a["typ"] == typ and pd.notna(a.get("varde_sek", np.nan))]
        varde = sum(a["varde_sek"] for a in rader)
        ansk = sum(a["anskaffning_sek"] for a in rader if pd.notna(a["anskaffning_sek"]))
        s[typ] = {"antal": len([a for a in analys if a["typ"] == typ]), "varde": varde,
                  "resultat": varde / ansk - 1 if ansk else np.nan}
    return s


def markdown_rader(analys: list[dict]) -> list[str]:
    """Rader till veckorapporten (.md) – så att säljsignalerna följer med när du klistrar in den."""
    if not analys:
        return []
    rader = ["## Dina innehav", "",
             "| Ticker | Typ | Beslut | Resultat | Från högsta | Till stopp | Över MA200 | Plats | Orsak |",
             "|---|---|---|---|---|---|---|---|---|"]

    def p(x):
        return "" if x is None or pd.isna(x) else f"{x * 100:+.1f}%"

    for a in analys:
        if a["saknar_data"]:
            rader.append(f"| {a['ticker']} | {a['typ']} | SAKNAR DATA | | | | | | |")
            continue
        orsak = "; ".join(a["signaler"] + a["manad"] + a["bevaka"] + (["trimma, för stor andel"] if a["trimma"] else []))
        plats = f"{a['plats']}/{a['n_godkanda']}" if a.get("plats") else ("ej godkänd" if a.get("i_screener") else "–")
        rader.append(f"| {a['ticker']} | {a['typ']} | **{a['beslut']}** | {p(a['resultat'])} | {p(a['fran_hogsta'])} "
                     f"| {p(a['till_stopp'])} | {p(a['over_ma200'])} | {plats} | {orsak} |")
    return rader
