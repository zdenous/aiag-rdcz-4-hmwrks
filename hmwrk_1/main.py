"""
Úkol 1 - LLM API + volání nástroje (function/tool calling).

Skript:
  1. zavolá LLM API s dotazem uživatele a se seznamem dostupných nástrojů,
  2. LLM se rozhodne zavolat nástroj (výpočetní funkci) a vrátí tool_call,
  3. skript nástroj lokálně vykoná,
  4. výsledek nástroje pošle zpět LLM,
  5. LLM z výsledku sestaví finální odpověď v přirozeném jazyce.

Kroky 1-4 běží ve smyčce, takže model může zavolat i více nástrojů za sebou.

Provider je nastavitelný přes .env (LiteLLM), kód se nemění. Výchozí je
free tier OpenRouteru, takže úkol jde spustit bez placeného API klíče:
    MODEL=openrouter/openai/gpt-oss-20b:free    + OPENROUTER_API_KEY
    MODEL=openai/gpt-4o-mini                    + OPENAI_API_KEY
    MODEL=anthropic/claude-sonnet-4-5           + ANTHROPIC_API_KEY
    MODEL=ollama/llama3.2                       + API_BASE=http://localhost:11434

Pozor: na OpenRouteru musí mít vybraný ":free" model podporu tool callingu
(viz README) - jinak model nástroj nikdy nezavolá.
"""

import ast
import json
import operator
import os
import sys
import time
from typing import Any, Callable, Dict, List

import litellm
from dotenv import load_dotenv

load_dotenv()

# Bez tohohle vypisuje LiteLLM ke každé zachycené chybě odstavec reklamy na
# vlastní GitHub issues, což při retry úplně zahltí výstup.
litellm.suppress_debug_info = True

MODEL = os.environ.get("MODEL", "openrouter/openai/gpt-oss-20b:free")
API_BASE = os.environ.get("API_BASE")  # jen pro lokální providery (Ollama, LM Studio)
MAX_ITERATIONS = 10

# Free modely na OpenRouteru sdílí kapacitu s ostatními uživateli, takže občas
# vrátí HTTP 429 ("temporarily rate-limited upstream"). Nejdřív to zkusíme
# přečkat krátkým čekáním, a když model pořád nedává, přepneme na náhradní.
# Všechny modely v seznamu musí umět tool calling (viz README).
FALLBACK_MODELS = [
    m.strip()
    for m in os.environ.get(
        "FALLBACK_MODELS",
        "openrouter/z-ai/glm-5.2:free,openrouter/google/gemma-4-31b-it:free",
    ).split(",")
    if m.strip()
]
MAX_RETRIES = 3  # pokusů na jeden model, než se přejde na další
RETRY_BACKOFF_SECONDS = 3  # 3s, 6s, 12s, ...

# Model, který naposledy odpověděl. Když je hlavní model zablokovaný, nemá smysl
# ho zkoušet v každé iteraci znovu - držíme se toho, co funguje.
_active_model: str | None = None

SYSTEM_PROMPT = (
    "Jsi pečlivý asistent. Na jakýkoli početní úkol VŽDY použij dostupné nástroje, "
    "nikdy nepočítej zpaměti. Výsledek nástroje stručně vysvětli v češtině."
)


# ---------------------------------------------------------------------------
# 1) Implementace nástrojů (běží lokálně, ne v LLM)
# ---------------------------------------------------------------------------

# Povolené uzly a operátory pro bezpečné vyhodnocení výrazu.
# Záměrně se nepoužívá eval() - LLM může poslat libovolný řetězec.
_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](
            _eval_node(node.left), _eval_node(node.right)
        )
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](_eval_node(node.operand))
    raise ValueError(f"Nepovolený výraz: {ast.dump(node)}")


def calculate(expression: str) -> Dict[str, Any]:
    """Bezpečně vyhodnotí aritmetický výraz, např. '(12.5 * 4) ** 2 / 7'."""
    try:
        result = _eval_node(ast.parse(expression, mode="eval"))
    except ZeroDivisionError:
        return {"expression": expression, "error": "dělení nulou"}
    except (ValueError, SyntaxError, TypeError) as exc:
        return {"expression": expression, "error": str(exc)}
    return {"expression": expression, "result": result}


def compound_interest(
    principal: float,
    annual_rate_pct: float,
    years: float,
    compounds_per_year: int = 1,
) -> Dict[str, Any]:
    """Spočítá hodnotu investice se složeným úrokem."""
    if compounds_per_year <= 0:
        return {"error": "compounds_per_year musí být kladné číslo"}

    rate = annual_rate_pct / 100.0
    periods = compounds_per_year * years
    final_amount = principal * (1 + rate / compounds_per_year) ** periods

    return {
        "principal": principal,
        "annual_rate_pct": annual_rate_pct,
        "years": years,
        "compounds_per_year": compounds_per_year,
        "final_amount": round(final_amount, 2),
        "interest_earned": round(final_amount - principal, 2),
    }


# ---------------------------------------------------------------------------
# 2) Popis nástrojů pro LLM (standardní OpenAI schéma, LiteLLM ho přeloží)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": (
                "Vyhodnotí aritmetický výraz a vrátí přesný výsledek. "
                "Použij pro jakýkoli výpočet (+, -, *, /, //, %, **)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Výraz v Python syntaxi, např. '(1250 * 3.5) / 7'",
                    }
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compound_interest",
            "description": "Spočítá konečnou hodnotu investice se složeným úrokem.",
            "parameters": {
                "type": "object",
                "properties": {
                    "principal": {
                        "type": "number",
                        "description": "Počáteční vložená částka",
                    },
                    "annual_rate_pct": {
                        "type": "number",
                        "description": "Roční úroková sazba v procentech, např. 5.5",
                    },
                    "years": {"type": "number", "description": "Doba investice v letech"},
                    "compounds_per_year": {
                        "type": "integer",
                        "description": "Počet připisování úroku za rok (1 = ročně, 12 = měsíčně)",
                    },
                },
                "required": ["principal", "annual_rate_pct", "years"],
            },
        },
    },
]

