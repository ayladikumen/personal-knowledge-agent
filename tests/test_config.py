import os

import config


def test_placeholder_values_count_as_unset():
    assert not config.is_configured("your_gemini_api_key_here")
    assert not config.is_configured("your_telegram_bot_token_here")
    assert not config.is_configured("")
    assert config.is_configured("AIzaSyRealLookingKey")


def test_setup_hint_names_the_missing_keys():
    hint = config.setup_hint(["GEMINI_API_KEY"])

    assert "GEMINI_API_KEY" in hint
    assert ".env" in hint


def test_relative_paths_resolve_against_project_root(monkeypatch):
    monkeypatch.setenv("SOME_PATH", "./notes")

    assert config._path_from_env("SOME_PATH", "unused") == os.path.join(
        config.PROJECT_ROOT, "notes"
    )


def test_resolved_paths_are_normalised(monkeypatch):
    """The default './vault' should read as a plain path in user-facing messages."""
    monkeypatch.setenv("SOME_PATH", "./vault")

    assert "." not in os.path.basename(config._path_from_env("SOME_PATH", "unused"))
    assert f"{os.sep}." not in config._path_from_env("SOME_PATH", "unused")


def test_surrounding_whitespace_in_a_path_is_ignored(monkeypatch):
    monkeypatch.setenv("SOME_PATH", "  ./notes  ")

    assert config._path_from_env("SOME_PATH", "unused") == os.path.join(
        config.PROJECT_ROOT, "notes"
    )


def test_absolute_paths_are_left_alone(monkeypatch):
    monkeypatch.setenv("SOME_PATH", os.path.join(os.sep, "srv", "vault"))

    assert config._path_from_env("SOME_PATH", "unused") == os.path.join(
        os.sep, "srv", "vault"
    )


def test_unset_path_falls_back_to_project_default(monkeypatch):
    monkeypatch.delenv("SOME_PATH", raising=False)

    assert config._path_from_env("SOME_PATH", "vault") == os.path.join(
        config.PROJECT_ROOT, "vault"
    )
