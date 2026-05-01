# Confluence Clone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a git-like `publish-confluence clone` command that creates a local Markdown project and `.confluence-publish.yml` from one Confluence root URL or page ID.

**Architecture:** Keep bootstrap behavior in a new `clone.py` module. Extend the MCP wrapper with read-only child-page traversal, then wire a `clone` subcommand into `cli.py` while preserving the existing `--project` and `--source` command forms.

**Tech Stack:** Python 3.11+, argparse, PyYAML, existing MCP client wrapper, existing storage-to-Markdown converter, pytest, uv.

---

## File Structure

- Create `src/confluence_publisher/clone.py`
  - Owns clone request validation, URL parsing, tree-to-path planning, reverse conversion checks, manifest generation, file writes, and clone summary formatting.
- Modify `src/confluence_publisher/models.py`
  - Add `ConfluenceTreeNode`, a small immutable tree node model used by MCP traversal and clone planning.
- Modify `src/confluence_publisher/mcp_atlassian.py`
  - Add `confluence_get_page_children` to `REQUIRED_TOOLS`.
  - Add `get_page_children()` and `get_page_tree()` methods on `McpAtlassianClient`.
- Modify `src/confluence_publisher/cli.py`
  - Add `publish-confluence clone ...` parsing and dispatch.
  - Preserve existing invocation forms exactly.
- Create `tests/confluence_publisher/test_clone.py`
  - Unit tests for URL parsing, path planning, manifest generation, unsupported content failures, output safety, and dry-run behavior.
- Modify `tests/confluence_publisher/test_mcp_atlassian.py`
  - Tests for child page parsing and recursive tree traversal.
- Modify `tests/confluence_publisher/test_cli.py`
  - CLI parser and `run_async()` integration tests for `clone`.
- Modify `README.md`
  - Document `clone`, then `check-sync`, `pull`, and publish workflow.
- Review `docs/superpowers/specs/2026-05-01-confluence-clone-design.md`
  - No implementation edits expected unless the implementation reveals a contract mismatch.

---

### Task 1: Clone Request Parsing And Output Safety

**Files:**
- Create: `src/confluence_publisher/clone.py`
- Test: `tests/confluence_publisher/test_clone.py`

- [ ] **Step 1: Write failing tests for URL parsing and output validation**

Add `tests/confluence_publisher/test_clone.py`:

```python
from pathlib import Path

import pytest

from confluence_publisher.clone import (
    CloneError,
    CloneRequest,
    assert_clone_output_safe,
    parse_clone_request,
    parse_confluence_page_url,
)


def test_parse_confluence_page_url_extracts_page_id_space_and_url():
    parsed = parse_confluence_page_url(
        "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/"
        "pages/5164564861/Custom+UI+NEXT+Migration"
    )

    assert parsed.page_id == "5164564861"
    assert parsed.space == "~6393203da593cb822e995262"
    assert parsed.url == (
        "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/"
        "pages/5164564861/Custom+UI+NEXT+Migration"
    )


def test_parse_clone_request_accepts_page_id_with_space(tmp_path):
    request = parse_clone_request(
        url=None,
        page_id="5164564861",
        space="~6393203da593cb822e995262",
        output=tmp_path / "docs",
        max_depth=None,
        max_pages=1000,
        dry_run=False,
    )

    assert request == CloneRequest(
        root_page_id="5164564861",
        space="~6393203da593cb822e995262",
        root_url=None,
        output=tmp_path / "docs",
        max_depth=None,
        max_pages=1000,
        dry_run=False,
    )


def test_parse_clone_request_requires_url_or_page_id(tmp_path):
    with pytest.raises(CloneError, match="Either --url or --page-id is required"):
        parse_clone_request(
            url=None,
            page_id=None,
            space=None,
            output=tmp_path / "docs",
            max_depth=None,
            max_pages=1000,
            dry_run=False,
        )


def test_parse_clone_request_requires_space_with_page_id(tmp_path):
    with pytest.raises(CloneError, match="--space is required with --page-id"):
        parse_clone_request(
            url=None,
            page_id="5164564861",
            space=None,
            output=tmp_path / "docs",
            max_depth=None,
            max_pages=1000,
            dry_run=False,
        )


def test_assert_clone_output_safe_rejects_manifest(tmp_path):
    output = tmp_path / "docs"
    output.mkdir()
    (output / ".confluence-publish.yml").write_text("pages: {}\n", encoding="utf-8")

    with pytest.raises(CloneError, match="already contains .confluence-publish.yml"):
        assert_clone_output_safe(output)


def test_assert_clone_output_safe_rejects_existing_markdown(tmp_path):
    output = tmp_path / "docs"
    output.mkdir()
    (output / "index.md").write_text("# Existing\n", encoding="utf-8")

    with pytest.raises(CloneError, match="already contains Markdown files"):
        assert_clone_output_safe(output)


def test_assert_clone_output_safe_allows_missing_output_directory(tmp_path):
    assert_clone_output_safe(tmp_path / "new-docs")
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_clone.py -v
```

