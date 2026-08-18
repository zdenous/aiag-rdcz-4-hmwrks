# Úkol 1 — LLM API + volání nástroje (function / tool calling)

Python skript, který zavolá LLM API, nechá model použít **výpočetní funkci**
(nástroj), výsledek nástroje pošle **zpět LLM** a model z něj sestaví finální
odpověď.

## Jak to funguje

```
uživatelský dotaz
      │
      ▼
1. volání LLM API  ──►  odpověď obsahuje tool_call (např. calculate)
      │
      ▼
2. skript nástroj lokálně vykoná        calculate("(1287*43)-9856/4") -> 52877.0
      │
      ▼
3. výsledek se přidá do historie jako zpráva role "tool"
      │
      ▼
4. druhé volání LLM API  ──►  finální odpověď v přirozeném jazyce
```

Kroky 1–3 běží ve smyčce (max. 10 iterací), takže model může zavolat i více
nástrojů za sebou nebo několik naráz. Smyčka končí ve chvíli, kdy odpověď LLM
už žádný `tool_call` neobsahuje.

Veškerá logika je v [main.py](main.py).

## Nástroje

| Nástroj | Co dělá |
|---|---|
| `calculate(expression)` | Vyhodnotí aritmetický výraz (`+ - * / // % **`) a vrátí přesný výsledek. |
| `compound_interest(principal, annual_rate_pct, years, compounds_per_year)` | Spočítá konečnou hodnotu investice se složeným úrokem a získaný úrok. |

`calculate` **nepoužívá `eval()`**. Výraz se rozparsuje pomocí `ast` a vyhodnotí
se jen povolené uzly (čísla a aritmetické operátory), takže model nemůže poslat
řetězec, který by spustil libovolný kód.

## Provider — výchozí je OpenRouter free tier

Skript používá [LiteLLM](https://docs.litellm.ai/docs/providers), takže stejný
kód funguje s OpenAI, Anthropic, Gemini, OpenRouter, Ollama i LM Studio.
Provider se přepíná jen v `.env`, kód se nemění.

Výchozí nastavení je **free tier OpenRouteru**, takže úkol jde spustit bez
placeného API klíče:

```bash
cp .env.example .env
# do .env vlož klíč z https://openrouter.ai/keys
```

### Free modely s podporou nástrojů

Klíčové omezení: **ne každý `:free` model umí tool calling**. Modely bez něj
nástroj nikdy nezavolají a smyčka v tomhle úkolu se nikdy nespustí. Aktuální
seznam se dá vytáhnout z veřejného API OpenRouteru (klíč není potřeba):

```bash
curl -s https://openrouter.ai/api/v1/models | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data'] if m['id'].endswith(':free') and 'tools' in (m.get('supported_parameters') or [])]"
```

Ověřeno 18. 8. 2026 — tool calling podporuje 16 ze 17 free modelů, mimo jiné:

| Model (do `MODEL` s prefixem `openrouter/`) | Kontext |
|---|---|
| `google/gemma-4-31b-it:free` | 262 144 |
| `openai/gpt-oss-20b:free` (výchozí) | 131 072 |
| `z-ai/glm-5.2:free` | 256 000 |
| `nvidia/nemotron-3-super-120b-a12b:free` | 262 144 |

### Limity free tieru a odolnost skriptu

Na `:free` modely platí strop požadavků za minutu a za den; po nákupu kreditů
je denní strop vyšší. Aktuální zbývající limit klíče:

```bash
curl -s https://openrouter.ai/api/v1/key -H "Authorization: Bearer $OPENROUTER_API_KEY"
```

Podrobnosti: <https://openrouter.ai/docs/api-reference/limits>.

Kromě limitu vlastního klíče se dá narazit i na **sdílený pool upstream
providera** — free varianta modelu vrátí HTTP 429 („temporarily rate-limited
upstream“) klidně i na první požadavek dne, protože kapacitu sdílíš se všemi
ostatními. Skript to řeší sám:

1. každý model zkusí 3× s prodlužující se pauzou (3 s, 6 s),
2. pak přejde na další model z `FALLBACK_MODELS`,
3. model, který uspěl, používá i v dalších iteracích (nezdržuje se opakovaným
   zkoušením zablokovaného modelu),
4. když nedá žádný, vypíše srozumitelnou hlášku místo tracebacku.

Chybný API klíč se naopak neopakuje — skončí hned.

## Spuštění

```bash
uv run main.py
```

Bez argumentů se spustí dvě ukázkové otázky. Vlastní dotaz se předá jako argument:

```bash
uv run main.py "Kolik je 17 na třetí mínus 42?"
```

Případně přes ručně vytvořené virtuální prostředí:

```bash
uv venv && source .venv/bin/activate && uv sync && python main.py
```

## Ukázkový výstup

Skutečný běh proti free tieru OpenRouteru:

```
Model: openrouter/openai/gpt-oss-20b:free

=== Dotaz: Kolik je 17 na třetí mínus 42? ===

--- Iterace 1: volám LLM ---
  odpověděl model: openrouter/openai/gpt-oss-20b:free
LLM chce zavolat nástroj: calculate({'expression': '17**3 - 42'})
Výsledek nástroje: {'expression': '17**3 - 42', 'result': 4871}

--- Iterace 2: volám LLM ---
  odpověděl model: openrouter/openai/gpt-oss-20b:free

Finální odpověď LLM:
Výpočet: 17^3 - 42 = 4913 - 42 = 4871.
Výsledek je **4871**.
```

Když je hlavní model zahlcený, přidají se do výpisu řádky o retry a přechodu
na náhradní model:

```
--- Iterace 1: volám LLM ---
  openrouter/google/gemma-4-31b-it:free: RateLimitError, pokus 1/3 - čekám 3s a zkouším znovu
  openrouter/google/gemma-4-31b-it:free: RateLimitError, pokus 2/3 - čekám 6s a zkouším znovu
  openrouter/google/gemma-4-31b-it:free: RateLimitError i po 3 pokusech, zkouším další model
  odpověděl model: openrouter/openai/gpt-oss-20b:free
```
