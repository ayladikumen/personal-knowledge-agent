"""Post-install sanity check: imports, config, live API keys."""

import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

ok = True


def result(label: str, passed: bool, detail: str = ""):
    global ok
    ok = ok and passed
    print(f"  [{'OK ' if passed else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))


print("\nDependencies")
for mod in ("google.genai", "bs4", "requests", "yt_dlp", "dotenv", "mcp"):
    try:
        __import__(mod)
        result(mod, True)
    except Exception as e:
        result(mod, False, str(e))

print("\nConfiguration")
try:
    import config
    missing = config.missing_keys()
    result("keys present", not missing, ", ".join(missing) if missing else "")
    result("data path (notes.db)", True, config.DATA_PATH)
    result("markdown import/export path", True, config.VAULT_PATH)
    result("model", True, config.GEMINI_MODEL)
except Exception as e:
    result("config import", False, str(e))
    raise SystemExit(1)

print("\nMCP server")
try:
    import mcp_server  # noqa: F401
    result("imports and builds tools", True)
except Exception as e:
    result("imports and builds tools", False, str(e))

print("\nLive API checks")
try:
    import requests
    r = requests.get(
        f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/getMe", timeout=20)
    body = r.json()
    if body.get("ok"):
        bot = body["result"]
        result("Telegram token", True, f"@{bot.get('username')} ({bot.get('first_name')})")
    else:
        result("Telegram token", False, body.get("description", "rejected"))
except Exception as e:
    result("Telegram token", False, f"{type(e).__name__}: {e}")

try:
    from google import genai
    client = genai.Client(api_key=config.GEMINI_KEY)
    resp = client.models.generate_content(
        model=config.GEMINI_MODEL, contents="Reply with the single word: ready")
    result(f"Gemini chat ({config.GEMINI_MODEL})", True, (resp.text or "").strip()[:40])
except Exception as e:
    result(f"Gemini chat ({config.GEMINI_MODEL})", False, f"{type(e).__name__}: {str(e)[:160]}")

# Read the model name once, up front: referencing it inside the except clause
# is how this check used to hide a failure behind a second AttributeError.
embed_model = config.GEMINI_EMBED_MODEL
try:
    from notes_db import NotesDB
    vec = NotesDB()._embed("smoke test")
    result(f"Gemini embeddings ({embed_model})", len(vec) > 0, f"dim={len(vec)}")
except Exception as e:
    result(f"Gemini embeddings ({embed_model})", False,
           f"{type(e).__name__}: {str(e)[:160]}")

print("\nEnd-to-end note pipeline")
try:
    from ai import AIEngine
    analysis = AIEngine(config.GEMINI_KEY).analyze_content(
        "A tiny CLI tool for pretty-printing JSON on the terminal.",
        "https://example.com/jsonpretty")
    result("AI analysis", bool(analysis.get("title")),
           f"title={analysis.get('title')!r} tags={analysis.get('tags')}")
except Exception as e:
    result("AI analysis", False, f"{type(e).__name__}: {str(e)[:160]}")

print("\n" + ("All checks passed." if ok else "Some checks failed - see above."))
raise SystemExit(0 if ok else 1)
