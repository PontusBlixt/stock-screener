#!/usr/bin/env python3
"""
Insideranalys – går aktier bättre efter att insiders köpt?
==========================================================

En händelsestudie på FI:s insynsregister (insyn/ÅÅÅÅ-MM.csv från insamling.py), med samma metod som
rapportanalys.py: avkastning JÄMFÖRT MED MARKNADEN (lika viktat snitt av alla aktier i listan).

Händelse = en vecka då insiders i ett bolag har KÖPT netto (köp minus sälj, publicerat den veckan), och
bolaget inte haft en köphändelse de 4 veckorna innan. Det är den första veckan i en ”köpvåg”.
Allt räknas på PUBLICERINGSDAGEN, inte affärsdagen – du kan bara agera när affären blivit offentlig.

  Köp  = stängningskursen första handelsdagen efter veckan (måndag) – samma rytm som veckorapporten.
  Mått = avkastning mot marknaden 20, 60 och 120 handelsdagar efter köpet, och 20 dagar före (köper
         insiders efter nedgångar?).

Händelserna delas upp efter det som var känt när veckan var slut (4 veckor bakåt):
  - storlek: nettoköp i procent av börsvärdet (stora ägare som köper för miljarder blir små i procent)
  - antal olika köpare
  - vem som köpte: vd/ekonomichef, styrelse, övriga
  - om köparen är en närstående (ofta ett bolag – storägare) eller personen själv
  - när i rapportcykeln: dagar sedan senaste rapport och dagar till nästa
  - kluster: första veckan då minst 3 olika insiders köpt inom 4 veckor
  - försäljningar som jämförelse (samma regler, nettosälj)
  - år för år

Data
  insyn/ÅÅÅÅ-MM.csv                från insamling.py (sedan 2016)
  rapporter/historik.csv           rapportdatum (för rapportcykeln)
  backtest/data/kurser.csv, volym.csv   5 års kurser (som backtest och rapportanalys)
  --ar 10                          10 års kurser i stället (analys/kurser_10y.csv). Hämtas första gången och
                                   sedan högst en gång i månaden (--ny tvingar fram en ny hämtning)

Resultat
  <utmapp>/analys/insideranalys_ÅÅÅÅ-MM-DD.md och .html
  <utmapp>/analys/insiderhandelser.csv     varje händelse med sina mått – underlag för backtest

OBS
  - Överlevnadsfel: bara bolag som är noterade idag ingår.
  - Börsvärdet bakåt i tiden uppskattas som dagens börsvärde × kursutvecklingen (nyemissioner syns inte).
  - Händelser samma vecka rör sig delvis tillsammans, så t-värdena överskattar säkerheten något.
  - Inget här påverkar rapporten, strategierna eller reglerna.

Körning:
  ./venv/bin/python insideranalys.py
  ./venv/bin/python insideranalys.py --ar 10
  ./venv/bin/python insideranalys.py --demo
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import innehav as ih
import rapportanalys as ra
import screener as sc
import signaler as sg

MAPP = Path(__file__).resolve().parent
KOSTNAD = ra.KOSTNAD_TUR_RETUR
HORISONTER = (20, 60, 120)
VECKOR_BAKAT = 4          # fönster för storlek och antal köpare
SPARR_VECKOR = 4          # ny köphändelse i samma bolag tidigast efter så här många veckor
KLUSTER_KOPARE = 3
KURS_MAX_ALDER = 30       # dagar – en analys över 10 år behöver inte färska kurser
MAX_ANDEL = 0.5           # nettoköp över halva börsvärdet på 4 veckor = fel i anmälan


# ---------------------------------------------------------------------------
# DATA
# ---------------------------------------------------------------------------
def roll(befattning: str) -> str:
    b = (befattning or "").lower()
    if any(o in b for o in ("verkställande", "vd", "ceo", "chief executive", "koncernchef")):
        return "vd"
    if any(o in b for o in ("finanschef", "cfo", "ekonomichef", "chief financial", "finansdirektör")):
        return "cfo"
    if any(o in b for o in ("styrelse", "ordförande", "board", "chair")):
        return "styrelse"
    return "ovrig"


def las_affarer(utmapp: Path, tickerfil: Path) -> pd.DataFrame:
    df = sg.las_insyn(utmapp)
    if df.empty:
        raise SystemExit(f"Ingen insynsdata i {utmapp / 'insyn'} – kör insamling.py först.")
    df = df[sg._ar_giltig(df)].dropna(subset=["publicerad"])
    print(f"{len(df)} köp/sälj av aktier, publicerade {df['publicerad'].min():%Y-%m-%d} – "
          f"{df['publicerad'].max():%Y-%m-%d}. Räknar om till kronor ...", file=sys.stderr)
    df = sg.till_kronor(df, utmapp, tickerfil)
    df = df.dropna(subset=["bolag", "belopp_sek"])
    df["kop"] = df["karaktar"].str.lower().isin(["förvärv", "acquisition"])
    df["roll"] = df["befattning"].map(roll)
    return df


def las_kurser(utmapp: Path, tickers: list[str], ar: int, demo: bool, ny: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    if demo or ar <= 5:
        return ra.las_kurser(utmapp, tickers, demo)
    m = utmapp / "analys"
    kf, vf = m / f"kurser_{ar}y.csv", m / f"volym_{ar}y.csv"
    alder = (dt.date.today() - dt.date.fromtimestamp(kf.stat().st_mtime)).days if kf.exists() and vf.exists() else None
    if alder is not None and alder < KURS_MAX_ALDER and not ny:
        print(f"Använder sparade {ar} års kurser ({alder} dagar gamla, hämtas om efter {KURS_MAX_ALDER} dagar "
              "eller med --ny).", file=sys.stderr)
        return ih.las_kurser(kf), ih.las_kurser(vf)
    print(f"Hämtar {ar} års kurser för {len(tickers)} aktier (några minuter) ...", file=sys.stderr)
    d = sc.hamta_kurser(tickers + [ih.JAMFORELSEINDEX], f"{ar}y")
    kurser = pd.DataFrame({t: x["Close"] for t, x in d.items()}).sort_index()
    volym = pd.DataFrame({t: x["Volume"] for t, x in d.items()}).sort_index()
    m.mkdir(parents=True, exist_ok=True)
    ih.skriv_kurser(kf, kurser)
    ih.skriv_kurser(vf, volym)
    return kurser, volym


def ticker_per_bolag(kurser: pd.DataFrame, volym: pd.DataFrame) -> dict[str, str]:
    """Ett aktieslag per bolag: det med högst omsättning."""
    ut, bast = {}, {}
    for t in kurser.columns:
        if t.startswith("^"):
            continue
        b = sc.bolagsnyckel(t)
        oms = float((kurser[t] * volym[t]).median()) if t in volym.columns else 0.0
        oms = oms if np.isfinite(oms) else 0.0
        if b not in bast or oms > bast[b]:
            ut[b], bast[b] = t, oms
    return ut


def rapportdagar(utmapp: Path, demo: bool, kurser: pd.DataFrame) -> dict[str, np.ndarray]:
    try:
        r = ra.demo_rapporter(kurser) if demo else ra.las_rapporter(utmapp)
    except SystemExit:
        return {}
    r["bolag"] = r["ticker"].map(sc.bolagsnyckel)
    return {b: np.sort(g["rapportdatum"].drop_duplicates().to_numpy(dtype="datetime64[D]"))
            for b, g in r.groupby("bolag")}


def demo_affarer(kurser: pd.DataFrame) -> pd.DataFrame:
    """Påhittade insynsaffärer, utan någon inbyggd effekt."""
    rng = np.random.default_rng(3)
    roller = ["Verkställande direktör", "Finanschef", "Styrelseledamot", "Styrelseordförande", "Försäljningschef"]
    rader = []
    for t in kurser.columns:
        if t.startswith("^"):
            continue
        for _ in range(rng.integers(3, 25)):
            d = kurser.index[rng.integers(0, len(kurser) - 1)]
            kop = rng.random() < 0.6
            rader.append({"publicerad": d, "transaktionsdatum": d - pd.Timedelta(days=2), "bolag": sc.bolagsnyckel(t),
                          "ticker": t, "person": f"Person {rng.integers(0, 6)}", "befattning": rng.choice(roller),
                          "narstaende": rng.random() < 0.2, "kop": kop,
                          "belopp_sek": float(rng.lognormal(13, 1.5))})
    df = pd.DataFrame(rader)
    df["roll"] = df["befattning"].map(roll)
    return df


# ---------------------------------------------------------------------------
# HÄNDELSER
# ---------------------------------------------------------------------------
def veckor(affarer: pd.DataFrame) -> pd.DataFrame:
    """En rad per bolag och vecka (måndag–söndag, efter publiceringsdag)."""
    a = affarer.copy()
    a["vecka"] = a["publicerad"].dt.to_period("W-SUN").dt.end_time.dt.normalize()   # veckans söndag
    a["kop_sek"] = np.where(a["kop"], a["belopp_sek"], 0.0)
    a["salj_sek"] = np.where(~a["kop"], a["belopp_sek"], 0.0)
    rang = {"vd": 3, "cfo": 3, "styrelse": 2, "ovrig": 1}
    ut = []
    for (b, v), g in a.groupby(["bolag", "vecka"]):
        k = g[g["kop"]]
        s = g[~g["kop"]]
        ut.append({"bolag": b, "vecka": v, "kop_sek": g["kop_sek"].sum(), "salj_sek": g["salj_sek"].sum(),
                   "kopare": frozenset(k["person"]), "saljare": frozenset(s["person"]),
                   "kop_roll": max(k["roll"], key=lambda r: rang[r]) if len(k) else "",
                   "salj_roll": max(s["roll"], key=lambda r: rang[r]) if len(s) else "",
                   "kop_bara_narstaende": bool(len(k)) and bool(k["narstaende"].all()),
                   "salj_bara_narstaende": bool(len(s)) and bool(s["narstaende"].all())})
    return pd.DataFrame(ut).sort_values(["bolag", "vecka"]).reset_index(drop=True)


def handelser(v: pd.DataFrame, kurser: pd.DataFrame, bolag_ticker: dict, borsvarde: dict,
              rapporter: dict) -> pd.DataFrame:
    kurser = kurser[~kurser.index.duplicated()].sort_index()
    m = ra.marknad(kurser).to_numpy()
    dagar = kurser.index
    ut = []
    for b, g in v.groupby("bolag", sort=False):
        t = bolag_ticker.get(b)
        if t is None:
            continue
        c = kurser[t].to_numpy()
        sista = pd.Series(c).dropna()
        sista = float(sista.iloc[-1]) if len(sista) else np.nan
        rd = rapporter.get(b)
        g = g.reset_index(drop=True)
        senast = {"kop": None, "salj": None, "kluster": None}
        for j, rad in g.iterrows():
            fonster = g[(g["vecka"] > rad.vecka - pd.Timedelta(weeks=VECKOR_BAKAT)) & (g["vecka"] <= rad.vecka)]
            kop4, salj4 = fonster["kop_sek"].sum(), fonster["salj_sek"].sum()
            kopare4 = frozenset().union(*fonster["kopare"])
            saljare4 = frozenset().union(*fonster["saljare"])
            netto_vecka = rad.kop_sek - rad.salj_sek
            typer = []
            if netto_vecka > 0 and kop4 - salj4 > 0:
                typer.append("kop")
            if netto_vecka < 0 and kop4 - salj4 < 0:
                typer.append("salj")
            if len(rad.kopare) and len(kopare4) >= KLUSTER_KOPARE and kop4 > salj4:
                typer.append("kluster")
            for typ in typer:
                sparr = SPARR_VECKOR * (2 if typ == "kluster" else 1)
                if senast[typ] is not None and (rad.vecka - senast[typ]).days < sparr * 7 + 1:
                    continue
                i = dagar.searchsorted(rad.vecka + pd.Timedelta(days=1))   # första handelsdagen efter veckan
                if i >= len(dagar) or (dagar[i] - rad.vecka).days > 7 or not np.isfinite(c[i]) or c[i] <= 0:
                    continue
                senast[typ] = rad.vecka

                def avk(a, bb):
                    if i + a < 0 or i + bb >= len(c):
                        return np.nan
                    ca, cb, ma, mb = c[i + a], c[i + bb], m[i + a], m[i + bb]
                    if not (np.isfinite(ca) and np.isfinite(cb) and np.isfinite(ma) and np.isfinite(mb)) or ca <= 0:
                        return np.nan
                    return (cb / ca - 1) - (mb / ma - 1)

                bv = borsvarde.get(b, np.nan) * c[i] / sista if np.isfinite(sista) and sista > 0 else np.nan
                kop = typ != "salj"
                e = {"typ": typ, "bolag": b, "ticker": t, "vecka": rad.vecka, "kopdag": dagar[i],
                     "netto_sek": kop4 - salj4, "kop_sek": kop4, "salj_sek": salj4,
                     "antal": len(kopare4) if kop else len(saljare4),
                     "roll": rad.kop_roll if kop else rad.salj_roll,
                     "bara_narstaende": rad.kop_bara_narstaende if kop else rad.salj_bara_narstaende,
                     "andel": abs(kop4 - salj4) / bv if bv and np.isfinite(bv) and bv > 0 else np.nan,
                     "fore20": avk(-20, 0)}
                for h in HORISONTER:
                    e[f"r{h}"] = avk(0, h)
                if rd is not None and len(rd):
                    d = np.datetime64(rad.vecka.date(), "D")
                    k = np.searchsorted(rd, d, side="right")
                    e["sedan_rapport"] = int((d - rd[k - 1]).astype(int)) if k > 0 else np.nan
                    e["till_rapport"] = int((rd[k] - d).astype(int)) if k < len(rd) else np.nan
                ut.append(e)
    df = pd.DataFrame(ut)
    if len(df):
        df["ar"] = df["kopdag"].dt.year
        orimlig = df["andel"] > MAX_ANDEL
        if orimlig.any():
            print(f"{int(orimlig.sum())} händelser har nettoköp/sälj över {MAX_ANDEL:.0%} av börsvärdet (fel i anmälan, "
                  "t.ex. pris i öre eller teckningsrätter) – storleken räknas inte för dem.", file=sys.stderr)
            df.loc[orimlig, "andel"] = np.nan
    return df


def nasta_rapport(df: pd.DataFrame, utmapp: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Lägger till reaktion och EPS-överraskning för bolagets NÄSTA rapport efter händelseveckan
    (från analys/rapporthandelser.csv som rapportanalys.py skriver)."""
    fil = utmapp / "analys" / "rapporthandelser.csv"
    if not fil.exists() or df.empty:
        return df, pd.DataFrame()
    r = pd.read_csv(fil, parse_dates=["rapportdatum", "dag0"])
    r["bolag"] = r["ticker"].map(sc.bolagsnyckel)
    r = r.dropna(subset=["rapportdatum"]).sort_values("rapportdatum")
    d = df.sort_values("vecka").copy()
    d = pd.merge_asof(d, r[["bolag", "rapportdatum", "reaktion", "overraskning_pct"]]
                      .rename(columns={"rapportdatum": "nasta_rapportdatum", "reaktion": "nasta_reaktion",
                                       "overraskning_pct": "nasta_overraskning"}),
                      left_on="vecka", right_on="nasta_rapportdatum", by="bolag", direction="forward",
                      allow_exact_matches=False, tolerance=pd.Timedelta(days=120))
    return d.sort_index(), r


