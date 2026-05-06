from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from confluence_publisher import cli
from confluence_publisher.cli import build_arg_parser, build_preview
from confluence_publisher.manifest import load_manifest
from confluence_publisher.models import PageInfo, PageStorage, RenderedDiagram


def test_arg_parser_requires_subcommand():
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args([])


@pytest.mark.parametrize(
    "argv",
    [
        ["--source", "page.md", "--page-id", "111111"],
        ["--project", "docs"],
        ["--project", "docs", "--check-sync"],
        ["--project", "docs", "--pull"],
        ["--project", "docs", "--sync"],
    ],
)
def test_arg_parser_rejects_legacy_top_level_forms(argv):
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(argv)


def test_arg_parser_accepts_clone_url_positional_form(tmp_path):
    args = build_arg_parser().parse_args(
        [
            "clone",
            "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/"
            "pages/5164564861/Custom+UI+NEXT+Migration",
            str(tmp_path / "docs"),
            "--max-depth",
            "2",
            "--max-pages",
            "25",
            "--dry-run",
        ]
    )

    assert args.command == "clone"
    assert args.clone_source.startswith("https://criteo.atlassian.net/wiki/")
    assert args.clone_output == tmp_path / "docs"
    assert args.clone_space is None
    assert args.clone_max_depth == 2
    assert args.clone_max_pages == 25
    assert args.dry_run is True


def test_arg_parser_accepts_clone_page_id_positional_form(tmp_path):
    args = build_arg_parser().parse_args(
        [
            "clone",
            "5164564861",
            str(tmp_path / "docs"),
            "--space",
            "~6393203da593cb822e995262",
        ]
    )

    assert args.command == "clone"
    assert args.clone_source == "5164564861"
    assert args.clone_output == tmp_path / "docs"
    assert args.clone_space == "~6393203da593cb822e995262"


@pytest.mark.parametrize("command", ["plan", "status", "pull", "push"])
def test_arg_parser_accepts_project_subcommands(command):
    args = build_arg_parser().parse_args([command, "docs"])

    assert args.command == command
    assert args.project == Path("docs")
    assert args.manifest is None


def test_arg_parser_accepts_plan_and_push_recovery_flags():
    plan_args = build_arg_parser().parse_args(
        ["plan", "docs", "--create-missing-pages", "--recreate-missing-root"]
    )
    push_args = build_arg_parser().parse_args(
        ["push", "docs", "--create-missing-pages", "--recreate-stale-pages", "--yes"]
    )

    assert plan_args.create_missing_pages is True
    assert plan_args.recreate_missing_root is True
    assert push_args.create_missing_pages is True
    assert push_args.recreate_stale_pages is True
    assert push_args.yes is True


@pytest.mark.parametrize(
    "argv",
    [
        ["status", "docs", "--create-missing-pages"],
        ["pull", "docs", "--recreate-missing-root"],
        ["pull", "docs", "--recreate-stale-pages"],
    ],
)
def test_arg_parser_rejects_recovery_flags_outside_plan_and_push(argv):
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(argv)


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


@pytest.mark.asyncio
async def test_run_async_clone_dispatches_to_clone_flow(tmp_path, monkeypatch, capsys):
    calls = []

    async def fake_clone_confluence_tree(request, *, client):
        calls.append(("clone", request.root_page_id, request.space, request.output, client))
        from confluence_publisher.clone import CloneResult, build_clone_file_plan
        from confluence_publisher.models import ConfluenceTreeNode

        tree = ConfluenceTreeNode(page_id=request.root_page_id, title="Root")
        return CloneResult(
            output=request.output,
            page_count=1,
            file_plan=build_clone_file_plan(tree),
            written=not request.dry_run,
        )

    client = FakeClient()
    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))
    monkeypatch.setattr(cli, "clone_confluence_tree", fake_clone_confluence_tree)

    result = await cli.run_async(
        [
            "clone",
            "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/"
            "pages/5164564861/Custom+UI+NEXT+Migration",
            str(tmp_path / "docs"),
            "--dry-run",
        ]
    )

    assert result == 0
    assert calls == [
        (
            "clone",
            "5164564861",
            "~6393203da593cb822e995262",
            tmp_path / "docs",
            client,
        )
    ]
    assert "Confluence clone dry run" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_run_async_clone_preserves_subcommand_dry_run(tmp_path, monkeypatch, capsys):
    calls = []

    async def fake_clone_confluence_tree(request, *, client):
        calls.append(request.dry_run)
        from confluence_publisher.clone import CloneResult, build_clone_file_plan
        from confluence_publisher.models import ConfluenceTreeNode

        tree = ConfluenceTreeNode(page_id=request.root_page_id, title="Root")
        return CloneResult(
            output=request.output,
            page_count=1,
            file_plan=build_clone_file_plan(tree),
            written=not request.dry_run,
        )

    client = FakeClient()
    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))
    monkeypatch.setattr(cli, "clone_confluence_tree", fake_clone_confluence_tree)

    result = await cli.run_async(
        [
            "clone",
            "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/"
            "pages/5164564861/Custom+UI+NEXT+Migration",
            str(tmp_path / "docs"),
            "--dry-run",
        ]
    )

    assert result == 0
    assert calls == [True]
    assert "Confluence clone dry run" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_run_async_clone_reports_clone_errors_after_mcp_shutdown(
    tmp_path,
    monkeypatch,
):
    from confluence_publisher.clone import CloneError

    async def fake_clone_confluence_tree(request, *, client):
        raise CloneError("Unsupported Confluence content: jira")

    client = FakeClient()
    monkeypatch.setattr(cli, "connect_mcp_atlassian", wrapping_connect_for(client))
    monkeypatch.setattr(cli, "clone_confluence_tree", fake_clone_confluence_tree)

    with pytest.raises(SystemExit, match="Unsupported Confluence content: jira"):
        await cli.run_async(
            [
                "clone",
                "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/"
                "pages/5164564861/Custom+UI+NEXT+Migration",
                str(tmp_path / "docs"),
            ]
        )