Expected: FAIL because `confluence_publisher.clone` does not exist.

- [ ] **Step 3: Implement request parsing and output validation**

Create `src/confluence_publisher/clone.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse


class CloneError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedConfluenceUrl:
    page_id: str
    space: str
    url: str


@dataclass(frozen=True)
class CloneRequest:
    root_page_id: str
    space: str
    root_url: str | None
    output: Path
    max_depth: int | None
    max_pages: int
    dry_run: bool


def parse_confluence_page_url(url: str) -> ParsedConfluenceUrl:
    parsed = urlparse(url)
    segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
    space = None
    page_id = None
    for index, segment in enumerate(segments[:-1]):
        if segment == "spaces" and segments[index + 1]:
            space = segments[index + 1]
        if segment == "pages" and segments[index + 1]:
            page_id = segments[index + 1]
    if page_id is None or space is None:
        raise CloneError("--url must contain Confluence /spaces/<space>/pages/<page_id> segments")
    return ParsedConfluenceUrl(page_id=page_id, space=space, url=url)


def parse_clone_request(
    *,
    url: str | None,
    page_id: str | None,
    space: str | None,
    output: Path,
    max_depth: int | None,
    max_pages: int,
    dry_run: bool,
) -> CloneRequest:
    if url is None and page_id is None:
        raise CloneError("Either --url or --page-id is required")
    if url is not None and page_id is not None:
        raise CloneError("--url and --page-id are mutually exclusive")
    if max_depth is not None and max_depth < 0:
        raise CloneError("--max-depth must be greater than or equal to 0")
    if max_pages <= 0:
        raise CloneError("--max-pages must be greater than 0")

    if url is not None:
        parsed = parse_confluence_page_url(url)
        return CloneRequest(
            root_page_id=parsed.page_id,
            space=parsed.space,
            root_url=parsed.url,
            output=output,
            max_depth=max_depth,
            max_pages=max_pages,
            dry_run=dry_run,
        )

    if space is None:
        raise CloneError("--space is required with --page-id")
    return CloneRequest(
        root_page_id=str(page_id),
        space=space,
        root_url=None,
        output=output,
        max_depth=max_depth,
        max_pages=max_pages,
        dry_run=dry_run,
    )


def assert_clone_output_safe(output: Path) -> None:
    if not output.exists():
        return
    if not output.is_dir():
        raise CloneError(f"Clone output must be a directory: {output}")
    if (output / ".confluence-publish.yml").exists():
        raise CloneError(f"Clone output already contains .confluence-publish.yml: {output}")
    markdown_files = sorted(path for path in output.rglob("*.md") if path.is_file())
    if markdown_files:
        raise CloneError(f"Clone output already contains Markdown files: {output}")
```

- [ ] **Step 4: Run tests and verify they pass**

Run:

```bash
uv run pytest tests/confluence_publisher/test_clone.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/confluence_publisher/clone.py tests/confluence_publisher/test_clone.py
git commit -m "Add clone request validation"
```

---

### Task 2: Tree-To-Path Planning

**Files:**
- Modify: `src/confluence_publisher/models.py`
- Modify: `src/confluence_publisher/clone.py`
- Modify: `tests/confluence_publisher/test_clone.py`

- [ ] **Step 1: Write failing tests for tree mapping**

Append to `tests/confluence_publisher/test_clone.py`:

```python
from confluence_publisher.clone import build_clone_file_plan
from confluence_publisher.models import ConfluenceTreeNode


def test_build_clone_file_plan_maps_nested_pages_to_indexes_and_leaves():
    tree = ConfluenceTreeNode(
        page_id="1",
        title="Root Docs",
        url="https://example.atlassian.net/wiki/spaces/ENG/pages/1/Root",
        children=(
            ConfluenceTreeNode(
                page_id="2",
                title="Guide",
                url="https://example.atlassian.net/wiki/spaces/ENG/pages/2/Guide",
                children=(
                    ConfluenceTreeNode(
                        page_id="3",
                        title="Install & Run",
                        url="https://example.atlassian.net/wiki/spaces/ENG/pages/3/Install",
                    ),
                ),
            ),
            ConfluenceTreeNode(
                page_id="4",
                title="Quick Reference",
                url="https://example.atlassian.net/wiki/spaces/ENG/pages/4/Quick",
            ),
        ),
    )

    plan = build_clone_file_plan(tree)

    assert [(page.page_id, page.source_path) for page in plan.pages] == [
        ("1", "index.md"),
        ("2", "guide/index.md"),
        ("3", "guide/install-run.md"),
        ("4", "quick-reference.md"),
    ]


def test_build_clone_file_plan_makes_duplicate_leaf_slugs_unique():
    tree = ConfluenceTreeNode(
        page_id="1",
        title="Root",
        children=(
            ConfluenceTreeNode(page_id="2", title="Overview"),
            ConfluenceTreeNode(page_id="3", title="Overview"),
        ),
    )

    plan = build_clone_file_plan(tree)

    assert [(page.page_id, page.source_path) for page in plan.pages] == [
        ("1", "index.md"),
        ("2", "overview.md"),
        ("3", "overview-2.md"),
    ]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_clone.py::test_build_clone_file_plan_maps_nested_pages_to_indexes_and_leaves tests/confluence_publisher/test_clone.py::test_build_clone_file_plan_makes_duplicate_leaf_slugs_unique -v
```

