from pathlib import Path

import pytest

from confluence_publisher import page
from confluence_publisher.mcp_atlassian import McpToolError
from confluence_publisher.models import PageInfo
from confluence_publisher.models import RenderedDiagram
from confluence_publisher.planner import PlannedPage, PublishPlan
from confluence_publisher.bidirectional import compute_canonical_markdown_hash
from confluence_publisher.sync import (
    CONTENT_HASH_VERSION,
    PreparedSyncPage,
    compute_content_hash,
    prepare_sync_pages,
)


class FakeSyncClient:
    def __init__(self, *, page_errors=None, search_results=None, update_errors=None):
        self.calls = []
        self.created = 0
        self.page_errors = page_errors or {}
        self.search_results = search_results or {}
        self.update_errors = update_errors or {}

    async def get_page(self, page_id: str) -> PageInfo:
        self.calls.append(("get_page", page_id))
        if page_id in self.page_errors:
            raise self.page_errors[page_id]
        return PageInfo(page_id=page_id, title=f"Page {page_id}")

    async def search_pages_by_title(self, *, space_key: str, title: str):
        self.calls.append(("search_pages_by_title", space_key, title))
        return self.search_results.get(title, [])

    async def create_page(self, *, space_key: str, title: str, parent_id: str, storage: str):
        self.created += 1
        page_id = str(800000 + self.created)
        self.calls.append(("create_page", space_key, title, parent_id, page_id))
        return PageInfo(page_id=page_id, title=title)

    async def upload_attachment(self, page_id: str, file_path: Path) -> None:
        self.calls.append(("upload_attachment", page_id, file_path.name))

    async def update_page_storage(
        self,
        page_id: str,
        title: str,
        storage: str,
        version_comment: str,
    ) -> None:
        self.calls.append(("update_page_storage", page_id, title, version_comment))
        if page_id in self.update_errors:
            raise self.update_errors[page_id]


def sync_plan(tmp_path, *, root_page_id="111111", root_parent_page_id="2163738446", items=None):
    if items is None:
        source = tmp_path / "index.md"
        source.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
        items = (
            PlannedPage(
                source_path="index.md",
                absolute_path=source,
                page_id=root_page_id,
                title="Home",
                url=None,
                parent_source_path=None,
            ),
        )
    return PublishPlan(
        project_root=tmp_path,
        manifest_path=tmp_path / ".confluence-publish.yml",
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=items,
        space="~6393203da593cb822e995262",
        root_page_id=root_page_id,
        root_parent_page_id=root_parent_page_id,
        sync_enabled=True,
    )


def test_compute_content_hash_is_deterministic_and_setting_sensitive():
    first = compute_content_hash(
        title="Home",
        storage="<h1>Home</h1>",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
    )
    second = compute_content_hash(
        title="Home",
        storage="<h1>Home</h1>",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
    )
    changed_width = compute_content_hash(
        title="Home",
        storage="<h1>Home</h1>",
        output_format="svg",
        diagram_width=1200,
        allow_diagram_fallback=False,
    )

    assert CONTENT_HASH_VERSION.startswith("storage:")
    assert first == second
    assert first.startswith("sha256:")
    assert first != changed_width


def test_compute_content_hash_supports_positional_calls():
    positional = compute_content_hash("Home", "<h1>Home</h1>", "svg", 1000, False)
    keyword = compute_content_hash(
        title="Home",
        storage="<h1>Home</h1>",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
    )

    assert positional == keyword


def test_prepare_sync_pages_returns_rendered_storage_and_hash(tmp_path):
    source = tmp_path / "index.md"
    source.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=tmp_path / ".confluence-publish.yml",
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(
            PlannedPage(
                source_path="index.md",
                absolute_path=source,
                page_id="111111",
                title="Home",
                url=None,
                parent_source_path=None,
            ),
        ),
        sync_enabled=True,
    )

    prepared = prepare_sync_pages(plan, mmdc_bin="mmdc")

    assert set(prepared) == {"index.md"}
    prepared_page = prepared["index.md"]
    assert isinstance(prepared_page, PreparedSyncPage)
    assert prepared_page.item == plan.items[0]
    assert prepared_page.rendered_diagrams == []
    assert "<h1>Home</h1>" in prepared_page.storage
    assert prepared_page.content_hash.startswith("sha256:")


