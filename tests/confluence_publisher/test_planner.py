import pytest

from confluence_publisher.manifest import ManifestPage, ProjectManifest, SyncSettings
from confluence_publisher.planner import PlannerError, build_publish_plan


def manifest(tmp_path, pages):
    return ProjectManifest(
        path=tmp_path / ".confluence-publish.yml",
        space="~6393203da593cb822e995262",
        include=("**/*.md",),
        exclude=(),
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        pages=pages,
    )


def page(path, page_id, title=None, url=None, content_hash=None, last_synced_at=None):
    return ManifestPage(
        path=path,
        page_id=page_id,
        title=title,
        url=url,
        content_hash=content_hash,
        last_synced_at=last_synced_at,
    )


def test_build_publish_plan_resolves_titles_and_parents(tmp_path):
    (tmp_path / "guide").mkdir()
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "guide" / "index.md").write_text("# Guide\n", encoding="utf-8")
    (tmp_path / "guide" / "getting-started.md").write_text("# Getting Started\n", encoding="utf-8")
    project_manifest = manifest(
        tmp_path,
        {
            "index.md": page("index.md", "111111", title="Root"),
            "guide/index.md": page("guide/index.md", "222222"),
            "guide/getting-started.md": page(
                "guide/getting-started.md",
                "333333",
                url="https://criteo.atlassian.net/wiki/pages/333333/Getting+Started",
            ),
        },
    )

    plan = build_publish_plan(
        project_root=tmp_path,
        manifest=project_manifest,
        discovered_paths=("guide/getting-started.md", "guide/index.md", "index.md"),
        excluded_count=2,
        version_comment="CLI comment",
        output_format="png",
        diagram_width=1200,
        allow_diagram_fallback=True,
    )

    assert plan.project_root == tmp_path
    assert plan.manifest_path == project_manifest.path
    assert plan.excluded_count == 2
    assert plan.version_comment == "CLI comment"
    assert plan.output_format == "png"
    assert plan.diagram_width == 1200
    assert plan.allow_diagram_fallback is True
    assert [item.source_path for item in plan.items] == (
        ["index.md", "guide/index.md", "guide/getting-started.md"]
    )
    assert plan.items[0].title == "Root"
    assert plan.items[1].title == "Guide"
    assert plan.items[2].title == "Getting Started"
    assert plan.items[2].parent_source_path == "guide/index.md"
    assert plan.items[2].url.endswith("/333333/Getting+Started")


def test_build_publish_plan_errors_for_discovered_file_missing_from_manifest(tmp_path):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "guide.md").write_text("# Guide\n", encoding="utf-8")
    project_manifest = manifest(tmp_path, {"index.md": page("index.md", "111111")})

    with pytest.raises(PlannerError, match="missing from manifest pages.*guide.md"):
        build_publish_plan(
            project_root=tmp_path,
            manifest=project_manifest,
            discovered_paths=("index.md", "guide.md"),
            excluded_count=0,
            version_comment=None,
            output_format=None,
            diagram_width=None,
            allow_diagram_fallback=None,
        )


def test_build_publish_plan_allows_missing_page_ids_with_create_mode(tmp_path):
    (tmp_path / "guide").mkdir()
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "guide" / "index.md").write_text("# Guide\n", encoding="utf-8")
    project_manifest = manifest(
        tmp_path,
        {
            "index.md": page("index.md", None),
            "guide/index.md": page("guide/index.md", None),
        },
    )
    project_manifest = ProjectManifest(
        **{
            **project_manifest.__dict__,
            "root_page_id": "999999",
        }
    )

    plan = build_publish_plan(
        project_root=tmp_path,
        manifest=project_manifest,
        discovered_paths=("guide/index.md", "index.md"),
        excluded_count=0,
        version_comment=None,
        output_format=None,
        diagram_width=None,
        allow_diagram_fallback=None,
        create_missing_pages=True,
    )

    assert plan.create_missing_pages is True
    assert plan.root_page_id == "999999"
    assert [item.page_id for item in plan.items] == [None, None]
    assert plan.items[1].parent_source_path == "index.md"