Expected: FAIL because `ConfluenceTreeNode` and `build_clone_file_plan` do not exist.

- [ ] **Step 3: Add tree model and clone file plan**

Modify `src/confluence_publisher/models.py`:

```python
@dataclass(frozen=True)
class ConfluenceTreeNode:
    page_id: str
    title: str
    url: str | None = None
    children: tuple["ConfluenceTreeNode", ...] = ()
```

Append to `src/confluence_publisher/clone.py`:

```python
from pathlib import PurePosixPath

from .models import ConfluenceTreeNode, slugify


@dataclass(frozen=True)
class ClonePlannedPage:
    page_id: str
    title: str
    source_path: str
    url: str | None
    has_children: bool


@dataclass(frozen=True)
class CloneFilePlan:
    root: ConfluenceTreeNode
    pages: tuple[ClonePlannedPage, ...]


def _unique_slug(title: str, used: set[str]) -> str:
    base = slugify(title)
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def build_clone_file_plan(root: ConfluenceTreeNode) -> CloneFilePlan:
    pages: list[ClonePlannedPage] = []

    def visit(node: ConfluenceTreeNode, directory: PurePosixPath) -> None:
        has_children = bool(node.children)
        if not pages:
            source_path = "index.md"
            child_directory = PurePosixPath("")
        elif has_children:
            source_path = (directory / "index.md").as_posix()
            child_directory = directory
        else:
            source_path = (directory.parent / f"{directory.name}.md").as_posix()
            child_directory = directory.parent

        pages.append(
            ClonePlannedPage(
                page_id=node.page_id,
                title=node.title,
                source_path=source_path,
                url=node.url,
                has_children=has_children,
            )
        )

        used_child_slugs: set[str] = set()
        for child in node.children:
            child_slug = _unique_slug(child.title, used_child_slugs)
            visit(child, child_directory / child_slug)

    visit(root, PurePosixPath(""))
    return CloneFilePlan(root=root, pages=tuple(pages))
```

- [ ] **Step 4: Run tree mapping tests**

Run:

```bash
uv run pytest tests/confluence_publisher/test_clone.py::test_build_clone_file_plan_maps_nested_pages_to_indexes_and_leaves tests/confluence_publisher/test_clone.py::test_build_clone_file_plan_makes_duplicate_leaf_slugs_unique -v
```

Expected: PASS.

- [ ] **Step 5: Run all clone tests**

Run:

```bash
uv run pytest tests/confluence_publisher/test_clone.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/confluence_publisher/models.py src/confluence_publisher/clone.py tests/confluence_publisher/test_clone.py
git commit -m "Add clone file planning"
```

---

### Task 3: Manifest Generation And Local Writes

**Files:**
- Modify: `src/confluence_publisher/clone.py`
- Modify: `tests/confluence_publisher/test_clone.py`

- [ ] **Step 1: Write failing tests for manifest generation and writes**

Append to `tests/confluence_publisher/test_clone.py`:

```python
from confluence_publisher.bidirectional import compute_canonical_markdown_hash
from confluence_publisher.clone import CloneContentPage, write_clone_project
from confluence_publisher.manifest import load_manifest


def test_write_clone_project_creates_markdown_and_manifest(tmp_path):
    tree = ConfluenceTreeNode(
        page_id="1",
        title="Root Docs",
        url="https://example.atlassian.net/wiki/spaces/ENG/pages/1/Root",
        children=(ConfluenceTreeNode(page_id="2", title="Guide", url=None),),
    )
    file_plan = build_clone_file_plan(tree)
    content = {
        "1": CloneContentPage(markdown="# Root Docs\n\nWelcome.\n"),
        "2": CloneContentPage(markdown="# Guide\n\nRead this.\n"),
    }

    write_clone_project(
        output=tmp_path / "docs",
        space="ENG",
        root_url=tree.url,
        file_plan=file_plan,
        content=content,
    )

    assert (tmp_path / "docs" / "index.md").read_text(encoding="utf-8") == (
        "# Root Docs\n\nWelcome.\n"
    )
    assert (tmp_path / "docs" / "guide.md").read_text(encoding="utf-8") == (
        "# Guide\n\nRead this.\n"
    )
    manifest = load_manifest(tmp_path / "docs" / ".confluence-publish.yml")
    assert manifest.space == "ENG"
    assert manifest.sync.enabled is True
    assert manifest.root_source_path == "index.md"
    assert manifest.root_page_id == "1"
    assert manifest.root_url == tree.url
    assert manifest.pages["index.md"].canonical_hash == compute_canonical_markdown_hash(
        "# Root Docs\n\nWelcome.\n"
    )
    assert manifest.pages["guide.md"].canonical_hash == compute_canonical_markdown_hash(
        "# Guide\n\nRead this.\n"
    )


def test_write_clone_project_refuses_missing_content(tmp_path):
    tree = ConfluenceTreeNode(page_id="1", title="Root Docs")
    file_plan = build_clone_file_plan(tree)

    with pytest.raises(CloneError, match="Missing converted content for page 1"):
        write_clone_project(
            output=tmp_path / "docs",
            space="ENG",
            root_url=None,
            file_plan=file_plan,
            content={},
        )
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_clone.py::test_write_clone_project_creates_markdown_and_manifest tests/confluence_publisher/test_clone.py::test_write_clone_project_refuses_missing_content -v
```

