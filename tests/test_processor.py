"""
Reading a link.

The rule these tests are built around: a link that was sent is a link that
gets saved. A page that 403s, a page that is gone, a page that is nothing but
a JavaScript mount point — each of those still has to come back as something
worth writing a note about, and none of them may raise, because a raised error
leaves the message stuck in Telegram's queue behind a URL that will never load.
"""

import pytest
import requests

import transient
from processor import ContentProcessor


@pytest.fixture
def proc():
    # The archive fallback is a network call of its own; the tests that care
    # about it turn it on explicitly.
    return ContentProcessor(archive_fallback=False)


@pytest.fixture
def archiving():
    return ContentProcessor(archive_fallback=True)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Retry backoff shouldn't make the suite wait for real seconds."""
    monkeypatch.setattr(transient.time, "sleep", lambda _: None)


class FakeResponse:
    def __init__(self, status_code=200, text="", payload=None,
                 content_type="text/html; charset=utf-8", url="", body=None):
        self.status_code = status_code
        self.url = url
        self.text = text
        self.headers = {"Content-Type": content_type}
        self._payload = payload
        self._body = body if body is not None else text.encode("utf-8")

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start:start + chunk_size]

    def close(self):
        pass

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


def route(proc, monkeypatch, handler):
    """Serve session.get from `handler(url) -> FakeResponse | Exception`."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        outcome = handler(url)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            return FakeResponse(404, url=url)
        return outcome

    monkeypatch.setattr(proc.session, "get", fake_get)
    return calls


def page(body, title="My Page", **kwargs):
    return FakeResponse(
        200, text=f"<html><head><title>{title}</title></head><body>{body}</body></html>",
        **kwargs,
    )


ARTICLE = "<p>" + ("A real sentence about a real thing. " * 12) + "</p>"


# ── Reading a message ───────────────────────────────────────────────────────


def test_plain_text_is_not_treated_as_a_link(proc):
    assert proc.process_message("remember to read about vector databases") == {
        "type": "text",
        "content": "remember to read about vector databases",
        "url": None,
    }


def test_the_note_the_user_wrote_is_kept_with_the_page(proc, monkeypatch):
    route(proc, monkeypatch, lambda url: page(ARTICLE))

    result = proc.process_message(
        "steal the onboarding flow from this https://example.com/x"
    )

    assert "steal the onboarding flow" in result["content"]
    assert "A real sentence" in result["content"]


def test_the_other_links_in_a_message_are_recorded(proc, monkeypatch):
    route(proc, monkeypatch, lambda url: page(ARTICLE))

    result = proc.process_message("https://example.com/a and https://example.com/b")

    assert result["url"] == "https://example.com/a"
    assert "https://example.com/b" in result["content"]


def test_a_link_hidden_behind_link_text_is_still_read(proc, monkeypatch):
    """Telegram keeps these in an entity — the message body has no URL at all."""
    fetched = []
    route(proc, monkeypatch, lambda url: fetched.append(url) or page(ARTICLE))

    result = proc.process_message(
        "read this", extra_urls=["https://example.com/hidden"]
    )

    assert result["url"] == "https://example.com/hidden"
    assert fetched == ["https://example.com/hidden"]


def test_a_hidden_link_does_not_duplicate_a_visible_one(proc, monkeypatch):
    route(proc, monkeypatch, lambda url: page(ARTICLE))

    result = proc.process_message(
        "see https://example.com/a", extra_urls=["https://example.com/a"]
    )

    assert "Also linked" not in result["content"]


# ── Ordinary web pages ──────────────────────────────────────────────────────


def test_a_page_is_read_without_its_scripts_and_furniture(proc, monkeypatch):
    html = """
    <html><head><title>My Page</title></head>
    <body>
      <nav><a href="/">Home</a> <a href="/pricing">Pricing</a></nav>
      <script>var noise = "should not appear";</script>
      <style>.x { color: red }</style>
      <article><h1>The Heading</h1><p>First paragraph.</p><p>Second paragraph.</p></article>
      <footer>Copyright nobody</footer>
    </body></html>
    """
    route(proc, monkeypatch, lambda url: FakeResponse(200, text=html))

    content = proc.process_url("https://example.com")["content"]

    assert "Title: My Page" in content
    assert "First paragraph." in content and "Second paragraph." in content
    assert "The Heading" in content
    for furniture in ("should not appear", "color: red", "Pricing", "Copyright"):
        assert furniture not in content


def test_the_opengraph_title_and_summary_are_preferred(proc, monkeypatch):
    html = """
    <html><head>
      <title>Site — Page | Brand</title>
      <meta property="og:title" content="The Real Title">
      <meta property="og:description" content="A one line summary of the page.">
      <meta property="og:site_name" content="Example Blog">
    </head><body><p>Body.</p></body></html>
    """
    route(proc, monkeypatch, lambda url: FakeResponse(200, text=html))

    content = proc.process_url("https://example.com/x")["content"]

    assert "Title: The Real Title" in content
    assert "Summary: A one line summary of the page." in content
    assert "Site: Example Blog" in content


def test_a_page_without_paragraphs_falls_back_to_its_body_text(proc, monkeypatch):
    html = "<html><head><title>T</title></head><body><div>Body words here</div></body></html>"
    route(proc, monkeypatch, lambda url: FakeResponse(200, text=html))

    assert "Body words here" in proc.process_url("https://example.com")["content"]


def test_a_page_is_decoded_by_its_declared_charset(proc, monkeypatch):
    """An unlabelled page is not Latin-1, whatever requests assumes."""
    html = (
        '<html><head><meta charset="utf-8"><title>Café</title></head>'
        f"<body>{ARTICLE}<p>naïve résumé</p></body></html>"
    ).encode("utf-8")
    route(proc, monkeypatch, lambda url: FakeResponse(
        200, body=html, content_type="text/html"
    ))

    content = proc.process_url("https://example.com")["content"]

    assert "Café" in content and "naïve résumé" in content


def test_plain_text_and_json_links_are_read_as_they_are(proc, monkeypatch):
    body = "# A markdown document\n" + ("Some prose about a thing. " * 12)
    route(proc, monkeypatch, lambda url: FakeResponse(
        200, text=body, content_type="text/plain; charset=utf-8"
    ))

    content = proc.process_url("https://example.com/notes.txt")["content"]

    assert "A markdown document" in content


def test_a_pdf_link_is_described_rather_than_scraped_as_gibberish(proc, monkeypatch):
    route(proc, monkeypatch, lambda url: FakeResponse(
        200, body=b"%PDF-1.7\n" + b"\x00" * 5000, content_type="application/pdf"
    ))

    result = proc.process_url("https://example.com/papers/attention-is-all-you-need.pdf")

    assert result["type"] == "url"
    assert "PDF document" in result["content"]
    assert "attention is all you need" in result["content"]


def test_a_huge_download_is_cut_off(proc, monkeypatch):
    """One link to a disk image must not hold up the whole sync."""
    import processor as processor_module

    monkeypatch.setattr(processor_module, "MAX_DOWNLOAD_BYTES", 4096)
    route(proc, monkeypatch, lambda url: FakeResponse(
        200, body=b"x" * 200_000, content_type="application/octet-stream"
    ))

    result = proc.process_url("https://example.com/big.iso")

    assert result["type"] in ("url", "link")


# ── Links that cannot be read ───────────────────────────────────────────────


def test_a_dead_link_is_still_saved_from_its_address(proc, monkeypatch):
    """
    The whole point: a link that cannot be fetched is not a lost save. The
    address alone still says which site it was and what it was about.
    """
    route(proc, monkeypatch, lambda url: requests.ConnectionError("no route to host"))

    result = proc.process_url("https://www.anthropic.com/news/claude-opus-5")

    assert result["type"] == "link"
    assert result["url"] == "https://www.anthropic.com/news/claude-opus-5"
    assert result["readable"] is False
    assert "anthropic.com" in result["content"]
    assert "claude opus 5" in result["content"]


def test_a_dead_link_still_carries_the_note_the_user_wrote(proc, monkeypatch):
    """When the page is unreadable, the user's own words are all there is."""
    route(proc, monkeypatch, lambda url: requests.ConnectionError("down"))

    result = proc.process_message(
        "the pricing page that inspired ours https://example.com/pricing-x"
    )

    assert "the pricing page that inspired ours" in result["content"]