# ---------------------------------------------------------------------------
# RAPPORT
# ---------------------------------------------------------------------------
p, tabell, stat = ra.p, ra.tabell, ra.stat


def rad_for(namn: str, g: pd.DataFrame) -> list[str]:
    s = {h: stat(g[f"r{h}"]) for h in HORISONTER}
    f = stat(g["fore20"])
    return ([namn, str(s[60]["n"]), p(f["snitt"])] + [p(s[h]["snitt"]) for h in HORISONTER]
            + [f"{s[60]['t']:.1f}" if pd.notna(s[60]["t"]) else "–", p(s[60]["median"]), p(s[60]["andel_pos"], 0, False),
               p(s[60]["snitt"] - KOSTNAD if pd.notna(s[60]["snitt"]) else np.nan)])


RUBRIK = ["", "Antal", "20 d före", "+20 d", "+60 d", "+120 d", "t (60 d)", "Median 60 d", "Andel upp", "60 d efter kostnad"]


def uppdelning(df: pd.DataFrame, nyckel, namn: dict | None = None, min_n: int = 20) -> list[list[str]]:
    rader = []
    for k, g in df.groupby(nyckel, observed=True, sort=True):
        if len(g) >= min_n:
            rader.append(rad_for(namn.get(k, str(k)) if namn else str(k), g))
    return rader


