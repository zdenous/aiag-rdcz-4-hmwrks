#!/usr/bin/env python3
"""Pošle testovací dotazy na produkční webhook agenta a vypíše odpovědi.

    python3 scripts/test_agent.py                 # sada ukázkových dotazů
    python3 scripts/test_agent.py "vlastní dotaz" # jeden vlastní dotaz
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

UKAZKOVE_DOTAZY = [
    "Kolik máme zákazníků a kolik z nich je VIP?",
    "Které tři produkty nám vydělaly nejvíc peněz? Počítej z položek objednávek.",
    "Kolik jsme celkem utržili za objednávky z července 2026?",
    "Máme skladem něco od značky Vela?",
]


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def zeptej_se(base: str, dotaz: str, session: str) -> dict:
    url = base.rstrip("/") + "/webhook/eshop-agent"
    payload = json.dumps({"dotaz": dotaz, "session_id": session}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"chyba": f"HTTP {exc.code}", "detail": exc.read().decode("utf-8", "replace")[:800]}
    except urllib.error.URLError as exc:
        return {"chyba": str(exc.reason)}


def main() -> None:
    env = load_env()
    base = env.get("N8N_URL", "http://localhost:5678")
    dotazy = sys.argv[1:] or UKAZKOVE_DOTAZY
    session = f"test-{int(time.time())}"

    for i, dotaz in enumerate(dotazy, start=1):
        print(f"\n=== {i}/{len(dotazy)}: {dotaz}")
        start = time.time()
        odpoved = zeptej_se(base, dotaz, session)
        trvani = time.time() - start
        if "chyba" in odpoved:
            print(f"  CHYBA ({trvani:.1f}s): {odpoved['chyba']}\n  {odpoved.get('detail', '')}")
        else:
            print(f"  ({trvani:.1f}s)\n{odpoved.get('odpoved', odpoved)}")


if __name__ == "__main__":
    main()