Expected: FAIL because `CloneContentPage` and `write_clone_project` do not exist.

- [ ] **Step 3: Implement manifest generation and file writes**

Append to `src/confluence_publisher/clone.py`:

```python
from typing import Any

import yaml

from .bidirectional import compute_canonical_markdown_hash


@dataclass(frozen=True)
class CloneContentPage:
    markdown: str


def _manifest_for_clone(
    *,
    space: str,
    root_url: str | None,
    file_plan: CloneFilePlan,
    content: dict[str, CloneContentPage],
) -> dict[str, Any]:
    root_page = file_plan.pages[0]
    manifest: dict[str, Any] = {
        "space": space,
        "sync": {"enabled": True},
        "root": {
            "source_path": "index.md",
            "page_id": root_page.page_id,
        },
        "pages": {},
    }
    if root_url is not None:
        manifest["root"]["url"] = root_url

    pages_block: dict[str, dict[str, str]] = {}
    for page in file_plan.pages:
        converted = content.get(page.page_id)
        if converted is None:
            raise CloneError(f"Missing converted content for page {page.page_id}")
        page_data = {
            "title": page.title,
            "page_id": page.page_id,
            "canonical_hash": compute_canonical_markdown_hash(converted.markdown),
        }
        if page.url is not None:
            page_data["url"] = page.url
        pages_block[page.source_path] = page_data
    manifest["pages"] = pages_block
    return manifest


def write_clone_project(
    *,
    output: Path,
    space: str,
    root_url: str | None,
    file_plan: CloneFilePlan,
    content: dict[str, CloneContentPage],
) -> None:
    assert_clone_output_safe(output)
    manifest = _manifest_for_clone(
        space=space,
        root_url=root_url,
        file_plan=file_plan,
        content=content,
    )
    output.mkdir(parents=True, exist_ok=True)
    for page in file_plan.pages:
        converted = content.get(page.page_id)
        if converted is None:
            raise CloneError(f"Missing converted content for page {page.page_id}")
        target = output / page.source_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(converted.markdown, encoding="utf-8")

    (output / ".confluence-publish.yml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
```

- [ ] **Step 4: Run manifest/write tests**

Run:

```bash
uv run pytest tests/confluence_publisher/test_clone.py::test_write_clone_project_creates_markdown_and_manifest tests/confluence_publisher/test_clone.py::test_write_clone_project_refuses_missing_content -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/confluence_publisher/clone.py tests/confluence_publisher/test_clone.py
git commit -m "Add clone manifest generation"
```

---

### Task 4: MCP Child Tree Traversal

**Files:**
- Modify: `src/confluence_publisher/mcp_atlassian.py`
- Modify: `tests/confluence_publisher/test_mcp_atlassian.py`

- [ ] **Step 1: Write failing MCP traversal tests**

Append to `tests/confluence_publisher/test_mcp_atlassian.py`:

