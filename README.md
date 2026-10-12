# Momentum-screener för svenska aktier

Ett Python-skript som varje vecka går igenom en lista svenska aktier och tar fram de som har starkast momentum och samtidigt ligger i upptrend. Resultatet är en topplista med kandidater att granska vidare, inte köprekommendationer.

## Så fungerar den

**1. Hämtar data.** Två års dagliga kurser för varje aktie i `tickers_se.csv`, från Yahoo Finance. Kurserna är justerade för utdelningar och splittar.

**2. Mäter fyra saker per aktie.**

| Mått | Vad det betyder | Varför |
|---|---|---|
| `mom_12_1` | Avkastning senaste 12 månaderna, exklusive senaste månaden | Huvudmåttet för momentum. Senaste månaden hoppas över eftersom aktier på mycket kort sikt tenderar att studsa tillbaka |
| `mom_6m` | Avkastning senaste 6 månaderna | Fångar nyare styrka |
| MA50 / MA200 | 50- och 200-dagars glidande medelvärde | Visar om aktien är i upp- eller nedtrend |
| Volatilitet | Hur mycket kursen svänger, på årsbasis | Används för positionsstorlek: högre volatilitet → mindre position |
| Omsättning | Snitt i kronor per dag senaste 20 dagarna | Illikvida aktier är dyra att köpa och sälja |

**3. Filtrerar bort** aktier som:
- ligger under sitt 200-dagars medelvärde (nedtrend)
- har 50-dagars under 200-dagars medelvärde ("death cross-läge")
- omsätter under 5 MSEK per dag

**4. Rankar** de som är kvar. Varje aktie får en percentilrank (0–100 %) på 12-1-momentum och på 6-månadersmomentum, och poängen är snittet av de två. Rank i stället för råa procent gör att en enda extrem aktie inte förvränger allt.

Rapporten visar också vilka som är **nya** på topplistan och vilka som **fallit ur** sedan förra körningen, och flaggar dina egna innehav om de inte längre klarar filtren.

## Installation på hemmaservern (Ubuntu)

```bash
mkdir -p ~/screener && cd ~/screener
# Kopiera hit screener.py och tickers_se.csv

python3 -m venv venv
./venv/bin/pip install yfinance pandas numpy

# Testa först utan internet:
./venv/bin/python screener.py --demo

# Sedan på riktigt:
./venv/bin/python screener.py --innehav SWED-A.ST
```

Rapporterna hamnar i `/srv/storage/screener/` (ändra `utmapp` i `KONFIG` eller använd `--utmapp`):
- `senaste.html` – veckans rapport, skrivs över varje körning. Bokmärk den.
- `rapport_ÅÅÅÅ-MM-DD.html` – sparad kopia per vecka
- `rapport_ÅÅÅÅ-MM-DD.md` – samma rapport som text, bra att klistra in i en chatt
- `screener_ÅÅÅÅ-MM-DD.csv` – all data, kan öppnas i Excel. Används också för veckojämförelsen och marknadsbredden, så radera inte de gamla.

Användaren som kör skriptet måste kunna skriva i mappen:

```bash
sudo mkdir -p /srv/storage/screener
sudo chown $USER /srv/storage/screener
```

## Köra automatiskt varje vecka

Öppna crontab med `crontab -e` och lägg till (lördag kl. 08:00, efter att fredagens kurser är klara):

```
0 8 * * 6 cd ~/screener && ./venv/bin/python screener.py --innehav SWED-A.ST >> /srv/storage/screener/logg.txt 2>&1
```

Byt `SWED-A.ST` mot alla dina innehav, kommaseparerade.

## Anpassa

Alla inställningar ligger i `KONFIG` högst upp i `screener.py`: utmapp, minsta omsättning, vilka trendfilter som gäller, vikten mellan 12- och 6-månadersmomentum, hur många som visas och urvalsreglerna. Ändra en sak i taget så du förstår effekten.

### Urvalsregler (gröna rader och ✓)

Av topplistan markeras de som också klarar:
- **1 mån ≤ +20 %** – inte rusat nyligen, för att undvika att köpa på toppen av en spurt
- **Omsättning ≥ 20 MSEK/dag** – lätt att köpa och sälja
- **Volatilitet ≤ 50 %** – inte för svängig

### Ett aktieslag per bolag

Har ett bolag flera aktieslag (SSAB A och B, SEB A och C) behålls bara det mest likvida, så samma bolag inte tar två platser. Äger du ett av aktieslagen och anger det i `--innehav` behålls det i stället. Stäng av med `"ett_aktieslag_per_bolag": False`.

### Hela Stockholmsbörsen

`uppdatera_tickers.py` hämtar alla aktier på Nasdaq Stockholm och skriver om `tickers_se.csv`:

```bash
./venv/bin/python uppdatera_tickers.py                # Large, Mid och Small Cap
./venv/bin/python uppdatera_tickers.py --first-north  # även First North
```

Den gamla listan sparas som `tickers_se.bak.csv`. Preferensaktier, teckningsoptioner och BTA tas bort automatiskt. Kör den någon gång i kvartalet så kommer nya noteringar med och avnoterade bolag försvinner.

Du kan också lägga till aktier för hand i `tickers_se.csv` med Yahoo-formatet `TICKER.ST` (t.ex. `VOLV-B.ST`). Tickers som Yahoo inte hittar listas under "Saknade data" i rapporten.

## Begränsningar att känna till

- **Yahoo Finance är gratis men inofficiellt.** Data kan saknas eller vara fel för enstaka dagar, och biblioteket `yfinance` kan sluta fungera tillfälligt när Yahoo ändrar något. Uppdatera med `./venv/bin/pip install -U yfinance` om hämtningen slutar fungera.
- **Listan avgör resultatet.** Screenern hittar bara bland aktierna i `tickers_se.csv`. Startlistan är främst Large och Mid Cap.
- **Momentum fungerar i perioder, inte alltid.** Strategin har historiskt gett överavkastning i snitt, men kan gå dåligt i månader, särskilt vid snabba vändningar på marknaden.
- **Den säger inget om varför en aktie gått upp.** Kontrollera alltid nyheter och rapporter innan köp – ett bud eller en engångshändelse ger starkt momentum som inte fortsätter.

Nästa steg är att backtesta strategin: hade den slagit index de senaste åren?