def test_prepare_sync_pages_keeps_rendered_diagram_files_after_return(tmp_path, monkeypatch):
    class FakeRenderer:
        def __init__(self, _mmdc_bin: str) -> None:
            pass

        def render(self, block, temp_dir):
            mmd_path = temp_dir / Path(block.attachment_name).with_suffix(".mmd").name
            output_path = temp_dir / block.attachment_name
            mmd_path.write_text(block.source, encoding="utf-8")
            output_path.write_text("<svg></svg>", encoding="utf-8")
            return RenderedDiagram(block=block, mmd_path=mmd_path, output_path=output_path)

    source = tmp_path / "index.md"
    source.write_text("# Home\n\n```mermaid\ngraph LR\nA --> B\n```\n", encoding="utf-8")
    plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=tmp_path / ".confluence-publish.yml",
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(
            PlannedPage(
                source_path="index.md",
                absolute_path=source,
                page_id="111111",
                title="Home",
                url=None,
                parent_source_path=None,
            ),
        ),
        sync_enabled=True,
    )

    monkeypatch.setattr(page, "MermaidRenderer", FakeRenderer)

    prepared = prepare_sync_pages(plan, mmdc_bin="mmdc")

    rendered_diagrams = prepared["index.md"].rendered_diagrams
    assert isinstance(rendered_diagrams, list)
    assert len(rendered_diagrams) == 1
    diagram = rendered_diagrams[0]
    assert diagram.mmd_path.exists()
    assert diagram.output_path.exists()
    assert diagram.output_path.name == diagram.block.attachment_name
    assert diagram.mmd_path.name == Path(diagram.block.attachment_name).with_suffix(".mmd").name


@pytest.mark.asyncio
async def test_sync_project_missing_root_without_policy_fails_before_create(tmp_path):
    from confluence_publisher.sync import sync_project

    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root:
  parent_page_id: "2163738446"
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
""".strip(),
        encoding="utf-8",
    )
    plan = sync_plan(tmp_path)
    client = FakeSyncClient(
        page_errors={"111111": McpToolError("page 111111 not found")}
    )

    with pytest.raises(RuntimeError, match="root page.*111111.*--recreate-missing-root"):
        await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert client.calls == [("get_page", "111111")]


@pytest.mark.asyncio
async def test_sync_project_missing_root_with_policy_requires_parent(tmp_path):
    from confluence_publisher.sync import sync_project

    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root:
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
""".strip(),
        encoding="utf-8",
    )
    plan = sync_plan(tmp_path, root_parent_page_id=None)
    plan = PublishPlan(**{**plan.__dict__, "recreate_missing_root": True})
    client = FakeSyncClient(
        page_errors={"111111": McpToolError("page 111111 not found")}
    )

    with pytest.raises(RuntimeError, match="root.parent_page_id"):
        await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert client.calls == [("get_page", "111111")]


