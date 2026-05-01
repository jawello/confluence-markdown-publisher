from pathlib import Path

import pytest

from confluence_publisher.bidirectional import compute_canonical_markdown_hash
from confluence_publisher.clone import (
    CloneContentPage,
    CloneError,
    CloneFilePlan,
    ClonePlannedPage,
    CloneRequest,
    assert_clone_output_safe,
    build_clone_file_plan,
    parse_clone_request,
    parse_confluence_page_url,
    write_clone_project,
)
from confluence_publisher.manifest import load_manifest
from confluence_publisher.models import ConfluenceTreeNode


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


def test_parse_clone_request_rejects_whitespace_page_id(tmp_path):
    with pytest.raises(CloneError, match="--page-id must be a non-empty string"):
        parse_clone_request(
            url=None,
            page_id=" \t\n",
            space="~6393203da593cb822e995262",
            output=tmp_path / "docs",
            max_depth=None,
            max_pages=1000,
            dry_run=False,
        )


def test_parse_clone_request_rejects_whitespace_space(tmp_path):
    with pytest.raises(CloneError, match="--space is required with --page-id"):
        parse_clone_request(
            url=None,
            page_id="5164564861",
            space=" \t\n",
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


def test_assert_clone_output_safe_rejects_existing_uppercase_markdown(tmp_path):
    output = tmp_path / "docs"
    output.mkdir()
    (output / "README.MD").write_text("# Existing\n", encoding="utf-8")

    with pytest.raises(CloneError, match="already contains Markdown files"):
        assert_clone_output_safe(output)


def test_assert_clone_output_safe_allows_missing_output_directory(tmp_path):
    assert_clone_output_safe(tmp_path / "new-docs")


def test_build_clone_file_plan_maps_nested_pages_to_indexes_and_leaves():
    tree = ConfluenceTreeNode(
        page_id="1",
        title="Root Docs",
        url="https://example.atlassian.net/wiki/spaces/ENG/pages/1/Root",
        children=(
            ConfluenceTreeNode(
                page_id="2",
                title="Guide",
                url="https://example.atlassian.net/wiki/spaces/ENG/pages/2/Guide",
                children=(
                    ConfluenceTreeNode(
                        page_id="3",
                        title="Install & Run",
                        url="https://example.atlassian.net/wiki/spaces/ENG/pages/3/Install",
                    ),
                ),
            ),
            ConfluenceTreeNode(
                page_id="4",
                title="Quick Reference",
                url="https://example.atlassian.net/wiki/spaces/ENG/pages/4/Quick",
            ),
        ),
    )

    plan = build_clone_file_plan(tree)

    assert [(page.page_id, page.source_path) for page in plan.pages] == [
        ("1", "index.md"),
        ("2", "guide/index.md"),
        ("3", "guide/install-run.md"),
        ("4", "quick-reference.md"),
    ]


def test_build_clone_file_plan_makes_duplicate_leaf_slugs_unique():
    tree = ConfluenceTreeNode(
        page_id="1",
        title="Root",
        children=(
            ConfluenceTreeNode(page_id="2", title="Overview"),
            ConfluenceTreeNode(page_id="3", title="Overview"),
        ),
    )

    plan = build_clone_file_plan(tree)

    assert [(page.page_id, page.source_path) for page in plan.pages] == [
        ("1", "index.md"),
        ("2", "overview.md"),
        ("3", "overview-2.md"),
    ]


def test_write_clone_project_creates_markdown_and_manifest(tmp_path):
    tree = ConfluenceTreeNode(
        page_id="1",
        title="Root Docs",
        url="https://example.atlassian.net/wiki/spaces/ENG/pages/1/Root",
        children=(ConfluenceTreeNode(page_id="2", title="Guide", url=None),),
    )
    file_plan = build_clone_file_plan(tree)
    content = {
        "1": CloneContentPage(markdown="# Root Docs\n\nWelcome.\n"),
        "2": CloneContentPage(markdown="# Guide\n\nRead this.\n"),
    }

    write_clone_project(
        output=tmp_path / "docs",
        space="ENG",
        root_url=tree.url,
        file_plan=file_plan,
        content=content,
    )

    assert (tmp_path / "docs" / "index.md").read_text(encoding="utf-8") == (
        "# Root Docs\n\nWelcome.\n"
    )
    assert (tmp_path / "docs" / "guide.md").read_text(encoding="utf-8") == (
        "# Guide\n\nRead this.\n"
    )
    manifest = load_manifest(tmp_path / "docs" / ".confluence-publish.yml")
    assert manifest.space == "ENG"
    assert manifest.sync.enabled is True
    assert manifest.root_source_path == "index.md"
    assert manifest.root_page_id == "1"
    assert manifest.root_url == tree.url
    assert manifest.pages["index.md"].canonical_hash == compute_canonical_markdown_hash(
        "# Root Docs\n\nWelcome.\n"
    )
    assert manifest.pages["guide.md"].canonical_hash == compute_canonical_markdown_hash(
        "# Guide\n\nRead this.\n"
    )


def test_write_clone_project_refuses_missing_content(tmp_path):
    tree = ConfluenceTreeNode(page_id="1", title="Root Docs")
    file_plan = build_clone_file_plan(tree)

    with pytest.raises(CloneError, match="Missing converted content for page 1"):
        write_clone_project(
            output=tmp_path / "docs",
            space="ENG",
            root_url=None,
            file_plan=file_plan,
            content={},
        )
    assert not (tmp_path / "docs").exists()


def test_write_clone_project_refuses_unsafe_source_path_without_writes(tmp_path):
    tree = ConfluenceTreeNode(page_id="1", title="Root Docs")
    file_plan = CloneFilePlan(
        root=tree,
        pages=(
            ClonePlannedPage(
                page_id="1",
                title="Root Docs",
                source_path="../escape.md",
                url=None,
                has_children=False,
            ),
        ),
    )

    with pytest.raises(CloneError, match=r"Invalid clone source path: \.\./escape\.md"):
        write_clone_project(
            output=tmp_path / "docs",
            space="ENG",
            root_url=None,
            file_plan=file_plan,
            content={"1": CloneContentPage(markdown="# Root Docs\n")},
        )
    assert not (tmp_path / "docs").exists()
    assert not (tmp_path / "escape.md").exists()


def test_write_clone_project_uses_root_url_for_root_page_manifest_entry(tmp_path):
    tree = ConfluenceTreeNode(page_id="1", title="Root Docs", url=None)
    root_url = "https://example.atlassian.net/wiki/spaces/ENG/pages/1/Root"

    write_clone_project(
        output=tmp_path / "docs",
        space="ENG",
        root_url=root_url,
        file_plan=build_clone_file_plan(tree),
        content={"1": CloneContentPage(markdown="# Root Docs\n")},
    )

    manifest = load_manifest(tmp_path / "docs" / ".confluence-publish.yml")
    assert manifest.root_url == root_url
    assert manifest.pages["index.md"].url == root_url


def test_write_clone_project_refuses_invalid_root_url_without_writes(tmp_path):
    tree = ConfluenceTreeNode(page_id="1", title="Root Docs", url=None)

    with pytest.raises(CloneError, match="Generated clone manifest is invalid"):
        write_clone_project(
            output=tmp_path / "docs",
            space="ENG",
            root_url="https://example.atlassian.net/wiki/spaces/ENG/pages/999/Root",
            file_plan=build_clone_file_plan(tree),
            content={"1": CloneContentPage(markdown="# Root Docs\n")},
        )
    assert not (tmp_path / "docs").exists()
    assert not (tmp_path / "docs" / "index.md").exists()
