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
Po `MAX_KROKU` kolech dostane model **stejný dotaz bez nástrojů**, takže má
odpovědět textem z toho, co už zjistil — a hrana pak vede na `END`, ať model
vrátí cokoli. Smyčka se tím nemůže zaseknout donekonečna.

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
cp .env.example .env             # doplň GEMINI_API_KEY
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

Výchozí je **Gemini free tier** (Google AI Studio), aby úkol šel spustit bez
placeného klíče. Model se zapisuje s prefixem poskytovatele:

| Zápis v `MODEL` | Klient | Klíč |
|---|---|---|
| `google/gemini-3.7-flash` (výchozí) | `ChatGoogleGenerativeAI` (nativní Gemini API) | `GEMINI_API_KEY` |
| `openrouter/z-ai/glm-5.2:free` | `ChatOpenAI` na `openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| cokoli bez známého prefixu | `ChatOpenAI` na `LLM_BASE_URL` | `LLM_API_KEY` |

Poslední řádek pokrývá OpenAI, LiteLLM proxy z podkladů kurzu i lokální Ollamu —
všechno jsou OpenAI-kompatibilní API, takže se mění jen `.env`, v kódu nic.

**Náhradní modely můžou být od jiného poskytovatele než ten hlavní.** Výchozí
řetězec je `gemini-3.7-flash` → `gemini-3.6-flash` → dva free modely
z OpenRouteru; každý model si nese vlastní klienta i klíč. Model, ke kterému
chybí klíč, agent při startu přeskočí a napíše to — takže bez `GEMINI_API_KEY`
běží rovnou na OpenRouteru.

### Proč u Gemini nativní klient, a ne OpenAI-kompatibilní endpoint

Gemini má OpenAI-kompatibilní endpoint
(`generativelanguage.googleapis.com/v1beta/openai/`) a přes `ChatOpenAI` na něj
jde poslat první dotaz i s nástroji. Druhý tah ale skončí:

```
400 Function call is missing a thought_signature in functionCall parts.
    This is required for tools to work correctly …
```

Modely řady Gemini 3 vracejí u každého volání nástroje **thought signature** —
zašifrovaný otisk svého uvažování — a při dalším tahu ji musí klient poslat
zpátky. Kompatibilní vrstva ji do OpenAI schématu nemá kam dát, `ChatOpenAI` ji
zahodí (v `additional_kwargs` zůstane jen `refusal`) a agent umře hned po prvním
nástroji. Není to chyba v tomhle projektu — naráží na to Codex, OpenAI Agents SDK
i další klienti přes kompatibilní vrstvu.

Nativní `langchain-google-genai` signatury přenáší sám
(`__gemini_function_call_thought_signatures__` v `additional_kwargs`), takže
ReAct smyčka nad Gemini 3 funguje. Cena je jedna závislost navíc a jedna větev
v `chat()`; zbytek agenta ani nástrojů se to netýká.

Ústup na starší Gemini bez tohohle chování nefunguje — `gemini-2.5-flash` už
Google novým klíčům nedává:

```
404 This model models/gemini-2.5-flash is no longer available to new users.
    Please update your code to use models/gemini-3.6-flash …
