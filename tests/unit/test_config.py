"""Unit tests for runtime settings parsing."""

from __future__ import annotations

from eflux.config import Settings


def test_llm_comment_placeholders_parse_as_blank():
    settings = Settings(
        llm_base_url="# e.g. https://api.example/v1",
        llm_model="# e.g. model-name",
    )

    assert settings.llm_base_url == ""
    assert settings.llm_model == ""
