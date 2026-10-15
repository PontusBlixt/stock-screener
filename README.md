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

Rapporten visar också vilka som är **nya** på topplistan och vilka som **fallit ur** sedan förra körningen, och ger dina innehav säljsignaler (se nedan).

## Installation på hemmaservern (Ubuntu)

```bash
mkdir -p ~/screener && cd ~/screener
# Kopiera hit: screener.py, innehav.py, app.py, avanza_import.py, uppdatera_tickers.py, tickers_se.csv

python3 -m venv venv
./venv/bin/pip install yfinance pandas numpy flask waitress

# Testa först utan internet:
./venv/bin/python screener.py --demo

# Sedan på riktigt:
./venv/bin/python screener.py
```

Rapporterna hamnar i `/srv/storage/screener/` (ändra `utmapp` i `KONFIG` eller använd `--utmapp`):
- `senaste.html` – veckans rapport, skrivs över varje körning. Bokmärk den.
- `rapport_ÅÅÅÅ-MM-DD.html` – sparad kopia per vecka
- `rapport_ÅÅÅÅ-MM-DD.md` – samma rapport som text, bra att klistra in i en chatt
- `screener_ÅÅÅÅ-MM-DD.csv` – all data, kan öppnas i Excel. Används också för veckojämförelsen och marknadsbredden, så radera inte de gamla.
- `innehav.csv` – dina innehav (redigeras på webbsidan eller direkt i filen)
- `affarer.csv` – dina avslutade affärer
- `isin_tickers.csv` – sparade kopplingar ISIN → ticker och typ från importen
- `kurser.csv` – stängningskurser som webbsidan använder för grafer och innehav

Användaren som kör skriptet måste kunna skriva i mappen:

```bash
sudo mkdir -p /srv/storage/screener
sudo chown $USER /srv/storage/screener
```

## Webbsidan: veckans rapport och dina innehav

`app.py` är en liten webbserver som visar veckans rapport, dina innehav med säljsignaler och en detaljsida med kursgraf för varje aktie. Den använder runt 50 MB RAM.

Starta den som en tjänst så att den alltid körs, även efter omstart:

```bash
sudo cp screener-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now screener-web
```

Öppna sedan `http://<serverns-ip>:8080` i webbläsaren. Status och fel ser du med `systemctl status screener-web` och `journalctl -u screener-web -f`.

Kontrollera att `User` och sökvägarna i `screener-web.service` stämmer (filen förutsätter användaren `blixt` och mappen `/home/blixt/screener`). Vill du kräva lösenord avkommenterar du raden `SCREENER_LOSEN`. Kör du brandvägg (`ufw`) behöver port 8080 öppnas för hemnätet: `sudo ufw allow from 192.168.0.0/16 to any port 8080`.

**Exponera inte porten mot internet.** Vill du nå sidan utifrån, använd Tailscale.

### Sidorna

- **Veckans rapport** – samma som `senaste.html`. Klicka på en ticker för att se dess graf.
- **Mina innehav** – lägg till ett köp (ticker, typ, datum, kurs, antal) eller ta bort ett. Kurserna hämtas direkt när du lägger till, och knappen *Uppdatera kurser* hämtar dagens kurser. Rankningen uppdateras bara vid veckokörningen.
- **Aktiesida** – kursgraf med MA50, MA200, köpkurs och stoppnivå, nyckeltal och status för varje säljregel.
- **Utveckling** – portföljens tidsviktade avkastning mot index (momentum, kärna eller hela portföljen, valfri period) och alla avslutade affärer med träffsäkerhet, snittvinst och snittförlust.
- **Sök** – skriv en ticker eller ett bolagsnamn (t.ex. *volvo*, *VOLV-B.ST* eller *ADCT*). Sidan hämtar färska kurser från Yahoo och kör samma köpregler som screenern: omsättning, trendfilter, plats i rankningen och urvalsreglerna. Platsen räknas mot senaste veckorapportens godkända aktier, så den visar var aktien hade hamnat. Svaret är **KÖPLÄGE** (klarar allt och är topp 20), **BEVAKA** (godkänd men plats 21–40 eller bryter en urvalsregel) eller **INTE KÖPLÄGE**. Sidan visar också var den släpande stoppen skulle börja om du köper idag. Fungerar även för utländska aktier – omsättningen räknas om till kronor.
- **Arkiv** – tidigare veckorapporter, även som .md.

### Importera från Avanza

Det rekommenderade sättet att lägga in innehav. Under **Mina innehav → Importera transaktioner**:

1. Exportera dina transaktioner från Avanza (Transaktioner → välj hela perioden → Exportera transaktioner).
2. Ladda upp .csv-filen.
3. Granska: välj vilka konton som ska räknas, kontrollera tickern för varje värdepapper och välj typ. Fonder och annat utan Yahoo-ticker väljer du *hoppa över* för.
4. Klicka Importera.

