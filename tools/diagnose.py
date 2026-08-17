"""Collect Claude Desktop MCP diagnostics into diag-log.txt."""

import glob
import json
import os
import subprocess
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT  = os.path.join(PROJ, "diag-log.txt")
CFG  = os.path.join(os.environ.get("APPDATA", ""), "Claude")

lines = []


def w(s=""):
    lines.append(str(s))


def section(name):
    w()
    w(f"---- {name} ----")


w("=============== CLAUDE DESKTOP DIAGNOSTICS ===============")

section("Config directory")
w(CFG)
if os.path.isdir(CFG):
    for entry in sorted(os.listdir(CFG)):
        p = os.path.join(CFG, entry)
        kind = "DIR " if os.path.isdir(p) else "FILE"
        size = "" if os.path.isdir(p) else f"{os.path.getsize(p)} bytes"
        w(f"  {kind} {entry}  {size}")
else:
    w("  MISSING")

section("claude_desktop_config.json")
cfg_file = os.path.join(CFG, "claude_desktop_config.json")
if os.path.isfile(cfg_file):
    with open(cfg_file, "r", encoding="utf-8") as f:
        raw = f.read()
    w(raw)
    try:
        data = json.loads(raw)
        servers = data.get("mcpServers", {})
        w(f"  parsed OK; mcpServers = {list(servers)}")
    except json.JSONDecodeError as e:
        w(f"  !! INVALID JSON: {e}")
else:
    w("  MISSING")

section("Other config-like files")
for name in ("config.json", "settings.json", "developer_settings.json"):
    p = os.path.join(CFG, name)
    if os.path.isfile(p):
        w(f"  {name}:")
        try:
            with open(p, "r", encoding="utf-8") as f:
                w("    " + f.read()[:3000].replace("\n", "\n    "))
        except Exception as e:
            w(f"    unreadable: {e}")

section("Log files")
logdir = os.path.join(CFG, "logs")
if os.path.isdir(logdir):
    for p in sorted(glob.glob(os.path.join(logdir, "*.log"))):
        w(f"  {os.path.basename(p)}  {os.path.getsize(p)} bytes")
else:
    w("  no logs directory")


def tail(path, n=150):
    if not os.path.isfile(path):
        return ["  (file not present)"]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return ["  " + ln.rstrip() for ln in f.readlines()[-n:]]
    except Exception as e:
        return [f"  unreadable: {e}"]


section("mcp.log (tail)")
lines.extend(tail(os.path.join(logdir, "mcp.log")))

section("mcp-server-personal-knowledge-base.log (tail)")
lines.extend(tail(os.path.join(logdir, "mcp-server-personal-knowledge-base.log")))

section("Any other mcp-server-*.log (tail 40 each)")
for p in sorted(glob.glob(os.path.join(logdir, "mcp-server-*.log"))):
    if "personal-knowledge-base" in p:
        continue
    w(f"  == {os.path.basename(p)} ==")
    lines.extend(tail(p, 40))

section("Direct launch test (what Claude Desktop would run)")
py     = os.path.join(PROJ, "venv", "Scripts", "python.exe")
script = os.path.join(PROJ, "src", "mcp_server.py")
w(f"  command: {py}")
w(f"  script : {script}")
w(f"  both exist: {os.path.isfile(py)} / {os.path.isfile(script)}")
try:
    p = subprocess.run(
        [py, script],
        input='{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
              '{"protocolVersion":"2024-11-05","capabilities":{},'
              '"clientInfo":{"name":"diag","version":"1"}}}\n',
        capture_output=True, text=True, timeout=30)
    w(f"  stdout: {p.stdout[:600]}")
    w(f"  stderr: {p.stderr[:600]}")
except subprocess.TimeoutExpired as e:
    w(f"  (timed out waiting, which is normal for a server)")
    w(f"  stdout: {(e.stdout or '')[:600]}")
    w(f"  stderr: {(e.stderr or '')[:600]}")
except Exception as e:
    w(f"  launch failed: {type(e).__name__}: {e}")

section("Claude processes")
try:
    p = subprocess.run(["tasklist", "/fi", "imagename eq claude.exe"],
                       capture_output=True, text=True, timeout=30)
    w(p.stdout)
except Exception as e:
    w(f"  {e}")

w()
w("=============== END ===============")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"Wrote {OUT}")
