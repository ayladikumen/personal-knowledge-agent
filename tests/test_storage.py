import os

import pytest

from storage import StorageManager


@pytest.fixture
def storage(tmp_path):
    return StorageManager(str(tmp_path / "vault"))


def test_creates_vault_and_writes_note(storage):
    path = storage.save_note(title="Hello", content="body text")

    assert os.path.basename(path) == "Hello.md"
    content = open(path, encoding="utf-8").read()
    assert content.startswith("---\n")
    assert 'title: "Hello"' in content
    assert content.endswith("body text")


def test_strips_characters_illegal_in_filenames(storage):
    path = storage.save_note(title='a/b:c*d?e"f<g>h|i', content="x")

    assert os.path.basename(path) == "abcdefghi.md"


def test_untitled_note_gets_timestamp_name(storage):
    path = storage.save_note(title="///", content="x")

    name = os.path.basename(path)
    assert name.startswith("Note_")
    # 14 digits: YYYYmmddHHMMSS — the old format emitted a stray literal 'd'.
    stamp = name[len("Note_"):-len(".md")]
    assert stamp.isdigit() and len(stamp) == 14


def test_duplicate_titles_do_not_overwrite(storage):
    first  = storage.save_note(title="Same", content="one")
    second = storage.save_note(title="Same", content="two")
    third  = storage.save_note(title="Same", content="three")

    assert {os.path.basename(p) for p in (first, second, third)} == {
        "Same.md", "Same (1).md", "Same (2).md",
    }
    assert open(first, encoding="utf-8").read().endswith("one")
    assert open(second, encoding="utf-8").read().endswith("two")


def test_frontmatter_includes_source_and_tags(storage):
    path = storage.save_note(
        title="Repo",
        content="body",
        original_url="https://github.com/a/b",
        tags=["python", "cli"],
    )

    content = open(path, encoding="utf-8").read()
    assert "source: https://github.com/a/b" in content
    assert "tags:\n  - python\n  - cli\n" in content


def test_quotes_in_title_do_not_break_frontmatter(storage):
    path = storage.save_note(title='The "Best" Tool', content="body")

    content = open(path, encoding="utf-8").read()
    assert 'title: "The \\"Best\\" Tool"' in content


def test_windows_reserved_names_are_escaped(storage):
    path = storage.save_note(title="CON", content="x")

    assert os.path.basename(path) == "_CON.md"


def test_long_titles_are_truncated(storage):
    path = storage.save_note(title="A" * 300, content="x")

    assert len(os.path.basename(path)) <= 103  # 100 chars + ".md"
