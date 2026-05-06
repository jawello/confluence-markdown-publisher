from pathlib import Path

from confluence_publisher.models import RenderedDiagram
from confluence_publisher.page import prepare_page
from confluence_publisher.renderer import RenderError


class FakeRenderer:
    def __init__(self, _mmdc_bin: str) -> None:
        pass

    def render(self, block, temp_dir: Path) -> RenderedDiagram:
        mmd_path = temp_dir / Path(block.attachment_name).with_suffix(".mmd").name
        output_path = temp_dir / block.attachment_name
        mmd_path.write_text(block.source, encoding="utf-8")
        output_path.write_text("<svg></svg>", encoding="utf-8")
        return RenderedDiagram(block=block, mmd_path=mmd_path, output_path=output_path)


class FailingRenderer:
    def __init__(self, _mmdc_bin: str) -> None:
        pass

    def render(self, _block, _temp_dir: Path) -> RenderedDiagram:
        raise RenderError("render failed")


def test_prepare_page_converts_markdown_to_storage(tmp_path):
    source = tmp_path / "overview.md"
    source.write_text("# Overview\n\nPlain text.\n", encoding="utf-8")

    with prepare_page(
        source=source,
        output_format="svg",
        diagram_width=1000,
        mmdc_bin="mmdc",
        allow_diagram_fallback=False,
    ) as prepared:
        assert "<h1>Overview</h1>" in prepared.storage
        assert "<p>Plain text.</p>" in prepared.storage
        assert prepared.rendered_diagrams == []


def test_prepare_page_renders_mermaid_and_honors_width_directives(tmp_path, monkeypatch):
    source = tmp_path / "overview.md"
    source.write_text(
        "# Overview\n\n```mermaid\n%% confluence: width=1200 %%\ngraph LR\nA --> B\n```\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("confluence_publisher.page.MermaidRenderer", FakeRenderer)

    with prepare_page(
        source=source,
        output_format="svg",
        diagram_width=1000,
        mmdc_bin="mmdc",
        allow_diagram_fallback=False,
    ) as prepared:
        assert len(prepared.rendered_diagrams) == 1
        assert prepared.rendered_diagrams[0].output_path.exists()
        assert prepared.rendered_diagrams[0].block.display_width == 1200
        assert 'ac:width="1200"' in prepared.storage
        assert 'ac:width="1000"' not in prepared.storage


def test_prepare_page_fallback_outputs_no_rendered_attachment_and_includes_mermaid_source(
    tmp_path, monkeypatch
):
    source = tmp_path / "overview.md"
    source.write_text("# Overview\n\n```mermaid\ngraph LR\nA --> B\n```\n", encoding="utf-8")

    monkeypatch.setattr("confluence_publisher.page.MermaidRenderer", FailingRenderer)

    with prepare_page(
        source=source,
        output_format="svg",
        diagram_width=1000,
        mmdc_bin="mmdc",
        allow_diagram_fallback=True,
    ) as prepared:
        assert prepared.rendered_diagrams == []
        assert "Mermaid diagram was not rendered automatically." in prepared.storage
        assert "graph LR\nA --> B" in prepared.storage
        assert "ri:attachment" not in prepared.storage
