"""Tests for the Telegram sync loop, with the network and AI stubbed out."""

import os

import pytest
from google.genai import errors as genai_errors

import config
import mcp_server
import transient


def gemini_error(code, status, message="boom"):
    """The error google-genai raises when the model is overloaded."""
    return genai_errors.ServerError(
        code, {"error": {"status": status, "message": message}}
    )


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
            raise ValueError("malformed content")
        return {"title": "ok", "tags": [], "file": "ok.md"}

    monkeypatch.setattr(mcp_server, "_process_and_save", flaky)
    stub_updates(monkeypatch, [[text_update(1, "fine"), text_update(2, "boom")]])

    results = mcp_server._sync()

    assert [r for r in results if "error" in r][0]["update_id"] == 2
    # Both updates are consumed — the failure is reported, not retried forever.
    assert open(offset_file).read() == "3"


def test_a_gemini_outage_does_not_consume_the_message(offset_file, monkeypatch):
    """
    Confirming an update_id deletes the message from Telegram's queue. A 503
    from Gemini is over in seconds, so consuming the message would trade a
    short wait for a permanently lost note.
    """
    def overloaded(content, url=None):
        raise gemini_error(503, "UNAVAILABLE", "The model is overloaded.")

    monkeypatch.setattr(mcp_server, "_process_and_save", overloaded)
    stub_updates(monkeypatch, [[text_update(100, "a note worth keeping")]])

    results = mcp_server._sync()

    assert results[0]["retryable"] is True
    # No offset written at all — Telegram redelivers this message next time.
    assert not os.path.exists(offset_file)


def test_messages_behind_an_outage_are_left_queued(offset_file, saved, monkeypatch):
    """Stopping at the first outage keeps the backlog intact and in order."""
    def overloaded(content, url=None):
        raise gemini_error(503, "UNAVAILABLE")

    monkeypatch.setattr(mcp_server, "_process_and_save", overloaded)
    stub_updates(monkeypatch, [[
        text_update(1, "first"), text_update(2, "second"), text_update(3, "third"),
    ]])

    results = mcp_server._sync()

    # One report, not three — and nothing consumed.
    assert len(results) == 1
    assert saved == []
    assert not os.path.exists(offset_file)


def test_saves_before_an_outage_are_still_confirmed(offset_file, monkeypatch):
    """An outage midway keeps its own message, but not the ones already saved."""
    def flaky(content, url=None):
        if content == "boom":
            raise gemini_error(503, "UNAVAILABLE")
        return {"title": "ok", "tags": [], "file": "ok.md"}

    monkeypatch.setattr(mcp_server, "_process_and_save", flaky)
    stub_updates(monkeypatch, [[text_update(1, "fine"), text_update(2, "boom")]])

    mcp_server._sync()

    # Update 1 is confirmed; update 2 is not, so the retry starts there.
    assert open(offset_file).read() == "2"


def test_the_outage_report_names_gemini_not_telegram(offset_file, monkeypatch):
    def overloaded(content, url=None):
        raise gemini_error(503, "UNAVAILABLE", "The model is overloaded.")

    monkeypatch.setattr(mcp_server, "_process_and_save", overloaded)
    stub_updates(monkeypatch, [[text_update(1, "hello")]])

    report = mcp_server._format_sync(mcp_server._sync())

    assert "Gemini" in report
    assert "Nothing was lost" in report
    assert "sync_telegram again" in report


def test_a_retried_outage_that_clears_saves_the_message(offset_file, monkeypatch):
    """
    The retry inside AIEngine means a brief 503 never reaches the sync loop at
    all — the save just takes a moment longer.
    """
    calls = []

    class FlakyModels:
        def generate_content(self, model, contents):
            calls.append(contents)
            if len(calls) < 3:
                raise gemini_error(503, "UNAVAILABLE")
            return type("Response", (), {"text": "# Recovered\n\nBody\n\nTAGS: a, b"})()

    # Stub the Gemini client, not _generate, so the real retry logic runs.
    monkeypatch.setattr(
        mcp_server.ai_engine, "_client", type("Client", (), {"models": FlakyModels()})()
    )
    monkeypatch.setattr(mcp_server, "_save", lambda a, url=None: {
        "title": a["title"], "tags": a["tags"], "file": "note.md"
    })
    monkeypatch.setattr(transient.time, "sleep", lambda _: None)
    stub_updates(monkeypatch, [[text_update(1, "plain text note")]])

    results = mcp_server._sync()

    assert len(calls) == 3
    assert results[0]["title"] == "Recovered"
    assert open(offset_file).read() == "2"