def test_build_publish_plan_carries_sync_settings_and_root_parent(tmp_path):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    project_manifest = ProjectManifest(
        path=tmp_path / ".confluence-publish.yml",
        space="~6393203da593cb822e995262",
        include=("**/*.md",),
        exclude=(),
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        pages={
            "index.md": page(
                "index.md",
                "111111",
                content_hash="sha256:abc123",
                last_synced_at="2026-04-28T07:00:00Z",
            )
        },
        root_page_id="111111",
        root_url="https://criteo.atlassian.net/wiki/pages/111111/Home",
        root_parent_page_id="2163738446",
        root_parent_url="https://criteo.atlassian.net/wiki/pages/2163738446/Docs",
        sync=SyncSettings(enabled=True, recreate_missing_root=True),
    )

    plan = build_publish_plan(
        project_root=tmp_path,
        manifest=project_manifest,
        discovered_paths=("index.md",),
        excluded_count=0,
        version_comment=None,
        output_format=None,
        diagram_width=None,
        allow_diagram_fallback=None,
        sync_enabled=True,
        recreate_missing_root=True,
        recreate_stale_pages=False,
    )

    assert plan.sync_enabled is True
    assert plan.recreate_missing_root is True
    assert plan.recreate_stale_pages is False
    assert plan.root_url == "https://criteo.atlassian.net/wiki/pages/111111/Home"
    assert plan.root_parent_page_id == "2163738446"
    assert plan.root_parent_url == "https://criteo.atlassian.net/wiki/pages/2163738446/Docs"
    assert plan.items[0].content_hash == "sha256:abc123"
    assert plan.items[0].last_synced_at == "2026-04-28T07:00:00Z"


def test_build_publish_plan_allows_missing_page_ids_with_sync_mode(tmp_path):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    project_manifest = ProjectManifest(
        path=tmp_path / ".confluence-publish.yml",
        space="~6393203da593cb822e995262",
        include=("**/*.md",),
        exclude=(),
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        pages={"index.md": page("index.md", None)},
        root_parent_page_id="2163738446",
        sync=SyncSettings(enabled=True),
    )

    plan = build_publish_plan(
        project_root=tmp_path,
        manifest=project_manifest,
        discovered_paths=("index.md",),
        excluded_count=0,
        version_comment=None,
        output_format=None,
        diagram_width=None,
        allow_diagram_fallback=None,
        sync_enabled=True,
        recreate_missing_root=False,
        recreate_stale_pages=False,
    )

    assert plan.items[0].page_id is None


def test_build_publish_plan_rejects_create_mode_without_root_page(tmp_path):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    project_manifest = manifest(tmp_path, {"index.md": page("index.md", None)})

    with pytest.raises(PlannerError, match="root page"):
        build_publish_plan(
            project_root=tmp_path,
            manifest=project_manifest,
            discovered_paths=("index.md",),
            excluded_count=0,
            version_comment=None,
            output_format=None,
            diagram_width=None,
            allow_diagram_fallback=None,
            create_missing_pages=True,
        )


def test_build_publish_plan_rejects_create_mode_without_space(tmp_path):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    project_manifest = ProjectManifest(
        path=tmp_path / ".confluence-publish.yml",
        space=None,
        include=("**/*.md",),
        exclude=(),
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        pages={"index.md": page("index.md", None)},
        root_page_id="999999",
    )

    with pytest.raises(PlannerError, match="Confluence space"):
        build_publish_plan(
            project_root=tmp_path,
            manifest=project_manifest,
            discovered_paths=("index.md",),
            excluded_count=0,
            version_comment=None,
            output_format=None,
            diagram_width=None,
            allow_diagram_fallback=None,
            create_missing_pages=True,
        )


def test_build_publish_plan_errors_for_manifest_file_missing_on_disk(tmp_path):
    project_manifest = manifest(tmp_path, {"index.md": page("index.md", "111111")})

    with pytest.raises(PlannerError, match="listed in manifest but missing on disk.*index.md"):
        build_publish_plan(
            project_root=tmp_path,
            manifest=project_manifest,
            discovered_paths=(),
            excluded_count=0,
            version_comment=None,
            output_format=None,
            diagram_width=None,
            allow_diagram_fallback=None,
        )


def test_build_publish_plan_errors_for_nested_page_without_parent_index(tmp_path):
    (tmp_path / "guide").mkdir()
    (tmp_path / "guide" / "getting-started.md").write_text("# Getting Started\n", encoding="utf-8")
    project_manifest = manifest(
        tmp_path,
        {"guide/getting-started.md": page("guide/getting-started.md", "333333")},
    )

    with pytest.raises(PlannerError, match="parent index.md.*guide/getting-started.md"):
        build_publish_plan(
            project_root=tmp_path,
            manifest=project_manifest,
            discovered_paths=("guide/getting-started.md",),
            excluded_count=0,
            version_comment=None,
            output_format=None,
            diagram_width=None,
            allow_diagram_fallback=None,
        )


def test_build_publish_plan_falls_back_to_file_stem_for_title(tmp_path):
    (tmp_path / "api-reference.md").write_text("No h1 here\n", encoding="utf-8")
    project_manifest = manifest(
        tmp_path,
        {"api-reference.md": page("api-reference.md", "444444")},
    )

    plan = build_publish_plan(
        project_root=tmp_path,
        manifest=project_manifest,
        discovered_paths=("api-reference.md",),
        excluded_count=0,
        version_comment=None,
        output_format=None,
        diagram_width=None,
        allow_diagram_fallback=None,
    )

    assert plan.items[0].title == "api-reference"
