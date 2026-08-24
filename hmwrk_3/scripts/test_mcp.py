"""
Kontrola MCP serveru bez LLM - ověří, že nástroje odpovídají a že databáze
opravdu nejde přes agenta měnit.

    uv run scripts/test_mcp.py

Klient je tady napsaný přímo nad MCP SDK, ne přes LangChain: server je na
frameworku nezávislý a takhle je to vidět.
"""

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

KOREN = Path(__file__).resolve().parent.parent

# (nástroj, argumenty, co musí být ve výsledku)
PRIPADY = [
    ("db_schema", {}, "CREATE TABLE knihy"),
    ("db_dotaz", {"sql": "SELECT count(*) AS n FROM knihy"}, "63"),
    (
        "db_dotaz",
        {
            "sql": "SELECT k.nazev, count(*) n FROM vypujcky v JOIN knihy k ON k.id = v.kniha_id"
                   " GROUP BY k.id ORDER BY n DESC LIMIT 1"
        },
        "Babička",
    ),
    ("hledej_v_recenzich", {"dotaz": "poskozen* OR vlhk*"}, "výtisk"),
    ("wikipedia_hledej", {"dotaz": "Karel Čapek", "limit": 2}, "Čapek"),
    ("wikipedia_clanek", {"nazev": "Toni Morrisonová", "max_znaku": 400}, "spisovatelka"),
    ("zapis_report", {"nazev_souboru": "test-mcp.md", "obsah": "# test\n"}, "Uloženo"),
    # zápis do databáze musí selhat, ať ho model napíše jakkoli
    ("db_dotaz", {"sql": "DELETE FROM recenze"}, "readonly"),
    ("db_dotaz", {"sql": "UPDATE knihy SET cena_kc = 0"}, "readonly"),
    ("db_dotaz", {"sql": "INSERT INTO autori (jmeno, zeme) VALUES ('X', 'Y')"}, "readonly"),
    ("db_dotaz", {"sql": "DROP TABLE knihy"}, "readonly"),
    ("db_dotaz", {"sql": "SELECT 1; DROP TABLE knihy"}, "one statement"),
    # pokus vylézt ze složky out/ musí skončit uvnitř ní
    ("zapis_report", {"nazev_souboru": "../../uteklo.md", "obsah": "x"}, "/out/uteklo.md"),
]


async def main() -> int:
    parametry = StdioServerParameters(
        command=sys.executable, args=[str(KOREN / "mcp_server" / "server.py")]
    )
    chyby = 0
    async with stdio_client(parametry) as (cti, piš):
        async with ClientSession(cti, piš) as session:
            await session.initialize()

            nastroje = (await session.list_tools()).tools
            print(f"Server nabízí {len(nastroje)} nástrojů:")
            for n in nastroje:
                print(f"  - {n.name}({', '.join(n.inputSchema.get('properties', {}))})")
            print()

            for nazev, argumenty, ocekavano in PRIPADY:
                vysledek = await session.call_tool(nazev, argumenty)
                text = " ".join(
                    blok.text for blok in vysledek.content if blok.type == "text"
                )
                ok = ocekavano.lower() in text.lower()
                chyby += not ok
                popis = ", ".join(f"{k}={v!r}" for k, v in argumenty.items())
                print(f"[{'OK ' if ok else 'CHYBA'}] {nazev}({popis[:70]})")
                if not ok:
                    print(f"         čekal jsem {ocekavano!r}, dostal: {text[:200]}")

    (KOREN / "out" / "test-mcp.md").unlink(missing_ok=True)
    (KOREN / "out" / "uteklo.md").unlink(missing_ok=True)
    print("\n" + ("Vše prošlo." if not chyby else f"Neprošlo: {chyby}"))
    return 1 if chyby else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