```python
from confluence_publisher.models import ConfluenceTreeNode


@pytest.mark.asyncio
async def test_client_gets_page_children():
    session = FakeMcpSession(
        page_result={
            "structuredContent": {
                "id": "1",
                "title": "Root",
                "url": "https://example.atlassian.net/wiki/spaces/ENG/pages/1/Root",
            }
        }
    )
    session.children_results = {
        "1": {
            "structuredContent": {
                "parent_id": "1",
                "count": 1,
                "results": [
                    {
                        "id": "2",
                        "title": "Child",
                        "url": "https://example.atlassian.net/wiki/spaces/ENG/pages/2/Child",
                    }
                ],
            }
        }
    }

    async def call_tool(name, arguments):
        session.calls.append((name, arguments))
        if name == "confluence_get_page_children":
            return session.children_results[arguments["parent_id"]]
        return await FakeMcpSession.call_tool(session, name, arguments)

    session.call_tool = call_tool
    client = McpAtlassianClient(session)

    children = await client.get_page_children("1")

    assert children == (
        ConfluenceTreeNode(
            page_id="2",
            title="Child",
            url="https://example.atlassian.net/wiki/spaces/ENG/pages/2/Child",
        ),
    )
    assert session.calls[-1] == (
        "confluence_get_page_children",
        {
            "parent_id": "1",
            "expand": "version",
            "limit": 50,
            "include_content": False,
            "convert_to_markdown": False,
            "start": 0,
            "include_folders": False,
        },
    )


@pytest.mark.asyncio
async def test_client_get_page_tree_recurses_with_limits():
    pages = {
        "1": {"id": "1", "title": "Root", "url": "https://example/pages/1/Root"},
        "2": {"id": "2", "title": "Child", "url": "https://example/pages/2/Child"},
        "3": {"id": "3", "title": "Grandchild", "url": "https://example/pages/3/Grandchild"},
    }
    children = {
        "1": [pages["2"]],
        "2": [pages["3"]],
        "3": [],
    }

    class TreeSession(FakeMcpSession):
        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            if name == "confluence_get_page":
                return {"structuredContent": pages[arguments["page_id"]]}
            if name == "confluence_get_page_children":
                return {
                    "structuredContent": {
                        "parent_id": arguments["parent_id"],
                        "count": len(children[arguments["parent_id"]]),
                        "results": children[arguments["parent_id"]],
                    }
                }
            raise AssertionError(name)

    session = TreeSession()
    client = McpAtlassianClient(session)

    tree = await client.get_page_tree("1", max_depth=1, max_pages=10)

    assert tree == ConfluenceTreeNode(
        page_id="1",
        title="Root",
        url="https://example/pages/1/Root",
        children=(
            ConfluenceTreeNode(
                page_id="2",
                title="Child",
                url="https://example/pages/2/Child",
            ),
        ),
    )
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_mcp_atlassian.py::test_build_server_command_limits_enabled_tools_and_version tests/confluence_publisher/test_mcp_atlassian.py::test_client_gets_page_children tests/confluence_publisher/test_mcp_atlassian.py::test_client_get_page_tree_recurses_with_limits -v
```

Expected: FAIL because `confluence_get_page_children` is not in `REQUIRED_TOOLS` and client methods do not exist.

- [ ] **Step 3: Implement MCP wrapper tree methods**

Modify imports and `REQUIRED_TOOLS` in `src/confluence_publisher/mcp_atlassian.py`:

```python
from .models import ConfluenceTreeNode, PageInfo, PageStorage

REQUIRED_TOOLS = (
    "confluence_search",
    "confluence_get_page",
    "confluence_get_page_children",
    "confluence_get_attachments",
    "confluence_create_page",
    "confluence_upload_attachment",
    "confluence_update_page",
)
```

Add helper after `_page_infos`:

```python
def _tree_nodes(payload: object, tool_name: str) -> tuple[ConfluenceTreeNode, ...]:
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        payload = payload["results"]
    if not isinstance(payload, list):
        raise McpToolError(f"{tool_name} returned no child page list")
    nodes = []
    for item in payload:
        if not isinstance(item, dict):
            raise McpToolError(f"{tool_name} returned invalid child page item")
        page_id = item.get("id")
        title = item.get("title")
        if page_id is None or str(page_id) == "" or not isinstance(title, str) or not title:
            raise McpToolError(f"{tool_name} returned invalid child page item")
        nodes.append(
            ConfluenceTreeNode(page_id=str(page_id), title=title, url=_page_url(item))
        )
    return tuple(nodes)
```

Add methods to `McpAtlassianClient`:

```python
    async def get_page_children(self, page_id: str) -> tuple[ConfluenceTreeNode, ...]:
        result = await self.session.call_tool(
            "confluence_get_page_children",
            {
                "parent_id": page_id,
                "expand": "version",
                "limit": 50,
                "include_content": False,
                "convert_to_markdown": False,
                "start": 0,
                "include_folders": False,
            },
        )
        payload = _result_payload(result)
        if isinstance(payload, dict):
            _raise_payload_error(payload, "confluence_get_page_children")
        return _tree_nodes(payload, "confluence_get_page_children")

    async def get_page_tree(
        self,
        page_id: str,
        *,
        max_depth: int | None,
        max_pages: int,
    ) -> ConfluenceTreeNode:
        seen: set[str] = set()

        async def visit(current_page_id: str, depth: int) -> ConfluenceTreeNode:
            if len(seen) >= max_pages:
                raise McpToolError(f"Confluence page tree exceeds --max-pages {max_pages}")
            if current_page_id in seen:
                raise McpToolError(f"Confluence page tree contains duplicate page {current_page_id}")
            seen.add(current_page_id)
            page = await self.get_page(current_page_id)
            if max_depth is not None and depth >= max_depth:
                return ConfluenceTreeNode(
                    page_id=page.page_id,
                    title=page.title,
                    url=page.url,
                )
            children = []
            for child in await self.get_page_children(current_page_id):
                children.append(await visit(child.page_id, depth + 1))
            return ConfluenceTreeNode(
                page_id=page.page_id,
                title=page.title,
                url=page.url,
                children=tuple(children),
            )

        return await visit(page_id, 0)
```

