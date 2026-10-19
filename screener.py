#!/usr/bin/env python3
"""
Momentum-screener för svenska aktier
====================================

Vad den gör, i fem steg:
  1. HÄMTAR    2 års dagliga kurser för alla aktier i tickers_se.csv (Yahoo Finance).
  2. MÄTER     momentum (hur mycket aktien gått upp), trend (glidande medelvärden),
               risk (volatilitet) och likviditet (hur mycket som handlas per dag).
  3. FILTRERAR bort aktier i nedtrend eller som handlas för lite, och behåller bara
               det mest likvida aktieslaget per bolag (t.ex. SSAB B men inte SSAB A).
  4. RANKAR    de som är kvar efter momentum.
  5. MARKERAR  vilka i topplistan som klarar urvalsreglerna (inte för uttänjda,
               tillräckligt likvida, inte för volatila) och skriver rapporter.

Logiken bakom: aktier som gått starkt de senaste 6-12 månaderna har historiskt
tenderat att fortsätta gå bra några månader till ("momentumeffekten"). Den senaste
månaden hoppas över i 12-månadersmåttet eftersom aktier på mycket kort sikt
tvärtom tenderar att studsa tillbaka lite.

Körning:
  python3 screener.py                                   # vanlig körning
  python3 screener.py --utmapp ~/test                   # annan mapp för rapporterna

Dina innehav läses från innehav.csv i utmappen (se innehav.py) och får
säljsignaler i rapporten.
  python3 screener.py --demo                            # testa utan internet (påhittad data)

Detta är ett verktyg för att hitta kandidater, inte köprekommendationer.
"""

import argparse
import datetime as dt
import html
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import innehav as ih

# ---------------------------------------------------------------------------
# INSTÄLLNINGAR – ändra här, inte längre ner i koden
# ---------------------------------------------------------------------------
KONFIG = {
    "utmapp": "/srv/storage/aktiedata",  # här hamnar rapporterna (din share)
    "historik": "2y",                 # hur mycket kurshistorik som hämtas
    "historik_egna": "5y",            # för dina innehav, sålda aktier och index
    "min_dagar": 260,                 # minst ~1 års handel krävs för att mäta momentum
    "min_omsattning_sek": 5_000_000,  # snittomsättning/dag (20 dagar) – annars för illikvid
    "krav_over_ma200": True,          # kursen måste ligga över 200-dagars medelvärde
    "krav_ma50_over_ma200": True,     # 50-dagars måste ligga över 200-dagars ("golden cross-läge")
    "ett_aktieslag_per_bolag": True,  # behåll bara det mest likvida aktieslaget (A/B/C/D/R)
    "vikt_mom_12_1": 0.5,             # vikt för 12-månaders momentum (exkl. senaste månaden)
    "vikt_mom_6m": 0.5,               # vikt för 6-månaders momentum
    "topp_n": 20,                     # hur många som visas i topplistan
    # Urvalsregler: vilka i topplistan som är värda att granska vidare
    "urval_max_1m": 0.20,             # inte gått mer än +20 % senaste månaden (inte för uttänjd)
    "urval_min_omsattning_sek": 20_000_000,  # minst 20 MSEK/dag
    "urval_max_volatilitet": 0.50,    # högst 50 % årlig volatilitet
}

# Ungefärligt antal handelsdagar
DAGAR_1M, DAGAR_3M, DAGAR_6M, DAGAR_12M = 21, 63, 126, 252

MAPP = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# STEG 1: HÄMTA DATA
# ---------------------------------------------------------------------------
def las_tickers(fil: Path) -> pd.DataFrame:
    """Läser tickerlistan. Rader som börjar med # ignoreras."""
    df = pd.read_csv(fil, comment="#", skipinitialspace=True)
    df["ticker"] = df["ticker"].str.strip()
    df["namn"] = df["namn"].astype(str).str.strip()
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
        if t.endswith("=X"):                         # valutakurs: runt 10 kr, låg volatilitet
            trend, vol = 0.0, 0.004
        avk = rng.normal(trend, vol, len(dagar))
        kurs = (10 if t.endswith("=X") else 100) * np.exp(np.cumsum(avk))
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
# STEG 3-5: ETT AKTIESLAG PER BOLAG, FILTRERA, RANKA, URVAL
# ---------------------------------------------------------------------------
def bolagsnyckel(ticker: str) -> str:
    """'SSAB-B.ST' -> 'SSAB'. Används för att känna igen olika aktieslag i samma bolag."""
    bas = ticker.removesuffix(".ST")
    return re.sub(r"-(A|B|C|D|R)$", "", bas)


