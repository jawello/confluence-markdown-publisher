# Subcommand-Only CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mixed legacy CLI with a subcommand-only, manifest-backed workflow: `clone`, `plan`, `status`, `pull`, and `push`.

**Architecture:** Keep project planning, bidirectional checks, and sync publishing as the core behavior, but move command selection to required subcommands. Extract page rendering/preparation out of `cli.py` so sync/push code does not import private CLI helpers. Delete dead single-page and non-sync project publishing internals after call-site verification.

**Tech Stack:** Python 3.11+, argparse, pytest, pytest-asyncio, uv, confluence-markdown-publisher modules under `src/confluence_publisher`.

---

## Workflow Requirements

- Use `superpowers:test-driven-development` for every behavior change: write failing tests first, run them and verify the expected failure, then implement minimal code.
- Use `superpowers:subagent-driven-development` when executing this plan. Dispatch a fresh worker per task, then run spec-compliance and code-quality review before marking each task complete.
- Use `superpowers:requesting-code-review` after implementation tasks and for the final review.
- Commit after each task with a short English message. Do not mention AI tools in commits.

## File Map

- Modify `src/confluence_publisher/cli.py`: replace top-level mode matrix with subcommand parser and command handlers.
- Create `src/confluence_publisher/page.py`: own reusable Markdown-to-storage preparation and optional attachment upload primitive currently hidden in `cli.py`.
- Modify `src/confluence_publisher/sync.py`: import page preparation from `page.py`; relax space requirements only where regular existing-page sync does not need a space.
- Modify `src/confluence_publisher/batch.py`: keep `format_publish_plan`; remove dead `publish_project` behavior once no callers remain.
- Modify `tests/confluence_publisher/test_cli.py`: rewrite CLI contract tests around subcommands and legacy rejection.
- Create `tests/confluence_publisher/test_page.py`: cover extracted page preparation behavior.
- Modify `tests/confluence_publisher/test_sync.py`: update renderer monkeypatch target after extraction and cover no-space existing-page push if needed.
- Modify `tests/confluence_publisher/test_batch.py`: keep plan-format tests; remove non-sync `publish_project` tests.
- Modify `README.md`, `DESIGN.md`, and `AGENTS.md`: document the subcommand-only contract.
- Modify `/home/akhadiev/.codex/skills/confluence-docs-publishing/SKILL.md` and references if sandbox approval allows writing outside the repo.

## Task 1: Parser Contract And Clone Positional Arguments

**Files:**
- Modify: `tests/confluence_publisher/test_cli.py`
- Modify: `src/confluence_publisher/cli.py`

- [ ] **Step 1: Replace parser tests with subcommand contract tests**

In `tests/confluence_publisher/test_cli.py`, replace the old parser tests at the top of the file with tests shaped like this:

```python
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
```

- [ ] **Step 2: Run parser tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_cli.py::test_arg_parser_requires_subcommand tests/confluence_publisher/test_cli.py::test_arg_parser_rejects_legacy_top_level_forms tests/confluence_publisher/test_cli.py::test_arg_parser_accepts_clone_url_positional_form tests/confluence_publisher/test_cli.py::test_arg_parser_accepts_clone_page_id_positional_form tests/confluence_publisher/test_cli.py::test_arg_parser_accepts_project_subcommands tests/confluence_publisher/test_cli.py::test_arg_parser_accepts_plan_and_push_recovery_flags tests/confluence_publisher/test_cli.py::test_arg_parser_rejects_recovery_flags_outside_plan_and_push -v
```

Expected: FAIL because the parser still accepts legacy top-level forms and `clone` still expects `--url` / `--output`.

- [ ] **Step 3: Implement required subcommands in `build_arg_parser()`**

In `src/confluence_publisher/cli.py`, change `build_arg_parser()` to use required subcommands. Keep `_positive_int` and `_StoreExplicit`.

Use this structure:

```python
def _add_manifest_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path)


def _add_create_recovery_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--create-missing-pages", action="store_true")
    parser.add_argument("--recreate-missing-root", action="store_true")
    parser.add_argument("--recreate-stale-pages", action="store_true")


