"""
Tests for markdown export and import.

Markdown is the interchange format now, so it has to survive a round trip: what
the exporter writes, the importer must read back into the same fields. The
filename rules are carried over from the old vault writer and still matter —
Obsidian on Windows is where these notes get opened.
"""

import os

import pytest

import markdown_io
from markdown_io import MarkdownExporter, parse_note, sanitize_filename


@pytest.fixture
def exporter(tmp_path):
    return MarkdownExporter(str(tmp_path / "export"))


def note(**overrides) -> dict:
    base = {
        "id": 1,
        "title": "Hello",
        "content": "body text",
        "url": None,
        "source_type": None,
        "created_at": "2026-08-17 12:00:00",
        "tags": [],
    }
    base.update(overrides)
    return base


# ── Filenames ───────────────────────────────────────────────────────────────


def test_writes_a_note_with_frontmatter(exporter):
    [path] = exporter.export([note()])

    assert os.path.basename(path) == "Hello.md"
    content = open(path, encoding="utf-8").read()
    assert content.startswith("---\n")
    assert 'title: "Hello"' in content
    assert content.endswith("body text")


def test_strips_characters_illegal_in_filenames(exporter):
    [path] = exporter.export([note(title='a/b:c*d?e"f<g>h|i')])

    assert os.path.basename(path) == "abcdefghi.md"


def test_untitled_note_gets_timestamp_name(exporter):
    [path] = exporter.export([note(title="///")])

    name = os.path.basename(path)
    assert name.startswith("Note_")
    # 14 digits: YYYYmmddHHMMSS — an older format emitted a stray literal 'd'.
    stamp = name[len("Note_"):-len(".md")]
    assert stamp.isdigit() and len(stamp) == 14


def test_windows_reserved_names_are_escaped(exporter):
    [path] = exporter.export([note(title="CON")])

    assert os.path.basename(path) == "_CON.md"


def test_trailing_dots_and_spaces_are_trimmed():
    # A file ending in '.' or ' ' cannot be opened on Windows at all.
    assert sanitize_filename("Notes. ") == "Notes"


def test_long_titles_are_truncated():
    assert len(sanitize_filename("A" * 300)) == 100


def test_notes_sharing_a_title_do_not_overwrite_each_other(exporter):
    paths = exporter.export([
        note(id=1, title="Same", content="one"),
        note(id=2, title="Same", content="two"),
        note(id=3, title="Same", content="three"),
    ])

    assert {os.path.basename(p) for p in paths} == {
        "Same.md", "Same (1).md", "Same (2).md",
    }
    assert open(paths[0], encoding="utf-8").read().endswith("one")
    assert open(paths[1], encoding="utf-8").read().endswith("two")


def test_a_second_export_does_not_clobber_the_first(exporter):
    exporter.export([note(title="Same", content="one")])
    [second] = exporter.export([note(title="Same", content="two")])

    assert os.path.basename(second) == "Same (1).md"


# ── Frontmatter ─────────────────────────────────────────────────────────────


def test_frontmatter_includes_source_and_tags(exporter):
    [path] = exporter.export([
        note(url="https://github.com/a/b", tags=["python", "cli"])
    ])

    content = open(path, encoding="utf-8").read()
    assert "source: https://github.com/a/b" in content
    assert "tags:\n  - python\n  - cli\n" in content


def test_quotes_in_title_do_not_break_frontmatter(exporter):
    [path] = exporter.export([note(title='The "Best" Tool')])

    content = open(path, encoding="utf-8").read()
    assert 'title: "The \\"Best\\" Tool"' in content


# ── Import ──────────────────────────────────────────────────────────────────


def test_parses_title_source_and_tags():
    parsed = parse_note(
        '---\n'
        'title: "Agent Framework"\n'
        'date: 2026-01-02 03:04:05\n'
        'source: https://github.com/a/b\n'
        'tags:\n'
        '  - ai\n'
        '  - agents\n'
        '---\n'
        '\n'
        '# Body\n\nText here.'
    )

    assert parsed["title"] == "Agent Framework"
    assert parsed["url"] == "https://github.com/a/b"
    assert parsed["tags"] == ["ai", "agents"]
    assert parsed["created_at"] == "2026-01-02 03:04:05"
    assert parsed["content"] == "# Body\n\nText here."


def test_a_note_with_no_frontmatter_keeps_its_whole_body():
    parsed = parse_note("just some text\nover two lines", fallback_title="From Name")

    assert parsed["title"] == "From Name"
    assert parsed["content"] == "just some text\nover two lines"
    assert parsed["tags"] == []


def test_an_unterminated_frontmatter_block_is_not_swallowed():
    """Losing the body to a malformed header would silently destroy the note."""
    parsed = parse_note("---\ntitle: broken\nno closing delimiter")

    assert "no closing delimiter" in parsed["content"]


def test_filename_is_used_when_frontmatter_has_no_title():
    parsed = parse_note("---\ndate: 2026-01-01\n---\n\nbody", fallback_title="On Disk")

    assert parsed["title"] == "On Disk"


def test_an_inline_tag_list_is_understood():
    """The exporter writes block lists, but a hand-edited vault may not."""
    parsed = parse_note('---\ntitle: x\ntags: [ai, "agents"]\n---\n\nbody')

    assert parsed["tags"] == ["ai", "agents"]


def test_escaped_quotes_in_a_title_are_unescaped():
    parsed = parse_note('---\ntitle: "The \\"Best\\" Tool"\n---\n\nbody')

    assert parsed["title"] == 'The "Best" Tool'


def test_a_backslash_before_a_quote_is_not_confused_for_an_escape():
    original = 'path\\'
    quoted = markdown_io._yaml_quote(original)

    assert markdown_io._yaml_unquote(quoted) == original


def test_export_then_import_round_trips_every_field(exporter, tmp_path):
    original = note(
        title='The "Best" Tool',
        content="# Heading\n\nBody with a — dash.",
        url="https://example.com/x",
        source_type="url",
        tags=["one", "two"],
    )

    exporter.export([original])
    [reimported] = markdown_io.read_vault(str(tmp_path / "export"))

    assert reimported["title"] == original["title"]
    assert reimported["content"] == original["content"]
    assert reimported["url"] == original["url"]
    assert reimported["source_type"] == original["source_type"]
    assert reimported["tags"] == original["tags"]
    assert reimported["created_at"] == original["created_at"]


def test_read_vault_ignores_non_markdown_files(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("---\ntitle: A\n---\n\nbody", encoding="utf-8")
    (vault / "notes.db").write_text("binary-ish", encoding="utf-8")
    (vault / "README.txt").write_text("nope", encoding="utf-8")

    assert [n["title"] for n in markdown_io.read_vault(str(vault))] == ["A"]


def test_read_vault_records_the_source_filename(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "My Note.md").write_text("body", encoding="utf-8")

    assert markdown_io.read_vault(str(vault))[0]["source_file"] == "My Note.md"
