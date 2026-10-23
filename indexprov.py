#!/usr/bin/env python3
"""
Indexprov – index som kärna och guldstjärnorna som tillägg
==========================================================

Användarens upplägg (2026-10-23): pengarna ligger i en indexfond, och när en guldstjärna (Insider 30-signal)
dyker upp säljs fondandelar och aktien köps. När aktien säljs går pengarna tillbaka till fonden.

  Insider 30 (kassa kontant)  strategin som den är – oinvesterade pengar ligger som kontanter
  Insider 30 + index          samma köp och sälj, men oinvesterade pengar ligger i indexfonden
  Indexfond                   bara fonden, inga aktier
  Blandningar                 t.ex. 80 % i fonden och 20 % i "Insider 30 + index" (ingen ombalansering)

Indexfonden = OMX Stockholm PI (prisindex) + 3 % utdelning per år − 0,2 % avgift (strategier.INDEXFOND_*),
ungefär som Avanza Sweden eller Länsförsäkringar Sverige Index. Fondköp och fondsälj kostar inget courtage.

KRAV (bestämda innan körningen) för att "Insider 30 + index" ska vara upplägget att framåttesta:
  1. Slår Insider 30 (kassa kontant) över hela perioden.
  2. Slår indexfonden över hela perioden.
  3. Slår indexfonden i minst 5 av 9 år.

Resultat: <utmapp>/analys/indexprov_ÅÅÅÅ-MM-DD.md och .html, och på webbsidan under Strategier → Backtest → Indexprov.

Körning:
  ./venv/bin/python indexprov.py --utmapp /srv/storage/aktiedata     # några minuter
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import backtest as bt
import innehav as ih
import rapportanalys as ra
import screener as sc
import strategier as st

MAPP = Path(__file__).resolve().parent
SIDS = ("ix_insider30", "ix_insider30_index")
BLANDNINGAR = (0.9, 0.8, 0.5)     # andel i indexfonden, resten i "Insider 30 + index"
MIN_AR = 5
p = ra.p


def avk(s: pd.Series, fran=None, till=None) -> float:
    s = s.dropna()
    if fran is not None:
        s = s[s.index >= fran]
    if till is not None:
        s = s[s.index < till]
    return float(s.iloc[-1] / s.iloc[0] - 1) if len(s) >= 2 else np.nan


def nedgang(s: pd.Series) -> float:
    s = s.dropna()
    return float((s / s.cummax() - 1).min()) if len(s) else np.nan


def arlig(s: pd.Series) -> float:
    s = s.dropna()
    if len(s) < 2:
        return np.nan
    ar = (s.index[-1] - s.index[0]).days / 365.25
    return float((s.iloc[-1] / s.iloc[0]) ** (1 / ar) - 1) if ar > 0 else np.nan


def main() -> int:
    a = argparse.ArgumentParser(description="Index som kärna, Insider 30 som tillägg")
    a.add_argument("--utmapp", default=sc.KONFIG["utmapp"])
    a.add_argument("--tickers", default=str(MAPP / "tickers_se.csv"))
    a.add_argument("--demo", action="store_true")
    arg = a.parse_args()
    utmapp = Path(arg.utmapp).expanduser()

    data = bt.insiderdata(utmapp, arg.demo, Path(arg.tickers))
    if ih.JAMFORELSEINDEX not in data["kurser"].columns:
        print(f"{ih.JAMFORELSEINDEX} saknas i kurserna – kan inte räkna indexfonden.", file=sys.stderr)
        return 1
    lordagar = bt.insider_lordagar(data)
    rot = utmapp / "backtest" / ("indexprov_demo" if arg.demo else "indexprov")
    if rot.exists():
        shutil.rmtree(rot)
    rot.mkdir(parents=True)
    ih.skriv_kurser(rot / "kurser.csv", data["kurser"])
    print(f"Indexprov {lordagar[0].date()} – {lordagar[-1].date()}: {len(lordagar)} veckor ...", file=sys.stderr)
    bt.simulera_insider(rot, data, lordagar, {sid: {} for sid in SIDS})
    (rot / "info.json").write_text(json.dumps({
        "manader": round((lordagar[-1] - lordagar[0]).days / 30.44), "etikett": "Indexprov", "fran": str(lordagar[0].date()),
        "till": str(lordagar[-1].date()), "veckor": len(lordagar), "aktier": len(data["bolag_ticker"]), "demo": arg.demo,
        "skapad": dt.datetime.now().isoformat(timespec="minutes"), "strategier": list(SIDS), "hoppade_over": []},
        indent=2, ensure_ascii=False), encoding="utf-8")

    k = data["kurser"]
    serie = {sid: st.vardeserie(rot, sid, k)["varde"] for sid in SIDS}
    start = serie["ix_insider30"].index[0]
    fond = st.indexfond(k)
    fond = fond[fond.index >= start].reindex(serie["ix_insider30"].index).ffill()
    fond = fond / fond.iloc[0] * st.STARTKAPITAL
    ins_ix = serie["ix_insider30_index"]
    portf = {"Indexfond (100 %)": fond}
    for andel in BLANDNINGAR:
        portf[f"{andel * 100:.0f} % index + {(1 - andel) * 100:.0f} % Insider 30 + index"] = andel * fond + (1 - andel) * ins_ix
    portf["Insider 30 + index (100 %)"] = ins_ix
    portf["Insider 30, kassa kontant (100 %)"] = serie["ix_insider30"]

    # Kassan: hur stor del låg i fonden respektive i aktier
    vi = st.vardeserie(rot, "ix_insider30_index", k)
    i_fond = float((vi["kassa"] / vi["varde"]).mean()) if len(vi) else np.nan

    ar = list(range(start.year, lordagar[-1].year + 1))

    def per_ar(s):
        return [avk(s, pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y + 1}-01-01")) for y in ar]

    y_ix, y_fond = per_ar(ins_ix), per_ar(fond)
    battre = sum(1 for v, f in zip(y_ix, y_fond) if pd.notna(v) and pd.notna(f) and v > f)
    k1 = avk(ins_ix) > avk(serie["ix_insider30"])
    k2 = avk(ins_ix) > avk(fond)
    k3 = battre >= MIN_AR
    krav = [("1. Slår Insider 30 med kassa kontant", k1, f"{p(avk(ins_ix))} mot {p(avk(serie['ix_insider30']))}"),
            ("2. Slår indexfonden över hela perioden", k2, f"{p(avk(ins_ix))} mot {p(avk(fond))}"),
            (f"3. Slår indexfonden minst {MIN_AR} av {len(ar)} år", k3, f"{battre} av {len(ar)} år")]
    klarade = sum(1 for _, ok, _ in krav if ok)

    D = ["# Indexprov: index som kärna, guldstjärnorna som tillägg" + (" (DEMODATA)" if arg.demo else ""), "",
         f"{lordagar[0]:%Y-%m-%d} – {lordagar[-1]:%Y-%m-%d}, {len(lordagar)} veckor. Indexfonden = OMX Stockholm PI "
         f"+ {st.INDEXFOND_UTDELNING * 100:.0f} % utdelning − {st.INDEXFOND_AVGIFT * 100:.1f} % avgift per år. "
         "Kraven bestämdes innan körningen.", "",
         f"## Resultat: {klarade} av {len(krav)} krav klarade", "",
         ra.tabell(["Krav", "Klarat", "Siffror"], [[n, "✅ ja" if ok else "❌ nej", t] for n, ok, t in krav]), "",
         f"I ”Insider 30 + index” låg i snitt **{i_fond * 100:.0f} %** av pengarna i indexfonden och resten i aktier.", "",
         "## Upplägg", "",
         ra.tabell(["Upplägg", "Avkastning", "Per år", "Största nedgång"],
                   [[n, p(avk(s)), p(arlig(s)), p(nedgang(s))] for n, s in portf.items()]), "",
         "Blandningarna är t.ex. 80 % av sparandet i indexfonden och 20 % i en pott som följer guldstjärnorna "
         "(med pottens egen oanvända kassa också i fonden). Ingen ombalansering mellan delarna.", "",
         "## År för år", "",
         ra.tabell(["År", "Indexfond", "Insider 30 + index", "Insider 30 (kontant)", "80/20"],
                   [[str(y), p(y_fond[i]), p(y_ix[i]), p(per_ar(serie["ix_insider30"])[i]),
                     p(per_ar(portf[f"80 % index + 20 % Insider 30 + index"])[i])] for i, y in enumerate(ar)]), "",
         "## Läs med försiktighet", "",
         "- Indexfonden är uppskattad (prisindex + schablonutdelning). Verklig fond kan avvika någon procentenhet per år.",
         "- Samma data som Insider 30 togs fram på. Framåttestet avgör.",
         "- Överlevnadsfel: bara bolag som är noterade idag ingår (gäller inte indexfonden).",
         "- Insättningar och uttag under perioden är inte med – allt börjar med 100 000 kr."]
    md = "\n".join(D) + "\n"
    m = utmapp / "analys"
    m.mkdir(parents=True, exist_ok=True)
    idag = dt.date.today().isoformat()
    (m / f"indexprov_{idag}.md").write_text(md, encoding="utf-8")
    (m / f"indexprov_{idag}.html").write_text(ra._html(md, "Indexprov"), encoding="utf-8")
    print(md)
    print(f"Sparat: {m / f'indexprov_{idag}.html'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
