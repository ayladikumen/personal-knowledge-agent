"""
Reading and writing notes as Obsidian-style markdown.

Notes live in SQLite now (see notes_db.py), so markdown is no longer the store
— it's the interchange format. Two jobs remain:

  * export — dump the database back out as browsable .md files, for Obsidian or
    for handing the collection to another tool.
  * import — pull an existing vault into the database, which is how the old
    vault's notes were migrated across.

The frontmatter shape and the Windows filename rules are carried over from the
original vault writer unchanged; they encode a set of edge cases (reserved
device names, trailing dots, over-long titles) that are easy to rediscover the
hard way.
"""

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


def _yaml_unquote(value: str) -> str:
    """Reverse _yaml_quote for a double-quoted scalar, leaving bare text alone."""
    if len(value) < 2 or value[0] != '"' or value[-1] != '"':
        return value

    inner = value[1:-1]
    out, i = [], 0
    # Scanned rather than chained .replace() calls, which would turn an escaped
    # backslash followed by a quote back into an unescaped quote.
    while i < len(inner):
        if inner[i] == "\\" and i + 1 < len(inner):
            out.append(inner[i + 1])
            i += 2
        else:
            out.append(inner[i])
            i += 1
    return "".join(out)


def sanitize_filename(title: str) -> str:
    """Turn a note title into a filename that Windows will actually open."""
    sanitized = _INVALID_FILENAME_CHARS.sub("", title or "")
    sanitized = re.sub(r"\s+", " ", sanitized).strip()

    # A trailing dot or space makes a file unopenable on Windows.
    sanitized = sanitized[:100].strip(" .")

    if sanitized.split(".")[0].upper() in _RESERVED_NAMES:
        sanitized = f"_{sanitized}"

    return sanitized


def render(note: dict) -> str:
    """Render a note row as markdown with YAML frontmatter."""
    lines = ["---", f"title: {_yaml_quote(note.get('title') or 'Untitled')}"]

    if note.get("id") is not None:
        lines.append(f"id: {note['id']}")
    lines.append(f"date: {note.get('created_at') or ''}")
    if note.get("url"):
        lines.append(f"source: {note['url']}")
    if note.get("source_type"):
        lines.append(f"type: {note['source_type']}")
    if note.get("tags"):
        lines.append("tags:")
        lines += [f"  - {tag}" for tag in note["tags"]]
    lines += ["---", ""]

    return "\n".join(lines) + "\n" + (note.get("content") or "")


# ── Export ──────────────────────────────────────────────────────────────────


class MarkdownExporter:
    def __init__(self, dest_path: str):
        self.dest_path = dest_path

    def export(self, notes: list[dict]) -> list[str]:
        """Write each note to dest_path, returning the paths written."""
        os.makedirs(self.dest_path, exist_ok=True)

        written, claimed = [], set()
        for note in notes:
            path = self._unique_path(note, claimed)
            claimed.add(path.lower())
            with open(path, "w", encoding="utf-8") as f:
                f.write(render(note))
            written.append(path)
        return written

    def _unique_path(self, note: dict, claimed: set) -> str:
        safe_title = sanitize_filename(note.get("title"))
        if not safe_title:
            safe_title = f"Note_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        path = os.path.join(self.dest_path, f"{safe_title}.md")

        # Two notes can legitimately share a title, and a previous export may
        # already be sitting in the directory.
        counter = 1
        while path.lower() in claimed or os.path.exists(path):
            path = os.path.join(self.dest_path, f"{safe_title} ({counter}).md")
            counter += 1
        return path


# ── Import ──────────────────────────────────────────────────────────────────


def _split_frontmatter(text: str) -> tuple[list[str], str]:
    """Separate a leading --- block from the body, if there is one."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return [], text

    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1:]).lstrip("\n")

    # An unterminated block isn't frontmatter; treat the whole file as content
    # rather than swallowing it.
    return [], text


def _parse_fields(lines: list[str]) -> dict:
    fields: dict = {}
    tags: list[str] = []
    in_tag_list = False

    for line in lines:
        stripped = line.strip()

        if in_tag_list and stripped.startswith("- "):
            tags.append(stripped[2:].strip())
            continue
        in_tag_list = False

        if ":" not in stripped:
            continue

        key, _, value = stripped.partition(":")
        key, value = key.strip(), value.strip()

        if key == "tags":
            if not value:
                in_tag_list = True
            elif value.startswith("[") and value.endswith("]"):
                # Obsidian also writes inline lists; the exporter doesn't, but
                # a hand-edited vault may.
                tags += [t.strip().strip("'\"") for t in value[1:-1].split(",") if t.strip()]
            else:
                tags += [t.strip() for t in value.split(",") if t.strip()]
            continue

        fields[key] = value

    fields["tags"] = tags
    return fields


def parse_note(text: str, fallback_title: str = "") -> dict:
    """
    Parse a markdown note into the fields notes_db.add_note expects.

    Anything the frontmatter doesn't say is left empty rather than guessed —
    except the title, which falls back to the filename the caller passes in,
    since an untitled note is worse than a slightly wrong one.
    """
    field_lines, body = _split_frontmatter(text)
    fields = _parse_fields(field_lines)

    title = _yaml_unquote(fields.get("title", "")).strip() or fallback_title.strip()

    return {
        "title": title or "Untitled",
        "content": body,
        "url": fields.get("source") or None,
        "source_type": fields.get("type") or None,
        "created_at": fields.get("date") or None,
        "tags": fields["tags"],
    }


def read_vault(vault_path: str) -> list[dict]:
    """Parse every .md file in a directory, in a stable order."""
    notes = []
    for entry in sorted(os.listdir(vault_path)):
        if not entry.endswith(".md"):
            continue
        path = os.path.join(vault_path, entry)
        with open(path, "r", encoding="utf-8") as f:
            note = parse_note(f.read(), fallback_title=os.path.splitext(entry)[0])
        note["source_file"] = entry
        notes.append(note)
    return notes
