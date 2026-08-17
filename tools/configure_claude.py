"""
Registers this project's MCP server in claude_desktop_config.json.

Handles both Claude Desktop builds on Windows:

  * the normal installer, which reads %APPDATA%\\Claude, and
  * the Microsoft Store (MSIX) build, whose %APPDATA% writes are redirected into
    %LOCALAPPDATA%\\Packages\\Claude_<hash>\\LocalCache\\Roaming\\Claude.

A config written to the wrong one of those is silently ignored, so every config
directory that actually exists gets updated.

Merges into whatever is already there rather than overwriting, and backs up each
file first. Safe to run more than once.
"""

import glob
import json
import os
import shutil
import sys
from datetime import datetime

SERVER_NAME  = "personal-knowledge-base"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILENAME     = "claude_desktop_config.json"


def config_paths() -> list[str]:
    """Every plausible Claude Desktop config location on this machine."""
    paths = []

    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA") or os.path.expanduser("~/AppData/Roaming")
        local   = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")

        # Microsoft Store build — the redirected copy is the one it reads.
        for pkg in sorted(glob.glob(os.path.join(local, "Packages", "*Claude*"))):
            paths.append(os.path.join(pkg, "LocalCache", "Roaming", "Claude", FILENAME))

        # Standard installer build.
        paths.append(os.path.join(roaming, "Claude", FILENAME))

    elif sys.platform == "darwin":
        paths.append(os.path.expanduser(
            f"~/Library/Application Support/Claude/{FILENAME}"))
    else:
        paths.append(os.path.expanduser(f"~/.config/Claude/{FILENAME}"))

    return paths


def venv_python() -> str:
    """Prefer the project venv's interpreter; fall back to the one running us."""
    for c in (os.path.join(PROJECT_ROOT, "venv", "Scripts", "python.exe"),
              os.path.join(PROJECT_ROOT, "venv", "bin", "python")):
        if os.path.isfile(c):
            return c.replace("\\", "/")
    return sys.executable.replace("\\", "/")


def load(path: str):
    """Return the parsed config, or None if it's present but unparseable."""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        return json.loads(text) if text else {}
    except (OSError, json.JSONDecodeError) as e:
        print(f"    ! could not parse ({e}) - skipping, will not overwrite")
        return None


def update(path: str, python: str, server: str) -> bool:
    existed = os.path.isfile(path)
    parent  = os.path.dirname(path)

    # Only create a config directory that Claude already owns. Inventing a new
    # one is how the previous run ended up writing somewhere nothing reads.
    if not existed and not os.path.isdir(parent):
        print(f"    - skipped (no Claude data directory here)")
        return False

    config = load(path)
    if config is None:
        return False

    if existed:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, f"{path}.backup-{stamp}")
        print(f"    backed up to {os.path.basename(path)}.backup-{stamp}")

    before  = json.dumps(config.get("mcpServers", {}), sort_keys=True)
    servers = config.setdefault("mcpServers", {})
    servers[SERVER_NAME] = {"command": python, "args": [server]}

    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    action = "unchanged" if before == json.dumps(servers, sort_keys=True) else "updated"
    print(f"    {action}; other top-level keys preserved: "
          f"{[k for k in config if k != 'mcpServers']}")
    print(f"    servers now: {', '.join(sorted(servers))}")
    return True


def main() -> int:
    server = os.path.join(PROJECT_ROOT, "src", "mcp_server.py").replace("\\", "/")
    python = venv_python()

    if not os.path.isfile(server):
        print(f"  ! Entry point not found: {server}")
        return 1

    print(f"  command: {python}")
    print(f"  script : {server}")

    wrote = 0
    for path in config_paths():
        print(f"\n  {path}")
        if update(path, python, server):
            wrote += 1

    print()
    if wrote:
        print(f"  Registered '{SERVER_NAME}' in {wrote} config file(s).")
        print("  Fully quit Claude Desktop (tray icon -> Quit) and reopen it.")
        return 0

    print("  ! No Claude Desktop config directory was found.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
