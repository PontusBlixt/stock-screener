#!/usr/bin/env python3
"""
Variantprov – är en ändring av Insider 30 bättre än Insider 30?
===============================================================

Kör Insider 30 och varianter av den sida vid sida 2018–2026 med samma kod, kurser och kostnader som
backtestet, och prövar varje variant mot krav som bestämts INNAN körningen. Insider 30 själv ändras inte –
klarar en variant kraven kan den bli en ny strategi som framåttestas bredvid.

VARIANTER (2026-10-23, användarens idé: "går aktien upp mer än 20 % före rapport från köp, sälj före rapport,
annars sälj efter")
  vinst20_direkt  sälj vid första veckokörningen före rapporten då aktien stigit ≥ 20 % sedan köpet
  vinst20_sista   sälj vid sista veckokörningen före rapporten om aktien då stigit ≥ 20 % sedan köpet
  Annars som Insider 30: sälj vid veckokörningen 30 dagar efter rapporten.

KRAV för en variant (alla tre):
  1. Slår Insider 30 över hela perioden.
  2. Slår Insider 30 i minst 5 av 9 år.
  3. Största nedgången är högst 5 procentenheter värre än Insider 30:s.

Dessutom: hur många dagar det tar från insideraffären till att den publiceras (fördröjningen).

Resultat: <utmapp>/analys/variantprov_ÅÅÅÅ-MM-DD.md och .html. Portföljerna visas på webbsidan under
Strategier → Backtest → Variantprov.

Körning:
  ./venv/bin/python variantprov.py --utmapp /srv/storage/aktiedata     # några minuter
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import backtest as bt
import innehav as ih
import rapportanalys as ra
import screener as sc
import signaler as sg
import strategier as st

MAPP = Path(__file__).resolve().parent
# Varianterna finns i strategier.VISNING (visas under Backtest på webbsidan, handlar aldrig framåt)
VARIANTER = {sid: st.VISNING[sid] for sid in ("vp_insider30", "vp_vinst20_direkt", "vp_vinst20_sista")}
MIN_AR = 5
MAX_SAMRE_NEDGANG = 0.05


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


def fordrojning(utmapp: Path) -> pd.Series:
    """Kalenderdagar från insideraffären till publiceringen (giltiga aktieaffärer)."""
    df = sg.las_insyn(utmapp)
    if df.empty:
        return pd.Series(dtype=float)
    df = df[sg._ar_giltig(df)].dropna(subset=["publicerad", "transaktionsdatum"])
    d = (df["publicerad"] - df["transaktionsdatum"]).dt.days
    return d[(d >= 0) & (d < 365)]


def main() -> int:
    a = argparse.ArgumentParser(description="Varianter av Insider 30 mot förbestämda krav")
    a.add_argument("--utmapp", default=sc.KONFIG["utmapp"])
    a.add_argument("--tickers", default=str(MAPP / "tickers_se.csv"))
    a.add_argument("--demo", action="store_true")
    arg = a.parse_args()
    utmapp = Path(arg.utmapp).expanduser()

    data = bt.insiderdata(utmapp, arg.demo, Path(arg.tickers))
    lordagar = bt.insider_lordagar(data)
    rot = utmapp / "backtest" / ("variantprov_demo" if arg.demo else "variantprov")
    if rot.exists():
        shutil.rmtree(rot)
    rot.mkdir(parents=True)
    print(f"Variantprov {lordagar[0].date()} – {lordagar[-1].date()}: {len(lordagar)} veckor, {len(VARIANTER)} portföljer ...",
          file=sys.stderr)
    ih.skriv_kurser(rot / "kurser.csv", data["kurser"])
    bt.simulera_insider(rot, data, lordagar, {sid: {} for sid in VARIANTER})
    import json
    (rot / "info.json").write_text(json.dumps({
        "manader": round((lordagar[-1] - lordagar[0]).days / 30.44), "etikett": "Variantprov", "fran": str(lordagar[0].date()),
        "till": str(lordagar[-1].date()), "veckor": len(lordagar), "aktier": len(data["bolag_ticker"]), "demo": arg.demo,
        "skapad": dt.datetime.now().isoformat(timespec="minutes"), "strategier": list(VARIANTER), "hoppade_over": []},
        indent=2, ensure_ascii=False), encoding="utf-8")

    k = data["kurser"]
    serie = {sid: st.vardeserie(rot, sid, k)["varde"] for sid in VARIANTER}
    bas = serie["vp_insider30"]
    start = bas.index[0]
    mark = ra.marknad(k)
    mark = mark[mark.index >= start]
    ar = list(range(start.year, lordagar[-1].year + 1))

    def per_ar(s):
        return [avk(s, pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y + 1}-01-01")) for y in ar]

    bas_ar = per_ar(bas)
    D = ["# Variantprov: Insider 30 med försäljning före rapport" + (" (DEMODATA)" if arg.demo else ""), "",
         f"{lordagar[0]:%Y-%m-%d} – {lordagar[-1]:%Y-%m-%d}, {len(lordagar)} veckor. Samma köp som Insider 30 "
         "(insiderköp 31–60 dagar före rapport). Varianterna säljer före rapporten om aktien stigit minst 20 % sedan "
         "köpet, annars 30 dagar efter rapporten. Kraven bestämdes innan körningen.", ""]
    rader, krav_rader = [], []
    for sid, inst in VARIANTER.items():
        s = serie[sid]
        avsl = ih.las_affarer(st.mapp(rot, sid) / "avslutade.csv")
        fore = int(avsl["orsak"].astype(str).str.startswith("före rapporten").sum()) if len(avsl) else 0
        rader.append([inst["namn"], p(avk(s)), p(avk(s) - avk(mark)), p(nedgang(s)), str(len(avsl)),
                      f"{(avsl['resultat_sek'] > 0).mean() * 100:.0f} %" if len(avsl) else "–", str(fore)])
        if sid == "vp_insider30":
            continue
        y = per_ar(s)
        battre = sum(1 for v, b in zip(y, bas_ar) if pd.notna(v) and pd.notna(b) and v > b)
        k1, k2 = avk(s) > avk(bas), battre >= MIN_AR
        k3 = nedgang(s) >= nedgang(bas) - MAX_SAMRE_NEDGANG
        krav_rader.append([inst["namn"], f"{'✅' if k1 else '❌'} {p(avk(s))} mot {p(avk(bas))}",
                           f"{'✅' if k2 else '❌'} {battre} av {len(ar)} år",
                           f"{'✅' if k3 else '❌'} {p(nedgang(s))} mot {p(nedgang(bas))}",
                           "**klarar alla – kandidat till ny strategi**" if k1 and k2 and k3 else "nej"])
    rader.append(["Marknaden (lika viktad, med utdelningar)", p(avk(mark)), "", p(nedgang(mark)), "", "", ""])
    D += ["## Krav", "", ra.tabell(["Variant", "1. Slår Insider 30", f"2. Bättre minst {MIN_AR} år",
                                    "3. Nedgång högst 5 pe värre", "Resultat"], krav_rader), "",
          "## Portföljerna", "",
          ra.tabell(["Portfölj", "Avkastning", "Mot marknaden", "Största nedgång", "Affärer", "Andel vinst",
                     "Sålda före rapport"], rader), "",
          "## År för år", "",
          ra.tabell(["År"] + [v["namn"] for v in VARIANTER.values()] + ["Marknaden"],
                    [[str(y)] + [p(per_ar(serie[sid])[i]) for sid in VARIANTER] + [p(per_ar(mark)[i])]
                     for i, y in enumerate(ar)]), ""]
    d = fordrojning(utmapp)
    if len(d):
        D += ["## Hur snabbt publiceras insideraffärer?", "",
              f"Från affär till publicering hos FI ({len(d)} affärer): median **{d.median():.0f} dagar**, "
              f"75 % inom {d.quantile(0.75):.0f} dagar, 90 % inom {d.quantile(0.9):.0f} dagar. "
              "MAR kräver anmälan inom 3 arbetsdagar; längre tider är oftast sena anmälningar.", ""]
    D += ["## Läs med försiktighet", "",
          "- Samma data som Insider 30 togs fram på. En variant som klarar kraven ska framåttestas innan den används.",
          "- Två tolkningar av samma idé prövas – det ökar chansen att en ser bra ut av slump. Kräv tydlig skillnad.",
          "- Överlevnadsfel: bara bolag som är noterade idag ingår."]
    md = "\n".join(D) + "\n"
    m = utmapp / "analys"
    m.mkdir(parents=True, exist_ok=True)
    idag = dt.date.today().isoformat()
    (m / f"variantprov_{idag}.md").write_text(md, encoding="utf-8")
    (m / f"variantprov_{idag}.html").write_text(ra._html(md, "Variantprov"), encoding="utf-8")
    print(md)
    print(f"Sparat: {m / f'variantprov_{idag}.html'}", file=sys.stderr)
    return 0


p = ra.p

if __name__ == "__main__":
    sys.exit(main())