@pytest.mark.parametrize("status, expected", [
    (403, "refused an automated request"),
    (404, "the page is gone"),
    (429, "rate limiting"),
    (401, "login or a paywall"),
])
def test_why_a_page_could_not_be_read_is_written_down(
    proc, monkeypatch, no_sleep, status, expected
):
    route(proc, monkeypatch, lambda url: FakeResponse(status, url=url))

    assert expected in proc.process_url("https://example.com/x")["content"]


def test_nothing_a_page_can_do_raises_out_of_the_processor(proc, monkeypatch, no_sleep):
    """A raised error would wedge the Telegram queue behind this link forever."""
    for outcome in (
        requests.ConnectionError("reset"),
        requests.Timeout("timed out"),
        ValueError("nonsense"),
        FakeResponse(500, url="https://example.com"),
    ):
        route(proc, monkeypatch, lambda url, o=outcome: o)

        result = proc.process_url("https://example.com/x")

        assert result["url"] == "https://example.com/x"
        assert result["content"]


# ── The archive fallback ────────────────────────────────────────────────────


def test_a_blocked_page_is_read_from_the_internet_archive(archiving, monkeypatch):
    def handler(url):
        if url.startswith("https://archive.org/wayback/available"):
            return FakeResponse(200, payload={"archived_snapshots": {"closest": {
                "available": True,
                "url": "http://web.archive.org/web/20240101/https://example.com/x",
                "timestamp": "20240101000000",
            }}})
        if url.startswith("http://web.archive.org/"):
            return page(ARTICLE)
        return FakeResponse(403, url=url)

    route(archiving, monkeypatch, handler)

    result = archiving.process_url("https://example.com/x")

    assert result["type"] == "url"
    assert "A real sentence" in result["content"]
    assert "Internet Archive" in result["content"]