- [ ] **Step 4: Run MCP tests**

Run:

```bash
uv run pytest tests/confluence_publisher/test_mcp_atlassian.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/confluence_publisher/models.py src/confluence_publisher/mcp_atlassian.py tests/confluence_publisher/test_mcp_atlassian.py
git commit -m "Add Confluence tree reads"
```

---

### Task 5: Clone Execution With Reverse Conversion

**Files:**
- Modify: `src/confluence_publisher/clone.py`
- Modify: `tests/confluence_publisher/test_clone.py`

- [ ] **Step 1: Write failing tests for clone execution**

Append to `tests/confluence_publisher/test_clone.py`:

```python
from confluence_publisher.clone import clone_confluence_tree, format_clone_summary
from confluence_publisher.models import PageStorage


class FakeCloneClient:
    def __init__(self, tree, storages):
        self.tree = tree
        self.storages = storages
        self.calls = []

    async def get_page_tree(self, page_id, *, max_depth, max_pages):
        self.calls.append(("get_page_tree", page_id, max_depth, max_pages))
        return self.tree

    async def get_page_storage(self, page_id):
        self.calls.append(("get_page_storage", page_id))
        return self.storages[page_id]


@pytest.mark.asyncio
async def test_clone_confluence_tree_writes_converted_project(tmp_path):
    tree = ConfluenceTreeNode(
        page_id="1",
        title="Root Docs",
        url="https://example.atlassian.net/wiki/spaces/ENG/pages/1/Root",
        children=(ConfluenceTreeNode(page_id="2", title="Guide"),),
    )
    client = FakeCloneClient(
        tree,
        {
            "1": PageStorage(page_id="1", title="Root Docs", storage="<h1>Root Docs</h1><p>Welcome.</p>"),
            "2": PageStorage(page_id="2", title="Guide", storage="<h1>Guide</h1><p>Read this.</p>"),
        },
    )
    request = CloneRequest(
        root_page_id="1",
        space="ENG",
        root_url=tree.url,
        output=tmp_path / "docs",
        max_depth=None,
        max_pages=1000,
        dry_run=False,
    )

    result = await clone_confluence_tree(request, client=client)

    assert result.page_count == 2
    assert result.written is True
    assert (tmp_path / "docs" / "index.md").read_text(encoding="utf-8") == (
        "# Root Docs\n\nWelcome.\n"
    )
    assert client.calls == [
        ("get_page_tree", "1", None, 1000),
        ("get_page_storage", "1"),
        ("get_page_storage", "2"),
    ]


@pytest.mark.asyncio
async def test_clone_confluence_tree_dry_run_writes_nothing(tmp_path):
    tree = ConfluenceTreeNode(page_id="1", title="Root Docs")
    client = FakeCloneClient(
        tree,
        {"1": PageStorage(page_id="1", title="Root Docs", storage="<h1>Root Docs</h1>")},
    )
    request = CloneRequest(
        root_page_id="1",
        space="ENG",
        root_url=None,
        output=tmp_path / "docs",
        max_depth=2,
        max_pages=50,
        dry_run=True,
    )

    result = await clone_confluence_tree(request, client=client)

    assert result.written is False
    assert not (tmp_path / "docs").exists()


@pytest.mark.asyncio
async def test_clone_confluence_tree_refuses_unsupported_remote_content(tmp_path):
    tree = ConfluenceTreeNode(page_id="1", title="Root Docs")
    client = FakeCloneClient(
        tree,
        {
            "1": PageStorage(
                page_id="1",
                title="Root Docs",
                storage='<ac:structured-macro ac:name="jira"><ac:parameter ac:name="key">ABC-1</ac:parameter></ac:structured-macro>',
            )
        },
    )
    request = CloneRequest(
        root_page_id="1",
        space="ENG",
        root_url=None,
        output=tmp_path / "docs",
        max_depth=None,
        max_pages=1000,
        dry_run=False,
    )

    with pytest.raises(CloneError, match="Unsupported Confluence content"):
        await clone_confluence_tree(request, client=client)
    assert not (tmp_path / "docs").exists()


def test_format_clone_summary_lists_next_commands(tmp_path):
    tree = ConfluenceTreeNode(page_id="1", title="Root Docs")
    from confluence_publisher.clone import CloneResult

    summary = format_clone_summary(
        CloneResult(
            output=tmp_path / "docs",
            page_count=1,
            file_plan=build_clone_file_plan(tree),
            written=True,
        )
    )

    assert "Confluence clone complete" in summary
    assert "Pages: 1" in summary
    assert f"--project {tmp_path / 'docs'} --check-sync" in summary
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_clone.py::test_clone_confluence_tree_writes_converted_project tests/confluence_publisher/test_clone.py::test_clone_confluence_tree_dry_run_writes_nothing tests/confluence_publisher/test_clone.py::test_clone_confluence_tree_refuses_unsupported_remote_content tests/confluence_publisher/test_clone.py::test_format_clone_summary_lists_next_commands -v
```

