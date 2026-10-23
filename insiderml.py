#!/usr/bin/env python3
"""
Insider ML – kan en modell sortera bort de insidersignaler som inte fungerar?
=============================================================================

Insider 30 köper varje köpvåg 31–60 dagar före rapport. Den här filen tränar en liten modell som för
varje sådan signal uppskattar sannolikheten att aktien slår marknaden efter kostnad fram till säljdagen
(30 dagar efter rapporten), och prövar om det hade hjälpt att bara köpa de signaler modellen godkänner.

RAMAR (bestämda 2026-10-20 INNAN modellen kördes – ändras inte efter resultatet)
  Data        Varje signal Insider 30 hade fått 2018–2026 (samma regel: signaler.insider_kandidater).
  Mål         Slår aktien marknaden (lika viktad, med utdelningar) efter 0,7 % kostnad, från köpdagen
              till säljdagen enligt Insider 30:s regel? (ja/nej)
  Egenskaper  Bara sådant som var känt på beslutsdagen (se EGENSKAPER).
  Test        Walk-forward per år: för testår Å tränas modellen på signaler vars säljdag låg före 1 jan Å
              (så att facit var känt), och bedöms på årets signaler. Aldrig samma år i träning och test.
              Testår 2021 och framåt (minst två års träningsdata).
  Modell      Huvudmodell: HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
              min_samples_leaf=40, l2_regularization=1.0). Jämförelse: logistisk regression (C=0.5).
  Beslut      Godkänn en signal om modellens sannolikhet ≥ medianen av sannolikheterna i träningsdatan
              (ungefär hälften godkänns).
  Kontroll    Slumpfilter som godkänner lika stor andel slumpvis (200 dragningar).

KRAV för att modellen ska bli strategin "Insider ML v1" (alla måste klaras):
  K1  Godkända slår underkända (snitt mot marknaden) över alla testår, och i minst 2/3 av teståren.
  K2  Godkända slår minst 95 % av slumpfiltren (190 av 200).
  K3  Portföljen med bara godkända köp slår Insider 30 under testperioden (samma regler och kostnader).
  K4  Portföljen slår portföljen med slumpfilter.
  Klaras inte kraven behålls Insider 30 som den är.

Resultat
  <utmapp>/analys/insiderml_ÅÅÅÅ-MM-DD.md och .html
  <utmapp>/analys/insiderml_signaler.csv    varje signal med egenskaper, facit och modellens bedömning
  <utmapp>/backtest/insiderml/              portföljerna – visas på webbsidan under Strategier → Backtest → Insider ML,
                                            med modellens bedömning (för/emot) på varje köp och signal

Livemodellen
  Efter körningen tränas huvudmodellen på ALLA signaler med känt facit och sparas i analys/insiderml_modell.pkl.
  Veckorapporten visar dess bedömning av veckans insidersignaler i guldrutan (bara information, påverkar inga köp)
  och sparar den i analys/insiderml_live.csv – ett äkta framåttest av modellen. Kör om en gång i månaden
  (cron, --utan-portfoljer) så lär den sig av nya signaler med facit.

Körning:
  ./venv/bin/python insiderml.py --utmapp /srv/storage/aktiedata     # några minuter
  ./venv/bin/python insiderml.py --demo
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
import zlib
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
KOSTNAD = ra.KOSTNAD_TUR_RETUR
SALJ_EFTER = 30
FORSTA_TESTAR = 2021
SLUMPFILTER = 200

EGENSKAPER = {
    # insiderköpet
    "log_dagar_oms": "nettoköp i dagars omsättning (log)",
    "log_netto": "nettoköp i kronor (log)",
    "kopare": "antal köpare (4 veckor)",
    "affarer": "antal köpaffärer (4 veckor)",
    "salj_kvot": "insiders försäljningar / köp (4 veckor)",
    "har_vd": "vd köpte", "har_cfo": "ekonomichef köpte", "har_styrelse": "styrelseledamot köpte",
    "bara_narstaende": "bara närstående köpte (ofta ett ägarbolag)",
    "dagar_till_rapport": "dagar till rapport",
    # aktien
    "mom20": "aktiens kursutveckling 1 mån", "mom60": "3 mån", "mom250": "12 mån",
    "vol60": "volatilitet 3 mån", "avst_ma200": "avstånd till MA200",
    "log_oms": "omsättning per dag (log) – mått på bolagets storlek",
    "forra_reaktion": "kursreaktion på förra rapporten (mot marknaden)",
    "forra_overraskning": "EPS-överraskning förra rapporten",
    # marknaden
    "mark60": "marknaden 3 mån", "mark250": "marknaden 12 mån", "markvol60": "marknadens volatilitet 3 mån",
}
ROLLER = {"vd": ("verkställande", "vd", "ceo", "chief executive", "koncernchef"),
          "cfo": ("finanschef", "cfo", "ekonomichef", "chief financial", "finansdirektör"),
          "styrelse": ("styrelse", "ordförande", "board", "chair")}


# ---------------------------------------------------------------------------
# SIGNALER MED EGENSKAPER OCH FACIT
# ---------------------------------------------------------------------------
def _roll(b: str, nyckel: str) -> bool:
    b = (b or "").lower()
    return any(o in b for o in ROLLER[nyckel])


def bygg_signaler(data: dict, lordagar: pd.DatetimeIndex, rapporter: pd.DataFrame | None,
                  kandidater=None) -> pd.DataFrame:
    """kandidater = färdig kandidattabell (veckorapporten, en lördag). Annars räknas de fram vecka för vecka."""
    kurser, aff, kal = data["kurser"], data["aff"], data["kal"]
    dagar = kurser.index
    m = ra.marknad(kurser).to_numpy()
    mret = np.diff(np.log(np.where(m > 0, m, np.nan)), prepend=np.nan)
    pub = aff["publicerad"].to_numpy()
    # förra rapportens EPS-överraskning per bolag
    rap = {}
    if rapporter is not None and len(rapporter):
        r = rapporter.dropna(subset=["rapportdatum"]).copy()
        r["bolag"] = r["ticker"].map(sc.bolagsnyckel)
        rap = {b: g.sort_values("rapportdatum") for b, g in r.groupby("bolag")}
    rader = []
    for lor in lordagar:
        if kandidater is None:
            tabell, lik = bt.likvida(data, lor)
            if not len(lik):
                continue
            kand = sg.insider_kandidater(aff, kal, lor, lik, data["bv"], kurser)
        else:
            kand = kandidater
        if not len(kand):
            continue
        i = dagar.searchsorted(lor, side="right") - 1          # köpdag = senaste handelsdag före lördagen
        if i < 260:
            continue
        slut, start = lor - pd.Timedelta(days=2), lor - pd.Timedelta(days=29)
        lo, hi = np.searchsorted(pub, np.datetime64(start)), np.searchsorted(pub, np.datetime64(slut + pd.Timedelta(days=1)))
        fon = aff.iloc[lo:hi]
        oms = data["oms"].iloc[i]
        for r in kand.itertuples():
            c = kurser[r.ticker].to_numpy()
            if not np.isfinite(c[i]) or c[i] <= 0:
                continue
            # säljdag enligt Insider 30: första lördagen ≥ rapport + 30 dagar, handelsdagen före
            rd = sg.nasta_rapport(kal, r.bolag, dagar[i])
            if rd is None or (rd - dagar[i]).days > st.INSIDER_RESERV_DAGAR:
                saljdag = dagar[i] + pd.Timedelta(days=st.INSIDER_RESERV_DAGAR + SALJ_EFTER)
            else:
                saljdag = rd + pd.Timedelta(days=SALJ_EFTER)
            salj_lor = saljdag + pd.Timedelta(days=(5 - saljdag.weekday()) % 7)
            j = dagar.searchsorted(salj_lor, side="right") - 1
            facit = np.nan
            if salj_lor <= dagar[-1] and j > i and np.isfinite(c[j]) and np.isfinite(m[j]) and m[i] > 0:
                facit = (c[j] / c[i] - 1) - (m[j] / m[i] - 1)
            g = fon[(fon["bolag"] == r.bolag)]
            k, s_ = g[g["kop"]], g[~g["kop"]]
            ks = k["belopp_sek"].sum()
            s = pd.Series(c[max(0, i - 260):i + 1])
            ret = np.diff(np.log(s.where(s > 0)))
            e = {"lordag": lor, "kopdag": dagar[i], "saljdag": dagar[j] if j > i else pd.NaT,
                 "ar": dagar[i].year, "ticker": r.ticker, "bolag": r.bolag, "facit": facit,
                 # Bara det som var känt då: nettoköp mot omsättningen (börsvärdet bakåt skulle läcka framtida kurs)
                 "log_dagar_oms": np.log10(r.dagar_oms) if pd.notna(r.dagar_oms) and r.dagar_oms > 0 else np.nan,
                 "log_netto": np.log10(max(r.netto_sek, 1.0)), "kopare": r.kopare, "affarer": len(k),
                 "salj_kvot": s_["belopp_sek"].sum() / ks if ks > 0 else np.nan,
                 "har_vd": float(any(_roll(b, "vd") for b in k["befattning"])),
                 "har_cfo": float(any(_roll(b, "cfo") for b in k["befattning"])),
                 "har_styrelse": float(any(_roll(b, "styrelse") for b in k["befattning"])),
                 "bara_narstaende": float(len(k) > 0 and bool(k["narstaende"].astype(bool).all())),
                 "dagar_till_rapport": r.dagar_till_rapport,
                 "mom20": c[i] / c[i - 20] - 1, "mom60": c[i] / c[i - 60] - 1, "mom250": c[i] / c[i - 250] - 1,
                 "vol60": float(np.nanstd(ret[-60:]) * np.sqrt(252)) if len(ret) >= 60 else np.nan,
                 "avst_ma200": c[i] / np.nanmean(c[i - 199:i + 1]) - 1,
                 "log_oms": np.log10(oms.get(r.ticker, np.nan)) if oms.get(r.ticker, 0) > 0 else np.nan,
                 "mark60": m[i] / m[i - 60] - 1, "mark250": m[i] / m[i - 250] - 1,
                 "markvol60": float(np.nanstd(mret[i - 59:i + 1]) * np.sqrt(252))}
            # förra rapporten: reaktion (dag −1 → +1 mot marknaden) och EPS-överraskning
            e["forra_reaktion"] = e["forra_overraskning"] = np.nan
            kd = kal.get(r.bolag)
            if kd is not None and len(kd):
                p = np.searchsorted(kd, np.datetime64(dagar[i].date(), "D"), side="right") - 1
                if p >= 0:
                    d0 = dagar.searchsorted(pd.Timestamp(kd[p]))
                    if 1 <= d0 < i - 1 and np.isfinite(c[d0 - 1]) and np.isfinite(c[d0 + 1]) and c[d0 - 1] > 0:
                        e["forra_reaktion"] = (c[d0 + 1] / c[d0 - 1] - 1) - (m[d0 + 1] / m[d0 - 1] - 1)
            g2 = rap.get(r.bolag)
            if g2 is not None:
                fore = g2[g2["rapportdatum"] < dagar[i]].dropna(subset=["overraskning_pct"])
                if len(fore):
                    e["forra_overraskning"] = float(np.clip(fore["overraskning_pct"].iloc[-1], -100, 100))
            rader.append(e)
    df = pd.DataFrame(rader)
    if len(df):
        df["mal"] = (df["facit"] > KOSTNAD).astype(float).where(df["facit"].notna())
    return df


# ---------------------------------------------------------------------------
# WALK-FORWARD
# ---------------------------------------------------------------------------
def modeller():
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return {
        "gb": lambda: HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
                                                     min_samples_leaf=40, l2_regularization=1.0, random_state=0),
        "lr": lambda: make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                                    LogisticRegression(C=0.5, max_iter=2000)),
    }


def walk_forward(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    X = list(EGENSKAPER)
    df = df.copy()
    for namn in ("gb", "lr"):
        df[f"p_{namn}"] = np.nan
        df[f"ok_{namn}"] = np.nan
    df["grans"] = np.nan
    df["for"] = ""
    df["emot"] = ""
    vikt = {}
    for ar in sorted(a for a in df["ar"].unique() if a >= FORSTA_TESTAR):
        grans = pd.Timestamp(f"{ar}-01-01")
        tr = df[(df["saljdag"] < grans) & df["mal"].notna()]
        te = df["ar"] == ar
        if len(tr) < 200 or tr["mal"].nunique() < 2 or not te.any():
            continue
        # Egenskaper som saknas helt eller är konstanta i träningsdatan (t.ex. EPS-data före 2020) tas bort det året
        Xa = [c for c in X if tr[c].notna().any() and tr[c].nunique(dropna=True) > 1]
        for namn, ny in modeller().items():
            mod = ny().fit(tr[Xa], tr["mal"])
            p_tr = mod.predict_proba(tr[Xa])[:, 1]
            p = mod.predict_proba(df.loc[te, Xa])[:, 1]
            df.loc[te, f"p_{namn}"] = p
            df.loc[te, f"ok_{namn}"] = (p >= np.median(p_tr)).astype(float)
            if namn == "gb":
                vikt[ar] = (mod, df.loc[te & df["mal"].notna()], Xa)
                df.loc[te, "grans"] = float(np.median(p_tr))
                for_, emot = forklara(mod, df.loc[te, Xa], tr[Xa], p)
                df.loc[te, "for"] = for_
                df.loc[te, "emot"] = emot
    return df, vikt


KORT = {"log_dagar_oms": "stort köp", "log_netto": "köpbelopp", "kopare": "antal köpare", "affarer": "antal köpaffärer",
        "salj_kvot": "insiders säljer också", "har_vd": "vd köpte", "har_cfo": "ekonomichef köpte",
        "har_styrelse": "styrelseledamot köpte", "bara_narstaende": "bara närstående köpte",
        "dagar_till_rapport": "dagar till rapport", "mom20": "aktien 1 mån", "mom60": "aktien 3 mån",
        "mom250": "aktien 12 mån", "vol60": "volatilitet", "avst_ma200": "avstånd till MA200", "log_oms": "omsättning",
        "forra_reaktion": "förra rapportens reaktion", "forra_overraskning": "förra EPS-överraskningen",
        "mark60": "marknaden 3 mån", "mark250": "marknaden 12 mån", "markvol60": "marknadens oro"}


def _varde(f: str, v) -> str:
    if v is None or pd.isna(v):
        return "saknas"
    if f.startswith("har_") or f == "bara_narstaende":
        return "ja" if v else "nej"
    if f in ("mom20", "mom60", "mom250", "avst_ma200", "forra_reaktion", "mark60", "mark250"):
        return f"{v * 100:+.0f} %"
    if f in ("vol60", "markvol60"):
        return f"{v * 100:.0f} %"
    if f == "log_dagar_oms":
        return f"{10 ** v:.2f} dagars oms."
    if f == "log_netto":
        return f"{10 ** v / 1e6:.1f} MSEK"
    if f == "log_oms":
        return f"{10 ** v / 1e6:.0f} MSEK/dag"
    if f == "forra_overraskning":
        return f"{v:+.0f} %"
    if f == "salj_kvot":
        return f"{v:.2f}"
    return f"{v:.0f}"


def forklara(mod, X: pd.DataFrame, Xtr, p: np.ndarray, n: int = 2, min_pe: float = 0.01
             ) -> tuple[list[str], list[str]]:
    """Varför modellen tyckte som den gjorde, per signal: hur mycket sannolikheten ändras om egenskapen i stället
    hade varit som för en typisk signal (träningens median). De två som drog mest upp (för) och ned (emot)."""
    med = Xtr if isinstance(Xtr, pd.Series) else Xtr.median(numeric_only=True)   # träningens median
    bidrag = {}
    for f in X.columns:
        X2 = X.copy()
        X2[f] = med.get(f, np.nan)
        bidrag[f] = p - mod.predict_proba(X2)[:, 1]
    B = pd.DataFrame(bidrag, index=X.index)
    for_, emot = [], []
    for idx in X.index:
        b = B.loc[idx].sort_values()
        upp = [f for f in b.index[::-1] if b[f] >= min_pe][:n]
        ned = [f for f in b.index if b[f] <= -min_pe][:n]
        for_.append("; ".join(f"{KORT.get(f, f)} {_varde(f, X.at[idx, f])} ({b[f] * 100:+.0f} pe)" for f in upp))
        emot.append("; ".join(f"{KORT.get(f, f)} {_varde(f, X.at[idx, f])} ({b[f] * 100:+.0f} pe)" for f in ned))
    return for_, emot


def betydelse(vikt: dict) -> pd.Series:
    """Permutationsvikt för huvudmodellen, snitt över teståren (hur mycket sämre den blir utan egenskapen)."""
    from sklearn.inspection import permutation_importance
    delar = []
    for ar, (mod, te, Xa) in vikt.items():
        if len(te) < 50 or te["mal"].nunique() < 2:
            continue
        r = permutation_importance(mod, te[Xa], te["mal"], n_repeats=10, random_state=0, scoring="roc_auc")
        delar.append(pd.Series(r.importances_mean, index=Xa).reindex(list(EGENSKAPER)))
    return pd.concat(delar, axis=1).mean(axis=1).sort_values(ascending=False) if delar else pd.Series(dtype=float)


# ---------------------------------------------------------------------------
# LIVEMODELLEN – tränad på alla signaler med känt facit, används i veckorapporten (bara information)
# ---------------------------------------------------------------------------
def modellfil(utmapp: Path) -> Path:
    return Path(utmapp) / "analys" / "insiderml_modell.pkl"


def spara_livemodell(df: pd.DataFrame, utmapp: Path) -> dict | None:
    """Huvudmodellen med samma inställningar, tränad på alla signaler vars facit är känt idag."""
    import pickle
    tr = df[df["mal"].notna() & (df["saljdag"] < pd.Timestamp.today().normalize())]
    if len(tr) < 200 or tr["mal"].nunique() < 2:
        return None
    Xa = [c for c in EGENSKAPER if tr[c].notna().any() and tr[c].nunique(dropna=True) > 1]
    mod = modeller()["gb"]().fit(tr[Xa], tr["mal"])
    info = {"modell": mod, "egenskaper": Xa, "grans": float(np.median(mod.predict_proba(tr[Xa])[:, 1])),
            "median": tr[Xa].median(numeric_only=True), "tranad": dt.date.today().isoformat(), "signaler": len(tr),
            "facit_till": str(tr["saljdag"].max().date())}
    f = modellfil(utmapp)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f.with_suffix(".tmp"), "wb") as fh:
        pickle.dump(info, fh)
    f.with_suffix(".tmp").replace(f)
    return info


def bedom_veckan(kand: pd.DataFrame, utmapp: Path, tabell: pd.DataFrame, dag=None) -> pd.DataFrame:
    """Modellens bedömning av veckans insidersignaler (guldrutan). Lägger till ml_p, ml_grans, ml_ok, ml_for,
    ml_emot och sparar bedömningen i analys/insiderml_live.csv så att modellen kan utvärderas framåt.
    Påverkar inga köp. Saknas modellen returneras tabellen oförändrad."""
    import pickle
    f = modellfil(utmapp)
    if kand is None or not len(kand) or not f.exists():
        return kand
    with open(f, "rb") as fh:
        info = pickle.load(fh)
    lor = pd.Timestamp(dag or dt.date.today()).normalize()
    kurser = ih.las_kurser(Path(utmapp) / "kurser.csv")
    kurser = kurser[~kurser.index.duplicated()].sort_index()
    kurser = kurser[kurser.index <= lor]
    # Bara svenska aktier (och index) – kurser.csv har också valutor och egna utländska innehav
    kurser = kurser[[c for c in kurser.columns if c.endswith(".ST") or c.startswith("^")]]
    # Bara riktiga handelsdagar (rader där de flesta aktierna har kurs)
    breda = kurser.notna().sum(axis=1)
    kurser = kurser[breda >= 0.5 * breda.max()]
    oms = pd.DataFrame(np.nan, index=kurser.index, columns=list(kand["ticker"]))
    t = tabell.set_index("ticker")["omsattning_sek"] if "omsattning_sek" in tabell.columns else pd.Series(dtype=float)
    for tk in kand["ticker"]:
        oms.loc[kurser.index[-1], tk] = t.get(tk, np.nan)
    try:
        rapporter = ra.las_rapporter(Path(utmapp))
    except SystemExit:
        rapporter = None
    data = {"kurser": kurser, "aff": sg.insider_affarer(Path(utmapp), MAPP / "tickers_se.csv"),
            "kal": sg.rapportkalender(Path(utmapp)), "bv": sg.borsvarden(Path(utmapp)), "oms": oms}
    df = bygg_signaler(data, pd.DatetimeIndex([lor]), rapporter, kandidater=kand)
    ut = kand.copy()
    for k in ("ml_p", "ml_grans", "ml_ok"):
        ut[k] = np.nan
    ut["ml_for"] = ut["ml_emot"] = ""
    if df.empty:
        return ut
    Xa = info["egenskaper"]
    X = df.reindex(columns=Xa)
    p = info["modell"].predict_proba(X)[:, 1]
    for_, emot = forklara(info["modell"], X, info["median"], p)
    bed = pd.DataFrame({"ticker": df["ticker"], "ml_p": p, "ml_grans": info["grans"],
                        "ml_ok": (p >= info["grans"]).astype(float), "ml_for": for_, "ml_emot": emot})
    ut = kand.merge(bed, on="ticker", how="left")
    ut["ml_for"] = ut["ml_for"].fillna("")
    ut["ml_emot"] = ut["ml_emot"].fillna("")
    # Logga veckans bedömning (en rad per vecka och aktie) för framtida utvärdering
    logg = Path(utmapp) / "analys" / "insiderml_live.csv"
    rad = ut.dropna(subset=["ml_p"]).assign(datum=lor.date().isoformat(), modell_tranad=info["tranad"])[
        ["datum", "ticker", "bolag", "ml_p", "ml_grans", "ml_ok", "ml_for", "ml_emot", "nasta_rapport", "modell_tranad"]]
    if len(rad):
        gammal = pd.read_csv(logg) if logg.exists() else pd.DataFrame(columns=rad.columns)
        alla = pd.concat([gammal[~gammal["datum"].isin(rad["datum"])], rad], ignore_index=True)
        alla.to_csv(logg, index=False, float_format="%.4g")
    ut.attrs["modell"] = {k: info[k] for k in ("tranad", "signaler", "facit_till")}
    return ut


# ---------------------------------------------------------------------------
# PORTFÖLJER
# ---------------------------------------------------------------------------
def portfoljer(utmapp: Path, data: dict, df: pd.DataFrame, demo: bool) -> tuple[Path, dict]:
    test = df[df["ok_gb"].notna()]
    if test.empty:
        return None, {}
    start = pd.Timestamp(f"{int(test['ar'].min())}-01-01")
    lordagar = bt.insider_lordagar(data)
    lordagar = lordagar[lordagar >= start]
    godk = {(r.lordag, r.bolag) for r in test.itertuples() if r.ok_gb == 1}
    orsak = {}
    for r in test.to_dict("records"):
        t = f"modellen {r['p_gb'] * 100:.0f} % (gräns {r['grans'] * 100:.0f} %)"
        if r.get("for"):
            t += f" – för: {r['for']}"
        if r.get("emot"):
            t += f" – emot: {r['emot']}"
        orsak[(r["lordag"], r["bolag"])] = t
    andel = float(test["ok_gb"].mean())

    def ml_filter(kand, lor, sid):
        k = kand[[(lor, b) in godk for b in kand["bolag"]]].copy()
        k["ml_orsak"] = [orsak.get((lor, b), "") for b in k["bolag"]]
        return k

    def slump_filter(kand, lor, sid):
        rng = np.random.default_rng(zlib.crc32(f"{sid}{lor.date()}".encode()))
        return kand[rng.random(len(kand)) < andel]

    # Strategierna finns i strategier.VISNING: de visas på webbsidan under Backtest men handlar aldrig framåt
    varianter = {"ml_insider30": ({"namn": st.VISNING["ml_insider30"]["namn"]}, {}),
                 "ml_filter": ({"namn": st.VISNING["ml_filter"]["namn"]}, {"filter": ml_filter}),
                 "ml_slump": ({"namn": st.VISNING["ml_slump"]["namn"]}, {"filter": slump_filter})}
    rot = utmapp / "backtest" / ("insiderml_demo" if demo else "insiderml")
    if rot.exists():
        shutil.rmtree(rot)
    rot.mkdir(parents=True)
    print(f"Portföljer {lordagar[0].date()} – {lordagar[-1].date()} ({len(lordagar)} veckor) ...", file=sys.stderr)
    ih.skriv_kurser(rot / "kurser.csv", data["kurser"])
    bt.simulera_insider(rot, data, lordagar, {sid: v for sid, (_, v) in varianter.items()})
    import json
    manader = round((lordagar[-1] - lordagar[0]).days / 30.44)
    (rot / "info.json").write_text(json.dumps({
        "manader": manader, "etikett": "Insider ML", "fran": str(lordagar[0].date()), "till": str(lordagar[-1].date()),
        "veckor": len(lordagar), "aktier": len(data["bolag_ticker"]), "demo": demo,
        "skapad": dt.datetime.now().isoformat(timespec="minutes"), "strategier": list(varianter), "hoppade_over": [],
        "ml": True}, indent=2, ensure_ascii=False), encoding="utf-8")
    ut = {}
    for sid, (n, _) in varianter.items():
        v = st.vardeserie(rot, sid, data["kurser"])
        ut[sid] = {"namn": n["namn"], "serie": v["varde"] if len(v) else pd.Series(dtype=float),
                   "affarer": len(ih.las_affarer(st.mapp(rot, sid) / "avslutade.csv"))}
    return rot, ut


# ---------------------------------------------------------------------------
# RAPPORT
# ---------------------------------------------------------------------------
p, tabell = ra.p, ra.tabell


def avk(s: pd.Series, fran=None, till=None) -> float:
    s = s.dropna()
    if fran is not None:
        s = s[s.index >= fran]
    if till is not None:
        s = s[s.index < till]
    return float(s.iloc[-1] / s.iloc[0] - 1) if len(s) >= 2 else np.nan


def skriv(df: pd.DataFrame, vikt: pd.Series, port: dict, data: dict, utmapp: Path, demo: bool) -> tuple[Path, str]:
    test = df[df["ok_gb"].notna() & df["facit"].notna()]
    D = ["# Insider ML" + (" (DEMODATA)" if demo else ""), "",
         f"{len(df)} insidersignaler (samma regel som Insider 30) {df['kopdag'].min():%Y-%m-%d} – {df['kopdag'].max():%Y-%m-%d}, "
         f"varav {df['facit'].notna().sum()} med känt facit. Testår {FORSTA_TESTAR}– (walk-forward). "
         f"Mål: slår aktien marknaden efter {KOSTNAD * 100:.1f} % kostnad till säljdagen? "
         f"Andel som gjorde det: {df['mal'].mean() * 100:.0f} %. Ramar och krav bestämdes innan körningen.", ""]
    if test.empty:
        D += ["För lite data för walk-forward (behöver minst 200 signaler med facit före första teståret)."]
        return _spara(D, df, utmapp)

    # Per år
    rader, plus = [], 0
    for ar, g in test.groupby("ar"):
        ok, nej = g[g["ok_gb"] == 1]["facit"], g[g["ok_gb"] == 0]["facit"]
        skill = ok.mean() - nej.mean()
        plus += skill > 0
        ok_lr = g[g["ok_lr"] == 1]["facit"].mean() - g[g["ok_lr"] == 0]["facit"].mean()
        rader.append([str(int(ar)), str(len(g)), f"{(g['ok_gb'] == 1).mean() * 100:.0f} %", p(ok.mean()), p(nej.mean()),
                      p(skill), p(ok.mean() - KOSTNAD), p(ok_lr)])
    ok_all, nej_all = test[test["ok_gb"] == 1]["facit"], test[test["ok_gb"] == 0]["facit"]
    rader.append(["Alla", str(len(test)), f"{(test['ok_gb'] == 1).mean() * 100:.0f} %", p(ok_all.mean()), p(nej_all.mean()),
                  p(ok_all.mean() - nej_all.mean()), p(ok_all.mean() - KOSTNAD),
                  p(test[test["ok_lr"] == 1]["facit"].mean() - test[test["ok_lr"] == 0]["facit"].mean())])
    antal_ar = test["ar"].nunique()
    k1 = (ok_all.mean() > nej_all.mean()) and plus >= np.ceil(2 / 3 * antal_ar)

    # Slumpfilter
    rng = np.random.default_rng(0)
    andel = (test["ok_gb"] == 1).mean()
    f = test["facit"].to_numpy()
    slump = np.array([f[rng.random(len(f)) < andel].mean() for _ in range(SLUMPFILTER)])
    slog = int((ok_all.mean() > slump).sum())
    k2 = slog >= 0.95 * SLUMPFILTER

    # Portföljer
    k3 = k4 = False
    prad = []
    if port:
        a = {sid: avk(v["serie"]) for sid, v in port.items()}
        k3 = a["ml_filter"] > a["ml_insider30"]
        k4 = a["ml_filter"] > a["ml_slump"]
        mark = ra.marknad(data["kurser"])
        s0 = port["ml_insider30"]["serie"].index[0] if len(port["ml_insider30"]["serie"]) else None
        mark = mark[mark.index >= s0] if s0 is not None else mark
        for sid, v in port.items():
            s = v["serie"]
            dd = float((s / s.cummax() - 1).min()) if len(s) else np.nan
            prad.append([v["namn"], p(a[sid]), p(a[sid] - avk(mark)), p(dd), str(v["affarer"])])
        prad.append(["Marknaden (lika viktad, med utdelningar)", p(avk(mark)), "", p(float((mark / mark.cummax() - 1).min())), ""])
        ar_rader = []
        for ar in range(s0.year, data["kurser"].index.max().year + 1):
            fr, ti = pd.Timestamp(f"{ar}-01-01"), pd.Timestamp(f"{ar + 1}-01-01")
            ar_rader.append([str(ar)] + [p(avk(port[s]["serie"], fr, ti)) for s in ("ml_insider30", "ml_filter", "ml_slump")]
                            + [p(avk(mark, fr, ti))])

    krav = [("K1. Godkända slår underkända totalt och i minst 2/3 av teståren", k1,
             f"{p(ok_all.mean())} mot {p(nej_all.mean())}, bättre {plus} av {antal_ar} år"),
            ("K2. Godkända slår minst 95 % av slumpfiltren", k2, f"slog {slog} av {SLUMPFILTER} (krav {int(0.95 * SLUMPFILTER)})"),
            ("K3. Portföljen med bara godkända slår Insider 30", k3,
             f"{p(avk(port['ml_filter']['serie']))} mot {p(avk(port['ml_insider30']['serie']))}" if port else "–"),
            ("K4. Portföljen slår slumpfiltret", k4,
             f"{p(avk(port['ml_filter']['serie']))} mot {p(avk(port['ml_slump']['serie']))}" if port else "–")]
    klarade = sum(1 for _, ok, _ in krav if ok)
    D += [f"## Resultat: {klarade} av {len(krav)} krav klarade" + (" – kandidat till Insider ML v1" if klarade == len(krav) else ""), "",
          tabell(["Krav", "Klarat", "Siffror"], [[n, "✅ ja" if ok else "❌ nej", t] for n, ok, t in krav]), "",
          "## Signalerna år för år (mot marknaden, före kostnad)", "",
          tabell(["År", "Signaler", "Godkända", "Godkända snitt", "Underkända snitt", "Skillnad", "Godkända efter kostnad",
                  "Skillnad logistisk"], rader), "",
          "Godkänd = modellens sannolikhet minst lika hög som medianen i träningsdatan. "
          "”Skillnad logistisk” = samma sak med jämförelsemodellen (logistisk regression).", ""]
    if port:
        D += ["## Portföljerna (samma regler som Insider 30)", "",
              tabell(["Portfölj", "Avkastning", "Mot marknaden", "Största nedgång", "Affärer"], prad), "",
              tabell(["År", "Insider 30", "Insider ML", "Slumpfilter", "Marknaden"], ar_rader), ""]
    if len(vikt):
        D += ["## Vad modellen tittar på", "",
              "Hur mycket sämre modellen blir på att skilja bra från dåliga signaler om egenskapen blandas om "
              "(permutationsvikt, AUC, snitt över teståren). Nära 0 = används knappt.", "",
              tabell(["Egenskap", "Vikt"], [[EGENSKAPER.get(k, k), f"{v:.3f}"] for k, v in vikt.items()]), ""]
    D += ["## Läs med försiktighet", "",
          "- Ungefär 150–200 signaler per år: lite data. En skillnad som inte syns de flesta år är troligen slump.",
          "- Färre köp betyder mer kassa. Portföljtestet (K3) visar om sorteringen lönar sig trots det.",
          "- Överlevnadsfel: bara bolag som är noterade idag ingår.",
          "- Ramarna och kraven ändras inte efter resultatet. Klaras de inte behålls Insider 30 som den är."]
    return _spara(D, df, utmapp)


def _spara(D: list[str], df: pd.DataFrame, utmapp: Path) -> tuple[Path, str]:
    md = "\n".join(D) + "\n"
    m = utmapp / "analys"
    m.mkdir(parents=True, exist_ok=True)
    idag = dt.date.today().isoformat()
    (m / f"insiderml_{idag}.md").write_text(md, encoding="utf-8")
    (m / f"insiderml_{idag}.html").write_text(ra._html(md, "Insider ML"), encoding="utf-8")
    df.to_csv(m / "insiderml_signaler.csv", index=False, float_format="%.6g")
    return m / f"insiderml_{idag}.html", md


def main() -> int:
    a = argparse.ArgumentParser(description="Maskininlärning på insidersignalerna")
    a.add_argument("--utmapp", default=sc.KONFIG["utmapp"])
    a.add_argument("--tickers", default=str(MAPP / "tickers_se.csv"))
    a.add_argument("--demo", action="store_true")
    a.add_argument("--utan-portfoljer", action="store_true", help="hoppa över portföljtestet (K3, K4) – snabbare")
    arg = a.parse_args()
    utmapp = Path(arg.utmapp).expanduser()
    try:
        import sklearn  # noqa: F401
    except ImportError:
        print("scikit-learn saknas: ./venv/bin/pip install scikit-learn", file=sys.stderr)
        return 1
    data = bt.insiderdata(utmapp, arg.demo, Path(arg.tickers))
    if arg.demo:
        data["aff"]["befattning"] = data["aff"].get("befattning", "")
        data["aff"]["narstaende"] = data["aff"].get("narstaende", False)
    rapporter = None
    if not arg.demo:
        try:
            rapporter = ra.las_rapporter(utmapp)
        except SystemExit:
            pass
    lordagar = bt.insider_lordagar(data)
    print(f"Bygger signaler {lordagar[0].date()} – {lordagar[-1].date()} ...", file=sys.stderr)
    df = bygg_signaler(data, lordagar, rapporter)
    if df.empty:
        print("Inga signaler.", file=sys.stderr)
        return 1
    print(f"{len(df)} signaler, {df['facit'].notna().sum()} med facit. Walk-forward ...", file=sys.stderr)
    df, modell_per_ar = walk_forward(df)
    vikt = betydelse(modell_per_ar)
    port = {}
    if not arg.utan_portfoljer and df["ok_gb"].notna().any():
        _, port = portfoljer(utmapp, data, df, arg.demo)
    fil, md = skriv(df, vikt, port, data, utmapp, arg.demo)
    print(md)
    print(f"Sparat: {fil}", file=sys.stderr)
    info = spara_livemodell(df, utmapp)
    if info:
        print(f"Livemodellen sparad ({modellfil(utmapp)}): tränad på {info['signaler']} signaler med facit till "
              f"{info['facit_till']}. Veckorapportens guldruta visar dess bedömning.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