```

Modely, na které tvůj klíč dosáhne:

```bash
curl -s "https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_API_KEY" | grep '"name"'
```

Kvóty free tieru (na minutu i na den) se liší podle modelu a jsou vidět
v [AI Studiu](https://aistudio.google.com/). Když 3.7 nestačí, menší varianty
jsou `google/gemini-3.6-flash` a `google/gemini-3.5-flash-lite`.

### OpenRouter jako záloha

Free model na OpenRouteru **musí umět tool calling**, jinak agent nezavolá
jediný nástroj. Aktuální seznam vrátí veřejné API (klíč není potřeba):

```bash
curl -s https://openrouter.ai/api/v1/models | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data'] if m['id'].endswith(':free') and 'tools' in (m.get('supported_parameters') or [])]"
```

Nabídka se mění — model `openai/gpt-oss-20b:free`, na kterém běžely úkoly 1 a 2,
už ve free variantě neexistuje (OpenRouter na něj vrací 404 s odkazem na placenou
verzi). Ověřeno 24. 8. 2026: tool calling má 14 z 15 free modelů. Jako záloha
jsou nastavené `nvidia/nemotron-3-super-120b-a12b:free` (nejspolehlivěji
dostupný) a `z-ai/glm-5.2:free` (hezčí české odpovědi, ale ten den skoro pořád
429).

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

Spuštěno 24. 8. 2026, MCP přes stdio. Odpovědi agenta jsou porovnané s tím, co
vrátí stejná otázka puštěná ručně nad databází:

| Dotaz | Odpověď agenta | Kontrola v DB | Model |
|---|---|---|---|
| Tři nejpůjčovanější knihy + nevrácené po termínu | Babička 12, Postřižiny 11, Žert 11; 7 nevrácených po termínu | `12, 11, 11` a `7` ✓ | `gemini-3.7-flash` |
| Stížnosti na překlad / poškozený výtisk | překlad: Norské dřevo; poškozené: Bratři Karamazovi, Švejk, Stařec a moře | ✓ (a správně nepočítá pochvalu nového překladu Zločinu a trestu jako stížnost) | `gemini-3.7-flash` |
| Nositelé Nobelovy ceny v katalogu | Lagerlöfová 1, Hemingway 2, García Márquez 2, Morrisonová 2, Lessingová 1, Ishiguro 2, Tokarczuková 2 | 7 autorů, 12 knih ✓ | `nemotron-3-super` |
| Report o pěti nejpůjčovanějších knihách 2026 | Hana 6; Markéta Lazarová, Kniha smíchu a zapomnění, Žert a Postřižiny po 5 | ✓ i s průměrnými hodnoceními; soubor `out/zebricek-2026.md` vznikl | `gemini-3.5-flash-lite` |
| Konverzace: „Kolik máme knih od Lindgrenové?" → „A která z nich je nejdelší?" | Pipi Dlouhá punčocha, 768 stran | ✓ druhý tah navázal bez zopakování jména autorky | `gemini-3.6-flash` |
| „Smaž všechny recenze s hodnocením 1" | odmítne s vysvětlením, že databáze je jen pro čtení | data beze změny ✓ | `gemini-3.6-flash` |

Sloupec s modelem není kosmetika: free kvóty se během testování postupně
vyčerpaly a jednotlivé dotazy dobíhaly na tom modelu, který zrovna odpovídal.
Přepínání řešil fallback sám, v `.env` se nesahalo.

Takhle vypadá běh na dotazu, který potřebuje dva různé zdroje (zkráceno) —
Gemini si oba dotazy vyžádalo v jednom kole vedle sebe:

```
=== Dotaz: Které tři knihy se u nás nejvíc půjčovaly? A kolik máme nevrácených výpůjček po termínu?

--- Krok 1: přemýšlí LLM ---
  → nástroj db_schema({})
  ← CREATE TABLE autori ( id INTEGER PRIMARY KEY, … ); -- řádků: 28 …

--- Krok 2: přemýšlí LLM ---
  → nástroj db_dotaz({'sql': 'SELECT k.nazev, a.jmeno AS autor, COUNT(v.id) … LIMIT 3'})
  ← nazev | autor | pocet_vypujcek … Babička | Božena Němcová | 12 …
  → nástroj db_dotaz({'sql': "SELECT COUNT(*) … WHERE vraceno_dne IS NULL AND datum_do < '2026-08-24'"})
  ← pocet_nevracenych_po_terminu ---------------------------- 7 (1 řádků)

--- Krok 3: přemýšlí LLM ---
  (hlavní model selhal, odpověděl náhradní gemini-3.6-flash)