Varje köp blir ett eget innehav med exakt datum och kurs, inklusive courtage. Försäljningar dras från de äldsta köpen först (FIFO) och hamnar i historiken. Avanza räknar med genomsnittskurs (GAV), så resultatet per affär kan skilja sig något från Avanzas, men totalen blir densamma. Insättningar, utdelningar och skatt hoppas över.

Importen ersätter `innehav.csv` och `affarer.csv`. De gamla sparas först som `*_fore_import_<tid>.csv`. Kopplingen ISIN → ticker och dina val av typ sparas i `isin_tickers.csv`, så nästa import går på några sekunder. Exportera alltid hela historiken.

Tickern hittas i första hand i sparade kopplingar, sedan i `tickers_se.csv` (om den skapats med `uppdatera_tickers.py`, som nu även sparar ISIN) och sist via en sökning hos Yahoo.

### Sälja

Klicka **Sälj** på ett innehav och ange säljdatum, kursen du fick och antal. Säljer du en del (till exempel vid trimning) ligger resten kvar som innehav. Den sålda delen sparas i `affarer.csv` och syns under Utveckling. **Ta bort** finns kvar för felinmatningar och sparar ingenting.

### Utveckling mot index

Utvecklingen räknas som tidsviktad avkastning: varje dag räknas avkastningen på det du ägde, så köp och försäljningar flyttar inte kurvan. Köpdagen räknas från din köpkurs och säljdagen till din säljkurs. Utländska aktier räknas om till kronor med dagens växelkurs.

Jämförelseindex är `^OMXSPI` (hela Stockholmsbörsen). Det kan ändras till `^OMX` (OMXS30) med `JAMFORELSEINDEX` i `innehav.py`. Båda är prisindex utan utdelningar, så index ser ungefär 3 procentenheter per år sämre ut än en indexfond. Dina aktiekurser är däremot justerade för utdelningar, vilket gör jämförelsen något för fördelaktig för dig.

Kurshistorik för dina innehav, sålda aktier och index hämtas 5 år bakåt och sparas i `kurser.csv`. Innehav köpta tidigare än så räknas från första kända kurs.

### Typer av innehav

- **momentum** – hobbydelen. Säljreglerna gäller och ger beslut: SÄLJ, SÄLJ VID MÅNADSGENOMGÅNG, BEVAKA, TRIMMA eller BEHÅLL.
- **kärna** – långsiktiga innehav. Signalerna visas som information men ger inget säljbeslut.
- **övrigt** – t.ex. tester av andra verktyg. Visas som information.

### Säljreglerna

Ändras i `REGLER` högst upp i `innehav.py`.

| Regel | Beslut | Kollas |
|---|---|---|
| Kursen under MA200 | SÄLJ | varje vecka |
| MA50 under MA200 | SÄLJ | varje vecka |
| Kursen −20 % från högsta stängning sedan köp (släpande stopp) | SÄLJ | varje vecka |
| Utanför topp 40 i screenern | SÄLJ VID MÅNADSGENOMGÅNG | en gång i månaden |
| Nära någon av gränserna ovan | BEVAKA | – |
| Positionen > 1,5 × jämn fördelning (minst 15 %) | TRIMMA | vid månadsgenomgång |

Köpregeln är topp 20, säljregeln topp 40. Bufferten emellan gör att en aktie som glider lite i rankningen inte säljs i onödan.

Resultatet räknas på kurs mot köpkurs, utan mottagna utdelningar. Utländska innehav räknas om till kronor med dagens växelkurs.

## Köra automatiskt varje vecka

Öppna crontab med `crontab -e` och lägg till (lördag kl. 08:00, efter att fredagens kurser är klara):

```
0 8 * * 6 cd ~/screener && ./venv/bin/python screener.py >> /srv/storage/screener/logg.txt 2>&1
```

## Anpassa

Alla inställningar ligger i `KONFIG` högst upp i `screener.py`: utmapp, minsta omsättning, vilka trendfilter som gäller, vikten mellan 12- och 6-månadersmomentum, hur många som visas och urvalsreglerna. Ändra en sak i taget så du förstår effekten.

### Urvalsregler (gröna rader och ✓)

Av topplistan markeras de som också klarar:
- **1 mån ≤ +20 %** – inte rusat nyligen, för att undvika att köpa på toppen av en spurt
- **Omsättning ≥ 20 MSEK/dag** – lätt att köpa och sälja
- **Volatilitet ≤ 50 %** – inte för svängig

### Ett aktieslag per bolag

Har ett bolag flera aktieslag (SSAB A och B, SEB A och C) behålls bara det mest likvida, så samma bolag inte tar två platser. Äger du ett av aktieslagen (finns i `innehav.csv`) behålls det i stället. Stäng av med `"ett_aktieslag_per_bolag": False`.

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