Expected: FAIL because clone execution functions do not exist.

- [ ] **Step 3: Implement clone execution**

Append to `src/confluence_publisher/clone.py`:

```python
from .storage_to_markdown import storage_to_markdown


@dataclass(frozen=True)
class CloneResult:
    output: Path
    page_count: int
    file_plan: CloneFilePlan
    written: bool


async def clone_confluence_tree(request: CloneRequest, *, client) -> CloneResult:
    assert_clone_output_safe(request.output)
    tree = await client.get_page_tree(
        request.root_page_id,
        max_depth=request.max_depth,
        max_pages=request.max_pages,
    )
    file_plan = build_clone_file_plan(tree)
    content: dict[str, CloneContentPage] = {}
    unsupported_messages: list[str] = []
    for planned_page in file_plan.pages:
        remote = await client.get_page_storage(planned_page.page_id)
        converted = storage_to_markdown(remote.storage)
        if converted.unsupported:
            unsupported_messages.append(
                f"{planned_page.page_id} {planned_page.title}: "
                + "; ".join(converted.unsupported)
            )
        content[planned_page.page_id] = CloneContentPage(markdown=converted.markdown)

    if unsupported_messages:
        raise CloneError("Unsupported Confluence content: " + " | ".join(unsupported_messages))

    if not request.dry_run:
        write_clone_project(
            output=request.output,
            space=request.space,
            root_url=request.root_url or tree.url,
            file_plan=file_plan,
            content=content,
        )

    return CloneResult(
        output=request.output,
        page_count=len(file_plan.pages),
        file_plan=file_plan,
        written=not request.dry_run,
    )


def format_clone_summary(result: CloneResult) -> str:
    status = "complete" if result.written else "dry run"
    file_lines = "\n".join(f"  - {page.source_path}" for page in result.file_plan.pages)
    return "\n".join(
        [
            f"Confluence clone {status}",
            f"Output: {result.output}",
            f"Pages: {result.page_count}",
            "Files:",
            file_lines,
            "Next commands:",
            f"  uv run publish-confluence --project {result.output} --check-sync",
            f"  uv run publish-confluence --project {result.output} --pull --yes",
        ]
    )
```

- [ ] **Step 4: Run clone tests**

Run:

```bash
uv run pytest tests/confluence_publisher/test_clone.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/confluence_publisher/clone.py tests/confluence_publisher/test_clone.py
git commit -m "Add clone execution"
```

---

### Task 6: CLI Subcommand Integration

**Files:**
- Modify: `src/confluence_publisher/cli.py`
- Modify: `tests/confluence_publisher/test_cli.py`

- [ ] **Step 1: Write failing parser and run_async tests**

Append to `tests/confluence_publisher/test_cli.py`:

```python
def test_arg_parser_accepts_clone_subcommand(tmp_path):
    args = build_arg_parser().parse_args(
        [
            "clone",
            "--url",
            "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/"
            "pages/5164564861/Custom+UI+NEXT+Migration",
            "--output",
            str(tmp_path / "docs"),
            "--max-depth",
            "2",
            "--max-pages",
            "25",
            "--dry-run",
        ]
    )

    assert args.command == "clone"
    assert args.clone_url.startswith("https://criteo.atlassian.net/wiki/")
    assert args.clone_output == tmp_path / "docs"
    assert args.clone_max_depth == 2
    assert args.clone_max_pages == 25
    assert args.dry_run is True


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
            "--url",
            "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/"
            "pages/5164564861/Custom+UI+NEXT+Migration",
            "--output",
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
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_cli.py::test_arg_parser_accepts_clone_subcommand tests/confluence_publisher/test_cli.py::test_run_async_clone_dispatches_to_clone_flow -v
```

Expected: FAIL because `clone` is not parsed or dispatched.

- [ ] **Step 3: Add clone parser and dispatch**

Modify imports in `src/confluence_publisher/cli.py`:

```python
from .clone import (
    CloneError,
    clone_confluence_tree,
    format_clone_summary,
    parse_clone_request,
)
```

In `build_arg_parser()`, after existing options are registered, add:

```python
    subparsers = parser.add_subparsers(dest="command")
    clone_parser = subparsers.add_parser(
        "clone",
        help="Clone a Confluence page tree into a local Markdown project.",
    )
    clone_parser.add_argument("--url", dest="clone_url")
    clone_parser.add_argument("--page-id", dest="clone_page_id")
    clone_parser.add_argument("--space", dest="clone_space")
    clone_parser.add_argument("--output", dest="clone_output", type=Path, required=True)
    clone_parser.add_argument("--max-depth", dest="clone_max_depth", type=int)
    clone_parser.add_argument("--max-pages", dest="clone_max_pages", type=int, default=1000)
    clone_parser.add_argument("--dry-run", action="store_true")
```

At the beginning of `validate_args(args)`, add:

```python
    if getattr(args, "command", None) == "clone":
        return
```

