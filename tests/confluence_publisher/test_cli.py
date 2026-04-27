from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from confluence_publisher import cli
from confluence_publisher.cli import build_arg_parser, build_preview
from confluence_publisher.manifest import load_manifest
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


def test_arg_parser_accepts_project_mode():
    args = build_arg_parser().parse_args(["--project", "docs", "--plan-only"])

    assert args.project == Path("docs")
    assert args.plan_only is True
    assert args.manifest is None


def test_validate_args_rejects_source_and_project_together():
    args = build_arg_parser().parse_args(
        ["--source", "page.md", "--page-id", "111111", "--project", "docs"]
    )

    with pytest.raises(SystemExit):
        cli.validate_args(args)


def test_validate_args_requires_page_id_for_single_page():
    args = build_arg_parser().parse_args(["--source", "page.md"])

    with pytest.raises(SystemExit):
        cli.validate_args(args)


def test_validate_args_rejects_page_id_in_project_mode():
    args = build_arg_parser().parse_args(["--project", "docs", "--page-id", "111111"])

    with pytest.raises(SystemExit):
        cli.validate_args(args)


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
        self.created = 0

    async def get_page(self, page_id: str) -> PageInfo:
        self.calls.append(("get_page", page_id))
        return PageInfo(page_id=page_id, title=self.title)

    async def create_page(
        self,
        *,
        space_key: str,
        title: str,
        parent_id: str,
        storage: str,
    ) -> PageInfo:
        self.created += 1
        page_id = str(700000 + self.created)
        self.calls.append(
            ("create_page", space_key, title, parent_id, "<p>" in storage, page_id)
        )
        return PageInfo(page_id=page_id, title=title)

    async def upload_attachment(self, page_id: str, file_path: Path) -> None:
        self.calls.append(("upload_attachment", page_id, file_path.name, file_path.exists()))

    async def update_page_storage(
        self,
        page_id: str,
        title: str,
        storage: str,
        version_comment: str = "Rendered Mermaid diagrams from Markdown source",
    ) -> None:
        self.calls.append(
            (
                "update_page_storage",
                page_id,
                title,
                "<ac:image" in storage,
                'ac:width="1000"' in storage,
                'ac:width="1200"' in storage,
                version_comment,
            )
        )


def fake_connect_for(client: FakeClient):
    @asynccontextmanager
    async def fake_connect():
        yield client

    return fake_connect


@pytest.mark.asyncio
async def test_run_async_project_plan_only_does_not_connect_to_mcp(tmp_path, monkeypatch, capsys):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
pages:
  index.md:
    page_id: "111111"
    title: "Home"
""".strip(),
        encoding="utf-8",
    )

    def fail_connect():
        raise AssertionError("plan-only must not connect to MCP")

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fail_connect)

    result = await cli.run_async(["--project", str(tmp_path), "--plan-only"])

    assert result == 0
    output = capsys.readouterr().out
    assert "Confluence project publish plan" in output
    assert "index.md -> 111111" in output


@pytest.mark.asyncio
async def test_run_async_project_missing_manifest_entry_exits_before_mcp(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
pages:
  index.md:
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    def fail_connect():
        raise AssertionError("planning error must not connect to MCP")

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fail_connect)

    with pytest.raises(SystemExit, match="guide.md"):
        await cli.run_async(["--project", str(tmp_path), "--plan-only"])


@pytest.mark.asyncio
async def test_run_async_project_invalid_include_pattern_exits_before_mcp(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
include:
  - "../*.md"
pages:
  index.md:
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    def fail_connect():
        raise AssertionError("validation error must not connect to MCP")

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fail_connect)

    with pytest.raises(SystemExit, match="parent traversal"):
        await cli.run_async(["--project", str(tmp_path), "--plan-only"])


@pytest.mark.asyncio
async def test_run_async_project_live_updates_with_comment(tmp_path, monkeypatch):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
defaults:
  version_comment: "Manifest comment"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
""".strip(),
        encoding="utf-8",
    )
    client = FakeClient()

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))

    result = await cli.run_async(
        ["--project", str(tmp_path), "--comment", "CLI comment", "--yes"]
    )

    assert result == 0
    assert client.calls == [
        ("update_page_storage", "111111", "Home", False, False, False, "CLI comment")
    ]


