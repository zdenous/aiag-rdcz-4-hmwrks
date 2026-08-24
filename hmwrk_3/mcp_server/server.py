"""
MCP server s nástroji pro agenta z úkolu 3.

Nástroje jsou schválně vystavené přes Model Context Protocol, ne jako funkce
napsané pro konkrétní framework: stejný server obslouží agenta v LangGraphu,
Claude Desktop i cokoli dalšího, co umí MCP. Agent o serveru nic neví předem -
seznam nástrojů i jejich JSON schémata si vytáhne za běhu přes `list_tools`.

Spuštění:
    uv run mcp_server/server.py                # stdio (výchozí, spouští si ho agent sám)
    uv run mcp_server/server.py --http         # streamable HTTP na http://127.0.0.1:8010/mcp

Nástroje:
    db_schema            struktura databáze knihovny
    db_dotaz             SELECT nad databází (spojení je otevřené jen pro čtení)
    hledej_v_recenzich   fulltextové hledání v recenzích (SQLite FTS5, bm25)
    wikipedia_hledej     hledání článků na Wikipedii
    wikipedia_clanek     text konkrétního článku
    zapis_report         uložení výstupu do souboru v out/
"""

import logging
import os
import re
import sqlite3
import sys
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

KOREN = Path(__file__).resolve().parent.parent
DB_CESTA = Path(os.environ.get("KNIHOVNA_DB") or KOREN / "data" / "knihovna.db")
OUT_ADRESAR = Path(os.environ.get("REPORT_DIR") or KOREN / "out")

MAX_RADKU = 200          # strop na jeden dotaz, ať výsledek nepřeteče kontext modelu
MAX_DELKA_BUNKY = 300    # delší textové hodnoty se ve výpisu zkrátí
HTTP_TIMEOUT = 20.0

# httpx loguje každý požadavek na Wikipedii do stderr, což při stdio transportu
# končí v konzoli agenta uprostřed jeho vlastního výpisu.
logging.getLogger("httpx").setLevel(logging.WARNING)

mcp = FastMCP(
    "knihovna-tools",
    # WARNING: bez toho server u každého volání nástroje vypíše do stderr řádek
    # "Processing request of type CallToolRequest" doprostřed výpisu agenta.
    log_level="WARNING",
    instructions=(
        "Nástroje nad katalogem malé městské knihovny (SQLite) a nad Wikipedií. "
        "Katalog obsahuje knihy, autory, čtenáře, výpůjčky a recenze; "
        "o autorech ale nevede žádné životopisné údaje - ty je potřeba dohledat "
        "na Wikipedii."
    ),
)


# ---------------------------------------------------------------------------
# Databáze knihovny
# ---------------------------------------------------------------------------


def _spojeni() -> sqlite3.Connection:
    """
    Spojení otevřené v režimu read-only.

    Zápis neblokuje kontrola SQL řetězce (tu jde obejít), ale samotný SQLite:
    `mode=ro` znamená, že INSERT/UPDATE/DELETE/DROP skončí chybou
    "attempt to write a readonly database", ať už je model vymyslí jakkoli.
    """
    if not DB_CESTA.exists():
        raise FileNotFoundError(
            f"Databáze {DB_CESTA} neexistuje - spusť nejdřív `uv run scripts/build_db.py`."
        )
    conn = sqlite3.connect(f"file:{DB_CESTA}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")  # druhá pojistka, kdyby se cesta otevřela jinak
    return conn


def _tabulka(sloupce: list[str], radky: list[tuple]) -> str:
    """Výsledek dotazu jako textová tabulka - pro LLM čitelnější než JSON."""
    if not radky:
        return "(dotaz proběhl v pořádku, ale nevrátil žádný řádek)"

    def bunka(hodnota) -> str:
        text = "NULL" if hodnota is None else str(hodnota)
        return text[: MAX_DELKA_BUNKY - 1] + "…" if len(text) > MAX_DELKA_BUNKY else text

    data = [[bunka(h) for h in radek] for radek in radky]
    sirky = [
        max(len(sloupec), *(len(radek[i]) for radek in data))
        for i, sloupec in enumerate(sloupce)
    ]
    hlavicka = " | ".join(s.ljust(sirky[i]) for i, s in enumerate(sloupce))
    oddelovac = "-+-".join("-" * s for s in sirky)
    telo = "\n".join(
        " | ".join(bunka.ljust(sirky[i]) for i, bunka in enumerate(radek)) for radek in data
    )
    return f"{hlavicka}\n{oddelovac}\n{telo}\n\n({len(radky)} řádků)"


