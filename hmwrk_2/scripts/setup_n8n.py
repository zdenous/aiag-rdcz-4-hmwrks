#!/usr/bin/env python3
"""Nahraje workflow do n8n přes veřejné API a připojí k němu credentials.

Bez závislostí (jen standardní knihovna), spouští se `python3 scripts/setup_n8n.py`.
Konfigurace se čte z `.env` vedle tohoto adresáře (vzor v `.env.example`).

Skript:
  1. vytvoří v n8n credential pro Postgres a pro OpenRouter (typ openAiApi),
  2. do `workflow.json` doplní jejich ID,
  3. workflow vytvoří (nebo aktualizuje, pokud už stejný název existuje),
  4. aktivuje ho, aby fungoval produkční webhook.

Vytvořená ID si pamatuje v `.n8n_state.json`, takže opakované spuštění
nezakládá credentials znovu.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / ".n8n_state.json"
WORKFLOW_FILE = ROOT / "workflow.json"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k in env or k.startswith(("N8N_", "PG_", "OPENROUTER_"))})
    return env


def require(env: dict[str, str], key: str) -> str:
    value = env.get(key, "")
    if not value or value.upper().startswith("CHANGE_ME"):
        sys.exit(f"CHYBA: v .env chybí {key} (vzor je v .env.example)")
    return value


def api(env: dict[str, str], method: str, path: str, payload: dict | None = None):
    url = env.get("N8N_URL", "http://localhost:5678").rstrip("/") + "/api/v1" + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-N8N-API-KEY", require(env, "N8N_API_KEY"))
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"CHYBA: {method} {path} -> HTTP {exc.code}\n{detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"CHYBA: n8n na {url} neodpovídá ({exc.reason}). Běží kontejner?") from exc


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def ensure_credential(env, state, key: str, name: str, cred_type: str, data: dict) -> str:
    if key in state:
        print(f"  credential {name}: už existuje (id {state[key]})")
        return state[key]
    created = api(env, "POST", "/credentials", {"name": name, "type": cred_type, "data": data})
    state[key] = created["id"]
    save_state(state)
    print(f"  credential {name}: vytvořen (id {created['id']})")
    return created["id"]


def main() -> None:
    env = load_env()
    state = load_state()

    print("1/4 Zakládám credentials v n8n")
    pg_id = ensure_credential(
        env, state, "postgres_credential_id", "Postgres eshop (Supabase)", "postgres",
        {
            "host": require(env, "PG_HOST"),
            "database": env.get("PG_DATABASE", "postgres"),
            "user": require(env, "PG_USER"),
            "password": require(env, "PG_PASSWORD"),
            "port": int(env.get("PG_PORT", "5432")),
            "ssl": env.get("PG_SSL", "require"),
            "allowUnauthorizedCerts": env.get("PG_ALLOW_UNAUTHORIZED", "true").lower() == "true",
            "maxConnections": 10,
        },
    )
    llm_id = ensure_credential(
        env, state, "openrouter_credential_id", "OpenRouter", "openAiApi",
        {"apiKey": require(env, "OPENROUTER_API_KEY"), "url": "https://openrouter.ai/api/v1"},
    )

    print("2/4 Připravuji workflow.json")
    workflow = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    model = env.get("OPENROUTER_MODEL", "").strip()
    for node in workflow["nodes"]:
        creds = node.get("credentials", {})
        if "postgres" in creds:
            creds["postgres"]["id"] = pg_id
        if "openAiApi" in creds:
            creds["openAiApi"]["id"] = llm_id
        if model and node["type"].endswith("lmChatOpenAi"):
            node["parameters"]["model"]["value"] = model
            print(f"  model přepsán na {model}")

    payload = {
        "name": workflow["name"],
        "nodes": workflow["nodes"],
        "connections": workflow["connections"],
        "settings": workflow.get("settings", {"executionOrder": "v1"}),
    }

    print("3/4 Nahrávám workflow")
    existing = api(env, "GET", "/workflows?limit=250").get("data", [])
    match = next((w for w in existing if w["name"] == workflow["name"]), None)
    if match:
        result = api(env, "PUT", f"/workflows/{match['id']}", payload)
        print(f"  aktualizováno (id {result['id']})")
    else:
        result = api(env, "POST", "/workflows", payload)
        print(f"  vytvořeno (id {result['id']})")
    workflow_id = result["id"]

    print("4/4 Aktivuji workflow")
    api(env, "POST", f"/workflows/{workflow_id}/activate")

    base = env.get("N8N_URL", "http://localhost:5678").rstrip("/")
    print("\nHotovo.")
    print(f"  editor:  {base}/workflow/{workflow_id}")
    print(f"  webhook: {base}/webhook/eshop-agent")
    print("\nTest:  python3 scripts/test_agent.py")


if __name__ == "__main__":
    main()
