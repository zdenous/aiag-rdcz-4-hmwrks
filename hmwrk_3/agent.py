"""
Úkol 3 - ReAct agent postavený v LangGraphu, nástroje si bere z MCP serveru.

Agent odpovídá na dotazy o malé městské knihovně. Data má v SQLite katalogu,
o autorech ale katalog nevede žádné životopisné údaje - ty si agent dohledá na
Wikipedii a s katalogem si je spojí sám.

Smyčka ReAct je v LangGraphu popsaná jako graf se dvěma uzly:

        ┌──────────────────────────────────────────┐
        │                                          │
    START ──► agent ──(volá nástroj?)──► nastroje ─┘
                 │
                 └──(hotová odpověď)──► END

Nástroje nejsou napsané pro LangGraph. Sedí v samostatném MCP serveru
(mcp_server/server.py) a agent si je za běhu natáhne přes MCP - stejný server
by beze změny obsloužil i Claude Desktop nebo jiného MCP klienta.

Spuštění:
    uv run agent.py                     # sada ukázkových dotazů
    uv run agent.py "Kolik máme knih?"  # vlastní dotaz
    uv run agent.py --chat              # konverzace s pamětí (thread_id)
"""

import asyncio
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Annotated, Any, NamedTuple, Sequence, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

load_dotenv()

# google-genai vypíše ke každému volání odstavec o "automatic function calling",
# který se tady netýká ničeho - nástroje si volá agent sám.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

KOREN = Path(__file__).resolve().parent

# --- LLM ------------------------------------------------------------------
# Model se zapisuje s prefixem poskytovatele, stejně jako v úkolu 1:
#   google/gemini-3.7-flash        -> nativní klient Gemini API (viz chat())
#   openrouter/z-ai/glm-5.2:free   -> OpenRouter
#   cokoli bez známého prefixu     -> LLM_BASE_URL + LLM_API_KEY
#                                     (OpenAI, LiteLLM proxy, Ollama, LM Studio…)
# Kromě Gemini jsou to všechno OpenAI-kompatibilní API, takže se mění jen adresa,
# klíč a jméno modelu - kód zůstává stejný.
MODEL = os.environ.get("MODEL", "google/gemini-3.7-flash")
# Náhradní modely mohou být od jiného poskytovatele než ten hlavní. Free modely
# na OpenRouteru sdílí kapacitu s ostatními uživateli a občas vrátí 429; přepnutí
# na ně řeší .with_fallbacks() z LangChainu, nic se neprogramuje ručně.
FALLBACK_MODELS = [
    m.strip()
    for m in os.environ.get(
        "FALLBACK_MODELS",
        "google/gemini-3.6-flash"
        ",google/gemini-3.5-flash-lite"
        ",openrouter/nvidia/nemotron-3-super-120b-a12b:free"
        ",openrouter/z-ai/glm-5.2:free",
    ).split(",")
    if m.strip()
]

# prefix -> (adresa API, proměnné prostředí s klíčem v pořadí, jak se zkoušejí)
POSKYTOVATELE = {
    "google": (None, ("GEMINI_API_KEY", "GOOGLE_API_KEY")),  # adresu řeší nativní klient
    "openrouter": ("https://openrouter.ai/api/v1", ("OPENROUTER_API_KEY",)),
}


class Spec(NamedTuple):
    """Rozložený zápis modelu."""

    nazev: str
    adresa: str | None
    klic: str
    poskytovatel: str  # "" = vlastní OpenAI-kompatibilní endpoint


def rozloz_model(zapis: str) -> Spec:
    """'google/gemini-3.7-flash' -> Spec('gemini-3.7-flash', None, klíč, 'google')."""
    prefix, _, zbytek = zapis.partition("/")
    if prefix in POSKYTOVATELE and zbytek:
        # pozor: jméno modelu samo obsahuje lomítka (openrouter/z-ai/glm-5.2:free),
        # takže se odřízne jen první část
        adresa, promenne = POSKYTOVATELE[prefix]
        klic = next((os.environ[p] for p in promenne if os.environ.get(p)), "")
        return Spec(zbytek, adresa, klic, prefix)
    return Spec(
        zapis,
        os.environ.get("LLM_BASE_URL") or None,  # None = výchozí OpenAI endpoint
        os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "",
        "",
    )


