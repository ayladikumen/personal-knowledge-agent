import re
from urllib.parse import urlsplit

import requests
import yt_dlp
from bs4 import BeautifulSoup

from transient import with_retries

# Trailing punctuation is almost always sentence punctuation rather than part of
# the link, so it is excluded from the final character class.
URL_PATTERN = re.compile(r'https?://[^\s<>"\']+[^\s<>"\'.,;:!?)\]}]')

REQUEST_TIMEOUT = 10
MAX_EXTRACT_CHARS = 5000


class ContentProcessor:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        })

    def _get(self, url: str, **kwargs):
        """
        Fetch a URL, retrying briefly through a blip so a moment's flakiness
        doesn't turn a saved link into a note that just says it failed.

        A failure here still degrades to an error note rather than propagating:
        a site that is down for good must not wedge the Telegram queue behind
        it on every future sync.
        """
        def fetch():
            response = self.session.get(url, **kwargs)
            # Inside the retry so a 503 from the page is retried too, not just
            # a connection that never landed.
            response.raise_for_status()
            return response

        return with_retries(fetch)

    def is_url(self, text: str) -> bool:
        return bool(URL_PATTERN.search(text or ""))

    def extract_url(self, text: str) -> str:
        match = URL_PATTERN.search(text or "")
        return match.group(0) if match else None

    def process_message(self, text: str) -> dict:
        url = self.extract_url(text)
        if not url:
            return {"type": "text", "content": text, "url": None}

        # Match on the parsed host, not a substring of the whole URL, so a link
        # like https://example.com/github.com/x isn't treated as a repo.
        host = (urlsplit(url).hostname or "").lower().removeprefix("www.")

        if host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"):
            return self._process_youtube(url)
        if host == "github.com":
            return self._process_github(url)
        return self._process_general_url(url)

    def _process_youtube(self, url: str) -> dict:
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                # Metadata only — we never download the video itself.
                "extract_flat": True,
                "socket_timeout": REQUEST_TIMEOUT,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            title = info.get("title", "Unknown YouTube Video")
            description = info.get("description") or ""
            channel = info.get("uploader") or info.get("channel") or ""

            content = f"Title: {title}"
            if channel:
                content += f"\nChannel: {channel}"
            content += f"\n\nDescription:\n{description[:MAX_EXTRACT_CHARS]}"

            return {"type": "youtube", "content": content, "url": url}
        except Exception as e:
            return {
                "type": "error",
                "content": f"Failed to extract YouTube info: {e}",
                "url": url,
            }

    def _github_default_branch(self, owner: str, repo: str) -> str:
        """Ask the GitHub API for the repo's default branch, falling back to main."""
        # Not retried: "main" is a perfectly good guess, so a slow retry here
        # buys nothing the fallback doesn't already give us.
        try:
            r = self.session.get(
                f"https://api.github.com/repos/{owner}/{repo}",
                timeout=REQUEST_TIMEOUT,
                headers={"Accept": "application/vnd.github+json"},
            )
            if r.status_code == 200:
                return r.json().get("default_branch") or "main"
        except requests.RequestException:
            pass
        return "main"

    def _process_github(self, url: str) -> dict:
        """Summarize a repo from its README, whatever the default branch is called."""
        try:
            parts = [p for p in urlsplit(url).path.strip("/").split("/") if p]
            if len(parts) < 2:
                return self._process_general_url(url)

            owner, repo = parts[0], parts[1].removesuffix(".git")

            branches = []
            default_branch = self._github_default_branch(owner, repo)
            for branch in (default_branch, "main", "master"):
                if branch not in branches:
                    branches.append(branch)

            for branch in branches:
                for name in ("README.md", "readme.md", "README.rst"):
                    raw_url = (
                        f"https://raw.githubusercontent.com/"
                        f"{owner}/{repo}/{branch}/{name}"
                    )
                    # Probing is expected to 404 its way down the list, so this
                    # loop stays unretried; a network failure drops straight to
                    # the fallback below, which does retry.
                    try:
                        r = self.session.get(raw_url, timeout=REQUEST_TIMEOUT)
                    except requests.RequestException:
                        break
                    if r.status_code == 200:
                        return {
                            "type": "github",
                            "content": (
                                f"Github Repo: {owner}/{repo}\n\n"
                                f"README:\n{r.text[:MAX_EXTRACT_CHARS]}"
                            ),
                            "url": url,
                        }

            # No README found (or a private repo) — fall back to scraping the page.
            return self._process_general_url(url)
        except Exception as e:
            return {
                "type": "error",
                "content": f"Failed to extract Github info: {e}",
                "url": url,
            }

    def _process_general_url(self, url: str) -> dict:
        try:
            r = self._get(url, timeout=REQUEST_TIMEOUT)
            soup = BeautifulSoup(r.text, "html.parser")

            title = soup.title.get_text(strip=True) if soup.title else "Unknown Page"

            # Scripts and styles leak minified JS into the summary otherwise.
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()

            paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
            text_content = "\n".join(p for p in paragraphs if p)

            # Some sites render body text without <p> tags at all.
            if not text_content.strip():
                text_content = soup.get_text("\n", strip=True)

            return {
                "type": "url",
                "content": (
                    f"Title: {title}\n\nContent:\n{text_content[:MAX_EXTRACT_CHARS]}"
                ),
                "url": url,
            }
        except Exception as e:
            return {
                "type": "error",
                "content": f"Failed to extract webpage: {e}",
                "url": url,
            }
