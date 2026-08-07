"""
Turning a link into something worth saving.

The job here is not "fetch a URL" — it is "make sure a save survives". A link
someone sent from their phone is worth keeping even when the page behind it is
paywalled, JavaScript-only, rate-limiting us, or simply gone, so nothing in
here is allowed to come back empty-handed. The chain is always:

    the real page  →  an archived copy  →  the address itself

and the last step cannot fail. What it produces is thinner than a scraped
article, but it still carries the URL, the site, the words in the address and
whatever the user typed alongside it, which is enough for a note that can be
found again later.

Failures degrade rather than raise on purpose: a site that is down for good
must not wedge the Telegram queue behind it on every future sync.
"""

import json
import re
from urllib.parse import quote, urlsplit

import requests
from bs4 import BeautifulSoup

import config
import links
from transient import with_retries

try:  # Optional: only YouTube links need it, and only for the rich metadata.
    import yt_dlp
except ImportError:  # pragma: no cover - exercised by the oEmbed fallback
    yt_dlp = None

REQUEST_TIMEOUT = 12
ARCHIVE_TIMEOUT = 8

# How much extracted text to keep. The model truncates again on its side; this
# just stops a 4 MB page from being carried around in memory as one string.
MAX_EXTRACT_CHARS = 6000

# Nothing we want to read is bigger than this, and streaming to a cap means a
# stray link to a disk image can't stall a sync.
MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024

# Below this, a page is a shell — a cookie wall, a login gate, or an empty
# JavaScript mount point — and an archived copy is usually better.
MIN_ARTICLE_CHARS = 200
MIN_SUMMARY_CHARS = 80

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    # Sites that serve a 403 to anything that looks scripted usually key off a
    # missing Accept or Accept-Language rather than the User-Agent alone.
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}

YOUTUBE_HOSTS = {
    "youtube.com", "youtu.be", "youtube-nocookie.com", "yt.be",
}

# Paths under github.com that are pages, not repositories.
GITHUB_RESERVED = {
    "about", "account", "apps", "blog", "codespaces", "collections", "contact",
    "dashboard", "enterprise", "events", "explore", "features", "join", "login",
    "marketplace", "new", "notifications", "orgs", "pricing", "pulls", "readme",
    "search", "security", "settings", "signup", "site", "sponsors", "topics",
    "trending", "users",
}

# Tags that never carry the content of a page, only the furniture around it.
_CHROME_TAGS = [
    "script", "style", "noscript", "template", "svg", "iframe", "form",
    "nav", "header", "footer", "aside", "button", "select",
]

_TEXT_TAGS = ["h1", "h2", "h3", "h4", "p", "li", "blockquote", "pre", "dd", "figcaption"]

_TEXTUAL_TYPES = {
    "application/json", "application/ld+json", "application/xml",
    "application/rss+xml", "application/atom+xml", "application/x-yaml",
    "application/javascript", "application/sql",
}

_HTML_TYPES = {"text/html", "application/xhtml+xml"}


class Fetched:
    """A response body we have already read, with the cap applied."""

    def __init__(self, url: str, status: int, content_type: str, body: bytes):
        self.url = url
        self.status = status
        self.content_type = content_type
        self.body = body


