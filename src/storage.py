import os
import re
from datetime import datetime

# Characters Windows forbids in filenames, plus control characters.
_INVALID_FILENAME_CHARS = re.compile(r'[\\/*?:"<>|\x00-\x1f]')

# Device names Windows reserves regardless of extension.
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _yaml_quote(value: str) -> str:
    """Escape a string for use inside a double-quoted YAML scalar."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


class StorageManager:
    def __init__(self, vault_path: str):
        self.vault_path = vault_path
        os.makedirs(self.vault_path, exist_ok=True)

    def _sanitize_filename(self, title: str) -> str:
        sanitized = _INVALID_FILENAME_CHARS.sub("", title or "")
        sanitized = re.sub(r"\s+", " ", sanitized).strip()

        # A trailing dot or space makes a file unopenable on Windows.
        sanitized = sanitized[:100].strip(" .")

        if sanitized.split(".")[0].upper() in _RESERVED_NAMES:
            sanitized = f"_{sanitized}"

        return sanitized

    def save_note(
        self,
        title: str,
        content: str,
        original_url: str = None,
        tags: list = None,
    ) -> str:
        """Save a markdown note with YAML frontmatter to the Obsidian vault."""
        now = datetime.now()

        safe_title = self._sanitize_filename(title)
        if not safe_title:
            safe_title = f"Note_{now.strftime('%Y%m%d%H%M%S')}"

        filepath = os.path.join(self.vault_path, f"{safe_title}.md")

        counter = 1
        while os.path.exists(filepath):
            filepath = os.path.join(self.vault_path, f"{safe_title} ({counter}).md")
            counter += 1

        frontmatter = (
            "---\n"
            f"title: {_yaml_quote(title or safe_title)}\n"
            f"date: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        if original_url:
            frontmatter += f"source: {original_url}\n"
        if tags:
            frontmatter += "tags:\n"
            frontmatter += "".join(f"  - {tag}\n" for tag in tags)
        frontmatter += "---\n\n"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(frontmatter + (content or ""))

        return filepath