def ett_aktieslag(tabell: pd.DataFrame, innehav: list[str]) -> pd.DataFrame:
    """Behåller ett aktieslag per bolag: det du äger om du äger något, annars det mest likvida."""
    t = tabell.copy()
    t["bolag"] = t["ticker"].map(bolagsnyckel)
    t["_ags"] = t["ticker"].isin(innehav)
    t = t.sort_values(["bolag", "_ags", "omsattning_sek"], ascending=[True, False, False])
    return t.drop_duplicates("bolag").drop(columns="_ags")


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

    tabell = tabell.sort_values(["godkand", "poang"], ascending=[False, False]).reset_index(drop=True)

    # Plats bland godkända och topplistan
    tabell["plats"] = np.nan
    tabell.loc[tabell["godkand"], "plats"] = range(1, int(tabell["godkand"].sum()) + 1)
    tabell["topp"] = tabell["plats"] <= KONFIG["topp_n"]

    # Urvalsregler – vad som gör en kandidat värd att granska, och varför inte
    urvalsorsak = []
    for _, r in tabell.iterrows():
        o = []
        if r["mom_1m"] > KONFIG["urval_max_1m"]:
            o.append("för uttänjd senaste månaden")
        if r["omsattning_sek"] < KONFIG["urval_min_omsattning_sek"]:
            o.append("för låg omsättning")
        if r["volatilitet"] > KONFIG["urval_max_volatilitet"]:
            o.append("för hög volatilitet")
        urvalsorsak.append(", ".join(o))
    tabell["urval_orsak"] = urvalsorsak
    tabell["urval"] = tabell["godkand"] & (tabell["urval_orsak"] == "")
    return tabell


