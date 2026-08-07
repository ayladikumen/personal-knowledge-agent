"""
Telling a temporary outage apart from a permanent failure.

Gemini answers 503 UNAVAILABLE whenever the model is briefly overloaded, and
429 when a rate limit is hit; both clear on their own within seconds. That
distinction matters more here than in most projects: Telegram deletes a message
from its queue as soon as we confirm its update_id, so treating a 503 as a
permanent failure silently destroys the note the user was trying to save.

So transient failures are retried a few times, and anything still failing is
reported *without* confirming the message, leaving it queued for the next sync.
"""

import time

import requests

# Codes that mean "ask again shortly" rather than "this will never work".
# A 401 (bad key) or 400 (bad request) is excluded on purpose — retrying those
# only delays telling the user what to fix.
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

# Some transports report an outage in prose without a usable status code.
_RETRYABLE_TEXT = (
    "unavailable",
    "overloaded",
    "deadline exceeded",
    "resource_exhausted",
    "rate limit",
    "too many requests",
    "timed out",
    "timeout",
    "temporarily",
    "try again",
)

MAX_ATTEMPTS = 3
BASE_DELAY = 1.0


def is_transient(exc: BaseException) -> bool:
    """True if `exc` looks like an outage that will clear on its own."""
    # Never reached the server at all: DNS, connection reset, socket timeout.
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True

    if isinstance(exc, requests.RequestException):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        # No response means the request died in transit, which is retryable.
        return status is None or status in RETRYABLE_STATUS

    # google-genai's APIError carries `.code` (503) and `.status` (UNAVAILABLE).
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code in RETRYABLE_STATUS

    haystack = f"{getattr(exc, 'status', '')} {exc}".lower()
    return any(phrase in haystack for phrase in _RETRYABLE_TEXT)


def describe(exc: BaseException) -> str:
    """
    Name the service that actually failed.

    Both Telegram and Gemini sit behind the same sync call, so a bare error
    string leaves the user (and their AI assistant) guessing which one broke —
    and a Gemini 503 misread as a Telegram outage sends them off waiting for the
    wrong thing to recover.
    """
    code, status = getattr(exc, "code", None), getattr(exc, "status", None)
    if isinstance(code, int) and status:
        return f"Gemini is unavailable right now ({code} {status})"
    return str(exc).strip() or exc.__class__.__name__


def with_retries(
    operation,
    attempts: int = MAX_ATTEMPTS,
    base_delay: float = BASE_DELAY,
    sleep=None,
):
    """
    Run `operation`, retrying transient failures with exponential backoff.

    Permanent failures propagate on the first attempt, and so does a transient
    one that outlives every retry — the caller decides what to do with it.
    """
    # Resolved per call rather than as a default argument, which would bind
    # time.sleep at import and quietly ignore any later substitution.
    pause = sleep or time.sleep

    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:
            if attempt == attempts - 1 or not is_transient(exc):
                raise
            pause(base_delay * (2 ** attempt))
