# Úkol 2 — No-code agent v n8n nad databází

Agent v **n8n**, který odpovídá na dotazy o e-shopu. Dotaz dostane v přirozeném
jazyce, sám si k němu napíše SQL, spustí ho nad databází (**Supabase / Postgres**),
výsledek si přečte a odpoví česky. Umí i zapisovat — založí tiket zákaznické
podpory.

Deliverable je [workflow.json](workflow.json) — definice workflow pro n8n.

## Jak to funguje

```
chat widget ─┐
             ├─► Sjednocení vstupu ─► AI Agent ─► IF (webhook?) ─► Odpověď webhooku
POST /webhook┘                           │
                                         │  ai_languageModel: OpenRouter (free) + záložní model
                                         │  ai_memory:        Paměť konverzace (12 zpráv)
                                         │  ai_tool:          db_schema
                                         │                    db_dotaz
                                         │                    zaloz_tiket
                                         └──────────────────  kalkulacka
```

Agent běží ve smyčce: LLM se rozhodne, který nástroj zavolat, n8n nástroj vykoná
a výsledek pošle zpět do LLM. Když už žádný nástroj nepotřebuje, vrátí finální
odpověď (max. 12 iterací).

## Nástroje agenta

| Nástroj | Node | Co dělá |
|---|---|---|
| `db_schema` | Postgres Tool | Vrátí tabulky a sloupce z `information_schema`. Agent si tím ověří strukturu. |
| `db_dotaz` | Postgres Tool | Spustí SELECT, který LLM napsalo, a vrátí řádky (max. 200). |
| `zaloz_tiket` | Postgres Tool | Vloží řádek do `tikety` a vrátí ID — jediný zápis, který agent smí. |
| `kalkulacka` | Calculator Tool | Přesná aritmetika, aby model nepočítal zpaměti. |

Parametry nástrojů plní LLM přes `$fromAI(...)` — n8n z nich vygeneruje JSON
schéma nástroje, které pošle modelu.

### Read-only pojistka u `db_dotaz`

Dotaz od modelu se nespouští přímo. Node ho vloží dovnitř poddotazu:

```sql
SELECT * FROM (
  <SQL od LLM>
) AS agent_dotaz LIMIT 200
```

Postgres uvnitř `FROM (...)` přijme jen SELECT — `INSERT`, `UPDATE`, `DELETE`
ani `DROP` se takhle syntakticky nedají spustit. Zároveň to vynutí strop 200
řádků, takže velký výsledek nepřeteče kontext modelu. Případný středník na konci
se odřízne výrazem `.replace(/;+\s*$/, '')`, jinak by poddotaz neprošel.

Zápis má vlastní nástroj `zaloz_tiket` s pevným `INSERT` a parametry `$1..$4`
předanými jako pole — hodnoty od modelu se tedy nikdy nelepí do SQL řetězce.

## Databáze

Malý e-shop s elektronikou: 6 kategorií, 40 produktů, 24 zákazníků,
80 objednávek, 163 položek a 5 tiketů.

```
kategorie ──< produkty ──< polozky_objednavky >── objednavky >── zakaznici
                                                                     │
                                                                  tikety
```

Definice je v [db/01_schema.sql](db/01_schema.sql), data v
[db/02_seed.sql](db/02_seed.sql) (generováno deterministicky, seed 42).

## Zprovoznění (n8n Cloud + Supabase)

Nic se neinstaluje lokálně — n8n Cloud se na Supabase připojí přímo přes veřejný
internet, stačí správný endpoint a SSL.

### 1. Supabase

Založ projekt (free tier stačí), otevři **SQL Editor** a spusť postupně obsah
[db/01_schema.sql](db/01_schema.sql) a [db/02_seed.sql](db/02_seed.sql).

Připojovací údaje vezmi z tlačítka **Connect → Session pooler**, ne z *Direct
connection*: direct je na free tieru dostupný jen přes IPv6 a n8n Cloud tam
nedosáhne. Pooler jede na IPv4.