# ---------------------------------------------------------------------------
# BEDÖMNING AV EN ENSKILD AKTIE (används av sökfunktionen på webbsidan)
# ---------------------------------------------------------------------------
def bedom_en(ticker: str, n: dict, tabell: pd.DataFrame | None, salj_rank: int = 40) -> dict:
    """Kör köpreglerna på en aktie och räknar ut vilken plats den skulle få i
    senaste veckorankningen. n = berakna_nyckeltal(), med omsattning_sek i kronor.

    Returnerar en dict med 'beslut' (KÖPLÄGE / BEVAKA / INTE KÖPLÄGE),
    'regler' [(status, regel, förklaring)] och rankningen."""
    K = KONFIG
    regler = []

    def regel(ok, namn, txt, bevaka=False):
        regler.append(("ok" if ok else "bevaka" if bevaka else "brutet", namn, txt))
        return ok

    # 1. Screenerns filter (samma som stoppar en aktie från rankningen)
    f_oms = regel(n["omsattning_sek"] >= K["min_omsattning_sek"], "Omsättning",
                  f"{n['omsattning_sek'] / 1e6:.1f} MSEK/dag (krav {K['min_omsattning_sek'] / 1e6:.0f})")
    f_ma200 = regel(not K["krav_over_ma200"] or n["over_ma200"], "Över MA200",
                    f"{n['avstand_ma200'] * 100:+.1f} % (MA200 {n['ma200']:.2f})")
    f_kors = regel(not K["krav_ma50_over_ma200"] or n["ma50_over_ma200"], "MA50 över MA200",
                   f"MA50 {n['ma50']:.2f} · MA200 {n['ma200']:.2f}")
    godkand = f_oms and f_ma200 and f_kors

    # 2. Plats i rankningen, räknad mot senaste veckans godkända aktier
    plats = poang = None
    n_godk = 0
    if godkand and tabell is not None and len(tabell):
        godk = tabell["godkand"].astype(str).str.lower().isin(["true", "1"])
        ovriga = tabell[godk & (tabell["ticker"] != ticker)].dropna(subset=["mom_12_1", "mom_6m"])
        n_godk = len(ovriga) + 1
        m12 = pd.concat([ovriga["mom_12_1"], pd.Series([n["mom_12_1"]])], ignore_index=True)
        m6 = pd.concat([ovriga["mom_6m"], pd.Series([n["mom_6m"]])], ignore_index=True)
        p = 100 * (K["vikt_mom_12_1"] * m12.rank(pct=True) + K["vikt_mom_6m"] * m6.rank(pct=True))
        poang = float(p.iloc[-1])
        ovr = p.iloc[:-1].to_numpy()
        # Lika poäng: samma ordning som i veckorapporten; en aktie som inte fanns där hamnar efter.
        if "plats" in tabell.columns:
            egen = tabell.loc[tabell["ticker"] == ticker, "plats"]
            egen_plats = float(egen.iloc[0]) if len(egen) and pd.notna(egen.iloc[0]) else np.inf
            fore = pd.to_numeric(ovriga["plats"], errors="coerce").to_numpy(dtype=float) < egen_plats
        else:  # veckorapport från en äldre version utan platskolumn
            fore = np.zeros(len(ovriga), dtype=bool)
        plats = int((ovr > poang).sum() + ((ovr == poang) & fore).sum()) + 1
        regel(plats <= K["topp_n"], f"Topp {K['topp_n']} i rankningen",
              f"plats {plats} av {n_godk} (poäng {poang:.0f})", bevaka=plats <= salj_rank)
    elif godkand:
        regler.append(("na", f"Topp {K['topp_n']} i rankningen", "ingen veckorapport att jämföra med ännu"))
    else:
        regler.append(("na", f"Topp {K['topp_n']} i rankningen", "rankas inte – klarar inte filtren ovan"))

    # 3. Urvalsreglerna
    u1 = regel(n["mom_1m"] <= K["urval_max_1m"], "Inte uttänjd",
               f"1 mån {n['mom_1m'] * 100:+.1f} % (max +{K['urval_max_1m'] * 100:.0f} %)", bevaka=True)
    u2 = regel(n["omsattning_sek"] >= K["urval_min_omsattning_sek"], "Likviditet för urval",
               f"{n['omsattning_sek'] / 1e6:.1f} MSEK/dag (krav {K['urval_min_omsattning_sek'] / 1e6:.0f})", bevaka=True)
    u3 = regel(n["volatilitet"] <= K["urval_max_volatilitet"], "Volatilitet",
               f"{n['volatilitet'] * 100:.0f} % (max {K['urval_max_volatilitet'] * 100:.0f} %)", bevaka=True)
    urval = u1 and u2 and u3

    if not godkand or (plats is not None and plats > salj_rank):
        beslut = "INTE KÖPLÄGE"
    elif plats is not None and plats <= K["topp_n"] and urval:
        beslut = "KÖPLÄGE"
    else:
        beslut = "BEVAKA"
    return {"beslut": beslut, "regler": regler, "godkand": godkand, "urval": urval,
            "plats": plats, "poang": poang, "n_godkanda": n_godk}


# ---------------------------------------------------------------------------
# HISTORIK (tidigare körningar i utmappen)
# ---------------------------------------------------------------------------
def tidigare_korningar(utmapp: Path, idag: str) -> list[Path]:
    return sorted(p for p in utmapp.glob("screener_*.csv") if p.stem != f"screener_{idag}")


def forra_topplistan(utmapp: Path, idag: str) -> set[str] | None:
    tidigare = tidigare_korningar(utmapp, idag)
    if not tidigare:
        return None
    df = pd.read_csv(tidigare[-1])
    return set(df[df["topp"]]["ticker"])


def bredd_historik(utmapp: Path, idag: str, tabell: pd.DataFrame, veckor: int = 12) -> list[tuple[str, int, int]]:
    """Andel aktier som klarar trendfiltren per körning – ett grovt mått på marknadens styrka."""
    rader = []
    for p in tidigare_korningar(utmapp, idag)[-(veckor - 1):]:
        df = pd.read_csv(p)
        rader.append((p.stem.removeprefix("screener_"), int(df["godkand"].sum()), len(df)))
    rader.append((idag, int(tabell["godkand"].sum()), len(tabell)))
    return rader


# ---------------------------------------------------------------------------
# RAPPORTER
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


