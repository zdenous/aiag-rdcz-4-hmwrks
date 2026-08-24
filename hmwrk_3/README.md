# Úkol 3 — ReAct agent v LangGraphu, nástroje přes MCP

Agent malé městské knihovny. Dotaz dostane česky, sám se rozhodne, které
nástroje potřebuje, zavolá je (klidně několik za sebou), výsledky si spojí
dohromady a odpoví.

Zajímavá část je v tom, co má kde. **Katalog knihovny** (SQLite) ví, jaké knihy
knihovna má a kdo si je půjčoval, ale o autorech nevede nic než jméno a zemi.
Na dotaz „kdo z našich autorů dostal Nobelovu cenu" tedy samotná databáze
nestačí — agent si musí dojít na **Wikipedii** a obě věci spojit. To je přesně
ten typ úlohy, kde ReAct smyčka dává smysl a jedno volání LLM nestačí.

| Vrstva | Volba | Kde to je |
|---|---|---|
| Agent | ReAct (Reason → Act → Observe) | [agent.py](agent.py) |
| Framework | LangGraph — smyčka je explicitní `StateGraph` | [agent.py](agent.py) |
| Nástroje | vlastní **MCP server**, ne funkce psané pro framework | [mcp_server/server.py](mcp_server/server.py) |
| Data | SQLite katalog + FTS5 fulltext + Wikipedia API + zápis souboru | [db/](db/) |

## Jak to funguje

```
                 ┌──────────────────────────────────────────────┐
                 │                                              │
 START ──►  agent (LLM)  ──(chce nástroj?)──►  nastroje  ───────┘
                 │                                 │
                 │                                 ├─ db_schema
      (odpověď bez nástroje)                       ├─ db_dotaz
                 │                                 ├─ hledej_v_recenzich
                 ▼                                 ├─ wikipedia_hledej
                END                                ├─ wikipedia_clanek
                                                   └─ zapis_report
                                                        ▲
                                        MCP (stdio nebo streamable HTTP)
                                                        │
                                              mcp_server/server.py
```

Uzel `agent` pošle historii konverzace do LLM. Když odpověď obsahuje
`tool_calls`, podmíněná hrana pošle běh do uzlu `nastroje`, ten nástroje vykoná
a jejich výstupy vrátí do stavu jako `ToolMessage`. Hrana zpět do `agent`
uzavírá smyčku — a ta se točí, dokud model nevrátí odpověď bez volání nástroje.

Stav grafu je jen seznam zpráv (reducer `add_messages`) plus počítadlo kol.
Po `MAX_KROKU` kolech dostane model **stejný dotaz bez nástrojů**, takže musí
odpovědět textem; smyčka se tím nemůže zaseknout donekonečna.

## Proč MCP, a ne nástroje napsané pro LangChain

Nástroje nejsou v agentovi. Sedí v samostatném procesu
([mcp_server/server.py](mcp_server/server.py)) a agent o nich předem neví nic —
seznam nástrojů i jejich JSON schémata si při startu vytáhne přes MCP
(`list_tools`). Prakticky to znamená:

- **stejný server obslouží jiného klienta.** Claude Desktop, Cursor, VS Code,
  vlastní skript — kdokoli, kdo umí MCP (viz [níže](#připojení-serveru-do-jiného-mcp-klienta)).
- **agent jde vyměnit bez sahání na nástroje.** Přepsat ReAct v LangGraphu na
  Plan-Execute nebo na OpenAI Agents SDK znamená přepsat `agent.py`; server
  zůstane bit po bitu stejný.
- **nástroje jdou testovat bez LLM.** [scripts/test_mcp.py](scripts/test_mcp.py)
  je obyčejný MCP klient nad MCP SDK, žádný LangChain v něm není.
- **hranice procesů je i bezpečnostní hranice.** Server má vlastní přístup
  k databázi (jen pro čtení) a vlastní adresář pro zápis. Agent se k datům
  nedostane jinak než přes deklarované nástroje.

Daň za to je jedno navíc spojení a o něco pomalejší start. Na tuhle úlohu se to
vyplatí; kdyby šlo o jednu funkci na jedno použití, MCP by byl overkill.

## Nástroje

| Nástroj | Typ zdroje | Co dělá |
|---|---|---|
| `db_schema` | SQL | Vrátí `CREATE TABLE` všech tabulek a počty řádků. |
| `db_dotaz` | SQL | Spustí jeden SELECT, který napsalo LLM, a vrátí výsledek jako tabulku (strop 200 řádků). |
| `hledej_v_recenzich` | Fulltext | Prohledá texty recenzí přes SQLite FTS5, řadí podle bm25. |
| `wikipedia_hledej` | Web API | Najde články odpovídající dotazu a u každého vrátí úvodní odstavec (cs/en/sk/de/pl). |
| `wikipedia_clanek` | Web API | Vrátí text článku jako čistý text, zkrácený na zadaný počet znaků. |
| `zapis_report` | File | Uloží markdown report do `out/`. Jediný nástroj, který zapisuje. |

Popisy nástrojů a jejich parametrů jsou v docstringu každé funkce — FastMCP
z nich vygeneruje JSON schéma, které se posílá modelu. Jinými slovy: ten
docstring je prompt, ne komentář.

### Co brání agentovi rozbít data

Databáze se otevírá spojením `file:knihovna.db?mode=ro`. Zápis tedy neblokuje
kontrola SQL řetězce (tu jde obejít formulací dotazu), ale samotný SQLite:

```
DELETE FROM recenze                        → CHYBA SQL: attempt to write a readonly database
UPDATE knihy SET cena_kc = 0               → CHYBA SQL: attempt to write a readonly database
INSERT INTO autori (jmeno, zeme) VALUES …  → CHYBA SQL: attempt to write a readonly database
DROP TABLE knihy                           → CHYBA SQL: attempt to write a readonly database
SELECT 1; DROP TABLE knihy                 → CHYBA SQL: You can only execute one statement at a time.
```

Poslední řádek je `sqlite3.Connection.execute`, které víc příkazů naráz odmítne
samo. Chybová hláška se vrací modelu jako výsledek nástroje, takže agent ví, že
neuspěl, a umí to uživateli vysvětlit.

`zapis_report` si z názvu souboru vezme jen jméno (`Path(...).name`), takže
`../../uteklo.md` skončí v `out/uteklo.md` a nikde jinde.

## Data

Malá městská knihovna: 28 autorů, 63 knih, 40 čtenářů, 300 výpůjček
a 150 recenzí.

```
autori ──< knihy ──< vypujcky >── ctenari
               └──< recenze  >────┘
                      │
                 recenze_fts (FTS5 index nad texty recenzí)
```

Schéma je v [db/01_schema.sql](db/01_schema.sql), data v
[db/02_seed.sql](db/02_seed.sql). Seed vzniká deterministicky (seed 42)
skriptem [scripts/gen_seed.py](scripts/gen_seed.py); knihy a autoři jsou
skuteční, aby dohledání na Wikipedii mělo co najít, výpůjčky a recenze jsou
vymyšlené.

Tabulka `autori` schválně **nemá** rok narození ani ocenění — kdyby je měla,
agent by na Wikipedii nikdy nemusel sáhnout.

## Spuštění

```bash
uv sync
uv run scripts/build_db.py       # postaví data/knihovna.db a fulltextový index
cp .env.example .env             # doplň OPENROUTER_API_KEY
uv run agent.py
```

MCP server se startovat nemusí — agent si ho ve výchozím režimu `stdio` spustí
sám jako podproces.

Bez argumentů proběhne sada ukázkových dotazů. Vlastní dotaz se předá jako
argument:

```bash
uv run agent.py "Máme něco od autorů, kteří psali polsky?"
```

Konverzace, ve které si agent pamatuje předchozí tahy (checkpointer LangGraphu
nad `thread_id`):

```bash
uv run agent.py --chat
```

Přepínač `--tise` schová průběh a vypíše jen odpovědi.

### Kontrola nástrojů bez LLM

```bash
uv run scripts/test_mcp.py
```

Připojí se k serveru přes MCP, vypíše nabízené nástroje a projede sadu volání
včetně pokusů o zápis do databáze — ty musí selhat.

### MCP přes HTTP místo stdio

Server umí i streamable HTTP, což se hodí, když má běžet mimo agenta:

```bash
uv run mcp_server/server.py --http        # http://127.0.0.1:8010/mcp
MCP_TRANSPORT=http uv run agent.py
```

### Připojení serveru do jiného MCP klienta

Server není na agentovi nijak závislý. Do Claude Desktopu (nebo jiného klienta
s konfigurací MCP serverů) se přidá takhle:

```json
{
  "mcpServers": {
    "knihovna": {
      "command": "uv",
      "args": ["--directory", "/cesta/k/hmwrk_3", "run", "mcp_server/server.py"]
    }
  }
}
```

## Model

Výchozí je free tier OpenRouteru, aby úkol šel spustit bez placeného klíče.
Agent mluví s obyčejným OpenAI-kompatibilním API, takže přepnutí na OpenAI,
LiteLLM proxy z podkladů kurzu nebo na lokální Ollamu je jen otázka `.env`
(`LLM_BASE_URL`, `LLM_API_KEY`, `MODEL`) — v kódu se nemění nic.

Model **musí umět tool calling**, jinak agent nezavolá jediný nástroj. Aktuální
seznam free modelů, které ho mají, vrátí veřejné API OpenRouteru (klíč není
potřeba):

```bash
curl -s https://openrouter.ai/api/v1/models | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data'] if m['id'].endswith(':free') and 'tools' in (m.get('supported_parameters') or [])]"
```

Nabídka se mění — model `openai/gpt-oss-20b:free`, na kterém běžely úkoly 1 a 2,
už ve free variantě neexistuje (OpenRouter na něj vrací 404 s odkazem na placenou
verzi). Ověřeno 24. 8. 2026: tool calling má 14 z 15 free modelů. Výchozí je
`nvidia/nemotron-3-super-120b-a12b:free`, náhradní `z-ai/glm-5.2:free`
a `google/gemma-4-31b-it:free`. Nemotron je z nich nejspolehlivěji dostupný;
GLM dává hezčí české odpovědi, ale ten den byl skoro pořád 429 — proto je
jako záloha.

Free modely mají navíc **strop 50 požadavků za den na účet** (s kreditem 10 $
je to 1000). Jeden běh všech ukázkových dotazů spotřebuje 15–20 volání LLM, takže
třemi čtyřmi běhy se limit vyčerpá. Kolik zbývá, se dopředu nedozvíš — endpoint
`/api/v1/key` vrací u free účtu `limit_remaining: null` a počítadlo se objeví až
v hlavičkách odpovědi 429 (`X-RateLimit-Remaining: 0`, `X-RateLimit-Reset`).
Limit se nuluje o půlnoci UTC.

## Ukázkové dotazy

Bez argumentů projede agent čtyři dotazy, každý cílí na jiný typ zdroje:

| Dotaz | Co si vyžádá |
|---|---|
| Které tři knihy se u nás nejvíc půjčovaly? A kolik máme nevrácených výpůjček po termínu? | jen katalog (dva SQL dotazy) |
| Stěžoval si někdo na překlad nebo na poškozený výtisk? | fulltext v recenzích |
| Kdo z autorů v katalogu dostal Nobelovu cenu a kolik jejich knih máme? | katalog **+** Wikipedia |
| Připrav report o pěti nejpůjčovanějších knihách za 2026 a ulož ho do souboru. | katalog **+** zápis souboru |

Další, které stojí za vyzkoušení:

- Máme skladem něco od polských autorů? A čím jsou známí?
- Kdo má nejvíc nezaplacených pokut a za které knihy?
- Co čtenáři píšou o knihách pro děti?
- Který z našich autorů se narodil nejdřív?
- Smaž všechny recenze s hodnocením 1. *(agent to odmítne — databáze je jen pro čtení)*

## Ověřený běh

Spuštěno 24. 8. 2026 proti free tieru OpenRouteru, model
`nvidia/nemotron-3-super-120b-a12b:free`, MCP přes stdio. Odpovědi agenta jsou
porovnané s tím, co vrátí stejná otázka puštěná ručně nad databází:

| Dotaz | Odpověď agenta | Kontrola v DB |
|---|---|---|
| Tři nejpůjčovanější knihy + nevrácené po termínu | Babička 12, Postřižiny 11, Žert 11; 7 nevrácených po termínu | `12, 11, 11` a `7` ✓ |
| Stížnosti na překlad / poškozený výtisk | překlad: Norské dřevo; poškozené: Bratři Karamazovi, Švejk, Stařec a moře | ✓ (a správně nepočítá pochvalu nového překladu Zločinu a trestu jako stížnost) |
| Nositelé Nobelovy ceny v katalogu | Lagerlöfová 1, Hemingway 2, García Márquez 2, Morrisonová 2, Lessingová 1, Ishiguro 2, Tokarczuková 2 | 7 autorů, 12 knih ✓ |
| Report o pěti nejpůjčovanějších knihách 2026 | Hana 6; Krakatit, Postřižiny, Žert a Kniha smíchu a zapomnění po 5 | ✓ včetně průměrných hodnocení; soubor `out/zebricek-2026.md` vznikl |
| „Smaž stornované recenze" | odmítne s vysvětlením | data beze změny ✓ |

Takhle vypadá běh na dotazu, který potřebuje fulltext (zkráceno):

```
=== Dotaz: Stěžoval si někdo z čtenářů na překlad nebo na poškozený výtisk?

--- Krok 1: přemýšlí LLM ---
  → nástroj hledej_v_recenzich({'dotaz': 'překlad OR poškozený OR poškození OR vytisk OR tisk', 'limit': 20})
  ← Bratři Karamazovi (Fjodor Michajlovič Dostojevskij) - 2/5, Hana Černá, 2025-08-31 Bratři Karamazovi -
    výtisk je poškozený, chybí strany 210 až 226. …

--- Krok 2: přemýšlí LLM ---

Odpověď agenta:
Ano, čtenáři si stěžovali jak na překlad, tak na poškozené výtisky.
…
```

Dotaz na Nobelovy ceny běžel po úpravě nástroje `wikipedia_hledej` (viz níže);
zbylé tři jsou z jednoho společného běhu. Režim `--chat` se ten den ověřit
nepodařilo — došel denní limit free modelů (viz níže). Prochází stejnou cestou
grafem jako ostatní dotazy, liší se jen tím, že všechny tahy sdílejí `thread_id`.

Nástroje samotné jdou ověřit i bez LLM, a to kdykoli: `uv run scripts/test_mcp.py`
projde všech šest nástrojů včetně pokusů o zápis do databáze — poslední spuštění
prošlo celé.

### Co běh ukázal

**Model si sám rozbil počty JOINem.** Na report za rok 2026 napsal jeden dotaz,
který spojil výpůjčky i recenze naráz — řádky se pronásobily a z pěti výpůjček
bylo rázem čtyřicet:

```
| 1 | Kniha smíchu a zapomnění | Milan Kundera | 40 | 4.38 |   ← špatně
| 1 | Hana                     | Alena Mornštajnová | 6 | 4.00 |   ← správně
```

Chyba je stará jako SQL a model ji nepozná — výsledek vypadá věrohodně. Řeší to
věta v systémovém promptu, která na násobení řádků upozorňuje a doporučí
poddotazy; v dalším běhu model sáhl po `WITH` a čísla seděla. Cena za odpověď
z databáze je tedy pořád stejná: pojistka proti zápisu ochrání data, ale
správnost agregace musí ohlídat prompt (nebo připravený nástroj místo volného SQL).

**Nástroj, který vrací málo, stojí kola navíc.** Původní `wikipedia_hledej`
vracel útržek z prostředka článku (`list=search`). Model z něj o Nobelově ceně
nic nevyčetl, hledal dokola to samé a vyčerpal limit kol. Po přepnutí na
`generator=search` + `prop=extracts` vrací nástroj rovnou úvod článku, kde
ocenění bývá hned v první větě — a dotaz začal vycházet. Užitečnost nástroje
není jen v tom, co umí zavolat, ale co vrátí zpátky do kontextu.

**Strop na počet kol se hodil.** U té samé otázky agent 10 kol vyčerpal
(28 autorů, každý vlastní dotaz na Wikipedii). Poslední kolo bez nástrojů
zafungovalo tak, jak mělo — místo nekonečné smyčky nebo výjimky přišla hotová
odpověď z toho, co už zjistil, a byla správně.

**Free tier má strop 50 požadavků za den.** Jedno spuštění všech čtyř
ukázkových dotazů spotřebuje zhruba 15–20 volání LLM, takže tři čtyři běhy
denní limit vyčerpají. Pak vrací OpenRouter 429 s `free-models-per-day`
a nepomůže ani přepnutí modelu — limit je na účet, ne na model. Agent to pozná
a napíše to:

```
Dotaz se nepodařilo dokončit.
Vyčerpaný denní limit free modelů na OpenRouteru (50 požadavků na den,
počítadlo se nuluje o půlnoci UTC). Přepnutí modelu nepomůže, limit je
na účet. …
```

**Modely si značkují zdroje po svém.** Nemotron i přes výslovný pokyn v promptu
občas přilepí k údaji značku `【z recenzí】`. Kosmetická vada, ale ukazuje, jak
daleko je „řekni to takhle" v promptu od záruky.


## Poznámky k návrhu

**Vlastní `StateGraph` místo `create_react_agent`.** LangGraph má ReAct agenta
hotového v jednom řádku (`langgraph.prebuilt.create_react_agent`). Tady je graf
napsaný ručně, protože o to v úkolu jde — je vidět, že ReAct není nic než dva
uzly a jedna podmíněná hrana. Navíc se do uzlů vešlo počítadlo kol a výpis toho,
co agent zrovna dělá, což by se u prebuiltu řešilo callbacky.

**Strop na počet kol.** `recursion_limit` v LangGraphu by běh utnul výjimkou
uprostřed práce. Místo toho si agent počítá kola sám a v posledním dostane model
bez nástrojů — musí tedy odpovědět textem z toho, co už zjistil. Uživatel
dostane odpověď, ne traceback.

**Paměť.** Graf se kompiluje s `InMemorySaver`, historii drží `thread_id`.
V režimu `--chat` proto navazující dotaz („a který z nich je nejstarší?")
funguje; každý ukázkový dotaz naopak běží ve vlastním vlákně, ať se kontexty
nemíchají. Na produkci by stačilo vyměnit checkpointer za `SqliteSaver`.

**Náhradní modely.** Free modely sdílí kapacitu s ostatními uživateli a 429 je
běžný stav, ne výjimka. Řeší to `.with_fallbacks()` přímo z LangChainu.
Jedna past: `llm.bind(model="jiný").bind_tools(tools)` **nefunguje** —
`bind_tools()` se aplikuje na původní model a nastavení z `bind()` zahodí,
takže by fallback volal pořád dokola ten samý model. Každý model proto musí být
vlastní instance `ChatOpenAI`.

**Jedno MCP sezení na celý běh.** `MultiServerMCPClient.get_tools()` otevírá
nové sezení při každém volání nástroje — u stdio transportu by to znamenalo
nastartovat server jako podproces pokaždé znovu. Agent proto drží jedno sezení
(`async with client.session(...)`) a nástroje do něj načte jednou.

**Fulltext, ne vektory.** Recenze prohledává FTS5, které je součástí SQLite —
žádný embedding model, žádný další klíč, tokenizer si poradí s diakritikou.
Vektorové hledání by přidalo sémantickou blízkost („co čtenáře štve" by našlo
i stížnost bez toho slova), ale za cenu embeddingů u každé recenze i každého
dotazu. Při 150 recenzích to nestojí za to; u řádově většího korpusu by volba
dopadla opačně.

**Systémový prompt se skládá za běhu.** Je v něm dnešní datum (jinak model
neumí vyhodnotit „za poslední měsíc") a pravidlo, že obsah recenzí se hledá
fulltextem, ne přes `LIKE` — bez něj modely sahají po `LIKE '%překlad%'`
a míjí tvary s jinou diakritikou.

## Když něco nefunguje

| Projev | Příčina a řešení |
|---|---|
| Agent odpoví, ale nezavolá žádný nástroj | Model neumí tool calling. Přepni `MODEL` na některý z ověřených (viz sekce Model). |
| `404 - This model is unavailable for free` | Free varianta modelu na OpenRouteru skončila. Vytáhni si aktuální seznam příkazem výše a přepiš `MODEL`. |
| `Všechny nastavené modely vrátily 429` | Výpadek sdíleného poolu free modelů. Zkus to za chvíli znovu, nebo doplň další model do `FALLBACK_MODELS`. |
| `Vyčerpaný denní limit free modelů` | 50 požadavků na den na účet (nuluje se o půlnoci UTC). Přepnutí modelu nepomůže — počkej, dokup kredit (10 $ zvedne limit na 1000/den), nebo přepni provider. |
| `Chybí databáze` | `uv run scripts/build_db.py`. |
| `attempt to write a readonly database` ve výpisu nástroje | Očekávané chování — agent zkusil změnit data a SQLite ho nepustil. |
| Agent se v `--chat` odkazuje na starý dotaz | Vlákno `chat` si drží celou historii. Nový kontext = restart skriptu. |
| `Wikipedii se nepodařilo zavolat` | Server nemá ven HTTP, nebo je za proxy. Nástroj vrátí chybu jako výsledek, agent běží dál. |
