"""
Finding the links inside a message.

What arrives from Telegram is prose, not a URL. People send links wrapped in
parentheses, trailed by the sentence's full stop, bolded with asterisks, typed
without a scheme, pasted with an invisible character glued to the end, or
hidden behind link text so the URL is not in the message body at all. Every
one of those used to end up either ignored or fetched as a subtly wrong
address, which is the same thing as losing the save.

So the scan here is deliberately generous about what it will recognise, and
strict about what it hands back: whatever shape the link arrived in, the
fetcher gets an address `requests` can actually open.
"""

import html
import re
from urllib.parse import urlsplit, urlunsplit

# Zero-width and bidirectional marks. Copying a link out of a rendered page, or
# out of a right-to-left message, routinely glues one of these to an end — and
# it breaks the URL without leaving anything visible to explain why.
_INVISIBLE = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]"
)

# Punctuation that ends a sentence rather than a URL. Closing brackets are not
# in here: they are balanced separately, so a Wikipedia link that genuinely
# ends in ")" keeps it.
_TRAILING_JUNK = ".,;:!?*_~\"'`«»…”’-–—"

_CLOSERS = {")": "(", "]": "["}

# Top-level domains recognised when a link is typed without a scheme. It is a
# whitelist on purpose: "main.py" and "README.md" are file names, not domains,
# so the real ccTLDs that collide with common file extensions (.py, .sh, .md,
# .rs, .pl, .so, .cc, .zip, .mov) are deliberately absent. Writing "https://"
# in front still forces any of them through.
_BARE_TLDS = frozenset("""
com org net int edu gov mil info biz name pro mobi asia tel
io ai dev app co me gg tv xyz cloud page blog news tech site online store shop
wiki space live life world today work agency studio design digital software
systems tools media network solutions services ventures capital finance fund
link list host press review reviews rocks school science security social
support team technology tips travel university video vision zone codes chat
community computer database email engineering expert gallery guide help
institute ninja party photo photography plus run fyi one art bot game games
graphics group house family center city club company careers cafe coffee
consulting education energy events exchange fashion film fitness football
foundation gold golf green health hospital hotel insurance jobs kitchen land
lawyer loans market marketing money movie music partners pizza pub recipes
report rest restaurant sale shoes show soccer solar sport style supply tax
theater tours toys trade training vacations vet vin wine works
ly to gl id im ac
us uk ca au nz de fr es it nl be ch at se no dk fi ie pt cz gr hu ro
ru ua tr il ae sa za eg ng ke in cn jp kr hk tw sg my th vn ph br mx ar cl
pe ee lv lt si sk hr bg by kz ge am az np pk bd lk ir ma tn dz gh tz ug
mu mt cy lu li mc is
""".split())

_URL_RE = re.compile(
    r"""
    (?<![\w@.\-/])                          # not glued to an email or a path
    (?:
        (?P<scheme>https?:/{1,3})           # an explicit scheme always wins
      | (?P<www>www\.)                      # the classic schemeless form
      | (?=                                 # or a plain host with a real TLD
            [^\W\d_][\w-]*
            (?:\.[\w-]+)*
            \.(?P<tld>[A-Za-z]{2,24})
            (?![\w-])
        )
    )
    [^\s<>"'`\\^|{}\u2018\u2019\u201c\u201d]*
    """,
    re.VERBOSE | re.IGNORECASE,
)

_SCHEME_PREFIX = re.compile(r"^(https?):/{1,3}", re.IGNORECASE)


def _prepare(text: str) -> str:
    """Undo the escaping and invisible characters a pasted link picks up."""
    return _INVISIBLE.sub("", html.unescape(text or ""))


def _trim(candidate: str) -> str:
    """
    Drop the sentence punctuation that got swept up with the link.

    Closing brackets are only dropped when they are unmatched, so
    `...wiki/Python_(programming_language)` keeps its parenthesis while
    `(see https://example.com)` loses one.
    """
    while candidate:
        last = candidate[-1]
        if last in _TRAILING_JUNK:
            candidate = candidate[:-1]
            continue
        opener = _CLOSERS.get(last)
        if opener and candidate.count(last) > candidate.count(opener):
            candidate = candidate[:-1]
            continue
        break
    return candidate


