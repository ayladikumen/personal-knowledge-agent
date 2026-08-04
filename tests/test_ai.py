from ai import detect_mime_type, parse_analysis


def test_parses_title_body_and_tags():
    result = parse_analysis(
        "# LangGraph\n\n## Summary\nAn agent framework.\n\nTAGS: python, agents, llm",
        default_title="Saved Item",
    )

    assert result["title"] == "LangGraph"
    assert result["tags"] == ["python", "agents", "llm"]
    assert "TAGS:" not in result["markdown_content"]
    assert "An agent framework." in result["markdown_content"]


def test_falls_back_to_default_title_when_no_heading():
    result = parse_analysis("Just a paragraph.", default_title="Saved Item")

    assert result["title"] == "Saved Item"
    assert result["tags"] == []
    assert result["markdown_content"] == "Just a paragraph."


def test_ignores_earlier_tags_mention_in_body():
    # A body that discusses tags shouldn't hijack the real trailing TAGS: line.
    result = parse_analysis(
        "# Notes\n\nTAGS: are useful for organizing.\n\nTAGS: obsidian, pkm",
        default_title="Saved Item",
    )

    assert result["tags"] == ["obsidian", "pkm"]
    assert result["title"] == "Notes"


def test_handles_empty_response():
    result = parse_analysis("", default_title="Saved Image")

    assert result["title"] == "Saved Image"
    assert result["markdown_content"] == ""
    assert result["tags"] == []


def test_blank_heading_does_not_produce_empty_title():
    result = parse_analysis("# \n\nSome body", default_title="Saved Item")

    assert result["title"] == "Saved Item"


def test_detects_image_types_from_magic_bytes():
    assert detect_mime_type(b"\x89PNG\r\n\x1a\n" + b"rest") == "image/png"
    assert detect_mime_type(b"GIF89a" + b"rest") == "image/gif"
    assert detect_mime_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert detect_mime_type(b"\xff\xd8\xff\xe0jpeg") == "image/jpeg"
    assert detect_mime_type(b"") == "image/jpeg"
