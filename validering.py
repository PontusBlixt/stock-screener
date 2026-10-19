#!/usr/bin/env python3
"""
Validering av Insider 30
========================

Insider 30 gav +175 % i backtestet 2018–2026. Den här filen prövar om resultatet håller, med krav som
bestämdes 2026-10-19 INNAN valideringen kördes. Insider 30 själv ändras inte, oavsett utfall –
varianterna nedan finns bara för att se om resultatet är robust.

  1. Kontroll        Rapport-slump: lika många slumpvisa likvida aktier med rapport om 31–60 dagar,
                     samma säljregel. Insider 30 ska ha minst halva sitt försprång mot marknaden kvar
                     jämfört med kontrollen – annars är det rapporttimingen och inte insiderköpen.
  2. Rättvist index  Marknaden = lika viktat snitt av alla aktier i listan, MED utdelningar
                     (samma som i analyserna). Insider 30 ska slå den över hela perioden.
  3. Båda halvorna   Slå marknaden både före och efter 2023-01-01.
  4. Utan tur        Slå marknaden även när vinsten från de 10 bästa affärerna räknas bort.
  5. Fördröjning     Köp en vecka senare: ska fortfarande slå marknaden.
  6. Grannregler     Fönster 21–60 och 31–75 dagar, sälj 20 och 40 dagar efter rapport:
                     alla fyra ska slå marknaden.
  7. Framåttest      6–12 månader från 2026-10-24. Kan inte prövas här.

Resultat
  <utmapp>/analys/validering_ÅÅÅÅ-MM-DD.md och .html
  <utmapp>/backtest/validering/   portföljerna (visas inte på webbsidan)

Körning:
  ./venv/bin/python validering.py --utmapp /srv/storage/aktiedata     # 10–20 minuter (7 portföljer)
  ./venv/bin/python validering.py --demo
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
import strategier as st

MAPP = Path(__file__).resolve().parent
DELNING = pd.Timestamp("2023-01-01")
BAS = {"namn": "", "kalla": "insider", "salj_efter_rapport": 30, "stopp": None, "salj_rank": None,
       "krav_urval": False, "kort": ""}
VARIANTER = {
    "v_bas": ({"namn": "Insider 30 (som strategin)"}, {}),
    "v_slump": ({"namn": "Kontroll: Rapport-slump"}, {"slump": True}),
    "v_fordrojd": ({"namn": "Köp en vecka senare"}, {"fordrojning": 1}),
    "v_f21_60": ({"namn": "Fönster 21–60 dagar"}, {"fonster": (21, 60)}),
    "v_f31_75": ({"namn": "Fönster 31–75 dagar"}, {"fonster": (31, 75)}),
    "v_s20": ({"namn": "Sälj 20 dagar efter rapport", "salj_efter_rapport": 20}, {}),
    "v_s40": ({"namn": "Sälj 40 dagar efter rapport", "salj_efter_rapport": 40}, {}),
}


def avk(serie: pd.Series, fran=None, till=None) -> float:
    s = serie.dropna()
    if fran is not None:
        s = s[s.index >= fran]
    if till is not None:
        s = s[s.index < till]
    return float(s.iloc[-1] / s.iloc[0] - 1) if len(s) >= 2 else np.nan


def storsta_nedgang(serie: pd.Series) -> float:
    s = serie.dropna()
    return float((s / s.cummax() - 1).min()) if len(s) else np.nan


def utan_basta(rot: Path, sid: str, varde: pd.Series, n: int = 10) -> tuple[float, float]:
    """Avkastning om vinsten från de n bästa avslutade affärerna inte funnits (räknas bort från slutvärdet).
    Ungefärligt: pengarna hade annars legat kvar och vuxit, men det överskattar inte resultatet."""
    avsl = ih.las_affarer(st.mapp(rot, sid) / "avslutade.csv")
    if not len(avsl):
        return np.nan, 0.0
    basta = avsl["resultat_sek"].nlargest(n)
    slut = float(varde.iloc[-1]) - float(basta.clip(lower=0).sum())
    return slut / float(varde.iloc[0]) - 1, float(basta.sum())


def p(x, d=1):
    return "–" if x is None or pd.isna(x) else f"{x * 100:+.{d}f} %"


def main() -> int:
    a = argparse.ArgumentParser(description="Validering av Insider 30")
    a.add_argument("--utmapp", default=sc.KONFIG["utmapp"])
    a.add_argument("--tickers", default=str(MAPP / "tickers_se.csv"))
    a.add_argument("--demo", action="store_true")
    arg = a.parse_args()
    utmapp = Path(arg.utmapp).expanduser()

    for sid, (namn, _) in VARIANTER.items():   # tillfälliga strategier, bara i den här körningen
        st.STRATEGIER[sid] = {**BAS, **namn}
    data = bt.insiderdata(utmapp, arg.demo, Path(arg.tickers))
    lordagar = bt.insider_lordagar(data)
    rot = utmapp / "backtest" / ("validering_demo" if arg.demo else "validering")
    if rot.exists():
        shutil.rmtree(rot)
    rot.mkdir(parents=True)
    print(f"Validering {lordagar[0].date()} – {lordagar[-1].date()}: {len(lordagar)} veckor, "
          f"{len(VARIANTER)} portföljer ...", file=sys.stderr)
    bt.simulera_insider(rot, data, lordagar, {sid: v for sid, (_, v) in VARIANTER.items()})

    kurser = data["kurser"]
    res = {}
    for sid in VARIANTER:
        v = st.vardeserie(rot, sid, kurser)
        res[sid] = v["varde"] if len(v) else pd.Series(dtype=float)
    start = res["v_bas"].index[0]
    mark = ra.marknad(kurser)
    mark = mark[mark.index >= start]
    omx = kurser[ih.JAMFORELSEINDEX].ffill() if ih.JAMFORELSEINDEX in kurser.columns else None
    m_tot = avk(mark)
    bas = res["v_bas"]
    b_tot = avk(bas)

    # Kraven
    k = []
    s_tot = avk(res["v_slump"])
    forsprang, mot_kontroll = b_tot - m_tot, b_tot - s_tot
    k.append(("1. Slår kontrollen (Rapport-slump) med minst halva försprånget",
              forsprang > 0 and mot_kontroll >= 0.5 * forsprang,
              f"Insider 30 {p(b_tot)}, kontroll {p(s_tot)}, marknaden {p(m_tot)} → "
              f"{p(mot_kontroll)} av försprånget {p(forsprang)} kommer från insiderköpen"))
    k.append(("2. Slår marknaden med utdelningar över hela perioden", b_tot > m_tot,
              f"{p(b_tot)} mot {p(m_tot)}"))
    h1 = (avk(bas, till=DELNING), avk(mark, till=DELNING))
    h2 = (avk(bas, fran=DELNING), avk(mark, fran=DELNING))
    k.append(("3. Slår marknaden i båda halvorna", h1[0] > h1[1] and h2[0] > h2[1],
              f"före 2023: {p(h1[0])} mot {p(h1[1])} · från 2023: {p(h2[0])} mot {p(h2[1])}"))
    ub, bort = utan_basta(rot, "v_bas", bas)
    k.append(("4. Slår marknaden utan de 10 bästa affärerna", ub > m_tot,
              f"{p(ub)} mot {p(m_tot)} (de 10 bästa gav {bort:,.0f} kr)".replace(",", " ")))
    f_tot = avk(res["v_fordrojd"])
    k.append(("5. Slår marknaden med köp en vecka senare", f_tot > m_tot, f"{p(f_tot)} mot {p(m_tot)}"))
    gr = {sid: avk(res[sid]) for sid in ("v_f21_60", "v_f31_75", "v_s20", "v_s40")}
    k.append(("6. Alla fyra grannregler slår marknaden", all(x > m_tot for x in gr.values()),
              " · ".join(f"{VARIANTER[s][0]['namn']} {p(x)}" for s, x in gr.items())))
    klarade = sum(1 for _, ok, _ in k if ok)

    D = ["# Validering av Insider 30" + (" (DEMODATA)" if arg.demo else ""), "",
         f"{lordagar[0]:%Y-%m-%d} – {lordagar[-1]:%Y-%m-%d}, {len(lordagar)} veckor. Kraven bestämdes innan körningen. "
         "Marknaden = lika viktat snitt av alla aktier i listan, med utdelningar.", "",
         f"## Resultat: {klarade} av {len(k)} krav klarade", "",
         ra.tabell(["Krav", "Klarat", "Siffror"], [[n, "✅ ja" if ok else "❌ nej", t] for n, ok, t in k]), "",
         "7. Framåttest 6–12 månader från 2026-10-24 – kan bara avgöras med tiden.", "",
         "## Alla portföljer", ""]
    rader = []
    for sid, (namn, _) in VARIANTER.items():
        s = res[sid]
        avsl = ih.las_affarer(st.mapp(rot, sid) / "avslutade.csv")
        rader.append([namn["namn"], p(avk(s)), p(avk(s) - m_tot), p(avk(s, till=DELNING)), p(avk(s, fran=DELNING)),
                      p(storsta_nedgang(s)), str(len(avsl)),
                      p((avsl["resultat_sek"] > 0).mean(), 0).lstrip("+") if len(avsl) else "–"])
    rader.append(["Marknaden (lika viktad, med utdelningar)", p(m_tot), "", p(h1[1]), p(h2[1]), p(storsta_nedgang(mark)),
                  "", ""])
    if omx is not None:
        o = omx[omx.index >= start]
        rader.append(["OMX Stockholm PI (utan utdelningar)", p(avk(o)), "", p(avk(o, till=DELNING)), p(avk(o, fran=DELNING)),
                      p(storsta_nedgang(o)), "", ""])
    D += [ra.tabell(["Portfölj", "Avkastning", "Mot marknaden", "Före 2023", "Från 2023", "Största nedgång",
                     "Affärer", "Andel vinst"], rader), ""]

    # Likvid marknad: lika viktat snitt av bara de aktier strategin får köpa (omsättning ≥ 5 MSEK den dagen).
    # Bara information – kraven ovan använder hela marknaden, som bestämdes i förväg.
    likv = data["oms"].shift(1) >= sc.KONFIG["min_omsattning_sek"]
    r_dag = kurser.pct_change(fill_method=None)
    r_dag = r_dag[[c for c in r_dag.columns if not c.startswith("^")]]
    r_dag = r_dag.where(r_dag.abs() < 0.5).where(likv.reindex(columns=r_dag.columns, fill_value=False))
    mark_lik = (1 + r_dag.mean(axis=1, skipna=True).fillna(0)).cumprod()
    mark_lik = mark_lik[mark_lik.index >= start]

    vb = st.vardeserie(rot, "v_bas", kurser)
    logg = st.las_logg(st.mapp(rot, "v_bas"))
    D += ["## År för år", ""]
    ar_rader = []
    for ar in range(start.year, lordagar[-1].year + 1):
        fr, ti = pd.Timestamp(f"{ar}-01-01"), pd.Timestamp(f"{ar + 1}-01-01")
        b, s, m, ml = avk(bas, fr, ti), avk(res["v_slump"], fr, ti), avk(mark, fr, ti), avk(mark_lik, fr, ti)
        v = vb[(vb.index >= fr) & (vb.index < ti)]
        kassa = float((v["kassa"] / v["varde"]).mean()) if len(v) else np.nan
        kop = int(((logg["handling"] == "KÖP") & (logg["datum"] >= fr) & (logg["datum"] < ti)).sum())
        ar_rader.append([str(ar), p(b), p(s), p(m), p(ml), p(b - m), p(b - s), f"{kassa * 100:.0f} %", str(kop)])
    D += [ra.tabell(["År", "Insider 30", "Kontroll", "Marknaden", "Likvid marknad", "Mot marknaden", "Mot kontrollen",
                     "Kassa i snitt", "Köp"], ar_rader), ""]
    plus = sum(1 for r in ar_rader if r[5].startswith("+"))
    plus_k = sum(1 for r in ar_rader if r[6].startswith("+"))
    D += [f"Insider 30 slog marknaden **{plus} av {len(ar_rader)} år** och kontrollen **{plus_k} av {len(ar_rader)} år**. "
          "Likvid marknad = bara aktier med omsättning ≥ 5 MSEK/dag (de strategin får köpa) – visas som information, "
          "kraven använder hela marknaden. Kassa i snitt = andel av portföljen som inte var investerad.", ""]

    avsl = ih.las_affarer(st.mapp(rot, "v_bas") / "avslutade.csv")
    if len(avsl):
        top = avsl.nlargest(10, "resultat_sek")
        D += ["## De 10 bästa affärerna", "",
              ra.tabell(["Aktie", "Köpt", "Sålt", "Resultat"],
                        [[r.ticker, f"{pd.Timestamp(r.kopdatum):%Y-%m-%d}", f"{pd.Timestamp(r.saljdatum):%Y-%m-%d}",
                          f"{r.resultat_sek:,.0f} kr".replace(",", " ")] for r in top.itertuples()]), ""]
    D += ["## Läs med försiktighet", "",
          "- Samma data som insideranalysen hittade effekten i. Klarade krav visar att resultatet är robust, inte att det "
          "fortsätter – det avgör framåttestet.",
          "- Överlevnadsfel: bara bolag som är noterade idag ingår.",
          "- Kontrollen lottas en gång. Ligger den nära Insider 30 är det ett varningstecken även om kravet klaras."]
    md = "\n".join(D) + "\n"
    m = utmapp / "analys"
    m.mkdir(parents=True, exist_ok=True)
    idag = dt.date.today().isoformat()
    (m / f"validering_{idag}.md").write_text(md, encoding="utf-8")
    (m / f"validering_{idag}.html").write_text(ra._html(md, "Validering Insider 30"), encoding="utf-8")
    print(md)
    print(f"Sparat: {m / f'validering_{idag}.html'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
