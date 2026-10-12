#!/usr/bin/env python3
"""
Uppdaterar tickers_se.csv med alla aktier på Nasdaq Stockholm.

Hämtar listan från Nasdaq Nordics öppna aktiescreener och gör om
symbolerna till Yahoo Finance-format:
    "VOLV B"   -> "VOLV-B.ST"
    "ALIV SDB" -> "ALIV-SDB.ST"
    "AZN"      -> "AZN.ST"

Körning:
  python3 uppdatera_tickers.py                 # huvudlistan (Large, Mid, Small Cap)
  python3 uppdatera_tickers.py --first-north   # även First North (många små bolag)

Den gamla listan sparas som tickers_se.bak.csv innan den skrivs över.
Preferensaktier, teckningsoptioner och BTA/BTU tas bort – de är inte vanliga aktier.
"""

import argparse
import csv
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

MAPP = Path(__file__).resolve().parent
UTFIL = MAPP / "tickers_se.csv"

URL = "https://api.nasdaq.com/api/nordic/screener/shares?category={kategori}&tableonly=false&market=STO"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}

# Symboler som innehåller något av detta är inte vanliga aktier
UTESLUT = re.compile(r"\b(PREF|PREFB|PREFA|TO\d*|BTA|BTU|UR|D)\b", re.IGNORECASE)


def hamta(kategori: str) -> list[dict]:
    req = urllib.request.Request(URL.format(kategori=kategori), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as svar:
        data = json.load(svar)
    return hitta_rader(data)


def hitta_rader(obj) -> list[dict]:
    """Letar upp listan med aktier i svaret, oavsett exakt struktur
    (en lista med objekt som har fältet 'symbol')."""
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and "symbol" in obj[0]:
            return obj
        for x in obj:
            r = hitta_rader(x)
            if r:
                return r
    elif isinstance(obj, dict):
        for x in obj.values():
            r = hitta_rader(x)
            if r:
                return r
    return []


def till_yahoo(symbol: str) -> str:
    """'VOLV B' -> 'VOLV-B.ST'"""
    return re.sub(r"\s+", "-", symbol.strip().upper()) + ".ST"


def main() -> int:
    p = argparse.ArgumentParser(description="Uppdatera tickers_se.csv från Nasdaq Stockholm")
    p.add_argument("--first-north", action="store_true", help="ta även med First North")
    a = p.parse_args()

    kategorier = ["MAIN_MARKET"] + (["FIRST_NORTH"] if a.first_north else [])
    aktier: dict[str, str] = {}
    for kat in kategorier:
        try:
            rader = hamta(kat)
        except Exception as e:  # nätverksfel, ändrat API osv.
            print(f"Kunde inte hämta {kat}: {e}", file=sys.stderr)
            print("Befintlig tickers_se.csv lämnas orörd.", file=sys.stderr)
            return 1
        if not rader:
            print(f"Hittade inga aktier i svaret för {kat} – Nasdaq kan ha ändrat formatet.", file=sys.stderr)
            print("Befintlig tickers_se.csv lämnas orörd.", file=sys.stderr)
            return 1

        fore = len(aktier)
        for r in rader:
            sym = str(r.get("symbol", "")).strip()
            if not sym or UTESLUT.search(sym):
                continue
            namn = str(r.get("fullName") or r.get("name") or sym).strip()
            aktier.setdefault(till_yahoo(sym), namn)
        print(f"{kat}: {len(aktier) - fore} aktier", file=sys.stderr)

    if UTFIL.exists():
        shutil.copy(UTFIL, MAPP / "tickers_se.bak.csv")

    with open(UTFIL, "w", newline="", encoding="utf-8") as f:
        f.write("# Genererad av uppdatera_tickers.py från Nasdaq Stockholm\n")
        f.write(f"# Marknader: {', '.join(kategorier)}\n")
        w = csv.writer(f)
        w.writerow(["ticker", "namn"])
        for t in sorted(aktier):
            w.writerow([t, aktier[t]])

    print(f"Skrev {len(aktier)} aktier till {UTFIL}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