def _add_render_arguments(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(output_format_explicit=False, diagram_width_explicit=False)
    parser.add_argument(
        "--output-format",
        choices=("svg", "png"),
        default="svg",
        action=_StoreExplicit,
    )
    parser.add_argument(
        "--diagram-width",
        type=_positive_int,
        default=1000,
        action=_StoreExplicit,
    )
    parser.add_argument("--mmdc-bin", default="mmdc")
    parser.add_argument("--allow-diagram-fallback", action="store_true")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="publish-confluence",
        description="Manage manifest-backed Markdown projects for Confluence.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    clone_parser = subparsers.add_parser(
        "clone",
        help="Clone a Confluence page tree into a local Markdown project.",
    )
    clone_parser.add_argument("clone_source")
    clone_parser.add_argument("clone_output", type=Path)
    clone_parser.add_argument("--space", dest="clone_space")
    clone_parser.add_argument("--max-depth", dest="clone_max_depth", type=int)
    clone_parser.add_argument("--max-pages", dest="clone_max_pages", type=int, default=1000)
    clone_parser.add_argument("--dry-run", action="store_true")

    plan_parser = subparsers.add_parser("plan", help="Validate and print a local project plan.")
    plan_parser.add_argument("project", type=Path)
    _add_manifest_argument(plan_parser)
    _add_create_recovery_arguments(plan_parser)

    status_parser = subparsers.add_parser("status", help="Check local and remote sync state.")
    status_parser.add_argument("project", type=Path)
    _add_manifest_argument(status_parser)

    pull_parser = subparsers.add_parser("pull", help="Pull safe remote-only changes into Markdown.")
    pull_parser.add_argument("project", type=Path)
    _add_manifest_argument(pull_parser)
    pull_parser.add_argument("--dry-run", action="store_true")
    pull_parser.add_argument("--yes", action="store_true")

    push_parser = subparsers.add_parser("push", help="Push local Markdown changes to Confluence.")
    push_parser.add_argument("project", type=Path)
    _add_manifest_argument(push_parser)
    _add_create_recovery_arguments(push_parser)
    push_parser.add_argument("--dry-run", action="store_true")
    push_parser.add_argument("--yes", action="store_true")
    push_parser.add_argument("--comment")
    _add_render_arguments(push_parser)

    return parser
```

Remove top-level `--source`, `--page-id`, `--project`, `--sync`, `--check-sync`, `--pull`, and the root-level `--dry-run` option.

- [ ] **Step 4: Update clone dispatch input mapping**

In `run_async()`, keep clone dispatch temporarily but convert positional `clone_source` to existing `parse_clone_request()` parameters:

```python
if args.command == "clone":
    source = args.clone_source
    is_url = "://" in source or "/pages/" in source
    request = parse_clone_request(
        url=source if is_url else None,
        page_id=None if is_url else source,
        space=args.clone_space,
        output=args.clone_output,
        max_depth=args.clone_max_depth,
        max_pages=args.clone_max_pages,
        dry_run=args.dry_run,
    )
```

Leave old project and single-page runtime branches in place for now if needed to keep unrelated tests passing; they will be removed in later tasks. Remove `validate_args()` or make it a no-op only if no current code still calls it.

- [ ] **Step 5: Run parser tests and verify they pass**

Run the same command from Step 2.

Expected: PASS.

- [ ] **Step 6: Commit parser contract**

```bash
git add src/confluence_publisher/cli.py tests/confluence_publisher/test_cli.py
git commit -m "Add subcommand parser contract"
```

## Task 2: Extract Page Preparation Out Of CLI

**Files:**
- Create: `src/confluence_publisher/page.py`
- Create: `tests/confluence_publisher/test_page.py`
- Modify: `src/confluence_publisher/sync.py`
- Modify: `tests/confluence_publisher/test_sync.py`
- Modify: `src/confluence_publisher/cli.py`

- [ ] **Step 1: Write failing tests for the extracted page module**

Create `tests/confluence_publisher/test_page.py`:

```python
from pathlib import Path

import pytest

from confluence_publisher.models import RenderedDiagram


def test_prepare_page_converts_markdown_to_storage(tmp_path):
    from confluence_publisher.page import prepare_page

    source = tmp_path / "index.md"
    source.write_text("# Home\n\nPlain text.\n", encoding="utf-8")

    with prepare_page(
        source=source,
        output_format="svg",
        diagram_width=1000,
        mmdc_bin="mmdc",
        allow_diagram_fallback=False,
    ) as prepared:
        assert "<h1>Home</h1>" in prepared.storage
        assert prepared.rendered_diagrams == []


