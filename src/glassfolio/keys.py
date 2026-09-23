"""Database key handling. The key lives in the macOS Keychain; never in code,
config files or git. GLASSFOLIO_DB_KEY overrides it for tests and development."""

import os
import re
import secrets

import keyring

SERVICE = "glassfolio"
ACCOUNT = "db-key"
ENV_VAR = "GLASSFOLIO_DB_KEY"
_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class DbKeyError(Exception):
    """Raised when the database key is missing or malformed."""


def validate_key(key: str) -> str:
    if not _KEY_PATTERN.match(key):
        raise DbKeyError("database key must be 64 lowercase hex characters")
    return key


def load_key() -> str:
    key = os.environ.get(ENV_VAR) or keyring.get_password(SERVICE, ACCOUNT)
    if not key:
        raise DbKeyError("no database key found; run `glassfolio init` first")
    return validate_key(key)


def create_key() -> str:
    """Generate a key and store it in the Keychain. Refuses to overwrite."""
    if keyring.get_password(SERVICE, ACCOUNT):
        raise DbKeyError("a database key already exists in the Keychain")
    key = secrets.token_hex(32)
    keyring.set_password(SERVICE, ACCOUNT, key)
    return key
