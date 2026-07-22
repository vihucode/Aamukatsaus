"""LLM provider abstraction: anthropic (default) | gemini | groq.

All providers are called over plain REST so the dependency footprint stays
at `requests`. `complete()` returns text; `complete_json()` returns a parsed
dict with fence-stripping, brace-extraction and one repair round-trip.

If the configured provider fails all retries, any other provider with an
API key present in the environment is tried before giving up — a single
LLM outage should not kill the nightly episode when a fallback key exists.
"""
from __future__ import annotations

import json
import os
import re
import time

import requests

from src.config import CFG, log

STAGE = "llm"
TIMEOUT = 120
RETRIES = 3

DEFAULT_MODELS = {
    "anthropic": CFG["llm"].get("model", "claude-haiku-4-5"),
    "gemini": "gemini-2.5-flash",
    "groq": "llama-3.3-70b-versatile",
}
KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
}


def _provider() -> str:
    return (os.environ.get("LLM_PROVIDER") or CFG["llm"].get("provider") or "anthropic").lower()


def _key(provider: str) -> str | None:
    return os.environ.get(KEY_ENV[provider]) or None


def _post(url: str, *, headers: dict, payload: dict) -> dict:
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
            if r.status_code in (429, 500, 502, 503, 504, 529):
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 — retry any transport/API hiccup
            last = e
            if attempt < RETRIES:
                wait = 2 ** attempt
                log(STAGE, f"attempt {attempt} failed ({e}); retrying in {wait}s")
                time.sleep(wait)
    raise RuntimeError(f"LLM request failed after {RETRIES} attempts: {last}")


def _call_anthropic(model: str, system: str, user: str, max_tokens: int,
                    temperature: float, json_mode: bool) -> str:
    if json_mode:
        system += "\n\nRespond with a single valid JSON object and nothing else — no markdown fences, no commentary."
    data = _post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": _key("anthropic"),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        payload={
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
    )
    return "".join(b.get("text", "") for b in data.get("content", []))


def _call_gemini(model: str, system: str, user: str, max_tokens: int,
                 temperature: float, json_mode: bool) -> str:
    gen: dict = {"maxOutputTokens": max_tokens, "temperature": temperature}
    if json_mode:
        gen["responseMimeType"] = "application/json"
    data = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": _key("gemini"), "content-type": "application/json"},
        payload={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen,
        },
    )
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


def _call_groq(model: str, system: str, user: str, max_tokens: int,
               temperature: float, json_mode: bool) -> str:
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    data = _post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {_key('groq')}", "content-type": "application/json"},
        payload=payload,
    )
    return data["choices"][0]["message"]["content"]


_CALLERS = {"anthropic": _call_anthropic, "gemini": _call_gemini, "groq": _call_groq}


def complete(system: str, user: str, max_tokens: int = 4000,
             json_mode: bool = False, temperature: float = 0.7) -> str:
    primary = _provider()
    chain = [primary] + [p for p in _CALLERS if p != primary and _key(p)]
    if not _key(primary):
        chain = chain[1:]
        log(STAGE, f"no API key for configured provider '{primary}' "
                   f"({KEY_ENV[primary]}); falling back to: {chain or 'none'}")
    if not chain:
        raise RuntimeError(
            "No LLM API key found. Set one of: " + ", ".join(KEY_ENV.values()))
    errors = []
    for provider in chain:
        model = DEFAULT_MODELS[provider]
        try:
            text = _CALLERS[provider](model, system, user, max_tokens, temperature, json_mode)
            if provider != primary:
                log(STAGE, f"NOTE: answered by fallback provider {provider}/{model}")
            return text
        except Exception as e:  # noqa: BLE001
            errors.append(f"{provider}: {e}")
            log(STAGE, f"provider {provider} failed: {e}")
    raise RuntimeError("All LLM providers failed: " + " | ".join(errors))


def extract_json(text: str) -> dict:
    """Parse a JSON object out of model text, tolerating fences and prose."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def complete_json(system: str, user: str, max_tokens: int = 4000,
                  temperature: float = 0.3) -> dict:
    text = complete(system, user, max_tokens=max_tokens, json_mode=True,
                    temperature=temperature)
    try:
        return extract_json(text)
    except json.JSONDecodeError:
        log(STAGE, "invalid JSON from model; asking it to repair")
        fixed = complete(
            "You fix malformed JSON. Return only the corrected JSON object, nothing else.",
            f"Fix this so it parses as strict JSON:\n\n{text[:20000]}",
            max_tokens=max_tokens, json_mode=True, temperature=0.0)
        return extract_json(fixed)