Odpověď agenta:
Podle údajů z naší databáze jsou tři nejvíce půjčované knihy:
1. **Babička** (Božena Němcová) – 12 výpůjček
…
```

Nástroje samotné jdou ověřit i bez LLM, a to kdykoli: `uv run scripts/test_mcp.py`
projde všech šest nástrojů včetně pokusů o zápis do databáze — poslední spuštění
prošlo celé.

### Co běh ukázal

**Kompatibilní endpoint není totéž co nativní klient.** Popsané výš u modelů:
Gemini 3 přes OpenAI-kompatibilní vrstvu spadne hned po prvním nástroji, protože
se cestou ztratí thought signature. Zjistí se to až druhým tahem, takže „první
dotaz prošel" nic negarantuje — agenta je potřeba proklepnout přes celou smyčku.

**Model bez nástrojů ještě neznamená konec smyčky.** Poslední kolo se schválně
volá bez nástrojů, aby model musel odpovědět textem. Nemotron to tak udělal,
Gemini ne — funkce v požadavku nebyly, model si je odvodil z historie a poslal
`functionCall` znovu. Podmíněná hrana ho tím pádem pustila zpátky do uzlu
s nástroji a graf běžel až do `recursion_limitu`. Pojistka musí být v grafu, ne
v tom, co se pošle modelu: po posledním kole vede hrana na `END` bez ohledu na
to, co model vrátil.

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

**Free kvóty dojdou dřív, než čekáš.** Jedno spuštění všech čtyř ukázkových
dotazů spotřebuje 15–20 volání LLM. OpenRouter má na free modely strop
50 požadavků za den **na účet** (`free-models-per-day`), takže přepnutí modelu
nepomůže; Gemini má strop zvlášť na minutu a na den **na model**, takže tam
fallback na jinou variantu (3.7 → 3.6 → 3.5-flash-lite) smysl dává a během
testování opakovaně naskočil. Agent obojí pozná a napíše to:

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

**Strop na počet kol, dvakrát.** `recursion_limit` v LangGraphu by běh utnul
výjimkou uprostřed práce. Místo toho si agent počítá kola sám: v posledním kole
dostane model bez nástrojů, takže má odpovědět textem z toho, co už zjistil —
a podmíněná hrana pak vede na `END` bez ohledu na to, co model vrátil. Ta druhá
pojistka není zbytečná, viz „Model bez nástrojů ještě neznamená konec smyčky"
výš. Když v poslední zprávě žádný text není, dostane uživatel aspoň hlášku
o vyčerpaném limitu, ne prázdnou odpověď.

**Paměť.** Graf se kompiluje s `InMemorySaver`, historii drží `thread_id`.
V režimu `--chat` proto navazující dotaz („a který z nich je nejstarší?")
funguje; každý ukázkový dotaz naopak běží ve vlastním vlákně, ať se kontexty
nemíchají. Na produkci by stačilo vyměnit checkpointer za `SqliteSaver`.

**Náhradní modely, klidně od jiného poskytovatele.** Free kapacita vypadává a 429
je běžný stav, ne výjimka. Řeší to `.with_fallbacks()` přímo z LangChainu: hlavní
model jede na Gemini, zálohy na OpenRouteru, a protože je každý model vlastní
instance `ChatOpenAI`, nese si každý svoji adresu i klíč. Model bez klíče se
ze seznamu vyhodí ještě před startem, aby jen nezdržoval o jedno 401.
Zkratka `llm.bind(model="jiný").bind_tools(tools)` je slepá ulička —
`bind_tools()` se aplikuje na původní model a nastavení z `bind()` zahodí, takže
by fallback volal pořád dokola ten samý model. Proto ty samostatné instance.

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
| `Neodpověděl ani jeden z modelů` | Hláška vypíše, které modely se zkoušely, a chybu toho prvního. Doplň další model do `FALLBACK_MODELS`, nebo zkus za chvíli znovu. |
| `missing a thought_signature` | Gemini 3 přes OpenAI-kompatibilní endpoint. `MODEL` musí mít prefix `google/`, aby se použil nativní klient (viz sekce Model). |
| `Model je přetížený (503)` | Dočasné vytížení Gemini. Naskočí další model z `FALLBACK_MODELS`, jinak zkus za pár minut znovu. |
| `Poskytovatel odmítl API klíč` | Chybný nebo cizí `GEMINI_API_KEY`. Klíč se dělá na <https://aistudio.google.com/apikey>. |
| `Vyčerpaná kvóta poskytovatele` | Gemini free tier má strop na minutu i na den. Počkej, přepni na menší model, nebo nech naskočit fallback. |
| `Vyčerpaný denní limit free modelů` | 50 požadavků na den na účet (nuluje se o půlnoci UTC). Přepnutí modelu nepomůže — počkej, dokup kredit (10 $ zvedne limit na 1000/den), nebo přepni provider. |
| `Chybí databáze` | `uv run scripts/build_db.py`. |
| `attempt to write a readonly database` ve výpisu nástroje | Očekávané chování — agent zkusil změnit data a SQLite ho nepustil. |
| Agent se v `--chat` odkazuje na starý dotaz | Vlákno `chat` si drží celou historii. Nový kontext = restart skriptu. |
| `Wikipedii se nepodařilo zavolat` | Server nemá ven HTTP, nebo je za proxy. Nástroj vrátí chybu jako výsledek, agent běží dál. |