class FakeClient:
    def __init__(self, title: str = "Fetched Title") -> None:
        self.title = title
        self.calls = []
        self.created = 0

    async def get_page(self, page_id: str) -> PageInfo:
        self.calls.append(("get_page", page_id))
        return PageInfo(page_id=page_id, title=self.title)

    async def get_page_storage(self, page_id: str) -> PageStorage:
        self.calls.append(("get_page_storage", page_id))
        return PageStorage(
            page_id=page_id,
            title=self.title,
            storage="<h1>Home</h1><p>Remote text.</p>",
            version=3,
        )

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

    async def search_pages_by_title(self, *, space_key: str, title: str):
        self.calls.append(("search_pages_by_title", space_key, title))
        return []

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


class WrappingConnect:
    def __init__(self, client: FakeClient) -> None:
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        if exc is not None:
            raise ExceptionGroup("wrapped MCP shutdown", [exc])
        return False


def wrapping_connect_for(client: FakeClient):
    def fake_connect():
        return WrappingConnect(client)

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
        ("get_page", "999999"),
        ("search_pages_by_title", "~6393203da593cb822e995262", "Home"),
        ("search_pages_by_title", "~6393203da593cb822e995262", "Guide"),
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
async def test_run_async_project_create_missing_pages_uses_explicit_flat_root(
    tmp_path, monkeypatch
):
    (tmp_path / "00-overview.md").write_text("# Overview\n", encoding="utf-8")
    (tmp_path / "01-prerequisites.md").write_text("# Prerequisites\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
root:
  source_path: "00-overview.md"
  parent_page_id: "5151786651"
pages:
  00-overview.md:
    title: "Overview"
  01-prerequisites.md:
    title: "Prerequisites"
""".strip(),
        encoding="utf-8",
    )
    client = FakeClient()

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))

    result = await cli.run_async(["--project", str(tmp_path), "--create-missing-pages", "--yes"])

    assert result == 0
    assert client.calls == [
        ("get_page", "5151786651"),
        ("search_pages_by_title", "~6393203da593cb822e995262", "Overview"),
        ("search_pages_by_title", "~6393203da593cb822e995262", "Prerequisites"),
        ("create_page", "~6393203da593cb822e995262", "Overview", "5151786651", True, "700001"),
        ("create_page", "~6393203da593cb822e995262", "Prerequisites", "700001", True, "700002"),
        (
            "update_page_storage",
            "700001",
            "Overview",
            False,
            False,
            False,
            "Sync documentation from Markdown",
        ),
        (
            "update_page_storage",
            "700002",
            "Prerequisites",
            False,
            False,
            False,
            "Sync documentation from Markdown",
        ),
    ]
    updated_manifest = load_manifest(manifest_path)
    assert updated_manifest.root_page_id == "700001"
    assert updated_manifest.pages["00-overview.md"].page_id == "700001"
    assert updated_manifest.pages["01-prerequisites.md"].page_id == "700002"


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
async def test_run_async_project_plan_only_shows_effective_sync_settings(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
sync:
  enabled: true
  recreate_missing_root: true
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

    result = await cli.run_async(
        ["--project", str(tmp_path), "--plan-only", "--recreate-stale-pages"]
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "Sync: enabled" in output
    assert "Recreate missing root: yes" in output
    assert "Recreate stale pages: yes" in output


@pytest.mark.asyncio
async def test_run_async_project_plan_only_recreate_flag_enables_sync(
    tmp_path, monkeypatch, capsys
):
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

    result = await cli.run_async(
        ["--project", str(tmp_path), "--plan-only", "--recreate-missing-root"]
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "Sync: enabled" in output
    assert "Recreate missing root: yes" in output


@pytest.mark.asyncio
async def test_run_async_project_check_sync_reads_remote_storage(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "index.md").write_text("# Home\n\nRemote text.\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
pages:
  index.md:
    page_id: "111111"
    title: "Home"
""".strip(),
        encoding="utf-8",
    )
    client = FakeClient()
    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))

    result = await cli.run_async(["--project", str(tmp_path), "--check-sync"])

    assert result == 0
    assert client.calls == [("get_page_storage", "111111")]
    output = capsys.readouterr().out
    assert "Confluence bidirectional sync check" in output
    assert "index.md: in_sync" in output


