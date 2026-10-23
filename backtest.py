#!/usr/bin/env python3
"""
Backtest av strategierna
========================

Spelar upp de prisbaserade strategierna bakåt i tiden, vecka för vecka, med exakt samma kod
som de virtuella portföljerna (strategier.py) och samma rangordning som veckorapporten
(screener.py). Varje lördag i perioden räknas topplistan fram med bara de kurser som fanns
då, och boten fattar sina beslut. Daglig stopp kontrolleras varje vardag.

Bara strategier som bygger på kurser och omsättning kan testas bakåt. Strategier som behöver
nyckeltal (B, C, Momentum + kvalitet) hoppas över – nyckeltalen sparas först sedan oktober 2026,
och dagens värden skulle ge ett fusktest.

VIKTIGT – resultatet är en indikation, inte ett facit:
  - Överlevnadsfel: tickerlistan innehåller bara bolag som är noterade idag. Bolag som gått i
    konkurs eller avnoterats under perioden saknas, så alla strategier ser något bättre ut.
  - En enda period säger lite. Kör gärna flera längder (6, 12, 24, 36 månader).
  - Ändra inte strategierna efter vad backtestet visar och kör om tills det ser bra ut – då
    anpassar du strategin till historiken och den slutar fungera framåt.

Körning (på servern, tar några minuter första gången):
  ./venv/bin/python backtest.py                # senaste 6 månaderna
  ./venv/bin/python backtest.py --manader 24   # senaste 2 åren
  ./venv/bin/python backtest.py --demo         # påhittade kurser, utan internet
  ./venv/bin/python backtest.py --insider      # Insider 30/60 sedan 2018 (insynsdatan, 10 års kurser)

Resultatet sparas i <utmapp>/backtest/<n>m/ och visas på webbsidan under Strategier → Backtest.
Kurserna sparas i <utmapp>/backtest/data/ och återanvänds samma dag.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

import innehav as ih
import screener as sc
import strategier as st

MAPP = Path(__file__).resolve().parent
MAX_MANADER = 45          # 5 års historik minus de ~13 månader som krävs innan första beslutet


def prisbaserade() -> list[str]:
    return [sid for sid in st.STRATEGIER if not st.inst(sid)["nyckeltal"] and not st.ar_insider(sid)]


def insiderstrategier() -> list[str]:
    return [sid for sid in st.STRATEGIER if st.ar_insider(sid)]


def hamta_data(utmapp: Path, tickers: list[str], demo: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Stängningskurser och volym, en kolumn per ticker. Cachas per dag i backtest/data/."""
    if demo:
        d = sc.demodata(tickers + [ih.JAMFORELSEINDEX])
        return (pd.DataFrame({t: x["Close"] for t, x in d.items()}),
                pd.DataFrame({t: x["Volume"] for t, x in d.items()}))
    m = utmapp / "backtest" / "data"
    kf, vf = m / "kurser.csv", m / "volym.csv"
    idag = dt.date.today()
    if kf.exists() and vf.exists() and dt.date.fromtimestamp(kf.stat().st_mtime) == idag:
        print("Använder dagens sparade kurser.", file=sys.stderr)
        return ih.las_kurser(kf), ih.las_kurser(vf)
    print(f"Hämtar 5 års kurser för {len(tickers)} aktier (några minuter) ...", file=sys.stderr)
    d = sc.hamta_kurser(tickers + [ih.JAMFORELSEINDEX], "5y")
    kurser = pd.DataFrame({t: x["Close"] for t, x in d.items()}).sort_index()
    volym = pd.DataFrame({t: x["Volume"] for t, x in d.items()}).sort_index()
    m.mkdir(parents=True, exist_ok=True)
    ih.skriv_kurser(kf, kurser)
    ih.skriv_kurser(vf, volym)
    return kurser, volym


def topplista(kurser: pd.DataFrame, volym: pd.DataFrame, namn: dict, dag: pd.Timestamp) -> pd.DataFrame | None:
    """Veckorapportens tabell som den hade sett ut efter stängning dag."""
    k, v = kurser[kurser.index <= dag], volym[volym.index <= dag]
    rader = []
    for t in namn:
        if t not in k.columns:
            continue
        df = pd.DataFrame({"Close": k[t], "Volume": v[t] if t in v.columns else 0.0}).dropna()
        n = sc.berakna_nyckeltal(df) if len(df) else None
        if n:
            rader.append({"ticker": t, "namn": namn[t], **n})
    if not rader:
        return None
    tabell = pd.DataFrame(rader)
    if sc.KONFIG["ett_aktieslag_per_bolag"]:
        tabell = sc.ett_aktieslag(tabell, [])
    tabell = sc.filtrera_och_ranka(tabell)
    if "bolag" not in tabell.columns:
        tabell["bolag"] = tabell["ticker"].map(sc.bolagsnyckel)
    return tabell


