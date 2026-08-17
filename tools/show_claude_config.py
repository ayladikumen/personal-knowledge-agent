"""Print the current Claude Desktop MCP config so setup can be verified."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from configure_claude import config_paths  # noqa: E402

print("\n=== Verifying every Claude Desktop config location ===")
ok = False
for path in config_paths():
    print(f"\nConfig file: {path}")
    if not os.path.isfile(path):
        print("  (not present)")
        continue

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    servers = data.get("mcpServers", {})
    print(f"  Top-level keys    : {list(data)}")
    print(f"  Servers registered: {len(servers)}")
    for name, spec in servers.items():
        print(f"    - {name}")
        print(f"        command: {spec.get('command')}")
        print(f"        args   : {spec.get('args')}")

    entry = servers.get("personal-knowledge-base")
    if not entry:
        print("  personal-knowledge-base is NOT registered here.")
        continue

    cmd    = entry.get("command", "")
    script = (entry.get("args") or [""])[0]
    print(f"  Interpreter exists: {os.path.isfile(cmd)}")
    print(f"  Script exists     : {os.path.isfile(script)}")
    ok = ok or (os.path.isfile(cmd) and os.path.isfile(script))

print("\nRegistered and runnable in at least one location:", ok)