def pouzitelne_modely() -> list[str]:
    """Modely v pořadí, jak se mají zkoušet; ty bez klíče se přeskočí."""
    poradi = [MODEL, *[m for m in FALLBACK_MODELS if m != MODEL]]
    # Bez klíče by model spadl na 401 a jen zdržel. Vlastní endpointy (Ollama,
    # LiteLLM) klíč mít nemusí, takže se u nich nekontroluje.
    return [
        zapis
        for zapis in poradi
        if rozloz_model(zapis).klic or not rozloz_model(zapis).poskytovatel
    ]

# --- MCP ------------------------------------------------------------------
# stdio = agent si server spustí sám jako podproces (nic se nemusí startovat
# zvlášť), http = připojí se na už běžící `uv run mcp_server/server.py --http`.
MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8010/mcp")

MAX_KROKU = 10  # kolik kol (volání LLM) smí agent nejvýš spotřebovat

SYSTEM_PROMPT = f"""Jsi asistent malé městské knihovny. Odpovídáš česky, stručně a věcně.

Dnešní datum je {date.today().isoformat()}.

Data hledáš VÝHRADNĚ nástroji, nikdy neodpovídáš z paměti a nevymýšlíš si čísla.
Pravidla:
- Na cokoli o knihách, autorech, čtenářích, výpůjčkách a pokutách použij db_dotaz
  (SQLite). Když si nejsi jistý strukturou, zavolej nejdřív db_schema.
- Obsah recenzí (co čtenáři píšou) hledej nástrojem hledej_v_recenzich, ne
  přes LIKE v db_dotaz.
- Katalog o autorech neví nic než jméno a zemi. Životopisné údaje - kdy se
  narodil, ceny, o čem kniha je - dohledej na Wikipedii
  (wikipedia_hledej, pak wikipedia_clanek).
- Když se odpověď skládá z více zdrojů, spoj si je sám a v odpovědi řekni,
  odkud který údaj je.
- Databáze je jen pro čtení. Když po tobě někdo chce změnu dat, vysvětli, že
  to neumíš.
- Report ukládej nástrojem zapis_report jen tehdy, když o soubor uživatel
  výslovně požádá.
- Počítej v SQL (SUM, AVG, COUNT), ne zpaměti.
- Pozor na spojení dvou tabulek 1:N naráz (výpůjčky i recenze k téže knize):
  řádky se pronásobí a COUNT vyjde několikanásobně vyšší. Každou agregaci
  spočítej vlastním poddotazem, nebo si to rozděl na víc dotazů.
- V odpovědi neuváděj citační značky ani jména nástrojů v závorkách; zdroj
  popiš slovy ("z katalogu", "z Wikipedie").
- Neopakuj stejné volání nástroje se stejnými parametry. Když výsledek nestačil,
  zkus jiný postup (jiný nástroj, jiný dotaz, jinou jazykovou verzi), nebo
  odpověz s tím, co víš."""


# ---------------------------------------------------------------------------
# Stav grafu
# ---------------------------------------------------------------------------


class Stav(TypedDict):
    """Stav, který si uzly grafu předávají."""

    # add_messages je reducer - nové zprávy se ke stavu přidávají, nepřepisují ho
    messages: Annotated[list[AnyMessage], add_messages]
    kroky: int


VERBOSE = True


def log(text: str = "") -> None:
    # flush: bez něj se při přesměrování do souboru neobjeví nic až do konce běhu
    if VERBOSE:
        print(text, flush=True)


def _text(obsah: Any) -> str:
    """Obsah zprávy jako holý text - MCP nástroje vracejí seznam bloků obsahu."""
    if isinstance(obsah, list):
        return " ".join(
            blok.get("text", "") if isinstance(blok, dict) else str(blok) for blok in obsah
        )
    return str(obsah)


def _zkrat(text: Any, limit: int = 220) -> str:
    text = " ".join(_text(text).split())
    return text if len(text) <= limit else text[:limit] + "…"


# ---------------------------------------------------------------------------
# Sestavení grafu
# ---------------------------------------------------------------------------


