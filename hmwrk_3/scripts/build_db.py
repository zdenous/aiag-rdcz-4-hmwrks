"""
Postaví SQLite databázi data/knihovna.db ze souborů db/01_schema.sql a
db/02_seed.sql a naplní fulltextový index nad recenzemi.

    uv run scripts/build_db.py

Skript je idempotentní - schéma tabulky nejdřív zahodí, takže opakované
spuštění vrátí databázi do výchozího stavu (třeba po experimentech agenta).
"""

import sqlite3
import sys
from pathlib import Path

KOREN = Path(__file__).resolve().parent.parent
DB = KOREN / "data" / "knihovna.db"
SQL_SOUBORY = [KOREN / "db" / "01_schema.sql", KOREN / "db" / "02_seed.sql"]


def main() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB)
    try:
        for soubor in SQL_SOUBORY:
            if not soubor.exists():
                sys.exit(f"Chybí {soubor}")
            conn.executescript(soubor.read_text(encoding="utf-8"))
            print(f"  nahráno: {soubor.relative_to(KOREN)}")

        # FTS tabulka s content='recenze' se neplní sama - index se postaví až
        # tímhle příkazem, po něm zná všechny existující řádky.
        conn.execute("INSERT INTO recenze_fts (recenze_fts) VALUES ('rebuild')")
        conn.commit()

        pocty = {
            tabulka: conn.execute(f"SELECT count(*) FROM {tabulka}").fetchone()[0]
            for tabulka in ("autori", "knihy", "ctenari", "vypujcky", "recenze")
        }
    finally:
        conn.close()

    print(f"\nHotovo: {DB.relative_to(KOREN)}")
    print("  " + ", ".join(f"{t} {p}" for t, p in pocty.items()))


if __name__ == "__main__":
    main()
