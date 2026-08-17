"""Find which Gemini models this API key can actually generate with."""

import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import config  # noqa: E402
from google import genai  # noqa: E402

client = genai.Client(api_key=config.GEMINI_KEY)

print("--- Models the key can list ---")
listed = []
try:
    for m in client.models.list():
        actions = getattr(m, "supported_actions", None) or []
        listed.append(m.name)
        print(f"  {m.name}   actions={list(actions)}")
except Exception as e:
    print(f"  list failed: {type(e).__name__}: {str(e)[:200]}")

CANDIDATES = [
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
]

print("\n--- generate_content probe ---")
working = []
for name in CANDIDATES:
    try:
        r = client.models.generate_content(model=name, contents="say ok")
        print(f"  [OK  ] {name} -> {(r.text or '').strip()[:30]}")
        working.append(name)
    except Exception as e:
        print(f"  [FAIL] {name} -> {type(e).__name__}: {str(e)[:110]}")

print("\n--- embeddings probe (required for search) ---")
emb_working = []
for name in ["gemini-embedding-001", "text-embedding-004", "models/text-embedding-004"]:
    try:
        r = client.models.embed_content(model=name, contents="hello")
        dim = len(r.embeddings[0].values)
        print(f"  [OK  ] {name} -> dim={dim}")
        emb_working.append(name)
    except Exception as e:
        print(f"  [FAIL] {name} -> {type(e).__name__}: {str(e)[:110]}")

print("\n=== SUMMARY ===")
print(f"  working chat models     : {working or 'NONE'}")
print(f"  working embedding models: {emb_working or 'NONE'}")
