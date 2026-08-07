import pytest
import requests

import transient
from processor import ContentProcessor


@pytest.fixture
def proc():
    return ContentProcessor()


@pytest.fixture
def no_sleep(monkeypatch):
    """Retry backoff shouldn't make the suite wait for real seconds."""
    monkeypatch.setattr(transient.time, "sleep", lambda _: None)


class FakeResponse:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


def test_extracts_url_from_surrounding_text(proc):
    assert proc.extract_url("check this out https://example.com/x cool right") == (
        "https://example.com/x"
    )


def test_trailing_punctuation_is_not_part_of_url(proc):
    assert proc.extract_url("see https://example.com/page.") == "https://example.com/page"
    assert proc.extract_url("(https://example.com/a)") == "https://example.com/a"


def test_plain_text_is_not_treated_as_a_link(proc):
    result = proc.process_message("remember to read about vector databases")

    assert result == {
        "type": "text",
        "content": "remember to read about vector databases",
        "url": None,
    }


@pytest.mark.parametrize("url", [
    "https://youtube.com/watch?v=abc",
    "https://www.youtube.com/watch?v=abc",
    "https://m.youtube.com/watch?v=abc",
    "https://youtu.be/abc",
])
def test_youtube_hosts_route_to_youtube(proc, monkeypatch, url):
    monkeypatch.setattr(proc, "_process_youtube", lambda u: {"type": "youtube", "url": u})

    assert proc.process_message(url)["type"] == "youtube"


def test_github_lookalike_host_is_not_routed_to_github(proc, monkeypatch):
    """https://evil.com/github.com/x must not be handled as a repo."""
    monkeypatch.setattr(proc, "_process_github", lambda u: {"type": "github", "url": u})
    monkeypatch.setattr(proc, "_process_general_url", lambda u: {"type": "url", "url": u})

    assert proc.process_message("https://evil.com/github.com/a/b")["type"] == "url"
    assert proc.process_message("https://github.com/a/b")["type"] == "github"


def test_github_readme_uses_reported_default_branch(proc, monkeypatch):
    """A repo whose default branch is neither main nor master still resolves."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if url.startswith("https://api.github.com/"):
            return FakeResponse(200, payload={"default_branch": "trunk"})
        if "/trunk/README.md" in url:
            return FakeResponse(200, text="# The Readme")
        return FakeResponse(404)

    monkeypatch.setattr(proc.session, "get", fake_get)

    result = proc._process_github("https://github.com/acme/widget")

    assert result["type"] == "github"
    assert "The Readme" in result["content"]
    assert "acme/widget" in result["content"]
    assert any("/trunk/README.md" in c for c in calls)


def test_github_falls_back_to_scraping_when_no_readme(proc, monkeypatch):
    monkeypatch.setattr(proc.session, "get", lambda url, **kw: FakeResponse(404))
    monkeypatch.setattr(proc, "_process_general_url", lambda u: {"type": "url", "url": u})

    assert proc._process_github("https://github.com/acme/widget")["type"] == "url"


def test_github_url_without_repo_falls_back(proc, monkeypatch):
    monkeypatch.setattr(proc, "_process_general_url", lambda u: {"type": "url", "url": u})

    assert proc._process_github("https://github.com/acme")["type"] == "url"


def test_general_url_extracts_title_and_drops_scripts(proc, monkeypatch):
    html = """
    <html><head><title>My Page</title></head>
    <body>
      <script>var noise = "should not appear";</script>
      <style>.x { color: red }</style>
      <p>First paragraph.</p>
      <p>Second paragraph.</p>
    </body></html>
    """
    monkeypatch.setattr(proc.session, "get", lambda url, **kw: FakeResponse(200, text=html))

    result = proc._process_general_url("https://example.com")

    assert result["type"] == "url"
    assert "Title: My Page" in result["content"]
    assert "First paragraph." in result["content"]
    assert "Second paragraph." in result["content"]
    assert "should not appear" not in result["content"]


def test_general_url_falls_back_to_body_text_without_paragraphs(proc, monkeypatch):
    html = "<html><head><title>T</title></head><body><div>Body words here</div></body></html>"
    monkeypatch.setattr(proc.session, "get", lambda url, **kw: FakeResponse(200, text=html))

    assert "Body words here" in proc._process_general_url("https://example.com")["content"]


def test_fetch_failure_is_reported_not_raised(proc, monkeypatch):
    def boom(url, **kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr(proc.session, "get", boom)

    result = proc._process_general_url("https://example.com")

    assert result["type"] == "error"
    assert "connection reset" in result["content"]


def test_a_flaky_fetch_is_retried_before_giving_up(proc, monkeypatch, no_sleep):
    """One dropped connection shouldn't become a note that says 'failed to extract'."""
    attempts = []

    def flaky(url, **kwargs):
        attempts.append(url)
        if len(attempts) < 2:
            raise requests.ConnectionError("connection reset by peer")
        return FakeResponse(200, text="<html><title>T</title><p>Real content</p></html>")

    monkeypatch.setattr(proc.session, "get", flaky)

    result = proc._process_general_url("https://example.com")

    assert result["type"] == "url"
    assert "Real content" in result["content"]
    assert len(attempts) == 2


