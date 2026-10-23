"""
Översättningar svenska → engelska för webbsidan (används av sprak.py)
=====================================================================

ORD      exakt text (blanksteg och radbrytningar räknas som ett mellanslag)
MONSTER  text med {} där något varierar (tal, datum, namn). Det som står i {} översätts också om det
         självt finns här (t.ex. "Sälj {}" + "HM-B" → "Sell HM-B", "Kärna ({})" → "Core (2)").

Lägg till nya rader när ny svensk text dyker upp på sidan. Saknade texter kan listas med
SCREENER_SPRAK_LOGG=1 (skriver till <utmapp>/.saknade_oversattningar.txt).
"""

ORD: dict[str, str] = {
    # --- Meny och ramar ---------------------------------------------------------------------------
    "📈 Screener": "📈 Screener",
    "Veckans rapport": "Weekly report",
    "Mina innehav": "My holdings",
    "Utveckling": "Performance",
    "Strategier": "Strategies",
    "Sök": "Search",
    "Arkiv": "Archive",
    "Rapport": "Report",
    "Innehav": "Holdings",
    "Status": "Status",
    "Meny": "Menu",
    "Data": "Data",
    "All insamling är i fas": "All data collection is up to date",
    "Byt till svenska": "Byt till svenska",
    "Underlag för egna beslut, inte köp- eller säljrekommendationer. Data: Yahoo Finance.":
        "Input for your own decisions, not buy or sell recommendations. Data: Yahoo Finance.",
    "Inloggning krävs": "Login required",
    "Ingen anslutning": "No connection",
    "Försök igen": "Try again",
    "Servern går inte att nå just nu. Kontrollera att Tailscale är på i telefonen och att hemmaservern är igång.":
        "The server can't be reached right now. Check that Tailscale is on in your phone and that the home server is running.",
    "Sidor du öppnat tidigare visas från telefonens minne när nätet saknas – de kan vara inaktuella.":
        "Pages you opened before are shown from the phone's memory when there is no network – they may be out of date.",
    "Offline": "Offline",

    # --- Allmänna ord -----------------------------------------------------------------------------
    "Aktie": "Stock", "aktie": "stock", "Ticker": "Ticker", "Namn": "Name", "Typ": "Type", "Beslut": "Decision",
    "Antal": "Shares", "Köpkurs": "Buy price", "köpkurs": "buy price", "Kurs": "Price", "kurs": "price",
    "Kurser": "Prices", "Värde": "Value", "Värde idag": "Value today", "Resultat": "Result", "Resultat kr": "Result SEK",
    "Från högsta": "From high", "Till stopp": "To stop", "Plats": "Rank", "plats": "rank", "Orsak": "Reason",
    "Datum": "Date", "Handling": "Action", "Belopp": "Amount", "Courtage": "Brokerage", "Courtage + spread": "Brokerage + spread",
    "Kassa": "Cash", "kassa": "cash", "Valuta": "Currency", "Notering": "Note", "Köpdatum": "Buy date",
    "Säljdatum": "Sell date", "Säljkurs": "Sell price", "Köpt": "Bought", "Sålt": "Sold", "Köp": "Buy", "köp": "buy",
    "Sälj": "Sell", "sälj": "sell", "Affär": "Trade", "Affärer": "Trades", "Affärslogg": "Trade log",
    "Avslutade affärer": "Closed trades", "Dagar": "Days", "dagar": "days", "Dagar kvar": "Days left",
    "Andel": "Share", "Avkastning": "Return", "Strategi": "Strategy", "Senast": "Latest", "Kommentar": "Comment",
    "Insamling": "Data collection", "Information": "Information", "Översikt": "Overview", "Nyckeltal": "Key figures",
    "Poäng": "Score", "Urval": "Selection", "Volatilitet": "Volatility", "Oms/dag MSEK": "Turnover/day MSEK",
    "Omsättning": "Turnover", "omsättning": "turnover", "Omsättning/dag": "Turnover/day", "Omsättning per dag": "Turnover per day",
    "Blankning": "Short interest", "Mått": "Measure", "Källa": "Source", "Köpare": "Buyers", "Säljare": "Sellers",
    "Person i ledande ställning": "Person discharging managerial responsibilities", "Befattning": "Position",
    "Närstående": "Closely associated", "Karaktär": "Nature", "Instrumenttyp": "Instrument type", "Volym": "Volume",
    "Pris": "Price", "Transaktionsdatum": "Transaction date", "Publiceringsdatum": "Publication date",
    "Är kopplad till aktieprogram": "Linked to share programme", "Värdepapper": "Security", "Transaktioner": "Transactions",
    "Lägg till": "Add", "Ta bort": "Remove", "Välj": "Choose", "Visa:": "Show:", "Period:": "Period:", "Klicka": "Click",
    "ja": "yes", "nej": "no", "alla": "all", "annat": "other", "senaste": "latest", "vecka": "week", "rapport": "report",
    "innehav": "holdings", "OK": "OK", "INFO": "INFO", "Ägd": "Owned", "Äger nu": "Owned now", "Snitt ägd": "Avg. held",
    "I år": "This year", "Sedan start": "Since start", "hela perioden": "whole period", "Totalt innehav": "Total holdings",
    "Hela portföljen": "Whole portfolio", "Din portfölj": "Your portfolio", "din portfölj": "your portfolio",
    "Dina innehav": "Your holdings", "Din momentumportfölj": "Your momentum portfolio", "Realiserat": "Realised",
    "Positionsstorlek": "Position size", "Högsta sedan köp": "High since purchase", "Uppdatera kurser": "Update prices",
    "Kursgraf": "Price chart", "Sålde": "Sold", "Börsvärde": "Market cap", "Av börsvärdet": "Of market cap",
    "Jämförelseindex": "Benchmark index", "OMX Stockholm PI": "OMX Stockholm PI",
    "Prisindex, utan utdelningar": "Price index, without dividends", "gissas": "estimated", "eget beslut": "own decision",
    "inga affärer": "no trades", "ej godk.": "not appr.", "ej godkänd": "not approved", "procentenheter": "percentage points",
    "under validering": "under validation", "Under utvärdering": "Under evaluation", "Läs med försiktighet.": "Read with caution.",
    "Överlevnadsfel:": "Survivorship bias:", "En period är ett utfall.": "One period is one outcome.",

    # --- Beslut och typer -------------------------------------------------------------------------
    "BEHÅLL": "HOLD", "SÄLJ": "SELL", "BEVAKA": "WATCH", "TRIMMA": "TRIM", "KÖP": "BUY", "KOLLA": "CHECK",
    "KÖPLÄGE": "BUY ZONE", "INTE KÖPLÄGE": "NOT A BUY", "SÄLJ VID MÅNADSGENOMGÅNG": "SELL AT MONTHLY REVIEW",
    "SAKNAR DATA": "NO DATA", "Bevaka": "Watch", "Trimma": "Trim", "Sälj vid månadsgenomgång": "Sell at monthly review",
    "behåll": "hold", "Kärna": "Core", "kärna": "core", "Övrigt": "Other", "övrigt": "other", "Momentum": "Momentum",
    "momentum": "momentum", "(kärna)": "(core)", "(övrigt)": "(other)", "(momentum)": "(momentum)",
    "(information – kärna)": "(information – core)", "(information – övrigt)": "(information – other)",
    "(närstående)": "(closely associated)",

    # --- Veckorapporten ---------------------------------------------------------------------------
    "Svenska aktier · följ trenden": "Swedish stocks · follow the trend",
    "Analyserade bolag": "Companies analysed", "Klarar trendfiltren": "Pass the trend filters",
    "12-1 mån": "12-1 mo", "6 mån": "6 mo", "1 mån": "1 mo", "3 mån": "3 mo", "12 mån": "12 mo",
    "Över MA200": "Above MA200", "Från 52v-topp": "From 52w high", "Från 52v-högsta": "From 52w high",
    "Insider 90 d": "Insider 90 d", "Kandidater att granska vidare, inte köprekommendationer.":
        "Candidates to examine further, not buy recommendations.",
    "Kandidater att granska vidare, inte köprekommendationer. Data: Yahoo Finance.":
        "Candidates to examine further, not buy recommendations. Data: Yahoo Finance.",
    "Utan ✓:": "Without ✓:", "Föll ur:": "Dropped out:", "Nya:": "New:", "ny": "new",
    "Förändring sedan förra veckan": "Change since last week", "Förändring sedan förra körningen": "Change since the last run",
    "Marknadsbredd": "Market breadth", "Bortfiltrerade": "Filtered out", "Saknade data": "Missing data",
    "Veckorapport": "Weekly report", "Momentum och risk": "Momentum and risk",
    "under MA200": "below MA200", "MA50 under MA200": "MA50 below MA200", "MA50 över MA200": "MA50 above MA200",
    "låg omsättning": "low turnover", "för låg omsättning": "turnover too low", "för hög volatilitet": "volatility too high",
    "för uttänjd senaste månaden": "too stretched last month", "för kort historik för MA200": "history too short for MA200",
    "(för kort historik)": "(history too short)",
    "Klarar urvalsreglerna": "Passes the selection rules",
    "Blankning = total blankning enligt FI (↑↓ förändring senaste 30 dagarna). Insider 90 d = nettoköp av personer i ledande ställning som andel av börsvärdet, antal köpare ↑ och säljare ↓. Bara information – påverkar inte poängen.":
        "Short interest = total short interest per FI (↑↓ change over the last 30 days). Insider 90 d = net buying by persons discharging managerial responsibilities as a share of market cap, number of buyers ↑ and sellers ↓. Information only – does not affect the score.",
    "Nettoköp av insiders senaste 90 dagarna som andel av börsvärdet, köpare ↑ och säljare ↓":
        "Net insider buying over the last 90 days as a share of market cap, buyers ↑ and sellers ↓",
    "Total blankning enligt FI, förändring senaste 30 dagarna": "Total short interest per FI, change over the last 30 days",
    "Inga signaler denna vecka.": "No signals this week.",
    "★ Insidersignaler denna vecka": "★ Insider signals this week",
    "★ Insidersignal denna vecka": "★ Insider signal this week",
    "★ insider": "★ insider",
    "Insiders har köpt netto (publicerat fredag–torsdag, första veckan i en köpvåg) och nästa rapport är 31–60 dagar bort – samma regel som strategin Insider 30. Insideranalysen: slog marknaden efter kostnad 8 av 9 år, nästa rapports reaktion +1,2 %. UNDER VALIDERING – påverkar inte momentumrankningen.":
        "Insiders have been net buyers (published Friday–Thursday, first week of a buying wave) and the next report is 31–60 days away – the same rule as the Insider 30 strategy. The insider analysis: beat the market after costs 8 of 9 years, next report reaction +1.2 %. UNDER VALIDATION – does not affect the momentum ranking.",
    "Insiders har köpt netto och nästa rapport är 31–60 dagar bort – samma regel som Insider 30 (under validering).":
        "Insiders have been net buyers and the next report is 31–60 days away – the same rule as Insider 30 (under validation).",
    "Insiders netto (4 v)": "Insiders net (4 w)", "Dagars omsättning": "Days of turnover",
    "Nettoköpet delat med aktiens snittomsättning per dag – ordningen strategin köper i":
        "Net buying divided by the stock's average daily turnover – the order the strategy buys in",
    "Ordningen i rutan och för strategins köp: nettoköpet i dagars omsättning.":
        "Order in the box and for the strategy's purchases: net buying in days of turnover.", "Grå rad = omsättning under 5 MSEK/dag – strategin köper inte dessa.":
        "Grey row = turnover below 5 MSEK/day – the strategy does not buy these.",
    "· = omsättning under 5 MSEK/dag – strategin köper inte dessa.":
        "· = turnover below 5 MSEK/day – the strategy does not buy these.",
    "ej i momentumlistan": "not in the momentum list", "klarar trendfilter": "passes trend filters",

    # --- Innehav ----------------------------------------------------------------------------------
    "Säljreglerna gäller innehav av typen": "The sell rules apply to holdings of type",
    "Lägg till innehav": "Add holding", "Ticker eller bolagsnamn": "Ticker or company name",
    "Svenska aktier skrivs med .ST, t.ex.": "Swedish stocks are written with .ST, e.g.",
    ". Amerikanska aktier skrivs utan suffix, t.ex.": ". US stocks are written without a suffix, e.g.",
    ". Köpkursen anges i aktiens egen valuta. Flera köp av samma aktie läggs in som separata rader.":
        ". Enter the buy price in the stock's own currency. Several purchases of the same stock are entered as separate rows.",
    "Hämtar dagens kurser för dina innehav. Rankningen uppdateras bara vid veckokörningen.":
        "Fetches today's prices for your holdings. The ranking is only updated in the weekly run.",
    "Säljreglerna": "The sell rules", "Köpreglerna": "The buy rules",
    "Släpande stopp startar på": "The trailing stop starts at",
    "Inga innehav ännu – lägg till ditt första nedan.": "No holdings yet – add your first one below.",
    "Inga innehav att uppdatera.": "No holdings to update.",
    "Importera från Avanza": "Import from Avanza", "Utveckling och historik →": "Performance and history →",
    "Registrera försäljning": "Register sale", "← Mina innehav": "← My holdings",
    "Säljer du färre aktier än du äger ligger resten kvar som innehav. Ange kursen du faktiskt fick (Avanza visar den under avslutade affärer).":
        "If you sell fewer shares than you own, the rest remains as a holding. Enter the price you actually got (Avanza shows it under closed trades).",
    "Ta bort affären? Använd bara för felinmatningar.": "Remove the trade? Only use this for input errors.",
    "För felinmatningar": "For input errors",
    "Köpkurs, antal och datum måste vara ifyllda med siffror.": "Buy price, shares and date must be filled in with numbers.",
    "Säljkurs, antal och datum måste vara ifyllda med siffror.": "Sell price, shares and date must be filled in with numbers.",
    "Antal och kurs måste vara större än noll.": "Shares and price must be greater than zero.",
    "Säljdatum kan inte vara före köpdatum.": "The sell date cannot be before the buy date.",
    "Ogiltig ticker.": "Invalid ticker.", "Svenska aktier slutar på .ST, t.ex. VOLV-B.ST.": "Swedish stocks end with .ST, e.g. VOLV-B.ST.",
    "Ingen kursdata – klicka \"Uppdatera kurser\" eller kontrollera tickern.":
        "No price data – click \"Update prices\" or check the ticker.",
    "Ingen kursdata för:": "No price data for:", "Inga affärer ännu.": "No trades yet.",
    "gamla kurser – kör screenern eller Uppdatera kurser": "old prices – run the screener or Update prices",
    "du äger den – se säljreglerna": "you own it – see the sell rules",
    "Om du köper idag": "If you buy today",
    "under Mina innehav när du säljer.": "under My holdings when you sell.",
    "Tidigare innehav sparades i backup/.": "Previous holdings were saved in backup/.",
    "t.ex. VOLV-B.ST": "e.g. VOLV-B.ST", "VOLV-B.ST eller ADCT": "VOLV-B.ST or ADCT",
    "stoppen nådd": "stop reached", "släpande stopp": "trailing stop", "stopp": "stop", "trimning": "trimming",
    "trendregel (MA200/MA50)": "trend rule (MA200/MA50)", "klarar inte screenerns filter": "fails the screener filters",
    "rankas inte – klarar inte filtren ovan": "not ranked – fails the filters above",
    "Klarar inte trend- eller omsättningsfiltret.": "Fails the trend or turnover filter.",
    "Kontrollera alltid nyheter och kommande rapportdatum innan köp. Regler tillämpade på kursdata, inte en köprekommendation.":
        "Always check news and upcoming report dates before buying. Rules applied to price data, not a buy recommendation.",
    "Inte uttänjd": "Not stretched", "Likviditet för urval": "Liquidity for selection",
    "Avstånd till MA200": "Distance to MA200", "Plats i veckorapporten": "Rank in the weekly report",
    "Lägg till som innehav →": "Add as holding →", "trimma, för stor andel": "trim, too large a share",
    "för stor andel – trimma": "too large a share – trim", "för stor andel – trimmas": "too large a share – being trimmed",
    "lottad bland godkända": "drawn among approved", "månadsgenomgång": "monthly review", "kvartalsgenomgång": "quarterly review",
    "månadsgenomgång:": "monthly review:",

    # --- Utveckling -------------------------------------------------------------------------------
    "Utveckling mot index": "Performance vs. index", "Mot index": "Vs. index",
    "Tidsviktad avkastning: köp och försäljningar flyttar inte kurvan, bara hur aktierna rört sig. Samma metod som fonder jämförs med.":
        "Time-weighted return: purchases and sales don't move the curve, only how the stocks moved. The same method funds are compared with.",
    ", från ditt första köp till idag, och alla konton.": ", from your first purchase until today, and all accounts.",
    "Snittvinst": "Average gain", "Snittförlust": "Average loss", "Snittvinst / snittförlust": "Average gain / average loss",
    "Träffsäkerhet": "Hit rate", "Dina riktiga affärer, samma period": "Your real trades, same period",

    # --- Strategier och backtest ------------------------------------------------------------------
    "Strategiernas utveckling": "Strategy performance", "Virtuella strategiportföljer": "Virtual strategy portfolios",
    "Backtest": "Backtest", "Backtest – hur hade det gått tidigare? →": "Backtest – how would it have gone before? →",
    "Mot OMX Stockholm PI": "Vs. OMX Stockholm PI", "Mot slump": "Vs. random", "Största nedgång": "Max drawdown",
    "Största nedgång = största fall från en tidigare topp under perioden – hur ont det hade gjort på vägen.":
        "Max drawdown = the largest fall from an earlier peak during the period – how much it would have hurt along the way.",
    "Momentum v1": "Momentum v1", "Veckorapporten som den är": "The weekly report as it is",
    "B: utan dyraste 20 %": "B: without the most expensive 20 %",
    "Momentum, men hoppar över de 20 % dyraste i varje sektor": "Momentum, but skips the 20 % most expensive in each sector",
    "C: 70/30 momentum/värde": "C: 70/30 momentum/value",
    "Rangordnas på 70 % momentumpoäng + 30 % värderingspoäng": "Ranked on 70 % momentum score + 30 % valuation score",
    "Momentum + kvalitet": "Momentum + quality",
    "Rangordnas på 70 % momentumpoäng + 30 % kvalitetspoäng": "Ranked on 70 % momentum score + 30 % quality score",
    "Topp 10": "Top 10", "Bara 10 innehav (plats 1–10), dubbel vikt, säljs utanför topp 20":
        "Only 10 holdings (rank 1–10), double weight, sold outside the top 20",
    "Utan urvalsregler": "Without selection rules",
    "Köper topp 20 även om de är uttänjda, volatila eller tunna": "Buys the top 20 even if stretched, volatile or thinly traded",
    "Daglig stopp": "Daily stop", "Som Momentum v1, men stopp och trendregler kollas varje vardag":
        "Like Momentum v1, but stop and trend rules are checked every weekday",
    "Slumpportfölj": "Random portfolio",
    "20 slumpvisa bland godkända, samma säljregler – måttstock för tur": "20 random among approved, same sell rules – a yardstick for luck",
    "Månadsvis": "Monthly", "Som Momentum v1, men säljer, köper och trimmar bara en gång i månaden":
        "Like Momentum v1, but sells, buys and trims only once a month",
    "Kvartalsvis": "Quarterly", "Som Momentum v1, men säljer, köper och trimmar bara en gång per kvartal":
        "Like Momentum v1, but sells, buys and trims only once a quarter",
    "Köper efter insiderköp 31–60 dagar före rapport, säljer 30 dagar efter rapporten":
        "Buys after insider buying 31–60 days before a report, sells 30 days after the report",
    "Köper efter insiderköp 31–60 dagar före rapport, säljer 60 dagar efter rapporten":
        "Buys after insider buying 31–60 days before a report, sells 60 days after the report",
    "Startar vid nästa veckokörning.": "Starts at the next weekly run.",
    "Startar vid nästa veckokörning (kräver nyckeltal från senaste veckan).":
        "Starts at the next weekly run (requires key figures from the last week).",
    "Nästa beslut vid veckokörningen på lördag.": "Next decision at the weekly run on Saturday.",
    "Grafen visas när strategin funnits i minst två handelsdagar.": "The chart is shown once the strategy has existed for at least two trading days.",
    "Inga innehav – inga gröna rader fanns vid senaste beslutet. Kassan väntar till nästa vecka.":
        "No holdings – there were no green rows at the last decision. The cash waits until next week.",
    "Innehaven när backtestet slutade, med beslutet som gällde då.": "The holdings when the backtest ended, with the decision that applied then.",
    "Beslutet visar läget med dagens kurser. Boten agerar bara vid veckokörningen, precis som du.":
        "The decision shows the situation at today's prices. The bot only acts at the weekly run, just like you.",
    "← Alla strategier": "← All strategies", "← Strategier (framåt, på riktigt)": "← Strategies (forward, for real)",
    "Ingen strategi har startat än. De startar automatiskt vid nästa veckokörning, eller direkt med":
        "No strategy has started yet. They start automatically at the next weekly run, or right away with",
    "Ingen strategi med daglig kontroll har startat än.": "No strategy with daily checks has started yet.",
    "Vad som hade hänt om strategierna startat tidigare. Samma kod som låtsasportföljerna: varje lördag i perioden räknas topplistan fram med bara de kurser som fanns då, och boten fattar sina beslut.":
        "What would have happened if the strategies had started earlier. The same code as the paper portfolios: every Saturday in the period the top list is computed using only the prices available then, and the bot makes its decisions.",
    "Jämför flera längder – en strategi som vinner i alla är mer trovärdig än en som vinner stort i en.":
        "Compare several lengths – a strategy that wins in all of them is more credible than one that wins big in one.",
    "Jämför med slumpportföljen": "Compare with the random portfolio",
    ", inte bara index. Ändra inte strategierna efter backtestet – det är framåttestet som avgör.":
        ", not just the index. Don't change the strategies after the backtest – the forward test is what decides.",
    "bara bolag som är noterade idag ingår. Bolag som gått i konkurs eller avnoterats saknas, så alla strategier ser något bättre ut än de hade varit.":
        "only companies listed today are included. Companies that went bankrupt or were delisted are missing, so all strategies look somewhat better than they would have been.",
    "Köra ett backtest": "Running a backtest", "På servern (första gången hämtas 5 års kurser, några minuter):":
        "On the server (the first time 5 years of prices are fetched, a few minutes):",
    "kräver historiska nyckeltal och kan inte testas bakåt.": "require historical key figures and cannot be backtested.",
    "Insider 30": "Insider 30", "Insider 60": "Insider 60", "har inte startat än.": "has not started yet.",
    "Inom topp 40": "Within top 40", "utanför topp 40": "outside top 40",

    # --- Sök och aktiesidan -----------------------------------------------------------------------
    "Flera träffar – välj en": "Several matches – choose one", "Senaste sökningar": "Recent searches",
    "t.ex. Volvo, VOLV-B.ST, ADCT": "e.g. Volvo, VOLV-B.ST, ADCT",
    "Analysera": "Analyse", "I screenern": "In the screener", "Klarar filtren": "Passes the filters",
    "Urvalsregler": "Selection rules", "Nästa rapport": "Next report", "Ingår inte i den svenska screenern.":
        "Not included in the Swedish screener.", "ingår inte i den svenska screenern": "not included in the Swedish screener",
    "Volatilitet (3 mån, årlig)": "Volatility (3 mo, annual)", "12 mån exkl. senaste": "12 mo excl. latest",
    "Insiderhandel och blankning": "Insider trading and short interest", "Insynsaffärer": "Insider trades",
    "Inga köp eller försäljningar av insiders senaste året.": "No insider purchases or sales in the last year.",
    "Insiders netto 90 dagar": "Insiders net 90 days",
    "Källa: Finansinspektionens insyns- och blankningsregister (offentliga). Belopp i kronor. Insiders får inte handla 30 dagar före en rapport. Affärer i aktieprogram räknas inte. Stora belopp från en enda person är ofta en storägare (t.ex. ett investmentbolag med plats i styrelsen) – titta på antal köpare och andel av börsvärdet.":
        "Source: the Swedish FSA's (Finansinspektionen) insider and short-selling registers (public). Amounts in SEK. Insiders may not trade 30 days before a report. Trades in share programmes are not counted. Large amounts from a single person are often a major owner (e.g. an investment company with a board seat) – look at the number of buyers and the share of market cap.",
    "Senaste 12 månaderna, per person, störst belopp först.": "Last 12 months, per person, largest amount first.",
    "* Anmält i annan valuta, men priset stämmer med aktiens kurs i kronor – räknas som kronor.":
        "* Reported in another currency, but the price matches the stock's price in SEK – counted as SEK.",
    "förändring visas när 30 dagars historik finns": "change is shown when 30 days of history exist",
    "förvärv": "acquisition", "Förvärv": "Acquisition", "Avyttring": "Disposal",
    "Kursgraf · 1 år": "Price chart · 1 year",
    "En topp vid en rapport är väntad. En topp utan rapport eller nyhet kan vara värd att undersöka.":
        "A spike at a report is expected. A spike without a report or news may be worth looking into.",
    "topp (över 3× snittet)": "spike (over 3× the average)", "snitt 20 dagar": "20-day average",
    "För lite data för en graf.": "Too little data for a chart.", "För lite omsättningsdata för en graf.": "Too little turnover data for a chart.",
    "För kort period för en graf ännu – kom tillbaka om några dagar.": "Too short a period for a chart yet – come back in a few days.",
    "Nyckeltalen kunde inte visas just nu.": "The key figures could not be shown right now.",
    "Värdering": "Valuation", "Tillväxt": "Growth", "Lönsamhet och kvalitet": "Profitability and quality",
    "Ägande och skuld": "Ownership and debt", "Läge i gruppen": "Position in the group",
    "Lägre multipel = billigare. Stapeln visar hur billig aktien är jämfört med gruppen.":
        "Lower multiple = cheaper. The bar shows how cheap the stock is compared with its group.",
    "Banker, finans och fastighet värderas med eget kapital mot kurs (P/B), eftersom EV-mått blir missvisande för dem.":
        "Banks, financials and real estate are valued on equity vs. price (P/B), since EV measures are misleading for them.",
    "Skydd mot värdefällor: billigt och lönsamt är bättre än billigt och olönsamt.":
        "Protection against value traps: cheap and profitable beats cheap and unprofitable.",
    "Prognosändringen (revideringen) blir meningsfull när några veckors historik finns. Stigande prognoser är en av de starkaste kända signalerna.":
        "The forecast change (revision) becomes meaningful once a few weeks of history exist. Rising forecasts are one of the strongest known signals.",
    "Värderingspoängen är snittet av percentilen för EBIT/EV, försäljning/EV och vinst/börsvärde;":
        "The valuation score is the average percentile of EBIT/EV, sales/EV and earnings/market cap;",
    "kvalitetspoängen av ROA, brutto- och rörelsemarginal. Negativt resultat ger låg poäng i stället för att räknas bort.":
        "the quality score from ROA, gross and operating margin. A negative result gives a low score instead of being excluded.",
    "Rörelsemarginal": "Operating margin", "Bruttomarginal": "Gross margin", "Vinsttillväxt": "Earnings growth",
    "Omsättningstillväxt": "Revenue growth", "Förväntad vinstförändring": "Expected earnings change",
    "Vinst per aktie, prognos": "Earnings per share, forecast", "Vinst per aktie, senaste 12 mån": "Earnings per share, last 12 mo",
    "senaste rapporterade kvartal": "latest reported quarter", "Senaste dagarna": "Recent days", "bättre": "better",
    "Nettoskuld/EBITDA": "Net debt/EBITDA", "Skuldsättning": "Leverage", "Direktavkastning": "Dividend yield",
    "Utdelning": "Dividend", "Sektor": "Sector", "Bransch": "Industry", "Riktkurs": "Target price", "ingen riktkurs": "no target price",
    "Analytiker": "Analysts", "Insiderägande": "Insider ownership", "Institutionellt ägande": "Institutional ownership",

    # --- Arkiv, status, import --------------------------------------------------------------------
    "Alla veckorapporter, nyast först.": "All weekly reports, newest first.", "Inga rapporter ännu.": "No reports yet.",
    "Om insamlingen körs som den ska. Nyckeltalen får missa en handelsdag (t.ex. helgdag) innan de flaggas.":
        "Whether data collection runs as it should. Key figures may miss one trading day (e.g. a holiday) before being flagged.",
    "Felsöka på servern": "Troubleshooting on the server", "ingen insamling har körts än": "no collection has run yet",
    "väntar på första dagliga körningen": "waiting for the first daily run", "screener.py har inte körts än": "screener.py has not run yet",
    "inga filer än – kör insamling.py": "no files yet – run insamling.py", "Rapportdatum": "Report dates",
    "ingen veckorapport att jämföra med ännu": "no weekly report to compare with yet",
    "Importera transaktioner": "Import transactions", "Exportera transaktioner": "Export transactions",
    "Läser in alla köp och försäljningar från Avanzas exportfil – rekommenderat i stället för att mata in för hand.":
        "Reads all purchases and sales from Avanza's export file – recommended instead of entering them by hand.",
    "Logga in på avanza.se och gå till": "Log in to avanza.se and go to", "(under Min ekonomi).": "(under My finances).",
    ". Du får en .csv-fil.": ". You get a .csv file.",
    "Ta alltid med hela historiken. Saknas gamla köp kan försäljningar inte matchas.":
        "Always include the full history. If old purchases are missing, sales cannot be matched.",
    "Läs in och granska": "Load and review", "Välj en fil att ladda upp.": "Choose a file to upload.",
    "Filen innehåller inga transaktioner.": "The file contains no transactions.",
    "Hittade inga köp eller försäljningar i filen.": "Found no purchases or sales in the file.",
    "Bocka bara i de konton som ska räknas, till exempel ditt ISK.": "Only tick the accounts that should count, for example your ISK.",
    "Välj minst ett konto.": "Choose at least one account.", "Importen ersätter": "The import replaces",
    "hoppa över": "skip", "Konto": "Account", "Konton": "Accounts",
    "Gula rader saknar ticker. Fyll i den (sök på finance.yahoo.com, svenska aktier slutar på":
        "Yellow rows are missing a ticker. Fill it in (search on finance.yahoo.com, Swedish stocks end with",
    ") eller välj": ") or choose",
    ", till exempel för fonder. Värdepapper utan ticker importeras ändå till historiken men får ingen kursdata.":
        ", for example for funds. Securities without a ticker are still imported to the history but get no price data.",
    "Insiders": "Insiders", "Institutioner": "Institutions", "Kvalitet": "Quality", "Rapportvaluta": "Reporting currency",
    "Rekommendation": "Recommendation", "Skuld / eget kapital": "Debt / equity", "Antal × köpkurs": "Shares × buy price",
    "idag": "today", "imorgon": "tomorrow", "räkna med större svängningar": "expect larger swings",
    "måttstock för tur.": "a yardstick for luck.", "måttstock för tur": "a yardstick for luck",
    "påverkar inte köp- eller säljreglerna.": "does not affect the buy or sell rules.",
    "påverkar inte poängen eller reglerna.": "does not affect the score or the rules.",
    "Modellens bedömningar": "The model's assessments", "GODKÄND": "APPROVED", "AVSTÅR": "SKIP", "väntar": "pending",
    "Vecka": "Week", "Modellen": "Model", "Gräns": "Threshold", "För": "For", "Emot": "Against", "Utfall": "Outcome",
    "Rätt?": "Right?", "Insider ML": "Insider ML", "Insider ML (bara godkända)": "Insider ML (approved only)",
    "Insider 30 (alla signaler)": "Insider 30 (all signals)", "Slumpfilter (samma andel)": "Random filter (same share)",
    "Jämförelse: Insider 30 under samma testperiod som modellen": "Comparison: Insider 30 over the same test period as the model",
    "Insider 30, men köper bara signaler som modellen godkänner (walk-forward, aldrig tränad på teståret)":
        "Insider 30, but buys only signals the model approves (walk-forward, never trained on the test year)",
    "Kontroll: Insider 30 där lika stor andel signaler hoppas över slumpvis":
        "Control: Insider 30 where the same share of signals is skipped at random",
    "Varje insidersignal under testperioden. Modellen bedömde den bara med det som var känt den veckan, och tränades aldrig på samma år.":
        "Every insider signal during the test period. The model judged it only on what was known that week, and was never trained on the same year.",
    "och": "and",
    "= de egenskaper som drog sannolikheten mest upp och ned (i procentenheter, jämfört med en typisk signal).":
        "= the features that pushed the probability most up and down (in percentage points, compared with a typical signal).",
    "= aktien mot marknaden till säljdagen, före kostnad. Godkänd om sannolikheten är minst gränsen (träningens median). En godkänd signal köps bara om det finns plats i portföljen.":
        "= the stock vs. the market until the sell date, before costs. Approved if the probability is at least the threshold (the training median). An approved signal is only bought if there is room in the portfolio.",
    "stort köp": "large purchase", "köpbelopp": "purchase amount", "saknas": "missing",
    "Modellens skäl": "Model's reasons",
    "Maskininlärningens sannolikhet att aktien slår marknaden efter kostnad till säljdagen. ✓ = över gränsen. Bara information – påverkar inga köp.":
        "The machine learning model's probability that the stock beats the market after costs until the sell date. ✓ = above the threshold. Information only – does not affect any purchases.",
    "Bedömningarna sparas varje vecka så att den kan utvärderas framåt.": "The assessments are saved every week so it can be evaluated going forward.",
    "Som Insider 30, men pengar som inte används ligger i en indexfond (OMXSPI + utdelning − avgift)":
        "Like Insider 30, but money not in use sits in an index fund (OMXSPI + dividends − fee)",
    "Indexprov": "Index test", "Insider 30 + index": "Insider 30 + index",
    "Insider 30 (kassa kontant)": "Insider 30 (cash as cash)",
    "Jämförelse: Insider 30 som den är, oinvesterad kassa kontant": "Comparison: Insider 30 as it is, uninvested cash held as cash",
    "Insider 30, men pengar som inte används ligger i en indexfond (OMXSPI + utdelning − avgift)":
        "Insider 30, but money not in use sits in an index fund (OMXSPI + dividends − fee)",
    "Variantprov": "Variant test", "Insider 30 (som strategin)": "Insider 30 (as the strategy)",
    "Jämförelse: Insider 30 under samma period": "Comparison: Insider 30 over the same period",
    "Sälj direkt vid +20 % före rapport": "Sell at once at +20 % before the report",
    "Sälj sista veckan före rapport om +20 %": "Sell the last week before the report if +20 %",
    "Som Insider 30, men säljer före rapporten så fort aktien stigit 20 % sedan köpet":
        "Like Insider 30, but sells before the report as soon as the stock has risen 20 % since purchase",
    "Som Insider 30, men säljer vid sista veckokörningen före rapporten om aktien då ligger 20 % upp":
        "Like Insider 30, but sells at the last weekly run before the report if the stock is then up 20 %",
    "Bekräfta import": "Confirm import", "Granska import": "Review import", "Importera": "Import",
    "1. Exportera från Avanza": "1. Export from Avanza", "2. Ladda upp filen": "2. Upload the file",
}

