#!/usr/bin/env python3
"""
Momentum-screener för svenska aktier
====================================

Vad den gör, i fyra steg:
  1. HÄMTAR   2 års dagliga kurser för alla aktier i tickers_se.csv (Yahoo Finance).
  2. MÄTER    momentum (hur mycket aktien gått upp), trend (glidande medelvärden),
              risk (volatilitet) och likviditet (hur mycket som handlas per dag).
  3. FILTRERAR bort aktier i nedtrend eller som handlas för lite.
  4. RANKAR   de som är kvar efter momentum och skriver en rapport.

Logiken bakom: aktier som gått starkt de senaste 6-12 månaderna har historiskt
tenderat att fortsätta gå bra några månader till ("momentumeffekten"). Den senaste
månaden hoppas över i 12-månadersmåttet eftersom aktier på mycket kort sikt
tvärtom tenderar att studsa tillbaka lite.

Körning:
  python3 screener.py                      # vanlig körning
  python3 screener.py --innehav SWED-A.ST,SEB-A.ST   # flagga dina innehav
  python3 screener.py --demo               # testa utan internet (påhittad data)

Detta är ett verktyg för att hitta kandidater, inte köprekommendationer.
"""

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# INSTÄLLNINGAR – ändra här, inte längre ner i koden
# ---------------------------------------------------------------------------
KONFIG = {
    "historik": "2y",                 # hur mycket kurshistorik som hämtas
    "min_dagar": 260,                 # minst ~1 års handel krävs för att mäta momentum
    "min_omsattning_sek": 5_000_000,  # snittomsättning/dag (20 dagar) – annars för illikvid
    "krav_over_ma200": True,          # kursen måste ligga över 200-dagars medelvärde
    "krav_ma50_over_ma200": True,     # 50-dagars måste ligga över 200-dagars ("golden cross-läge")
    "vikt_mom_12_1": 0.5,             # vikt för 12-månaders momentum (exkl. senaste månaden)
    "vikt_mom_6m": 0.5,               # vikt för 6-månaders momentum
    "topp_n": 20,                     # hur många som visas i topplistan
}

# Ungefärligt antal handelsdagar
DAGAR_1M, DAGAR_3M, DAGAR_6M, DAGAR_12M = 21, 63, 126, 252

MAPP = Path(__file__).resolve().parent
RAPPORTMAPP = MAPP / "rapporter"


# ---------------------------------------------------------------------------
# STEG 1: HÄMTA DATA
# ---------------------------------------------------------------------------
def las_tickers(fil: Path) -> pd.DataFrame:
    """Läser tickerlistan. Rader som börjar med # ignoreras."""
    df = pd.read_csv(fil, comment="#", skipinitialspace=True)
    df["ticker"] = df["ticker"].str.strip()
    df["namn"] = df["namn"].str.strip()
    return df.drop_duplicates("ticker")


def hamta_kurser(tickers: list[str], period: str) -> dict[str, pd.DataFrame]:
    """Hämtar dagliga kurser från Yahoo Finance. Returnerar {ticker: DataFrame(Close, Volume)}.
    auto_adjust=True betyder att kurserna är justerade för utdelningar och splittar,
    så att en utdelning inte ser ut som ett kursfall."""
    import time
    import yfinance as yf  # importeras här så att --demo fungerar utan yfinance

    # Hämta i omgångar om 100 – Yahoo stryper ibland stora anrop
    BATCH = 100
    data = {}
    for i in range(0, len(tickers), BATCH):
        grupp = tickers[i:i + BATCH]
        print(f"  hämtar {i + 1}-{i + len(grupp)} av {len(tickers)} ...", file=sys.stderr)
        raw = yf.download(grupp, period=period, auto_adjust=True,
                          group_by="ticker", threads=True, progress=False)
        for t in grupp:
            try:
                df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = df[["Close", "Volume"]].dropna()
                if len(df) > 0:
                    data[t] = df
            except KeyError:
                pass  # tickern fanns inte hos Yahoo – rapporteras som saknad
        if i + BATCH < len(tickers):
            time.sleep(2)
    return data


def demodata(tickers: list[str], seed: int = 42) -> dict[str, pd.DataFrame]:
    """Påhittade kurser (slumpvandring med olika trend per aktie) för att testa utan internet."""
    rng = np.random.default_rng(seed)
    dagar = pd.bdate_range(end=dt.date.today(), periods=500)
    data = {}
    for t in tickers:
        trend = rng.normal(0.0002, 0.0008)          # årlig drift mellan ca -35% och +45%
        vol = rng.uniform(0.012, 0.03)               # daglig volatilitet
        avk = rng.normal(trend, vol, len(dagar))
        kurs = 100 * np.exp(np.cumsum(avk))
        volym = rng.uniform(20_000, 2_000_000) * np.ones(len(dagar))
        data[t] = pd.DataFrame({"Close": kurs, "Volume": volym}, index=dagar)
    return data


