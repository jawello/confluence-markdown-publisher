from pathlib import Path

from confluence_publisher import cli
from confluence_publisher.models import RenderedDiagram
from confluence_publisher.planner import PlannedPage, PublishPlan
from confluence_publisher.sync import (
    CONTENT_HASH_VERSION,
    PreparedSyncPage,
    compute_content_hash,
    prepare_sync_pages,
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
