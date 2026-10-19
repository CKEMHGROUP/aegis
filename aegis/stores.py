"""
Settings and secrets.

  * Secrets (API keys, mailbox credentials, PII presets) -> macOS Keychain via keyring.
  * Non-secret preferences (theme, models, host/port) -> QSettings. In v0.9.5 these were
    also in the Keychain, which causes extra Keychain prompts on an unsigned app.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass

import keyring
from PySide6.QtCore import QSettings

SERVICE = "AegisPrivacyAirlock"
LEGACY_PRESETS_FILE = os.path.expanduser("~/.aegis_pii_presets.txt")

K_PRESETS = "PIIPresetsJSON"
K_IMAP_USER = "IMAPUser"
K_IMAP_PASS = "IMAPPass"
SECRET_KEY_NAMES = {
    "gemini": "GeminiAPIKey",
    "openai": "OpenAIAPIKey",
    "anthropic": "AnthropicAPIKey",
    "custom": "CustomLLMAPIKey",
}
LEGACY_CUSTOM_ENDPOINT = "CustomLLMEndpoint"
LEGACY_THEME = "ThemeMode"


def settings() -> QSettings:
    return QSettings()  # org/app name are set in app.main()


# Each Keychain read can raise a permission prompt on an unsigned app, so secrets are read once
# per run and cached in memory (the cache is updated on every write).
_CACHE: dict = {}


def get_secret(name: str) -> str:
    if name in _CACHE:
        return _CACHE[name]
    try:
        value = keyring.get_password(SERVICE, name) or ""
    except Exception:
        return ""
    _CACHE[name] = value
    return value


def set_secret(name: str, value: str) -> None:
    value = (value or "").strip()
    try:
        if value:
            keyring.set_password(SERVICE, name, value)
        else:
            try:
                keyring.delete_password(SERVICE, name)
            except keyring.errors.PasswordDeleteError:
                pass
    except Exception as e:  # surfaced to the UI by the caller
        raise RuntimeError(f"Keychain write failed ({type(e).__name__})") from None
    _CACHE[name] = value


def get_api_key(provider: str) -> str:
    return get_secret(SECRET_KEY_NAMES[provider])


# ----------------------------------------------------------------------------- presets
def load_presets() -> list:
    raw = get_secret(K_PRESETS)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [t for t in data if isinstance(t, str) and t.strip()]
    except Exception:
        return []


def save_presets(terms) -> None:
    clean = list(dict.fromkeys(t.strip() for t in terms if t and t.strip()))
    set_secret(K_PRESETS, json.dumps(clean, ensure_ascii=False) if clean else "")


def legacy_presets_available() -> bool:
    return os.path.exists(LEGACY_PRESETS_FILE)


def import_legacy_presets() -> int:
    """Merge the old plaintext file into the Keychain list. Returns terms imported."""
    with open(LEGACY_PRESETS_FILE, "r", encoding="utf-8") as f:
        legacy = [l.strip() for l in f if l.strip()]
    merged = load_presets() + legacy
    save_presets(merged)
    return len(legacy)


def delete_legacy_presets() -> None:
    try:
        size = os.path.getsize(LEGACY_PRESETS_FILE)
        with open(LEGACY_PRESETS_FILE, "r+b") as f:  # overwrite before unlink
            f.write(b"\0" * size)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    try:
        os.remove(LEGACY_PRESETS_FILE)
    except OSError:
        pass


# ----------------------------------------------------------------------------- mail accounts
@dataclass
class Account:
    id: str
    label: str        # chosen by the user; shown only in this app, never sent anywhere
    provider: str
    host: str
    port: int
    enabled: bool = True


def new_account_id() -> str:
    return uuid.uuid4().hex[:8]


def load_accounts() -> list:
    raw = settings().value("imap/accounts", "[]")
    try:
        return [Account(**a) for a in json.loads(raw)]
    except Exception:
        return []


def save_accounts(accounts) -> None:
    settings().setValue("imap/accounts", json.dumps([asdict(a) for a in accounts]))


def account_credentials(account_id: str):
    return get_secret(f"IMAP-{account_id}-user"), get_secret(f"IMAP-{account_id}-pass")


def set_account_credentials(account_id: str, user: str, password: str) -> None:
    set_secret(f"IMAP-{account_id}-user", user)
    set_secret(f"IMAP-{account_id}-pass", password)


def delete_account(account_id: str) -> None:
    set_account_credentials(account_id, "", "")
    save_accounts([a for a in load_accounts() if a.id != account_id])


def migrate_legacy_account() -> bool:
    """v0.10/0.11 kept exactly one account. Turn it into the first saved account, once."""
    if load_accounts():
        return False
    user, pw, host = get_secret(K_IMAP_USER), get_secret(K_IMAP_PASS), settings().value("imap/host", "")
    if not (user and pw and host):
        return False
    acct = Account(new_account_id(), "Primary", settings().value("imap/provider", "Custom IMAP Server"),
                   host, settings().value("imap/port", 993, type=int), True)
    set_account_credentials(acct.id, user, pw)
    save_accounts([acct])
    set_secret(K_IMAP_USER, "")
    set_secret(K_IMAP_PASS, "")
    return True


def default_out_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "Documents", "Aegis Sanitised")
