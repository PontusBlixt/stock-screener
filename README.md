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

Koden och datan ligger i två olika mappar – blanda inte ihop dem:

| | Sökväg | Innehåll |
|---|---|---|
| **Kodmappen** | `~/screener` | `.py`-filerna (inklusive `ikoner.py` med appikonerna), `tickers_se.csv`, `venv/`. Det här är git-repot. |
| **Datamappen** | `/srv/storage/screener` (standard) | Dina innehav, affärer, rapporter, kurser och nyckeltal. Ingen kod här. |

Datamappens sökväg ändras med `utmapp` i `KONFIG` i `screener.py` och med `SCREENER_UTMAPP` i `screener-web.service`. Båda måste peka på samma mapp.

```bash
# 1. Koden
git clone git@github.com:<användare>/stock-screener.git ~/screener
cd ~/screener
python3 -m venv venv
./venv/bin/pip install yfinance pandas numpy flask waitress

# 2. Datamappen (användaren som kör skripten måste kunna skriva där)
sudo mkdir -p /srv/storage/screener
sudo chown $USER /srv/storage/screener

# 3. Svensk tid – cron och tidsstämplar följer serverns klocka
sudo timedatectl set-timezone Europe/Stockholm
sudo systemctl restart cron

# 4. Testa utan internet, sedan på riktigt
./venv/bin/python screener.py --demo
./venv/bin/python screener.py
```

Kör alltid skripten med `./venv/bin/python` (eller efter `source venv/bin/activate`). Systemets `python3` saknar paketen.

### Datamappens innehåll

Undermapparna skapas automatiskt första gången de behövs.

| Fil / mapp | Vad | Får raderas? |
|---|---|---|
| `innehav.csv` | Dina innehav | Nej |
| `affarer.csv` | Dina avslutade affärer | Nej |
| `isin_tickers.csv`, `.import_installningar.json` | Dina val vid Avanza-importen | Nej (annars får du välja om) |
| `nyckeltal/ÅÅÅÅ-MM-DD.csv` | Daglig historik av nyckeltal – går inte att återskapa | **Aldrig** |
| `screener_ÅÅÅÅ-MM-DD.csv` | All data från varje veckokörning; används för veckojämförelse, marknadsbredd och utvärdering | Nej |
| `rapport_ÅÅÅÅ-MM-DD.html` / `.md` | Veckorapporterna (arkivet) | Ja, men de är små |
| `senaste.html` | Senaste veckorapporten, skrivs över varje vecka | – |
| `kurser.csv` | Stängningskurser för grafer och utveckling | Ja – återskapas vid nästa körning |
| `backup/` | Säkerhetskopior från Avanza-importen. Bara de 10 senaste per fil behålls, äldre raderas automatiskt | Ja |
| `strategier/<strategi>/` | Strategiernas låtsasportföljer: innehav, affärslogg och tillstånd | Nej – raderas den börjar strategin om från noll |
| `strategier_demo/` | Påhittade strategiportföljer från `screener.py --demo` | Ja |
| `nyckeltal_demo/` | Påhittad data från `nyckeltal.py --demo` | Ja |
| `.sok_historik.json` | Senaste sökningarna på Sök-sidan | Ja |

Säkerhetskopiera datamappen, inte bara git-repot – det är där allt som inte går att återskapa ligger.

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
- **Aktiesida** – två flikar. *Översikt*: kursgraf med MA50, MA200, köpkurs och stoppnivå, momentum och status för varje säljregel. *Nyckeltal*: värderings- och kvalitetspoäng mot sektorn, varje mått med sektormedian, analytikerdata, rapportdatum och ägande (se nedan). Adressen `…/aktie/VOLV-B.ST#nyckeltal` öppnar fliken direkt.
- **Utveckling** – portföljens tidsviktade avkastning mot index (momentum, kärna eller hela portföljen, valfri period) och alla avslutade affärer med träffsäkerhet, snittvinst och snittförlust.
- **Sök** – skriv en ticker eller ett bolagsnamn (t.ex. *volvo*, *VOLV-B.ST* eller *ADCT*). Sidan hämtar färska kurser från Yahoo och kör samma köpregler som screenern: omsättning, trendfilter, plats i rankningen och urvalsreglerna. Platsen räknas mot senaste veckorapportens godkända aktier, så den visar var aktien hade hamnat. Svaret är **KÖPLÄGE** (klarar allt och är topp 20), **BEVAKA** (godkänd men plats 21–40 eller bryter en urvalsregel) eller **INTE KÖPLÄGE**. Sidan visar också var den släpande stoppen skulle börja om du köper idag, och har samma Nyckeltal-flik som aktiesidan. Fungerar även för utländska aktier – omsättningen räknas om till kronor.
- **Strategier** – låtsasportföljer som följer varje strategi exakt, utan riktiga pengar (se *Strategier* nedan). Graf och tabell jämför strategierna med varandra, med din egen momentumportfölj och med index. Klicka på en strategi för dess innehav, beslut och affärslogg.
- **Arkiv** – tidigare veckorapporter, även som .md.
- **Status** (knappen *Data* uppe till höger) – visar om insamlingen går som den ska. Knappen är grön när allt är aktuellt och blir röd med namnet på det som släpar när:
  - **Nyckeltal** har missat mer än en handelsdag. Dagens fil räknas först efter kl. 19, eftersom cron kör 18:30.
  - **Veckorapporten** är äldre än 8 dagar.
  - **Kurserna** är äldre än 9 dagar.

  Sidan `/status` visar datum och antal filer för varje del. Vid rött: kontrollera cron (`crontab -l`) och loggfilerna i datamappen. Nyckeltalsdagar som missas går inte att hämta i efterhand.

