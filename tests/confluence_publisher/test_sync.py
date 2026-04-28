from pathlib import Path

import pytest

from confluence_publisher import cli
from confluence_publisher.mcp_atlassian import McpToolError
from confluence_publisher.models import PageInfo
from confluence_publisher.models import RenderedDiagram
from confluence_publisher.planner import PlannedPage, PublishPlan
from confluence_publisher.sync import (
    CONTENT_HASH_VERSION,
    PreparedSyncPage,
    compute_content_hash,
    prepare_sync_pages,
)


class FakeSyncClient:
    def __init__(self, *, page_errors=None, search_results=None):
        self.calls = []
        self.created = 0
        self.page_errors = page_errors or {}
        self.search_results = search_results or {}

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

    monkeypatch.setattr(cli, "MermaidRenderer", FakeRenderer)

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
async def test_sync_project_stale_page_with_policy_uses_recovery_stub_before_writes(tmp_path):
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
            PlannedPage("guide/index.md", child, "222222", "Guide", None, "index.md"),
        ),
    )
    plan = PublishPlan(**{**plan.__dict__, "recreate_stale_pages": True})
    client = FakeSyncClient(
        page_errors={"222222": McpToolError("page 222222 not found")}
    )

    with pytest.raises(RuntimeError, match="Stale page recreation requires the page recovery task"):
        await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert client.calls == [("get_page", "111111"), ("get_page", "222222")]


@pytest.mark.asyncio
async def test_sync_project_missing_page_id_fails_before_writes(tmp_path):
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

    with pytest.raises(RuntimeError, match="missing page_id.*guide/index.md"):
        await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert client.calls == [("get_page", "111111")]


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

    monkeypatch.setattr(cli, "MermaidRenderer", FakeRenderer)

    prepared = prepare_sync_pages(plan, mmdc_bin="mmdc")

    diagram = prepared[source_path].rendered_diagrams[0]
    assert diagram.mmd_path.exists()
    assert diagram.output_path.exists()
    assert diagram.output_path.name == diagram.block.attachment_name
    assert diagram.mmd_path.name == Path(diagram.block.attachment_name).with_suffix(".mmd").name
