from pathlib import Path

import pytest

from confluence_publisher.clone import (
    CloneError,
    CloneRequest,
    assert_clone_output_safe,
    parse_clone_request,
    parse_confluence_page_url,
)


def test_parse_confluence_page_url_extracts_page_id_space_and_url():
    parsed = parse_confluence_page_url(
        "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/"
        "pages/5164564861/Custom+UI+NEXT+Migration"
    )

    assert parsed.page_id == "5164564861"
    assert parsed.space == "~6393203da593cb822e995262"
    assert parsed.url == (
        "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/"
        "pages/5164564861/Custom+UI+NEXT+Migration"
    )


def test_parse_clone_request_accepts_page_id_with_space(tmp_path):
    request = parse_clone_request(
        url=None,
        page_id="5164564861",
        space="~6393203da593cb822e995262",
        output=tmp_path / "docs",
        max_depth=None,
        max_pages=1000,
        dry_run=False,
    )

    assert request == CloneRequest(
        root_page_id="5164564861",
        space="~6393203da593cb822e995262",
        root_url=None,
        output=tmp_path / "docs",
        max_depth=None,
        max_pages=1000,
        dry_run=False,
    )


def test_parse_clone_request_requires_url_or_page_id(tmp_path):
    with pytest.raises(CloneError, match="Either --url or --page-id is required"):
        parse_clone_request(
            url=None,
            page_id=None,
            space=None,
            output=tmp_path / "docs",
            max_depth=None,
            max_pages=1000,
            dry_run=False,
        )


def test_parse_clone_request_requires_space_with_page_id(tmp_path):
    with pytest.raises(CloneError, match="--space is required with --page-id"):
        parse_clone_request(
            url=None,
            page_id="5164564861",
            space=None,
            output=tmp_path / "docs",
            max_depth=None,
            max_pages=1000,
            dry_run=False,
        )


def test_assert_clone_output_safe_rejects_manifest(tmp_path):
    output = tmp_path / "docs"
    output.mkdir()
    (output / ".confluence-publish.yml").write_text("pages: {}\n", encoding="utf-8")

    with pytest.raises(CloneError, match="already contains .confluence-publish.yml"):
        assert_clone_output_safe(output)


def test_assert_clone_output_safe_rejects_existing_markdown(tmp_path):
    output = tmp_path / "docs"
    output.mkdir()
    (output / "index.md").write_text("# Existing\n", encoding="utf-8")

    with pytest.raises(CloneError, match="already contains Markdown files"):
        assert_clone_output_safe(output)


def test_assert_clone_output_safe_allows_missing_output_directory(tmp_path):
    assert_clone_output_safe(tmp_path / "new-docs")