def postav_graf(nastroje: Sequence[BaseTool]):
    """Poskládá ReAct smyčku: uzel s LLM, uzel s nástroji a podmíněná hrana mezi nimi."""

    def chat(zapis: str) -> BaseChatModel:
        spec = rozloz_model(zapis)
        if spec.poskytovatel == "google":
            # Gemini má sice OpenAI-kompatibilní endpoint, ale přes něj se ztrácí
            # "thought signature", kterou modely řady Gemini 3 vyžadují vrátit
            # u každého volání nástroje. Druhý tah pak skončí chybou 400. Nativní
            # klient signatury přenáší sám, takže Google jede přes něj.
            return ChatGoogleGenerativeAI(
                model=spec.nazev,
                google_api_key=spec.klic,
                timeout=120,
                max_retries=2,
            )
        return ChatOpenAI(
            model=spec.nazev,
            base_url=spec.adresa,
            api_key=spec.klic or "nic",  # lokální endpointy klíč nekontrolují
            temperature=0,
            timeout=120,
            max_retries=2,  # klient sám zopakuje 429 podle hlavičky Retry-After
        )

    # Když hlavní model spadne (typicky 429 ze sdíleného poolu free modelů),
    # LangChain pošle stejný vstup na další model ze seznamu - .with_fallbacks()
    # je součást frameworku, nic se nemusí programovat ručně.
    # Pozor: každý model musí být vlastní instance ChatOpenAI. Zkratka
    # llm.bind(model=...).bind_tools(...) nefunguje, bind_tools() se aplikuje na
    # původní model a nastavení z bind() zahodí - fallback by pak volal pořád
    # dokola ten samý model.
    modely = pouzitelne_modely()
    llm_s_nastroji = chat(modely[0]).bind_tools(nastroje).with_fallbacks(
        [chat(nahradni).bind_tools(nastroje) for nahradni in modely[1:]]
    )
    # Poslední kolo běží bez nástrojů, aby model odpověděl textem. Samo o sobě
    # to nestačí (viz kam_dal), ale bez toho by poslední zpráva byla často jen
    # další volání nástroje.
    llm_bez_nastroju = chat(modely[0]).with_fallbacks(
        [chat(nahradni) for nahradni in modely[1:]]
    )

    podle_jmena = {nastroj.name: nastroj for nastroj in nastroje}

    async def uzel_agent(stav: Stav) -> dict[str, Any]:
        """Reason: LLM se rozhodne, jestli zavolá nástroj, nebo už umí odpovědět."""
        kroky = stav.get("kroky", 0)
        dosly_kroky = kroky >= MAX_KROKU
        model = llm_bez_nastroju if dosly_kroky else llm_s_nastroji

        log(f"\n--- Krok {kroky + 1}: přemýšlí LLM ---")
        if dosly_kroky:
            log(f"  (vyčerpáno {MAX_KROKU} kol - volám model bez nástrojů o finální odpověď)")

        zpravy = [SystemMessage(SYSTEM_PROMPT), *stav["messages"]]
        if dosly_kroky:
            # Samotné odebrání nástrojů nestačí - některé modely pak volání
            # nástroje jen "napíšou" do textu odpovědi.
            zpravy.append(
                SystemMessage(
                    "Nástroje už nejsou k dispozici a další volání není možné. "
                    "Odpověz teď rovnou textem z toho, co už víš, a u údajů, které "
                    "se zjistit nepodařilo, to řekni. Nevypisuj volání nástrojů."
                )
            )
        odpoved = await model.ainvoke(zpravy)

        hlavni = rozloz_model(modely[0]).nazev
        model_ktery_odpovedel = odpoved.response_metadata.get("model_name", hlavni)
        if model_ktery_odpovedel != hlavni:
            log(f"  (hlavní model selhal, odpověděl náhradní {model_ktery_odpovedel})")
        if VERBOSE and odpoved.content and odpoved.tool_calls:
            # Některé modely posílají úvahu i se zavoláním nástroje.
            log(f"  úvaha: {_zkrat(odpoved.content)}")

        return {"messages": [odpoved], "kroky": kroky + 1}

    async def uzel_nastroje(stav: Stav) -> dict[str, Any]:
        """Act + Observe: vykoná nástroje, které si LLM vyžádalo, a vrátí výsledky."""
        posledni = stav["messages"][-1]
        vysledky: list[ToolMessage] = []

        for volani in posledni.tool_calls:
            log(f"  → nástroj {volani['name']}({_zkrat(volani['args'], 160)})")
            nastroj = podle_jmena.get(volani["name"])
            if nastroj is None:
                vysledky.append(
                    ToolMessage(
                        content=f"CHYBA: nástroj '{volani['name']}' neexistuje.",
                        tool_call_id=volani["id"],
                        name=volani["name"],
                    )
                )
                continue
            # ainvoke s ToolCall na vstupu vrátí rovnou ToolMessage se správným id
            zprava = await nastroj.ainvoke(volani)
            log(f"  ← {_zkrat(zprava.content)}")
            vysledky.append(zprava)

        return {"messages": vysledky}

    def kam_dal(stav: Stav) -> str:
        """Podmíněná hrana: chce LLM nástroj, nebo je odpověď hotová?"""
        if stav.get("kroky", 0) > MAX_KROKU:
            # Poslední kolo (to bez nástrojů) už proběhlo - dál se nepokračuje ani
            # tehdy, když model volání nástroje napsal znovu. Gemini to dělá:
            # funkce v požadavku nejsou, ale model je odvodí z historie a pošle
            # functionCall stejně, takže bez téhle pojistky se smyčka točí až do
            # recursion_limitu.
            return END
        return "nastroje" if getattr(stav["messages"][-1], "tool_calls", None) else END

    graf = StateGraph(Stav)
    graf.add_node("agent", uzel_agent)
    graf.add_node("nastroje", uzel_nastroje)
    graf.add_edge(START, "agent")
    graf.add_conditional_edges("agent", kam_dal, {"nastroje": "nastroje", END: END})
    graf.add_edge("nastroje", "agent")  # výsledek nástroje jde zpátky do LLM

    # Checkpointer drží historii konverzace podle thread_id -> agent si v režimu
    # --chat pamatuje předchozí dotazy.
    return graf.compile(checkpointer=InMemorySaver())


