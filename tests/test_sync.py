"""Tests for the Telegram sync loop, with the network and AI stubbed out."""

import pytest

import config
import mcp_server


@pytest.fixture
def offset_file(tmp_path, monkeypatch):
    path = str(tmp_path / ".telegram_offset")
    monkeypatch.setattr(config, "OFFSET_FILE", path)
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "test-token")
    return path


def text_update(update_id, text):
    return {"update_id": update_id, "message": {"text": text}}


@pytest.fixture
def saved(monkeypatch):
    """Capture what would have been saved, skipping AI, disk and the vector DB."""
    captured = []

    def fake_process_and_save(content, url=None):
        captured.append({"content": content, "url": url})
        return {"title": f"Note {len(captured)}", "tags": [], "file": "note.md"}

    monkeypatch.setattr(mcp_server, "_process_and_save", fake_process_and_save)
    return captured


def stub_updates(monkeypatch, batches):
    """Serve getUpdates from a list of batches, respecting the offset param."""
    calls = []

    def fake_api(method, params=None):
        calls.append((method, dict(params or {})))
        if method != "getUpdates":
            raise AssertionError(f"unexpected API call: {method}")
        return {"ok": True, "result": batches.pop(0) if batches else []}

    monkeypatch.setattr(mcp_server, "_telegram_api", fake_api)
    return calls


def test_processes_messages_and_records_offset(offset_file, saved, monkeypatch):
    stub_updates(monkeypatch, [[text_update(10, "hello"), text_update(11, "world")]])

    results = mcp_server._sync()

    assert len(results) == 2
    assert [s["content"] for s in saved] == ["hello", "world"]
    assert open(offset_file).read() == "12"


def test_already_seen_messages_are_not_reprocessed(offset_file, saved, monkeypatch):
    with open(offset_file, "w") as f:
        f.write("12")
    calls = stub_updates(monkeypatch, [[]])

    assert mcp_server._sync() == []
    assert calls[0][1]["offset"] == 12


def test_backlog_larger_than_one_batch_is_fully_drained(offset_file, saved, monkeypatch):
    """Telegram caps getUpdates at 100, so a 150-message backlog needs two rounds."""
    first  = [text_update(i, f"m{i}") for i in range(1, 101)]
    second = [text_update(i, f"m{i}") for i in range(101, 151)]
    stub_updates(monkeypatch, [first, second])

    results = mcp_server._sync()

    assert len(results) == 150
    assert open(offset_file).read() == "151"


def test_offset_is_persisted_before_a_later_message_fails(offset_file, monkeypatch):
    """A crash midway must not replay the messages already saved."""
    def flaky(content, url=None):
        if content == "boom":
            raise RuntimeError("gemini exploded")
        return {"title": "ok", "tags": [], "file": "ok.md"}

    monkeypatch.setattr(mcp_server, "_process_and_save", flaky)
    stub_updates(monkeypatch, [[text_update(1, "fine"), text_update(2, "boom")]])

    results = mcp_server._sync()

    assert [r for r in results if "error" in r][0]["update_id"] == 2
    # Both updates are consumed — the failure is reported, not retried forever.
    assert open(offset_file).read() == "3"


def test_telegram_outage_is_reported_not_raised(offset_file, monkeypatch):
    def boom(method, params=None):
        raise OSError("network down")

    monkeypatch.setattr(mcp_server, "_telegram_api", boom)

    results = mcp_server._sync()

    assert len(results) == 1
    assert "network down" in results[0]["error"]


def test_missing_token_returns_setup_hint(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_TOKEN", "")

    results = mcp_server._sync()

    assert "TELEGRAM_BOT_TOKEN" in results[0]["error"]


def test_photo_messages_are_routed_to_vision(offset_file, monkeypatch):
    monkeypatch.setattr(mcp_server, "_download_telegram_file", lambda fid: b"imgbytes")
    monkeypatch.setattr(
        mcp_server,
        "_process_image_and_save",
        lambda data: {"title": "An image", "tags": [], "file": "img.md"},
    )
    stub_updates(monkeypatch, [[
        {"update_id": 5, "message": {"photo": [{"file_id": "small"}, {"file_id": "large"}]}}
    ]])

    results = mcp_server._sync()

    assert results[0]["type"] == "image"


def test_largest_photo_size_is_downloaded(offset_file, monkeypatch):
    requested = []
    monkeypatch.setattr(
        mcp_server, "_download_telegram_file", lambda fid: requested.append(fid) or b""
    )
    monkeypatch.setattr(
        mcp_server, "_process_image_and_save", lambda d: {"title": "i", "tags": [], "file": "i"}
    )
    stub_updates(monkeypatch, [[
        {"update_id": 5, "message": {"photo": [{"file_id": "small"}, {"file_id": "large"}]}}
    ]])

    mcp_server._sync()

    assert requested == ["large"]


def test_photo_caption_is_used_as_content(offset_file, saved, monkeypatch):
    """A link shared with media arrives as a caption, not text."""
    monkeypatch.setattr(
        mcp_server.processor,
        "process_message",
        lambda text: {"type": "url", "content": f"scraped:{text}", "url": text},
    )
    stub_updates(monkeypatch, [[
        {"update_id": 7, "message": {"caption": "https://example.com/thing"}}
    ]])

    mcp_server._sync()

    assert saved and saved[0]["url"] == "https://example.com/thing"


def test_non_message_updates_are_skipped(offset_file, saved, monkeypatch):
    stub_updates(monkeypatch, [[
        {"update_id": 1, "edited_message": {"text": "ignored"}},
        {"update_id": 2, "message": {"text": ""}},
    ]])

    assert mcp_server._sync() == []
    assert saved == []
    assert open(offset_file).read() == "3"