```
Host:     aws-1-<region>.pooler.supabase.com
Port:     5432                       (session pooler; 6543 je transaction pooler)
Database: postgres
User:     postgres.<project-ref>
Password: heslo k databázi
SSL:      require
```

Free projekt nemá zapnuté síťové restrikce, takže IP adresy n8n Cloudu nikam
povolovat nemusíš. Tabulky jsou ve schématu `public` bez RLS politik — agent se
připojuje přímo přes Postgres roli, ne přes PostgREST s anon klíčem, takže ho RLS
neomezuje. Data jsou vymyšlená, ne osobní.

### 2. n8n Cloud

Stačí běžná instance na `https://<tvoje-instance>.app.n8n.cloud`. Workflow
nepotřebuje community nody, externí npm balíčky ani proměnné prostředí, takže
běží na cloudu bez omezení stejně jako v self-hosted Dockeru.

Free tier nemá veřejné API — na chodu agenta to nic nemění, jen se workflow
vloží ručně (další krok) místo skriptem.

### 3. Import workflow

n8n Cloud ve free tieru nemá veřejné API, takže workflow se vkládá ručně —
`workflow.json` je na to připravený.

1. V n8n si nejdřív založ obě credentials (**Credentials → Add credential**).
   Pojmenuj je přesně takhle, ať je n8n při vkládání rovnou napáruje k nodům:

   | Název | Typ | Co vyplnit |
   |---|---|---|
   | `Postgres eshop (Supabase)` | Postgres | údaje ze Session pooleru, SSL `require`, *Ignore SSL Issues* zapnuto |
   | `OpenRouter` | OpenAI | API klíč z openrouter.ai (Base URL řeší přímo node) |

2. Vytvoř nové prázdné workflow.
3. Otevři [workflow.json](workflow.json), zkopíruj **celý obsah** (Ctrl+A, Ctrl+C)
   a na plátně n8n dej **Ctrl+V**. Nody, propojení i poznámkové bublinky se
   vloží najednou.
   Stejně dobře funguje **⋮ → Import from File…** a vybrat `workflow.json`.
4. Zkontroluj credentials ve čtyřech nodech — `db_schema`, `db_dotaz`,
   `zaloz_tiket` a `OpenRouter Chat Model`. Pokud se nenapárovaly podle názvu,
   vyber je v každém nodu z rozbalovátka.
5. Workflow ulož a přepínačem vpravo nahoře ho **aktivuj** (bez toho vrací
   produkční webhook 404).

<details>
<summary>Automatický import přes API (self-hosted nebo placený cloud plán)</summary>

Tam, kde veřejné API k dispozici je, udělá totéž jeden příkaz — založí obě
credentials, doplní jejich ID do workflow, nahraje ho a aktivuje:

```bash
cp .env.example .env      # doplň N8N_URL, N8N_API_KEY, OPENROUTER_API_KEY a PG_*
python3 scripts/setup_n8n.py
```

Opakované spuštění workflow jen aktualizuje.

</details>

### 4. Vyzkoušení

**V editoru:** tlačítko **Chat** dole na plátně (n8n ho zobrazí, protože je ve
workflow Chat Trigger). Zprávy se posílají v testovacím režimu, takže je na
nodech vidět, co agent volal a co mu databáze vrátila.

**Veřejná chat stránka:** Chat Trigger má zapnuté *Make Chat Publicly Available*,
takže má vlastní URL ve tvaru
`https://<instance>.app.n8n.cloud/webhook/<webhookId nodu>/chat`
(v tomhle workflow `7c1f0f9e-0001-4a10-9d31-eshopagent01`). Funguje jen na
aktivovaném workflow.

**Přes HTTP zvenku:**

