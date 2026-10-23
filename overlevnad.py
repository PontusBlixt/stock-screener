#!/usr/bin/env python3
"""
Överlevnadsfel – hur många insiderköp gällde bolag som inte finns kvar?
=======================================================================

Backtesten använder dagens bolagslista (tickers_se.csv). Bolag som gått i konkurs, köpts upp eller avnoterats
sedan 2018 saknas, så strategierna kunde aldrig köpa dem. FI:s insynsregister har däremot ALLA bolag, så här
räknas hur stor del av insiderköpen (köpvågor per vecka, samma definition som Insider 30) som gällde bolag
utanför dagens lista.

Ett bolag som inte finns i dagens lista kan vara:
  - försvunnet (konkurs, uppköp, avnoterat)  → det är överlevnadsfelet
  - noterat på en annan marknad (First North, Spotlight, NGM) → aldrig med i vår lista, inget fel
FI anger inte vilket, så här används en tumregel: ett bolag utan någon insynsanmälan de senaste 12 månaderna
räknas som "troligen försvunnet". Bolag som fortfarande anmäler insynsaffärer räknas som "annan marknad".

Resultat: <utmapp>/analys/overlevnad_ÅÅÅÅ-MM-DD.md och .html

Körning:
  ./venv/bin/python overlevnad.py --utmapp /srv/storage/aktiedata
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import rapportanalys as ra
import screener as sc
import signaler as sg

MAPP = Path(__file__).resolve().parent
AKTIV_MANADER = 12
SPARR_VECKOR = 4


def main() -> int:
    a = argparse.ArgumentParser(description="Hur stort är överlevnadsfelet i insiderstrategierna?")
    a.add_argument("--utmapp", default=sc.KONFIG["utmapp"])
    a.add_argument("--tickers", default=str(MAPP / "tickers_se.csv"))
    arg = a.parse_args()
    utmapp, tickerfil = Path(arg.utmapp).expanduser(), Path(arg.tickers)

    df = sg.las_insyn(utmapp)
    if df.empty:
        print("Ingen insynsdata.", file=sys.stderr)
        return 1
    alla = df.dropna(subset=["publicerad"])
    sista_anmalan = alla.groupby(alla["lei"].where(alla["lei"] != "", alla["emittent"]))["publicerad"].max()
    df = alla[sg._ar_giltig(alla)].copy()
    print(f"{len(df)} köp/sälj av aktier. Räknar om till kronor och kopplar till dagens bolagslista ...", file=sys.stderr)
    df = sg.till_kronor(df, utmapp, tickerfil).dropna(subset=["belopp_sek"])
    df["kop"] = df["karaktar"].str.lower().isin(["förvärv", "acquisition"])
    df["id"] = df["lei"].where(df["lei"] != "", df["emittent"])
    df["i_listan"] = df["bolag"].notna()
    df["vecka"] = df["publicerad"].dt.to_period("W-SUN").dt.end_time.dt.normalize()
    df["netto"] = np.where(df["kop"], df["belopp_sek"], -df["belopp_sek"])

    # Köpvågor: veckor med nettoköp, inga nettoköp de 4 veckorna innan (samma idé som Insider 30)
    v = df.groupby(["id", "vecka"]).agg(netto=("netto", "sum"), i_listan=("i_listan", "max"),
                                         emittent=("emittent", "first")).reset_index()
    v = v[v["netto"] > 0].sort_values(["id", "vecka"])
    v["forra"] = v.groupby("id")["vecka"].shift(1)
    v = v[v["forra"].isna() | ((v["vecka"] - v["forra"]).dt.days > SPARR_VECKOR * 7)]
    grans = pd.Timestamp.today().normalize() - pd.DateOffset(months=AKTIV_MANADER)
    v["aktiv"] = v["id"].map(sista_anmalan) >= grans
    v["grupp"] = np.select([v["i_listan"], ~v["aktiv"]], ["i listan", "troligen försvunnet"], "annan marknad")
    v["ar"] = v["vecka"].dt.year

    t = v.pivot_table(index="ar", columns="grupp", values="id", aggfunc="count", fill_value=0)
    for k in ("i listan", "troligen försvunnet", "annan marknad"):
        if k not in t.columns:
            t[k] = 0
    t["andel"] = t["troligen försvunnet"] / (t["i listan"] + t["troligen försvunnet"]).replace(0, np.nan)
    tot = t[["i listan", "troligen försvunnet", "annan marknad"]].sum()
    andel_tot = tot["troligen försvunnet"] / (tot["i listan"] + tot["troligen försvunnet"])
    bolag = v.groupby("grupp")["id"].nunique()

    D = ["# Överlevnadsfel i insiderstrategierna", "",
         f"Köpvågor (veckor med nettoköp av insiders, inga nettoköp de {SPARR_VECKOR} veckorna innan) "
         f"{v['vecka'].min():%Y-%m-%d} – {v['vecka'].max():%Y-%m-%d}, uppdelat på om bolaget finns i dagens "
         "bolagslista. Ett bolag utanför listan räknas som *troligen försvunnet* om det inte gjort någon "
         f"insynsanmälan de senaste {AKTIV_MANADER} månaderna, annars som *annan marknad* (First North m.fl.).", "",
         "## År för år", "",
         ra.tabell(["År", "I dagens lista", "Troligen försvunna", "Annan marknad", "Försvunna av möjliga"],
                   [[str(int(ar)), str(int(r["i listan"])), str(int(r["troligen försvunnet"])), str(int(r["annan marknad"])),
                     f"{r['andel'] * 100:.0f} %" if pd.notna(r["andel"]) else "–"] for ar, r in t.iterrows()]), "",
         f"**Totalt: {andel_tot * 100:.0f} %** av köpvågorna i bolag som fanns på listan då gällde bolag som troligen "
         f"har försvunnit ({int(tot['troligen försvunnet'])} av {int(tot['i listan'] + tot['troligen försvunnet'])}). "
         f"Antal bolag: {int(bolag.get('i listan', 0))} i listan, {int(bolag.get('troligen försvunnet', 0))} troligen "
         f"försvunna, {int(bolag.get('annan marknad', 0))} på andra marknader.", "",
         "## Hur det ska läsas", "",
         "- Under ~5–10 %: överlevnadsfelet påverkar backtesten lite. Även om alla försvunna hade varit förluster "
         "flyttar de bara snittet något.",
         "- Över ~20 %: felet kan vara stort – backtesten ser bättre ut än verkligheten, och vi behöver försöka få fram "
         "kurser för de avnoterade bolagen.",
         "- Tumregeln är grov. \"Troligen försvunna\" innehåller också bolag på andra marknader som slutat anmäla, "
         "och bolag som bara bytt namn eller LEI. Uppköpta bolag är ofta inte förluster – uppköp sker oftast med premie.",
         "", "## De största troligen försvunna (flest köpvågor)", "",
         ra.tabell(["Emittent", "Köpvågor", "Senaste insynsanmälan"],
                   [[r.emittent, str(r.n), f"{sista_anmalan.get(r.id):%Y-%m-%d}"] for r in
                    v[v["grupp"] == "troligen försvunnet"].groupby("id").agg(emittent=("emittent", "first"), n=("id", "size"))
                    .reset_index().sort_values("n", ascending=False).head(25).itertuples()])]
    md = "\n".join(D) + "\n"
    m = utmapp / "analys"
    m.mkdir(parents=True, exist_ok=True)
    idag = dt.date.today().isoformat()
    (m / f"overlevnad_{idag}.md").write_text(md, encoding="utf-8")
    (m / f"overlevnad_{idag}.html").write_text(ra._html(md, "Överlevnadsfel"), encoding="utf-8")
    print(md)
    print(f"Sparat: {m / f'overlevnad_{idag}.html'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