MONSTER: dict[str, str] = {
    # --- Sidtitlar ---
    "{} · Screener": "{} · Screener",
    "Sälj {} · Screener": "Sell {} · Screener",
    "Sälj {}": "Sell {}",
    "Sök {}": "Search {}",
    "Strategi {}": "Strategy {}",

    # --- Veckorapporten ---
    "Momentum-screener – {} (DEMODATA)": "Momentum screener – {} (DEMO DATA)",
    "Momentum-screener – {}": "Momentum screener – {}",
    "Urval i topp {}": "Selected in top {}",
    "Grön rad = klarar urvalsreglerna: 1 mån ≤ {} %, omsättning ≥ {} MSEK/dag, volatilitet ≤ {} %. Håll muspekaren över en rad för orsaken.":
        "Green row = passes the selection rules: 1 mo ≤ {} %, turnover ≥ {} MSEK/day, volatility ≤ {} %. Hover over a row for the reason.",
    "Andel bolag som klarar trendfiltren, senaste {} körningar": "Share of companies passing the trend filters, last {} runs",
    "{} ({})": "{} ({})",
    "under MA200: {}": "below MA200: {}", "MA50 under MA200: {}": "MA50 below MA200: {}", "låg omsättning: {}": "low turnover: {}",
    "under MA200, MA50 under MA200": "below MA200, MA50 below MA200",
    "{}: {} av {}": "{}: {} of {}",
    "plats {}": "rank {}", "plats {} av {}": "rank {} of {}", "plats {}av {}": "rank {} of {}",
    "plats {}, grön rad": "rank {}, green row", "plats {}, topp {}": "rank {}, top {}",
    "plats {}, nära gränsen topp {}": "rank {}, close to the top-{} limit", "plats {}, utanför topp {}": "rank {}, outside top {}",
    "· plats {}, nära gränsen topp {}": "· rank {}, close to the top-{} limit",
    "plats {} av {} (poäng {})": "rank {} of {} (score {})", "plats > {}": "rank > {}",
    "Plats {} – utanför topp {}.": "Rank {} – outside top {}.",
    "Godkänd men plats {} – inte topp {} ännu.": "Approved but rank {} – not top {} yet.",
    "Klarar filtren, topp {} och urvalsreglerna.": "Passes the filters, top {} and the selection rules.",
    "utanför topp {}": "outside top {}", "Topp {}": "Top {}", "Inom topp {}": "Within top {}",
    "1 mån {} (max +{} %)": "1 mo {} (max +{} %)",
    "{} (för hög volatilitet)": "{} (volatility too high)", "{} (för uttänjd senaste månaden)": "{} (too stretched last month)",
    "{} (för låg omsättning)": "{} (turnover too low)",
    "{} köpare · {} säljare · {} av börsvärdet": "{} buyers · {} sellers · {} of market cap",
    "{} köpare · {} säljare": "{} buyers · {} sellers",
    "{} pe senaste {} dagarna": "{} pp over the last {} days",
    "under {} %": "below {} %",

    # --- Innehav ---
    "Kärna ({})": "Core ({})", "Momentum ({})": "Momentum ({})", "Övrigt ({})": "Other ({})",
    ". Kärna och övrigt visas som information. Kurser från {}.": ". Core and other are shown as information. Prices from {}.",
    "Köpt {} · {} st à {} SEK · senaste kurs {}": "Bought {} · {} shares at {} SEK · latest price {}",
    "Köpt {} · {} st à {} · senaste kurs {}": "Bought {} · {} shares at {} · latest price {}",
    "Innehav köpt {}": "Holding bought {}",
    "{} · plats {}": "{} · rank {}",
    "Senaste kurs {} ({})": "Latest price {} ({})",
    "Säljkurs ({})": "Sell price ({})",
    "{} × {} SEK": "{} × {} SEK",
    "{} kr ({} %)": "{} SEK ({} %)", "{} kr": "{} SEK",
    "Ta bort {} helt, utan att spara som affär? Använd Sälj om du sålt aktien.":
        "Remove {} completely, without saving it as a trade? Use Sell if you sold the stock.",
    "{} tillagd.": "{} added.", "{} borttagen.": "{} removed.", "Affären i {} borttagen.": "The trade in {} was removed.",
    "Kunde inte hämta kurser: {}": "Could not fetch prices: {}",
    "Kunde inte hämta kurser just nu ({}). Klicka Uppdatera kurser senare.": "Could not fetch prices right now ({}). Click Update prices later.",
    "Hittade ingen kursdata för {} hos Yahoo. Kontrollera tickern.{}": "Found no price data for {} at Yahoo. Check the ticker.{}",
    "Yahoo har ingen kursdata för {}.{}": "Yahoo has no price data for {}.{}",
    "Inga nya kurser för: {}": "No new prices for: {}",
    "Släpande stopp ({} %)": "Trailing stop ({} %)", "Släpande stopp ({}%)": "Trailing stop ({} %)",
    "Stoppen flyttas upp när kursen stiger, {} % under högsta stängning sedan köp.":
        "The stop moves up as the price rises, {} % below the highest close since purchase.",
    "om kursen hamnar under MA200, om MA50 korsar under MA200, eller om kursen fallit {} % från högsta sedan köp.":
        "if the price falls below MA200, if MA50 crosses below MA200, or if the price has fallen {} % from the high since purchase.",
    "om kursen hamnar under MA{}, om MA{} korsar under MA{}, eller om kursen fallit {} % från högsta sedan köp.":
        "if the price falls below MA{}, if MA{} crosses below MA{}, or if the price has fallen {} % from the high since purchase.",
    "om aktien hamnat utanför topp {} i screenern.": "if the stock has fallen outside the top {} in the screener.",
    "om en position är större än {} gånger en jämn fördelning (minst {} %).": "if a position is larger than {} times an even allocation (at least {} %).",
    "Sälj vid månadsgenomgång om": "Sell at the monthly review if",
    "stoppen nådd ({} % från högsta sedan köp)": "stop reached ({} % from the high since purchase)",
    "stoppen nådd ({}% från högsta sedan köp)": "stop reached ({} % from the high since purchase)",
    "kursen under MA{}": "price below MA{}", "MA{} under MA{}": "MA{} below MA{}", "under MA{}": "below MA{}",
    "MA{}under MA{}": "MA{} below MA{}", "MA{}över MA{}": "MA{} above MA{}", "Över MA{}": "Above MA{}",
    "bara {} % över MA{}": "only {} % above MA{}", "{} % (MA{})": "{} % (MA{})", "MA{} · MA{}": "MA{} · MA{}",
    "stopp {}, högsta sedan köp {}, {} % kvar": "stop {}, high since purchase {}, {} % left",
    "{} % ovanför stoppen": "{} % above the stop", "{} % av momentumdelen (gräns {} %)": "{} % of the momentum part (limit {} %)",
    "{} % av momentumdelen (gräns {}%)": "{} % of the momentum part (limit {} %)",
    "trimning: {} % av portföljen": "trimming: {} % of the portfolio",
    "daglig kontroll: {}": "daily check: {}",
    "månadsgenomgång: {}": "monthly review: {}",
    "Sålde {} {} – {} ({} kr).": "Sold {} {} – {} ({} SEK).",
    "Från {} · {} dagar sparade sedan {} ·": "From {} · {} days saved since {} ·",
    "Importerade {} köp som du fortfarande äger och {} avslutade affärer.": "Imported {} purchases you still own and {} closed trades.",
    "dina nuvarande {} innehav och {} avslutade affärer. En säkerhetskopia sparas först. Dina val av ticker och typ sparas till nästa import.":
        "your current {} holdings and {} closed trades. A backup is saved first. Your choices of ticker and type are saved for the next import.",
    "{} köp och försäljningar i {} värdepapper, från {} till {}. Hoppar över: {} ({}){}.":
        "{} purchases and sales in {} securities, from {} to {}. Skipping: {} ({}){}.",
    "Inget sparas förrän du har granskat och bekräftat. {} sparade kopplingar ISIN → ticker återanvänds.":
        "Nothing is saved until you have reviewed and confirmed. {} saved ISIN → ticker mappings are reused.",
    "Kunde inte läsa filen: {}": "Could not read the file: {}",

    # --- Utveckling ---
    "{} % sedan start": "{} % since start", "index {} %": "index {} %", "{} % med vinst": "{} % profitable",
    "{} med vinst": "{} profitable", "{} av {}": "{} of {}", "{} dagar": "{} days", "{} filer": "{} files",
    "{} rapporter": "{} reports", "{} mån (demo)": "{} mo (demo)", "{} mån": "{} mo", "{} mån exkl. senaste": "{} mo excl. latest",
    "kassa {} kr": "cash {} SEK", "kassa {}": "cash {}",
    "Inga avslutade affärer ännu för \"{}\". Använd": "No closed trades yet for \"{}\". Use",
    "Ingen utveckling att visa ännu för \"{}\". Lägg till innehav med köpdatum och köpkurs.":
        "No performance to show yet for \"{}\". Add holdings with buy date and buy price.",
    "Alla {} affärer": "All {} trades",
    "Indexdata saknas ({}). Klicka \"Uppdatera kurser\" under Mina innehav, eller ändra JAMFORELSEINDEX i innehav.py till ^OMX.":
        "Index data is missing ({}). Click \"Update prices\" under My holdings, or change JAMFORELSEINDEX in innehav.py to ^OMX.",

    # --- Strategier ---
    "{} har inte startat än.": "{} has not started yet.",
    "Insider {}": "Insider {}", "Insider {} mån": "Insider {} mo", "Momentum v{}": "Momentum v{}",
    "B: utan dyraste {} %": "B: without the most expensive {} %", "C: {}/{} momentum/värde": "C: {}/{} momentum/value",
    "Startad {} · senaste beslut {} .": "Started {} · last decision {}.",
    "Startad {} · senaste beslut {}.": "Started {} · last decision {}.",
    "Startad {} · senaste beslut {}": "Started {} · last decision {}",
    "Startad {}": "Started {}", "senaste beslut {} .": "last decision {}.", "senaste beslut {}": "last decision {}",
    "nyckeltal från {}.": "key figures from {}.", "nyckeltal från {}": "key figures from {}",
    "Stopp och trendregler kontrolleras dessutom varje vardag – senast {}.": "Stop and trend rules are also checked every weekday – latest {}.",
    "Stopp och trendregler kontrolleras dessutom varje vardag": "Stop and trend rules are also checked every weekday",
    "senast {}.": "latest {}.",
    "Låtsasportföljer som följer varje strategi exakt, utan riktiga pengar. Varje lördag efter veckorapporten säljer de det reglerna säger sälj och köper gröna rader (köpläge) tills de är fulla ({} aktier om inget annat anges). Start {} kr per strategi, lika vikt, courtage {} % (minst {} kr) och {} % spread på varje affär.":
        "Paper portfolios that follow each strategy exactly, without real money. Every Saturday after the weekly report they sell what the rules say to sell and buy green rows (buy zone) until full ({} stocks unless stated otherwise). Start {} SEK per strategy, equal weight, brokerage {} % (at least {} SEK) and {} % spread on every trade.",
    "Avkastningen räknas från varje strategis startdag och index från den tidigaste starten. Strategierna får utdelningar (räknas som återinvesterade, samma kurser som Utveckling-sidan), medan {} är ett prisindex utan utdelningar – index ser därför runt 3 procentenheter per år sämre ut än det är. Jämförelsen mellan strategierna är helt rättvis. Det krävs flera månader innan skillnaderna betyder något.":
        "Returns are counted from each strategy's start date and the index from the earliest start. The strategies receive dividends (counted as reinvested, same prices as the Performance page), while {} is a price index without dividends – the index therefore looks around 3 percentage points per year worse than it is. The comparison between the strategies is completely fair. It takes several months before the differences mean anything.",
    "Backtest – vad som hade hänt om boten startat {}. Inte riktig pappershandel.":
        "Backtest – what would have happened if the bot had started {}. Not real paper trading.",
    "← Backtest {} – {}": "← Backtest {} – {}",
    "{} – {} · {} veckor · {} aktier · kördes {}": "{} – {} · {} weeks · {} stocks · run {}",
    "{} kräver historiska nyckeltal och kan inte testas bakåt.": "{} require historical key figures and cannot be backtested.",
    "av {} startade": "of {} started",
    "insiderköp netto {} MSEK ({} köpare, {} % av börsvärdet), rapport {} om {} dagar":
        "insider net buying {} MSEK ({} buyers, {} % of market cap), report {} in {} days",
    "insiderköp netto {} MSEK ({} köpare), rapport {} om {} dagar": "insider net buying {} MSEK ({} buyers), report {} in {} days",
    "insiderköp netto {} MSEK ({} köpare, {} dagars omsättning), rapport {} om {} dagar":
        "insider net buying {} MSEK ({} buyers, {} days of turnover), report {} in {} days",
    "{} dagar efter rapporten {}": "{} days after the report {}",
    "{} dagar efter rapporten {} (inkl. utdelning {} kr)": "{} days after the report {} (incl. dividend {} SEK)",
    "ingen rapport hittad – {} dagar efter köpet": "no report found – {} days after purchase",
    "säljs vid veckokörningen efter {} ({} d efter rapporten {})": "to be sold at the weekly run after {} ({} d after the report {})",
    "säljs vid veckokörningen efter {} (inget rapportdatum hittat)": "to be sold at the weekly run after {} (no report date found)",
    "{} (inkl. utdelning {} kr)": "{} (incl. dividend {} SEK)",

    # --- Aktiesidan och sök ---
    "Inga nyckeltal för {} ännu.": "No key figures for {} yet.",
    "Hittade ingen aktie som matchar \"{}\".": "Found no stock matching \"{}\".",
    "Något gick fel vid analysen av \"{}\": {}: {}.": "Something went wrong analysing \"{}\": {}: {}.",
    "Hämtar färska kurser och kör samma köpregler som screenern. Platsen räknas mot senaste veckorapporten ({}).":
        "Fetches fresh prices and runs the same buy rules as the screener. The rank is counted against the latest weekly report ({}).",
    "har bara {} handelsdagar. Momentum kräver minst ett års historik": "has only {} trading days. Momentum requires at least a year of history",
    "({} dagar), så den kan inte bedömas ännu.": "({} days), so it cannot be assessed yet.",
    "{} (om {} dagar)": "{} (in {} days)", "{} (idag)": "{} (today)", "{} (imorgon)": "{} (tomorrow)",
    "om {} dagar": "in {} days",
    "Senaste dagen {} MSEK, {}× snittet för 20 dagar ({} MSEK). {} dagar med topp senaste månaden.":
        "Latest day {} MSEK, {}× the 20-day average ({} MSEK). {} days with a spike in the last month.",
    "Senaste dagen {} MSEK, {}× snittet för 20 dagar ({} MSEK). {} dag med topp senaste månaden.":
        "Latest day {} MSEK, {}× the 20-day average ({} MSEK). {} day with a spike in the last month.",
    "Jämförelsegrupp: {} ({} bolag). {}": "Comparison group: {} ({} companies). {}",
    "billigare än {} % av {}": "cheaper than {} % of {}", "lönsammare än {} % av {}": "more profitable than {} % of {}",
    "Prognosändring sedan {}": "Forecast change since {}", "riktkurs {} ({})": "target price {} ({})",
    "{} (omräknat)": "{} (converted)", "Anmält i {}, men priset stämmer med kursen i kronor": "Reported in {}, but the price matches the price in SEK",
    "Kurs {} {} · {} ·": "Price {} {} · {} ·", "rapport · {}": "report · {}", "rapport {}": "report {}",
    "{} +{} %": "{} +{} %",

    # --- Status ---
    "inga beslut sedan {} – kontrollera logg.txt": "no decisions since {} – check logg.txt",
    "ingen daglig kontroll sedan {} – kontrollera cron och strategier_logg.txt": "no daily check since {} – check cron and strategier_logg.txt",
    "ingen ny rapport sedan {} – kontrollera cron och logg.txt": "no new report since {} – check cron and logg.txt",
    "inget nytt sedan {} – kontrollera cron och {}": "nothing new since {} – check cron and {}",
    "inget sparat sedan {} – kontrollera cron och nyckeltal_logg.txt": "nothing saved since {} – check cron and nyckeltal_logg.txt",
    "Nyckeltal samlas varje vardag kl. {} för alla aktier i tickerlistan och dina innehav (":
        "Key figures are collected every weekday at {} for all stocks in the ticker list and your holdings (",
    "). Senaste insamlingen: {}.": "). Latest collection: {}.",
    "). Ingen insamling har körts än.": "). No collection has run yet.",
    "{} slumpvisa bland godkända, samma säljregler": "{} random among approved, same sell rules",
    "Median {}": "Median {}", "billigare än {}%": "cheaper than {} %", "bättre än {}%": "better than {} %",
    "billigare än {} %": "cheaper than {} %", "bättre än {} %": "better than {} %", "Momentum {}": "Momentum {}",
    "{} signaler, {} godkända": "{} signals, {} approved",
    "godkända {} mot underkända {}": "approved {} vs. rejected {}", "rätt bedömt {}": "judged right {}",
    "modellen {} % (gräns {} %)": "model {} % (threshold {} %)",
    "för: {}": "for: {}", "emot: {}": "against: {}", "{} ({} pe)": "{} ({} pp)",
    "stort köp {}": "large purchase {}", "köpbelopp {}": "purchase amount {}", "antal köpare {}": "number of buyers {}",
    "antal köpaffärer {}": "number of purchases {}", "insiders säljer också {}": "insiders also selling {}",
    "vd köpte {}": "CEO bought {}", "ekonomichef köpte {}": "CFO bought {}", "styrelseledamot köpte {}": "board member bought {}",
    "bara närstående köpte {}": "only closely associated bought {}", "dagar till rapport {}": "days to report {}",
    "aktien 1 mån {}": "stock 1 mo {}", "aktien 3 mån {}": "stock 3 mo {}", "aktien 12 mån {}": "stock 12 mo {}",
    "volatilitet {}": "volatility {}", "avstånd till MA200 {}": "distance to MA200 {}", "omsättning {}": "turnover {}",
    "förra rapportens reaktion {}": "last report's reaction {}", "förra EPS-överraskningen {}": "last EPS surprise {}",
    "marknaden 3 mån {}": "market 3 mo {}", "marknaden 12 mån {}": "market 12 mo {}", "marknadens oro {}": "market stress {}",
    "{} dagars oms.": "{} days of turnover", "{} MSEK/dag": "{} MSEK/day",
    "Modellen (insiderml.py) är under prövning: den klarade 3 av 4 krav i backtestet och påverkar inga köp.":
        "The model (insiderml.py) is on trial: it passed 3 of 4 requirements in the backtest and does not affect any purchases.",
    "Tränad {} på {} signaler.": "Trained {} on {} signals.",
    "{} % ✓": "{} % ✓", "{} % ✗": "{} % ✗",
    "före rapporten {}: upp {} % sedan köpet": "before the report {}: up {} % since purchase",
    "{} sparade": "{} saved", "{} bolag": "{} companies", "{} st": "{} shares",
}
