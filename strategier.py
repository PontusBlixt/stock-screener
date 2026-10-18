#!/usr/bin/env python3
"""
Virtuella strategiportföljer (pappershandel)
============================================

Varje strategi har en låtsasportfölj som köper och säljer exakt enligt reglerna,
en gång i veckan, på samma kurser som veckorapporten. Inga riktiga pengar och
inga ändringar i dina egna filer (innehav.csv, affarer.csv).

Regler, samma som du själv följer:
  - Säljer bara när reglerna säger sälj (innehav.analysera):
      SÄLJ                      -> säljs direkt (stopp, MA200, MA50 under MA200)
      SÄLJ VID MÅNADSGENOMGÅNG  -> säljs vid första körningen i en ny månad
      TRIMMA                    -> säljs ned till jämn vikt (1/20 av portföljen)
      BEHÅLL / BEVAKA           -> behålls
  - Köper bara aktier som är KÖPLÄGE: plats <= 20 och klarar urvalsreglerna (grön rad).
  - Högst 20 innehav. Finns det färre gröna än lediga platser köps bara de som finns,
    resten ligger kvar som kassa till nästa vecka.
  - En aktie som säljs köps inte tillbaka samma vecka.
  - Lika vikt: varje köp är 1/20 av portföljens värde (eller den kassa som finns kvar).
  - Courtage och spread dras på varje affär.

Strategier (bestämda i förväg, se README):
  momentum_v1  Momentum v1: veckorapporten som den är
  b            Momentum utan de 20 % dyraste i varje sektor (värderingspoäng)
  c            70 % momentum + 30 % värdering

Filer: <utmapp>/strategier/<id>/
  innehav.csv     nuvarande innehav (samma format som din innehav.csv)
  avslutade.csv   avslutade affärer (samma format som din affarer.csv)
  logg.csv        alla köp och försäljningar med kassaflöde
  tillstand.json  startkapital, kassa, senast körda rapport

Körning:
  Körs automatiskt i slutet av screener.py. Manuellt:
    ./venv/bin/python strategier.py            # kör senaste veckorapporten om den inte redan körts
    ./venv/bin/python strategier.py --status   # visa ställningen
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import innehav as ih

# ---------------------------------------------------------------------------
# INSTÄLLNINGAR – ändra här
# ---------------------------------------------------------------------------
STARTKAPITAL = 100_000.0
MAX_INNEHAV = 20
KOP_PLATS = 20                  # köp bara plats 1-20 (topp_n i screenern)
COURTAGE_PROCENT = 0.0025       # Avanza Mini: 0,25 % ...
COURTAGE_MINIMUM = 1.0          # ... dock minst 1 kr. Ändra om du har en annan courtageklass.
SPREAD = 0.001                  # halva skillnaden köp/sälj: köp 0,1 % över, sälj 0,1 % under stängning
MIN_KOP_SEK = 1_000.0           # köp inte rester mindre än så
NYCKELTAL_MAX_ALDER = 7         # dagar: B och C kräver nyckeltal högst så gamla

STRATEGIER = {
    "momentum_v1": {"namn": "Momentum v1", "kort": "Veckorapporten som den är", "nyckeltal": False},
    "b": {"namn": "B: utan dyraste 20 %", "kort": "Momentum, men hoppar över de 20 % dyraste i varje sektor",
          "nyckeltal": True},
    "c": {"namn": "C: 70/30 momentum/värde", "kort": "Rangordnas på 70 % momentumpoäng + 30 % värderingspoäng",
          "nyckeltal": True},
}

LOGG_KOLUMNER = ["datum", "rapport", "handling", "ticker", "antal", "kurs", "brutto", "courtage", "utdelning",
                 "kassaflode", "kassa_efter", "plats", "bas", "orsak"]
# bas = stängningskursen köpdagen. Yahoos kurser justeras i efterhand för utdelningar, så värdet räknas
# som bas × (justerad kurs idag / justerad kurs köpdagen). Då ingår utdelningarna (som om de återinvesterats)
# och värdet blir detsamma oavsett när kurser.csv senast hämtades om.


# ---------------------------------------------------------------------------
# FILER
# ---------------------------------------------------------------------------
def rotmapp(utmapp: Path, demo: bool = False) -> Path:
    return Path(utmapp) / ("strategier_demo" if demo else "strategier")


def mapp(utmapp: Path, sid: str, demo: bool = False) -> Path:
    return rotmapp(utmapp, demo) / sid


def las_tillstand(m: Path) -> dict | None:
    f = m / "tillstand.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def skriv_tillstand(m: Path, t: dict) -> None:
    m.mkdir(parents=True, exist_ok=True)
    tmp = m / "tillstand.tmp"
    tmp.write_text(json.dumps(t, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, m / "tillstand.json")


def las_logg(m: Path) -> pd.DataFrame:
    f = m / "logg.csv"
    if not f.exists():
        return pd.DataFrame(columns=LOGG_KOLUMNER)
    df = pd.read_csv(f).reindex(columns=LOGG_KOLUMNER)
    df["datum"] = pd.to_datetime(df["datum"])
    df["utdelning"] = df["utdelning"].fillna(0.0)
    return df


def _skriv_csv(df: pd.DataFrame, fil: Path) -> None:
    tmp = fil.with_suffix(".tmp")
    df.to_csv(tmp, index=False, float_format="%.10g")
    os.replace(tmp, fil)


# ---------------------------------------------------------------------------
# RANGORDNING PER STRATEGI
# ---------------------------------------------------------------------------
def las_nyckeltal(utmapp: Path, datum: dt.date) -> pd.DataFrame | None:
    """Senaste nyckeltalsfilen på eller före datum (högst NYCKELTAL_MAX_ALDER dagar gammal), med poäng."""
    import nyckeltal as nk
    m = nk.mapp(utmapp)
    filer = [f for f in sorted(m.glob("????-??-??.csv")) if dt.date.fromisoformat(f.stem) <= datum] if m.exists() else []
    if not filer or (datum - dt.date.fromisoformat(filer[-1].stem)).days > NYCKELTAL_MAX_ALDER:
        return None
    df = pd.read_csv(filer[-1])
    df["datum"] = pd.to_datetime(df["datum"])
    if "fx_rapport" in df.columns:
        df = nk.korrigera(df)
    df = nk.poang(df)
    # Värderingspoängens percentil inom gruppen, räknat på unika bolag (100 = billigast)
    unika = df.dropna(subset=["varde_poang"]).drop_duplicates("bolag").copy()
    unika["varde_pct"] = unika.groupby("grupp")["varde_poang"].rank(pct=True) * 100
    df = df.merge(unika[["bolag", "varde_pct"]], on="bolag", how="left")
    ut = df[["ticker", "varde_poang", "varde_pct", "grupp"]].copy()
    ut.attrs["fil"] = filer[-1].stem
    return ut


def rangordna(sid: str, tabell: pd.DataFrame, nyckeltal: pd.DataFrame | None) -> pd.DataFrame:
    """Returnerar tabellen med strategins egna kolumner godkand, plats och poang (samma namn som
    screenern, så att innehav.analysera kan användas oförändrad)."""
    t = tabell.copy()
    if sid == "momentum_v1":
        return t
    if nyckeltal is None:
        raise ValueError("nyckeltal saknas")
    t = t.merge(nyckeltal[["ticker", "varde_pct"]], on="ticker", how="left")
    if sid == "b":
        # De 20 % dyraste i sin sektor räknas som "klarar inte filtret". Saknas poäng tas aktien inte bort.
        dyr = t["varde_pct"].notna() & (t["varde_pct"] <= 20)
        t["orsak"] = np.where(t["godkand"] & dyr, "bland de 20 % dyraste i sektorn", t["orsak"].fillna(""))
        t["godkand"] = t["godkand"] & ~dyr
    elif sid == "c":
        # Saknas värderingspoäng räknas den som medel (50)
        t["poang"] = np.where(t["godkand"], 0.7 * t["poang"] + 0.3 * t["varde_pct"].fillna(50), np.nan)
    t = t.sort_values(["godkand", "poang"], ascending=[False, False]).reset_index(drop=True)
    t["plats"] = np.nan
    t.loc[t["godkand"], "plats"] = range(1, int(t["godkand"].sum()) + 1)
    t["urval"] = t["godkand"] & t["urval"].astype(bool)
    return t


# ---------------------------------------------------------------------------
# AFFÄRER
# ---------------------------------------------------------------------------
def courtage(brutto: float) -> float:
    return max(COURTAGE_MINIMUM, brutto * COURTAGE_PROCENT) if brutto > 0 else 0.0


def _kurs(kurser: pd.DataFrame, ticker: str) -> float | None:
    if ticker not in kurser.columns:
        return None
    s = kurser[ticker].dropna()
    return float(s.iloc[-1]) if len(s) else None


def _tillvaxt(kurser: pd.DataFrame, ticker: str, fran) -> float:
    """Justerad kurs senaste dagen / justerad kurs dagen fran (kursutveckling inklusive utdelningar)."""
    if ticker not in kurser.columns:
        return 1.0
    s = kurser[ticker].dropna()
    fore = s[s.index <= pd.Timestamp(fran)]
    if not len(s) or not len(fore) or fore.iloc[-1] <= 0:
        return 1.0
    return float(s.iloc[-1] / fore.iloc[-1])


def _lot(logg: pd.DataFrame, ticker: str) -> tuple[float, pd.Timestamp] | None:
    """(bas, köpdag) för det öppna köpet av ticker – det senaste KÖP i loggen."""
    if not len(logg):
        return None
    r = logg[(logg["ticker"] == ticker) & (logg["handling"] == "KÖP")]
    if not len(r):
        return None
    return float(r["bas"].iloc[-1]), pd.Timestamp(r["datum"].iloc[-1])


def aktievarde(kurser: pd.DataFrame, logg: pd.DataFrame, ticker: str) -> float | None:
    """Värde per aktie idag inklusive utdelningar sedan köpet."""
    lot = _lot(logg, ticker)
    if lot is None or not np.isfinite(lot[0]):
        return _kurs(kurser, ticker)
    return lot[0] * _tillvaxt(kurser, ticker, lot[1])


def portfoljvarde(innehav: pd.DataFrame, kurser: pd.DataFrame, logg: pd.DataFrame | None = None) -> float:
    v = 0.0
    logg = logg if logg is not None else pd.DataFrame(columns=LOGG_KOLUMNER)
    for h in innehav.itertuples():
        k = aktievarde(kurser, logg, h.ticker)
        v += (k if k is not None else h.kopkurs) * h.antal
    return v


def kor_strategi(sid: str, utmapp: Path, rapport: str, tabell: pd.DataFrame, kurser: pd.DataFrame,
                 nyckeltal: pd.DataFrame | None, demo: bool = False) -> str:
    """Kör en veckas beslut för en strategi. Returnerar en rad text för loggen."""
    m = mapp(utmapp, sid, demo)
    t = las_tillstand(m)
    rdatum = dt.date.fromisoformat(rapport)
    if t and t["senaste_rapport"] >= rapport:
        return f"{sid}: {rapport} redan körd"
    if STRATEGIER[sid]["nyckeltal"] and nyckeltal is None:
        return f"{sid}: väntar – inga nyckeltal från de senaste {NYCKELTAL_MAX_ALDER} dagarna"
    if t is None:
        t = {"startkapital": STARTKAPITAL, "kassa": STARTKAPITAL, "start": rapport, "senaste_rapport": "",
             "senaste_manad": rapport[:7], "namn": STRATEGIER[sid]["namn"]}
        manadsgenomgang = False  # inga innehav att granska första gången
    else:
        manadsgenomgang = rapport[:7] != t["senaste_manad"]

    k = kurser[kurser.index <= pd.Timestamp(rdatum)]
    # Handelsdagen = senaste dagen med kurs för de flesta aktierna. kurser.csv kan ha en rad för
    # idag eller en helgdag med bara några få aktier (efter "Uppdatera kurser" på webbsidan).
    breda = k[[c for c in tabell["ticker"] if c in k.columns]].notna().sum(axis=1)
    handelsdag = breda[breda >= 0.5 * breda.max()].index.max() if len(breda) and breda.max() > 0 else k.index.max()
    t.setdefault("start_handelsdag", str(handelsdag.date()))
    rank = rangordna(sid, tabell, nyckeltal)
    inn = ih.las_innehav(m / "innehav.csv")
    avsl = ih.las_affarer(m / "avslutade.csv")
    logg = las_logg(m)
    kassa = float(t["kassa"])
    nya_logg, nya_avsl = [], []

    def logga(handling, ticker, antal, kurs, brutto, ct, flode, plats, orsak, bas=np.nan, utd=0.0):
        nya_logg.append({"datum": handelsdag, "rapport": rapport, "handling": handling,
                         "ticker": ticker, "antal": antal, "kurs": kurs, "brutto": brutto, "courtage": ct,
                         "utdelning": utd, "kassaflode": flode, "kassa_efter": kassa, "plats": plats,
                         "bas": bas, "orsak": orsak})

    def alla_logg() -> pd.DataFrame:
        return pd.concat([logg, pd.DataFrame(nya_logg, columns=LOGG_KOLUMNER)], ignore_index=True) if nya_logg else logg

    def salj(idx: int, antal: float, orsak: str, plats) -> None:
        nonlocal kassa
        h = inn.loc[idx]
        stang = _kurs(k, h["ticker"])
        if stang is None:
            return
        pris = stang * (1 - SPREAD)
        brutto = pris * antal
        varde = (aktievarde(k, logg, h["ticker"]) or stang) * (1 - SPREAD) * antal  # inkl. utdelningar
        utd = varde - brutto
        ct = courtage(brutto)
        netto = varde - ct
        kassa += netto
        if utd > 0.5:
            orsak += f" (inkl. utdelning {utd:,.0f} kr)".replace(",", " ")
        nya_avsl.append({"ticker": h["ticker"], "typ": "momentum", "kopdatum": h["kopdatum"], "kopkurs": h["kopkurs"],
                         "saljdatum": handelsdag, "saljkurs": netto / antal, "antal": antal, "valuta": "SEK",
                         "fx_kop": 1.0, "fx_salj": 1.0, "resultat_sek": netto - h["kopkurs"] * antal,
                         "orsak": orsak, "notering": ""})
        logga("SÄLJ", h["ticker"], antal, pris, brutto, ct, netto, plats, orsak, utd=utd)
        inn.loc[idx, "antal"] = h["antal"] - antal

    # 1. Försäljningar enligt säljreglerna
    analys = ih.analysera(inn, k, rank) if len(inn) else []
    for i, a in enumerate(analys):
        b = a.get("beslut")
        if b == "SÄLJ":
            salj(i, inn.loc[i, "antal"], "; ".join(a["signaler"]), a.get("plats"))
        elif b == "SÄLJ VID MÅNADSGENOMGÅNG" and manadsgenomgang:
            salj(i, inn.loc[i, "antal"], "månadsgenomgång: " + "; ".join(a["manad"]), a.get("plats"))
    inn = inn[inn["antal"] > 1e-9].reset_index(drop=True)

    # 2. Trimning ned till jämn vikt
    if len(inn):
        total = kassa + portfoljvarde(inn, k, logg)
        mal = total / MAX_INNEHAV
        for i, a in enumerate(ih.analysera(inn, k, rank)):
            if a.get("beslut") == "TRIMMA" and pd.notna(a.get("varde_sek")):
                antal = math.floor((a["varde_sek"] - mal) / a["kurs"])
                if antal >= 1:
                    salj(i, antal, f"trimning: {a['andel'] * 100:.0f} % av portföljen", a.get("plats"))
        inn = inn[inn["antal"] > 1e-9].reset_index(drop=True)

    # 3. Köp: gröna rader (plats <= 20 och urval), som inte redan ägs, i platsordning
    agda_bolag = {_bolag(x) for x in inn["ticker"]}
    kandidater = rank[(rank["plats"] <= KOP_PLATS) & rank["urval"].astype(bool)].sort_values("plats")
    salda = {r["ticker"] for r in nya_logg if r["handling"] == "SÄLJ"}  # köps inte tillbaka samma vecka
    kandidater = kandidater[~kandidater["bolag"].fillna(kandidater["ticker"]).isin(agda_bolag)
                            & ~kandidater["ticker"].isin(set(inn["ticker"]) | salda)]
    lediga = MAX_INNEHAV - len(inn)
    total = kassa + portfoljvarde(inn, k, logg)
    mal = total / MAX_INNEHAV
    nya_rader = []
    for r in kandidater.head(max(lediga, 0)).itertuples():
        stang = _kurs(k, r.ticker)
        if stang is None or stang <= 0:
            continue
        pris = stang * (1 + SPREAD)
        belopp = min(mal, kassa)
        antal = math.floor(belopp / (pris * (1 + COURTAGE_PROCENT)))
        while antal >= 1 and pris * antal + courtage(pris * antal) > kassa:
            antal -= 1
        brutto = pris * antal
        if antal < 1 or brutto < MIN_KOP_SEK:
            break
        ct = courtage(brutto)
        kassa -= brutto + ct
        nya_rader.append({"ticker": r.ticker, "typ": "momentum", "kopdatum": handelsdag,
                          "kopkurs": (brutto + ct) / antal, "antal": antal, "valuta": "SEK",
                          "notering": f"plats {int(r.plats)}"})
        logga("KÖP", r.ticker, antal, pris, brutto, ct, -(brutto + ct), int(r.plats), f"plats {int(r.plats)}, grön rad",
              bas=stang)
    if nya_rader:
        inn = pd.concat([inn, pd.DataFrame(nya_rader)], ignore_index=True)

    # Spara
    m.mkdir(parents=True, exist_ok=True)
    ih.skriv_innehav(m / "innehav.csv", inn)
    if nya_avsl:
        avsl = pd.concat([avsl, pd.DataFrame(nya_avsl)], ignore_index=True)
    ih.skriv_affarer(m / "avslutade.csv", avsl)
    logg = alla_logg()
    ut = logg.copy()
    ut["datum"] = pd.to_datetime(ut["datum"]).dt.strftime("%Y-%m-%d")
    _skriv_csv(ut.reindex(columns=LOGG_KOLUMNER), m / "logg.csv")
    t.update(kassa=kassa, senaste_rapport=rapport, senaste_manad=rapport[:7], senaste_handelsdag=str(handelsdag.date()))
    if nyckeltal is not None and STRATEGIER[sid]["nyckeltal"]:
        t["senaste_nyckeltal"] = nyckeltal.attrs.get("fil", "")
    skriv_tillstand(m, t)
    kop = sum(1 for r in nya_logg if r["handling"] == "KÖP")
    sal = len(nya_logg) - kop
    return (f"{sid}: {kop} köp, {sal} försäljningar{' (månadsgenomgång)' if manadsgenomgang else ''}, "
            f"{len(inn)} innehav, kassa {kassa:,.0f} kr".replace(",", " "))


def _bolag(ticker: str) -> str:
    import screener as sc
    return sc.bolagsnyckel(ticker)


def kor_alla(utmapp: Path, demo: bool = False, rapportfil: Path | None = None) -> list[str]:
    """Kör alla strategier för veckorapporter som inte körts än (äldst först).
    En strategi som startas nu börjar på den senaste rapporten – historiken spelas inte upp i efterhand."""
    utmapp = Path(utmapp)
    filer = [rapportfil] if rapportfil else sorted(utmapp.glob("screener_????-??-??.csv"))
    if not filer:
        return ["Ingen veckorapport hittades – kör screener.py först."]
    kurser = ih.las_kurser(utmapp / "kurser.csv")
    ut = []
    for sid in STRATEGIER:
        t = las_tillstand(mapp(utmapp, sid, demo))
        if t is None:
            att_kora = filer[-1:]
        else:
            att_kora = [f for f in filer if f.stem.removeprefix("screener_") > t["senaste_rapport"]]
        if not att_kora:
            ut.append(f"{sid}: inget nytt")
        for f in att_kora:
            rapport = f.stem.removeprefix("screener_")
            try:
                tabell = pd.read_csv(f)
                if "bolag" not in tabell.columns:
                    tabell["bolag"] = tabell["ticker"].map(_bolag)
                nk = las_nyckeltal(utmapp, dt.date.fromisoformat(rapport)) if STRATEGIER[sid]["nyckeltal"] else None
                ut.append(kor_strategi(sid, utmapp, rapport, tabell, kurser, nk, demo))
            except Exception as fel:  # en strategi som felar får inte stoppa de andra
                ut.append(f"{sid}: FEL {type(fel).__name__}: {fel}")
    return ut


# ---------------------------------------------------------------------------
# UTVÄRDERING (används av webbsidan)
# ---------------------------------------------------------------------------
def vardeserie(utmapp: Path, sid: str, kurser: pd.DataFrame, demo: bool = False) -> pd.DataFrame:
    """Portföljens värde dag för dag sedan start: kassa + innehav till stängningskurs.
    Räknas om från loggen, så den är alltid i fas med affärerna."""
    m = mapp(utmapp, sid, demo)
    t = las_tillstand(m)
    if t is None:
        return pd.DataFrame(columns=["varde", "kassa", "avkastning"])
    logg = las_logg(m)
    start = pd.Timestamp(t["start_handelsdag"])
    dagar = kurser.index[kurser.index >= start]
    if len(dagar) == 0:
        return pd.DataFrame(columns=["varde", "kassa", "avkastning"])
    kapital = float(t["startkapital"])
    kassa = pd.Series(kapital, index=dagar)
    innehav_v = pd.Series(0.0, index=dagar)
    oppna: dict[str, dict] = {}
    lotter = []
    for r in logg.itertuples():
        d = dagar >= r.datum
        kassa[d] += r.kassaflode
        if r.handling == "KÖP":
            oppna[r.ticker] = {"ticker": r.ticker, "fran": r.datum, "bas": r.bas,
                               "antal": pd.Series(0.0, index=dagar)}
            oppna[r.ticker]["antal"][d] += r.antal
            lotter.append(oppna[r.ticker])
        elif r.ticker in oppna:
            oppna[r.ticker]["antal"][d] -= r.antal
    for lot in lotter:
        if lot["ticker"] not in kurser.columns:
            continue
        s = kurser[lot["ticker"]].ffill().reindex(dagar)  # senast kända kurs även dagar med glesa rader
        fore = kurser[lot["ticker"]].dropna()
        fore = fore[fore.index <= lot["fran"]]
        if not len(fore):
            continue
        innehav_v += (lot["antal"] * lot["bas"] * s / float(fore.iloc[-1])).fillna(0)
    df = pd.DataFrame({"varde": kassa + innehav_v, "kassa": kassa})
    df["avkastning"] = df["varde"] / kapital - 1
    return df


def oversikt(utmapp: Path, kurser: pd.DataFrame, demo: bool = False) -> list[dict]:
    ut = []
    for sid, s in STRATEGIER.items():
        m = mapp(utmapp, sid, demo)
        t = las_tillstand(m)
        rad = {"id": sid, **s, "startad": t is not None, "tillstand": t or {}}
        if t:
            v = vardeserie(utmapp, sid, kurser, demo)
            inn = ih.las_innehav(m / "innehav.csv")
            avsl = ih.las_affarer(m / "avslutade.csv")
            logg = las_logg(m)
            rad.update(serie=v, varde=float(v["varde"].iloc[-1]) if len(v) else t["kassa"],
                       avkastning=float(v["avkastning"].iloc[-1]) if len(v) else 0.0,
                       kassa=float(t["kassa"]), antal=len(inn), stat=ih.affarsstatistik(avsl),
                       courtage=float(logg["courtage"].sum()) if len(logg) else 0.0, affarer=len(logg))
        ut.append(rad)
    return ut


def main() -> int:
    import screener as sc
    p = argparse.ArgumentParser(description="Virtuella strategiportföljer")
    p.add_argument("--utmapp", default=os.environ.get("SCREENER_UTMAPP", sc.KONFIG["utmapp"]))
    p.add_argument("--demo", action="store_true", help="skriv till strategier_demo/")
    p.add_argument("--status", action="store_true", help="visa ställningen, kör inget")
    a = p.parse_args()
    utmapp = Path(a.utmapp).expanduser()
    if not a.status:
        for rad in kor_alla(utmapp, a.demo):
            print(rad)
    k = ih.las_kurser(utmapp / "kurser.csv")
    for r in oversikt(utmapp, k, a.demo):
        if r["startad"]:
            print(f"  {r['namn']:<26} {r['varde']:>10,.0f} kr  {r['avkastning'] * 100:+6.2f} %  "
                  f"{r['antal']:>2} innehav  kassa {r['kassa']:>9,.0f} kr  sedan {r['tillstand']['start']}"
                  .replace(",", " "))
        else:
            print(f"  {r['namn']:<26} inte startad")
    return 0


if __name__ == "__main__":
    sys.exit(main())