def kvintiler(df: pd.DataFrame, kol: str, fmt) -> list[list[str]]:
    d = df.dropna(subset=[kol]).copy()
    if len(d) < 100:
        return []
    d["q"] = pd.qcut(d[kol].rank(method="first"), 5, labels=False)
    rader = []
    for q, g in d.groupby("q"):
        etikett = f"{int(q) + 1}{' (minst)' if q == 0 else ' (störst)' if q == 4 else ''}: {fmt(g[kol].min())} … {fmt(g[kol].max())}"
        rader.append(rad_for(etikett, g))
    return rader


def kapa(x: pd.Series) -> pd.Series:
    """Kapar vid 1 % och 99 % så att enstaka extrema aktier inte styr ett år."""
    x = pd.Series(x, dtype=float)
    return x.clip(x.quantile(0.01), x.quantile(0.99)) if x.notna().sum() >= 20 else x


def andel_fmt(x):
    return f"{x * 100:.3f} %" if x < 0.001 else f"{x * 100:.2f} %"


def skriv(df: pd.DataFrame, utmapp: Path, demo: bool, rapporter: pd.DataFrame | None = None) -> tuple[Path, str]:
    kop, salj, kl = (df[df["typ"] == t] for t in ("kop", "salj", "kluster"))
    D = ["# Insideranalys" + (" (DEMODATA)" if demo else ""), "",
         f"{len(kop)} köpvågor och {len(salj)} säljvågor i {df['bolag'].nunique()} bolag, köpdagar "
         f"{df['kopdag'].min():%Y-%m-%d} – {df['kopdag'].max():%Y-%m-%d}. "
         "Köp sker första handelsdagen efter veckan då affären **publicerades**. All avkastning är **jämfört med "
         f"marknaden** (snittet av alla aktier i listan). Kostnad fram och tillbaka: {KOSTNAD * 100:.1f} %.", ""]

    def avsnitt(titel, rader, text=None):
        nonlocal D
        D += [f"## {titel}", ""]
        if text:
            D += [text, ""]
        D += [tabell(RUBRIK, rader) if rader else "För få händelser.", ""]

    avsnitt("1. Alla köpvågor mot alla säljvågor",
            [rad_for("Insiders köpte netto", kop), rad_for("Insiders sålde netto", salj)],
            "”20 d före” visar hur aktien gått innan – negativt betyder att insiders köper efter nedgångar.")
    avsnitt("2. Köp efter storlek (nettoköp i procent av börsvärdet, 4 veckor)",
            kvintiler(kop, "andel", andel_fmt))
    antal = kop.assign(k=np.minimum(kop["antal"], 4))
    avsnitt("3. Köp efter antal olika köpare (4 veckor)",
            uppdelning(antal, "k", {1: "1 köpare", 2: "2 köpare", 3: "3 köpare", 4: "4 eller fler"}, 10))
    avsnitt("4. Köp efter vem som köpte (högsta befattningen den veckan)",
            uppdelning(kop, "roll", {"vd": "Vd", "cfo": "Ekonomi-/finanschef", "styrelse": "Styrelse",
                                     "ovrig": "Övriga i ledningen"}, 10))
    avsnitt("5. Personen själv eller närstående",
            uppdelning(kop, "bara_narstaende", {False: "Personen själv (minst ett köp)",
                                                True: "Bara närstående (ofta ett ägarbolag)"}, 10))
    if "sedan_rapport" in kop.columns and kop["sedan_rapport"].notna().sum() >= 50:
        s = kop.assign(k=pd.cut(kop["sedan_rapport"], [-1, 14, 30, 60, 10_000],
                                labels=["0–14 dagar", "15–30 dagar", "31–60 dagar", "Mer än 60 dagar"]))
        avsnitt("6. Köp efter dagar sedan senaste rapport", uppdelning(s, "k", None, 10),
                "Stängd period är 30 dagar före rapport, så de flesta köp sker strax efter en rapport.")
        t = kop.assign(k=pd.cut(kop["till_rapport"], [-1, 30, 60, 10_000],
                                labels=["0–30 dagar (stängd period)", "31–60 dagar", "Mer än 60 dagar"]))
        avsnitt("7. Köp efter dagar till nästa rapport", uppdelning(t, "k", None, 10),
                "Din idé: stora köp strax innan den stängda perioden börjar (31–60 dagar före) – vet de något om rapporten?")
        if "till_rapport" in kop.columns:
            nara = kop[(kop["till_rapport"] > 30) & (kop["till_rapport"] <= 60)]
            avsnitt("7b. Köp 31–60 dagar före rapport, efter storlek", kvintiler(nara, "andel", andel_fmt)
                    if len(nara) >= 100 else [])
            ovr = kop[~kop.index.isin(nara.index)]
            rader = []
            for ar in sorted(kop["ar"].unique()):
                x, y = kapa(kop.loc[kop["ar"] == ar, "r60"]), None
                a, b = x[nara.loc[nara["ar"] == ar].index.intersection(x.index)], x[ovr.loc[ovr["ar"] == ar].index]
                if len(a) < 10:
                    continue
                rader.append([str(int(ar)), str(len(a.dropna())), p(a.mean()), p(a.median()), p((a.dropna() > 0).mean(), 0, False),
                              p(a.mean() - KOSTNAD), p(b.mean())])
            D += ["## 7c. Köp 31–60 dagar före rapport, år för år (60 dagar)", "",
                  tabell(["År", "Köp", "Snitt", "Median", "Andel upp", "Efter kostnad", "Övriga köp snitt"], rader)
                  if rader else "För få händelser.", ""]
            if rader:
                plus = sum(1 for r in rader if r[5].startswith("+"))
                D += [f"Köp 31–60 dagar före rapport slog marknaden efter kostnad **{plus} av {len(rader)} år**. "
                      "Snitten är kapade vid 1 % och 99 % inom året.", ""]
            if rapporter is not None and len(rapporter) and "nasta_reaktion" in kop.columns:
                stor = nara[nara["andel"] >= nara["andel"].quantile(0.6)] if nara["andel"].notna().sum() >= 50 else nara.iloc[0:0]
                snara = salj[(salj["till_rapport"] > 30) & (salj["till_rapport"] <= 60)] if "till_rapport" in salj.columns else salj.iloc[0:0]

                def rr(namn, reak, overr):
                    a, o = stat(reak), pd.Series(overr).dropna()
                    return [namn, str(a["n"]), p(a["snitt"], 2), f"{a['t']:.1f}" if pd.notna(a["t"]) else "–",
                            p(a["andel_pos"], 0, False), f"{o.median():+.1f} %" if len(o) else "–",
                            p((o > 0).mean(), 0, False) if len(o) else "–"]
                D += ["## 7d. Vet de något om rapporten? Nästa rapports reaktion", "",
                      "Kursreaktionen (dag −1 → +1 mot marknaden) och EPS-överraskningen på bolagets nästa rapport efter köpveckan.", "",
                      tabell(["", "Rapporter", "Reaktion", "t", "Andel upp", "EPS-överraskning median", "Andel slog förväntan"],
                             [rr("Alla rapporter", rapporter["reaktion"], rapporter["overraskning_pct"]),
                              rr("Efter köp 31–60 d före", nara["nasta_reaktion"], nara["nasta_overraskning"]),
                              rr("Efter stora köp 31–60 d före (största 40 %)", stor["nasta_reaktion"], stor["nasta_overraskning"]),
                              rr("Efter sälj 31–60 d före", snara["nasta_reaktion"], snara["nasta_overraskning"]),
                              rr("Efter alla köpvågor", kop["nasta_reaktion"], kop["nasta_overraskning"])]), ""]
    else:
        D += ["## 6–7. Rapportcykeln", "", "Inga rapportdatum hittades (rapporter/historik.csv).", ""]
    avsnitt(f"8. Kluster: första veckan då minst {KLUSTER_KOPARE} olika insiders köpt inom 4 veckor",
            [rad_for("Kluster", kl)] if len(kl) >= 5 else [])
    avsnitt("9. Försäljningar efter storlek", kvintiler(salj, "andel", andel_fmt),
            "Insiders säljer av många skäl (skatt, bostad, diversifiering) – en svagare signal väntas.")

    # År för år: alla köp och den största femtedelen
    D += ["## 10. År för år (köp, 60 dagar)", ""]
    rader = []
    q = kop.dropna(subset=["andel"])
    grans = q["andel"].quantile(0.8) if len(q) >= 100 else np.inf
    for ar, g in kop.groupby("ar"):
        if len(g) < 10:
            continue
        x = kapa(g["r60"]).dropna()
        st = x[g.loc[x.index, "andel"] >= grans]
        s = kapa(salj.loc[salj["ar"] == ar, "r60"])
        rader.append([str(int(ar)), str(len(x)), p(x.mean()), p(x.median()), str(len(st)), p(st.mean()), p(st.median()),
                      p(st.mean() - KOSTNAD if len(st) else np.nan), p(s.mean())])
    D += [tabell(["År", "Köp", "Snitt", "Median", "Största 20 %", "Snitt", "Median", "Efter kostnad", "Sälj snitt"], rader)
          if rader else "För få händelser.", ""]
    if rader:
        plus = sum(1 for r in rader if r[7].startswith("+"))
        D += ["Snitten är kapade vid 1 % och 99 % inom året.", "", f"Största femtedelen köp slog marknaden efter kostnad **{plus} av {len(rader)} år**.", ""]

    D += ["## Så läser du tabellerna", "",
          "- En effekt är trovärdig om den (1) växer i ordning med storleken eller antalet köpare, (2) syns i medianen "
          "och andelen upp och inte bara i snittet, och (3) håller de flesta år efter kostnad.",
          "- t över ~2 betyder att snittet troligen inte är slump, men händelser samma vecka rör sig ihop så kräv mer.",
          "- Överlevnadsfel: bara bolag som är noterade idag ingår. Bolag som gick i konkurs trots insiderköp saknas, "
          "vilket gör köpen något för bra.",
          "- Det här är underlag för en ny strategi (Insider v1) som först backtestas och sedan framåttestas – "
          "inte en strategi i sig."]
    md = "\n".join(D) + "\n"

    m = utmapp / "analys"
    m.mkdir(parents=True, exist_ok=True)
    idag = dt.date.today().isoformat()
    (m / f"insideranalys_{idag}.md").write_text(md, encoding="utf-8")
    (m / f"insideranalys_{idag}.html").write_text(ra._html(md, "Insideranalys"), encoding="utf-8")
    df.to_csv(m / "insiderhandelser.csv", index=False, float_format="%.6g")
    return m / f"insideranalys_{idag}.html", md