@mcp.tool()
def db_schema() -> str:
    """
    Vrátí strukturu databáze knihovny - CREATE příkazy všech tabulek a počty řádků.
    Zavolej, když si nejsi jistý názvem tabulky nebo sloupce.
    """
    try:
        conn = _spojeni()
    except FileNotFoundError as exc:
        return f"CHYBA: {exc}"
    try:
        tabulky = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'recenze_fts%' ORDER BY name"
        ).fetchall()
        casti = []
        for nazev, sql in tabulky:
            pocet = conn.execute(f"SELECT count(*) FROM {nazev}").fetchone()[0]
            casti.append(f"{sql};\n-- řádků: {pocet}")
    finally:
        conn.close()

    casti.append(
        "-- Fulltextový index nad recenze.text je v tabulce recenze_fts (FTS5).\n"
        "-- Nedotazuj se na něj přes db_dotaz, použij nástroj hledej_v_recenzich."
    )
    return "\n\n".join(casti)


@mcp.tool()
def db_dotaz(sql: str, limit: int = 50) -> str:
    """
    Spustí jeden SELECT nad databází knihovny a vrátí výsledek jako tabulku.

    Args:
        sql: jediný SELECT příkaz v syntaxi SQLite (bez středníku na konci).
             Spojení je otevřené jen pro čtení, zápis skončí chybou.
        limit: kolik řádků nejvýš vrátit (strop je 200).
    """
    prikaz = sql.strip().rstrip(";").strip()
    if not prikaz:
        return "CHYBA: prázdný dotaz."

    try:
        conn = _spojeni()
    except FileNotFoundError as exc:
        return f"CHYBA: {exc}"
    try:
        kurzor = conn.execute(prikaz)  # execute() odmítne víc příkazů najednou
        if kurzor.description is None:
            return "CHYBA: dotaz nevrací žádná data - použij SELECT."
        sloupce = [popis[0] for popis in kurzor.description]
        radky = kurzor.fetchmany(max(1, min(limit, MAX_RADKU)))
        dalsi = kurzor.fetchone() is not None
    except sqlite3.Error as exc:
        return f"CHYBA SQL: {exc}"
    finally:
        conn.close()

    vysledek = _tabulka(sloupce, radky)
    if dalsi:
        vysledek += "\n(výsledek je oříznutý na limit - zpřesni dotaz nebo použij agregaci)"
    return vysledek


@mcp.tool()
def hledej_v_recenzich(dotaz: str, limit: int = 8) -> str:
    """
    Fulltextově prohledá texty čtenářských recenzí (SQLite FTS5, řazeno podle bm25).

    Hledá se bez ohledu na diakritiku. Podporuje syntaxi FTS5: hvězdička jako
    předpona ("preklad*"), operátory OR / NOT, uvozovky pro frázi.
    Použij pro dotazy na to, co čtenáři o knihách píšou - obsah recenzí se
    přes db_dotaz rozumně prohledat nedá.

    Args:
        dotaz: hledaný výraz, např. 'preklad*' nebo '"cteci klub" OR audiokniha'.
        limit: kolik recenzí vrátit (nejvýš 20).
    """
    if not dotaz.strip():
        return "CHYBA: prázdný dotaz."
    try:
        conn = _spojeni()
    except FileNotFoundError as exc:
        return f"CHYBA: {exc}"
    try:
        radky = conn.execute(
            """
            SELECT k.nazev, a.jmeno, c.jmeno, r.hodnoceni, r.datum, r.text
            FROM recenze_fts f
            JOIN recenze  r ON r.id = f.rowid
            JOIN knihy    k ON k.id = r.kniha_id
            JOIN autori   a ON a.id = k.autor_id
            JOIN ctenari  c ON c.id = r.ctenar_id
            WHERE recenze_fts MATCH ?
            ORDER BY bm25(recenze_fts)
            LIMIT ?
            """,
            (dotaz, max(1, min(limit, 20))),
        ).fetchall()
    except sqlite3.Error as exc:
        return f"CHYBA FTS dotazu: {exc}"
    finally:
        conn.close()

    if not radky:
        return f"Pro výraz '{dotaz}' nemá žádná recenze shodu."

    return "\n\n".join(
        f"{nazev} ({autor}) - {hodnoceni}/5, {ctenar}, {datum}\n  {text}"
        for nazev, autor, ctenar, hodnoceni, datum, text in radky
    )


# ---------------------------------------------------------------------------
# Wikipedia
# ---------------------------------------------------------------------------


def _wikipedia_api(jazyk: str, parametry: dict) -> dict:
    jazyk = jazyk.lower().strip() or "cs"
    if jazyk not in {"cs", "en", "sk", "de", "pl"}:
        raise ValueError(f"nepodporovaný jazyk '{jazyk}' (povolené: cs, en, sk, de, pl)")
    odpoved = httpx.get(
        f"https://{jazyk}.wikipedia.org/w/api.php",
        params={"format": "json", "formatversion": "2", **parametry},
        headers={"User-Agent": "aiag-hmwrk3-agent/1.0 (vyukovy projekt)"},
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
    )
    odpoved.raise_for_status()
    return odpoved.json()