KOLUMNER = [
    ("#", "plats", lambda x: str(int(x))),
    ("Ticker", "ticker", str),
    ("Namn", "namn", str),
    ("Poäng", "poang", lambda x: f"{x:.0f}"),
    ("Urval", "urval", lambda x: "✓" if x else ""),
    ("12-1 mån", "mom_12_1", pct),
    ("6 mån", "mom_6m", pct),
    ("1 mån", "mom_1m", pct),
    ("Över MA200", "avstand_ma200", pct),
    ("Från 52v-topp", "fran_52v_hogsta", pct),
    ("Volatilitet", "volatilitet", lambda x: f"{x * 100:.0f}%"),
    ("Oms/dag (MSEK)", "omsattning_sek", lambda x: f"{x / 1e6:.0f}"),
]


def _blankning_txt(pct, andring) -> str:
    """'12,5 % ↑2,5' – total blankning och förändring på 30 dagar (bara om den är minst 0,5 pe)."""
    if pct is None or pd.isna(pct) or pct <= 0:
        return ""
    t = f"{pct:.1f} %".replace(".", ",")
    if andring is not None and pd.notna(andring) and abs(andring) >= 0.5:
        t += f" {'↑' if andring > 0 else '↓'}{abs(andring):.1f}".replace(".", ",")
    return t


def _insider_txt(netto, kopare) -> str:
    """'+1,2 M (3)' – nettoköp i miljoner kronor senaste 90 dagarna och antal köpare."""
    if netto is None or pd.isna(netto) or abs(netto) < 1:
        return ""
    t = f"{netto / 1e6:+.1f} M".replace(".", ",")
    if kopare and pd.notna(kopare) and kopare > 0:
        t += f" ({int(kopare)})"
    return t


def info_kolumner(tabell: pd.DataFrame) -> list:
    """Blankning och insiderhandel visas bara om signaler.berika() har körts. Påverkar inte poängen."""
    if "blankning_pct" not in tabell.columns:
        return []
    return [("Blankning", "txt_blankning", str), ("Insider 90 d", "txt_insider", str)]


def bortfall_per_orsak(tabell: pd.DataFrame) -> list[tuple[str, int]]:
    antal = {}
    for o in tabell.loc[~tabell["godkand"], "orsak"]:
        for del_ in o.split(", "):
            antal[del_] = antal.get(del_, 0) + 1
    return sorted(antal.items(), key=lambda x: -x[1])


def skriv_markdown(fil: Path, idag: str, tabell, topp, saknade, analys, nya, ute, bredd, demo, utv_rader=()):
    n_godk = int(tabell["godkand"].sum())
    delar = [f"# Momentum-screener – {idag}" + (" (DEMODATA)" if demo else ""),
             "",
             f"Analyserade: {len(tabell)} bolag · Klarade filtren: {n_godk} "
             f"({n_godk / len(tabell) * 100:.0f} %) · Saknade data: {len(saknade)}",
             "",
             f"## Topp {KONFIG['topp_n']}",
             "",
             "✓ = klarar urvalsreglerna (1 mån ≤ "
             f"{KONFIG['urval_max_1m'] * 100:.0f} %, oms ≥ {KONFIG['urval_min_omsattning_sek'] / 1e6:.0f} MSEK, "
             f"volatilitet ≤ {KONFIG['urval_max_volatilitet'] * 100:.0f} %)",
             "",
             tabell_md(topp, KOLUMNER + info_kolumner(topp))]
    if info_kolumner(topp):
        delar += ["", "Blankning = total blankning (FI), ↑↓ förändring senaste 30 dagarna. Insider 90 d = nettoköp av "
                      "personer i ledande ställning i miljoner kronor (antal köpare). Information – påverkar inte poängen."]

    ej_urval = topp[~topp["urval"]]
    if len(ej_urval):
        delar += ["", "**Utan ✓:** " + "; ".join(f"{r.ticker} ({r.urval_orsak})" for r in ej_urval.itertuples())]

    if nya is not None:
        delar += ["", "## Förändring sedan förra körningen", "",
                  f"- **Nya på listan:** {', '.join(nya) or '–'}",
                  f"- **Föll ur listan:** {', '.join(ute) or '–'}"]

    if analys:
        delar += [""] + ih.markdown_rader(analys)
    if utv_rader:
        delar += [""] + utv_rader

    if len(bredd) > 1:
        delar += ["", "## Marknadsbredd (andel som klarar trendfiltren)", ""]
        delar += [f"- {d}: {g} av {n} ({g / n * 100:.0f} %)" for d, g, n in bredd]

    bort = bortfall_per_orsak(tabell)
    if bort:
        delar += ["", f"## Bortfiltrerade: {int((~tabell['godkand']).sum())} bolag", ""]
        delar += [f"- {o}: {n}" for o, n in bort]
        delar += ["", "En aktie kan falla på flera filter. Se kolumnen `orsak` i CSV-filen för detaljer."]
    if saknade:
        delar += ["", "## Saknade data", "", ", ".join(saknade)]

    delar += ["", "---", "Kandidater att granska vidare, inte köprekommendationer."]
    fil.write_text("\n".join(delar) + "\n", encoding="utf-8")