def normalize(url: str) -> str | None:
    """
    Turn a recognised candidate into an address that can be fetched, or None
    if it turns out not to be one.
    """
    url = _prepare(url).strip()
    if not url:
        return None

    # "https:/example.com" and "https:///example.com" are both typos we can
    # read; neither is something requests will accept.
    if _SCHEME_PREFIX.match(url):
        url = _SCHEME_PREFIX.sub(lambda m: f"{m.group(1).lower()}://", url)
    elif "://" in url or url.split(":", 1)[0].lower() in ("mailto", "tel", "tg"):
        return None  # some other scheme — not ours to fetch
    else:
        url = f"https://{url}"

    try:
        scheme, netloc, path, query, fragment = urlsplit(url)
    except ValueError:
        return None

    # Host names are case-insensitive; the rest of a URL is not, and neither is
    # the "user:pass@" that lowercasing the whole netloc would destroy.
    if "@" not in netloc:
        netloc = netloc.lower()
    netloc = netloc.rstrip(".")

    host = netloc.rsplit("@", 1)[-1].rsplit(":", 1)[0].strip("[]")
    if not host or (host != "localhost" and "." not in host):
        return None

    return urlunsplit((scheme.lower(), netloc, path, query, fragment))


def _scan(prepared: str):
    """Yield (start, end, url) for every link in already-prepared text."""
    for match in _URL_RE.finditer(prepared):
        candidate = _trim(match.group(0))
        if not candidate:
            continue
        # A bare host is only a link if its TLD is one people actually browse.
        if match.group("scheme") is None and match.group("www") is None:
            if (match.group("tld") or "").lower() not in _BARE_TLDS:
                continue
        url = normalize(candidate)
        if url:
            yield match.start(), match.start() + len(candidate), url


def find_urls(text: str) -> list[str]:
    """Every link in `text`, normalized, in the order they were written."""
    seen, urls = set(), []
    for _, _, url in _scan(_prepare(text)):
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def first_url(text: str) -> str | None:
    urls = find_urls(text)
    return urls[0] if urls else None


def has_url(text: str) -> bool:
    return bool(find_urls(text))


def strip_urls(text: str) -> str:
    """
    What the user actually wrote, with the links taken out.

    A message is usually a link plus a reason for saving it, and that reason is
    often the most useful sentence in the note — especially when the page
    itself turns out to be unreadable.
    """
    prepared = _prepare(text)
    out, cursor = [], 0
    for start, end, _ in _scan(prepared):
        out.append(prepared[cursor:start])
        cursor = end
    out.append(prepared[cursor:])

    # Whatever the removed link was wrapped in or joined to is left behind:
    # "[the docs]()" keeps an empty pair, "(see )" a gap, "a and b" a dangling
    # conjunction.
    remainder = re.sub(r"\s+", " ", "".join(out)).strip()
    remainder = re.sub(r"([(\[])\s+", r"\1", remainder)
    remainder = re.sub(r"\s+([)\]])", r"\1", remainder)
    remainder = re.sub(r"\(\)|\[\]|<>", " ", remainder)
    remainder = _drop_orphan_brackets(remainder)
    remainder = re.sub(r"\s+", " ", remainder).strip(" -–—:;,|/<>\"'")
    # "compare A and B" loses its "and" with the second link, but "worth
    # stealing from" ends in a preposition on purpose — so only conjunctions.
    return re.sub(r"(?:\s|^)(?:and|or|plus|&)$", "", remainder, flags=re.I).strip()


def _drop_orphan_brackets(text: str) -> str:
    """Remove the brackets whose partner went with the link."""
    keep = [True] * len(text)
    for opener, closer in (("(", ")"), ("[", "]")):
        open_at = []
        for index, char in enumerate(text):
            if char == opener:
                open_at.append(index)
            elif char == closer:
                if open_at:
                    open_at.pop()
                else:
                    keep[index] = False
        for index in open_at:
            keep[index] = False
    return "".join(char for char, kept in zip(text, keep) if kept)


_SLUG_EXTENSION = re.compile(r"\.(html?|php|aspx?|jsp|md|pdf|txt)$", re.IGNORECASE)


def describe(url: str) -> tuple[str, str]:
    """
    The host, and a human-readable guess at the title taken from the URL.

    Used when a page cannot be read at all: "/news/claude-opus-5" still says
    considerably more about a save than "a link that failed" does.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")

    for segment in reversed([seg for seg in parts.path.split("/") if seg]):
        slug = _SLUG_EXTENSION.sub("", segment)
        slug = re.sub(r"[-_+]+", " ", slug).strip()
        # Skip opaque ids: "12345", "p", "3f9a2b7c1e".
        if len(slug) < 3 or not re.search(r"[A-Za-z]{3}", slug):
            continue
        if re.fullmatch(r"[0-9a-f]{8,}", slug, re.IGNORECASE):
            continue
        return host, slug
    return host, ""