@pytest.mark.asyncio
async def test_sync_project_stale_page_without_policy_fails_before_create(tmp_path):
    from confluence_publisher.sync import sync_project

    child_dir = tmp_path / "guide"
    child_dir.mkdir()
    root = tmp_path / "index.md"
    child = child_dir / "index.md"
    root.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    child.write_text("# Guide\n\nPlain text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root:
  parent_page_id: "2163738446"
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
  guide/index.md:
    page_id: "222222"
    title: "Guide"
""".strip(),
        encoding="utf-8",
    )
    plan = sync_plan(
        tmp_path,
        items=(
            PlannedPage("index.md", root, "111111", "Home", None, None),
            PlannedPage("guide/index.md", child, "222222", "Guide", None, "index.md"),
        ),
    )
    client = FakeSyncClient(
        page_errors={"222222": McpToolError("page 222222 not found")}
    )

    with pytest.raises(RuntimeError, match="guide/index.md.*222222.*--recreate-stale-pages"):
        await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert client.calls == [("get_page", "111111"), ("get_page", "222222")]


@pytest.mark.asyncio
async def test_sync_project_recreates_stale_child_page(tmp_path):
    from confluence_publisher.manifest import load_manifest
    from confluence_publisher.sync import sync_project

    child_dir = tmp_path / "guide"
    child_dir.mkdir()
    root = tmp_path / "index.md"
    child = child_dir / "index.md"
    root.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    child.write_text("# Guide\n\nPlain text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
root:
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
  guide/index.md:
    page_id: "222222"
    title: "Guide"
""".strip(),
        encoding="utf-8",
    )
    plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=manifest_path,
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(
            PlannedPage("index.md", root, "111111", "Home", None, None),
            PlannedPage("guide/index.md", child, "222222", "Guide", None, "index.md"),
        ),
        space="~6393203da593cb822e995262",
        root_page_id="111111",
        sync_enabled=True,
        recreate_stale_pages=True,
    )
    client = FakeSyncClient(
        page_errors={"222222": McpToolError("page 222222 not found")}
    )

    result = await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert result == 0
    assert ("search_pages_by_title", "~6393203da593cb822e995262", "Guide") in client.calls
    assert ("create_page", "~6393203da593cb822e995262", "Guide", "111111", "800001") in client.calls
    assert ("update_page_storage", "800001", "Guide", "Sync docs") in client.calls
    manifest = load_manifest(manifest_path)
    assert manifest.pages["guide/index.md"].page_id == "800001"
    assert manifest.pages["guide/index.md"].content_hash.startswith("sha256:")