def skriv_html(fil: Path, idag: str, tabell, topp, saknade, analys, nya, ute, bredd, demo):
    e = html.escape
    n_godk = int(tabell["godkand"].sum())
    andel = n_godk / len(tabell) * 100

    def cell_pct(x):
        if pd.isna(x):
            return "<td></td>"
        klass = "pos" if x > 0 else "neg" if x < 0 else ""
        return f'<td class="num {klass}">{x * 100:+.1f}%</td>'

    har_info = bool(info_kolumner(topp))
    info_rubriker = ('<th class="num">Blankning</th><th class="num">Insider 90 d</th>' if har_info else "")
    rader = []
    for r in topp.itertuples():
        ny = ' <span class="tag">ny</span>' if nya and r.ticker in nya else ""
        rader.append(
            f'<tr class="{"urval" if r.urval else ""}" title="{e(r.urval_orsak or "Klarar urvalsreglerna")}">'
            f'<td class="num">{int(r.plats)}</td>'
            f'<td><b data-t="{e(r.ticker)}">{e(r.ticker.removesuffix(".ST"))}</b>{ny}<br><span class="muted">{e(r.namn)}</span></td>'
            f'<td class="num"><b>{r.poang:.0f}</b></td>'
            f'<td class="mid">{"✓" if r.urval else ""}</td>'
            f'{cell_pct(r.mom_12_1)}{cell_pct(r.mom_6m)}{cell_pct(r.mom_1m)}'
            f'{cell_pct(r.avstand_ma200)}{cell_pct(r.fran_52v_hogsta)}'
            f'<td class="num">{r.volatilitet * 100:.0f}%</td>'
            f'<td class="num">{r.omsattning_sek / 1e6:.0f}</td>'
            + (f'<td class="num" title="Total blankning enligt FI, förändring senaste 30 dagarna">{e(r.txt_blankning)}</td>'
               f'<td class="num {"pos" if (r.insider_netto_sek or 0) > 0 else "neg" if (r.insider_netto_sek or 0) < 0 else ""}"'
               f' title="Nettoköp av insiders senaste 90 dagarna (antal köpare)">{e(r.txt_insider)}</td>' if har_info else "")
            + '</tr>'
        )

    ej_urval = topp[~topp["urval"]]
    ej_urval_html = ""
    if len(ej_urval):
        ej_urval_html = "<p class='muted small'><b>Utan ✓:</b> " + "; ".join(
            f"{e(r.ticker.removesuffix('.ST'))} ({e(r.urval_orsak)})" for r in ej_urval.itertuples()) + "</p>"

    kort = []
    if nya is not None:
        kort.append(
            "<div class='card'><h2>Förändring sedan förra veckan</h2>"
            f"<p><b>Nya:</b> {e(', '.join(t.removesuffix('.ST') for t in nya)) or '–'}</p>"
            f"<p><b>Föll ur:</b> {e(', '.join(t.removesuffix('.ST') for t in ute)) or '–'}</p></div>")
    if analys:
        li = []
        for a in analys:
            varn = a["beslut"] in ("SÄLJ", "SÄLJ VID MÅNADSGENOMGÅNG", "SAKNAR DATA", "TRIMMA")
            orsak = "; ".join(a["signaler"] + a["manad"] + a["bevaka"])
            li.append(f"<li class='{'varning' if varn else 'ok'}'><b>{e(a['ticker'].removesuffix('.ST'))}</b> "
                      f"<span class='muted small'>({e(a['typ'])})</span> – <b>{e(a['beslut'])}</b>"
                      f"{' · ' + e(orsak) if orsak else ''}</li>")
        kort.append(f"<div class='card'><h2>Dina innehav</h2><ul class='innehav'>{''.join(li)}</ul></div>")
    if bredd:
        staplar = "".join(
            f"<div class='bar' title='{d}: {g} av {n}'><em>{g / n * 100:.0f}%</em>"
            f"<div style='height:{g / n * 100:.0f}%'></div>"
            f"<span>{d[5:]}</span></div>" for d, g, n in bredd)
        kort.append(
            "<div class='card'><h2>Marknadsbredd</h2>"
            f"<p class='muted small'>Andel bolag som klarar trendfiltren, senaste {len(bredd)} körningar</p>"
            f"<div class='bars'>{staplar}</div></div>")
    bort = bortfall_per_orsak(tabell)
    if bort or saknade:
        txt = "".join(f"<li>{e(o)}: {n}</li>" for o, n in bort)
        sak = f"<p class='muted small'>Saknade data: {e(', '.join(saknade))}</p>" if saknade else ""
        kort.append(f"<div class='card'><h2>Bortfiltrerade</h2><ul>{txt}</ul>{sak}</div>")

    sida = f"""<!doctype html>
<html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Momentum {idag}</title>
<style>
:root {{ --bg:#f6f7f9; --kort:#fff; --text:#1c1f24; --muted:#6b7280; --linje:#e5e7eb;
         --pos:#0f7b3f; --neg:#c0352b; --accent:#2b5fd9; --urval:#eaf6ee; --varn:#fdf0e6; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#14161a; --kort:#1d2026; --text:#e6e8eb; --muted:#9aa1ab; --linje:#2c3038;
           --pos:#4cc47f; --neg:#f07167; --accent:#7aa2ff; --urval:#183324; --varn:#3a2a1a; }} }}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--bg); color:var(--text);
        font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }}
main {{ max-width:1200px; margin:0 auto; padding:24px 16px 48px }}
h1 {{ margin:0 0 4px; font-size:24px }} h2 {{ margin:0 0 10px; font-size:16px }}
.muted {{ color:var(--muted) }} .small {{ font-size:13px }}
.stats {{ display:flex; flex-wrap:wrap; gap:12px; margin:16px 0 20px }}
.stat {{ background:var(--kort); border:1px solid var(--linje); border-radius:10px; padding:10px 14px; min-width:130px }}
.stat b {{ display:block; font-size:22px }}
.card {{ background:var(--kort); border:1px solid var(--linje); border-radius:10px; padding:16px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:12px; margin-top:16px }}
.wrap {{ overflow-x:auto; background:var(--kort); border:1px solid var(--linje); border-radius:10px }}
table {{ border-collapse:collapse; width:100%; min-width:860px }}
th,td {{ padding:8px 10px; border-bottom:1px solid var(--linje); text-align:left; vertical-align:top }}
th {{ font-size:12px; color:var(--muted); font-weight:600; white-space:nowrap; position:sticky; top:0; background:var(--kort) }}
tr.urval td {{ background:var(--urval) }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap }} .mid {{ text-align:center }}
.pos {{ color:var(--pos) }} .neg {{ color:var(--neg) }}
.tag {{ font-size:11px; background:var(--accent); color:#fff; border-radius:4px; padding:1px 5px; margin-left:4px }}
ul {{ margin:0; padding-left:18px }} li {{ margin:4px 0 }}
ul.innehav {{ list-style:none; padding:0 }} ul.innehav li {{ padding:6px 8px; border-radius:6px }}
li.varning {{ background:var(--varn) }}
.bars {{ display:flex; gap:6px; align-items:flex-end; height:120px }}
.bar {{ flex:1; height:100%; display:flex; flex-direction:column; justify-content:flex-end; align-items:center }}
.bar div {{ width:100%; background:var(--accent); border-radius:3px 3px 0 0; min-height:2px }}
.bar span {{ font-size:10px; color:var(--muted); margin-top:4px }}
.bar em {{ font-size:10px; font-style:normal; color:var(--muted); margin-bottom:2px }}
footer {{ margin-top:24px; font-size:12px; color:var(--muted) }}
</style></head><body><main>
<h1>Momentum-screener – {idag}{' (DEMODATA)' if demo else ''}</h1>
<div class="muted">Svenska aktier · följ trenden</div>
<div class="stats">
  <div class="stat"><span class="muted small">Analyserade bolag</span><b>{len(tabell)}</b></div>
  <div class="stat"><span class="muted small">Klarar trendfiltren</span><b>{n_godk} <span class="muted small">({andel:.0f} %)</span></b></div>
  <div class="stat"><span class="muted small">Urval i topp {KONFIG['topp_n']}</span><b>{int(topp['urval'].sum())}</b></div>
</div>
<div class="wrap"><table>
<thead><tr><th>#</th><th>Aktie</th><th class="num">Poäng</th><th class="mid">Urval</th>
<th class="num">12-1 mån</th><th class="num">6 mån</th><th class="num">1 mån</th>
<th class="num">Över MA200</th><th class="num">Från 52v-topp</th><th class="num">Volatilitet</th>
<th class="num">Oms/dag MSEK</th>{info_rubriker}</tr></thead>
<tbody>{''.join(rader)}</tbody></table></div>
<p class="muted small">Grön rad = klarar urvalsreglerna: 1 mån ≤ {KONFIG['urval_max_1m'] * 100:.0f} %,
omsättning ≥ {KONFIG['urval_min_omsattning_sek'] / 1e6:.0f} MSEK/dag,
volatilitet ≤ {KONFIG['urval_max_volatilitet'] * 100:.0f} %. Håll muspekaren över en rad för orsaken.
{"<br>Blankning = total blankning enligt FI (↑↓ förändring senaste 30 dagarna). Insider 90 d = nettoköp av personer i ledande ställning i miljoner kronor (antal köpare). Bara information – påverkar inte poängen." if har_info else ""}</p>
{ej_urval_html}
<div class="grid">{''.join(kort)}</div>
<footer>Kandidater att granska vidare, inte köprekommendationer. Data: Yahoo Finance.</footer>
</main></body></html>
"""
    fil.write_text(sida, encoding="utf-8")