```bash
curl -s -X POST https://<tvoje-instance>.app.n8n.cloud/webhook/eshop-agent -H "Content-Type: application/json" -d '{"dotaz":"Které tři produkty vydělaly nejvíc?","session_id":"demo"}'
```

Sada testovacích dotazů najednou (potřebuje z `.env` jen `N8N_URL`, žádný API
klíč):

```bash
python3 scripts/test_agent.py
```

Webhook vrací `{"odpoved": "...", "dotaz": "...", "session_id": "..."}`.
`session_id` drží paměť konverzace, takže navazující dotazy fungují.

### Alternativa: všechno lokálně

Workflow je stejné, mění se jen `.env`. Databáze:

```bash
docker compose up -d      # Postgres na portu 5433, sám si nahraje obě SQL
```

Data do libovolné z obou databází jde nalít i skriptem místo ručního kopírování
do SQL Editoru:

```bash
uv run --with "psycopg[binary]" scripts/apply_sql.py
```

n8n pak z podkladů kurzu (`SOURCE/3_N8N`, `docker compose up -d --build`). Pozor:
n8n v Dockeru se na lokální Postgres dostane přes `host.docker.internal:5433`,
zatímco skripty běžící na hostu přes `localhost:5433`.

## Ukázkové dotazy

- Kolik máme zákazníků a kolik z nich je VIP?
- Které tři produkty nám vydělaly nejvíc peněz? Počítej z položek objednávek.
- Kolik jsme utržili v červenci 2026?
- Máme skladem něco od značky Vela?
- Jaká je průměrná hodnota objednávky u plateb kartou?
- Založ tiket pro jana.novakova@example.cz: nedorazila faktura, priorita vysoka.

## Ověřený běh

Spuštěno 19. 8. 2026 proti n8n Cloud (`reichl.app.n8n.cloud`) a Supabase
(`eu-north-1`), model `openai/gpt-oss-20b:free`. Odpovědi agenta jsou porovnané
s tím, co vrátí stejný dotaz puštěný přímo nad databází:

| Dotaz | Odpověď agenta | Kontrola v DB |
|---|---|---|
| Kolik máme zákazníků a kolik z nich je VIP? | 24 zákazníků, 11 VIP | `24, 11` ✓ |
| Které tři produkty vydělaly nejvíc? | Nordis Basic 15 (192 622), Lumex A7 (183 253), Aurora 14 Pro (164 950) | ✓ včetně vynechání storen |
| Kolik jsme utržili za červenec 2026? | 206 612 Kč | `206612` ✓ |
| Máme skladem něco od značky Vela? | Vela S9 (8 ks), Vela Tab Go (31 ks) | ✓ správně vynechal S9 Mini s nulou |
| Založ tiket… | „Tiket byl úspěšně založen, ID 7" | řádek v `tikety` vznikl ✓ (testovací tikety pak smazány) |
| Smaž stornované objednávky | odmítl to udělat | data beze změny ✓ |
| Paměť: „Kolik máme produktů Kometa?" → „A který z nich je nejdražší?" | Kometa 12 Pro, 26 490 Kč | ✓ druhý tah navázal bez zopakování značky |

Read-only pojistka drží i na úrovni databáze, ne jen na ochotě modelu — SQL,
které by měnilo data, uvnitř poddotazu neprojde:

```
SELECT * FROM (DELETE FROM tikety WHERE id = 8 RETURNING *) AS agent_dotaz …
  → ERROR: syntax error at or near "FROM"
SELECT * FROM (UPDATE produkty SET cena_kc = 1 RETURNING *) AS agent_dotaz …
  → ERROR: syntax error at or near "SET"
SELECT * FROM (WITH x AS (DELETE FROM tikety RETURNING *) SELECT * FROM x) AS … 
  → ERROR: WITH clause containing a data-modifying statement must be at the top level
```

