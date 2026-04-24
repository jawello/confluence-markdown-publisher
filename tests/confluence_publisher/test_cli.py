from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from confluence_publisher import cli
from confluence_publisher.cli import build_arg_parser, build_preview
from confluence_publisher.models import PageInfo, RenderedDiagram


def test_arg_parser_defaults_to_svg_and_mcp_backend():
    args = build_arg_parser().parse_args(
        ["--source", "en/00-overview.md", "--page-id", "5151854234"]
    )

    assert args.source == Path("en/00-overview.md")
    assert args.page_id == "5151854234"
    assert args.output_format == "svg"
    assert args.diagram_width == 1000
    assert args.dry_run is False


def test_arg_parser_rejects_non_positive_diagram_width():
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(
            [
                "--source",
                "en/00-overview.md",
                "--page-id",
                "5151854234",
                "--diagram-width",
                "0",
            ]
        )


def test_preview_lists_page_source_and_attachments():
    preview = build_preview(
        page_id="5151854234",
        title="Test",
        source=Path("en/00-overview.md"),
        attachments=["diagram-00-overview-01-a1b2c3d4.svg"],
        output_format="svg",
        dry_run=True,
        fallback_enabled=False,
    )

    assert "Page ID: 5151854234" in preview
    assert "Title: Test" in preview
    assert "Source: en/00-overview.md" in preview
    assert "Format: svg" in preview
    assert "Dry run: yes" in preview
    assert "Fallback mode: no" in preview
    assert "diagram-00-overview-01-a1b2c3d4.svg" in preview


@pytest.mark.asyncio
async def test_run_async_dry_run_does_not_connect_to_mcp(tmp_path, monkeypatch, capsys):
    source = tmp_path / "overview.md"
    source.write_text("# Overview\n\nPlain text.\n", encoding="utf-8")

    def fail_connect():
        raise AssertionError("dry-run must not connect to MCP")

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fail_connect)

    result = await cli.run_async(["--source", str(source), "--page-id", "5151854234", "--dry-run"])

    assert result == 0
    output = capsys.readouterr().out
    assert "Dry run: yes" in output


class FakeClient:
    def __init__(self, title: str = "Fetched Title") -> None:
        self.title = title
        self.calls = []

    async def get_page(self, page_id: str) -> PageInfo:
        self.calls.append(("get_page", page_id))
        return PageInfo(page_id=page_id, title=self.title)

    async def upload_attachment(self, page_id: str, file_path: Path) -> None:
        self.calls.append(("upload_attachment", page_id, file_path.name, file_path.exists()))

    async def update_page_storage(self, page_id: str, title: str, storage: str) -> None:
        self.calls.append(
            (
                "update_page_storage",
                page_id,
                title,
                "<ac:image" in storage,
                'ac:width="1000"' in storage,
                'ac:width="1200"' in storage,
            )
        )


def fake_connect_for(client: FakeClient):
    @asynccontextmanager
    async def fake_connect():
        yield client

    return fake_connect


@pytest.mark.asyncio
async def test_run_async_live_fetches_missing_title_and_abort_prevents_writes(
    tmp_path, monkeypatch
):
    source = tmp_path / "overview.md"
    source.write_text("# Overview\n\nPlain text.\n", encoding="utf-8")
    client = FakeClient(title="Existing Confluence Title")

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))
    monkeypatch.setattr("builtins.input", lambda _prompt: "no")

    result = await cli.run_async(["--source", str(source), "--page-id", "5151854234"])

    assert result == 1
    assert client.calls == [("get_page", "5151854234")]


class FakeRenderer:
    def __init__(self, _mmdc_bin: str) -> None:
        pass

    def render(self, block, temp_dir: Path) -> RenderedDiagram:
        output_path = temp_dir / block.attachment_name
        output_path.write_text("<svg></svg>", encoding="utf-8")
        return RenderedDiagram(block=block, mmd_path=temp_dir / "diagram.mmd", output_path=output_path)


@pytest.mark.asyncio
async def test_run_async_live_uploads_attachments_before_storage_update(tmp_path, monkeypatch):
    source = tmp_path / "overview.md"
    source.write_text("# Overview\n\n```mermaid\ngraph LR\nA --> B\n```\n", encoding="utf-8")
    client = FakeClient()

    monkeypatch.setattr(cli, "MermaidRenderer", FakeRenderer)
    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))

    result = await cli.run_async(
        ["--source", str(source), "--page-id", "5151854234", "--title", "Test", "--yes"]
    )

    assert result == 0
    assert client.calls[0][:2] == ("upload_attachment", "5151854234")
    assert client.calls[0][3] is True
    assert client.calls[1] == ("update_page_storage", "5151854234", "Test", True, True, False)


@pytest.mark.asyncio
async def test_run_async_uses_mermaid_width_directive_over_cli_default(tmp_path, monkeypatch):
    source = tmp_path / "overview.md"
    source.write_text(
        "# Overview\n\n```mermaid\n%% confluence: width=1200 %%\ngraph LR\nA --> B\n```\n",
        encoding="utf-8",
    )
    client = FakeClient()

    monkeypatch.setattr(cli, "MermaidRenderer", FakeRenderer)
    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))

    result = await cli.run_async(
        ["--source", str(source), "--page-id", "5151854234", "--title", "Test", "--yes"]
    )

    assert result == 0
    assert client.calls[1] == ("update_page_storage", "5151854234", "Test", True, False, True)


class FailingRenderer:
    def __init__(self, _mmdc_bin: str) -> None:
        pass

    def render(self, _block, _temp_dir: Path) -> RenderedDiagram:
        raise cli.RenderError("render failed")


@pytest.mark.asyncio
async def test_run_async_render_failure_without_fallback_exits_before_mcp(
    tmp_path, monkeypatch
):
    source = tmp_path / "overview.md"
    source.write_text("# Overview\n\n```mermaid\ngraph LR\nA --> B\n```\n", encoding="utf-8")

    def fail_connect():
        raise AssertionError("render failure must stop before MCP connection")

    monkeypatch.setattr(cli, "MermaidRenderer", FailingRenderer)
    monkeypatch.setattr(cli, "connect_mcp_atlassian", fail_connect)

    with pytest.raises(SystemExit, match="render failed"):
        await cli.run_async(["--source", str(source), "--page-id", "5151854234"])


@pytest.mark.asyncio
async def test_run_async_fallback_outputs_no_attachments_for_failed_diagram(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "overview.md"
    source.write_text("# Overview\n\n```mermaid\ngraph LR\nA --> B\n```\n", encoding="utf-8")

    def fail_connect():
        raise AssertionError("dry-run fallback must not connect to MCP")

    monkeypatch.setattr(cli, "MermaidRenderer", FailingRenderer)
    monkeypatch.setattr(cli, "connect_mcp_atlassian", fail_connect)

    result = await cli.run_async(
        [
            "--source",
            str(source),
            "--page-id",
            "5151854234",
            "--dry-run",
            "--allow-diagram-fallback",
        ]
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "Attachments:\n  - none" in output
    assert "Mermaid diagram was not rendered automatically." in output