def test_a_javascript_shell_falls_back_to_the_archive(archiving, monkeypatch):
    """A page that renders nothing server-side is not a page we can read."""
    def handler(url):
        if url.startswith("https://archive.org/wayback/available"):
            return FakeResponse(200, payload={"archived_snapshots": {"closest": {
                "available": True, "url": "http://web.archive.org/x", "timestamp": "20230515",
            }}})
        if url.startswith("http://web.archive.org/"):
            return page(ARTICLE)
        return page('<div id="root"></div>')

    route(archiving, monkeypatch, handler)

    assert "A real sentence" in archiving.process_url("https://example.com/x")["content"]


def test_a_link_the_archive_never_saw_falls_back_to_its_address(archiving, monkeypatch):
    def handler(url):
        if url.startswith("https://archive.org/"):
            return FakeResponse(200, payload={"archived_snapshots": {}})
        return FakeResponse(403, url=url)

    route(archiving, monkeypatch, handler)

    result = archiving.process_url("https://example.com/guides/prompt-caching")

    assert result["type"] == "link"
    assert "prompt caching" in result["content"]


def test_an_archive_outage_does_not_break_the_save(archiving, monkeypatch):
    def handler(url):
        if url.startswith("https://archive.org/"):
            return requests.ConnectionError("archive.org is down")
        return FakeResponse(403, url=url)

    route(archiving, monkeypatch, handler)

    assert archiving.process_url("https://example.com/x")["type"] == "link"


def test_the_archive_is_not_consulted_when_it_is_switched_off(proc, monkeypatch):
    calls = route(proc, monkeypatch, lambda url: FakeResponse(403, url=url))

    proc.process_url("https://example.com/x")

    assert not any("archive.org" in call for call in calls)


# ── Retrying ────────────────────────────────────────────────────────────────


def test_a_flaky_fetch_is_retried_before_giving_up(proc, monkeypatch, no_sleep):
    """One dropped connection shouldn't become a note that says 'failed'."""
    attempts = []

    def handler(url):
        attempts.append(url)
        if len(attempts) < 2:
            return requests.ConnectionError("connection reset by peer")
        return page(ARTICLE)

    route(proc, monkeypatch, handler)

    result = proc.process_url("https://example.com")

    assert "A real sentence" in result["content"]
    assert len(attempts) == 2


def test_a_502_from_a_page_is_retried(proc, monkeypatch, no_sleep):
    attempts = []

    def handler(url):
        attempts.append(url)
        return FakeResponse(502, url=url) if len(attempts) < 2 else page(ARTICLE)

    route(proc, monkeypatch, handler)

    assert "A real sentence" in proc.process_url("https://example.com")["content"]
    assert len(attempts) == 2


