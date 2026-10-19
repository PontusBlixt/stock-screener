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
            "insider_senaste_kop", "blankning_pct", "blankning_andring_30d"]


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
# Sammanfattningar per bolag
# ---------------------------------------------------------------------------
def insider_sammanfattning(utmapp: Path, tickerfil: Path, slut: dt.date | None = None,
                           dagar: int = INSIDER_DAGAR) -> pd.DataFrame:
    slut = slut or dt.date.today()
    start = slut - dt.timedelta(days=dagar)
    df = las_insyn(utmapp, fran=start - dt.timedelta(days=31))
    tom = pd.DataFrame(columns=["bolag"] + KOLUMNER[:5])
    if df.empty:
        return tom
    df = df[_ar_giltig(df) & (df["transaktionsdatum"] >= pd.Timestamp(start))
            & (df["transaktionsdatum"] <= pd.Timestamp(slut))].copy()
    if df.empty:
        return tom
    lista = tickerlista(tickerfil)
    isin_bolag = dict(zip(lista["isin"], lista["bolag"]))
    isin_bolag.pop("", None)
    df["bolag"] = df["isin"].map(isin_bolag)
    # ISIN saknas i listan (t.ex. äldre tickers_se.csv utan isin): via LEI-kartan
    if df["bolag"].isna().any():
        karta = lei_karta(utmapp, tickerfil)
        df["bolag"] = df["bolag"].fillna(df["lei"].map(dict(zip(karta["lei"], karta["bolag"]))))
    df = df.dropna(subset=["bolag"])
    kop = df["karaktar"].str.lower().isin(["förvärv", "acquisition"])
    ut = pd.DataFrame({
        "insider_kop_sek": df[kop].groupby("bolag")["belopp"].sum(),
        "insider_salj_sek": df[~kop].groupby("bolag")["belopp"].sum(),
        "insider_kopare": df[kop].groupby("bolag")["person"].nunique(),
        "insider_senaste_kop": df[kop].groupby("bolag")["transaktionsdatum"].max(),
    }).fillna({"insider_kop_sek": 0.0, "insider_salj_sek": 0.0, "insider_kopare": 0})
    ut["insider_netto_sek"] = ut["insider_kop_sek"] - ut["insider_salj_sek"]
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
    for k in ("insider_kop_sek", "insider_salj_sek", "insider_netto_sek", "insider_kopare"):
        t[k] = t[k].fillna(0)
    t.index = tabell.index
    return t


def for_bolag(ticker: str, utmapp: Path, tickerfil: Path, dagar: int = 365) -> dict:
    """Detaljer för aktiesidan: insideraffärer senaste året och blankningens historik."""
    bolag = sc.bolagsnyckel(ticker)
    ut = {"affarer": pd.DataFrame(), "blankning": None, "blankning_hist": pd.DataFrame(), "har_data": False}
    lista = tickerlista(tickerfil)
    isin_bolag = dict(zip(lista["isin"], lista["bolag"]))
    isin_bolag.pop("", None)
    df = las_insyn(utmapp, fran=dt.date.today() - dt.timedelta(days=dagar + 31))
    if len(df):
        ut["har_data"] = True
        df = df[_ar_giltig(df)].copy()
        df["bolag"] = df["isin"].map(isin_bolag)
        if df["bolag"].isna().any():
            karta = lei_karta(utmapp, tickerfil)
            df["bolag"] = df["bolag"].fillna(df["lei"].map(dict(zip(karta["lei"], karta["bolag"]))))
        df = df[(df["bolag"] == bolag)
                & (df["transaktionsdatum"] >= pd.Timestamp(dt.date.today() - dt.timedelta(days=dagar)))]
        ut["affarer"] = df.sort_values("transaktionsdatum", ascending=False)
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