@pytest.mark.asyncio
async def test_run_async_project_pull_dry_run_reads_remote_but_does_not_write(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "index.md").write_text("# Home\n\nBase text.\n", encoding="utf-8")
    baseline = "sha256:d8f52ec520ffada82e3a0218191af05b65c407a0f8e1e3f691ec9e829ed1d780"
    (tmp_path / ".confluence-publish.yml").write_text(
        f"""
pages:
  index.md:
    page_id: "111111"
    title: "Home"
    canonical_hash: "{baseline}"
""".strip(),
        encoding="utf-8",
    )
    client = FakeClient()
    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))

    result = await cli.run_async(["--project", str(tmp_path), "--pull", "--dry-run"])

    assert result == 0
    assert client.calls == [("get_page_storage", "111111")]
    assert (tmp_path / "index.md").read_text(encoding="utf-8") == "# Home\n\nBase text.\n"
    output = capsys.readouterr().out
    assert "Remote pages to pull: index.md" in output


@pytest.mark.asyncio
async def test_run_async_project_pull_reports_unsafe_error_after_mcp_shutdown(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "index.md").write_text("# Home\n\nBase text.\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
pages:
  index.md:
    page_id: "111111"
    title: "Home"
    canonical_hash: "sha256:placeholder"
""".strip(),
        encoding="utf-8",
    )

    async def fake_pull_remote_changes(plan, *, client, dry_run):
        raise RuntimeError("Remote changes are unsafe to pull: index.md (conflict)")

    import confluence_publisher.bidirectional as bidirectional

    client = FakeClient()
    monkeypatch.setattr(cli, "connect_mcp_atlassian", wrapping_connect_for(client))
    monkeypatch.setattr(bidirectional, "pull_remote_changes", fake_pull_remote_changes)

    with pytest.raises(SystemExit, match="unsafe to pull"):
        await cli.run_async(["--project", str(tmp_path), "--pull", "--dry-run"])


@pytest.mark.asyncio
async def test_run_async_project_live_sync_missing_page_id_routes_to_sync(
    tmp_path, monkeypatch
):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
pages:
  index.md:
    title: "Home"
""".strip(),
        encoding="utf-8",
    )
    calls = []

    async def fake_sync_project(plan, *, client, dry_run, mmdc_bin):
        calls.append(("sync_project", plan.sync_enabled, dry_run, mmdc_bin))
        return 0

    async def fail_publish_project(*_args, **_kwargs):
        raise AssertionError("sync plans must not use legacy publish_project")

    from confluence_publisher import batch, sync

    client = FakeClient()
    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))
    monkeypatch.setattr(sync, "sync_project", fake_sync_project)
    monkeypatch.setattr(batch, "publish_project", fail_publish_project)

    result = await cli.run_async(["--project", str(tmp_path), "--sync", "--yes"])

    assert result == 0
    assert calls == [("sync_project", True, False, "mmdc")]


@pytest.mark.asyncio
async def test_run_async_project_live_manifest_sync_missing_page_id_routes_to_sync(
    tmp_path, monkeypatch
):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
sync:
  enabled: true
pages:
  index.md:
    title: "Home"
""".strip(),
        encoding="utf-8",
    )
    calls = []

    async def fake_sync_project(plan, *, client, dry_run, mmdc_bin):
        calls.append(("sync_project", plan.sync_enabled, dry_run, mmdc_bin))
        return 0

    async def fail_publish_project(*_args, **_kwargs):
        raise AssertionError("sync plans must not use legacy publish_project")

    from confluence_publisher import batch, sync

    client = FakeClient()
    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))
    monkeypatch.setattr(sync, "sync_project", fake_sync_project)
    monkeypatch.setattr(batch, "publish_project", fail_publish_project)

    result = await cli.run_async(["--project", str(tmp_path), "--yes"])

    assert result == 0
    assert calls == [("sync_project", True, False, "mmdc")]


@pytest.mark.asyncio
async def test_run_async_project_manifest_sync_enabled_uses_sync_without_flag(
    tmp_path, monkeypatch
):
    (tmp_path / "index.md").write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
sync:
  enabled: true
root:
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
    content_hash: "sha256:old"
""".strip(),
        encoding="utf-8",
    )
    client = FakeClient()
    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))

    result = await cli.run_async(["--project", str(tmp_path), "--yes"])

    assert result == 0
    assert client.calls[0] == ("get_page", "111111")


@pytest.mark.asyncio
async def test_run_async_project_sync_dry_run_uses_sync_without_connecting(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "index.md").write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
space: "~6393203da593cb822e995262"
sync:
  enabled: true
root:
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
""".strip(),
        encoding="utf-8",
    )

    def fail_connect():
        raise AssertionError("sync dry-run must not connect to MCP")

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fail_connect)

    result = await cli.run_async(["--project", str(tmp_path), "--dry-run"])

    assert result == 0
    output = capsys.readouterr().out
    assert "Sync: enabled" in output
    assert "Storage preview for index.md" in output


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
