"""
LLM provider layer. Every provider is dispatched explicitly by key: the selected
provider is the only destination text can be sent to (v0.9.5 always called Gemini
regardless of the dropdown).
"""
from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

GEMINI, OPENAI, ANTHROPIC, CUSTOM = "gemini", "openai", "anthropic", "custom"

PROVIDERS = {
    GEMINI: "Google Gemini",
    OPENAI: "OpenAI",
    ANTHROPIC: "Anthropic Claude",
    CUSTOM: "Custom / Local LLM",
}

DEFAULT_MODELS = {
    GEMINI: "gemini-3.5-flash-lite",
    OPENAI: "gpt-4o",
    ANTHROPIC: "claude-sonnet-5-5",
    CUSTOM: "llama3.1",
}

OPENAI_URL = "https://api.openai.com/v1"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

BASE_SYSTEM_PROMPT = (
    "You are an executive assistant analysing text that has been privacy-sanitised. "
    "Sensitive values were replaced by placeholders such as <PERSON_1>, <EMAIL_ADDRESS_2> or "
    "<PRESET_1>. Reproduce every placeholder EXACTLY as written, including the angle brackets, "
    "and never guess, invent or complete the real value behind a placeholder. "
    "Give a clear, structured, concise answer."
)


class ProviderError(Exception):
    def __init__(self, message, error_class="ProviderError", status=None):
        super().__init__(message)
        self.error_class = error_class
        self.status = status


@dataclass
class Destination:
    provider: str
    label: str
    host: str
    model: str
    is_local: bool


def is_loopback_host(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    if host in ("localhost", "ip6-localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    try:  # e.g. "localhost.localdomain": resolve and require every address to be loopback
        infos = socket.getaddrinfo(host, None)
        return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_loopback for i in infos)
    except Exception:
        return False


def describe_destination(provider: str, model: str, custom_url: str = "") -> Destination:
    if provider == CUSTOM:
        host = urlparse(custom_url).hostname or "(not set)"
        return Destination(provider, PROVIDERS[CUSTOM], host, model, is_loopback_host(host))
    host = {GEMINI: "generativelanguage.googleapis.com",
            OPENAI: "api.openai.com", ANTHROPIC: "api.anthropic.com"}[provider]
    return Destination(provider, PROVIDERS[provider], host, model, False)


def validate_config(provider: str, api_key: str, custom_url: str = ""):
    """Return an error string, or None when the provider can be used."""
    if provider == CUSTOM:
        u = urlparse(custom_url or "")
        if u.scheme not in ("http", "https") or not u.hostname:
            return "Set the Custom / Local LLM base URL in Preferences (e.g. http://localhost:11434/v1)."
        if u.scheme == "http" and not is_loopback_host(u.hostname):
            return "Remote custom endpoints must use https. Plain http is only allowed for this Mac (localhost)."
        return None
    if not api_key:
        return f"Add your {PROVIDERS[provider]} API key in Preferences."
    return None


def _post_json(url, headers, body, timeout=120):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            payload = json.loads(e.read().decode("utf-8"))
            err = payload.get("error", payload)
            detail = err.get("message", "") if isinstance(err, dict) else str(err)
        except Exception:
            pass
        raise ProviderError(f"The provider returned HTTP {e.code}. {detail}".strip(), "HTTPError", e.code)
    except urllib.error.URLError as e:
        raise ProviderError(f"Could not reach the provider: {e.reason}", "URLError")
    except TimeoutError:
        raise ProviderError("The provider did not answer in time.", "Timeout")


def _openai_compatible(base_url, api_key, model, system, prompt):
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    data = _post_json(base_url.rstrip("/") + "/chat/completions", headers, {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    })
    try:
        return data["choices"][0]["message"]["content"], model
    except (KeyError, IndexError, TypeError):
        raise ProviderError("Unexpected response format from the provider.", "BadResponse")


def _anthropic(api_key, model, system, prompt):
    data = _post_json(ANTHROPIC_URL, {"x-api-key": api_key, "anthropic-version": "2023-06-01"}, {
        "model": model, "max_tokens": 4096, "system": system,
        "messages": [{"role": "user", "content": prompt}],
    })
    try:
        text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
    except (KeyError, TypeError):
        raise ProviderError("Unexpected response format from the provider.", "BadResponse")
    return text, data.get("model", model)


def _gemini(api_key, model, system, prompt):
    try:
        from google import genai
    except Exception:
        raise ProviderError("The google-genai package is not installed.", "ImportError")
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model, contents=prompt, config={"system_instruction": system})
        return response.text or "", model
    except Exception as e:
        raise ProviderError(f"Gemini request failed: {e}", type(e).__name__)


def send(provider: str, api_key: str, model: str, system: str, prompt: str, custom_url: str = ""):
    """Send an already-sanitised prompt. Returns (text, model_used)."""
    if provider == GEMINI:
        return _gemini(api_key, model, system, prompt)
    if provider == OPENAI:
        return _openai_compatible(OPENAI_URL, api_key, model, system, prompt)
    if provider == ANTHROPIC:
        return _anthropic(api_key, model, system, prompt)
    if provider == CUSTOM:
        return _openai_compatible(custom_url, api_key, model, system, prompt)
    raise ProviderError(f"Unknown provider: {provider}", "UnknownProvider")