AVAILABLE_FUNCTIONS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "calculate": calculate,
    "compound_interest": compound_interest,
}


# ---------------------------------------------------------------------------
# 3) Smyčka LLM <-> nástroje
# ---------------------------------------------------------------------------


def _completion(model: str, messages: List[Dict[str, Any]]):
    """Jedno volání LLM API se seznamem nástrojů."""
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",  # model se sám rozhodne, zda nástroj potřebuje
    }
    if API_BASE:
        kwargs["api_base"] = API_BASE
    return litellm.completion(**kwargs)


def call_llm(messages: List[Dict[str, Any]]):
    """
    Volání LLM odolné vůči zahlcení free tieru.

    Postup: model zkusí MAX_RETRIES krát s prodlužující se pauzou, a pokud
    provider pořád vrací chybu (typicky 429 ze sdíleného poolu), přejde na další
    model z FALLBACK_MODELS. Model, který uspěl, se použije i v dalších
    iteracích. Vrací dvojici (odpověď, model který ji vrátil).
    """
    global _active_model

    candidates = [MODEL] + [m for m in FALLBACK_MODELS if m != MODEL]
    if _active_model in candidates:  # osvědčený model zkusíme první
        candidates.remove(_active_model)
        candidates.insert(0, _active_model)

    last_error: Exception | None = None

    for model in candidates:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = _completion(model, messages)
                _active_model = model
                return response, model
            except litellm.AuthenticationError:
                # Špatný / chybějící klíč - opakování ani jiný model nepomůže.
                raise
            except Exception as exc:
                # Kromě RateLimitError sem spadne i cokoli dalšího dočasného
                # (výpadek providera, timeout, chyba uvnitř LiteLLM).
                last_error = exc
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF_SECONDS * 2 ** (attempt - 1)
                    print(
                        f"  {model}: {type(exc).__name__}, pokus {attempt}/{MAX_RETRIES}"
                        f" - čekám {wait}s a zkouším znovu"
                    )
                    time.sleep(wait)
                else:
                    print(
                        f"  {model}: {type(exc).__name__} i po {MAX_RETRIES}"
                        " pokusech, zkouším další model"
                    )

    raise RuntimeError(
        "Žádný z modelů neodpověděl (zkoušeno: "
        + ", ".join(candidates)
        + f").\nPoslední chyba: {last_error}"
    )


def run(question: str) -> str:
    """Zeptá se LLM, obslouží volání nástrojů a vrátí finální odpověď."""
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    print(f"\n=== Dotaz: {question} ===")

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"\n--- Iterace {iteration}: volám LLM ---")
        response, model_used = call_llm(messages)
        response_message = response.choices[0].message
        print(f"  odpověděl model: {model_used}")

        # Bez tool_calls je odpověď finální -> konec smyčky.
        if not response_message.tool_calls:
            final_answer = response_message.content
            messages.append({"role": "assistant", "content": final_answer})
            print(f"\nFinální odpověď LLM:\n{final_answer}")
            return final_answer

        # Do historie patří i zpráva asistenta s požadavky na nástroje.
        messages.append(
            {
                "role": "assistant",
                "content": response_message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in response_message.tool_calls
                ],
            }
        )

        # Vykonáme všechny požadované nástroje a výsledky vrátíme zpět LLM.
        for tool_call in response_message.tool_calls:
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments or "{}")
            print(f"LLM chce zavolat nástroj: {name}({args})")

            function_to_call = AVAILABLE_FUNCTIONS.get(name)
            if function_to_call is None:
                result: Dict[str, Any] = {"error": f"neznámý nástroj '{name}'"}
            else:
                try:
                    result = function_to_call(**args)
                except TypeError as exc:
                    result = {"error": f"špatné argumenty: {exc}"}

            print(f"Výsledek nástroje: {result}")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    return "Chyba: vyčerpán maximální počet iterací bez finální odpovědi."


DEMO_QUESTIONS = [
    "Kolik je (1287 * 43) - 9856 děleno 4?",
    "Když si dnes uložím 250000 Kč na 4,8 % ročně s měsíčním připisováním úroku, "
    "kolik budu mít za 7 let a kolik z toho je úrok?",
]


def main() -> None:
    # Srozumitelná hláška místo authentication erroru z hloubi knihovny.
    if MODEL.startswith("openrouter/") and not os.environ.get("OPENROUTER_API_KEY"):
        sys.exit(
            "Chybí OPENROUTER_API_KEY.\n"
            "1) zdarma se registruj na https://openrouter.ai a vytvoř klíč,\n"
            "2) `cp .env.example .env` a klíč do .env doplň."
        )

    print(f"Model: {MODEL}" + (f" (api_base={API_BASE})" if API_BASE else ""))

    # Vlastní dotaz: `uv run main.py "Kolik je 17 na třetí?"`
    questions = sys.argv[1:] or DEMO_QUESTIONS
    for question in questions:
        try:
            run(question)
        except RuntimeError as exc:  # všechny modely nedostupné
            sys.exit(f"\n{exc}\nZkus to za chvíli znovu nebo nastav jiný MODEL v .env.")


if __name__ == "__main__":
    main()