### Importera från Avanza

Det rekommenderade sättet att lägga in innehav. Under **Mina innehav → Importera transaktioner**:

1. Exportera dina transaktioner från Avanza (Transaktioner → välj hela perioden → Exportera transaktioner).
2. Ladda upp .csv-filen.
3. Granska: välj vilka konton som ska räknas, kontrollera tickern för varje värdepapper och välj typ. Fonder och annat utan Yahoo-ticker väljer du *hoppa över* för.
4. Klicka Importera.

Varje köp blir ett eget innehav med exakt datum och kurs, inklusive courtage. Försäljningar dras från de äldsta köpen först (FIFO) och hamnar i historiken. Avanza räknar med genomsnittskurs (GAV), så resultatet per affär kan skilja sig något från Avanzas, men totalen blir densamma. Insättningar, utdelningar och skatt hoppas över.

Importen ersätter `innehav.csv` och `affarer.csv`. De gamla sparas först i `backup/` (de 10 senaste behålls). Kopplingen ISIN → ticker och dina val av typ sparas i `isin_tickers.csv`, så nästa import går på några sekunder. Exportera alltid hela historiken.

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

## På mobilen: app via Tailscale

Webbsidan fungerar som en app på telefonen (en PWA): på smal skärm flyttas menyn till en bottenrad, tabellerna visas som kort och graferna ritas i ett smalare format. Den kan läggas på hemskärmen och öppnas då i helskärm med egen ikon. Tailscale ger åtkomst hemifrån utan att någon port öppnas mot internet.