At the beginning of `run_async(argv)`, after parsing and validating args, add:

```python
    if getattr(args, "command", None) == "clone":
        try:
            request = parse_clone_request(
                url=args.clone_url,
                page_id=args.clone_page_id,
                space=args.clone_space,
                output=args.clone_output,
                max_depth=args.clone_max_depth,
                max_pages=args.clone_max_pages,
                dry_run=args.dry_run,
            )
            async with connect_mcp_atlassian() as client:
                result = await clone_confluence_tree(request, client=client)
            print(format_clone_summary(result))
            return 0
        except CloneError as exc:
            raise SystemExit(str(exc)) from exc
```

- [ ] **Step 4: Run targeted CLI tests**

Run:

```bash
uv run pytest tests/confluence_publisher/test_cli.py::test_arg_parser_defaults_to_svg_and_mcp_backend tests/confluence_publisher/test_cli.py::test_arg_parser_accepts_project_mode tests/confluence_publisher/test_cli.py::test_arg_parser_accepts_clone_subcommand tests/confluence_publisher/test_cli.py::test_run_async_clone_dispatches_to_clone_flow -v
```

Expected: PASS.

- [ ] **Step 5: Run all CLI tests**

Run:

```bash
uv run pytest tests/confluence_publisher/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/confluence_publisher/cli.py tests/confluence_publisher/test_cli.py
git commit -m "Add clone CLI command"
```

---

### Task 7: README And Workflow Documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add clone workflow documentation**

In `README.md`, near the bidirectional sync section, add:

```markdown
## Clone Existing Confluence Pages

Create a local Markdown project from an existing Confluence page tree:

```bash
uv run publish-confluence clone \
  --url "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/5164564861/Custom+UI+NEXT+Migration" \
  --output docs/custom-ui-next-migration
```

`clone` reads the Confluence root page and its descendants, converts supported
storage XHTML back to Markdown, writes one Markdown file per page, and creates
`.confluence-publish.yml` with page IDs, URLs, titles, and `canonical_hash`
baselines. It never updates Confluence.

After cloning, use the regular project commands:

```bash
uv run publish-confluence --project docs/custom-ui-next-migration --check-sync
uv run publish-confluence --project docs/custom-ui-next-migration --pull --yes
uv run publish-confluence --project docs/custom-ui-next-migration --dry-run
```
```

- [ ] **Step 2: Run README grep check**

Run:

```bash
rg -n "Clone Existing Confluence Pages|publish-confluence clone|--check-sync|--pull" README.md
```

Expected: output includes the new clone section and the existing bidirectional sync commands.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document Confluence clone workflow"
```

---

### Task 8: Verification And Local Clone Smoke

**Files:**
- No code edits expected.

- [ ] **Step 1: Run clone and bidirectional targeted tests**

Run:

```bash
uv run pytest tests/confluence_publisher/test_clone.py tests/confluence_publisher/test_bidirectional.py -v
```

Expected: PASS.

- [ ] **Step 2: Run MCP and CLI tests**

Run:

```bash
uv run pytest tests/confluence_publisher/test_mcp_atlassian.py tests/confluence_publisher/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run:

```bash
uv run pytest tests/confluence_publisher -v
```

Expected: PASS.

- [ ] **Step 4: Run coverage**

Run:

```bash
uv run pytest tests/confluence_publisher --cov=confluence_publisher --cov-report=term-missing
```

Expected: PASS. Coverage should remain near the current 91% baseline.

- [ ] **Step 5: Run whitespace check**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 6: Run CLI help smoke**

Run:

```bash
uv run publish-confluence clone --help
```

Expected: help output includes `--url`, `--page-id`, `--space`, `--output`, `--max-depth`, `--max-pages`, and `--dry-run`.

- [ ] **Step 7: Clone the requested Confluence tree locally**

Run only after tests pass:

```bash
set -a
source ~/.uagent/.env
set +a
CONFLUENCE_URL="$CRITEO_CONFLUENCE_BASE_URL/wiki" \
CONFLUENCE_USERNAME="$CRITEO_CONFLUENCE_USERNAME" \
CONFLUENCE_API_TOKEN="$CRITEO_CONFLUENCE_API_TOKEN" \
uv run publish-confluence clone \
  --url "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/5164564861/Custom+UI+NEXT+Migration" \
  --output docs/pulled/custom-ui-next-migration
```

Expected: command completes without Confluence writes and reports 19 pages.

- [ ] **Step 8: Validate the cloned project plan**

Run:

```bash
uv run publish-confluence --project docs/pulled/custom-ui-next-migration --plan-only
```

Expected: PASS, no MCP connection required, publish plan lists the cloned Markdown files.

- [ ] **Step 9: Commit cloned docs only if the user wants them in the branch**

Default: leave `docs/pulled/custom-ui-next-migration` uncommitted for review.

If the user asks to commit:

```bash
git add docs/pulled/custom-ui-next-migration
git commit -m "Clone Custom UI NEXT Migration docs"
```