def kor(utmapp: Path, manader: int, demo: bool = False, tickerfil: Path | None = None) -> Path:
    lista = sc.las_tickers(tickerfil or MAPP / "tickers_se.csv")
    namn = dict(zip(lista["ticker"], lista["namn"]))
    kurser, volym = hamta_data(utmapp, list(namn), demo)

    sista = kurser.index.max()
    start = sista - pd.DateOffset(months=manader)
    tidigast = kurser.index[min(sc.KONFIG["min_dagar"] + 5, len(kurser.index) - 1)]
    if start < tidigast:
        print(f"För lite historik för {manader} månader – börjar {tidigast.date()} i stället.", file=sys.stderr)
        start = tidigast
    lordagar = pd.date_range(start, sista, freq="W-SAT")
    if sista.weekday() == 4 and (not len(lordagar) or lordagar[-1] < sista):
        lordagar = lordagar.append(pd.DatetimeIndex([sista + pd.Timedelta(days=1)]))
    vardagar = kurser.index[(kurser.index > lordagar[0]) & (kurser.index <= sista)]

    namn_kor = f"{manader}m" + ("_demo" if demo else "")
    rot = utmapp / "backtest" / namn_kor
    if rot.exists():
        shutil.rmtree(rot)
    rot.mkdir(parents=True)
    ih.skriv_kurser(rot / "kurser.csv", kurser)

    sids = prisbaserade()
    dagliga = [s for s in sids if st.inst(s)["dagligt"]]
    print(f"Backtest {lordagar[0].date()} – {sista.date()}: {len(lordagar)} veckor, {len(namn)} aktier, "
          f"strategier: {', '.join(sids)}", file=sys.stderr)
    t0 = time.time()
    tabell = None
    for i, lor in enumerate(lordagar, 1):
        tabell = topplista(kurser, volym, namn, lor)
        if tabell is None:
            continue
        rapport = lor.date().isoformat()
        for sid in sids:
            st.kor_strategi(sid, rot, rapport, tabell, kurser, None)
        nasta = lordagar[i] if i < len(lordagar) else sista + pd.Timedelta(days=1)
        for dag in vardagar[(vardagar > lor) & (vardagar < nasta)]:
            for sid in dagliga:
                st.kor_strategi(sid, rot, dag.date().isoformat(), None, kurser, None, dagligt=True)
        if i % 4 == 0 or i == len(lordagar):
            print(f"  vecka {i}/{len(lordagar)} ({time.time() - t0:.0f} s)", file=sys.stderr)

    if tabell is not None:
        tabell.to_csv(rot / f"screener_{lordagar[-1].date()}.csv", index=False)
    info = {"manader": manader, "fran": str(lordagar[0].date()), "till": str(sista.date()),
            "veckor": len(lordagar), "aktier": len(namn), "skapad": dt.datetime.now().isoformat(timespec="minutes"),
            "demo": demo, "strategier": sids,
            "hoppade_over": [s for s in st.STRATEGIER if s not in sids]}
    (rot / "info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    return rot


def insiderdata(utmapp: Path, demo: bool = False, tickerfil: Path | None = None) -> dict:
    """Allt insiderbacktestet behöver: 10-årskurser, volym, insynsaffärer, rapportkalender, börsvärden,
    omsättning (snitt 20 dagar) och ett aktieslag per bolag (det mest omsatta)."""
    import insideranalys as ia
    import rapportanalys as ra
    import signaler as sg
    tickerfil = tickerfil or MAPP / "tickers_se.csv"
    lista = sc.las_tickers(tickerfil)
    kurser, volym = ia.las_kurser(utmapp, list(lista["ticker"]), 10, demo)
    kurser = kurser[~kurser.index.duplicated()].sort_index()
    volym = volym[~volym.index.duplicated()].sort_index().reindex(kurser.index)
    if demo:
        aff = ia.demo_affarer(kurser).sort_values("publicerad").reset_index(drop=True)
        r = ra.demo_rapporter(kurser)
        r["bolag"] = r["ticker"].map(sc.bolagsnyckel)
        kal = {b: np.unique(g["rapportdatum"].to_numpy(dtype="datetime64[D]")) for b, g in r.groupby("bolag")}
        bv = {sc.bolagsnyckel(t): 5e9 for t in kurser.columns}
    else:
        print("Läser insynsaffärer och rapportdatum ...", file=sys.stderr)
        aff, kal, bv = sg.insider_affarer(utmapp, tickerfil), sg.rapportkalender(utmapp), sg.borsvarden(utmapp)
    if aff.empty:
        raise SystemExit("Ingen insynsdata – kör insamling.py först.")
    return {"kurser": kurser, "volym": volym, "aff": aff, "kal": kal, "bv": bv,
            "oms": (kurser * volym).rolling(20, min_periods=10).mean(),
            "bolag_ticker": ia.ticker_per_bolag(kurser, volym)}


def insider_lordagar(data: dict, manader: int | None = None) -> pd.DatetimeIndex:
    kurser, aff = data["kurser"], data["aff"]
    sista = kurser.index.max()
    start = max(aff["publicerad"].min() + pd.Timedelta(weeks=6), kurser.index[25])
    if manader:
        start = max(start, sista - pd.DateOffset(months=manader))
    lordagar = pd.date_range(start.normalize(), sista, freq="W-SAT")
    if sista.weekday() == 4 and (not len(lordagar) or lordagar[-1] < sista):
        lordagar = lordagar.append(pd.DatetimeIndex([sista + pd.Timedelta(days=1)]))
    return lordagar


def likvida(data: dict, lor: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(alla, likvida) – en rad per bolag med tickern och omsättningen på lördagens senaste handelsdag."""
    dag = data["kurser"].index[data["kurser"].index <= lor]
    o = data["oms"].loc[dag[-1]] if len(dag) else pd.Series(dtype=float)
    tabell = pd.DataFrame([{"ticker": t, "bolag": b, "omsattning_sek": o.get(t, np.nan)}
                           for b, t in data["bolag_ticker"].items()])
    return tabell, tabell[tabell["omsattning_sek"] >= sc.KONFIG["min_omsattning_sek"]]


def simulera_insider(rot: Path, data: dict, lordagar: pd.DatetimeIndex, varianter: dict) -> int:
    """Kör insiderstrategier vecka för vecka i rot/strategier/. varianter = {sid: {"fonster": (31, 60),
    "fordrojning": 0 (veckor), "slump": False, "filter": None}}. Strategierna måste finnas i strategier.STRATEGIER.
    filter = funktion (kandidater, lördag, sid) -> de kandidater som får köpas.
    slump=True: lika många slumpvisa likvida aktier som insidersignalerna, med rapport inom samma fönster."""
    import zlib
    import signaler as sg
    t0, antal_kand, tidigare = time.time(), 0, {}
    for i, lor in enumerate(lordagar, 1):
        tabell, lik = likvida(data, lor)
        if not len(tabell):
            continue
        rapport = lor.date().isoformat()
        cache = {}
        for sid, v in varianter.items():
            fonster = v.get("fonster", sg.INSIDER_RAPPORT_FONSTER)
            if fonster not in cache:
                cache[fonster] = sg.insider_kandidater(data["aff"], data["kal"], lor, lik, data["bv"], data["kurser"],
                                                       fonster=fonster)
            kand = cache[fonster]
            if v.get("slump"):
                pool = []
                for r in lik.itertuples():
                    nr = sg.nasta_rapport(data["kal"], r.bolag, lor)
                    if nr is not None and fonster[0] <= (nr - lor).days <= fonster[1]:
                        pool.append({"ticker": r.ticker, "bolag": r.bolag, "netto_sek": 0.0, "kopare": 0,
                                     "nasta_rapport": nr, "dagar_till_rapport": (nr - lor).days, "andel": np.nan})
                rng = np.random.default_rng(zlib.crc32(f"{sid}{rapport}".encode()))
                pool = pd.DataFrame(pool, columns=kand.columns)
                kand = pool.iloc[rng.permutation(len(pool))[:len(kand)]].reset_index(drop=True)
            if v.get("filter") is not None and len(kand):   # t.ex. maskininlärningens urval (insiderml.py)
                kand = v["filter"](kand, lor, sid).reset_index(drop=True)
            f = v.get("fordrojning", 0)
            if f:
                kö = tidigare.setdefault(sid, [])
                kö.append(kand)
                kand = kö.pop(0) if len(kö) > f else kand.iloc[0:0]
            if sid == next(iter(varianter)):
                antal_kand += len(kand)
            st.kor_insider(sid, rot, rapport, lik if len(lik) else tabell, data["kurser"], kand, data["kal"])
        if i % 52 == 0 or i == len(lordagar):
            print(f"  vecka {i}/{len(lordagar)} ({time.time() - t0:.0f} s)", file=sys.stderr)
    return antal_kand


def kor_insider(utmapp: Path, demo: bool = False, tickerfil: Path | None = None, manader: int | None = None) -> Path:
    """Backtest av insiderstrategierna så långt bakåt som insynsdatan räcker (sedan 2018).
    Använder insideranalysens 10-årskurser (analys/kurser_10y.csv) – hämtas om de saknas.
    Varje lördag: kandidater från insyn publicerad fredag–torsdag, nästa rapport om 31–60 dagar,
    omsättning (snitt 20 dagar) minst 5 MSEK. Ett aktieslag per bolag (det mest omsatta)."""
    data = insiderdata(utmapp, demo, tickerfil)
    lordagar = insider_lordagar(data, manader)
    sista = data["kurser"].index.max()
    manader_tot = round((lordagar[-1] - lordagar[0]).days / 30.44)
    namn_kor = f"insider_{manader_tot}m" + ("_demo" if demo else "")
    rot = utmapp / "backtest" / namn_kor
    if rot.exists():
        shutil.rmtree(rot)
    rot.mkdir(parents=True)
    ih.skriv_kurser(rot / "kurser.csv", data["kurser"])
    sids = insiderstrategier()
    print(f"Backtest insider {lordagar[0].date()} – {sista.date()}: {len(lordagar)} veckor, "
          f"{len(data['aff'])} insynsaffärer, strategier: {', '.join(sids)}", file=sys.stderr)
    antal_kand = simulera_insider(rot, data, lordagar, {sid: {} for sid in sids})
    info = {"manader": manader_tot, "etikett": f"Insider {manader_tot} mån", "fran": str(lordagar[0].date()),
            "till": str(sista.date()), "veckor": len(lordagar), "aktier": len(data["bolag_ticker"]),
            "skapad": dt.datetime.now().isoformat(timespec="minutes"), "demo": demo, "strategier": sids,
            "kandidater": antal_kand, "hoppade_over": []}
    (rot / "info.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    return rot


def sammanfatta(rot: Path) -> None:
    k = ih.las_kurser(rot / "kurser.csv")
    rader = [r for r in st.oversikt(rot, k) if r["startad"]]
    idx = None
    if rader and ih.JAMFORELSEINDEX in k.columns:
        s = k[ih.JAMFORELSEINDEX].ffill()
        s = s[s.index >= rader[0]["serie"].index[0]].dropna()
        idx = float(s.iloc[-1] / s.iloc[0] - 1) if len(s) else None
    info = json.loads((rot / "info.json").read_text(encoding="utf-8"))
    print(f"\nBacktest {info['fran']} – {info['till']} ({info['veckor']} veckor)\n")
    for r in sorted(rader, key=lambda r: -r["avkastning"]):
        print(f"  {r['namn']:<24} {r['avkastning'] * 100:+7.1f} %  {r['affarer']:>4} affärer  "
              f"träffsäkerhet {r['stat'].get('traffsakerhet', float('nan')) * 100 if r['stat']['antal'] else float('nan'):5.1f} %")
    if idx is not None:
        print(f"  {ih.INDEXNAMN.get(ih.JAMFORELSEINDEX, ih.JAMFORELSEINDEX):<24} {idx * 100:+7.1f} %  (utan utdelningar)")
    if info["hoppade_over"]:
        print(f"\nKan inte testas bakåt (kräver historiska nyckeltal): {', '.join(info['hoppade_over'])}")
    print("OBS: bara bolag som är noterade idag ingår – resultaten är något för bra (överlevnadsfel).")


def main() -> int:
    p = argparse.ArgumentParser(description="Backtest av de prisbaserade strategierna")
    p.add_argument("--manader", type=int, default=6, help=f"hur långt tillbaka (1–{MAX_MANADER})")
    p.add_argument("--utmapp", default=sc.KONFIG["utmapp"])
    p.add_argument("--tickers", default=str(MAPP / "tickers_se.csv"))
    p.add_argument("--demo", action="store_true", help="påhittade kurser, utan internet")
    p.add_argument("--insider", action="store_true",
                   help="testa insiderstrategierna så långt bakåt som insynsdatan räcker (--manader begränsar)")
    a = p.parse_args()
    if a.insider:
        utmapp = Path(a.utmapp).expanduser()
        rot = kor_insider(utmapp, a.demo, Path(a.tickers), a.manader if "--manader" in sys.argv else None)
        sammanfatta(rot)
        print(f"\nSparat i {rot} – visas på webbsidan under Strategier → Backtest.")
        return 0
    if not 1 <= a.manader <= MAX_MANADER:
        print(f"--manader måste vara 1–{MAX_MANADER}", file=sys.stderr)
        return 1
    utmapp = Path(a.utmapp).expanduser()
    rot = kor(utmapp, a.manader, a.demo, Path(a.tickers))
    sammanfatta(rot)
    print(f"\nSparat i {rot} – visas på webbsidan under Strategier → Backtest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