@pytest.mark.asyncio
async def test_run_async_project_create_missing_pages_updates_manifest_and_publishes(
    tmp_path, monkeypatch
):
    (tmp_path / "guide").mkdir()
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "guide" / "index.md").write_text("# Guide\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root:
  url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/999999/Docs"
pages:
  index.md:
    title: "Home"
  guide/index.md:
    title: "Guide"
""".strip(),
        encoding="utf-8",
    )
    client = FakeClient()

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))

    result = await cli.run_async(["--project", str(tmp_path), "--create-missing-pages", "--yes"])

    assert result == 0
    assert client.calls == [
        ("create_page", "~6393203da593cb822e995262", "Home", "999999", True, "700001"),
        ("create_page", "~6393203da593cb822e995262", "Guide", "700001", True, "700002"),
        (
            "update_page_storage",
            "700001",
            "Home",
            False,
            False,
            False,
            "Sync documentation from Markdown",
        ),
        (
            "update_page_storage",
            "700002",
            "Guide",
            False,
            False,
            False,
            "Sync documentation from Markdown",
        ),
    ]
    updated_manifest = load_manifest(manifest_path)
    assert updated_manifest.pages["index.md"].page_id == "700001"
    assert updated_manifest.pages["guide/index.md"].page_id == "700002"


@pytest.mark.asyncio
async def test_run_async_project_plan_only_create_missing_pages_does_not_connect(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
root:
  page_id: "999999"
space: "~6393203da593cb822e995262"
pages:
  index.md:
    title: "Home"
""".strip(),
        encoding="utf-8",
    )

    def fail_connect():
        raise AssertionError("plan-only must not connect to MCP")

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fail_connect)

    result = await cli.run_async(
        ["--project", str(tmp_path), "--create-missing-pages", "--plan-only"]
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "Root page: 999999" in output
    assert "Creates: 1" in output
    assert "index.md -> <missing>" in output


@pytest.mark.asyncio
async def test_run_async_project_dry_run_uses_manifest_publish_defaults(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "index.md").write_text(
        "# Home\n\n```mermaid\ngraph LR\nA --> B\n```\n",
        encoding="utf-8",
    )
    (tmp_path / ".confluence-publish.yml").write_text(
        """
defaults:
  output_format: "png"
  diagram_width: 1400
pages:
  index.md:
    page_id: "111111"
    title: "Home"
""".strip(),
        encoding="utf-8",
    )

    def fail_connect():
        raise AssertionError("project dry-run must not connect to MCP")

    monkeypatch.setattr(cli, "MermaidRenderer", FakeRenderer)
    monkeypatch.setattr(cli, "connect_mcp_atlassian", fail_connect)

    result = await cli.run_async(["--project", str(tmp_path), "--dry-run"])

    assert result == 0
    output = capsys.readouterr().out
    assert ".png" in output
    assert 'ac:width="1400"' in output
    assert ".svg" not in output
    assert 'ac:width="1000"' not in output


@pytest.mark.asyncio
async def test_run_async_project_dry_run_cli_publish_defaults_override_manifest(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "index.md").write_text(
        "# Home\n\n```mermaid\ngraph LR\nA --> B\n```\n",
        encoding="utf-8",
    )
    (tmp_path / ".confluence-publish.yml").write_text(
        """
defaults:
  output_format: "png"
  diagram_width: 1400
pages:
  index.md:
    page_id: "111111"
    title: "Home"
""".strip(),
        encoding="utf-8",
    )

    def fail_connect():
        raise AssertionError("project dry-run must not connect to MCP")

    monkeypatch.setattr(cli, "MermaidRenderer", FakeRenderer)
    monkeypatch.setattr(cli, "connect_mcp_atlassian", fail_connect)

    result = await cli.run_async(
        [
            "--project",
            str(tmp_path),
            "--dry-run",
            "--output-format",
            "svg",
            "--diagram-width",
            "900",
        ]
    )

    assert result == 0
    output = capsys.readouterr().out
    assert ".svg" in output
    assert 'ac:width="900"' in output
    assert ".png" not in output
    assert 'ac:width="1400"' not in output


@pytest.mark.asyncio
async def test_publish_single_page_uses_supplied_version_comment(tmp_path, monkeypatch):
    source = tmp_path / "overview.md"
    source.write_text("# Overview\n\nPlain text.\n", encoding="utf-8")
    client = FakeClient()

    await cli.publish_single_page(
        page_id="5151854234",
        title="Test",
        source=source,
        output_format="svg",
        diagram_width=1000,
        mmdc_bin="mmdc",
        allow_diagram_fallback=False,
        dry_run=False,
        client=client,
        version_comment="Sync docs",
    )

    assert client.calls == [
        ("update_page_storage", "5151854234", "Test", False, False, False, "Sync docs")
    ]


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
    assert client.calls[1] == (
        "update_page_storage",
        "5151854234",
        "Test",
        True,
        True,
        False,
        "Rendered Mermaid diagrams from Markdown source",
    )


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
    assert client.calls[1] == (
        "update_page_storage",
        "5151854234",
        "Test",
        True,
        False,
        True,
        "Rendered Mermaid diagrams from Markdown source",
    )


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
