"""
The shapes a link actually arrives in.

Every case here came from a real message: a link in the middle of a sentence,
one wrapped in markdown, one typed without a scheme, one with an invisible
character pasted onto the end.
"""

import pytest

import links


@pytest.mark.parametrize("text, expected", [
    ("check this out https://example.com/x cool right", "https://example.com/x"),
    ("see https://example.com/page.", "https://example.com/page"),
    ("(https://example.com/a)", "https://example.com/a"),
    ("[the docs](https://example.com/docs)", "https://example.com/docs"),
    ("**https://example.com/bold**", "https://example.com/bold"),
    ("<https://example.com/angle>", "https://example.com/angle"),
    ('"https://example.com/quoted"', "https://example.com/quoted"),
    ("read this: https://example.com/a; then this", "https://example.com/a"),
])
def test_a_link_is_found_however_it_is_wrapped(text, expected):
    assert links.first_url(text) == expected


def test_a_url_that_really_ends_in_a_bracket_keeps_it():
    """The classic false positive: stripping the ")" makes it a 404."""
    url = "https://en.wikipedia.org/wiki/Python_(programming_language)"

    assert links.first_url(f"see {url} for more") == url
    assert links.first_url(f"[python]({url})") == url


@pytest.mark.parametrize("text, expected", [
    ("www.example.com/path", "https://www.example.com/path"),
    ("example.com", "https://example.com"),
    ("t.me/somechannel", "https://t.me/somechannel"),
    ("blog.example.dev/post", "https://blog.example.dev/post"),
])
def test_a_link_typed_without_a_scheme_still_counts(text, expected):
    assert links.first_url(text) == expected


@pytest.mark.parametrize("text", [
    "read main.py and README.md, then run setup.sh",
    "node.js and vue.js are frameworks",
    "version 1.5.2 shipped",
    "mail me at bob@example.com",
    "remember to read about vector databases",
    "see e.g. the appendix",
])
def test_things_that_only_look_like_domains_are_left_alone(text):
    """A file name turned into a link is a note about nothing."""
    assert links.find_urls(text) == []


def test_an_explicit_scheme_overrides_the_file_name_guard():
    assert links.first_url("https://main.py/x") == "https://main.py/x"


@pytest.mark.parametrize("text, expected", [
    ("HTTPS://EXAMPLE.COM/Path?A=B", "https://example.com/Path?A=B"),
    ("https:/example.com/typo", "https://example.com/typo"),
    ("https:///example.com/typo", "https://example.com/typo"),
    ("https://example.com/a?x=1&amp;y=2", "https://example.com/a?x=1&y=2"),
    ("https://example.com/a​", "https://example.com/a"),
    ("https://example.com.", "https://example.com"),
])
def test_a_link_is_repaired_into_something_fetchable(text, expected):
    assert links.first_url(text) == expected


def test_the_host_is_lowercased_but_the_path_is_not():
    """Paths are case-sensitive; a "fixed" one is a different page."""
    assert links.first_url("https://Example.COM/CaseSensitive") == (
        "https://example.com/CaseSensitive"
    )


def test_other_schemes_are_not_treated_as_links():
    assert links.find_urls("mailto:bob@example.com") == []
    assert links.find_urls("ftp://files.example.com/x") == []


def test_every_link_in_a_message_is_found_in_order():
    text = "compare https://a.com/1 with https://b.org/2 and www.c.io/3"

    assert links.find_urls(text) == [
        "https://a.com/1", "https://b.org/2", "https://www.c.io/3",
    ]


def test_the_same_link_twice_is_one_link():
    text = "https://example.com/a is great, https://example.com/a really is"

    assert links.find_urls(text) == ["https://example.com/a"]


def test_the_users_own_words_survive_without_the_link():
    note = links.strip_urls(
        "great RAG explainer https://blog.example.dev/rag — worth stealing from"
    )

    assert note == "great RAG explainer — worth stealing from"


def test_a_message_that_is_only_a_link_has_no_note():
    assert links.strip_urls("  https://example.com/x  ") == ""


@pytest.mark.parametrize("text, expected", [
    ("[the docs](https://a.com/x) are good", "[the docs] are good"),
    ("compare https://a.com/x and https://b.com/y", "compare"),
    ("read this: https://a.com/x", "read this"),
    ("note (important) here https://a.com/x", "note (important) here"),
    ("worth stealing from https://a.com/x", "worth stealing from"),
])
def test_the_punctuation_the_link_was_holding_up_is_cleaned_away(text, expected):
    assert links.strip_urls(text) == expected


def test_a_link_describes_itself_from_its_address():
    assert links.describe("https://www.anthropic.com/news/claude-opus-5") == (
        "anthropic.com", "claude opus 5",
    )


def test_an_opaque_address_describes_only_its_host():
    """An id says nothing, so it is not worth putting in a title."""
    assert links.describe("https://example.com/p/3f9a2b7c1e0d") == ("example.com", "")
    assert links.describe("https://example.com/") == ("example.com", "")


def test_normalize_rejects_what_is_not_a_url():
    assert links.normalize("") is None
    assert links.normalize("just words") is None
    assert links.normalize("https://") is None
