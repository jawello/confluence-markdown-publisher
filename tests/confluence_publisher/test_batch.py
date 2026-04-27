from pathlib import Path

import pytest

from confluence_publisher import batch
from confluence_publisher.mcp_atlassian import McpToolError
from confluence_publisher.models import PageInfo
from confluence_publisher.planner import PlannedPage, PublishPlan


class FakeClient:
    def __init__(self, search_results=None, page_errors=None):
        self.calls = []
        self.search_results = search_results or {}
        self.page_errors = page_errors or {}

    async def get_page(self, page_id: str) -> PageInfo:
        self.calls.append(("get_page", page_id))
        if page_id in self.page_errors:
            raise self.page_errors[page_id]
        return PageInfo(page_id=page_id, title=f"Page {page_id}")

    async def search_pages_by_title(self, *, space_key: str, title: str):
        self.calls.append(("search_pages_by_title", space_key, title))
        return self.search_results.get(title, [])

    async def create_page(
        self,
        *,
        space_key: str,
        title: str,
        parent_id: str,
        storage: str,
    ) -> PageInfo:
        self.calls.append(("create_page", space_key, title, parent_id, "<p>" in storage))
        return PageInfo(page_id="222222", title=title)

    async def upload_attachment(self, page_id: str, file_path: Path) -> None:
        self.calls.append(("upload_attachment", page_id, file_path.name))

    async def update_page_storage(
        self,
        page_id: str,
        title: str,
        storage: str,
        version_comment: str = "Rendered Mermaid diagrams from Markdown source",
    ) -> None:
        self.calls.append(("update_page_storage", page_id, title, version_comment, "<h1>" in storage))


def make_plan(tmp_path):
    source = tmp_path / "index.md"
    source.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    return PublishPlan(
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
                url="https://criteo.atlassian.net/wiki/pages/111111/Home",
                parent_source_path=None,
            ),
        ),
    )


def make_create_plan(tmp_path):
    source = tmp_path / "index.md"
    source.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    return PublishPlan(
        project_root=tmp_path,
        manifest_path=tmp_path / ".confluence-publish.yml",
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        space="~6393203da593cb822e995262",
        root_page_id="999999",
        create_missing_pages=True,
        items=(
            PlannedPage(
                source_path="index.md",
                absolute_path=source,
                page_id=None,
                title="Post-Deployment Verification",
                url=None,
                parent_source_path=None,
            ),
        ),
    )


def test_format_publish_plan_includes_urls_and_comment(tmp_path):
    plan = make_plan(tmp_path)

    text = batch.format_publish_plan(plan)

    assert f"Project root: {tmp_path}" in text
    assert f"Manifest: {tmp_path / '.confluence-publish.yml'}" in text
    assert "Updates: 1" in text
    assert "Excluded markdown files: 0" in text
    assert "Version comment: Sync docs" in text
    assert "index.md -> 111111" in text
    assert "Title: Home" in text
    assert "URL: https://criteo.atlassian.net/wiki/pages/111111/Home" in text


def test_format_publish_plan_warns_for_missing_url(tmp_path):
    plan = make_plan(tmp_path)
    item = plan.items[0]
    plan = PublishPlan(
        project_root=plan.project_root,
        manifest_path=plan.manifest_path,
        version_comment=plan.version_comment,
        output_format=plan.output_format,
        diagram_width=plan.diagram_width,
        allow_diagram_fallback=plan.allow_diagram_fallback,
        excluded_count=plan.excluded_count,
        items=(
            PlannedPage(
                source_path=item.source_path,
                absolute_path=item.absolute_path,
                page_id=item.page_id,
                title=item.title,
                url=None,
                parent_source_path=item.parent_source_path,
            ),
        ),
    )

    text = batch.format_publish_plan(plan)

    assert "URL: missing" in text


@pytest.mark.asyncio
async def test_publish_project_dry_run_does_not_write_to_confluence(tmp_path):
    plan = make_plan(tmp_path)
    client = FakeClient()

    result = await batch.publish_project(plan, client=client, dry_run=True, mmdc_bin="mmdc")

    assert result == 0
    assert client.calls == []


@pytest.mark.asyncio
async def test_publish_project_live_updates_each_page_with_comment(tmp_path):
    plan = make_plan(tmp_path)
    client = FakeClient()

    result = await batch.publish_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert result == 0
    assert client.calls == [("update_page_storage", "111111", "Home", "Sync docs", True)]


@pytest.mark.asyncio
async def test_publish_project_preflights_title_collisions_before_creating(tmp_path):
    plan = make_create_plan(tmp_path)
    client = FakeClient(
        search_results={
            "Post-Deployment Verification": [
                PageInfo(page_id="5155817687", title="Post-Deployment Verification")
            ]
        }
    )

    with pytest.raises(RuntimeError, match="Post-Deployment Verification.*5155817687"):
        await batch.publish_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert client.calls == [
        ("get_page", "999999"),
        (
            "search_pages_by_title",
            "~6393203da593cb822e995262",
            "Post-Deployment Verification",
        )
    ]


@pytest.mark.asyncio
async def test_publish_project_preflights_parent_visibility_before_title_search(tmp_path):
    plan = make_create_plan(tmp_path)
    client = FakeClient(
        page_errors={
            "999999": McpToolError(
                "Failed to retrieve page by ID '999999': no permission to view content"
            )
        }
    )

    with pytest.raises(RuntimeError, match="999999.*no permission"):
        await batch.publish_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert client.calls == [("get_page", "999999")]