# ---------------------------------------------------------------------------
# STEG 2: MÄT
# ---------------------------------------------------------------------------
def berakna_nyckeltal(df: pd.DataFrame) -> dict | None:
    """Räknar fram alla mått för en aktie. Returnerar None om historiken är för kort."""
    close, volym = df["Close"], df["Volume"]
    if len(close) < KONFIG["min_dagar"]:
        return None

    sista = close.iloc[-1]

    def avkastning(dagar_bakat: int, hoppa_over: int = 0) -> float:
        """Avkastning från för 'dagar_bakat' dagar sedan till för 'hoppa_over' dagar sedan."""
        return close.iloc[-1 - hoppa_over] / close.iloc[-1 - dagar_bakat] - 1

    ma50 = close.rolling(50).mean().iloc[-1]
    ma200 = close.rolling(200).mean().iloc[-1]
    dagsavkastning = close.pct_change()

    return {
        "kurs": sista,
        # Momentum
        "mom_12_1": avkastning(DAGAR_12M, hoppa_over=DAGAR_1M),  # 12 mån exkl. senaste månaden
        "mom_6m": avkastning(DAGAR_6M),
        "mom_3m": avkastning(DAGAR_3M),
        "mom_1m": avkastning(DAGAR_1M),
        # Trend
        "ma50": ma50,
        "ma200": ma200,
        "over_ma200": bool(sista > ma200),
        "ma50_over_ma200": bool(ma50 > ma200),
        "avstand_ma200": sista / ma200 - 1,
        "fran_52v_hogsta": sista / close.iloc[-DAGAR_12M:].max() - 1,
        # Risk: årlig volatilitet beräknad på senaste 3 månaderna
        "volatilitet": dagsavkastning.iloc[-DAGAR_3M:].std() * np.sqrt(252),
        # Likviditet: genomsnittlig omsättning i kronor per dag senaste 20 dagarna
        "omsattning_sek": (close * volym).iloc[-20:].mean(),
        "datum": close.index[-1].date(),
    }


# ---------------------------------------------------------------------------
# STEG 3 + 4: FILTRERA OCH RANKA
# ---------------------------------------------------------------------------
def filtrera_och_ranka(tabell: pd.DataFrame) -> pd.DataFrame:
    """Sätter kolumnen 'godkand' + 'orsak' (varför den föll bort) och ger godkända en poäng 0-100."""
    orsaker = []
    for _, r in tabell.iterrows():
        o = []
        if r["omsattning_sek"] < KONFIG["min_omsattning_sek"]:
            o.append("låg omsättning")
        if KONFIG["krav_over_ma200"] and not r["over_ma200"]:
            o.append("under MA200")
        if KONFIG["krav_ma50_over_ma200"] and not r["ma50_over_ma200"]:
            o.append("MA50 under MA200")
        orsaker.append(", ".join(o))
    tabell["orsak"] = orsaker
    tabell["godkand"] = tabell["orsak"] == ""

    # Poäng: percentilrank (0-1) inom de godkända, viktat ihop och skalat till 0-100.
    # Rank i stället för råa procenttal gör att en enda extrem aktie inte dominerar.
    g = tabell["godkand"]
    tabell["poang"] = np.nan
    if g.any():
        r12 = tabell.loc[g, "mom_12_1"].rank(pct=True)
        r6 = tabell.loc[g, "mom_6m"].rank(pct=True)
        tabell.loc[g, "poang"] = 100 * (KONFIG["vikt_mom_12_1"] * r12 + KONFIG["vikt_mom_6m"] * r6)

    return tabell.sort_values(["godkand", "poang"], ascending=[False, False])


# ---------------------------------------------------------------------------
# RAPPORT
# ---------------------------------------------------------------------------
def pct(x: float) -> str:
    return "" if pd.isna(x) else f"{x * 100:+.1f}%"


def tabell_md(df: pd.DataFrame, kolumner: list[tuple[str, str, callable]]) -> str:
    """Enkel markdown-tabell utan extra beroenden. kolumner = [(rubrik, kolumn, formatfunktion)]."""
    rader = ["| " + " | ".join(r for r, _, _ in kolumner) + " |",
             "|" + "|".join("---" for _ in kolumner) + "|"]
    for _, rad in df.iterrows():
        rader.append("| " + " | ".join(f(rad[k]) for _, k, f in kolumner) + " |")
    return "\n".join(rader)


def forra_topplistan(idag: str) -> set[str] | None:
    """Hittar senaste tidigare körning och returnerar dess topplista."""
    tidigare = sorted(p for p in RAPPORTMAPP.glob("screener_*.csv") if p.stem != f"screener_{idag}")
    if not tidigare:
        return None
    df = pd.read_csv(tidigare[-1])
    return set(df[df["topp"]]["ticker"])


