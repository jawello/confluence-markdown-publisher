from pathlib import Path

from confluence_publisher import batch
from confluence_publisher.planner import PlannedPage, PublishPlan


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