def test_a_404_is_not_retried(proc, monkeypatch, no_sleep):
    """A missing page will still be missing on the third try."""
    attempts = []

    def handler(url):
        attempts.append(url)
        return FakeResponse(404, url=url)

    route(proc, monkeypatch, handler)

    assert proc.process_url("https://example.com")["type"] == "link"
    assert len(attempts) == 1


# ── YouTube ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", [
    "https://youtube.com/watch?v=abc",
    "https://www.youtube.com/watch?v=abc",
    "https://m.youtube.com/watch?v=abc",
    "https://music.youtube.com/watch?v=abc",
    "https://www.youtube-nocookie.com/embed/abc",
    "https://youtu.be/abc",
    "https://youtube.com/shorts/abc",
])
def test_youtube_hosts_route_to_youtube(proc, monkeypatch, url):
    monkeypatch.setattr(proc, "_youtube", lambda u: {"type": "youtube", "url": u})

    assert proc.process_url(url)["type"] == "youtube"


def test_a_video_is_read_through_yt_dlp(proc, monkeypatch):
    monkeypatch.setattr(proc, "_ytdlp", lambda url: "Title: A Talk\nChannel: Someone")

    result = proc.process_url("https://youtu.be/abc")

    assert result["type"] == "youtube"
    assert "A Talk" in result["content"]


def test_a_video_falls_back_to_oembed_when_yt_dlp_fails(proc, monkeypatch):
    """yt-dlp breaks whenever YouTube changes something; oEmbed does not."""
    monkeypatch.setattr(proc, "_ytdlp", lambda url: None)
    route(proc, monkeypatch, lambda url: FakeResponse(
        200, payload={"title": "A Talk About RAG", "author_name": "Some Channel"},
        content_type="application/json",
    ))

    result = proc.process_url("https://youtu.be/abc")

    assert result["type"] == "youtube"
    assert "A Talk About RAG" in result["content"]
    assert "Some Channel" in result["content"]


def test_a_video_that_answers_nothing_still_produces_a_note(proc, monkeypatch, no_sleep):
    monkeypatch.setattr(proc, "_ytdlp", lambda url: None)
    route(proc, monkeypatch, lambda url: requests.ConnectionError("blocked"))

    result = proc.process_url("https://www.youtube.com/watch?v=abc")

    assert result["content"]
    assert result["url"] == "https://www.youtube.com/watch?v=abc"


# ── GitHub ──────────────────────────────────────────────────────────────────


def test_a_github_lookalike_host_is_not_routed_to_github(proc, monkeypatch):
    monkeypatch.setattr(proc, "_github", lambda u: {"type": "github", "url": u})
    monkeypatch.setattr(proc, "_webpage", lambda u: {"type": "url", "url": u})

    assert proc.process_url("https://evil.com/github.com/a/b")["type"] == "url"
    assert proc.process_url("https://github.com/a/b")["type"] == "github"


def test_a_repo_is_read_through_the_readme_endpoint(proc, monkeypatch):
    """One call, any default branch, any README file name."""
    def handler(url):
        if url == "https://api.github.com/repos/acme/widget":
            return FakeResponse(200, payload={
                "description": "A widget for widgets",
                "language": "Python",
                "stargazers_count": 1200,
                "topics": ["widgets", "cli"],
                "default_branch": "trunk",
            })
        if url == "https://api.github.com/repos/acme/widget/readme":
            return FakeResponse(200, text="# The Readme")
        return None

    calls = route(proc, monkeypatch, handler)

    content = proc.process_url("https://github.com/acme/widget")["content"]

    assert "acme/widget" in content
    assert "A widget for widgets" in content
    assert "Python" in content and "1200 stars" in content
    assert "The Readme" in content
    # No branch guessing needed at all.
    assert not any("raw.githubusercontent.com" in call for call in calls)


def test_a_repo_falls_back_to_raw_branches_when_the_api_is_unavailable(proc, monkeypatch):
    """The API allows 60 calls an hour per IP; raw files have no such limit."""
    def handler(url):
        if url.startswith("https://api.github.com/"):
            return FakeResponse(403, url=url)  # rate limited
        if "/master/README.md" in url:
            return FakeResponse(200, text="# From master")
        return None

    route(proc, monkeypatch, handler)

    assert "From master" in proc.process_url("https://github.com/acme/widget")["content"]