def skriv_rapport(tabell: pd.DataFrame, saknade: list[str], innehav: list[str], demo: bool) -> Path:
    RAPPORTMAPP.mkdir(exist_ok=True)
    idag = dt.date.today().isoformat()
    n = KONFIG["topp_n"]

    godkanda = tabell[tabell["godkand"]]
    topp = godkanda.head(n)
    tabell["topp"] = tabell["ticker"].isin(topp["ticker"])

    # Spara all data som CSV (används för jämförelse nästa vecka och för egen analys)
    tabell.to_csv(RAPPORTMAPP / f"screener_{idag}.csv", index=False)

    kol = [
        ("#", "plats", lambda x: str(int(x))),
        ("Ticker", "ticker", str),
        ("Namn", "namn", str),
        ("Poäng", "poang", lambda x: f"{x:.0f}"),
        ("12-1 mån", "mom_12_1", pct),
        ("6 mån", "mom_6m", pct),
        ("1 mån", "mom_1m", pct),
        ("Över MA200", "avstand_ma200", pct),
        ("Från 52v-topp", "fran_52v_hogsta", pct),
        ("Volatilitet", "volatilitet", lambda x: f"{x * 100:.0f}%"),
        ("Oms/dag (MSEK)", "omsattning_sek", lambda x: f"{x / 1e6:.0f}"),
    ]
    topp = topp.copy()
    topp.insert(0, "plats", range(1, len(topp) + 1))

    delar = [f"# Momentum-screener – {idag}" + (" (DEMODATA)" if demo else ""),
             "",
             f"Analyserade: {len(tabell)} aktier · Klarade filtren: {len(godkanda)} · "
             f"Saknade data: {len(saknade)}",
             "",
             f"## Topp {n}",
             "",
             tabell_md(topp, kol)]

    # Förändring mot förra körningen
    forra = forra_topplistan(idag)
    if forra is not None:
        nu = set(topp["ticker"])
        nya, ute = sorted(nu - forra), sorted(forra - nu)
        delar += ["", "## Förändring sedan förra körningen", "",
                  f"- **Nya på listan:** {', '.join(nya) or '–'}",
                  f"- **Föll ur listan:** {', '.join(ute) or '–'}"]

    # Dina innehav
    if innehav:
        delar += ["", "## Dina innehav", ""]
        for t in innehav:
            rad = tabell[tabell["ticker"] == t]
            if rad.empty:
                delar.append(f"- **{t}:** finns inte i analysen (saknas i tickerlistan eller saknar data)")
                continue
            r = rad.iloc[0]
            if r["godkand"]:
                plats = list(godkanda["ticker"]).index(t) + 1
                delar.append(f"- **{t}:** klarar filtren, plats {plats} av {len(godkanda)} (poäng {r['poang']:.0f})")
            else:
                delar.append(f"- **{t}:** ⚠ klarar INTE filtren ({r['orsak']}) – granska enligt dina säljregler")

    # Varför andra föll bort
    bort = tabell[~tabell["godkand"]]
    if len(bort):
        # Med hundratals aktier blir en full lista oläslig – visa antal per orsak.
        # Varje akties orsak finns i CSV-filen (kolumnen 'orsak').
        antal = {}
        for o in bort["orsak"]:
            for del_ in o.split(", "):
                antal[del_] = antal.get(del_, 0) + 1
        delar += ["", f"## Bortfiltrerade: {len(bort)} aktier", ""]
        delar += [f"- {orsak}: {n}" for orsak, n in sorted(antal.items(), key=lambda x: -x[1])]
        delar += ["", "En aktie kan falla på flera filter. Se kolumnen `orsak` i CSV-filen för detaljer."]
    if saknade:
        delar += ["", "## Saknade data (kontrollera tickern)", "", ", ".join(saknade)]

    delar += ["", "---", "Kandidater att granska vidare, inte köprekommendationer."]

    md = RAPPORTMAPP / f"rapport_{idag}.md"
    md.write_text("\n".join(delar) + "\n", encoding="utf-8")
    return md


# ---------------------------------------------------------------------------
# HUVUDPROGRAM
# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description="Momentum-screener för svenska aktier")
    p.add_argument("--tickers", default=str(MAPP / "tickers_se.csv"), help="tickerlista (CSV)")
    p.add_argument("--innehav", default="", help="kommaseparerade tickers du äger, t.ex. SWED-A.ST")
    p.add_argument("--demo", action="store_true", help="kör med påhittad data (ingen internet)")
    a = p.parse_args()

    lista = las_tickers(Path(a.tickers))
    namn = dict(zip(lista["ticker"], lista["namn"]))
    tickers = list(lista["ticker"])
    innehav = [t.strip() for t in a.innehav.split(",") if t.strip()]

    print(f"Hämtar kurser för {len(tickers)} aktier ...", file=sys.stderr)
    kurser = demodata(tickers) if a.demo else hamta_kurser(tickers, KONFIG["historik"])

    rader, saknade = [], []
    for t in tickers:
        if t not in kurser:
            saknade.append(t)
            continue
        n = berakna_nyckeltal(kurser[t])
        if n is None:
            saknade.append(f"{t} (för kort historik)")
            continue
        rader.append({"ticker": t, "namn": namn[t], **n})

    if not rader:
        print("Ingen data kunde hämtas. Kontrollera internetanslutning och tickerlista.", file=sys.stderr)
        return 1

    tabell = filtrera_och_ranka(pd.DataFrame(rader))
    rapport = skriv_rapport(tabell, saknade, innehav, a.demo)
    print(rapport.read_text(encoding="utf-8"))
    print(f"\nRapport sparad: {rapport}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
