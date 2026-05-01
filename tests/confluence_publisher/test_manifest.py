from pathlib import Path

import pytest

from confluence_publisher.manifest import (
    ManifestError,
    ManifestPageSyncState,
    ProjectManifest,
    SyncSettings,
    load_manifest,
    write_manifest_page_ids,
    write_manifest_sync_state,
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


def test_load_manifest_allows_missing_page_id_with_create_mode_root_url(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root:
  url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/999999/Documentation"
pages:
  index.md:
    title: "Home"
""".strip(),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path, allow_missing_page_ids=True)

    assert manifest.root_page_id == "999999"
    assert manifest.root_url.endswith("/999999/Documentation")
    assert manifest.space == "~6393203da593cb822e995262"
    assert manifest.pages["index.md"].page_id is None


def test_load_manifest_requires_root_url_with_page_id(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root:
  url: "https://criteo.atlassian.net/wiki/spaces/DOC/overview"
pages:
  index.md:
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="root.url.*page ID"):
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


def test_write_manifest_page_ids_updates_created_pages(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root:
  page_id: "999999"
pages:
  index.md:
    title: "Home"
  guide/index.md:
    title: "Guide"
""".strip(),
        encoding="utf-8",
    )

    write_manifest_page_ids(
        manifest_path,
        {
            "index.md": "111111",
            "guide/index.md": "222222",
        },
    )

    manifest = load_manifest(manifest_path)
    assert manifest.pages["index.md"].page_id == "111111"
    assert manifest.pages["guide/index.md"].page_id == "222222"


def test_load_manifest_rejects_url_with_partial_page_id_segment(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
pages:
  index.md:
    page_id: "123"
    url: "https://criteo.atlassian.net/wiki/pages/1234/Home"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="url.*page_id"):
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


def test_load_manifest_rejects_duplicate_yaml_page_keys(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
pages:
  index.md:
    page_id: "111111"
  index.md:
    page_id: "222222"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match=r"[Dd]uplicate (mapping )?key"):
        load_manifest(manifest_path)


@pytest.mark.parametrize(
    "page_path",
    [
        "C:/docs/index.md",
        r"C:\docs\index.md",
        "C:docs/index.md",
        r"C:docs\index.md",
    ],
)
def test_load_manifest_rejects_windows_drive_page_path(tmp_path, page_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        f"""
pages:
  '{page_path}':
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="Invalid page path"):
        load_manifest(manifest_path)


def test_load_manifest_reads_sync_root_parent_and_page_hashes(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
sync:
  enabled: true
  recreate_missing_root: true
  recreate_stale_pages: false
root:
  parent_page_id: "2163738446"
  parent_url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/2163738446/Overview"
  page_id: "5155097071"
  url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/5155097071/White-label+NEXT+Deployment+-+Overview"
pages:
  00-overview.md:
    title: "White-label NEXT Deployment - Overview"
    page_id: "5155097071"
    url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/5155097071/White-label+NEXT+Deployment+-+Overview"
    content_hash: "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    last_synced_at: "2026-04-28T12:00:00Z"
""".strip(),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert manifest.sync == SyncSettings(
        enabled=True,
        recreate_missing_root=True,
        recreate_stale_pages=False,
    )
    assert manifest.root_parent_page_id == "2163738446"
    assert manifest.root_parent_url.endswith("/2163738446/Overview")
    assert manifest.root_page_id == "5155097071"
    page = manifest.pages["00-overview.md"]
    assert page.content_hash == "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert page.canonical_hash is None
    assert page.last_synced_at == "2026-04-28T12:00:00Z"


def test_load_manifest_reads_page_canonical_hash(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
pages:
  index.md:
    page_id: "111111"
    canonical_hash: "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
""".strip(),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert (
        manifest.pages["index.md"].canonical_hash
        == "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    )


def test_load_manifest_reads_root_source_path_and_page_parent(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root:
  source_path: "00-overview.md"
  parent_page_id: "5151786651"
  page_id: "5156570282"
pages:
  00-overview.md:
    title: "White-label NEXT Deployment - Overview"
    page_id: "5156570282"
  01-prerequisites.md:
    title: "Prerequisites"
    page_id: "5156602817"
    parent: "00-overview.md"
""".strip(),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert manifest.root_source_path == "00-overview.md"
    assert manifest.root_parent_page_id == "5151786651"
    assert manifest.root_page_id == "5156570282"
    assert manifest.pages["00-overview.md"].parent is None
    assert manifest.pages["01-prerequisites.md"].parent == "00-overview.md"


def test_load_manifest_rejects_root_source_path_missing_from_pages(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root:
  source_path: "00-overview.md"
pages:
  01-prerequisites.md:
    page_id: "5156602817"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="root.source_path.*00-overview.md.*pages"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_page_parent_missing_from_pages(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
pages:
  01-prerequisites.md:
    page_id: "5156602817"
    parent: "00-overview.md"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="parent.*00-overview.md.*pages"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_root_page_with_explicit_parent(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root:
  source_path: "00-overview.md"
pages:
  00-overview.md:
    page_id: "5156570282"
    parent: "01-prerequisites.md"
  01-prerequisites.md:
    page_id: "5156602817"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="root.source_path.*must not define parent"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_non_boolean_sync_setting(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
sync:
  enabled: "yes"
pages:
  index.md:
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="sync.enabled"):
        load_manifest(manifest_path)


def test_write_manifest_sync_state_updates_root_and_pages_preserving_titles(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root:
  parent_page_id: "2163738446"
  page_id: "old-root"
pages:
  00-overview.md:
    title: "Overview"
    page_id: "old-root"
  guide/index.md:
    title: "Guide"
""".strip(),
        encoding="utf-8",
    )

    write_manifest_sync_state(
        manifest_path,
        root_page_id="new-root",
        root_url="https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/new-root/Overview",
        pages={
            "00-overview.md": ManifestPageSyncState(
                page_id="new-root",
                url="https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/new-root/Overview",
                content_hash="sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                last_synced_at="2026-04-28T13:00:00Z",
            ),
            "guide/index.md": ManifestPageSyncState(
                page_id="new-guide",
                url="https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/new-guide/Guide",
                content_hash="sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
                canonical_hash="sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
                last_synced_at="2026-04-28T13:00:00Z",
            ),
        },
    )

    manifest = load_manifest(manifest_path)
    assert manifest.root_parent_page_id == "2163738446"
    assert manifest.root_page_id == "new-root"
    assert manifest.root_url.endswith("/new-root/Overview")
    assert manifest.pages["00-overview.md"].title == "Overview"
    assert manifest.pages["00-overview.md"].page_id == "new-root"
    assert manifest.pages["guide/index.md"].page_id == "new-guide"
    assert manifest.pages["guide/index.md"].content_hash.startswith("sha256:")
    assert (
        manifest.pages["guide/index.md"].canonical_hash
        == "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    )
    assert manifest.pages["guide/index.md"].last_synced_at == "2026-04-28T13:00:00Z"


def test_write_manifest_sync_state_updates_legacy_top_level_root_metadata(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root_page_id: "old-root"
root_url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/old-root/Overview"
pages:
  index.md:
    title: "Overview"
    page_id: "old-root"
""".strip(),
        encoding="utf-8",
    )

    write_manifest_sync_state(
        manifest_path,
        root_page_id="new-root",
        root_url="https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/new-root/Overview",
        pages={
            "index.md": ManifestPageSyncState(
                page_id="new-root",
                url="https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/new-root/Overview",
            ),
        },
    )

    manifest = load_manifest(manifest_path)
    assert manifest.root_page_id == "new-root"
    assert manifest.root_url.endswith("/new-root/Overview")
    assert manifest.pages["index.md"].page_id == "new-root"
