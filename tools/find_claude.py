"""Explore the MSIX package data folder to find the config Claude actually reads."""

import glob
import os

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "diag-log.txt")
lines = []


def w(s=""):
    lines.append(str(s))


LOCAL = os.environ.get("LOCALAPPDATA", "")
PKGS  = os.path.join(LOCAL, "Packages")

w("=============== MSIX PACKAGE DATA ===============")
w(f"Packages root: {PKGS}")

w("\n---- Claude package folders ----")
pkg_dirs = sorted(glob.glob(os.path.join(PKGS, "*Claude*")))
for p in pkg_dirs:
    w(f"  {p}")
if not pkg_dirs:
    w("  none found")


def walk(root, max_depth=4):
    """List the tree under root, skipping noisy cache dirs."""
    SKIP = {"Cache", "Code Cache", "GPUCache", "DawnCache", "blob_storage",
            "Service Worker", "Partitions", "Crashpad", "IndexedDB"}
    base_depth = root.rstrip("\\").count("\\")
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath.rstrip("\\").count("\\") - base_depth
        if depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        rel = os.path.relpath(dirpath, root)
        w(f"    [{rel}]")
        for fn in sorted(filenames)[:25]:
            try:
                size = os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                size = "?"
            w(f"        {fn}  ({size})")


for p in pkg_dirs:
    w(f"\n---- Tree under {os.path.basename(p)} ----")
    for sub in ("LocalCache\\Roaming", "LocalCache\\Local", "LocalState",
                "RoamingState"):
        full = os.path.join(p, sub)
        if os.path.isdir(full):
            w(f"\n  == {sub} ==")
            try:
                walk(full)
            except Exception as e:
                w(f"    walk failed: {e}")
        else:
            w(f"\n  == {sub} == (absent)")

w("\n---- Any claude_desktop_config.json anywhere under Packages ----")
hits = glob.glob(os.path.join(PKGS, "**", "claude_desktop_config.json"),
                 recursive=True)
for h in sorted(hits):
    w(f"  {h}  ({os.path.getsize(h)} bytes)")
    try:
        with open(h, "r", encoding="utf-8") as f:
            w("      " + f.read()[:1500].replace("\n", "\n      "))
    except Exception as e:
        w(f"      unreadable: {e}")
if not hits:
    w("  none found")

w("\n---- Any mcp*.log anywhere under Packages ----")
for h in sorted(glob.glob(os.path.join(PKGS, "**", "mcp*.log"), recursive=True)):
    w(f"  {h}  ({os.path.getsize(h)} bytes)")

w("\n=============== END ===============")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(f"Wrote {OUT}")