@mcp.tool()
def wikipedia_hledej(dotaz: str, jazyk: str = "cs", limit: int = 3) -> str:
    """
    Najde na Wikipedii články odpovídající dotazu a u každého vrátí úvod článku.

    U jednoduchých faktů (kdo to byl, kdy se narodil, jaké dostal ceny) obvykle
    stačí tenhle nástroj a wikipedia_clanek už volat nemusíš. Když v úvodu
    hledaný údaj není, dotáhni celý článek nástrojem wikipedia_clanek.

    Args:
        dotaz: hledaný výraz, např. 'Karel Čapek'.
        jazyk: jazyková verze - cs, en, sk, de nebo pl (výchozí cs).
        limit: kolik článků vrátit (nejvýš 5).
    """
    try:
        # generator=search + prop=extracts: jedním voláním se dá dohromady
        # výsledek hledání a úvodní odstavec, ne jen útržek z prostředka článku
        data = _wikipedia_api(
            jazyk,
            {
                "action": "query",
                "generator": "search",
                "gsrsearch": dotaz,
                "gsrlimit": max(1, min(limit, 5)),
                "prop": "extracts",
                "exintro": "1",
                "explaintext": "1",
                "exsentences": "3",
            },
        )
    except ValueError as exc:
        return f"CHYBA: {exc}"
    except httpx.HTTPError as exc:
        return f"CHYBA: Wikipedii se nepodařilo zavolat ({exc})."

    stranky = data.get("query", {}).get("pages", [])
    if not stranky:
        return f"Na {jazyk}.wikipedia.org nic pro '{dotaz}' nenašlo."

    # generator vrací stránky v náhodném pořadí, "index" drží pořadí z hledání
    stranky.sort(key=lambda s: s.get("index", 0))
    return "\n\n".join(
        f"## {stranka['title']}\n{' '.join((stranka.get('extract') or '(bez úvodu)').split())}"
        for stranka in stranky
    )


@mcp.tool()
def wikipedia_clanek(nazev: str, jazyk: str = "cs", max_znaku: int = 2500) -> str:
    """
    Vrátí text článku z Wikipedie jako čistý text (přesměrování se následují).

    Args:
        nazev: přesný název článku, ideálně z nástroje wikipedia_hledej.
        jazyk: jazyková verze - cs, en, sk, de nebo pl (výchozí cs).
        max_znaku: na kolik znaků text zkrátit (nejvýš 8000; úvod článku
                   obvykle stačí, delší text zbytečně plní kontext).
    """
    try:
        data = _wikipedia_api(
            jazyk,
            {
                "action": "query",
                "prop": "extracts",
                "explaintext": "1",
                "redirects": "1",
                "titles": nazev,
            },
        )
    except ValueError as exc:
        return f"CHYBA: {exc}"
    except httpx.HTTPError as exc:
        return f"CHYBA: Wikipedii se nepodařilo zavolat ({exc})."

    stranky = data.get("query", {}).get("pages", [])
    if not stranky or stranky[0].get("missing"):
        return (
            f"Článek '{nazev}' na {jazyk}.wikipedia.org neexistuje. "
            "Zkus nejdřív wikipedia_hledej, nebo jinou jazykovou verzi."
        )

    stranka = stranky[0]
    text = (stranka.get("extract") or "").strip()
    if not text:
        return f"Článek '{stranka['title']}' nemá textový výtah."

    strop = max(200, min(max_znaku, 8000))
    zkraceno = len(text) > strop
    return (
        f"# {stranka['title']} ({jazyk}.wikipedia.org)\n\n"
        + text[:strop]
        + ("\n\n… (text zkrácen)" if zkraceno else "")
    )


# ---------------------------------------------------------------------------
# Zápis výstupu
# ---------------------------------------------------------------------------


@mcp.tool()
def zapis_report(nazev_souboru: str, obsah: str) -> str:
    """
    Uloží text (typicky markdown report) do souboru ve složce out/.

    Jediný nástroj, který něco zapisuje. Použij ho, až když je report hotový -
    ne na ukládání mezivýsledků.

    Args:
        nazev_souboru: jméno souboru bez cesty, např. 'nejpujcovanejsi-2026.md'.
        obsah: celý obsah souboru.
    """
    jmeno = Path(nazev_souboru.strip()).name  # zahodí ../ i absolutní cestu
    jmeno = re.sub(r"[^\w.\- ]", "_", jmeno).strip()
    if not jmeno or jmeno.startswith("."):
        return "CHYBA: neplatné jméno souboru."
    if not jmeno.lower().endswith((".md", ".txt", ".csv")):
        jmeno += ".md"

    OUT_ADRESAR.mkdir(parents=True, exist_ok=True)
    cesta = OUT_ADRESAR / jmeno
    cesta.write_text(obsah, encoding="utf-8")
    return f"Uloženo do {cesta} ({len(obsah)} znaků)."


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.settings.host = os.environ.get("MCP_HOST", "127.0.0.1")
        mcp.settings.port = int(os.environ.get("MCP_PORT", "8010"))
        print(
            f"MCP server běží na http://{mcp.settings.host}:{mcp.settings.port}/mcp",
            file=sys.stderr,
        )
        mcp.run(transport="streamable-http")
    else:
        # stdio: server komunikuje po stdin/stdout, takže na stdout nesmí nic jiného
        mcp.run(transport="stdio")