def main() -> int:
    a = argparse.ArgumentParser(description="Händelsestudie på insynsaffärer")
    a.add_argument("--utmapp", default=sc.KONFIG["utmapp"])
    a.add_argument("--tickers", default=str(MAPP / "tickers_se.csv"))
    a.add_argument("--ar", type=int, default=5, help="år med kurser (5 = samma som backtest, 10 = hämtar mer)")
    a.add_argument("--ny", action="store_true", help="hämta om 10-årskurserna även om de sparade är nya")
    a.add_argument("--demo", action="store_true")
    arg = a.parse_args()
    utmapp = Path(arg.utmapp).expanduser()
    tickerfil = Path(arg.tickers)
    tickers = list(sc.las_tickers(tickerfil)["ticker"])
    kurser, volym = las_kurser(utmapp, tickers, arg.ar, arg.demo, arg.ny)
    affarer = demo_affarer(kurser) if arg.demo else las_affarer(utmapp, tickerfil)
    borsvarde = ({sc.bolagsnyckel(t): 5e9 for t in kurser.columns} if arg.demo else sg.borsvarden(utmapp))
    v = veckor(affarer)
    df = handelser(v, kurser, ticker_per_bolag(kurser, volym), borsvarde, rapportdagar(utmapp, arg.demo, kurser))
    if df.empty:
        print("Inga händelser inom kursdatan.", file=sys.stderr)
        return 1
    df, rapporter = nasta_rapport(df, utmapp)
    fil, md = skriv(df, utmapp, arg.demo, rapporter)
    print(md)
    print(f"Sparat: {fil}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