@pytest.mark.asyncio
async def test_sync_project_recreates_stale_ancestor_with_readable_descendant(tmp_path):
    from confluence_publisher.manifest import load_manifest
    from confluence_publisher.sync import sync_project

    guide_dir = tmp_path / "guide"
    guide_dir.mkdir()
    root = tmp_path / "index.md"
    guide = guide_dir / "index.md"
    details = guide_dir / "details.md"
    root.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    guide.write_text("# Guide\n\nPlain text.\n", encoding="utf-8")
    details.write_text("# Details\n\nPlain text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
root:
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
  guide/index.md:
    page_id: "222222"
    title: "Guide"
  guide/details.md:
    page_id: "333333"
    title: "Details"
""".strip(),
        encoding="utf-8",
    )
    plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=manifest_path,
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(
            PlannedPage("index.md", root, "111111", "Home", None, None),
            PlannedPage("guide/index.md", guide, "222222", "Guide", None, "index.md"),
            PlannedPage("guide/details.md", details, "333333", "Details", None, "guide/index.md"),
        ),
        space="~6393203da593cb822e995262",
        root_page_id="111111",
        sync_enabled=True,
        recreate_stale_pages=True,
    )
    client = FakeSyncClient(
        page_errors={"222222": McpToolError("page 222222 not found")}
    )

    result = await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert result == 0
    assert ("create_page", "~6393203da593cb822e995262", "Guide", "111111", "800001") in client.calls
    assert ("create_page", "~6393203da593cb822e995262", "Details", "800001", "800002") in client.calls
    manifest = load_manifest(manifest_path)
    assert manifest.pages["guide/index.md"].page_id == "800001"
    assert manifest.pages["guide/details.md"].page_id == "800002"
    assert manifest.pages["guide/details.md"].content_hash.startswith("sha256:")


@pytest.mark.asyncio
async def test_sync_project_skips_page_with_matching_hash(tmp_path):
    from confluence_publisher.manifest import load_manifest
    from confluence_publisher.sync import prepare_sync_pages, sync_project

    source = tmp_path / "index.md"
    source.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    base_item = PlannedPage("index.md", source, "111111", "Home", None, None)
    base_plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=tmp_path / ".confluence-publish.yml",
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(base_item,),
        space="~6393203da593cb822e995262",
        root_page_id="111111",
        sync_enabled=True,
    )
    content_hash = prepare_sync_pages(base_plan, mmdc_bin="mmdc")["index.md"].content_hash
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        f"""
space: "~6393203da593cb822e995262"
root:
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
    content_hash: "{content_hash}"
    last_synced_at: "2026-04-28T12:00:00Z"
""".strip(),
        encoding="utf-8",
    )
    plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=manifest_path,
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(
            PlannedPage(
                "index.md",
                source,
                "111111",
                "Home",
                None,
                None,
                content_hash=content_hash,
                last_synced_at="2026-04-28T12:00:00Z",
            ),
        ),
        space="~6393203da593cb822e995262",
        root_page_id="111111",
        sync_enabled=True,
    )
    client = FakeSyncClient()

    result = await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert result == 0
    assert client.calls == [("get_page", "111111")]
    manifest = load_manifest(manifest_path)
    assert manifest.pages["index.md"].last_synced_at == "2026-04-28T12:00:00Z"


@pytest.mark.asyncio
async def test_sync_project_updates_page_with_changed_hash(tmp_path):
    from confluence_publisher.manifest import load_manifest
    from confluence_publisher.sync import sync_project

    source = tmp_path / "index.md"
    source.write_text("# Home\n\nChanged text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
root:
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
    content_hash: "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    last_synced_at: "2026-04-28T12:00:00Z"
""".strip(),
        encoding="utf-8",
    )
    plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=manifest_path,
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(
            PlannedPage(
                "index.md",
                source,
                "111111",
                "Home",
                None,
                None,
                content_hash="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                last_synced_at="2026-04-28T12:00:00Z",
            ),
        ),
        space="~6393203da593cb822e995262",
        root_page_id="111111",
        sync_enabled=True,
    )
    client = FakeSyncClient()

    result = await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert result == 0
    assert ("update_page_storage", "111111", "Home", "Sync docs") in client.calls
    manifest = load_manifest(manifest_path)
    assert manifest.pages["index.md"].content_hash.startswith("sha256:")
    assert manifest.pages["index.md"].canonical_hash == compute_canonical_markdown_hash(
        "# Home\n\nChanged text.\n"
    )
    assert (
        manifest.pages["index.md"].content_hash
        != "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    assert manifest.pages["index.md"].last_synced_at != "2026-04-28T12:00:00Z"


@pytest.mark.asyncio
async def test_sync_project_updates_existing_pages_without_space(tmp_path):
    from confluence_publisher.manifest import load_manifest
    from confluence_publisher.sync import sync_project

    source = tmp_path / "index.md"
    source.write_text("# Home\n\nChanged text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root:
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
    content_hash: "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    last_synced_at: "2026-04-28T12:00:00Z"
""".strip(),
        encoding="utf-8",
    )
    plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=manifest_path,
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(
            PlannedPage(
                "index.md",
                source,
                "111111",
                "Home",
                None,
                None,
                content_hash="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                last_synced_at="2026-04-28T12:00:00Z",
            ),
        ),
        space=None,
        root_page_id="111111",
        sync_enabled=True,
    )
    client = FakeSyncClient()

    result = await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert result == 0
    assert ("update_page_storage", "111111", "Home", "Sync docs") in client.calls
    assert not any(call[0] == "search_pages_by_title" for call in client.calls)
    assert not any(call[0] == "create_page" for call in client.calls)
    manifest = load_manifest(manifest_path)
    assert manifest.pages["index.md"].content_hash.startswith("sha256:")


@pytest.mark.asyncio
async def test_sync_project_missing_child_page_id_requires_create_missing_pages(tmp_path):
    from confluence_publisher.sync import sync_project

    child_dir = tmp_path / "guide"
    child_dir.mkdir()
    root = tmp_path / "index.md"
    child = child_dir / "index.md"
    root.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    child.write_text("# Guide\n\nPlain text.\n", encoding="utf-8")
    plan = sync_plan(
        tmp_path,
        items=(
            PlannedPage("index.md", root, "111111", "Home", None, None),
            PlannedPage("guide/index.md", child, None, "Guide", None, "index.md"),
        ),
    )
    client = FakeSyncClient()

    with pytest.raises(RuntimeError, match="--create-missing-pages"):
        await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert not any(call[0] == "create_page" for call in client.calls)


@pytest.mark.asyncio
async def test_sync_project_missing_child_page_id_creates_under_root(tmp_path):
    from confluence_publisher.manifest import load_manifest
    from confluence_publisher.sync import prepare_sync_pages, sync_project

    child_dir = tmp_path / "guide"
    child_dir.mkdir()
    root = tmp_path / "index.md"
    child = child_dir / "index.md"
    root.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    child.write_text("# Guide\n\nPlain text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
root:
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
  guide/index.md:
    title: "Guide"
""".strip(),
        encoding="utf-8",
    )
    child_hash_plan = sync_plan(
        tmp_path,
        items=(PlannedPage("guide/index.md", child, None, "Guide", None, "index.md"),),
    )
    child_hash = prepare_sync_pages(child_hash_plan, mmdc_bin="mmdc")[
        "guide/index.md"
    ].content_hash
    plan = sync_plan(
        tmp_path,
        items=(
            PlannedPage("index.md", root, "111111", "Home", None, None),
            PlannedPage(
                "guide/index.md",
                child,
                None,
                "Guide",
                None,
                "index.md",
                content_hash=child_hash,
            ),
        ),
    )
    plan = PublishPlan(
        **{**plan.__dict__, "manifest_path": manifest_path, "create_missing_pages": True}
    )
    client = FakeSyncClient()

    result = await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert result == 0
    assert ("search_pages_by_title", "~6393203da593cb822e995262", "Guide") in client.calls
    assert ("create_page", "~6393203da593cb822e995262", "Guide", "111111", "800001") in client.calls
    assert ("update_page_storage", "800001", "Guide", "Sync docs") in client.calls
    manifest = load_manifest(manifest_path)
    assert manifest.pages["guide/index.md"].page_id == "800001"
    assert manifest.pages["guide/index.md"].content_hash.startswith("sha256:")


@pytest.mark.asyncio
async def test_sync_project_dry_run_does_not_call_client_or_write_manifest(tmp_path):
    from confluence_publisher.sync import sync_project

    source = tmp_path / "index.md"
    source.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    original_manifest = """
space: "~6393203da593cb822e995262"
root:
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
""".strip()
    manifest_path.write_text(original_manifest, encoding="utf-8")
    plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=manifest_path,
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(PlannedPage("index.md", source, "111111", "Home", None, None),),
        space="~6393203da593cb822e995262",
        root_page_id="111111",
        sync_enabled=True,
    )
    client = FakeSyncClient()

    result = await sync_project(plan, client=client, dry_run=True, mmdc_bin="mmdc")

    assert result == 0
    assert client.calls == []
    assert manifest_path.read_text(encoding="utf-8") == original_manifest


