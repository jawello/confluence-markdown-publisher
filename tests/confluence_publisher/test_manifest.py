from pathlib import Path

import pytest

from confluence_publisher.manifest import (
    ManifestError,
    ProjectManifest,
    load_manifest,
)


def test_load_manifest_reads_pages_defaults_and_url(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
include:
  - "**/*.md"
exclude:
  - "**/drafts/**"
defaults:
  version_comment: "Sync docs"
  output_format: "png"
  diagram_width: 1400
  allow_diagram_fallback: true
pages:
  guide/getting-started.md:
    page_id: "333333"
    title: "Getting Started"
    url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/333333/Getting+Started"
""".strip(),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert manifest == ProjectManifest(
        path=manifest_path,
        space="~6393203da593cb822e995262",
        include=("**/*.md",),
        exclude=("**/drafts/**",),
        version_comment="Sync docs",
        output_format="png",
        diagram_width=1400,
        allow_diagram_fallback=True,
        pages={
            "guide/getting-started.md": manifest.pages["guide/getting-started.md"],
        },
    )
    page = manifest.pages["guide/getting-started.md"]
    assert page.page_id == "333333"
    assert page.title == "Getting Started"
    assert page.url.endswith("/333333/Getting+Started")


def test_load_manifest_applies_defaults(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
pages:
  index.md:
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert manifest.include == ("**/*.md",)
    assert manifest.exclude == ()
    assert manifest.version_comment == "Sync documentation from Markdown"
    assert manifest.output_format == "svg"
    assert manifest.diagram_width == 1000
    assert manifest.allow_diagram_fallback is False
    assert manifest.pages["index.md"].page_id == "111111"


def test_load_manifest_rejects_missing_file(tmp_path):
    with pytest.raises(ManifestError, match="Manifest not found"):
        load_manifest(tmp_path / ".confluence-publish.yml")


def test_load_manifest_rejects_page_without_page_id(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
pages:
  index.md:
    title: "Home"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="index.md.*page_id"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_url_that_does_not_contain_page_id(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
pages:
  index.md:
    page_id: "111111"
    url: "https://criteo.atlassian.net/wiki/pages/222222/Home"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="url.*111111"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_duplicate_page_ids(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
pages:
  index.md:
    page_id: "111111"
  guide/index.md:
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="Duplicate page_id.*111111"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_invalid_output_format(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
defaults:
  output_format: "pdf"
pages:
  index.md:
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="output_format"):
        load_manifest(manifest_path)


@pytest.mark.parametrize("page_path", ["..", "../outside.md", "docs/..", "docs/../index.md"])
def test_load_manifest_rejects_parent_directory_page_paths(tmp_path, page_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        f"""
pages:
  {page_path}:
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="Invalid page path"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_non_string_output_format(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
defaults:
  output_format:
    - svg
pages:
  index.md:
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="output_format"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_boolean_diagram_width(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
defaults:
  diagram_width: true
pages:
  index.md:
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="diagram_width"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_absolute_page_path(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
pages:
  /index.md:
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="Invalid page path"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_duplicate_normalized_page_paths(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
pages:
  docs/index.md:
    page_id: "111111"
  docs//index.md:
    page_id: "222222"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="Duplicate page path.*docs/index.md"):
        load_manifest(manifest_path)
