import os
import sys
import tempfile

# Point the vault and vector DB at throwaway directories *before* config is
# imported, so tests never touch the real vault.
_TEST_HOME = tempfile.mkdtemp(prefix="pka-tests-")
os.environ.setdefault("OBSIDIAN_VAULT_PATH", os.path.join(_TEST_HOME, "vault"))
os.environ.setdefault("DATA_PATH", os.path.join(_TEST_HOME, "data"))

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