@pytest.mark.asyncio
async def test_sync_project_recreates_missing_root_and_full_tree(tmp_path):
    from confluence_publisher.manifest import load_manifest
    from confluence_publisher.sync import sync_project

    guide_dir = tmp_path / "guide"
    guide_dir.mkdir()
    overview = tmp_path / "00-overview.md"
    guide = guide_dir / "index.md"
    overview.write_text("# Overview\n\nPlain text.\n", encoding="utf-8")
    guide.write_text("# Guide\n\nPlain text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
root:
  parent_page_id: "2163738446"
  page_id: "old-root"
pages:
  00-overview.md:
    page_id: "old-root"
    title: "Overview"
  guide/index.md:
    page_id: "old-guide"
    title: "Guide"
""".strip(),
        encoding="utf-8",
    )
    plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=manifest_path,
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(
            PlannedPage("00-overview.md", overview, "old-root", "Overview", None, None),
            PlannedPage("guide/index.md", guide, "old-guide", "Guide", None, None),
        ),
        space="~6393203da593cb822e995262",
        root_page_id="old-root",
        root_parent_page_id="2163738446",
        sync_enabled=True,
        recreate_missing_root=True,
    )
    client = FakeSyncClient(
        page_errors={"old-root": McpToolError("page old-root not found")}
    )

    result = await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert result == 0
    assert client.calls[:5] == [
        ("get_page", "old-root"),
        ("get_page", "2163738446"),
        ("search_pages_by_title", "~6393203da593cb822e995262", "Overview"),
        ("search_pages_by_title", "~6393203da593cb822e995262", "Guide"),
        ("create_page", "~6393203da593cb822e995262", "Overview", "2163738446", "800001"),
    ]
    assert ("create_page", "~6393203da593cb822e995262", "Guide", "800001", "800002") in client.calls
    assert ("update_page_storage", "800001", "Overview", "Sync docs") in client.calls
    assert ("update_page_storage", "800002", "Guide", "Sync docs") in client.calls
    manifest = load_manifest(manifest_path)
    assert manifest.root_page_id == "800001"
    assert manifest.pages["00-overview.md"].page_id == "800001"
    assert manifest.pages["guide/index.md"].page_id == "800002"
    assert manifest.pages["00-overview.md"].content_hash.startswith("sha256:")
    assert manifest.pages["guide/index.md"].last_synced_at.endswith("Z")


@pytest.mark.asyncio
async def test_sync_project_recreated_identities_are_written_before_publish_failure(tmp_path):
    from confluence_publisher.manifest import load_manifest
    from confluence_publisher.sync import sync_project

    guide_dir = tmp_path / "guide"
    guide_dir.mkdir()
    overview = tmp_path / "00-overview.md"
    guide = guide_dir / "index.md"
    overview.write_text("# Overview\n\nPlain text.\n", encoding="utf-8")
    guide.write_text("# Guide\n\nPlain text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
root:
  parent_page_id: "2163738446"
  page_id: "old-root"
pages:
  00-overview.md:
    page_id: "old-root"
    title: "Overview"
  guide/index.md:
    page_id: "old-guide"
    title: "Guide"
""".strip(),
        encoding="utf-8",
    )
    plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=manifest_path,
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(
            PlannedPage("00-overview.md", overview, "old-root", "Overview", None, None),
            PlannedPage("guide/index.md", guide, "old-guide", "Guide", None, None),
        ),
        space="~6393203da593cb822e995262",
        root_page_id="old-root",
        root_parent_page_id="2163738446",
        sync_enabled=True,
        recreate_missing_root=True,
    )
    client = FakeSyncClient(
        page_errors={"old-root": McpToolError("page old-root not found")},
        update_errors={"800001": RuntimeError("update failed")},
    )

    with pytest.raises(RuntimeError, match="update failed"):
        await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    manifest = load_manifest(manifest_path)
    assert manifest.root_page_id == "800001"
    assert manifest.root_url == "/spaces/~6393203da593cb822e995262/pages/800001/Overview"
    assert manifest.pages["00-overview.md"].page_id == "800001"
    assert manifest.pages["00-overview.md"].url == "/spaces/~6393203da593cb822e995262/pages/800001/Overview"
    assert manifest.pages["guide/index.md"].page_id == "800002"
    assert manifest.pages["guide/index.md"].url == "/spaces/~6393203da593cb822e995262/pages/800002/Guide"
    assert manifest.pages["00-overview.md"].content_hash is None
    assert manifest.pages["00-overview.md"].last_synced_at is None
    assert manifest.pages["guide/index.md"].content_hash is None
    assert manifest.pages["guide/index.md"].last_synced_at is None


