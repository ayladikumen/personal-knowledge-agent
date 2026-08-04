from typing import Any, Dict

from google import genai
from google.genai import types

import config

# How much source text to hand the model. Long READMEs and article dumps get
# truncated here so a single huge page can't blow up the request.
MAX_CONTENT_CHARS = 25000

_OUTPUT_FORMAT = """
Please provide your analysis in the following format (ensure it's clean Markdown):

# [Title of the Content]

## Summary
[A brief 2-3 sentence summary of what this is]

## Why this is useful
[List 2-3 specific project ideas or situations where the user should come back to this resource]

{extra_section}

At the very end of your response, on a new line, provide exactly 3 to 5
comma-separated tags relevant to this content, prefixed with TAGS:. For example:
TAGS: python, web-scraping, tool, inspiration
"""

_TEXT_SECTION = """## Key Takeaways
- [Point 1]
- [Point 2]"""

_IMAGE_SECTION = """## Image Contents
[Describe the image and any text found in it]"""


def detect_mime_type(image_data: bytes) -> str:
    """
    Guess an image's MIME type from its magic bytes.

    Telegram re-encodes most photos as JPEG, but stickers and screenshots can
    arrive as PNG or WebP, and Gemini rejects a mismatched mime_type.
    """
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_data.startswith(b"GIF87a") or image_data.startswith(b"GIF89a"):
        return "image/gif"
    if image_data.startswith(b"RIFF") and image_data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def parse_analysis(result_text: str, default_title: str) -> Dict[str, Any]:
    """
    Split a model response into title, markdown body and tags.

    The model is asked to end with a `TAGS: a, b, c` line and to open with a
    `# Title` heading; both are optional here so a malformed response still
    produces a usable note.
    """
    clean_text = (result_text or "").strip()
    tags: list[str] = []

    if "TAGS:" in clean_text:
        body, _, tags_part = clean_text.rpartition("TAGS:")
        clean_text = body.strip()
        tags = [t.strip() for t in tags_part.split(",") if t.strip()]

    title = default_title
    for line in clean_text.split("\n"):
        if line.startswith("# "):
            title = line[2:].strip() or default_title
            break

    return {"title": title, "markdown_content": clean_text, "tags": tags}


class AIEngine:
    def __init__(self, api_key: str = None, model_name: str = None):
        self.api_key = config.GEMINI_KEY if api_key is None else api_key
        self.model_name = model_name or config.GEMINI_MODEL
        self._client = None

    @property
    def client(self):
        """Build the client lazily so an unconfigured key fails with a clear message."""
        if self._client is None:
            if not config.is_configured(self.api_key):
                raise RuntimeError(config.setup_hint(["GEMINI_API_KEY"]))
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _generate(self, contents) -> str:
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=contents,
        )
        text = getattr(response, "text", "") or ""
        if not text.strip():
            raise RuntimeError(
                "Gemini returned an empty response (the content may have been "
                "blocked by a safety filter)."
            )
        return text

    def analyze_content(self, text_content: str, source_url: str = None) -> Dict[str, Any]:
        """Summarize extracted text and derive a title and tags for it."""
        prompt = (
            "You are a personal knowledge assistant. Analyze the following content.\n"
            "Your goal is to extract the core value of this content so the user can "
            "find it useful later.\n\n"
            f"Content:\n{(text_content or '')[:MAX_CONTENT_CHARS]}\n"
        )
        if source_url:
            prompt += f"\nSource URL: {source_url}\n"
        prompt += _OUTPUT_FORMAT.format(extra_section=_TEXT_SECTION)

        return parse_analysis(self._generate(prompt), default_title="Saved Item")

    def analyze_image(self, image_data: bytes) -> Dict[str, Any]:
        """Analyze an image with Gemini Vision."""
        prompt = (
            "You are a personal knowledge assistant. Analyze this image.\n"
            "Extract any text, describe what it is, and extract the core value so "
            "the user can find it useful later.\n"
        ) + _OUTPUT_FORMAT.format(extra_section=_IMAGE_SECTION)

        image_part = types.Part.from_bytes(
            data=image_data, mime_type=detect_mime_type(image_data)
        )

        return parse_analysis(
            self._generate([prompt, image_part]), default_title="Saved Image"
        )