def skriv_rapporter(tabell, saknade, analys, utmapp: Path, demo: bool, utv_rader=()) -> list[Path]:
    utmapp.mkdir(parents=True, exist_ok=True)
    idag = dt.date.today().isoformat()

    topp = tabell[tabell["topp"]].copy()
    if "blankning_pct" in topp.columns:
        topp["txt_blankning"] = [_blankning_txt(p, a) for p, a in zip(topp["blankning_pct"], topp["blankning_andring_30d"])]
        topp["txt_insider"] = [_insider_txt(n, k) for n, k in zip(topp["insider_netto_sek"], topp["insider_kopare"])]

    forra = forra_topplistan(utmapp, idag)
    nya = ute = None
    if forra is not None:
        nu = set(topp["ticker"])
        nya, ute = sorted(nu - forra), sorted(forra - nu)

    bredd = bredd_historik(utmapp, idag, tabell)

    # All data som CSV (används för jämförelse nästa vecka och för egen analys)
    csv_fil = utmapp / f"screener_{idag}.csv"
    tabell.to_csv(csv_fil, index=False)

    md = utmapp / f"rapport_{idag}.md"
    ht = utmapp / f"rapport_{idag}.html"
    args = (idag, tabell, topp, saknade, analys, nya, ute, bredd, demo)
    skriv_markdown(md, *args, utv_rader=utv_rader)
    skriv_html(ht, *args)
    # senaste.html skrivs alltid över – bokmärk den
    (utmapp / "senaste.html").write_text(ht.read_text(encoding="utf-8"), encoding="utf-8")
    return [md, ht, csv_fil]


