from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from confluence_publisher import cli
from confluence_publisher.cli import build_arg_parser
from confluence_publisher.models import PageInfo, PageStorage
from confluence_publisher.planner import PlannedPage, PublishPlan


def test_arg_parser_requires_subcommand():
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args([])


@pytest.mark.parametrize(
    "argv",
    [
        ["--source", "page.md", "--page-id", "111111"],
        ["--source", "page.md", "--page-id", "111111", "--dry-run"],
        ["--project", "docs", "--plan-only"],
        ["--project", "docs", "--check-sync"],
        ["--project", "docs", "--pull"],
        ["--project", "docs", "--sync", "--yes"],
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
async def test_run_async_plan_does_not_connect_to_mcp(tmp_path, monkeypatch, capsys):
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
        raise AssertionError("plan must not connect to MCP")

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fail_connect)

    result = await cli.run_async(["plan", str(tmp_path)])

    assert result == 0
    output = capsys.readouterr().out
    assert "Confluence project publish plan" in output
    assert "Sync: enabled" in output
    assert "index.md -> 111111" in output


@pytest.mark.asyncio
async def test_run_async_plan_uses_manifest_recovery_flags(tmp_path, monkeypatch, capsys):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
sync:
  recreate_missing_root: true
  recreate_stale_pages: true
pages:
  index.md:
    page_id: "111111"
    title: "Home"
""".strip(),
        encoding="utf-8",
    )

    def fail_connect():
        raise AssertionError("plan must not connect to MCP")

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fail_connect)

    result = await cli.run_async(["plan", str(tmp_path)])

    assert result == 0
    output = capsys.readouterr().out
    assert "Recreate missing root: yes" in output
    assert "Recreate stale pages: yes" in output


@pytest.mark.asyncio
async def test_run_async_plan_rejects_missing_page_id_without_create_missing_pages(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
space: "~6393203da593cb822e995262"
root:
  parent_page_id: "999999"
pages:
  index.md:
    title: "Home"
""".strip(),
        encoding="utf-8",
    )

    def fail_connect():
        raise AssertionError("planning error must not connect to MCP")

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fail_connect)

    with pytest.raises(SystemExit, match="missing page_id"):
        await cli.run_async(["plan", str(tmp_path)])


@pytest.mark.asyncio
async def test_run_async_push_dry_run_renders_without_connecting(tmp_path, monkeypatch, capsys):
    (tmp_path / "index.md").write_text("# Home\n\nPlain text.\n", encoding="utf-8")
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
        raise AssertionError("push dry-run must not connect to MCP")

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fail_connect)

    result = await cli.run_async(["push", str(tmp_path), "--dry-run"])

    assert result == 0
    output = capsys.readouterr().out
    assert "Confluence project publish plan" in output
    assert "Storage preview for index.md:" in output
    assert "<h1>Home</h1>" in output


@pytest.mark.asyncio
async def test_run_async_push_dry_run_uses_manifest_recovery_flags(tmp_path, monkeypatch):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
sync:
  recreate_missing_root: true
  recreate_stale_pages: true
pages:
  index.md:
    page_id: "111111"
    title: "Home"
""".strip(),
        encoding="utf-8",
    )
    calls = []

    async def fake_sync_project(plan, *, client, dry_run, mmdc_bin):
        calls.append((plan.recreate_missing_root, plan.recreate_stale_pages))
        return 0

    monkeypatch.setattr(cli.sync, "sync_project", fake_sync_project)

    result = await cli.run_async(["push", str(tmp_path), "--dry-run"])

    assert result == 0
    assert calls == [(True, True)]


@pytest.mark.asyncio
async def test_run_async_push_live_routes_to_sync_project(tmp_path, monkeypatch, capsys):
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
    client = FakeClient()
    calls = []

    async def fake_sync_project(plan, *, client, dry_run, mmdc_bin):
        calls.append((plan.project_root, client, dry_run, mmdc_bin))
        return 0

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))
    monkeypatch.setattr(cli.sync, "sync_project", fake_sync_project)

    result = await cli.run_async(["push", str(tmp_path), "--yes", "--mmdc-bin", "custom-mmdc"])

    assert result == 0
    assert calls == [(tmp_path, client, False, "custom-mmdc")]
    assert "Confluence project publish plan" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_run_async_push_passes_recovery_flags_to_plan(tmp_path, monkeypatch):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=tmp_path / ".confluence-publish.yml",
        version_comment="Sync documentation from Markdown",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(
            PlannedPage(
                "index.md",
                tmp_path / "index.md",
                "111111",
                "Home",
                None,
                None,
            ),
        ),
        sync_enabled=True,
    )
    calls = []

    def fake_build_project_plan(
        args,
        *,
        create_missing_pages=False,
        recreate_missing_root=False,
        recreate_stale_pages=False,
    ):
        calls.append(
            (
                create_missing_pages,
                recreate_missing_root,
                recreate_stale_pages,
            )
        )
        return plan

    async def fake_sync_project(plan, *, client, dry_run, mmdc_bin):
        return 0

    monkeypatch.setattr(cli, "_build_project_plan", fake_build_project_plan)
    monkeypatch.setattr(cli.sync, "sync_project", fake_sync_project)

    result = await cli.run_async(
        [
            "push",
            str(tmp_path),
            "--dry-run",
            "--create-missing-pages",
            "--recreate-missing-root",
            "--recreate-stale-pages",
        ]
    )

    assert result == 0
    assert calls == [(True, True, True)]


@pytest.mark.asyncio
async def test_run_async_status_reads_remote_storage(
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

    result = await cli.run_async(["status", str(tmp_path)])

    assert result == 0
    assert client.calls == [("get_page_storage", "111111")]
    output = capsys.readouterr().out
    assert "Confluence bidirectional sync check" in output
    assert "index.md: in_sync" in output


@pytest.mark.asyncio
async def test_run_async_status_returns_nonzero_for_divergence(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "index.md").write_text("# Home\n\nLocal text.\n", encoding="utf-8")
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

    result = await cli.run_async(["status", str(tmp_path)])

    assert result == 1
    output = capsys.readouterr().out
    assert "unknown_divergence" in output


@pytest.mark.asyncio
async def test_run_async_pull_dry_run_reads_remote_but_does_not_write(
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

    result = await cli.run_async(["pull", str(tmp_path), "--dry-run"])

    assert result == 0
    assert client.calls == [("get_page_storage", "111111")]
    assert (tmp_path / "index.md").read_text(encoding="utf-8") == "# Home\n\nBase text.\n"
    output = capsys.readouterr().out
    assert "Remote pages to pull: index.md" in output


@pytest.mark.asyncio
async def test_run_async_pull_reports_unsafe_error_after_mcp_shutdown(
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
        await cli.run_async(["pull", str(tmp_path), "--dry-run"])