# ---------------------------------------------------------------------------
# Připojení k MCP serveru
# ---------------------------------------------------------------------------


def mcp_spojeni() -> dict[str, Any]:
    if MCP_TRANSPORT == "http":
        return {"transport": "streamable_http", "url": MCP_URL}
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(KOREN / "mcp_server" / "server.py")],
        "env": {**os.environ},
        "cwd": str(KOREN),
    }


def _vysvetli_chybu(exc: BaseException, hlavicka: bool = True) -> str:
    """Místo tracebacku z hloubi knihovny srozumitelná hláška."""
    # .with_fallbacks() vyhodí chybu prvního modelu, takže bez tohohle není
    # z výpisu poznat, že se zkoušely i náhradní.
    uvod = (
        f"Neodpověděl ani jeden z modelů ({' → '.join(pouzitelne_modely())}).\n"
        if hlavicka
        else ""
    )
    if isinstance(exc, GraphRecursionError):
        return (
            f"Agent se zacyklil - graf vyčerpal recursion_limit dřív, než stihl\n"
            f"odpovědět. Zkus dotaz rozdělit na menší části, nebo zvedni MAX_KROKU."
        )
    zprava = " ".join(str(exc).split())
    if isinstance(exc, BaseExceptionGroup):  # anyio balí chyby do skupin
        zprava = " ".join(_vysvetli_chybu(pod, hlavicka=False) for pod in exc.exceptions)
    if any(
        hlaska in zprava
        for hlaska in ("API_KEY_INVALID", "API key not valid", "pass a valid API key")
    ):
        return uvod + (
            "Poskytovatel odmítl API klíč. U Gemini zkontroluj GEMINI_API_KEY v .env -\n"
            "klíč se vytváří na https://aistudio.google.com/apikey a musí patřit\n"
            "k projektu, kde je Gemini API zapnuté."
        )
    if "RESOURCE_EXHAUSTED" in zprava or "quota" in zprava.lower():
        return uvod + (
            "Vyčerpaná kvóta poskytovatele (u Gemini free tieru je strop na minutu\n"
            "i na den). Zkus to za chvíli znovu, přepni MODEL na menší variantu\n"
            "(např. google/gemini-3.5-flash-lite), nebo nech naskočit fallback."
        )
    if "UNAVAILABLE" in zprava or "503" in zprava:
        return uvod + (
            "Model je přetížený (503). Bývá to na pár minut - zkus to znovu, nebo\n"
            "doplň do FALLBACK_MODELS další model, na který máš klíč."
        )
    if "free-models-per-day" in zprava:
        return uvod + (
            "Vyčerpaný denní limit free modelů na OpenRouteru (50 požadavků na den,\n"
            "počítadlo se nuluje o půlnoci UTC). Přepnutí modelu nepomůže, limit je\n"
            "na účet. Řešení: počkat, dokoupit kredit, nebo v .env nastavit jiný\n"
            "provider (LLM_BASE_URL + LLM_API_KEY + MODEL)."
        )
    if "429" in zprava or "rate-limited" in zprava:
        return uvod + (
            "Poskytovatel vrátil 429. Free modely na OpenRouteru sdílí kapacitu se\n"
            "všemi uživateli, takže výpadky jsou běžné - zkus to za chvíli znovu,\n"
            "nebo v .env přepni MODEL / FALLBACK_MODELS (seznam ověřených je v README)."
        )
    return uvod + f"{type(exc).__name__}: {zprava[:400]}"