def test_permanent_failures_still_advance_the_offset(offset_file, monkeypatch):
    """A message that can never succeed must not wedge the queue forever."""
    def bad_key(content, url=None):
        raise genai_errors.ClientError(401, {"error": {"status": "UNAUTHENTICATED"}})

    monkeypatch.setattr(mcp_server, "_process_and_save", bad_key)
    stub_updates(monkeypatch, [[text_update(1, "hello"), text_update(2, "world")]])

    results = mcp_server._sync()

    assert len(results) == 2
    assert not any(r.get("retryable") for r in results)
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


def stub_processor(monkeypatch):
    """Record what the processor was asked to read, without any network."""
    seen = []

    def fake_process_message(text, extra_urls=None):
        seen.append({"text": text, "extra_urls": extra_urls or []})
        # Stands in for the real routing: a hidden link is the one to read
        # when the visible text has none of its own.
        url = extra_urls[0] if extra_urls else text
        return {"type": "url", "content": f"scraped:{text}", "url": url}

    monkeypatch.setattr(mcp_server.processor, "process_message", fake_process_message)
    return seen


def test_photo_caption_is_used_as_content(offset_file, saved, monkeypatch):
    """A link shared with media arrives as a caption, not text."""
    stub_processor(monkeypatch)
    stub_updates(monkeypatch, [[
        {"update_id": 7, "message": {"caption": "https://example.com/thing"}}
    ]])

    mcp_server._sync()

    assert saved and saved[0]["url"] == "https://example.com/thing"


def test_a_link_hidden_behind_link_text_is_passed_on(offset_file, saved, monkeypatch):
    """
    "read this" with the URL behind the words has no link in the message body.
    Scanning the text alone finds nothing, and the save is silently dropped.
    """
    seen = stub_processor(monkeypatch)
    stub_updates(monkeypatch, [[{
        "update_id": 8,
        "message": {
            "text": "read this",
            "entities": [{
                "type": "text_link", "offset": 0, "length": 9,
                "url": "https://example.com/hidden",
            }],
        },
    }]])

    mcp_server._sync()

    assert seen[0]["extra_urls"] == ["https://example.com/hidden"]
    assert saved and saved[0]["url"] == "https://example.com/hidden"


def test_a_link_preview_url_is_passed_on(offset_file, saved, monkeypatch):
    seen = stub_processor(monkeypatch)
    stub_updates(monkeypatch, [[{
        "update_id": 9,
        "message": {
            "caption": "",
            "caption_entities": [],
            "link_preview_options": {"url": "https://example.com/preview"},
        },
    }]])

    mcp_server._sync()

    assert seen[0]["extra_urls"] == ["https://example.com/preview"]


def test_plain_entities_that_are_not_links_are_ignored(offset_file, monkeypatch):
    seen = stub_processor(monkeypatch)
    stub_updates(monkeypatch, [[{
        "update_id": 10,
        "message": {
            "text": "some **bold** words",
            "entities": [{"type": "bold", "offset": 5, "length": 4}],
        },
    }]])

    mcp_server._sync()

    assert seen[0]["extra_urls"] == []


def test_non_message_updates_are_skipped(offset_file, saved, monkeypatch):
    stub_updates(monkeypatch, [[
        {"update_id": 1, "edited_message": {"text": "ignored"}},
        {"update_id": 2, "message": {"text": ""}},
    ]])

    assert mcp_server._sync() == []
    assert saved == []
    assert open(offset_file).read() == "3"