Poslední případ je ten zajímavý: schovat zápis do CTE je obvyklá cesta, jak
takovou pojistku obejít, a Postgres ho uvnitř poddotazu taky nepustí. Zápis se
tedy dá udělat jen nástrojem `zaloz_tiket`, který má SQL napevno.

### Co běh ukázal jako slabinu

Free tier OpenRouteru občas vrátí 429 „temporarily rate-limited upstream" —
z několika desítek volání zhruba čtvrtina skončila chybou agenta a webhook vrátil
prázdné tělo. Přímé měření modelů (5 volání za sebou) přitom prošlo 5/5, takže
jde o výpadky sdíleného poolu, ne o vyčerpaný klíč.

Workflow na to reaguje dvěma způsoby:

- **Záložní model** — agent má `needsFallback`, druhý model
  (`nvidia/nemotron-3-super-120b-a12b:free`) naskočí, když hlavní selže.
  Ověřeno 19. 8. 2026: `z-ai/glm-5.2:free` i `google/gemma-4-31b-it:free` byly
  ten den samy 429, nemotron odpovídal do 2 s — proto je fallbackem on.
- **`onError: continueRegularOutput` na agentovi** — místo prázdné odpovědi
  vrátí webhook `{"odpoved": "Agenta se nepodařilo dokončit: …"}`.

## Poznámky k návrhu

**Dva spouštěče, jeden agent.** Chat trigger slouží k ručnímu zkoušení v n8n,
webhook k volání z venku (a k automatickému testu). Code node `Sjednocení vstupu`
oba tvary sjednotí na `{ dotaz, session_id, kanal }`. Uzel `IF` pak pošle
odpověď do `Respond to Webhook` jen u webhooku — v chatu by node navíc za agentem
rozbil vracení odpovědi do widgetu.

**Schéma v systémovém promptu.** Struktura tabulek je i v systémové zprávě, aby
model nemusel volat `db_schema` při každém dotazu; nástroj zůstává pro ověření.
Do promptu se přes výraz `{{ $now.format('yyyy-MM-dd') }}` doplňuje aktuální
datum, jinak by model neuměl vyhodnotit "za poslední měsíc".

**Model.** `openai/gpt-oss-20b:free` na OpenRouteru — free tier, ale umí tool
calling (bez něj by agent nezavolal žádný nástroj). Záložní je
`nvidia/nemotron-3-super-120b-a12b:free`. Oba se přepínají v nodech
`OpenRouter Chat Model` a `Záložní model (OpenRouter)`, případně přes
`OPENROUTER_MODEL` v `.env`, když se importuje skriptem.

Než nějaký model nasadíš, ověř si, že zrovna odpovídá — dostupnost free variant
kolísá podle vytížení sdíleného poolu:

```bash
curl -s https://openrouter.ai/api/v1/models | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data'] if m['id'].endswith(':free') and 'tools' in (m.get('supported_parameters') or [])]"
```

## Když něco nefunguje

| Projev | Příčina |
|---|---|
| Agent odpoví, ale nezavolá žádný nástroj | Model neumí tool calling — přepni na jiný free model (seznam níž v Poznámkách). |
| `connect ETIMEDOUT` v Postgres nodu | Použil jsi Supabase *Direct connection* (IPv6). Přepni na Session pooler (IPv4). |
| `self signed certificate` | V credentialu zapni *Ignore SSL Issues* (`PG_ALLOW_UNAUTHORIZED=true`). |
| `syntax error at or near` v `db_dotaz` | Model poslal víc příkazů najednou; poddotaz povolí jen jeden SELECT. |
| Webhook vrací 404 | Workflow není aktivní — `scripts/setup_n8n.py` ho aktivuje, jinak přepínač vpravo nahoře. Testovací URL má navíc `/webhook-test/`, produkční `/webhook/`. |
| 429 z OpenRouteru | Výpadek sdíleného poolu free modelů. Naskočí záložní model; když je 429 i ten, zkus za chvíli znovu nebo přepni model. |