def test_a_502_from_a_page_is_retried(proc, monkeypatch, no_sleep):
    attempts = []

    def flaky(url, **kwargs):
        attempts.append(url)
        return FakeResponse(502 if len(attempts) < 2 else 200, text="<p>Recovered</p>")

    monkeypatch.setattr(proc.session, "get", flaky)

    assert "Recovered" in proc._process_general_url("https://example.com")["content"]
    assert len(attempts) == 2


def test_a_404_is_not_retried(proc, monkeypatch, no_sleep):
    """A missing page will still be missing on the third try."""
    attempts = []

    def missing(url, **kwargs):
        attempts.append(url)
        return FakeResponse(404)

    monkeypatch.setattr(proc.session, "get", missing)

    assert proc._process_general_url("https://example.com")["type"] == "error"
    assert len(attempts) == 1


def test_a_dead_site_degrades_instead_of_wedging_the_queue(proc, monkeypatch, no_sleep):
    """
    A permanently unreachable link must end as an error note. Raising instead
    would leave the message queued in Telegram and block every later sync
    behind a URL that will never load.
    """
    def always_down(url, **kwargs):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(proc.session, "get", always_down)

    result = proc._process_general_url("https://example.com")

    assert result["type"] == "error"
    assert result["url"] == "https://example.com"


def test_readme_probing_does_not_retry_each_candidate(proc, monkeypatch, no_sleep):
    """Probing branches is meant to 404 its way down the list, quickly."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if url.startswith("https://api.github.com/"):
            return FakeResponse(200, payload={"default_branch": "main"})
        return FakeResponse(404)

    monkeypatch.setattr(proc.session, "get", fake_get)
    monkeypatch.setattr(proc, "_process_general_url", lambda u: {"type": "url", "url": u})

    proc._process_github("https://github.com/acme/widget")

    raw = [c for c in calls if "raw.githubusercontent.com" in c]
    # main + master, three filenames each — each tried exactly once.
    assert len(raw) == len(set(raw)) == 6


def test_unreachable_github_falls_back_to_scraping(proc, monkeypatch, no_sleep):
    def fake_get(url, **kwargs):
        if url.startswith("https://api.github.com/"):
            return FakeResponse(200, payload={"default_branch": "main"})
        raise requests.ConnectionError("connection reset")

    monkeypatch.setattr(proc.session, "get", fake_get)
    monkeypatch.setattr(proc, "_process_general_url", lambda u: {"type": "url", "url": u})

    assert proc._process_github("https://github.com/acme/widget")["type"] == "url"