def test_prepare_page_renders_mermaid_with_width_directive(tmp_path, monkeypatch):
    import confluence_publisher.page as page
    from confluence_publisher.page import prepare_page

    class FakeRenderer:
        def __init__(self, _mmdc_bin: str) -> None:
            pass

        def render(self, block, temp_dir: Path) -> RenderedDiagram:
            output_path = temp_dir / block.attachment_name
            output_path.write_text("<svg></svg>", encoding="utf-8")
            return RenderedDiagram(
                block=block,
                mmd_path=temp_dir / "diagram.mmd",
                output_path=output_path,
            )

    source = tmp_path / "index.md"
    source.write_text(
        "# Home\n\n```mermaid\n%% confluence: width=1200 %%\ngraph LR\nA --> B\n```\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(page, "MermaidRenderer", FakeRenderer)

    with prepare_page(
        source=source,
        output_format="svg",
        diagram_width=1000,
        mmdc_bin="mmdc",
        allow_diagram_fallback=False,
    ) as prepared:
        assert len(prepared.rendered_diagrams) == 1
        assert 'ac:width="1200"' in prepared.storage


def test_prepare_page_fallback_outputs_no_rendered_attachment(tmp_path, monkeypatch):
    import confluence_publisher.page as page
    from confluence_publisher.page import prepare_page
    from confluence_publisher.renderer import RenderError

    class FailingRenderer:
        def __init__(self, _mmdc_bin: str) -> None:
            pass

        def render(self, block, temp_dir: Path):
            raise RenderError("mermaid failed")

    source = tmp_path / "index.md"
    source.write_text("# Home\n\n```mermaid\ngraph LR\nA --> B\n```\n", encoding="utf-8")
    monkeypatch.setattr(page, "MermaidRenderer", FailingRenderer)

    with prepare_page(
        source=source,
        output_format="svg",
        diagram_width=1000,
        mmdc_bin="mmdc",
        allow_diagram_fallback=True,
    ) as prepared:
        assert prepared.rendered_diagrams == []
        assert "Mermaid source" in prepared.storage
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_page.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'confluence_publisher.page'`.

- [ ] **Step 3: Create `src/confluence_publisher/page.py`**

Move the page preparation dataclass and context manager from `cli.py` into the new module. The new module should look like this:

```python
from __future__ import annotations

import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .converter import collect_mermaid_blocks, convert_markdown_to_storage
from .models import RenderedDiagram
from .renderer import MermaidRenderer, RenderError
from .storage import diagram_fragment, fallback_mermaid_fragment


@dataclass(frozen=True)
class PreparedPage:
    storage: str
    rendered_diagrams: list[RenderedDiagram]


@contextmanager
def prepare_page(
    *,
    source: Path,
    output_format: str,
    diagram_width: int,
    mmdc_bin: str,
    allow_diagram_fallback: bool,
) -> Iterator[PreparedPage]:
    markdown_text = source.read_text(encoding="utf-8")
    page_slug = source.stem
    blocks = collect_mermaid_blocks(markdown_text, page_slug, output_format)
    diagram_fragments: dict[int, str] = {}
    rendered_diagrams: list[RenderedDiagram] = []

    with tempfile.TemporaryDirectory(prefix="confluence-mermaid-") as temp_path:
        temp_dir = Path(temp_path)
        renderer = MermaidRenderer(mmdc_bin)
        for block in blocks:
            try:
                rendered = renderer.render(block, temp_dir)
            except RenderError as exc:
                if not allow_diagram_fallback:
                    raise SystemExit(str(exc)) from exc
                diagram_fragments[block.ordinal] = fallback_mermaid_fragment(block.source)
                continue

            rendered_diagrams.append(rendered)
            diagram_fragments[block.ordinal] = diagram_fragment(
                block,
                width=block.display_width or diagram_width,
            )

        storage = convert_markdown_to_storage(
            markdown_text,
            page_slug=page_slug,
            output_format=output_format,
            diagram_fragments=diagram_fragments,
            allow_diagram_fallback=allow_diagram_fallback,
        )
        yield PreparedPage(storage=storage, rendered_diagrams=rendered_diagrams)


async def publish_prepared_page(
    *,
    page_id: str,
    title: str,
    storage: str,
    rendered_diagrams: Sequence[RenderedDiagram],
    client,
    version_comment: str,
) -> None:
    for rendered in rendered_diagrams:
        await client.upload_attachment(page_id, rendered.output_path)
    await client.update_page_storage(page_id, title, storage, version_comment=version_comment)
```

- [ ] **Step 4: Update imports to use `page.prepare_page`**

In `src/confluence_publisher/sync.py`, change:

```python
from .cli import _prepare_single_page
```

to:

```python
from .page import prepare_page
```

and change the call:

```python
with _prepare_single_page(
```

to:

```python
with prepare_page(
```

In `tests/confluence_publisher/test_sync.py`, update monkeypatch targets from `cli.MermaidRenderer` to `confluence_publisher.page.MermaidRenderer`:

```python
import confluence_publisher.page as page

monkeypatch.setattr(page, "MermaidRenderer", FakeRenderer)
```

In `src/confluence_publisher/cli.py`, import `prepare_page` and `publish_prepared_page` from `.page` only for any remaining temporary single-page branch. Later tasks delete that branch.

- [ ] **Step 5: Run page and sync preparation tests**

Run:

```bash
uv run pytest tests/confluence_publisher/test_page.py tests/confluence_publisher/test_sync.py::test_prepare_sync_pages_returns_rendered_storage_and_hash tests/confluence_publisher/test_sync.py::test_prepare_sync_pages_keeps_rendered_diagram_files_after_return tests/confluence_publisher/test_sync.py::test_prepare_sync_pages_handles_long_deep_source_paths -v
```

Expected: PASS.

- [ ] **Step 6: Commit page extraction**

```bash
git add src/confluence_publisher/page.py src/confluence_publisher/sync.py src/confluence_publisher/cli.py tests/confluence_publisher/test_page.py tests/confluence_publisher/test_sync.py
git commit -m "Move page preparation out of CLI"
```

## Task 3: Project Helper, `plan`, `status`, And `pull`

**Files:**
- Modify: `tests/confluence_publisher/test_cli.py`
- Modify: `src/confluence_publisher/cli.py`

- [ ] **Step 1: Write failing command tests for `plan`, `status`, and `pull`**

In `tests/confluence_publisher/test_cli.py`, rewrite old project-mode tests into subcommand tests. Use these exact behaviors:

```python
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
async def test_run_async_status_reads_remote_storage(tmp_path, monkeypatch, capsys):
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
async def test_run_async_status_returns_nonzero_for_divergence(tmp_path, monkeypatch, capsys):
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
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_cli.py::test_run_async_plan_does_not_connect_to_mcp tests/confluence_publisher/test_cli.py::test_run_async_status_reads_remote_storage tests/confluence_publisher/test_cli.py::test_run_async_status_returns_nonzero_for_divergence tests/confluence_publisher/test_cli.py::test_run_async_pull_dry_run_reads_remote_but_does_not_write -v
```

Expected: FAIL because `plan`, `status`, and `pull` are parsed but not dispatched.

- [ ] **Step 3: Add project plan helper and command handlers**

In `src/confluence_publisher/cli.py`, import modules rather than individual functions for monkeypatch-friendly tests:

```python
from . import bidirectional, sync
from .batch import format_publish_plan
```

Add helper:

```python
def _manifest_path_for(project: Path, manifest: Path | None) -> Path:
    return manifest or project / ".confluence-publish.yml"


def _build_project_plan(
    args: argparse.Namespace,
    *,
    create_missing_pages: bool = False,
    recreate_missing_root: bool = False,
    recreate_stale_pages: bool = False,
) -> object:
    manifest_path = _manifest_path_for(args.project, args.manifest)
    manifest = load_manifest(manifest_path, allow_missing_page_ids=True)
    discovery = discover_markdown_files(
        args.project,
        include=manifest.include,
        exclude=manifest.exclude,
    )
    return build_publish_plan(
        project_root=args.project,
        manifest=manifest,
        discovered_paths=discovery.included,
        excluded_count=discovery.excluded_count,
        version_comment=getattr(args, "comment", None),
        output_format=args.output_format if getattr(args, "output_format_explicit", False) else None,
        diagram_width=args.diagram_width if getattr(args, "diagram_width_explicit", False) else None,
        allow_diagram_fallback=True if getattr(args, "allow_diagram_fallback", False) else None,
        create_missing_pages=create_missing_pages,
        sync_enabled=True,
        recreate_missing_root=recreate_missing_root,
        recreate_stale_pages=recreate_stale_pages,
    )
```

Catch `ManifestError`, `PlannerError`, `OSError`, and `ValueError` in command handlers and re-raise `SystemExit(str(exc))`.

Add handlers:

```python
async def run_plan_command(args: argparse.Namespace) -> int:
    try:
        plan = _build_project_plan(
            args,
            create_missing_pages=args.create_missing_pages,
            recreate_missing_root=args.recreate_missing_root,
            recreate_stale_pages=args.recreate_stale_pages,
        )
    except (ManifestError, PlannerError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(format_publish_plan(plan))
    return 0


async def run_status_command(args: argparse.Namespace) -> int:
    try:
        plan = _build_project_plan(args)
    except (ManifestError, PlannerError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(format_publish_plan(plan))
    async with connect_mcp_atlassian() as client:
        report = await bidirectional.check_project_invariants(plan, client=client)
    print(bidirectional.format_invariant_report(report))
    return 1 if report.has_conflicts else 0


async def run_pull_command(args: argparse.Namespace) -> int:
    try:
        plan = _build_project_plan(args)
    except (ManifestError, PlannerError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(format_publish_plan(plan))
    if not args.dry_run and not args.yes:
        answer = input(
            "Pull remote Confluence changes into local Markdown? "
            "Type 'yes' to continue: "
        )
        if answer != "yes":
            print("Aborted.")
            return 1
    sync_error: RuntimeError | None = None
    async with connect_mcp_atlassian() as client:
        try:
            result = await bidirectional.pull_remote_changes(
                plan,
                client=client,
                dry_run=args.dry_run,
            )
        except RuntimeError as exc:
            sync_error = exc
    if sync_error is not None:
        raise SystemExit(str(sync_error)) from sync_error
    print(bidirectional.format_invariant_report(result.report))
    pulled = ", ".join(result.updated_paths) if result.updated_paths else "none"
    print(f"Remote pages to pull: {pulled}")
    return 0
```

Dispatch in `run_async()`:

```python
if args.command == "plan":
    return await run_plan_command(args)
if args.command == "status":
    return await run_status_command(args)
if args.command == "pull":
    return await run_pull_command(args)
```

- [ ] **Step 4: Run command tests and verify they pass**

Run the same command from Step 2.

Expected: PASS.

- [ ] **Step 5: Commit plan/status/pull commands**

```bash
git add src/confluence_publisher/cli.py tests/confluence_publisher/test_cli.py
git commit -m "Add project read commands"
```

## Task 4: `push` Command And Sync-Only Publishing

**Files:**
- Modify: `tests/confluence_publisher/test_cli.py`
- Modify: `tests/confluence_publisher/test_sync.py`
- Modify: `src/confluence_publisher/cli.py`
- Modify: `src/confluence_publisher/sync.py`

- [ ] **Step 1: Write failing push command tests**

In `tests/confluence_publisher/test_cli.py`, add or rewrite tests:

```python
@pytest.mark.asyncio
async def test_run_async_push_dry_run_renders_without_connecting(
    tmp_path, monkeypatch, capsys
):
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
    assert "Sync: enabled" in output
    assert "Storage preview for index.md" in output


@pytest.mark.asyncio
async def test_run_async_push_live_routes_to_sync_project(tmp_path, monkeypatch):
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

    client = FakeClient()
    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))
    monkeypatch.setattr("confluence_publisher.sync.sync_project", fake_sync_project)

    result = await cli.run_async(["push", str(tmp_path), "--yes"])

    assert result == 0
    assert calls == [("sync_project", True, False, "mmdc")]


@pytest.mark.asyncio
async def test_run_async_push_passes_recovery_flags_to_plan(tmp_path, monkeypatch):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
space: "~6393203da593cb822e995262"
root:
  source_path: "index.md"
  parent_page_id: "999999"
pages:
  index.md:
    title: "Home"
""".strip(),
        encoding="utf-8",
    )
    calls = []

    async def fake_sync_project(plan, *, client, dry_run, mmdc_bin):
        calls.append(
            (
                plan.create_missing_pages,
                plan.recreate_missing_root,
                plan.recreate_stale_pages,
            )
        )
        return 0

    client = FakeClient()
    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))
    monkeypatch.setattr("confluence_publisher.sync.sync_project", fake_sync_project)

    result = await cli.run_async(
        [
            "push",
            str(tmp_path),
            "--create-missing-pages",
            "--recreate-missing-root",
            "--recreate-stale-pages",
            "--yes",
        ]
    )

    assert result == 0
    assert calls == [(True, True, True)]
```

- [ ] **Step 2: Write failing sync test for existing page updates without `space`**

In `tests/confluence_publisher/test_sync.py`, add:

```python
@pytest.mark.asyncio
async def test_sync_project_updates_existing_pages_without_space(tmp_path):
    from confluence_publisher.sync import sync_project

    source = tmp_path / "index.md"
    source.write_text("# Home\n\nChanged text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
pages:
  index.md:
    page_id: "111111"
    title: "Home"
    content_hash: "sha256:old"
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
                source_path="index.md",
                absolute_path=source,
                page_id="111111",
                title="Home",
                url=None,
                parent_source_path=None,
                content_hash="sha256:old",
            ),
        ),
        sync_enabled=True,
    )
    client = FakeSyncClient()

    result = await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert result == 0
    assert client.calls[0] == ("get_page", "111111")
    assert client.calls[-1] == ("update_page_storage", "111111", "Home", "Sync docs")
```

- [ ] **Step 3: Run push and sync tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_cli.py::test_run_async_push_dry_run_renders_without_connecting tests/confluence_publisher/test_cli.py::test_run_async_push_live_routes_to_sync_project tests/confluence_publisher/test_cli.py::test_run_async_push_passes_recovery_flags_to_plan tests/confluence_publisher/test_sync.py::test_sync_project_updates_existing_pages_without_space -v
```

Expected: FAIL because `push` is not dispatched and `sync_project()` still requires `plan.space` unconditionally.

- [ ] **Step 4: Implement `run_push_command()`**

Add to `src/confluence_publisher/cli.py`:

```python
class _DryRunClient:
    async def upload_attachment(self, page_id, file_path):
        raise AssertionError("dry-run must not upload attachments")

    async def update_page_storage(self, page_id, title, storage, version_comment):
        raise AssertionError("dry-run must not update pages")


async def run_push_command(args: argparse.Namespace) -> int:
    try:
        plan = _build_project_plan(
            args,
            create_missing_pages=args.create_missing_pages,
            recreate_missing_root=args.recreate_missing_root,
            recreate_stale_pages=args.recreate_stale_pages,
        )
    except (ManifestError, PlannerError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(format_publish_plan(plan))
    if args.dry_run:
        return await sync.sync_project(
            plan,
            client=_DryRunClient(),
            dry_run=True,
            mmdc_bin=args.mmdc_bin,
        )
    if not args.yes:
        answer = input("Push project to Confluence? Type 'yes' to continue: ")
        if answer != "yes":
            print("Aborted.")
            return 1
    async with connect_mcp_atlassian() as client:
        return await sync.sync_project(
            plan,
            client=client,
            dry_run=False,
            mmdc_bin=args.mmdc_bin,
        )
```

Dispatch in `run_async()`:

```python
if args.command == "push":
    return await run_push_command(args)
```

- [ ] **Step 5: Relax `sync_project()` space requirement for existing-page updates**

In `src/confluence_publisher/sync.py`, replace:

```python
if plan.space is None:
    raise RuntimeError("Cannot sync project without a Confluence space")
```

with logic that only requires space when creation/recreation can occur:

```python
def _sync_requires_space(plan: PublishPlan) -> bool:
    return (
        any(item.page_id is None for item in plan.items)
        or plan.recreate_missing_root
        or plan.recreate_stale_pages
    )
```

Then in `sync_project()`:

```python
if plan.space is None and _sync_requires_space(plan):
    raise RuntimeError("Cannot create or recreate Confluence pages without a space")
```

Leave `_preflight_titles()` and `_create_page()` space checks in place.

- [ ] **Step 6: Run push and sync tests**

Run the command from Step 3.

Expected: PASS.

- [ ] **Step 7: Commit push command**

```bash
git add src/confluence_publisher/cli.py src/confluence_publisher/sync.py tests/confluence_publisher/test_cli.py tests/confluence_publisher/test_sync.py
git commit -m "Add sync-backed push command"
```

## Task 5: Remove Legacy Single-Page And Non-Sync Publish Paths

**Files:**
- Modify: `src/confluence_publisher/cli.py`
- Modify: `src/confluence_publisher/batch.py`
- Modify: `tests/confluence_publisher/test_cli.py`
- Modify: `tests/confluence_publisher/test_batch.py`

- [ ] **Step 1: Add call-site guard tests for removed public behavior**

In `tests/confluence_publisher/test_cli.py`, keep explicit rejection tests for legacy forms:

```python
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
def test_legacy_public_commands_are_not_supported(argv):
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(argv)
```

Remove or rewrite old single-page tests:

- `test_arg_parser_defaults_to_svg_and_mcp_backend`;
- `test_validate_args_requires_page_id_for_single_page`;
- `test_preview_lists_page_source_and_attachments`;
- `test_run_async_dry_run_does_not_connect_to_mcp`;
- `test_publish_single_page_uses_supplied_version_comment`;
- `test_run_async_live_fetches_missing_title_and_abort_prevents_writes`;
- `test_run_async_live_uploads_attachments_before_storage_update`;
- `test_run_async_uses_mermaid_width_directive_over_cli_default`;
- `test_run_async_render_failure_without_fallback_exits_before_mcp`;
- `test_run_async_fallback_outputs_no_attachments_for_failed_diagram`.

Those behaviors are now covered through `page.py`, `sync.py`, and `push --dry-run` tests.

- [ ] **Step 2: Run legacy guard and affected tests**

Run:

```bash
uv run pytest tests/confluence_publisher/test_cli.py tests/confluence_publisher/test_page.py tests/confluence_publisher/test_sync.py -v
```

Expected before code cleanup: CLI tests may still fail if old runtime branches or imports conflict with new subcommands.

- [ ] **Step 3: Delete dead CLI internals**

In `src/confluence_publisher/cli.py`, remove:

- `DEFAULT_SINGLE_PAGE_VERSION_COMMENT`;
- `_PreparedSinglePage`;
- `build_preview()`;
- `_prepare_single_page()`;
- `_publish_prepared_single_page()`;
- `publish_single_page()`;
- the old `if args.project is not None:` branch;
- the final old single-page branch in `run_async()`;
- imports used only by those removed functions: `tempfile`, `Iterator`, `contextmanager`, `RenderedDiagram`, `collect_mermaid_blocks`, `convert_markdown_to_storage`, `MermaidRenderer`, `RenderError`, `diagram_fragment`, `fallback_mermaid_fragment`.

After cleanup, `run_async()` should only dispatch:

```python
async def run_async(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.command == "clone":
        return await run_clone_command(args)
    if args.command == "plan":
        return await run_plan_command(args)
    if args.command == "status":
        return await run_status_command(args)
    if args.command == "pull":
        return await run_pull_command(args)
    if args.command == "push":
        return await run_push_command(args)
    raise SystemExit(f"Unsupported command: {args.command}")
```

Create `run_clone_command()` from the remaining clone branch:

```python
async def run_clone_command(args: argparse.Namespace) -> int:
    try:
        source = args.clone_source
        is_url = "://" in source or "/pages/" in source
        request = parse_clone_request(
            url=source if is_url else None,
            page_id=None if is_url else source,
            space=args.clone_space,
            output=args.clone_output,
            max_depth=args.clone_max_depth,
            max_pages=args.clone_max_pages,
            dry_run=args.dry_run,
        )
        clone_error: CloneError | None = None
        async with connect_mcp_atlassian() as client:
            try:
                result = await clone_confluence_tree(request, client=client)
            except CloneError as exc:
                clone_error = exc
        if clone_error is not None:
            raise SystemExit(str(clone_error)) from clone_error
        print(format_clone_summary(result))
        return 0
    except CloneError as exc:
        raise SystemExit(str(exc)) from exc
```

- [ ] **Step 4: Remove non-sync `publish_project()`**

In `src/confluence_publisher/batch.py`, keep `format_publish_plan()` and delete:

- `CREATED_PAGE_STORAGE`;
- `_preflight_missing_page_titles()`;
- `_preflight_missing_page_parents()`;
- `publish_project()`;
- imports that become unused: `write_manifest_page_ids`.

In `tests/confluence_publisher/test_batch.py`, keep only formatter tests. Delete tests that call `batch.publish_project()`.

- [ ] **Step 5: Verify no dead references remain**

Run:

```bash
rg -n "publish_single_page|_prepare_single_page|_publish_prepared_single_page|build_preview|publish_project|DEFAULT_SINGLE_PAGE_VERSION_COMMENT|--source|--project|--check-sync|--pull|--sync" src/confluence_publisher tests/confluence_publisher
```

Expected:

- no references to removed Python symbols;
- legacy command strings appear only in rejection tests, not in supported examples.

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run pytest tests/confluence_publisher/test_cli.py tests/confluence_publisher/test_batch.py tests/confluence_publisher/test_page.py tests/confluence_publisher/test_sync.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit legacy removal**

```bash
git add src/confluence_publisher/cli.py src/confluence_publisher/batch.py tests/confluence_publisher/test_cli.py tests/confluence_publisher/test_batch.py
git commit -m "Remove legacy publish command paths"
```

## Task 6: Documentation And Skill Updates

**Files:**
- Modify: `README.md`
- Modify: `DESIGN.md`
- Modify: `AGENTS.md`
- Modify, with approval if sandbox requires it: `/home/akhadiev/.codex/skills/confluence-docs-publishing/SKILL.md`
- Modify, with approval if sandbox requires it: `/home/akhadiev/.codex/skills/confluence-docs-publishing/references/publisher-cli.md`
- Modify, with approval if sandbox requires it: `/home/akhadiev/.codex/skills/confluence-docs-publishing/references/project-workflow.md`

- [ ] **Step 1: Search stale command examples**

Run:

```bash
rg -n "publish-confluence --source|publish-confluence --project|--check-sync|--pull|--sync|--plan-only|--page-id" README.md DESIGN.md AGENTS.md /home/akhadiev/.codex/skills/confluence-docs-publishing
```

Expected before docs update: matches in README, DESIGN, AGENTS, and skill docs.

- [ ] **Step 2: Update `README.md` command examples**

Replace old examples with:

```bash
uv run publish-confluence clone "<confluence-page-url>" docs
uv run publish-confluence plan docs
uv run publish-confluence status docs
uv run publish-confluence pull docs --dry-run
uv run publish-confluence pull docs --yes
uv run publish-confluence push docs --dry-run
uv run publish-confluence push docs --yes
```

For first-time creation or recovery examples, use:

```bash
uv run publish-confluence plan docs --create-missing-pages
uv run publish-confluence push docs --create-missing-pages --yes
uv run publish-confluence plan docs --recreate-missing-root --recreate-stale-pages
uv run publish-confluence push docs --recreate-missing-root --recreate-stale-pages --yes
```

Remove all public single-page publishing documentation.

- [ ] **Step 3: Update `DESIGN.md` architecture and flows**

Replace the old CLI flow descriptions with the subcommand contract:

```text
clone  - read Confluence and write a new local Markdown project
plan   - local manifest/discovery/planning preflight
status - read-only local/remote invariant check
pull   - safe remote-only changes into Markdown
push   - sync-aware Confluence write path
```

State that single-page publishing is no longer public behavior and that page preparation is an internal primitive used by sync-backed project push.

- [ ] **Step 4: Update `AGENTS.md` repository instructions**

Change the project section from:

```text
- single-page publishing with `--source` and `--page-id`;
- project publishing with `--project` and `.confluence-publish.yml`;
```

to:

```text
- project planning with `plan <project>`;
- bidirectional checks with `status <project>`;
- safe remote pulls with `pull <project>`;
- sync-backed publishing with `push <project>`;
- project bootstrapping with `clone <url-or-page-id> <output>`.
```

Update verification smoke command to:

```bash
uv run publish-confluence plan /tmp/confluence-publisher-smoke-docs
```

- [ ] **Step 5: Update `confluence-docs-publishing` skill files**

If writing outside the repo requires approval, request it. Replace stale examples:

```bash
publish-confluence clone --url "<confluence-page-url>" --output <docs-root>
publish-confluence --project <docs-root> --plan-only
publish-confluence --project <docs-root> --check-sync
publish-confluence --project <docs-root> --pull
publish-confluence --project <docs-root> --dry-run
publish-confluence --project <docs-root> --yes
```

with:

```bash
publish-confluence clone "<confluence-page-url>" <docs-root>
publish-confluence plan <docs-root>
publish-confluence status <docs-root>
publish-confluence pull <docs-root> --dry-run
publish-confluence pull <docs-root> --yes
publish-confluence push <docs-root> --dry-run
publish-confluence push <docs-root> --yes
```

Update prose:

- `status` is read-only against Confluence and replaces `--check-sync`.
- `pull` may rewrite local Markdown and manifest; use `pull --dry-run` to preview.
- `push` is the only Confluence-write command.
- `plan` replaces `--plan-only`.
- one-run sync recovery flags belong on `plan` and `push`.

- [ ] **Step 6: Run stale-command search again**

Run:

```bash
rg -n "publish-confluence --source|publish-confluence --project|--check-sync|--pull|--sync|--plan-only|--page-id" README.md DESIGN.md AGENTS.md /home/akhadiev/.codex/skills/confluence-docs-publishing
```

Expected: no matches except historical notes that explicitly say the forms were removed. Prefer no matches in current docs and skills.

- [ ] **Step 7: Commit docs and skill updates**

If skill files outside the repo changed, commit only repo files here and report the external skill file changes separately because they are not in this repository.

```bash
git add README.md DESIGN.md AGENTS.md
git commit -m "Document subcommand-only workflow"
```

## Task 7: Final Verification And Review

**Files:**
- No production files unless verification finds issues.

- [ ] **Step 1: Run full test suite**

Run:

```bash
uv run pytest tests/confluence_publisher -v
```

Expected: PASS.

- [ ] **Step 2: Run coverage command**

Run:

```bash
uv run pytest tests/confluence_publisher --cov=confluence_publisher --cov-report=term-missing
```

Expected: PASS. Coverage is informational; compare the total with the current observed baseline around `91%`.

- [ ] **Step 3: Run whitespace check**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 4: Run project-mode smoke test**

Create a temporary docs tree:

```bash
mkdir -p /tmp/confluence-publisher-smoke-docs
cat > /tmp/confluence-publisher-smoke-docs/index.md <<'EOF'
# Home

Plain text.
EOF
cat > /tmp/confluence-publisher-smoke-docs/.confluence-publish.yml <<'EOF'
pages:
  index.md:
    page_id: "111111"
    title: "Home"
EOF
uv run publish-confluence plan /tmp/confluence-publisher-smoke-docs
```

Expected: PASS and output includes `Confluence project publish plan`, `Sync: enabled`, and `index.md -> 111111`.

- [ ] **Step 5: Run stale legacy search across current code and docs**

Run:

```bash
rg -n "publish-confluence --source|publish-confluence --project|--check-sync|--pull|--sync|--plan-only|publish_single_page|_prepare_single_page|publish_project" src tests README.md DESIGN.md AGENTS.md docs/superpowers/specs/2026-05-06-subcommand-only-cli-design.md docs/superpowers/plans/2026-05-06-subcommand-only-cli.md
```

Expected: matches only in the approved design/plan sections that discuss removed legacy forms, not in current runtime code or current user docs.

- [ ] **Step 6: Request final code review**

Use `superpowers:requesting-code-review`. Determine the base commit before this implementation branch and the current head:

```bash
BASE_SHA=$(git rev-parse 0487d3b)
HEAD_SHA=$(git rev-parse HEAD)
```

Dispatch a reviewer with:

```text
WHAT_WAS_IMPLEMENTED: Subcommand-only CLI migration for confluence-markdown-publisher.
PLAN_OR_REQUIREMENTS: docs/superpowers/specs/2026-05-06-subcommand-only-cli-design.md and docs/superpowers/plans/2026-05-06-subcommand-only-cli.md.
BASE_SHA: <BASE_SHA>
HEAD_SHA: <HEAD_SHA>
DESCRIPTION: Replaced legacy top-level CLI modes with clone/plan/status/pull/push, removed public single-page publishing, moved page preparation out of cli.py, removed non-sync publish path, updated tests/docs/skills.
```

Fix Critical and Important findings before proceeding.

- [ ] **Step 7: Final status**

Run:

```bash
git status --short
```

Expected: only intentional files remain. Report any unrelated untracked files separately.
