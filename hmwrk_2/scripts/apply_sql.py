#!/usr/bin/env python3
"""Nahraje schéma a data do databáze z .env (Supabase i lokální Postgres).

    uv run --with "psycopg[binary]" scripts/apply_sql.py

U Supabase jde totéž udělat ručně: obsah db/01_schema.sql a db/02_seed.sql
zkopírovat do SQL Editoru a spustit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
SOUBORY = ["db/01_schema.sql", "db/02_seed.sql"]


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_file = ROOT / ".env"
    if not env_file.exists():
        sys.exit("CHYBA: chybí .env (zkopíruj .env.example)")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main() -> None:
    env = load_env()
    host = env.get("PG_HOST", "localhost")
    # skripty běží na hostu, ne v kontejneru
    if host == "host.docker.internal":
        host = "localhost"
    conninfo = (
        f"host={host} port={env.get('PG_PORT', '5432')} dbname={env.get('PG_DATABASE', 'postgres')} "
        f"user={env.get('PG_USER', 'postgres')} password={env.get('PG_PASSWORD', '')} "
        f"sslmode={'require' if env.get('PG_SSL', 'require') == 'require' else 'disable'}"
    )
    print(f"Připojuji se na {host}:{env.get('PG_PORT', '5432')}/{env.get('PG_DATABASE', 'postgres')}")
    with psycopg.connect(conninfo, connect_timeout=15) as conn:
        for soubor in SOUBORY:
            sql = (ROOT / soubor).read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
            print(f"  {soubor}: OK")
        with conn.cursor() as cur:
            for tabulka in ("kategorie", "zakaznici", "produkty", "objednavky", "polozky_objednavky", "tikety"):
                cur.execute(f"SELECT count(*) FROM {tabulka}")
                print(f"  {tabulka}: {cur.fetchone()[0]} řádků")


if __name__ == "__main__":
    main()
