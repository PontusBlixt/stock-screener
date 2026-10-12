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


# ---------------------------------------------------------------------------
# AFFÄRSHISTORIK (avslutade affärer)
# ---------------------------------------------------------------------------
# affarer.csv ligger bredvid innehav.csv. En rad per försäljning; säljer du en del
# av ett innehav blir den sålda delen en affär och resten ligger kvar som innehav.
AFFAR_KOLUMNER = ["ticker", "typ", "kopdatum", "kopkurs", "saljdatum", "saljkurs", "antal", "valuta",
                  "fx_kop", "fx_salj", "resultat_sek", "orsak", "notering"]

SALJORSAKER = ["trendregel (MA200/MA50)", "släpande stopp", "utanför topp 40", "trimning",
               "eget beslut", "annat"]

# Jämförelseindex på Yahoo Finance. ^OMXSPI = hela Stockholmsbörsen, ^OMX = OMXS30.
# Båda är prisindex (utan utdelningar), så index ser något sämre ut än verkligheten (~3 %/år).
JAMFORELSEINDEX = "^OMXSPI"
INDEXNAMN = {"^OMXSPI": "OMX Stockholm PI", "^OMX": "OMXS30"}


def las_affarer(fil: Path) -> pd.DataFrame:
    if not fil.exists():
        return pd.DataFrame(columns=AFFAR_KOLUMNER)
    df = pd.read_csv(fil, dtype=str, keep_default_na=False)
    for k in AFFAR_KOLUMNER:
        if k not in df.columns:
            df[k] = ""
    df = df[AFFAR_KOLUMNER].copy()
    for k in ["kopkurs", "saljkurs", "antal", "fx_kop", "fx_salj", "resultat_sek"]:
        df[k] = pd.to_numeric(df[k].str.replace(",", "."), errors="coerce")
    for k in ["kopdatum", "saljdatum"]:
        df[k] = pd.to_datetime(df[k], errors="coerce")
    return df.reset_index(drop=True)


def skriv_affarer(fil: Path, df: pd.DataFrame) -> None:
    ut = df[AFFAR_KOLUMNER].copy()
    for k in ["kopdatum", "saljdatum"]:
        ut[k] = pd.to_datetime(ut[k]).dt.strftime("%Y-%m-%d")
    tmp = fil.with_suffix(".tmp")
    ut.to_csv(tmp, index=False, quoting=csv.QUOTE_MINIMAL)
    os.replace(tmp, fil)


def fx_vid(kurser: pd.DataFrame, valuta: str, datum) -> float:
    """Växelkurs till SEK vid ett datum (senast kända). 1.0 för SEK, NaN om den saknas."""
    fxt = fx_ticker(valuta)
    if not fxt:
        return 1.0
    if fxt not in kurser.columns:
        return np.nan
    s = kurser[fxt].dropna()
    if s.empty:
        return np.nan
    v = s.asof(pd.Timestamp(datum))
    return float(v) if pd.notna(v) else float(s.iloc[0])