@pytest.mark.asyncio
async def test_sync_project_root_recreation_title_collision_blocks_before_create(tmp_path):
    from confluence_publisher.sync import sync_project

    source = tmp_path / "index.md"
    source.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
root:
  parent_page_id: "2163738446"
  page_id: "old-root"
pages:
  index.md:
    page_id: "old-root"
    title: "Home"
""".strip(),
        encoding="utf-8",
    )
    plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=manifest_path,
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(PlannedPage("index.md", source, "old-root", "Home", None, None),),
        space="~6393203da593cb822e995262",
        root_page_id="old-root",
        root_parent_page_id="2163738446",
        sync_enabled=True,
        recreate_missing_root=True,
    )
    client = FakeSyncClient(
        page_errors={"old-root": McpToolError("page old-root not found")},
        search_results={"Home": [PageInfo(page_id="999999", title="Home")]},
    )

    with pytest.raises(RuntimeError, match="titles already exist.*Home.*999999"):
        await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert ("create_page", "~6393203da593cb822e995262", "Home", "2163738446", "800001") not in client.calls


@pytest.mark.asyncio
async def test_sync_project_root_recreation_duplicate_local_titles_block_before_create(tmp_path):
    from confluence_publisher.sync import sync_project

    guide_dir = tmp_path / "guide"
    guide_dir.mkdir()
    overview = tmp_path / "00-overview.md"
    guide = guide_dir / "index.md"
    overview.write_text("# Duplicate\n\nPlain text.\n", encoding="utf-8")
    guide.write_text("# Duplicate\n\nPlain text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
root:
  parent_page_id: "2163738446"
  page_id: "old-root"
pages:
  00-overview.md:
    page_id: "old-root"
    title: "Duplicate"
  guide/index.md:
    page_id: "old-guide"
    title: "Duplicate"
""".strip(),
        encoding="utf-8",
    )
    plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=manifest_path,
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(
            PlannedPage("00-overview.md", overview, "old-root", "Duplicate", None, None),
            PlannedPage("guide/index.md", guide, "old-guide", "Duplicate", None, None),
        ),
        space="~6393203da593cb822e995262",
        root_page_id="old-root",
        root_parent_page_id="2163738446",
        sync_enabled=True,
        recreate_missing_root=True,
    )
    client = FakeSyncClient(
        page_errors={"old-root": McpToolError("page old-root not found")}
    )

    with pytest.raises(
        RuntimeError,
        match="duplicate.*title.*Duplicate.*00-overview\\.md.*guide/index\\.md",
    ):
        await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert not any(call[0] == "create_page" for call in client.calls)


