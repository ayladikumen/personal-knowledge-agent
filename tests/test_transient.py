"""Tests for telling a passing outage apart from a permanent failure."""

import pytest
import requests
from google.genai import errors as genai_errors

import transient


def gemini_error(code, status, message="boom"):
    """Build the error google-genai raises, with its real payload shape."""
    return genai_errors.ServerError(
        code, {"error": {"status": status, "message": message}}
    )


def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status} error", response=response)


@pytest.mark.parametrize("exc", [
    gemini_error(503, "UNAVAILABLE", "The model is overloaded."),
    gemini_error(429, "RESOURCE_EXHAUSTED"),
    gemini_error(500, "INTERNAL"),
    http_error(503),
    http_error(429),
    requests.ConnectionError("connection reset"),
    requests.Timeout("read timed out"),
    TimeoutError("socket timeout"),
    ConnectionError("dns failure"),
])
def test_outages_are_transient(exc):
    assert transient.is_transient(exc)


@pytest.mark.parametrize("exc", [
    genai_errors.ClientError(401, {"error": {"status": "UNAUTHENTICATED"}}),
    genai_errors.ClientError(400, {"error": {"status": "INVALID_ARGUMENT"}}),
    http_error(404),
    ValueError("malformed response"),
    KeyError("title"),
    PermissionError("vault is read-only"),
])
def test_permanent_failures_are_not_transient(exc):
    assert not transient.is_transient(exc)


def test_a_gemini_outage_is_described_as_gemini():
    """The user must not be told to wait on Telegram when Gemini is the one down."""
    message = transient.describe(gemini_error(503, "UNAVAILABLE"))

    assert "Gemini" in message
    assert "503 UNAVAILABLE" in message


def test_describe_falls_back_to_the_error_text():
    assert transient.describe(RuntimeError("network down")) == "network down"


def test_describe_never_returns_an_empty_string():
    assert transient.describe(RuntimeError()) == "RuntimeError"


# ── with_retries ────────────────────────────────────────────────────────────


def test_transient_failure_is_retried_until_it_succeeds():
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise gemini_error(503, "UNAVAILABLE")
        return "saved"

    assert transient.with_retries(flaky, sleep=lambda _: None) == "saved"
    assert len(attempts) == 3


def test_permanent_failure_is_not_retried():
    attempts = []

    def bad_key():
        attempts.append(1)
        raise genai_errors.ClientError(401, {"error": {"status": "UNAUTHENTICATED"}})

    with pytest.raises(genai_errors.ClientError):
        transient.with_retries(bad_key, sleep=lambda _: None)

    assert len(attempts) == 1


def test_a_persistent_outage_gives_up_and_raises():
    attempts = []

    def always_down():
        attempts.append(1)
        raise gemini_error(503, "UNAVAILABLE")

    with pytest.raises(genai_errors.ServerError):
        transient.with_retries(always_down, attempts=3, sleep=lambda _: None)

    assert len(attempts) == 3


def test_backoff_grows_between_attempts():
    delays = []

    def always_down():
        raise gemini_error(503, "UNAVAILABLE")

    with pytest.raises(genai_errors.ServerError):
        transient.with_retries(
            always_down, attempts=4, base_delay=1.0, sleep=delays.append
        )

    # One sleep fewer than attempts — no point waiting after the last failure.
    assert delays == [1.0, 2.0, 4.0]
