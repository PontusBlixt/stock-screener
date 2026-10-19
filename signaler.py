"""
Signaler från insynsaffärer och blankning
=========================================

Kopplar datan som insamling.py sparar till våra tickers och sammanfattar den per bolag,
så att den kan visas i veckorapporten och på aktiesidan.

  INFORMATION, INTE REGLER: kolumnerna påverkar inte poängen, rankningen, köpreglerna eller
  säljreglerna. De visas som stöd när du själv granskar en kandidat. Visar analysen senare
  att de har effekt, blir de en del av en ny strategiversion som testas mot Momentum v1.

Koppling
  Insyn:     FI anger aktiens ISIN -> ticker via tickers_se.csv (kolumnen isin).
  Blankning: FI anger bara bolagets LEI -> lärs från insynsdatan (där både LEI och ISIN finns).
             Bolag som inte finns där matchas på namn. Kopplingen sparas i koppling_lei.csv.
  Flera aktieslag (A/B) räknas som ett bolag (screener.bolagsnyckel).

Insider 90 d (per bolag, transaktionsdatum de senaste 90 dagarna)
  Bara aktier, bara Förvärv och Avyttring. Affärer kopplade till aktieprogram och
  makulerade/ersatta rader räknas inte. Belopp = volym × pris (utländsk valuta räknas som den är).
    insider_kop_sek, insider_salj_sek, insider_netto_sek, insider_kopare (antal personer som köpt),
    insider_senaste_kop (datum)

Blankning
    blankning_pct          total blankning i procent (FI:s tabell, positioner över 0,1 %)
    blankning_andring_30d  förändring i procentenheter mot läget för 30 dagar sedan (när historik finns)
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

import screener as sc

INSIDER_DAGAR = 90
KOLUMNER = ["insider_kop_sek", "insider_salj_sek", "insider_netto_sek", "insider_kopare",
            "insider_senaste_kop", "insider_saljare", "insider_netto_pct", "blankning_pct", "blankning_andring_30d"]


# ---------------------------------------------------------------------------
# Hjälpfunktioner
# ---------------------------------------------------------------------------
def _tal(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.replace("\xa0", "", regex=False).str.replace(" ", "", regex=False)
    return pd.to_numeric(s.str.replace(",", ".", regex=False), errors="coerce")


def _datum(s: pd.Series) -> pd.Series:
    """FI skriver både '2026-10-14 00:00:00' och '14/10/2026 00:00:00'."""
    s = s.astype(str).str.strip()
    iso = pd.to_datetime(s.where(s.str.match(r"^\d{4}-")), errors="coerce")
    eu = pd.to_datetime(s.where(s.str.match(r"^\d{1,2}/")), format="%d/%m/%Y %H:%M:%S", errors="coerce")
    return iso.fillna(eu).dt.normalize()


def normalisera_namn(namn: str) -> str:
    """'Aktiebolaget Volvo (publ)' -> 'volvo'. För att matcha bolagsnamn mellan källor."""
    t = unicodedata.normalize("NFKD", str(namn)).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\(.*?\)", " ", t)
    t = re.sub(r"\b(aktiebolaget|aktiebolag|ab|publ|asa|oyj|plc|ltd|sa|nv|se|inc|corp|group|holding|holdings"
               r"|ser|serie|class|a|b|c|d|sdb)\b", " ", t)
    return re.sub(r"[^a-z0-9]+", "", t)


def tickerlista(tickerfil: Path) -> pd.DataFrame:
    df = pd.read_csv(tickerfil, comment="#", dtype=str, keep_default_na=False, skipinitialspace=True)
    df["ticker"] = df["ticker"].str.strip()
    if "isin" not in df.columns:
        df["isin"] = ""
    df["bolag"] = df["ticker"].map(sc.bolagsnyckel)
    df["namn_n"] = df["namn"].map(normalisera_namn)
    return df


# ---------------------------------------------------------------------------
# Insyn
# ---------------------------------------------------------------------------
def las_insyn(utmapp: Path, fran: dt.date | None = None) -> pd.DataFrame:
    """Insynsaffärer, normaliserade. fran = läs bara månadsfiler från och med den månaden."""
    m = Path(utmapp) / "insyn"
    filer = sorted(m.glob("????-??.csv")) if m.exists() else []
    if fran is not None:
        filer = [f for f in filer if f.stem >= f"{fran:%Y-%m}"]
    delar = []
    for f in filer:
        try:
            d = pd.read_csv(f, dtype=str, keep_default_na=False)
        except (pd.errors.EmptyDataError, ValueError):
            continue
        if len(d):
            delar.append(d)
    if not delar:
        return pd.DataFrame()
    df = pd.concat(delar, ignore_index=True)

    def kol(namn):
        return df[namn] if namn in df.columns else pd.Series("", index=df.index)

    ut = pd.DataFrame({
        "publicerad": _datum(kol("Publiceringsdatum")),
        "transaktionsdatum": _datum(kol("Transaktionsdatum")),
        "emittent": kol("Emittent").str.strip(),
        "lei": kol("LEI-kod").str.strip().str.upper(),
        "person": kol("Person i ledande ställning").str.strip(),
        "befattning": kol("Befattning").str.strip(),
        "narstaende": kol("Närstående").str.strip().str.lower().isin(["ja", "yes"]),
        "karaktar": kol("Karaktär").str.strip(),
        "instrumenttyp": kol("Instrumenttyp").str.strip(),
        "isin": kol("ISIN").str.strip().str.upper(),
        "volym": _tal(kol("Volym")),
        "pris": _tal(kol("Pris")),
        "valuta": kol("Valuta").str.strip().str.upper(),
        "aktieprogram": kol("Är kopplad till aktieprogram").str.strip().str.lower().isin(["ja", "yes"]),
        "status": kol("Status").str.strip(),
    })
    ut["belopp"] = ut["volym"] * ut["pris"]
    return ut.drop_duplicates()


def _ar_giltig(df: pd.DataFrame) -> pd.Series:
    """Aktier, köp eller sälj, inte aktieprogram. Status Aktuell – en korrigerad anmälan finns kvar som
    Reviderad (den gamla) och Aktuell (den nya), så bara Aktuell räknas. Makulerade räknas inte."""
    k = df["karaktar"].str.lower()
    typ = df["instrumenttyp"].str.lower()
    status = df["status"].str.lower()
    return ((typ.str.contains("aktie") | (typ == "share")) & k.isin(["förvärv", "avyttring", "acquisition", "disposal"])
            & ~df["aktieprogram"] & status.isin(["aktuell", "current", ""]))  # inte Reviderad/Makulerad


# ---------------------------------------------------------------------------
# Koppling LEI/ISIN -> bolag
# ---------------------------------------------------------------------------
def lei_karta(utmapp: Path, tickerfil: Path, blankning: pd.DataFrame | None = None) -> pd.DataFrame:
    """lei -> bolag. Lärs från insynsdatan (LEI + ISIN), sedan namnmatchning för resten.
    Sparas i koppling_lei.csv och byggs om när den är äldre än en vecka."""
    fil = Path(utmapp) / "koppling_lei.csv"
    if fil.exists() and (dt.date.today() - dt.date.fromtimestamp(fil.stat().st_mtime)).days < 7:
        karta = pd.read_csv(fil, dtype=str, keep_default_na=False)
    else:
        lista = tickerlista(tickerfil)
        isin_bolag = dict(zip(lista["isin"], lista["bolag"]))
        isin_bolag.pop("", None)
        rader = []
        insyn = las_insyn(utmapp)
        if len(insyn):
            par = insyn[(insyn["lei"] != "") & (insyn["isin"] != "")][["lei", "isin", "emittent"]].drop_duplicates()
            par["bolag"] = par["isin"].map(isin_bolag)
            traff = par.dropna(subset=["bolag"])
            # Vanligast förekommande bolag per LEI (ett bolag kan ha flera ISIN – aktieslag, teckningsrätter)
            for lei, g in traff.groupby("lei"):
                rader.append({"lei": lei, "bolag": g["bolag"].mode().iloc[0], "metod": "isin",
                              "emittent": g["emittent"].iloc[0]})
            # Emittenter utan ISIN-träff: matcha på namn
            kanda = {r["lei"] for r in rader}
            namn_bolag = dict(zip(lista["namn_n"], lista["bolag"]))
            for lei, g in par[~par["lei"].isin(kanda)].groupby("lei"):
                b = namn_bolag.get(normalisera_namn(g["emittent"].iloc[0]))
                if b:
                    rader.append({"lei": lei, "bolag": b, "metod": "namn", "emittent": g["emittent"].iloc[0]})
        karta = pd.DataFrame(rader, columns=["lei", "bolag", "metod", "emittent"])
        if len(karta):
            karta.to_csv(fil, index=False)
    # Blankade bolag som inte finns i kartan: namnmatchning direkt (sparas inte – billigt)
    if blankning is not None and len(blankning):
        saknas = blankning[~blankning["lei"].isin(set(karta["lei"]))]
        if len(saknas):
            lista = tickerlista(tickerfil)
            namn_bolag = dict(zip(lista["namn_n"], lista["bolag"]))
            extra = [{"lei": r.lei, "bolag": namn_bolag.get(normalisera_namn(r.emittent)), "metod": "namn",
                      "emittent": r.emittent} for r in saknas.itertuples()]
            extra = pd.DataFrame(extra).dropna(subset=["bolag"])
            karta = pd.concat([karta, extra], ignore_index=True)
    return karta


# ---------------------------------------------------------------------------
# Belopp i kronor
# ---------------------------------------------------------------------------
# Reserv om växelkursen inte går att hämta (ungefärliga, uppdateras sällan)
FX_RESERV = {"SEK": 1.0, "EUR": 11.0, "USD": 9.6, "DKK": 1.47, "NOK": 0.93, "GBP": 12.7, "CHF": 11.9, "ISK": 0.077,
             "PLN": 2.6, "CAD": 7.0}


def valutakurser(utmapp: Path, valutor: set[str]) -> dict[str, float]:
    """Växelkurs till SEK per valuta. kurser.csv först, sedan valutor.csv (hämtas från Yahoo högst en gång
    i veckan), sist ungefärliga reservkurser."""
    valutor = {v for v in valutor if v and v != "SEK"}
    ut = {"SEK": 1.0, "": 1.0}
    if not valutor:
        return ut
    import innehav as ih
    k = ih.las_kurser(Path(utmapp) / "kurser.csv")
    for v in list(valutor):
        c = f"{v}SEK=X"
        if c in k.columns and k[c].notna().any():
            ut[v] = float(k[c].dropna().iloc[-1])
            valutor.discard(v)
    fil = Path(utmapp) / "valutor.csv"
    sparade = pd.read_csv(fil).set_index("valuta")["kurs"].to_dict() if fil.exists() else {}
    gammal = not fil.exists() or (dt.date.today() - dt.date.fromtimestamp(fil.stat().st_mtime)).days >= 7
    saknas = [v for v in valutor if v not in sparade]
    if gammal or saknas:
        try:
            data = sc.hamta_kurser([f"{v}SEK=X" for v in set(valutor) | set(sparade)], "5d")
            for t, d in data.items():
                if len(d["Close"].dropna()):
                    sparade[t.removesuffix("SEK=X")] = float(d["Close"].dropna().iloc[-1])
            pd.DataFrame({"valuta": list(sparade), "kurs": list(sparade.values())}).to_csv(fil, index=False)
        except Exception:
            pass
    for v in valutor:
        ut[v] = sparade.get(v, FX_RESERV.get(v, np.nan))
    return ut


def till_kronor(df: pd.DataFrame, utmapp: Path, tickerfil: Path) -> pd.DataFrame:
    """Lägger till bolag, ticker och belopp_sek. Utländsk valuta räknas om till kronor. Är priset närmare
    aktiens stängningskurs i kronor än i den angivna valutan, tolkas det som kronor (FI-anmälningar har
    ibland fel valuta angiven, t.ex. DKK för en aktie som handlas i SEK)."""
    import innehav as ih
    df = df.copy()
    lista = tickerlista(tickerfil)
    isin_ticker = dict(zip(lista["isin"], lista["ticker"]))
    isin_ticker.pop("", None)
    df["ticker"] = df["isin"].map(isin_ticker)
    df["bolag"] = df["ticker"].map(lambda t: sc.bolagsnyckel(t) if isinstance(t, str) else None)
    if df["bolag"].isna().any():  # ISIN saknas i listan: via LEI-kartan
        karta = lei_karta(utmapp, tickerfil)
        df["bolag"] = df["bolag"].fillna(df["lei"].map(dict(zip(karta["lei"], karta["bolag"]))))
    fx = valutakurser(utmapp, set(df["valuta"]))
    df["fx"] = df["valuta"].map(lambda v: fx.get(v, np.nan))
    df["valuta_tolkad"] = df["valuta"].replace("", "SEK")
    utl = (df["valuta"] != "SEK") & (df["valuta"] != "") & df["ticker"].notna()
    if utl.any():
        k = ih.las_kurser(Path(utmapp) / "kurser.csv")
        for i in df.index[utl]:
            t = df.at[i, "ticker"]
            if t not in k.columns:
                continue
            s = k[t].dropna()
            stang = s.asof(df.at[i, "transaktionsdatum"]) if len(s) else np.nan
            pris, f = df.at[i, "pris"], df.at[i, "fx"]
            if pd.notna(stang) and stang > 0 and pd.notna(pris) and pris > 0 and pd.notna(f):
                if abs(np.log(pris / stang)) < abs(np.log(pris * f / stang)):
                    df.at[i, "fx"] = 1.0
                    df.at[i, "valuta_tolkad"] = "SEK"
    df["belopp_sek"] = df["belopp"] * df["fx"]
    return df


def borsvarden(utmapp: Path) -> dict[str, float]:
    """Börsvärde i kronor per bolag ur senaste nyckeltalsfilen (största värdet om bolaget har flera aktieslag)."""
    m = Path(utmapp) / "nyckeltal"
    filer = sorted(m.glob("????-??-??.csv")) if m.exists() else []
    if not filer:
        return {}
    d = pd.read_csv(filer[-1], usecols=["ticker", "borsvarde"])
    d["bolag"] = d["ticker"].map(sc.bolagsnyckel)
    return d.groupby("bolag")["borsvarde"].max().dropna().to_dict()


# ---------------------------------------------------------------------------
# Sammanfattningar per bolag
# ---------------------------------------------------------------------------
def insider_sammanfattning(utmapp: Path, tickerfil: Path, slut: dt.date | None = None,
                           dagar: int = INSIDER_DAGAR) -> pd.DataFrame:
    slut = slut or dt.date.today()
    start = slut - dt.timedelta(days=dagar)
    df = las_insyn(utmapp, fran=start - dt.timedelta(days=31))
    tom = pd.DataFrame(columns=["bolag"] + KOLUMNER[:7])
    if df.empty:
        return tom
    df = df[_ar_giltig(df) & (df["transaktionsdatum"] >= pd.Timestamp(start))
            & (df["transaktionsdatum"] <= pd.Timestamp(slut))].copy()
    if df.empty:
        return tom
    df = till_kronor(df, utmapp, tickerfil).dropna(subset=["bolag"])
    kop = df["karaktar"].str.lower().isin(["förvärv", "acquisition"])
    ut = pd.DataFrame({
        "insider_kop_sek": df[kop].groupby("bolag")["belopp_sek"].sum(),
        "insider_salj_sek": df[~kop].groupby("bolag")["belopp_sek"].sum(),
        "insider_kopare": df[kop].groupby("bolag")["person"].nunique(),
        "insider_saljare": df[~kop].groupby("bolag")["person"].nunique(),
        "insider_senaste_kop": df[kop].groupby("bolag")["transaktionsdatum"].max(),
    }).fillna({"insider_kop_sek": 0.0, "insider_salj_sek": 0.0, "insider_kopare": 0, "insider_saljare": 0})
    ut["insider_netto_sek"] = ut["insider_kop_sek"] - ut["insider_salj_sek"]
    bv = borsvarden(utmapp)
    ut["insider_netto_pct"] = [n / bv[b] if bv.get(b) else np.nan for b, n in zip(ut.index, ut["insider_netto_sek"])]
    return ut.reset_index().rename(columns={"index": "bolag"})


def blankning_sammanfattning(utmapp: Path, tickerfil: Path, dag: dt.date | None = None) -> pd.DataFrame:
    tom = pd.DataFrame(columns=["bolag", "blankning_pct", "blankning_andring_30d"])
    m = Path(utmapp) / "blankning"
    if not (m / "senaste.csv").exists():
        return tom
    nu = pd.read_csv(m / "senaste.csv", dtype={"lei": str})
    if nu.empty:
        return tom
    karta = lei_karta(utmapp, tickerfil, nu)
    nu["bolag"] = nu["lei"].map(dict(zip(karta["lei"], karta["bolag"])))
    nu = nu.dropna(subset=["bolag"])
    ut = nu.groupby("bolag")["summa_pct"].max().rename("blankning_pct").to_frame()
    # Förändring mot läget för 30 dagar sedan, ur ändringsloggen
    andr = m / "andringar.csv"
    ut["blankning_andring_30d"] = np.nan
    if andr.exists():
        logg = pd.read_csv(andr, dtype={"lei": str})
        logg["hamtad"] = pd.to_datetime(logg["hamtad"])
        grans = pd.Timestamp(dag or dt.date.today()) - pd.Timedelta(days=30)
        fore = logg[logg["hamtad"] <= grans]
        if len(fore) and logg["hamtad"].min() <= grans:
            dåvarande = fore.sort_values("hamtad").drop_duplicates("lei", keep="last")
            dåvarande["bolag"] = dåvarande["lei"].map(dict(zip(karta["lei"], karta["bolag"])))
            da = dåvarande.dropna(subset=["bolag"]).groupby("bolag")["summa_pct"].max()
            ut["blankning_andring_30d"] = ut["blankning_pct"] - da.reindex(ut.index).fillna(0.0)
    return ut.reset_index()


def berika(tabell: pd.DataFrame, utmapp: Path, tickerfil: Path, dag: dt.date | None = None) -> pd.DataFrame:
    """Lägger till insider- och blankningskolumnerna i screenerns tabell (påverkar inget annat)."""
    t = tabell.copy()
    if "bolag" not in t.columns:
        t["bolag"] = t["ticker"].map(sc.bolagsnyckel)
    t = t.drop(columns=[k for k in KOLUMNER if k in t.columns])
    ins = insider_sammanfattning(utmapp, tickerfil, dag)
    bl = blankning_sammanfattning(utmapp, tickerfil, dag)
    t = t.merge(ins, on="bolag", how="left").merge(bl, on="bolag", how="left")
    for k in ("insider_kop_sek", "insider_salj_sek", "insider_netto_sek", "insider_kopare", "insider_saljare"):
        t[k] = t[k].fillna(0)
    t.index = tabell.index
    return t


def for_bolag(ticker: str, utmapp: Path, tickerfil: Path, dagar: int = 365) -> dict:
    """Detaljer för aktiesidan: insideraffärer senaste året och blankningens historik."""
    bolag = sc.bolagsnyckel(ticker)
    ut = {"affarer": pd.DataFrame(), "blankning": None, "blankning_hist": pd.DataFrame(), "har_data": False}
    ut["per_person"] = pd.DataFrame()
    ut["borsvarde"] = borsvarden(utmapp).get(bolag)
    df = las_insyn(utmapp, fran=dt.date.today() - dt.timedelta(days=dagar + 31))
    if len(df):
        ut["har_data"] = True
        df = df[_ar_giltig(df) & (df["transaktionsdatum"] >= pd.Timestamp(dt.date.today() - dt.timedelta(days=dagar)))]
        lista = tickerlista(tickerfil)
        isins = set(lista.loc[lista["bolag"] == bolag, "isin"]) - {""}
        karta = lei_karta(utmapp, tickerfil)
        leis = set(karta.loc[karta["bolag"] == bolag, "lei"])
        df = df[df["isin"].isin(isins) | df["lei"].isin(leis)]  # snabbt förurval innan omräkningen
        if len(df):
            df = till_kronor(df, utmapp, tickerfil)
            df = df[df["bolag"] == bolag].copy()
            df["kop"] = df["karaktar"].str.lower().isin(["förvärv", "acquisition"])
            ut["affarer"] = df.sort_values(["transaktionsdatum", "belopp_sek"], ascending=[False, False])
            # En rad per person: köpt, sålt, antal affärer, senaste datum – störst belopp först
            df["kop_sek"] = np.where(df["kop"], df["belopp_sek"], 0.0)
            df["salj_sek"] = np.where(df["kop"], 0.0, df["belopp_sek"])
            df["kop_antal"] = np.where(df["kop"], df["volym"], 0.0)
            df["salj_antal"] = np.where(df["kop"], 0.0, df["volym"])
            pp = df.groupby(["person", "befattning"], dropna=False).agg(
                narstaende=("narstaende", "max"), kop_sek=("kop_sek", "sum"), salj_sek=("salj_sek", "sum"),
                kop_antal=("kop_antal", "sum"), salj_antal=("salj_antal", "sum"), affarer=("kop", "size"),
                forsta=("transaktionsdatum", "min"), senaste=("transaktionsdatum", "max")).reset_index()
            pp["netto_sek"] = pp["kop_sek"] - pp["salj_sek"]
            ut["per_person"] = pp.reindex(pp["netto_sek"].abs().sort_values(ascending=False).index)
    m = Path(utmapp) / "blankning"
    if (m / "senaste.csv").exists():
        ut["har_data"] = True
        bl = blankning_sammanfattning(utmapp, tickerfil)
        rad = bl[bl["bolag"] == bolag]
        ut["blankning"] = rad.iloc[0].to_dict() if len(rad) else {"blankning_pct": 0.0}
        if (m / "andringar.csv").exists():
            logg = pd.read_csv(m / "andringar.csv", dtype={"lei": str})
            karta = lei_karta(utmapp, tickerfil, pd.read_csv(m / "senaste.csv", dtype={"lei": str}))
            leis = set(karta.loc[karta["bolag"] == bolag, "lei"])
            ut["blankning_hist"] = logg[logg["lei"].isin(leis)].sort_values("hamtad")
    return ut