async def zeptej_se(graf, dotaz: str, vlakno: str = "demo") -> str | None:
    """Pošle dotaz do grafu a vrátí finální textovou odpověď (None při chybě)."""
    log(f"\n{'=' * 72}\n=== Dotaz: {dotaz}\n{'=' * 72}")
    try:
        stav = await graf.ainvoke(
            {"messages": [HumanMessage(dotaz)], "kroky": 0},
            config={
                "configurable": {"thread_id": vlakno},
                "recursion_limit": 2 * MAX_KROKU + 5,
            },
        )
    except Exception as exc:  # výpadek LLM nebo nástroje nesmí shodit celý běh
        print(f"\nDotaz se nepodařilo dokončit.\n{_vysvetli_chybu(exc)}")
        return None

    odpoved = _text(stav["messages"][-1].content).strip()
    if not odpoved:
        # Poslední zpráva byla samé volání nástroje - text v ní žádný není.
        odpoved = (
            f"(Agent nedospěl k odpovědi v limitu {MAX_KROKU} kol. "
            "Zkus dotaz rozdělit na menší části.)"
        )
    log("\nOdpověď agenta:")
    print(odpoved, flush=True)
    return odpoved


DEMO_DOTAZY = [
    # jen katalog
    "Které tři knihy se u nás nejvíc půjčovaly? A kolik máme celkem nevrácených výpůjček po termínu?",
    # katalog + fulltext v recenzích
    "Stěžoval si někdo z čtenářů na překlad nebo na poškozený výtisk? Kterých knih se to týká?",
    # katalog + Wikipedia
    "Kdo z autorů v našem katalogu dostal Nobelovu cenu za literaturu a kolik jejich knih máme?",
    # zápis souboru
    "Připrav mi report o pěti nejpůjčovanějších knihách za rok 2026 včetně průměrného hodnocení a ulož ho do souboru zebricek-2026.md.",
]


async def rezim_chat(graf) -> None:
    """Interaktivní konverzace - díky checkpointeru si agent pamatuje předchozí tahy."""
    print("Konverzace s agentem (prázdný řádek nebo Ctrl+D ukončí).\n")
    while True:
        try:
            dotaz = input("Ty: ").strip()
        except EOFError:
            break
        if not dotaz:
            break
        await zeptej_se(graf, dotaz, vlakno="chat")
        print()


async def main() -> None:
    global VERBOSE

    argumenty = [a for a in sys.argv[1:] if a != "--tise"]
    VERBOSE = "--tise" not in sys.argv

    modely = pouzitelne_modely()
    if not modely:
        sys.exit(
            "Ani jeden z nastavených modelů nemá API klíč.\n"
            "  cp .env.example .env  a doplň aspoň jeden z nich:\n"
            "    GEMINI_API_KEY      klíč zdarma na https://aistudio.google.com/apikey\n"
            "    OPENROUTER_API_KEY  klíč zdarma na https://openrouter.ai/keys\n"
            f"  (nastavené modely: {', '.join([MODEL, *FALLBACK_MODELS])})"
        )
    if modely[0] != MODEL:
        print(f"Pozor: {MODEL} nemá klíč, přeskakuji ho.")
    if not (KOREN / "data" / "knihovna.db").exists() and MCP_TRANSPORT != "http":
        sys.exit("Chybí databáze - spusť nejdřív `uv run scripts/build_db.py`.")

    print("Modely: " + " → ".join(modely))
    print(f"MCP: {MCP_TRANSPORT}" + (f" {MCP_URL}" if MCP_TRANSPORT == "http" else ""))

    client = MultiServerMCPClient({"knihovna": mcp_spojeni()})
    # Jedno MCP sezení na celý běh - jinak by se stdio server startoval znovu
    # při každém volání nástroje.
    async with client.session("knihovna") as session:
        nastroje = await load_mcp_tools(session)
        print("Nástroje z MCP serveru: " + ", ".join(n.name for n in nastroje))

        graf = postav_graf(nastroje)

        if "--chat" in argumenty:
            await rezim_chat(graf)
            return

        dotazy = argumenty or DEMO_DOTAZY
        neuspech = False
        for poradi, dotaz in enumerate(dotazy):
            # Každý ukázkový dotaz má vlastní vlákno, ať se historie nemíchá.
            neuspech |= await zeptej_se(graf, dotaz, vlakno=f"demo-{poradi}") is None

    if neuspech:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