# ---------------------------------------------------------------------------
# HUVUDPROGRAM
# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description="Momentum-screener för svenska aktier")
    p.add_argument("--tickers", default=str(MAPP / "tickers_se.csv"), help="tickerlista (CSV)")
    p.add_argument("--innehav", default="", help=argparse.SUPPRESS)  # ersatt av innehav.csv
    p.add_argument("--utmapp", default=KONFIG["utmapp"], help="mapp för rapporterna")
    p.add_argument("--demo", action="store_true", help="kör med påhittad data (ingen internet)")
    a = p.parse_args()

    utmapp = Path(a.utmapp).expanduser()
    try:
        utmapp.mkdir(parents=True, exist_ok=True)
        (utmapp / ".skrivtest").touch()
        (utmapp / ".skrivtest").unlink()
    except OSError as fel:
        print(f"Kan inte skriva till {utmapp}: {fel}", file=sys.stderr)
        print("Kontrollera rättigheterna, t.ex: sudo chown $USER " + str(utmapp), file=sys.stderr)
        return 1

    if a.innehav:
        print("OBS: --innehav används inte längre. Innehaven läses från "
              f"{utmapp / 'innehav.csv'} (redigera via webbsidan eller direkt i filen).", file=sys.stderr)

    lista = las_tickers(Path(a.tickers))
    namn = dict(zip(lista["ticker"], lista["namn"]))
    tickers = list(lista["ticker"])

    # Innehaven: deras kurser hämtas också, även om de inte finns i den svenska listan
    # (t.ex. amerikanska aktier), plus valutakurser för att räkna om till SEK.
    innehav_df = ih.las_innehav(utmapp / "innehav.csv")
    affarer_df = ih.las_affarer(utmapp / "affarer.csv")
    innehav = list(innehav_df["ticker"])

    # Egna aktier (nuvarande och sålda), valutor och jämförelseindex hämtas med
    # längre historik, så att utvecklingen kan räknas ända från köpet.
    valutor = list(innehav_df["valuta"]) + list(affarer_df["valuta"])
    egna = list(dict.fromkeys(innehav + list(affarer_df["ticker"])
                              + sorted({f for f in (ih.fx_ticker(v) for v in valutor) if f})
                              + [ih.JAMFORELSEINDEX]))

    print(f"Hämtar kurser för {len(tickers)} aktier + {len(egna)} egna/valutor/index ...", file=sys.stderr)
    if a.demo:
        kurser = demodata(list(dict.fromkeys(tickers + egna)))
        egna_kurser = {t: kurser[t] for t in egna if t in kurser}
    else:
        kurser = hamta_kurser(tickers, KONFIG["historik"])
        egna_kurser = hamta_kurser(egna, KONFIG["historik_egna"])
        kurser.update({t: df for t, df in egna_kurser.items() if t not in kurser})

    # Spara stängningskurserna – webbsidan använder dem för grafer, innehav och utveckling.
    # Gamla kolumner som inte hämtades nu (t.ex. sålda bolag som avnoterats) behålls.
    stangning = ih.slå_ihop_kurser(ih.las_kurser(utmapp / "kurser.csv"),
                                   {t: df["Close"] for t, df in kurser.items()})
    stangning = ih.slå_ihop_kurser(stangning, {t: df["Close"] for t, df in egna_kurser.items()})
    ih.skriv_kurser(utmapp / "kurser.csv", stangning)

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

    tabell = pd.DataFrame(rader)
    if KONFIG["ett_aktieslag_per_bolag"]:
        tabell = ett_aktieslag(tabell, innehav)
    tabell = filtrera_och_ranka(tabell)

    # Insiderhandel och blankning som informationskolumner (påverkar inte rankning eller regler)
    try:
        import signaler
        tabell = signaler.berika(tabell, utmapp, Path(a.tickers))
    except Exception as fel:
        print(f"Insyn/blankning kunde inte läggas till: {type(fel).__name__}: {fel}", file=sys.stderr)

    analys = ih.analysera(innehav_df, stangning, tabell)
    utv_rader = ih.utveckling_markdown(innehav_df, affarer_df, stangning)
    filer = skriv_rapporter(tabell, saknade, analys, utmapp, a.demo, utv_rader)
    print(filer[0].read_text(encoding="utf-8"))
    for f in filer:
        print(f"Sparad: {f}", file=sys.stderr)
    print(f"Sparad: {utmapp / 'senaste.html'}", file=sys.stderr)

    # Virtuella strategiportföljer (pappershandel). Ett fel här får aldrig stoppa veckorapporten.
    try:
        import strategier
        for rad in strategier.kor_alla(utmapp, a.demo):
            print(f"Strategi {rad}", file=sys.stderr)
    except Exception as fel:
        print(f"Strategierna kunde inte köras: {type(fel).__name__}: {fel}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