def _reason(exc: Exception) -> str:
    """A short phrase naming why a fetch failed, for the note to carry."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 403:
        return "HTTP 403 — the site refused an automated request"
    if status == 404:
        return "HTTP 404 — the page is gone"
    if status == 429:
        return "HTTP 429 — the site is rate limiting us"
    if status in (401, 402, 451):
        return f"HTTP {status} — the page is behind a login or a paywall"
    if status:
        return f"HTTP {status}"
    return str(exc).strip() or exc.__class__.__name__


def _decode(fetched: Fetched) -> str:
    """Decode a text body, preferring the declared charset over guesswork."""
    match = re.search(r"charset=([\w-]+)", fetched.content_type, re.IGNORECASE)
    if match:
        try:
            return fetched.body.decode(match.group(1), errors="replace")
        except LookupError:
            pass
    return fetched.body.decode("utf-8", errors="replace")


def _human_size(num_bytes: int) -> str:
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MB"
    return f"{max(num_bytes // 1024, 1)} KB"


class ContentProcessor:
    def __init__(self, session=None, archive_fallback: bool = None):
        self.session = session or requests.Session()
        self.session.headers.update(BROWSER_HEADERS)
        self.archive_fallback = (
            config.ARCHIVE_FALLBACK if archive_fallback is None else archive_fallback
        )

    # ── Fetching ────────────────────────────────────────────────────────────

    def _read(self, response, url: str) -> Fetched:
        chunks, total = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= MAX_DOWNLOAD_BYTES:
                break
        response.close()

        return Fetched(
            url=getattr(response, "url", url) or url,
            status=getattr(response, "status_code", 200),
            content_type=(response.headers or {}).get("Content-Type", ""),
            body=b"".join(chunks),
        )

    def _get(self, url: str, timeout: int = REQUEST_TIMEOUT, retry: bool = True) -> Fetched:
        """
        Fetch a URL, retrying briefly through a blip so a moment's flakiness
        doesn't turn a saved link into a note that says it failed.
        """
        def fetch():
            response = self.session.get(url, timeout=timeout, stream=True, allow_redirects=True)
            # Inside the retry so a 503 from the page is retried too, not just
            # a connection that never landed.
            response.raise_for_status()
            return self._read(response, url)

        return with_retries(fetch) if retry else fetch()

    # ── Entry points ────────────────────────────────────────────────────────

    def is_url(self, text: str) -> bool:
        return links.has_url(text)

    def extract_url(self, text: str) -> str | None:
        return links.first_url(text)

    def process_message(self, text: str, extra_urls: list[str] = None) -> dict:
        """
        Read a message: the links in it, and the note the user wrote around
        them.

        `extra_urls` carries the links Telegram keeps outside the visible text
        — a hyperlink hidden behind link text has no URL in the body at all.
        """
        found = links.find_urls(text)
        for extra in extra_urls or []:
            normalized = links.normalize(extra)
            if normalized and normalized not in found:
                found.append(normalized)

        if not found:
            return {"type": "text", "content": text, "url": None}

        result = self.process_url(found[0])
        result["content"] = self._with_context(
            result["content"], links.strip_urls(text), found[1:]
        )
        return result

    def process_url(self, url: str) -> dict:
        host = (urlsplit(url).hostname or "").lower()
        host = re.sub(r"^(www|m|mobile|amp)\.", "", host)

        if host in YOUTUBE_HOSTS or host.endswith(".youtube.com"):
            return self._youtube(url)
        if host == "github.com":
            return self._github(url)
        if host == "gist.github.com":
            return self._gist(url)
        return self._webpage(url)

    @staticmethod
    def _with_context(content: str, note: str, other_urls: list[str]) -> str:
        """
        Put the user's own words in front of whatever was scraped.

        "worth stealing the onboarding flow from this" is often the single most
        useful line in the note — and when the page could not be read, it is
        the only thing that says why the link was saved at all.
        """
        prefix = []
        if note:
            prefix.append(f"The user saved this link with the note: {note}")
        if other_urls:
            prefix.append("Also linked in the same message: " + ", ".join(other_urls))
        if not prefix:
            return content
        return "\n".join(prefix) + "\n\n" + content

    # ── The last resort ─────────────────────────────────────────────────────

    def _unreadable(self, url: str, reason: str) -> dict:
        """
        A note built from the address alone.

        This is what makes a link that cannot be fetched still worth saving:
        "/news/claude-opus-5" on anthropic.com says a great deal more than a
        note that only records that something failed.
        """
        host, slug = links.describe(url)
        return {
            "type": "link",
            "url": url,
            "readable": False,
            "content": (
                f"Title: {slug or host}\n"
                f"Site: {host}\n"
                f"Link: {url}\n\n"
                f"The page itself could not be read ({reason}), so there is no "
                "body text to work from. Write the note from the link: the "
                "site it points at, the words in the address, and any note the "
                "user attached to it. Say plainly that the page was not read."
            ),
        }

    # ── Web pages ───────────────────────────────────────────────────────────

    def _webpage(self, url: str) -> dict:
        thin, reason = None, "no readable text"

        try:
            content, rich = self._render(self._get(url))
            if content and rich:
                return {"type": "url", "content": content, "url": url}
            thin = content
        except Exception as exc:
            reason = _reason(exc)

        archived = self._from_archive(url)
        if archived:
            return {"type": "url", "content": archived, "url": url}
        if thin:
            return {"type": "url", "content": thin, "url": url}
        return self._unreadable(url, reason)

    def _render(self, fetched: Fetched) -> tuple[str | None, bool]:
        """
        Turn a fetched body into note-ready text.

        Returns the text and whether it is substantial enough to stop here; a
        thin result is kept as a fallback but an archived copy is tried first.
        """
        content_type = fetched.content_type.split(";")[0].strip().lower()
        looks_like_html = not content_type and b"<html" in fetched.body[:2048].lower()

        if content_type in _HTML_TYPES or looks_like_html:
            return self._from_html(fetched)

        if content_type.startswith("text/") or content_type in _TEXTUAL_TYPES:
            text = _decode(fetched).strip()
            if not text:
                return None, False
            host, slug = links.describe(fetched.url)
            return (
                f"Title: {slug or host}\nSite: {host}\n\nContent:\n"
                f"{text[:MAX_EXTRACT_CHARS]}",
                len(text) >= MIN_ARTICLE_CHARS,
            )

        # A PDF, an image or a video. There is no text to pull out without
        # dragging in a parser for each format, but the note should still say
        # what the link actually is rather than claiming it failed.
        return self._describe_file(fetched, content_type), True

    def _describe_file(self, fetched: Fetched, content_type: str) -> str:
        host, slug = links.describe(fetched.url)
        kind = {
            "application/pdf": "a PDF document",
            "application/zip": "a zip archive",
            "application/epub+zip": "an EPUB book",
        }.get(content_type)
        if not kind and content_type:
            family = content_type.split("/")[0]
            kind = {
                "image": "an image", "video": "a video", "audio": "an audio file",
            }.get(family, f"a {content_type} file")

        return (
            f"Title: {slug or host}\n"
            f"Site: {host}\n"
            f"Link: {fetched.url}\n\n"
            f"The link is {kind or 'a file'} ({_human_size(len(fetched.body))}), "
            "not a web page, so its text was not extracted. Write the note from "
            "the link, the file type and any note the user attached to it."
        )

    def _from_html(self, fetched: Fetched) -> tuple[str | None, bool]:
        # Bytes, not str: BeautifulSoup reads the charset off the meta tags,
        # which beats the ISO-8859-1 requests assumes for an unlabelled page.
        soup = BeautifulSoup(fetched.body, "html.parser")

        title = (
            self._meta(soup, "og:title")
            or self._meta(soup, "twitter:title")
            or (soup.title.get_text(strip=True) if soup.title else "")
            or (soup.h1.get_text(" ", strip=True) if soup.h1 else "")
        )
        summary = (
            self._meta(soup, "og:description")
            or self._meta(soup, "description")
            or self._meta(soup, "twitter:description")
        )
        site = self._meta(soup, "og:site_name")

        for tag in soup(_CHROME_TAGS):
            tag.decompose()

        root = (
            soup.find("article")
            or soup.find("main")
            or soup.find(attrs={"role": "main"})
            or soup.body
            or soup
        )
        body_text = self._readable_text(root)

        host = urlsplit(fetched.url).hostname or ""
        header = [f"Title: {title or links.describe(fetched.url)[1] or host}"]
        header.append(f"Site: {site or host}")
        if summary:
            header.append(f"Summary: {summary}")

        if not body_text and not summary:
            return None, False

        content = "\n".join(header)
        if body_text:
            content += f"\n\nContent:\n{body_text[:MAX_EXTRACT_CHARS]}"

        rich = len(body_text) >= MIN_ARTICLE_CHARS or len(summary) >= MIN_SUMMARY_CHARS
        return content, rich

    @staticmethod
    def _meta(soup, key: str) -> str:
        tag = soup.find("meta", attrs={"property": key}) or soup.find(
            "meta", attrs={"name": key}
        )
        return (tag.get("content") or "").strip() if tag else ""

    @staticmethod
    def _readable_text(root) -> str:
        """Collect the text-bearing elements, skipping nested repeats."""
        blocks, seen, previous = [], set(), ""
        for element in root.find_all(_TEXT_TAGS):
            text = element.get_text(" ", strip=True)
            # find_all walks parents before children, so a nested <li> or a <p>
            # inside a <blockquote> arrives as a substring of the block before.
            if not text or text in seen or text in previous:
                continue
            seen.add(text)
            previous = text
            blocks.append(f"## {text}" if element.name.startswith("h") else text)

        if blocks:
            return "\n".join(blocks).strip()
        # Some pages render body text with no semantic tags at all.
        return root.get_text("\n", strip=True)

    def _from_archive(self, url: str) -> str | None:
        """
        Fall back to the Internet Archive's copy of the page.

        Dead links, paywalls and sites that refuse scripted requests are the
        ordinary fate of a bookmark a year later, and for all three the
        archived copy is usually complete.
        """
        if not self.archive_fallback:
            return None

        try:
            response = self.session.get(
                "https://archive.org/wayback/available",
                params={"url": url},
                timeout=ARCHIVE_TIMEOUT,
            )
            snapshot = (
                (response.json().get("archived_snapshots") or {}).get("closest") or {}
            )
            if not snapshot.get("available") or not snapshot.get("url"):
                return None

            content, rich = self._render(
                self._get(snapshot["url"], timeout=ARCHIVE_TIMEOUT, retry=False)
            )
            if not content or not rich:
                return None

            stamp = str(snapshot.get("timestamp", ""))[:8]
            return (
                f"{content}\n\n(The live page could not be read; this is the "
                f"Internet Archive's snapshot from {stamp or 'an earlier date'}.)"
            )
        except Exception:
            # The archive is a bonus. If it is down, or has never seen the
            # page, the caller falls through to the address-only note.
            return None

    # ── YouTube ─────────────────────────────────────────────────────────────

    def _youtube(self, url: str) -> dict:
        info = self._ytdlp(url)
        if info:
            return {"type": "youtube", "content": info, "url": url}

        oembed = self._oembed(url)
        if oembed:
            return {"type": "youtube", "content": oembed, "url": url}

        # The watch page still carries og:title and og:description.
        result = self._webpage(url)
        if result["type"] == "url":
            result["type"] = "youtube"
        return result

    def _ytdlp(self, url: str) -> str | None:
        if yt_dlp is None:
            return None
        try:
            options = {
                "quiet": True,
                "no_warnings": True,
                # Metadata only — we never download the video itself.
                "skip_download": True,
                "extract_flat": True,
                "socket_timeout": REQUEST_TIMEOUT,
            }
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            # yt-dlp breaks whenever YouTube changes something, and it is the
            # single most common reason a video link used to fail. oEmbed does
            # not go stale in the same way.
            return None

        if not info:
            return None

        lines = [f"Title: {info.get('title') or 'Unknown YouTube Video'}"]
        channel = info.get("uploader") or info.get("channel")
        if channel:
            lines.append(f"Channel: {channel}")
        if info.get("duration"):
            lines.append(f"Duration: {int(info['duration']) // 60} min")
        description = (info.get("description") or "").strip()
        if description:
            lines.append(f"\nDescription:\n{description[:MAX_EXTRACT_CHARS]}")
        return "\n".join(lines)

    def _oembed(self, url: str) -> str | None:
        try:
            response = self.session.get(
                "https://www.youtube.com/oembed",
                params={"url": url, "format": "json"},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                return None
            data = response.json()
        except Exception:
            return None

        title = (data or {}).get("title")
        if not title:
            return None
        lines = [f"Title: {title}"]
        if data.get("author_name"):
            lines.append(f"Channel: {data['author_name']}")
        lines.append(
            "\n(Only the video's title and channel could be read — its "
            "description was not available.)"
        )
        return "\n".join(lines)

    # ── GitHub ──────────────────────────────────────────────────────────────

    def _github_api(self, path: str, raw: bool = False, timeout: int = REQUEST_TIMEOUT):
        """GET the GitHub API, returning None for anything that isn't a 200."""
        headers = {
            "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"
        }
        attempts = [headers]
        if config.GITHUB_TOKEN:
            # A stale or wrong token is worse than no token at all: it turns a
            # public repo that anyone can read into a 401. Try it, then drop it.
            attempts.insert(0, {**headers, "Authorization": f"Bearer {config.GITHUB_TOKEN}"})

        for attempt in attempts:
            try:
                response = self.session.get(
                    f"https://api.github.com/{path}", headers=attempt, timeout=timeout
                )
                if response.status_code == 200:
                    return response.text if raw else response.json()
                if response.status_code not in (401, 403):
                    return None
            except (requests.RequestException, ValueError, json.JSONDecodeError):
                return None
        return None

    def _github(self, url: str) -> dict:
        parts = [p for p in urlsplit(url).path.strip("/").split("/") if p]
        if len(parts) < 2 or parts[0].lower() in GITHUB_RESERVED:
            return self._webpage(url)

        owner, repo = parts[0], parts[1].removesuffix(".git")

        if len(parts) >= 4 and parts[2] in ("issues", "pull") and parts[3].isdigit():
            issue = self._github_issue(owner, repo, parts[3])
            if issue:
                return {"type": "github", "content": issue, "url": url}
        elif len(parts) >= 5 and parts[2] in ("blob", "raw"):
            source = self._github_file(owner, repo, parts[3], "/".join(parts[4:]))
            if source:
                return {"type": "github", "content": source, "url": url}

        return self._github_repo(owner, repo, url)

    def _github_repo(self, owner: str, repo: str, url: str) -> dict:
        meta = self._github_api(f"repos/{owner}/{repo}")
        # One call, any default branch, any README file name — which is why it
        # comes before probing raw.githubusercontent.com by hand.
        readme = self._github_api(f"repos/{owner}/{repo}/readme", raw=True)
        if readme is None:
            readme = self._probe_readme(owner, repo, (meta or {}).get("default_branch"))

        if readme is None and not meta:
            # Private, renamed, or the API is rate limited: scrape the page.
            return self._webpage(url)

        lines = [f"Github Repo: {owner}/{repo}"]
        if meta:
            if meta.get("description"):
                lines.append(f"Description: {meta['description']}")
            facts = []
            if meta.get("language"):
                facts.append(meta["language"])
            if meta.get("stargazers_count"):
                facts.append(f"{meta['stargazers_count']} stars")
            if facts:
                lines.append(" · ".join(facts))
            if meta.get("topics"):
                lines.append("Topics: " + ", ".join(meta["topics"][:10]))

        if readme:
            lines.append(f"\nREADME:\n{readme[:MAX_EXTRACT_CHARS]}")
        else:
            lines.append("\n(This repository has no README.)")

        return {"type": "github", "content": "\n".join(lines), "url": url}

    def _probe_readme(self, owner: str, repo: str, default_branch: str = None) -> str | None:
        """Fall back to raw.githubusercontent.com when the API is unavailable."""
        branches = []
        for branch in (default_branch, "main", "master"):
            if branch and branch not in branches:
                branches.append(branch)

        for branch in branches:
            for name in ("README.md", "readme.md", "README.rst", "README"):
                raw_url = (
                    f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{name}"
                )
                # Probing is expected to 404 its way down the list, so this
                # loop stays unretried.
                try:
                    response = self.session.get(raw_url, timeout=REQUEST_TIMEOUT)
                except requests.RequestException:
                    return None
                if response.status_code == 200:
                    return response.text
        return None

    def _github_issue(self, owner: str, repo: str, number: str) -> str | None:
        # Pull requests are issues as far as this endpoint is concerned.
        issue = self._github_api(f"repos/{owner}/{repo}/issues/{number}")
        if not issue:
            return None

        kind = "Pull request" if issue.get("pull_request") else "Issue"
        lines = [
            f"{kind} in {owner}/{repo}: {issue.get('title', '')}",
            f"State: {issue.get('state', 'unknown')} · "
            f"opened by {(issue.get('user') or {}).get('login', 'unknown')}",
        ]
        if issue.get("body"):
            lines.append(f"\n{issue['body'][:MAX_EXTRACT_CHARS]}")
        return "\n".join(lines)

    def _github_file(self, owner: str, repo: str, ref: str, path: str) -> str | None:
        raw_url = (
            f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/"
            f"{quote(path)}"
        )
        try:
            response = self.session.get(raw_url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException:
            return None
        if response.status_code != 200:
            return None
        return (
            f"File {path} in {owner}/{repo} (branch {ref}):\n\n"
            f"{response.text[:MAX_EXTRACT_CHARS]}"
        )

    def _gist(self, url: str) -> dict:
        parts = [p for p in urlsplit(url).path.strip("/").split("/") if p]
        gist_id = parts[-1] if parts else ""
        gist = self._github_api(f"gists/{gist_id}") if gist_id else None
        if not gist:
            return self._webpage(url)

        lines = [f"Github Gist: {gist.get('description') or gist_id}"]
        for name, meta in (gist.get("files") or {}).items():
            body = (meta or {}).get("content") or ""
            lines.append(f"\n--- {name} ---\n{body[:MAX_EXTRACT_CHARS]}")
        return {"type": "github", "content": "\n".join(lines), "url": url}
