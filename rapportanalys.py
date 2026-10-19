#!/usr/bin/env python3
"""
Rapportanalys – hur beter sig aktier runt sina rapporter?
==========================================================

En händelsestudie: för varje rapport mäts aktiens avkastning JÄMFÖRT MED MARKNADEN i några fönster
runt rapportdagen (dag 0 = första handelsdagen på eller efter rapportdatumet).

Marknaden = snittet av alla aktier i tickerlistan (lika vikt), räknat på samma utdelningsjusterade kurser.
^OMXSPI används inte som jämförelse: det är ett prisindex utan utdelningar och domineras av storbolag,
så nästan alla aktier skulle se ut att slå det lite i varje fönster även när ingenting händer.

  före       dag −20 → −1        uppgång inför rapporten
  reaktion   dag −1 → +1         själva reaktionen (de flesta svenska bolag rapporterar före börsen öppnar)
  drift60    dag +1 → +60        driften efter rapporten (köp dagen efter, håll 60 handelsdagar)
  kvartal    dag +1 → 5 dagar före nästa rapport   "köp efter bra rapport, sälj före nästa"
  omsättning snitt dag −5 → −1 delat med snitt dag −60 → −21  (ovanlig omsättning före rapport)

Frågor:
  1. Rapportpremie: ger rapportfönstret högre avkastning än vanliga dagar, och hur mycket större svängningar?
  2. Drift: fortsätter aktier som överraskat positivt att gå bättre än marknaden? Rapporterna delas i fem lika
     stora grupper efter EPS-överraskning (analytikernas förväntan mot utfall) och efter kursreaktionen.
  3. Stabilitet: samma sak år för år.
  4. Ovanlig omsättning före rapport: ger den bättre eller sämre reaktion?

Data
  rapporter/historik.csv            från insamling.py
  backtest/data/kurser.csv, volym.csv   5 års kurser och omsättning – hämtas om om de inte är från idag

Resultat
  <utmapp>/analys/rapportanalys_ÅÅÅÅ-MM-DD.md och .html   tabellerna (öppna från Windows eller klistra in)
  <utmapp>/analys/rapporthandelser.csv                     varje rapport med sina mått – underlag för backtest

OBS
  - Överlevnadsfel: bara bolag som är noterade idag ingår.
  - Rapporter samma dag rör sig delvis tillsammans, så t-värdena överskattar säkerheten något.
    Lita mer på en effekt som syns år efter år än på ett enskilt t-värde.
  - Inget här påverkar rapporten, strategierna eller reglerna.

Körning:
  ./venv/bin/python rapportanalys.py           # några minuter första gången (hämtar kurser)
  ./venv/bin/python rapportanalys.py --demo    # påhittad data, utan internet
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import innehav as ih
import screener as sc

MAPP = Path(__file__).resolve().parent
KOSTNAD_TUR_RETUR = 0.007   # courtage 2 × 0,25 % + spread 2 × 0,1 %
FONSTER = {"fore": (-21, -1), "reaktion": (-1, 1), "drift60": (1, 61)}
MIN_HANDELSDAGAR_KVARTAL = 20


# ---------------------------------------------------------------------------
# DATA
# ---------------------------------------------------------------------------
def las_rapporter(utmapp: Path) -> pd.DataFrame:
    fil = utmapp / "rapporter" / "historik.csv"
    if not fil.exists():
        raise SystemExit(f"Hittar inte {fil} – kör insamling.py först.")
    r = pd.read_csv(fil)
    r["rapportdatum"] = pd.to_datetime(r["rapportdatum"]).dt.normalize()
    r = r[r["rapportdatum"] < pd.Timestamp.today().normalize()]
    for k in ("eps_forvantad", "eps_utfall", "overraskning_pct"):
        r[k] = pd.to_numeric(r.get(k), errors="coerce")
    r = r.drop_duplicates(["ticker", "rapportdatum"]).sort_values(["ticker", "rapportdatum"]).reset_index(drop=True)
    return slå_ihop_nara(r)


def slå_ihop_nara(r: pd.DataFrame, dagar: int = 20) -> pd.DataFrame:
    """Yahoo listar ibland samma rapport två gånger med några dagars mellanrum (förväntat och faktiskt datum).
    Rapporter för samma bolag inom 'dagar' kalenderdagar slås ihop: den med utfall behålls, annars den första."""
    behall = []
    for _, g in r.groupby("ticker", sort=False):
        grupp = []
        for idx, d in zip(g.index, g["rapportdatum"]):
            if grupp and (d - r.at[grupp[0], "rapportdatum"]).days > dagar:
                behall.append(_valj(r, grupp))
                grupp = []
            grupp.append(idx)
        if grupp:
            behall.append(_valj(r, grupp))
    ut = r.loc[behall].sort_values(["ticker", "rapportdatum"]).reset_index(drop=True)
    if len(ut) < len(r):
        print(f"Slog ihop {len(r) - len(ut)} rapporter som låg inom {dagar} dagar från en annan (dubbletter hos Yahoo).",
              file=sys.stderr)
    return ut


def _valj(r: pd.DataFrame, idx: list) -> int:
    med_utfall = [i for i in idx if pd.notna(r.at[i, "eps_utfall"])]
    return med_utfall[0] if med_utfall else idx[0]


def las_kurser(utmapp: Path, tickers: list[str], demo: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    import backtest
    return backtest.hamta_data(utmapp, tickers, demo)


def demo_rapporter(kurser: pd.DataFrame) -> pd.DataFrame:
    """Påhittade rapporter var 63:e handelsdag med slumpad överraskning."""
    rng = np.random.default_rng(7)
    rader = []
    for t in kurser.columns:
        if t.startswith("^"):
            continue
        for i in range(30 + rng.integers(0, 40), len(kurser) - 5, 63):
            rader.append({"ticker": t, "rapportdatum": kurser.index[i], "eps_forvantad": 1.0,
                          "eps_utfall": 1 + rng.normal(0, 0.15), "overraskning_pct": rng.normal(0, 15)})
    return pd.DataFrame(rader)


# ---------------------------------------------------------------------------
# HÄNDELSER
# ---------------------------------------------------------------------------
def marknad(kurser: pd.DataFrame) -> pd.Series:
    """Lika viktat snitt av alla aktier: dagliga avkastningar medelvärdesbildas och kedjas ihop.
    Extrema dagliga rörelser (> ±50 %, ofta datafel) räknas inte."""
    aktier = kurser[[c for c in kurser.columns if not c.startswith("^")]]
    r = aktier.pct_change(fill_method=None)
    r = r.where(r.abs() < 0.5)
    return (1 + r.mean(axis=1, skipna=True).fillna(0)).cumprod()


def handelser(rapporter: pd.DataFrame, kurser: pd.DataFrame, volym: pd.DataFrame) -> pd.DataFrame:
    """En rad per rapport med avkastning mot marknaden i varje fönster och ovanlig omsättning."""
    kurser = kurser[~kurser.index.duplicated()].sort_index()
    idx = marknad(kurser)
    dagar = kurser.index
    m = idx.to_numpy()
    ut = []
    for ticker, g in rapporter.groupby("ticker"):
        if ticker not in kurser.columns:
            continue
        c = kurser[ticker].to_numpy()
        oms = (kurser[ticker] * volym[ticker]).to_numpy() if ticker in volym.columns else None
        datum = list(g["rapportdatum"])
        pos = dagar.searchsorted(pd.DatetimeIndex(datum))
        for j, (rad, i) in enumerate(zip(g.itertuples(), pos)):
            if i >= len(dagar) or (dagar[i] - rad.rapportdatum).days > 4:
                continue  # rapportdagen ligger utanför kursdatan

            def avk(a, b):
                if i + a < 0 or i + b >= len(c):
                    return np.nan
                ca, cb, ma, mb = c[i + a], c[i + b], m[i + a], m[i + b]
                if not (np.isfinite(ca) and np.isfinite(cb) and np.isfinite(ma) and np.isfinite(mb)) or ca <= 0:
                    return np.nan
                return (cb / ca - 1) - (mb / ma - 1)

            e = {"ticker": ticker, "rapportdatum": rad.rapportdatum, "dag0": dagar[i],
                 "overraskning_pct": rad.overraskning_pct}
            for namn, (a, b) in FONSTER.items():
                e[namn] = avk(a, b)
            # Rå avkastning i rapportfönstret, och index samma dagar
            if i - 1 >= 0 and i + 1 < len(c) and np.isfinite(c[i - 1]) and np.isfinite(c[i + 1]) and c[i - 1] > 0:
                e["reaktion_ra"] = c[i + 1] / c[i - 1] - 1
            # Köp dag +1, sälj 5 handelsdagar före nästa rapport
            if j + 1 < len(pos) and pos[j + 1] - 5 - (i + 1) >= MIN_HANDELSDAGAR_KVARTAL:
                e["kvartal"] = avk(1, pos[j + 1] - 5 - i)   # avk tar dagar RELATIVT rapportdagen
                e["kvartal_dagar"] = pos[j + 1] - 5 - (i + 1)
            # Ovanlig omsättning före rapporten
            if oms is not None and i - 60 >= 0:
                with np.errstate(all="ignore"):
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        fore, normal = np.nanmean(oms[i - 5:i]), np.nanmean(oms[i - 60:i - 20])
                e["oms_kvot"] = fore / normal if normal and np.isfinite(normal) and normal > 0 else np.nan
            ut.append(e)
    df = pd.DataFrame(ut)
    if df.empty:
        return df
    df["ar"] = df["dag0"].dt.year
    return df


def vanliga_dagar(kurser: pd.DataFrame, handelser_df: pd.DataFrame, langd: int = 2) -> pd.Series:
    """Avkastning mot marknaden för alla 'langd'-dagarsfönster som INTE överlappar en rapport – jämförelse för premien."""
    idx = marknad(kurser)
    ut = []
    for ticker in handelser_df["ticker"].unique():
        c = kurser[ticker]
        r = (c / c.shift(langd) - 1) - (idx / idx.shift(langd) - 1)
        r = r.iloc[langd::langd]  # ej överlappande
        h = handelser_df.loc[handelser_df["ticker"] == ticker, "dag0"]
        pos = kurser.index.searchsorted(pd.DatetimeIndex(h))
        bort = set()
        for p in pos:
            bort.update(kurser.index[max(0, p - 3):p + 4])
        ut.append(r[~r.index.isin(bort)].dropna())
    return pd.concat(ut) if ut else pd.Series(dtype=float)


# ---------------------------------------------------------------------------
# STATISTIK
# ---------------------------------------------------------------------------
def stat(x: pd.Series) -> dict:
    x = pd.Series(x).dropna()
    x = x.clip(x.quantile(0.005), x.quantile(0.995)) if len(x) > 200 else x  # några extrema dagar får inte styra
    n = len(x)
    if n < 5:
        return {"n": n, "snitt": np.nan, "median": np.nan, "andel_pos": np.nan, "std": np.nan, "t": np.nan}
    s = x.std()
    return {"n": n, "snitt": x.mean(), "median": x.median(), "andel_pos": (x > 0).mean(), "std": s,
            "t": x.mean() / (s / np.sqrt(n)) if s > 0 else np.nan}


def grupper(df: pd.DataFrame, efter: str, matt: list[str], antal: int = 5) -> pd.DataFrame:
    d = df.dropna(subset=[efter]).copy()
    if len(d) < antal * 20:
        return pd.DataFrame()
    d["grupp"] = pd.qcut(d[efter].rank(method="first"), antal, labels=[f"{i + 1}" for i in range(antal)])
    rader = []
    for gnamn, g in d.groupby("grupp", observed=True):
        rad = {"grupp": gnamn, "n": len(g), "fran": g[efter].min(), "till": g[efter].max()}
        for k in matt:
            s = stat(g[k])
            rad[k + "_median"] = s["median"]
            rad[k] = s["snitt"]
            rad[k + "_t"] = s["t"]
            rad[k + "_pos"] = s["andel_pos"]
        rader.append(rad)
    return pd.DataFrame(rader)


def per_ar(df: pd.DataFrame, efter: str, matt: str) -> pd.DataFrame:
    """Bästa femtedelen minus sämsta femtedelen, år för år (femtedelarna räknas inom varje år)."""
    rader = []
    for ar, g in df.dropna(subset=[efter, matt]).groupby("ar"):  # efter rapportår
        if len(g) < 50:
            continue
        q = pd.qcut(g[efter].rank(method="first"), 5, labels=False)
        # Kapa de 2 % mest extrema inom året – en enda aktie som gått +500 % ska inte avgöra ett år
        x = g[matt].clip(g[matt].quantile(0.01), g[matt].quantile(0.99))
        bast, samst = x[q == 4], x[q == 0]
        rader.append({"ar": ar, "n": len(g), "bast": bast.mean(), "samst": samst.mean(), "bast_median": bast.median(),
                      "skillnad": bast.mean() - samst.mean(), "bast_netto": bast.mean() - KOSTNAD_TUR_RETUR,
                      "bast_max": g.loc[q == 4, matt].max()})
    return pd.DataFrame(rader)


# ---------------------------------------------------------------------------
# RAPPORT
# ---------------------------------------------------------------------------
def p(x, d=1, tecken=True):
    if x is None or pd.isna(x):
        return "–"
    return f"{x * 100:+.{d}f} %" if tecken else f"{x * 100:.{d}f} %"


def tabell(rubriker: list[str], rader: list[list[str]]) -> str:
    return "\n".join(["| " + " | ".join(rubriker) + " |", "|" + "|".join("---" for _ in rubriker) + "|"]
                     + ["| " + " | ".join(r) + " |" for r in rader])


def skriv(df: pd.DataFrame, vanliga: pd.Series, utmapp: Path, demo: bool) -> tuple[Path, str]:
    D = ["# Rapportanalys" + (" (DEMODATA)" if demo else ""), "",
         f"{len(df)} rapporter i {df['ticker'].nunique()} bolag, {df['dag0'].min():%Y-%m-%d} – {df['dag0'].max():%Y-%m-%d}. "
         "All avkastning är **jämfört med marknaden** = snittet av alla aktier i listan, samma dagar. 0 % betyder ”som en vanlig aktie”. "
         f"Kostnad för en affär fram och tillbaka räknas som {KOSTNAD_TUR_RETUR * 100:.1f} %.", ""]

    # Täckning per år – visar om något år tappar rapporter (t.ex. för att nästa rapport saknas)
    tack = df.groupby("ar").agg(n=("ticker", "size"), eps=("overraskning_pct", lambda x: x.notna().sum()),
                                reak=("reaktion", lambda x: x.notna().sum()), kv=("kvartal", lambda x: x.notna().sum()))
    D += ["## Täckning", "",
          tabell(["År", "Rapporter", "Med EPS-förväntan", "Med reaktion", "Med köp-till-nästa-rapport"],
                 [[str(int(a)), str(r.n), str(r.eps), str(r.reak), str(r.kv)] for a, r in tack.iterrows()]),
          "", "Sista rapporten för varje bolag saknar ”till nästa rapport” (den har inte kommit än).", ""]

    # 1. Rapportpremie
    s_rap, s_van = stat(df["reaktion"]), stat(vanliga)
    D += ["## 1. Äga över rapporten?", "",
          tabell(["", "Antal", "Snitt mot marknaden", "Median", "Andel upp", "Svängning (std)"],
                 [["Rapportfönstret (dag −1 → +1)", str(s_rap["n"]), p(s_rap["snitt"], 2), p(s_rap["median"], 2),
                   p(s_rap["andel_pos"], 0, False), p(s_rap["std"], 1, False)],
                  ["Vanliga 2-dagarsperioder", str(s_van["n"]), p(s_van["snitt"], 2), p(s_van["median"], 2),
                   p(s_van["andel_pos"], 0, False), p(s_van["std"], 1, False)]]), ""]
    if pd.notna(s_rap["std"]) and pd.notna(s_van["std"]) and s_van["std"] > 0:
        D += [f"Rapportdagarna svänger **{s_rap['std'] / s_van['std']:.1f} gånger** så mycket som vanliga dagar. "
              f"Skillnad i snittavkastning: {p(s_rap['snitt'] - s_van['snitt'], 2)} per rapport "
              f"(t = {s_rap['t']:.1f} för rapportfönstret). Ett positivt snitt med t över ~2 tyder på en rapportpremie: "
              "då kostar det i snitt att undvika rapporter.", ""]
    fore = stat(df["fore"])
    D += [f"Inför rapporten (dag −20 → −1): snitt {p(fore['snitt'], 2)} mot marknaden (t = {fore['t']:.1f}).", ""]

    # 2. Drift efter rapport
    matt = ["reaktion", "drift60", "kvartal"]
    for efter, titel, enhet in (("overraskning_pct", "EPS-överraskning (analytikernas förväntan mot utfall)", "%"),
                                ("reaktion", "kursreaktionen på rapporten", "")):
        g = grupper(df, efter, matt)
        D += [f"## 2. Drift efter rapport – grupperat efter {titel}", ""]
        if g.empty:
            D += ["För få rapporter med data.", ""]
            continue
        rader = []
        for r in g.itertuples():
            spann = (f"{r.fran:.0f} … {r.till:.0f} %" if enhet == "%" else f"{r.fran * 100:+.1f} … {r.till * 100:+.1f} %")
            rader.append([f"{r.grupp}{' (sämst)' if r.grupp == '1' else ' (bäst)' if r.grupp == '5' else ''}", spann, str(r.n),
                          p(r.reaktion), p(r.drift60), f"{r.drift60_t:.1f}", p(r.drift60_pos, 0, False),
                          p(r.kvartal), p(r.kvartal_median), p(r.kvartal - KOSTNAD_TUR_RETUR if pd.notna(r.kvartal) else np.nan)])
        D += [tabell(["Grupp", "Spann", "Antal", "Reaktion", "Drift 60 d", "t", "Andel upp", "Till nästa rapport",
                      "– median", "Efter kostnad"], rader), ""]
        b, s = g.iloc[-1], g.iloc[0]
        D += [f"Bästa minus sämsta gruppen: drift 60 dagar **{p(b['drift60'] - s['drift60'])}**, "
              f"till nästa rapport **{p(b['kvartal'] - s['kvartal'])}**. "
              f"Din strategi (köp bästa gruppen dagen efter, sälj 5 dagar före nästa rapport) gav i snitt "
              f"**{p(b['kvartal'] - KOSTNAD_TUR_RETUR)} mot marknaden efter kostnad** per affär.", ""]

        a = per_ar(df, efter, "kvartal")
        if len(a):
            D += [f"### År för år – grupperat efter {titel}, köp efter, sälj före nästa rapport", "",
                  tabell(["År", "Rapporter", "Bästa gruppen", "– median", "Sämsta gruppen", "Skillnad", "Bästa efter kostnad",
                          "Bästa enskilda"],
                         [[str(int(r.ar)), str(r.n), p(r.bast), p(r.bast_median), p(r.samst), p(r.skillnad), p(r.bast_netto),
                           p(r.bast_max, 0)] for r in a.itertuples()]),
                  "", "Snitten per år är kapade vid 1 % och 99 % inom året så att enstaka extrema aktier inte styr.",
                  "", f"Bästa gruppen slog marknaden efter kostnad **{int((a['bast_netto'] > 0).sum())} av {len(a)} år**.", ""]

    # 4. Omsättning före rapport
    g = grupper(df, "oms_kvot", ["reaktion"])
    D += ["## 3. Ovanlig omsättning före rapporten", "",
          "Omsättning dag −5 → −1 delat med normal omsättning (dag −60 → −21). 1,0 = som vanligt.", ""]
    if g.empty:
        D += ["För få rapporter med omsättningsdata.", ""]
    else:
        d2 = df.dropna(subset=["oms_kvot", "reaktion"]).copy()
        d2["grupp"] = pd.qcut(d2["oms_kvot"].rank(method="first"), 5, labels=[str(i + 1) for i in range(5)])
        absr = d2.groupby("grupp", observed=True)["reaktion"].apply(lambda x: x.abs().mean())
        D += [tabell(["Grupp", "Omsättning mot normalt", "Antal", "Reaktion (snitt)", "t", "Andel upp", "Reaktionens storlek"],
                     [[r.grupp, f"{r.fran:.2f} … {r.till:.2f}×", str(r.n), p(r.reaktion, 2), f"{r.reaktion_t:.1f}",
                       p(r.reaktion_pos, 0, False), p(absr.get(r.grupp), 1, False)] for r in g.itertuples()]),
              "", "Om grupp 5 (mest ovanlig omsättning) har klart högre snitt och andel upp än de andra, säger hög omsättning "
              "före rapport något om riktningen. Är bara *storleken* större vet marknaden att något är på gång, men inte åt vilket håll.", ""]

    D += ["## Läs med försiktighet", "",
          "- Överlevnadsfel: bara bolag som är noterade idag ingår.",
          "- Rapporter samma dag rör sig delvis tillsammans – t-värdena överskattar säkerheten något. "
          "En effekt som syns de flesta år är mer trovärdig än ett högt t-värde.",
          "- Snitt kan styras av ett fåtal extrema rapporter. Medianen och andelen upp visar om effekten är bred.",
          "- Det här är underlag för att bestämma vad som ska testas i backtest och framåttest, inte en strategi i sig."]
    md = "\n".join(D) + "\n"

    m = utmapp / "analys"
    m.mkdir(parents=True, exist_ok=True)
    idag = dt.date.today().isoformat()
    (m / f"rapportanalys_{idag}.md").write_text(md, encoding="utf-8")
    (m / f"rapportanalys_{idag}.html").write_text(_html(md), encoding="utf-8")
    df.to_csv(m / "rapporthandelser.csv", index=False, float_format="%.6g")
    return m / f"rapportanalys_{idag}.html", md


def _html(md: str, titel: str = "Rapportanalys") -> str:
    """Enkel markdown → HTML för tabellerna (inga extra paket)."""
    import re
    ut, i, rader = [], 0, md.splitlines()

    def inline(t):
        t = html.escape(t)
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)

    while i < len(rader):
        r = rader[i]
        if r.startswith("| "):
            block = []
            while i < len(rader) and rader[i].startswith("|"):
                block.append(rader[i])
                i += 1
            rub = [c.strip() for c in block[0].strip("|").split("|")]
            ut.append("<div class='wrap'><table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in rub) + "</tr></thead><tbody>")
            for b in block[2:]:
                ut.append("<tr>" + "".join(f"<td>{inline(c.strip())}</td>" for c in b.strip("|").split("|")) + "</tr>")
            ut.append("</tbody></table></div>")
            continue
        if r.startswith("### "):
            ut.append(f"<h3>{inline(r[4:])}</h3>")
        elif r.startswith("## "):
            ut.append(f"<h2>{inline(r[3:])}</h2>")
        elif r.startswith("# "):
            ut.append(f"<h1>{inline(r[2:])}</h1>")
        elif r.startswith("- "):
            ut.append(f"<li>{inline(r[2:])}</li>")
        elif r.strip():
            ut.append(f"<p>{inline(r)}</p>")
        i += 1
    return ("<!doctype html><html lang='sv'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(titel)}</title>"
            "<style>body{font:15px/1.5 system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;"
            "color:#1c1f24;background:#f6f7f9}h2{margin-top:28px}.wrap{overflow-x:auto;background:#fff;border:1px solid #e5e7eb;border-radius:8px}"
            "table{border-collapse:collapse;width:100%}th,td{padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:right;white-space:nowrap}"
            "th:first-child,td:first-child{text-align:left}th{font-size:12px;color:#6b7280}"
            "@media(prefers-color-scheme:dark){body{background:#14161a;color:#e6e8eb}.wrap{background:#1d2026;border-color:#2c3038}"
            "th,td{border-color:#2c3038}}</style></head><body>" + "\n".join(ut) + "</body></html>")


def main() -> int:
    a = argparse.ArgumentParser(description="Händelsestudie runt rapporter")
    a.add_argument("--utmapp", default=sc.KONFIG["utmapp"])
    a.add_argument("--tickers", default=str(MAPP / "tickers_se.csv"))
    a.add_argument("--demo", action="store_true")
    arg = a.parse_args()
    utmapp = Path(arg.utmapp).expanduser()
    tickers = list(sc.las_tickers(Path(arg.tickers))["ticker"])
    kurser, volym = las_kurser(utmapp, tickers, arg.demo)
    rapporter = demo_rapporter(kurser) if arg.demo else las_rapporter(utmapp)
    print(f"{len(rapporter)} rapporter, kurser {kurser.index.min():%Y-%m-%d} – {kurser.index.max():%Y-%m-%d}", file=sys.stderr)
    df = handelser(rapporter, kurser, volym)
    if df.empty:
        print("Inga rapporter inom kursdatan.", file=sys.stderr)
        return 1
    vanliga = vanliga_dagar(kurser, df)
    fil, md = skriv(df, vanliga, utmapp, arg.demo)
    print(md)
    print(f"Sparat: {fil}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