def test_prepare_sync_pages_handles_long_deep_source_paths(tmp_path, monkeypatch):
    class FakeRenderer:
        def __init__(self, _mmdc_bin: str) -> None:
            pass

        def render(self, block, temp_dir):
            mmd_path = temp_dir / Path(block.attachment_name).with_suffix(".mmd").name
            output_path = temp_dir / block.attachment_name
            mmd_path.write_text(block.source, encoding="utf-8")
            output_path.write_text("<svg></svg>", encoding="utf-8")
            return RenderedDiagram(block=block, mmd_path=mmd_path, output_path=output_path)

    source_path = "/".join(["deep"] * 70 + ["index.md"])
    source = tmp_path / source_path
    source.parent.mkdir(parents=True)
    source.write_text("# Deep Page\n\n```mermaid\ngraph LR\nA --> B\n```\n", encoding="utf-8")
    plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=tmp_path / ".confluence-publish.yml",
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(
            PlannedPage(
                source_path=source_path,
                absolute_path=source,
                page_id="111111",
                title="Deep Page",
                url=None,
                parent_source_path=None,
            ),
        ),
        sync_enabled=True,
    )

    monkeypatch.setattr(page, "MermaidRenderer", FakeRenderer)

    prepared = prepare_sync_pages(plan, mmdc_bin="mmdc")

    diagram = prepared[source_path].rendered_diagrams[0]
    assert diagram.mmd_path.exists()
    assert diagram.output_path.exists()
    assert diagram.output_path.name == diagram.block.attachment_name
    assert diagram.mmd_path.name == Path(diagram.block.attachment_name).with_suffix(".mmd").name
