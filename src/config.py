"""
Central configuration for the Personal Knowledge Agent.

Every path and key the project needs is resolved here once, so the rest of the
modules never have to guess where the project root is or re-read the .env file.
"""

import os

from dotenv import load_dotenv

# ── Paths ───────────────────────────────────────────────────────────────────

SRC_DIR      = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SRC_DIR)

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


def _path_from_env(var: str, default_name: str) -> str:
    """Resolve a path from the environment, relative to the project root."""
    value = (os.getenv(var) or "").strip()
    if not value:
        return os.path.join(PROJECT_ROOT, default_name)
    if os.path.isabs(value):
        return os.path.normpath(value)
    # normpath so the default "./vault" doesn't surface to the user as
    # "C:\...\personal-knowledge-agent\.\vault" in every message.
    return os.path.normpath(os.path.join(PROJECT_ROOT, value))


# Notes are stored in DATA_PATH/notes.db. VAULT_PATH is only the markdown
# import/export directory now — nothing reads or writes it during normal use.
VAULT_PATH  = _path_from_env("OBSIDIAN_VAULT_PATH", "vault")
DATA_PATH   = _path_from_env("DATA_PATH", "data")
OFFSET_FILE = os.path.join(PROJECT_ROOT, ".telegram_offset")

# ── Keys & models ───────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_KEY     = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip()

# Switching this invalidates every stored vector — embeddings from two models
# aren't comparable — so notes_db records the model alongside each one and
# reindex_notes re-embeds whatever no longer matches.
GEMINI_EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-2").strip()

# Values shipped in .env.example — present but useless, so treat them as unset.
_PLACEHOLDERS = {
    "your_telegram_bot_token_here",
    "your_gemini_api_key_here",
}


def is_configured(value: str) -> bool:
    """True if a key is set to something other than an .env.example placeholder."""
    return bool(value) and value not in _PLACEHOLDERS


def missing_keys() -> list[str]:
    """Return the names of required keys that are absent or still placeholders."""
    missing = []
    if not is_configured(TELEGRAM_TOKEN):
        missing.append("TELEGRAM_BOT_TOKEN")
    if not is_configured(GEMINI_KEY):
        missing.append("GEMINI_API_KEY")
    return missing


def has_telegram() -> bool:
    return is_configured(TELEGRAM_TOKEN)


def has_gemini() -> bool:
    return is_configured(GEMINI_KEY)


def setup_hint(keys: list[str]) -> str:
    """A one-line, actionable message naming what the user still has to set."""
    return (
        f"Missing configuration: {', '.join(keys)}. "
        f"Copy .env.example to .env in {PROJECT_ROOT} and fill in the real values "
        "(bot token from @BotFather, Gemini key from https://aistudio.google.com/)."
    )