**1. Tailscale på servern**

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up            # öppna länken som visas och logga in
```

**2. Slå på HTTPS i Tailscale**

I Tailscales admin-konsol (login.tailscale.com) → **DNS**: slå på **MagicDNS** och **HTTPS Certificates**. HTTPS behövs för att telefonen ska kunna installera sidan som app.

**3. Publicera webbsidan i ditt tailnet**

```bash
sudo tailscale serve --bg 8080
tailscale serve status       # visar adressen, t.ex. https://linux-server.<ditt-tailnet>.ts.net
```

Inställningen ligger kvar efter omstart. Sidan nås bara från enheter inloggade i ditt tailnet – den är inte publik. Ta bort med `sudo tailscale serve --https=443 off`.

**4. Telefonen**

1. Installera Tailscale-appen och logga in med samma konto.
2. Öppna adressen från steg 3.
3. Lägg till på hemskärmen:
   - **iPhone (Safari):** Dela → *Lägg till på hemskärmen*
   - **Android (Chrome):** menyn ⋮ → *Installera app*

Utan nät visas senast öppnade versionen av varje sida (kan vara inaktuell), annars en offline-sida.

Tailscale räcker som skydd. Lösenordet `SCREENER_LOSEN` fungerar i webbläsaren men kan krångla i hemskärmsläget på iPhone.

## Nyckeltal varje dag

`nyckeltal.py` sparar värderings-, kvalitets- och analytikernyckeltal (bolagsvärde, EV/EBITDA, EV/Sales, P/E, P/B, marginaler, ROA, tillväxt, analytikernas vinstprognos och riktkurs, ägarandelar, rapportdatum, sektor m.m.) för alla aktier i tickerlistan och dina innehav. En fil per dag i `<utmapp>/nyckeltal/ÅÅÅÅ-MM-DD.csv`. Yahoo lämnar bara ut dagens nyckeltal, så historiken finns bara om den sparas löpande – den används senare för att backtesta ett värderingsfilter.

```bash
./venv/bin/python nyckeltal.py --demo    # test utan internet
./venv/bin/python nyckeltal.py           # dagens nyckeltal, tar några minuter
```

Efter körningen skrivs hur stor andel av aktierna som har varje mått ("Täckning"). Låg täckning för något mått betyder att Yahoo saknar det för många svenska bolag.

**Använd kolumnerna som slutar på `_k`** (`ev_ebit_k`, `ev_ebitda_k`, `ev_sales_k`, `ps_k`, `pb_k`), inte Yahoos färdiga kvoter. Yahoo har två fel som rättas här:

- **Valuta:** för bolag som rapporterar i euro eller dollar (ABB, AstraZeneca, Hexagon m.fl.) anger Yahoo kurs och börsvärde i kronor men omsättning, resultat och eget kapital i rapportvalutan – utan att växla. Kvoterna blir ungefär tio gånger fel.
- **Bolagsvärde:** Yahoos `enterpriseValue` är ibland helt fel. Vi räknar själva: börsvärde + (skulder − kassa) × växelkurs.

De korrigerade värdena räknas fram från råsiffrorna varje gång historiken läses in med `las_historik()`, så en framtida rättelse gäller automatiskt hela historiken. För banker och fastighetsbolag är EV-kvoter missvisande – använd `pb_k`.

### Värderings- och kvalitetspoäng (under utvärdering)

Visas i fliken Nyckeltal. **Poängen påverkar inte rankningen, köpreglerna eller säljreglerna** – `screener.py` och `innehav.py` läser aldrig nyckeltalen. Bara låtsasportföljerna B och C i `strategier.py` använder dem – aldrig veckorapporten eller dina egna säljsignaler. Under testperioden ska bara Översikt styra köp och försäljningar, så att utvärderingen av momentum blir ren.

Definitionen är bestämd i förväg (funktionen `poang()` i `nyckeltal.py`), så att ett kommande backtest testar exakt detta:

| | Värderingspoäng | Kvalitetspoäng |
|---|---|---|
| Vanliga bolag | snitt av percentilen för EBIT/EV, försäljning/EV och vinst/börsvärde | snitt av percentilen för ROA, bruttomarginal och rörelsemarginal |
| Banker, finans, fastighet | percentilen för eget kapital/kurs (inverterat P/B) | percentilen för ROE |

- Percentilen räknas inom sektorn; sektorer med färre än 8 bolag jämförs mot övriga sektorer. 100 = billigast/bäst i gruppen.
- Negativt resultat ger låg poäng i stället för att räknas bort. Flera aktieslag i samma bolag räknas som ett bolag.
- Planerad utvärdering efter ~6 månader: A) bara momentum, B) momentum men utan de 20 % dyraste i varje sektor, C) 70 % momentum + 30 % värdering. Ändring införs bara om både det historiska backtestet och den egna insamlade datan pekar åt samma håll.

Fält som saknas i äldre dagsfiler (t.ex. analytikerdata före 2026-10-19) visas som "–". Minikurvorna i fliken visas när minst fem dagars historik finns.

## Strategier: låtsasportföljer (pappershandel)

Det enda sättet att veta om en strategi fungerar är att se den gå framåt i tiden, där ingen kan ha facit i efterhand. Varje strategi har därför en egen låtsasportfölj som sköts av `strategier.py`. Den körs automatiskt i slutet av `screener.py` varje lördag och rör aldrig `innehav.csv` eller `affarer.csv`. Bara strategin *Daglig stopp* behöver en egen cron-rad (se *Köra automatiskt*).

**Reglerna är desamma som du följer** (om inte strategin säger annat):

- Boten beslutar en gång i veckan, på samma kurser som veckorapporten (fredagens stängning).
- Den säljer bara det reglerna säger: **SÄLJ** direkt (stopp, MA200, MA50 under MA200), **SÄLJ VID MÅNADSGENOMGÅNG** vid första körningen i en ny månad, och **TRIMMA** ned till jämn vikt. BEHÅLL och BEVAKA behålls.
- Den köper bara gröna rader (köpläge: plats 1–20 och klarar urvalsreglerna), i platsordning, tills den äger 20 aktier. Finns det färre gröna än lediga platser köps bara de som finns, och resten ligger kvar som kassa till nästa vecka. Äger den redan 20 som ska behållas händer ingenting.
- En aktie som säljs köps inte tillbaka samma vecka. Bara ett aktieslag per bolag.
- Startkapital 100 000 kr, lika vikt (varje köp är 1/20 av portföljens värde), hela aktier.
- Courtage 0,25 %, minst 1 kr (Avanza Mini), och 0,1 % spread på varje köp och sälj. Ändra `COURTAGE_PROCENT`, `COURTAGE_MINIMUM` och `SPREAD` överst i `strategier.py` om din courtageklass är en annan.
- Utdelningar räknas som återinvesterade (Yahoos justerade kurser), precis som på Utveckling-sidan.

**Strategierna.** Varje strategi ändrar en sak jämfört med Momentum v1 och svarar på en egen fråga:

| Strategi | Skillnad mot Momentum v1 | Frågan den svarar på |
|---|---|---|
| Momentum v1 | Ingen – exakt veckorapporten | Grundstrategin du följer |
| B: utan dyraste 20 % | Aktier bland de 20 % dyraste i sin sektor räknas som "klarar inte filtret" | Lönar det sig att undvika dyra aktier? |
| C: 70/30 momentum/värde | Rangordnas på 70 % momentumpoäng + 30 % värderingspoäng | Lönar det sig att väga in billighet? |
| Momentum + kvalitet | Rangordnas på 70 % momentumpoäng + 30 % kvalitetspoäng | Lönar det sig att väga in lönsamhet? |
| Topp 10 | 10 innehav (plats 1–10), dubbel vikt, säljs utanför topp 20 | Ger färre och starkare aktier mer? |
| Utan urvalsregler | Köper plats 1–20 även utan grön rad | Hjälper urvalsreglerna (uttänjd, likviditet, volatilitet)? |
| Daglig stopp | SÄLJ-reglerna (stopp, MA200, MA50) kollas varje vardag. Köp, månadsgenomgång och trimning sker fortfarande bara på lördagar | Räddar en snabbare stopp pengar, eller säljer den bara i onödan? |
| Slumpportfölj | Köper slumpvisa aktier bland de godkända (som klarar trendfiltren). Ingen rankningsregel vid sälj – annars samma säljregler | Hur mycket ger ren tur? Måttstocken för alla andra |

B, C och Momentum + kvalitet behöver nyckeltal från senaste veckan. Saknas de väntar strategin i stället för att handla på gammal data. Saknar en enskild aktie poäng tas den inte bort i B och räknas som medel (50) i C och Momentum + kvalitet. Slumpportföljens lottning bestäms av strategi och datum, så en omkörning ger samma lottning.

**Så läser du resultatet.** Med åtta strategier kommer någon att se bäst ut av ren tur, särskilt de första månaderna. Jämför därför alltid med Slumpportföljen: en strategi som inte ligger tydligt och stabilt över den har inte visat något. Tumregel: döm inget förrän efter minst 6 månader, och lita mer på en strategi som ligger före hela tiden än på en som fick ett enstaka lyft.

**Starta nu** i stället för att vänta till lördag (startar på den senaste veckorapporten):

```bash
cd ~/screener && ./venv/bin/python strategier.py
./venv/bin/python strategier.py --status   # visa ställningen utan att köra
./venv/bin/python strategier.py --dagligt  # daglig kontroll för Daglig stopp (körs av cron)
```

En strategi börjar på den rapport som finns när den startas – historiken spelas inte upp i efterhand, eftersom det vore ett backtest och inte ett framåttest. Om veckokörningen missas en lördag kör boten ikapp de rapporter den missat nästa gång, i datumordning.

**Lägga till en strategi senare:** lägg till den i `STRATEGIER` i `strategier.py`. Enkla varianter (antal innehav, köpgräns, säljgräns, stopp, urvalsregler, daglig kontroll) görs bara med inställningarna i `STANDARD`. En ny rangordning behöver också en rad i `rangordna()`. Den startar vid nästa körning och syns automatiskt i fliken. Ändra aldrig en strategi som redan körs – lägg i stället till en ny version (t.ex. "Momentum v2"), annars blandas två strategier i samma resultat.

## Köra automatiskt varje vecka

Öppna crontab med `crontab -e` och lägg till (lördag kl. 08:00, efter att fredagens kurser är klara):

```
0 8 * * 6 cd ~/screener && ./venv/bin/python screener.py >> /srv/storage/screener/logg.txt 2>&1
30 18 * * 1-5 cd ~/screener && ./venv/bin/python nyckeltal.py >> /srv/storage/screener/nyckeltal_logg.txt 2>&1
45 18 * * 1-5 cd ~/screener && ./venv/bin/python strategier.py --dagligt >> /srv/storage/screener/strategier_logg.txt 2>&1
```

Den andra raden sparar nyckeltalen varje vardag kl. 18:30, en timme efter Stockholmsbörsens stängning.

Den tredje raden är den dagliga kontrollen för strategin *Daglig stopp*. Den hämtar dagens kurser för strategins innehav (bara ett tjugotal aktier, några sekunder) och säljer det som bryter stoppen eller trendreglerna.

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