def test_a_repo_with_no_readme_still_uses_what_the_api_knows(proc, monkeypatch):
    def handler(url):
        if url == "https://api.github.com/repos/acme/widget":
            return FakeResponse(200, payload={"description": "A widget", "topics": []})
        return None

    route(proc, monkeypatch, handler)

    content = proc.process_url("https://github.com/acme/widget")["content"]

    assert "A widget" in content
    assert "no README" in content


def test_a_private_or_missing_repo_falls_back_to_scraping_the_page(proc, monkeypatch):
    route(proc, monkeypatch, lambda url: FakeResponse(404, url=url))
    monkeypatch.setattr(proc, "_webpage", lambda u: {"type": "url", "url": u})

    assert proc.process_url("https://github.com/acme/widget")["type"] == "url"


@pytest.mark.parametrize("url", [
    "https://github.com/features/copilot",
    "https://github.com/pricing",
    "https://github.com/topics/rag",
])
def test_github_pages_that_are_not_repos_are_scraped(proc, monkeypatch, url):
    monkeypatch.setattr(proc, "_webpage", lambda u: {"type": "url", "url": u})

    assert proc.process_url(url)["type"] == "url"


def test_an_issue_link_is_read_as_the_issue(proc, monkeypatch):
    def handler(url):
        if url == "https://api.github.com/repos/acme/widget/issues/42":
            return FakeResponse(200, payload={
                "title": "Search returns nothing",
                "state": "open",
                "user": {"login": "someone"},
                "body": "The index looks empty after a rebuild.",
            })
        return None

    route(proc, monkeypatch, handler)

    content = proc.process_url("https://github.com/acme/widget/issues/42")["content"]

    assert "Search returns nothing" in content
    assert "The index looks empty" in content


def test_a_pull_request_link_says_it_is_a_pull_request(proc, monkeypatch):
    route(proc, monkeypatch, lambda url: FakeResponse(200, payload={
        "title": "Fix the parser", "state": "merged",
        "user": {"login": "someone"}, "pull_request": {"url": "..."},
    }) if "issues/7" in url else None)

    content = proc.process_url("https://github.com/acme/widget/pull/7")["content"]

    assert content.startswith("Pull request in acme/widget: Fix the parser")


def test_a_link_to_a_file_reads_the_file(proc, monkeypatch):
    def handler(url):
        if url == "https://raw.githubusercontent.com/acme/widget/main/src/app.py":
            return FakeResponse(200, text="print('hello')")
        return None

    route(proc, monkeypatch, handler)

    content = proc.process_url(
        "https://github.com/acme/widget/blob/main/src/app.py"
    )["content"]

    assert "src/app.py" in content
    assert "print('hello')" in content


def test_a_gist_is_read_through_the_api(proc, monkeypatch):
    def handler(url):
        if url == "https://api.github.com/gists/abc123":
            return FakeResponse(200, payload={
                "description": "A handy snippet",
                "files": {"snippet.py": {"content": "x = 1"}},
            })
        return None

    route(proc, monkeypatch, handler)

    content = proc.process_url("https://gist.github.com/someone/abc123")["content"]

    assert "A handy snippet" in content
    assert "snippet.py" in content and "x = 1" in content


def test_a_stale_github_token_is_dropped_rather_than_failing_the_read(proc, monkeypatch):
    """A wrong token turns a repo anyone can read into a 401. No token is better."""
    import config as config_module

    monkeypatch.setattr(config_module, "GITHUB_TOKEN", "stale-token")
    tried = []

    def fake_get(url, **kwargs):
        authorization = (kwargs.get("headers") or {}).get("Authorization")
        tried.append(authorization)
        if authorization:
            return FakeResponse(401, url=url)
        if url.endswith("/readme"):
            return FakeResponse(200, text="# The Readme")
        return FakeResponse(200, payload={"description": "A widget"})

    monkeypatch.setattr(proc.session, "get", fake_get)

    content = proc.process_url("https://github.com/acme/widget")["content"]

    assert "The Readme" in content
    assert tried[0] == "Bearer stale-token" and tried[1] is None


def test_an_unreachable_github_falls_back_to_scraping(proc, monkeypatch, no_sleep):
    route(proc, monkeypatch, lambda url: requests.ConnectionError("connection reset"))
    monkeypatch.setattr(proc, "_webpage", lambda u: {"type": "url", "url": u})

    assert proc.process_url("https://github.com/acme/widget")["type"] == "url"