def salj(innehav: pd.DataFrame, idx: int, saljdatum, saljkurs: float, antal: float,
         orsak: str, kurser: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Säljer hela eller en del av innehav nr idx. Returnerar (nya innehav, affärsraden)."""
    h = innehav.iloc[idx]
    antal = min(float(antal), float(h["antal"]))
    fx_k = fx_vid(kurser, h["valuta"], h["kopdatum"])
    fx_s = fx_vid(kurser, h["valuta"], saljdatum)
    affar = {
        "ticker": h["ticker"], "typ": h["typ"], "kopdatum": h["kopdatum"], "kopkurs": h["kopkurs"],
        "saljdatum": pd.Timestamp(saljdatum), "saljkurs": float(saljkurs), "antal": antal,
        "valuta": h["valuta"], "fx_kop": fx_k, "fx_salj": fx_s,
        "resultat_sek": antal * (float(saljkurs) * fx_s - float(h["kopkurs"]) * fx_k),
        "orsak": orsak, "notering": h["notering"],
    }
    ny = innehav.copy()
    kvar = float(h["antal"]) - antal
    if kvar > 1e-9:
        ny.loc[ny.index[idx], "antal"] = kvar
    else:
        ny = ny.drop(index=ny.index[idx]).reset_index(drop=True)
    return ny, affar


def affarsstatistik(affarer: pd.DataFrame) -> dict:
    """Träffsäkerhet, snittvinst och snittförlust för avslutade affärer."""
    if affarer.empty:
        return {"antal": 0}
    r = affarer["saljkurs"] / affarer["kopkurs"] - 1
    vinst, forl = r[r > 0], r[r <= 0]
    return {
        "antal": len(affarer),
        "vinnare": len(vinst),
        "traffsakerhet": len(vinst) / len(affarer),
        "snittvinst": vinst.mean() if len(vinst) else np.nan,
        "snittforlust": forl.mean() if len(forl) else np.nan,
        "resultat_sek": affarer["resultat_sek"].sum(skipna=True),
        "snitt_dagar": (affarer["saljdatum"] - affarer["kopdatum"]).dt.days.mean(),
    }


# ---------------------------------------------------------------------------
# UTVECKLING (tidsviktad avkastning)
# ---------------------------------------------------------------------------
def utveckling(innehav: pd.DataFrame, affarer: pd.DataFrame, kurser: pd.DataFrame,
               typer: list[str] | None = None) -> pd.DataFrame:
    """Portföljens utveckling dag för dag, som tidsviktad avkastning.

    För varje handelsdag t och varje position som ägdes under dagen:
      början_t = gårdagens stängningsvärde (köpdagen: köpkurs × antal)
      slut_t   = dagens stängningsvärde    (säljdagen: säljkurs × antal)
    Dagens avkastning = Σ slut_t / Σ början_t − 1, och dagarna kedjas ihop.
    Köp och försäljningar påverkar alltså inte kurvan, bara hur aktierna rörde sig.
    Allt räknas om till SEK med dagens växelkurs.

    Returnerar DataFrame med 'varde_sek' (marknadsvärde vid dagens slut, sålt
    borträknat) och 'avkastning' (ackumulerad, 0.10 = +10 %).
    """
    tom = pd.DataFrame(columns=["varde_sek", "avkastning"])
    pos = [(h.ticker, h.typ, h.antal, h.kopdatum, h.kopkurs, h.valuta, None, None)
           for h in innehav.itertuples()]
    pos += [(a.ticker, a.typ, a.antal, a.kopdatum, a.kopkurs, a.valuta, a.saljdatum, a.saljkurs)
            for a in affarer.itertuples()]
    if typer:
        pos = [p for p in pos if p[1] in typer]
    pos = [p for p in pos if p[0] in kurser.columns and all(pd.notna(x) for x in (p[2], p[3], p[4]))]
    if not pos or kurser.empty:
        return tom

    alla_dagar = kurser.index
    borjan = pd.Series(0.0, index=alla_dagar)
    slutv = pd.Series(0.0, index=alla_dagar)
    varde = pd.Series(0.0, index=alla_dagar)
    forsta = None

    for ticker, _typ, antal, kopd, kopk, valuta, sald, salk in pos:
        s = kurser[ticker].ffill()
        fxt = fx_ticker(valuta)
        if fxt:
            if fxt not in kurser.columns:
                continue                       # valutakurs saknas
            fx = kurser[fxt].ffill().bfill()
        else:
            fx = pd.Series(1.0, index=alla_dagar)
        mask = (alla_dagar >= kopd) & s.notna().values
        if sald is not None:
            mask &= alla_dagar <= sald
        dagar = alla_dagar[mask]
        if len(dagar) == 0:
            continue
        stang = antal * s.loc[dagar] * fx.loc[dagar]          # stängningsvärde
        slut = stang.copy()
        if sald is not None:
            slut.iloc[-1] = antal * salk * fx.loc[dagar[-1]]   # säljdagen: säljkursen
        b = stang.shift(1)
        if kopd >= s.first_valid_index():
            b.iloc[0] = antal * kopk * fx.loc[dagar[0]]        # köpdagen: köpkursen
        else:
            b.iloc[0] = stang.iloc[0]   # köpt före kurshistoriken: räkna från första kända kurs
        borjan.loc[dagar] += b
        slutv.loc[dagar] += slut
        kvar = dagar[:-1] if sald is not None else dagar        # sålt: inte kvar vid dagens slut
        varde.loc[kvar] += stang.loc[kvar]
        forsta = dagar[0] if forsta is None else min(forsta, dagar[0])

    if forsta is None:
        return tom
    d = alla_dagar[alla_dagar >= forsta]
    r = (slutv.loc[d] / borjan.loc[d].where(borjan.loc[d] > 0) - 1).fillna(0.0)
    return pd.DataFrame({"varde_sek": varde.loc[d], "avkastning": (1 + r).cumprod() - 1}, index=d)


def indexutveckling(kurser: pd.DataFrame, datum: pd.DatetimeIndex, ticker: str = JAMFORELSEINDEX) -> pd.Series | None:
    """Indexets ackumulerade utveckling över samma dagar, från stängningen dagen före första dagen."""
    if ticker not in kurser.columns or len(datum) == 0:
        return None
    s = kurser[ticker].dropna()
    if s.empty:
        return None
    bas = s.asof(datum[0] - pd.Timedelta(days=1))
    if pd.isna(bas):
        bas = s.iloc[0]
    return (s.reindex(datum).ffill() / bas - 1)


def utveckling_markdown(innehav: pd.DataFrame, affarer: pd.DataFrame, kurser: pd.DataFrame) -> list[str]:
    """Kort sammanfattning av utveckling mot index och avslutade affärer, till veckorapporten."""
    rader = []
    namn = INDEXNAMN.get(JAMFORELSEINDEX, JAMFORELSEINDEX)
    for etikett, typer in (("Momentum", ["momentum"]), ("Hela portföljen", None)):
        u = utveckling(innehav, affarer, kurser, typer)
        if len(u) < 2:
            continue
        idx = indexutveckling(kurser, u.index)
        p = u["avkastning"].iloc[-1]
        i = idx.iloc[-1] if idx is not None and pd.notna(idx.iloc[-1]) else None
        txt = f"- **{etikett}** sedan {u.index[0].date()}: {p * 100:+.1f} %"
        if i is not None:
            txt += f" · {namn}: {i * 100:+.1f} % · skillnad {(p - i) * 100:+.1f} procentenheter"
        rader.append(txt)
    s = affarsstatistik(affarer)
    if s["antal"]:
        rader.append(f"- **Avslutade affärer:** {s['antal']} st, träffsäkerhet {s['traffsakerhet'] * 100:.0f} %, "
                     f"snittvinst {s['snittvinst'] * 100 if pd.notna(s['snittvinst']) else 0:+.1f} %, "
                     f"snittförlust {s['snittforlust'] * 100 if pd.notna(s['snittforlust']) else 0:+.1f} %, "
                     f"realiserat {s['resultat_sek']:,.0f} kr".replace(",", " "))
    return (["## Utveckling", ""] + rader) if rader else []
