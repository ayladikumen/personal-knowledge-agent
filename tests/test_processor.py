import pytest

from processor import ContentProcessor


@pytest.fixture
def proc():
    return ContentProcessor()


class FakeResponse:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


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
