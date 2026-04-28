# Confluence Sync Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe Confluence sync mode that can recreate a deleted project root, recreate stale pages when allowed, write new page IDs and URLs back to `.confluence-publish.yml`, and skip unchanged page updates using stored content hashes.

**Architecture:** Keep the existing `batch.py` flow for `--create-missing-pages` and add a focused `sync.py` state machine for reconciliation. Extend manifest and planner models with sync policy, root parent metadata, and page sync metadata; then route project mode to `sync_project()` only when sync is enabled by CLI or manifest.

**Tech Stack:** Python 3.12, argparse, dataclasses, PyYAML, pytest, pytest-asyncio, existing MCP Atlassian client wrapper, existing Markdown-to-Confluence renderer.

---

## File Structure

- Modify `src/confluence_publisher/manifest.py`
  - Add `SyncSettings` and `ManifestPageSyncState`.
  - Parse `sync.enabled`, `sync.recreate_missing_root`, `sync.recreate_stale_pages`.
  - Parse `root.parent_page_id` and `root.parent_url`.
  - Parse per-page `content_hash` and `last_synced_at`.
  - Add `write_manifest_sync_state()` for page IDs, URLs, hashes, timestamps, and root metadata.
- Modify `src/confluence_publisher/models.py`
  - Add optional `url` to `PageInfo` so created page links can flow back to the manifest.
- Modify `src/confluence_publisher/mcp_atlassian.py`
  - Preserve existing behavior and populate `PageInfo.url` from common MCP/Confluence response shapes when available.
- Modify `src/confluence_publisher/planner.py`
  - Carry sync settings and root parent metadata in `PublishPlan`.
  - Allow missing page IDs in sync mode.
- Modify `src/confluence_publisher/batch.py`
  - Keep legacy publishing behavior.
  - Reuse title and parent preflight helpers from sync.
  - Include effective sync settings in plan output when sync is enabled.
- Create `src/confluence_publisher/sync.py`
  - Prepare rendered storage once per page.
  - Compute deterministic content hashes.
  - Validate root and page state before writes.
  - Recreate the root and project tree when the root is stale and policy allows it.
  - Recreate individual stale pages when policy allows it.
  - Update only changed pages and rewrite manifest sync metadata.
- Modify `src/confluence_publisher/cli.py`
  - Add `--sync`, `--recreate-missing-root`, `--recreate-stale-pages`.
  - Merge manifest and CLI sync policy.
  - Route sync-enabled project publishing through `sync_project()`.
- Add `tests/confluence_publisher/test_sync.py`
  - Cover root recovery, stale page policy, title collisions, manifest writes, and hash skip.
- Modify existing tests:
  - `tests/confluence_publisher/test_manifest.py`
  - `tests/confluence_publisher/test_planner.py`
  - `tests/confluence_publisher/test_cli.py`
  - `tests/confluence_publisher/test_mcp_atlassian.py`
- Modify `README.md` and `DESIGN.md`
  - Document sync mode, manifest config, recovery behavior, and safety limits.

## Design Details

Root page selection in sync mode:

1. If any planned page has `page_id == plan.root_page_id`, that page is the project root page.
2. Else if any planned page has `url == plan.root_url`, that page is the project root page.
3. Else use the first planned page in planner order.

This supports the current Whitelabel shape, where `00-overview.md` is the root page, while also supporting `index.md` projects.

Root recovery behavior:

- If the configured root page is readable, keep it.
- If the root is missing and `recreate_missing_root` is false, fail before writes.
- If the root is missing and `recreate_missing_root` is true, require a readable `root.parent_page_id`.
- When recreating the root, create the root page under `root.parent_page_id`.
- When recreating the root, create every other project page under the recreated root or under its planned directory parent.
- Old readable pages left behind by deleted roots are not deleted or moved.
- Title collision preflight still blocks any create. This is required because Confluence title uniqueness is space-wide in the target setup.

Hash behavior:

- Hash input is the rendered storage payload plus publish settings that affect storage.
- The hash format is `sha256:<hex>`.
- A matching stored hash skips attachment upload and page update.
- A missing or different hash publishes the page and stores the new hash and timestamp.
- Creation always stores a new hash and timestamp after the created page is published.

## Task 1: Manifest Sync Metadata

**Files:**
- Modify: `src/confluence_publisher/manifest.py`
- Test: `tests/confluence_publisher/test_manifest.py`

- [ ] **Step 1: Write failing manifest tests**

Append these tests to `tests/confluence_publisher/test_manifest.py`:

```python
from confluence_publisher.manifest import (
    ManifestPageSyncState,
    SyncSettings,
    write_manifest_sync_state,
)


def test_load_manifest_reads_sync_root_parent_and_page_hashes(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
sync:
  enabled: true
  recreate_missing_root: true
  recreate_stale_pages: false
root:
  parent_page_id: "2163738446"
  parent_url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/2163738446/Overview"
  page_id: "5155097071"
  url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/5155097071/White-label+NEXT+Deployment+-+Overview"
pages:
  00-overview.md:
    title: "White-label NEXT Deployment - Overview"
    page_id: "5155097071"
    url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/5155097071/White-label+NEXT+Deployment+-+Overview"
    content_hash: "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    last_synced_at: "2026-04-28T12:00:00Z"
""".strip(),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert manifest.sync == SyncSettings(
        enabled=True,
        recreate_missing_root=True,
        recreate_stale_pages=False,
    )
    assert manifest.root_parent_page_id == "2163738446"
    assert manifest.root_parent_url.endswith("/2163738446/Overview")
    assert manifest.root_page_id == "5155097071"
    page = manifest.pages["00-overview.md"]
    assert page.content_hash == "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert page.last_synced_at == "2026-04-28T12:00:00Z"


def test_load_manifest_rejects_non_boolean_sync_setting(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
sync:
  enabled: "yes"
pages:
  index.md:
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="sync.enabled"):
        load_manifest(manifest_path)


def test_write_manifest_sync_state_updates_root_and_pages_preserving_titles(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root:
  parent_page_id: "2163738446"
  page_id: "old-root"
pages:
  00-overview.md:
    title: "Overview"
    page_id: "old-root"
  guide/index.md:
    title: "Guide"
""".strip(),
        encoding="utf-8",
    )

    write_manifest_sync_state(
        manifest_path,
        root_page_id="new-root",
        root_url="https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/new-root/Overview",
        pages={
            "00-overview.md": ManifestPageSyncState(
                page_id="new-root",
                url="https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/new-root/Overview",
                content_hash="sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                last_synced_at="2026-04-28T13:00:00Z",
            ),
            "guide/index.md": ManifestPageSyncState(
                page_id="new-guide",
                url="https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/new-guide/Guide",
                content_hash="sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
                last_synced_at="2026-04-28T13:00:00Z",
            ),
        },
    )

    manifest = load_manifest(manifest_path)
    assert manifest.root_parent_page_id == "2163738446"
    assert manifest.root_page_id == "new-root"
    assert manifest.root_url.endswith("/new-root/Overview")
    assert manifest.pages["00-overview.md"].title == "Overview"
    assert manifest.pages["00-overview.md"].page_id == "new-root"
    assert manifest.pages["guide/index.md"].page_id == "new-guide"
    assert manifest.pages["guide/index.md"].content_hash.startswith("sha256:")
    assert manifest.pages["guide/index.md"].last_synced_at == "2026-04-28T13:00:00Z"
```

- [ ] **Step 2: Run manifest tests and confirm failure**

Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/confluence_publisher/test_manifest.py -q -p no:cacheprovider
```

Expected: FAIL because `SyncSettings`, `ManifestPageSyncState`, and `write_manifest_sync_state` do not exist.

- [ ] **Step 3: Add manifest dataclasses and parsing**

In `src/confluence_publisher/manifest.py`, add these dataclasses next to `ManifestPage`:

```python
@dataclass(frozen=True)
class SyncSettings:
    enabled: bool = False
    recreate_missing_root: bool = False
    recreate_stale_pages: bool = False


@dataclass(frozen=True)
class ManifestPageSyncState:
    page_id: str | None = None
    url: str | None = None
    content_hash: str | None = None
    last_synced_at: str | None = None
```

Extend `ManifestPage`:

```python
@dataclass(frozen=True)
class ManifestPage:
    path: str
    page_id: str | None
    title: str | None = None
    url: str | None = None
    content_hash: str | None = None
    last_synced_at: str | None = None
```

Extend `ProjectManifest`:

```python
    root_page_id: str | None = None
    root_url: str | None = None
    root_parent_page_id: str | None = None
    root_parent_url: str | None = None
    sync: SyncSettings = SyncSettings()
```

Add boolean parsing:

```python
def _optional_bool(value: Any, field_name: str, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ManifestError(f"{field_name} must be a boolean")
    return value


def _sync_settings(root: dict[str, Any]) -> SyncSettings:
    raw = root.get("sync", {})
    data = _as_mapping(raw, "sync")
    return SyncSettings(
        enabled=_optional_bool(data.get("enabled"), "sync.enabled"),
        recreate_missing_root=_optional_bool(
            data.get("recreate_missing_root"),
            "sync.recreate_missing_root",
        ),
        recreate_stale_pages=_optional_bool(
            data.get("recreate_stale_pages"),
            "sync.recreate_stale_pages",
        ),
    )
```

Change `_root_metadata()` to return five values:

```python
def _root_metadata(
    root: dict[str, Any],
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
```

Inside nested `root:` parsing, read:

```python
        nested_parent_page_id = _optional_str(
            root_data.get("parent_page_id"),
            "root.parent_page_id",
        )
        nested_parent_url = _optional_str(root_data.get("parent_url"), "root.parent_url")
```

Validate `root.parent_url` using `_page_id_from_url()` exactly like `root.url`, and return:

```python
    return (
        root_page_id,
        root_url,
        root_parent_page_id,
        root_parent_url,
        _space_from_url(root_url or root_parent_url) if (root_url or root_parent_url) else None,
    )
```

In `load_manifest()`, unpack five values, parse sync settings, and include page sync fields:

```python
    root_page_id, root_url, root_parent_page_id, root_parent_url, root_space = _root_metadata(root)
    sync = _sync_settings(root)
```

When constructing `ManifestPage`, pass:

```python
        content_hash=_optional_str(
            page_data.get("content_hash"),
            f"pages.{page_path}.content_hash",
        ),
        last_synced_at=_optional_str(
            page_data.get("last_synced_at"),
            f"pages.{page_path}.last_synced_at",
        ),
```

Return the new manifest fields:

```python
        root_parent_page_id=root_parent_page_id,
        root_parent_url=root_parent_url,
        sync=sync,
```

- [ ] **Step 4: Add manifest sync-state writer**

Add this function below `write_manifest_page_ids()`:

```python
def write_manifest_sync_state(
    path: Path,
    *,
    root_page_id: str | None = None,
    root_url: str | None = None,
    pages: dict[str, ManifestPageSyncState],
) -> None:
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise ManifestError(f"Invalid YAML in manifest {path}: {exc}") from exc

    if raw is None:
        raw = {}
    root = _as_mapping(raw, "manifest")
    root_block = root.get("root")
    if root_block is None:
        root_block = {}
        root["root"] = root_block
    root_data = _as_mapping(root_block, "root")

    if root_page_id is not None:
        root_data["page_id"] = root_page_id
    if root_url is not None:
        root_data["url"] = root_url

    pages_block = _as_mapping(root.get("pages"), "pages")
    for page_path, state in pages.items():
        normalized_path = _normalize_page_path(page_path)
        page_data = _as_mapping(pages_block.get(normalized_path), f"pages.{normalized_path}")
        if state.page_id is not None:
            page_data["page_id"] = state.page_id
        if state.url is not None:
            page_data["url"] = state.url
        if state.content_hash is not None:
            page_data["content_hash"] = state.content_hash
        if state.last_synced_at is not None:
            page_data["last_synced_at"] = state.last_synced_at

    path.write_text(
        yaml.safe_dump(root, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
```

- [ ] **Step 5: Run manifest tests and commit**

Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/confluence_publisher/test_manifest.py -q -p no:cacheprovider
```

Expected: PASS.

Commit:

```bash
git add src/confluence_publisher/manifest.py tests/confluence_publisher/test_manifest.py
git commit -m "Add manifest sync metadata"
```

## Task 2: Page URLs, Planner Sync Settings, and CLI Flags

**Files:**
- Modify: `src/confluence_publisher/models.py`
- Modify: `src/confluence_publisher/mcp_atlassian.py`
- Modify: `src/confluence_publisher/planner.py`
- Modify: `src/confluence_publisher/cli.py`
- Modify: `src/confluence_publisher/batch.py`
- Test: `tests/confluence_publisher/test_mcp_atlassian.py`
- Test: `tests/confluence_publisher/test_planner.py`
- Test: `tests/confluence_publisher/test_cli.py`

- [ ] **Step 1: Add failing tests for URLs and sync planning**

Add this test to `tests/confluence_publisher/test_mcp_atlassian.py`:

```python
@pytest.mark.asyncio
async def test_create_page_reads_url_from_links_payload():
    session = FakeSession(
        {
            "structuredContent": {
                "page": {
                    "id": "5155099000",
                    "title": "Overview",
                    "_links": {
                        "base": "https://criteo.atlassian.net/wiki",
                        "webui": "/spaces/~6393203da593cb822e995262/pages/5155099000/Overview",
                    },
                }
            }
        }
    )
    client = McpAtlassianClient(session)

    page = await client.create_page(
        space_key="~6393203da593cb822e995262",
        title="Overview",
        parent_id="2163738446",
        storage="<p>Created.</p>",
    )

    assert page.page_id == "5155099000"
    assert page.url == (
        "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/"
        "pages/5155099000/Overview"
    )
```

Add these tests to `tests/confluence_publisher/test_planner.py`:

```python
def test_build_publish_plan_carries_sync_settings_and_root_parent(tmp_path):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    project_manifest = ProjectManifest(
        path=tmp_path / ".confluence-publish.yml",
        space="~6393203da593cb822e995262",
        include=("**/*.md",),
        exclude=(),
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        pages={"index.md": page("index.md", "111111")},
        root_page_id="111111",
        root_parent_page_id="2163738446",
        sync=SyncSettings(enabled=True, recreate_missing_root=True),
    )

    plan = build_publish_plan(
        project_root=tmp_path,
        manifest=project_manifest,
        discovered_paths=("index.md",),
        excluded_count=0,
        version_comment=None,
        output_format=None,
        diagram_width=None,
        allow_diagram_fallback=None,
        sync_enabled=True,
        recreate_missing_root=True,
        recreate_stale_pages=False,
    )

    assert plan.sync_enabled is True
    assert plan.recreate_missing_root is True
    assert plan.recreate_stale_pages is False
    assert plan.root_parent_page_id == "2163738446"


def test_build_publish_plan_allows_missing_page_ids_with_sync_mode(tmp_path):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    project_manifest = ProjectManifest(
        path=tmp_path / ".confluence-publish.yml",
        space="~6393203da593cb822e995262",
        include=("**/*.md",),
        exclude=(),
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        pages={"index.md": page("index.md", None)},
        root_parent_page_id="2163738446",
        sync=SyncSettings(enabled=True),
    )

    plan = build_publish_plan(
        project_root=tmp_path,
        manifest=project_manifest,
        discovered_paths=("index.md",),
        excluded_count=0,
        version_comment=None,
        output_format=None,
        diagram_width=None,
        allow_diagram_fallback=None,
        sync_enabled=True,
        recreate_missing_root=False,
        recreate_stale_pages=False,
    )

    assert plan.items[0].page_id is None
```

Update imports in `tests/confluence_publisher/test_planner.py`:

```python
from confluence_publisher.manifest import ManifestPage, ProjectManifest, SyncSettings
```

Add these tests to `tests/confluence_publisher/test_cli.py`:

```python
def test_arg_parser_accepts_sync_flags():
    args = build_arg_parser().parse_args(
        [
            "--project",
            "docs",
            "--sync",
            "--recreate-missing-root",
            "--recreate-stale-pages",
        ]
    )

    assert args.sync is True
    assert args.recreate_missing_root is True
    assert args.recreate_stale_pages is True


def test_validate_args_rejects_sync_flags_in_source_mode():
    args = build_arg_parser().parse_args(
        ["--source", "page.md", "--page-id", "111111", "--sync"]
    )

    with pytest.raises(SystemExit, match="--sync"):
        cli.validate_args(args)
```

- [ ] **Step 2: Run targeted tests and confirm failure**

Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run pytest \
  tests/confluence_publisher/test_mcp_atlassian.py::test_create_page_reads_url_from_links_payload \
  tests/confluence_publisher/test_planner.py::test_build_publish_plan_carries_sync_settings_and_root_parent \
  tests/confluence_publisher/test_planner.py::test_build_publish_plan_allows_missing_page_ids_with_sync_mode \
  tests/confluence_publisher/test_cli.py::test_arg_parser_accepts_sync_flags \
  tests/confluence_publisher/test_cli.py::test_validate_args_rejects_sync_flags_in_source_mode \
  -q -p no:cacheprovider
```

Expected: FAIL because the new model fields and CLI flags do not exist.

- [ ] **Step 3: Extend `PageInfo` and MCP URL parsing**

In `src/confluence_publisher/models.py`, change `PageInfo`:

```python
@dataclass(frozen=True)
class PageInfo:
    page_id: str
    title: str
    url: str | None = None
```

In `src/confluence_publisher/mcp_atlassian.py`, add:

```python
def _page_url(payload: dict[str, Any]) -> str | None:
    url = payload.get("url")
    if isinstance(url, str) and url:
        return url
    links = payload.get("_links")
    if not isinstance(links, dict):
        return None
    webui = links.get("webui")
    if not isinstance(webui, str) or not webui:
        return None
    base = links.get("base")
    if isinstance(base, str) and base:
        return f"{base.rstrip('/')}/{webui.lstrip('/')}"
    return webui
```

Return URLs from `get_page()` and `create_page()`:

```python
        return PageInfo(page_id=str(payload.get("id", page_id)), title=title, url=_page_url(payload))
```

```python
        return PageInfo(
            page_id=page_id,
            title=page_title if isinstance(page_title, str) and page_title else title,
            url=_page_url(page_payload),
        )
```

- [ ] **Step 4: Extend planner with sync settings**

In `src/confluence_publisher/planner.py`, extend `PlannedPage`:

```python
    content_hash: str | None = None
    last_synced_at: str | None = None
```

When building `PlannedPage`, pass the manifest sync metadata:

```python
                content_hash=manifest_page.content_hash,
                last_synced_at=manifest_page.last_synced_at,
```

Extend `PublishPlan`:

```python
    root_url: str | None = None
    root_parent_page_id: str | None = None
    root_parent_url: str | None = None
    create_missing_pages: bool = False
    sync_enabled: bool = False
    recreate_missing_root: bool = False
    recreate_stale_pages: bool = False
```

Add parameters to `build_publish_plan()`:

```python
    sync_enabled: bool = False,
    recreate_missing_root: bool = False,
    recreate_stale_pages: bool = False,
```

Change missing ID validation:

```python
    allow_missing_ids = create_missing_pages or sync_enabled
    if missing_page_ids and not allow_missing_ids:
```

Keep the existing `create_missing_pages` root/space requirement only inside the create-mode branch:

```python
    if missing_page_ids and create_missing_pages:
        if not manifest.root_page_id:
            raise PlannerError("Cannot create missing pages without a root page")
        if not manifest.space:
            raise PlannerError("Cannot create missing pages without a Confluence space")
```

Return the new plan fields:

```python
        root_url=manifest.root_url,
        root_parent_page_id=manifest.root_parent_page_id,
        root_parent_url=manifest.root_parent_url,
        sync_enabled=sync_enabled,
        recreate_missing_root=recreate_missing_root,
        recreate_stale_pages=recreate_stale_pages,
```

- [ ] **Step 5: Add CLI flags and effective policy calculation**

In `build_arg_parser()`:

```python
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--recreate-missing-root", action="store_true")
    parser.add_argument("--recreate-stale-pages", action="store_true")
```

In `validate_args()`, reject these for source mode:

```python
        if args.sync:
            raise SystemExit("--sync is only supported with --project")
        if args.recreate_missing_root:
            raise SystemExit("--recreate-missing-root is only supported with --project")
        if args.recreate_stale_pages:
            raise SystemExit("--recreate-stale-pages is only supported with --project")
```

In `run_project_mode()`, after loading the manifest:

```python
        sync_enabled = manifest.sync.enabled or args.sync
        recreate_missing_root = (
            manifest.sync.recreate_missing_root or args.recreate_missing_root
        )
        recreate_stale_pages = (
            manifest.sync.recreate_stale_pages or args.recreate_stale_pages
        )
```

Use:

```python
            allow_missing_page_ids=args.create_missing_pages or sync_enabled,
```

Pass the settings into `build_publish_plan()`:

```python
            sync_enabled=sync_enabled,
            recreate_missing_root=recreate_missing_root,
            recreate_stale_pages=recreate_stale_pages,
```

- [ ] **Step 6: Show sync settings in plan output**

In `src/confluence_publisher/batch.py`, extend `format_publish_plan()`:

```python
    if plan.sync_enabled:
        lines.extend(
            [
                f"Sync: enabled",
                f"Recreate missing root: {'yes' if plan.recreate_missing_root else 'no'}",
                f"Recreate stale pages: {'yes' if plan.recreate_stale_pages else 'no'}",
            ]
        )
```

- [ ] **Step 7: Run targeted tests and commit**

Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run pytest \
  tests/confluence_publisher/test_mcp_atlassian.py \
  tests/confluence_publisher/test_planner.py \
  tests/confluence_publisher/test_cli.py \
  -q -p no:cacheprovider
```

Expected: PASS.

Commit:

```bash
git add src/confluence_publisher/models.py src/confluence_publisher/mcp_atlassian.py src/confluence_publisher/planner.py src/confluence_publisher/cli.py src/confluence_publisher/batch.py tests/confluence_publisher/test_mcp_atlassian.py tests/confluence_publisher/test_planner.py tests/confluence_publisher/test_cli.py
git commit -m "Add sync planning options"
```

## Task 3: Sync Hashing and Prepared Page Rendering

**Files:**
- Create: `src/confluence_publisher/sync.py`
- Test: `tests/confluence_publisher/test_sync.py`

- [ ] **Step 1: Write failing hash tests**

Create `tests/confluence_publisher/test_sync.py`:

```python
from pathlib import Path

import pytest

from confluence_publisher.models import PageInfo
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


@pytest.mark.asyncio
async def test_prepare_sync_pages_returns_rendered_storage_and_hash(tmp_path):
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

    assert prepared == {
        "index.md": PreparedSyncPage(
            item=plan.items[0],
            storage=prepared["index.md"].storage,
            rendered_diagrams=[],
            content_hash=prepared["index.md"].content_hash,
        )
    }
    assert "<h1>Home</h1>" in prepared["index.md"].storage
    assert prepared["index.md"].content_hash.startswith("sha256:")
```

- [ ] **Step 2: Run sync hash tests and confirm failure**

Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/confluence_publisher/test_sync.py -q -p no:cacheprovider
```

Expected: FAIL because `confluence_publisher.sync` does not exist.

- [ ] **Step 3: Create sync hashing module**

Create `src/confluence_publisher/sync.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from .cli import _prepare_single_page
from .models import RenderedDiagram
from .planner import PlannedPage, PublishPlan


CONTENT_HASH_VERSION = "storage:v1"


@dataclass(frozen=True)
class PreparedSyncPage:
    item: PlannedPage
    storage: str
    rendered_diagrams: list[RenderedDiagram]
    content_hash: str


def compute_content_hash(
    *,
    title: str,
    storage: str,
    output_format: str,
    diagram_width: int,
    allow_diagram_fallback: bool,
) -> str:
    payload = {
        "version": CONTENT_HASH_VERSION,
        "title": title,
        "storage": storage,
        "output_format": output_format,
        "diagram_width": diagram_width,
        "allow_diagram_fallback": allow_diagram_fallback,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def prepare_sync_pages(plan: PublishPlan, *, mmdc_bin: str) -> dict[str, PreparedSyncPage]:
    prepared_pages: dict[str, PreparedSyncPage] = {}
    for item in plan.items:
        with _prepare_single_page(
            source=item.absolute_path,
            output_format=plan.output_format,
            diagram_width=plan.diagram_width,
            mmdc_bin=mmdc_bin,
            allow_diagram_fallback=plan.allow_diagram_fallback,
        ) as prepared:
            content_hash = compute_content_hash(
                title=item.title,
                storage=prepared.storage,
                output_format=plan.output_format,
                diagram_width=plan.diagram_width,
                allow_diagram_fallback=plan.allow_diagram_fallback,
            )
            prepared_pages[item.source_path] = PreparedSyncPage(
                item=item,
                storage=prepared.storage,
                rendered_diagrams=list(prepared.rendered_diagrams),
                content_hash=content_hash,
            )
    return prepared_pages
```

- [ ] **Step 4: Run sync hash tests and commit**

Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/confluence_publisher/test_sync.py -q -p no:cacheprovider
```

Expected: PASS.

Commit:

```bash
git add src/confluence_publisher/sync.py tests/confluence_publisher/test_sync.py
git commit -m "Add sync content hashing"
```

## Task 4: Sync State Validation Before Writes

**Files:**
- Modify: `src/confluence_publisher/sync.py`
- Test: `tests/confluence_publisher/test_sync.py`

- [ ] **Step 1: Add fake client and failing validation tests**

Append this fake client and helpers to `tests/confluence_publisher/test_sync.py`:

```python
from confluence_publisher.mcp_atlassian import McpToolError


class FakeSyncClient:
    def __init__(self, *, page_errors=None, search_results=None):
        self.calls = []
        self.created = 0
        self.page_errors = page_errors or {}
        self.search_results = search_results or {}

    async def get_page(self, page_id: str) -> PageInfo:
        self.calls.append(("get_page", page_id))
        if page_id in self.page_errors:
            raise self.page_errors[page_id]
        return PageInfo(page_id=page_id, title=f"Page {page_id}")

    async def search_pages_by_title(self, *, space_key: str, title: str):
        self.calls.append(("search_pages_by_title", space_key, title))
        return self.search_results.get(title, [])

    async def create_page(self, *, space_key: str, title: str, parent_id: str, storage: str):
        self.created += 1
        page_id = str(800000 + self.created)
        self.calls.append(("create_page", space_key, title, parent_id, page_id))
        return PageInfo(page_id=page_id, title=title)

    async def upload_attachment(self, page_id: str, file_path: Path) -> None:
        self.calls.append(("upload_attachment", page_id, file_path.name))

    async def update_page_storage(
        self,
        page_id: str,
        title: str,
        storage: str,
        version_comment: str,
    ) -> None:
        self.calls.append(("update_page_storage", page_id, title, version_comment))


def sync_plan(tmp_path, *, root_page_id="111111", root_parent_page_id="2163738446", items=None):
    if items is None:
        source = tmp_path / "index.md"
        source.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
        items = (
            PlannedPage(
                source_path="index.md",
                absolute_path=source,
                page_id=root_page_id,
                title="Home",
                url=None,
                parent_source_path=None,
            ),
        )
    return PublishPlan(
        project_root=tmp_path,
        manifest_path=tmp_path / ".confluence-publish.yml",
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=items,
        space="~6393203da593cb822e995262",
        root_page_id=root_page_id,
        root_parent_page_id=root_parent_page_id,
        sync_enabled=True,
    )
```

Append these tests:

```python
@pytest.mark.asyncio
async def test_sync_project_missing_root_without_policy_fails_before_create(tmp_path):
    from confluence_publisher.sync import sync_project

    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root:
  parent_page_id: "2163738446"
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
""".strip(),
        encoding="utf-8",
    )
    plan = sync_plan(tmp_path)
    client = FakeSyncClient(
        page_errors={"111111": McpToolError("page 111111 not found")}
    )

    with pytest.raises(RuntimeError, match="root page.*111111.*--recreate-missing-root"):
        await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert client.calls == [("get_page", "111111")]


@pytest.mark.asyncio
async def test_sync_project_missing_root_with_policy_requires_parent(tmp_path):
    from confluence_publisher.sync import sync_project

    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root:
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
""".strip(),
        encoding="utf-8",
    )
    plan = sync_plan(tmp_path, root_parent_page_id=None)
    plan = PublishPlan(**{**plan.__dict__, "recreate_missing_root": True})
    client = FakeSyncClient(
        page_errors={"111111": McpToolError("page 111111 not found")}
    )

    with pytest.raises(RuntimeError, match="root.parent_page_id"):
        await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert client.calls == [("get_page", "111111")]


@pytest.mark.asyncio
async def test_sync_project_stale_page_without_policy_fails_before_create(tmp_path):
    from confluence_publisher.sync import sync_project

    child_dir = tmp_path / "guide"
    child_dir.mkdir()
    root = tmp_path / "index.md"
    child = child_dir / "index.md"
    root.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    child.write_text("# Guide\n\nPlain text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
root:
  parent_page_id: "2163738446"
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
  guide/index.md:
    page_id: "222222"
    title: "Guide"
""".strip(),
        encoding="utf-8",
    )
    plan = sync_plan(
        tmp_path,
        items=(
            PlannedPage("index.md", root, "111111", "Home", None, None),
            PlannedPage("guide/index.md", child, "222222", "Guide", None, "index.md"),
        ),
    )
    client = FakeSyncClient(
        page_errors={"222222": McpToolError("page 222222 not found")}
    )

    with pytest.raises(RuntimeError, match="guide/index.md.*222222.*--recreate-stale-pages"):
        await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert client.calls == [("get_page", "111111"), ("get_page", "222222")]
```

- [ ] **Step 2: Run validation tests and confirm failure**

Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/confluence_publisher/test_sync.py -q -p no:cacheprovider
```

Expected: FAIL because `sync_project()` does not exist.

- [ ] **Step 3: Implement validation helpers**

In `src/confluence_publisher/sync.py`, add:

```python
from datetime import UTC, datetime
from urllib.parse import quote

from .manifest import ManifestPageSyncState, write_manifest_sync_state
from .mcp_atlassian import McpToolError
from .models import PageInfo
```

Add root item and URL helpers:

```python
def _root_item(plan: PublishPlan) -> PlannedPage:
    if plan.root_page_id is not None:
        for item in plan.items:
            if item.page_id == plan.root_page_id:
                return item
    if plan.root_url is not None:
        for item in plan.items:
            if item.url == plan.root_url:
                return item
    if not plan.items:
        raise RuntimeError("Cannot sync project without pages")
    return plan.items[0]


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fallback_page_url(*, space_key: str | None, page_id: str, title: str) -> str | None:
    if space_key is None:
        return None
    encoded_space = quote(space_key, safe="~")
    encoded_title = quote(title.replace(" ", "+"), safe="+")
    return f"/spaces/{encoded_space}/pages/{page_id}/{encoded_title}"
```

Add page state checker:

```python
async def _readable_page_ids(plan: PublishPlan, *, client) -> dict[str, bool]:
    checked: dict[str, bool] = {}
    for item in plan.items:
        if item.page_id is None or item.page_id in checked:
            continue
        try:
            await client.get_page(item.page_id)
        except Exception:
            checked[item.page_id] = False
        else:
            checked[item.page_id] = True
    return checked
```

Add initial `sync_project()` that validates and publishes readable changed pages:

```python
async def sync_project(plan: PublishPlan, *, client, dry_run: bool, mmdc_bin: str) -> int:
    if not plan.sync_enabled:
        raise RuntimeError("sync_project requires a sync-enabled plan")
    if plan.space is None:
        raise RuntimeError("Cannot sync project without a Confluence space")

    root = _root_item(plan)
    if root.page_id is not None:
        try:
            await client.get_page(root.page_id)
        except Exception as exc:
            if not plan.recreate_missing_root:
                raise RuntimeError(
                    f"Cannot sync project because root page {root.page_id} for "
                    f"{root.source_path} is not readable. Enable --recreate-missing-root "
                    "or set sync.recreate_missing_root: true."
                ) from exc
            if plan.root_parent_page_id is None:
                raise RuntimeError(
                    "Cannot recreate missing root without root.parent_page_id in manifest"
                ) from exc
            try:
                await client.get_page(plan.root_parent_page_id)
            except Exception as parent_exc:
                raise RuntimeError(
                    f"Cannot recreate missing root because root.parent_page_id "
                    f"{plan.root_parent_page_id} is not readable: {parent_exc}"
                ) from parent_exc
            return await _recreate_full_tree(plan, client=client, dry_run=dry_run, mmdc_bin=mmdc_bin)

    readable = await _readable_page_ids(plan, client=client)
    stale_pages = [
        item
        for item in plan.items
        if item.page_id is not None and readable.get(item.page_id) is False
    ]
    if stale_pages and not plan.recreate_stale_pages:
        details = ", ".join(f"{item.source_path} ({item.page_id})" for item in stale_pages)
        raise RuntimeError(
            "Cannot sync project because existing page IDs are not readable: "
            f"{details}. Enable --recreate-stale-pages or set sync.recreate_stale_pages: true."
        )

    prepared_pages = prepare_sync_pages(plan, mmdc_bin=mmdc_bin)
    return await _publish_prepared_sync_pages(
        plan,
        prepared_pages=prepared_pages,
        client=client,
        dry_run=dry_run,
        page_ids={item.source_path: item.page_id for item in plan.items if item.page_id},
    )
```

Add interim functions at the bottom so validation tests can pass for non-recreate paths:

```python
async def _recreate_full_tree(plan: PublishPlan, *, client, dry_run: bool, mmdc_bin: str) -> int:
    raise RuntimeError("Full tree recreation requires the root recovery task")


async def _publish_prepared_sync_pages(
    plan: PublishPlan,
    *,
    prepared_pages: dict[str, PreparedSyncPage],
    client,
    dry_run: bool,
    page_ids: dict[str, str],
) -> int:
    for source_path, prepared in prepared_pages.items():
        page_id = page_ids.get(source_path)
        if page_id is None:
            continue
        if dry_run:
            print()
            print(f"Storage preview for {source_path}:")
            print(prepared.storage[:1200])
            continue
        for rendered in prepared.rendered_diagrams:
            await client.upload_attachment(page_id, rendered.output_path)
        await client.update_page_storage(
            page_id,
            prepared.item.title,
            prepared.storage,
            version_comment=plan.version_comment,
        )
    return 0
```

- [ ] **Step 4: Run validation tests and commit**

Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/confluence_publisher/test_sync.py -q -p no:cacheprovider
```

Expected: PASS for hash and validation tests.

Commit:

```bash
git add src/confluence_publisher/sync.py tests/confluence_publisher/test_sync.py
git commit -m "Validate sync page state"
```

## Task 5: Root Recreation, Stale Page Recreation, and Manifest Writes

**Files:**
- Modify: `src/confluence_publisher/sync.py`
- Test: `tests/confluence_publisher/test_sync.py`

- [ ] **Step 1: Add failing root recreation tests**

Append these tests to `tests/confluence_publisher/test_sync.py`:

```python
@pytest.mark.asyncio
async def test_sync_project_recreates_missing_root_and_full_tree(tmp_path):
    from confluence_publisher.manifest import load_manifest
    from confluence_publisher.sync import sync_project

    guide_dir = tmp_path / "guide"
    guide_dir.mkdir()
    overview = tmp_path / "00-overview.md"
    guide = guide_dir / "index.md"
    overview.write_text("# Overview\n\nPlain text.\n", encoding="utf-8")
    guide.write_text("# Guide\n\nPlain text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
root:
  parent_page_id: "2163738446"
  page_id: "old-root"
pages:
  00-overview.md:
    page_id: "old-root"
    title: "Overview"
  guide/index.md:
    page_id: "old-guide"
    title: "Guide"
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
            PlannedPage("00-overview.md", overview, "old-root", "Overview", None, None),
            PlannedPage("guide/index.md", guide, "old-guide", "Guide", None, None),
        ),
        space="~6393203da593cb822e995262",
        root_page_id="old-root",
        root_parent_page_id="2163738446",
        sync_enabled=True,
        recreate_missing_root=True,
    )
    client = FakeSyncClient(
        page_errors={"old-root": McpToolError("page old-root not found")}
    )

    result = await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert result == 0
    assert client.calls[:5] == [
        ("get_page", "old-root"),
        ("get_page", "2163738446"),
        ("search_pages_by_title", "~6393203da593cb822e995262", "Overview"),
        ("search_pages_by_title", "~6393203da593cb822e995262", "Guide"),
        ("create_page", "~6393203da593cb822e995262", "Overview", "2163738446", "800001"),
    ]
    assert ("create_page", "~6393203da593cb822e995262", "Guide", "800001", "800002") in client.calls
    assert ("update_page_storage", "800001", "Overview", "Sync docs") in client.calls
    assert ("update_page_storage", "800002", "Guide", "Sync docs") in client.calls
    manifest = load_manifest(manifest_path)
    assert manifest.root_page_id == "800001"
    assert manifest.pages["00-overview.md"].page_id == "800001"
    assert manifest.pages["guide/index.md"].page_id == "800002"
    assert manifest.pages["00-overview.md"].content_hash.startswith("sha256:")
    assert manifest.pages["guide/index.md"].last_synced_at.endswith("Z")


@pytest.mark.asyncio
async def test_sync_project_root_recreation_title_collision_blocks_before_create(tmp_path):
    from confluence_publisher.sync import sync_project

    source = tmp_path / "index.md"
    source.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
root:
  parent_page_id: "2163738446"
  page_id: "old-root"
pages:
  index.md:
    page_id: "old-root"
    title: "Home"
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
        items=(PlannedPage("index.md", source, "old-root", "Home", None, None),),
        space="~6393203da593cb822e995262",
        root_page_id="old-root",
        root_parent_page_id="2163738446",
        sync_enabled=True,
        recreate_missing_root=True,
    )
    client = FakeSyncClient(
        page_errors={"old-root": McpToolError("page old-root not found")},
        search_results={"Home": [PageInfo(page_id="999999", title="Home")]},
    )

    with pytest.raises(RuntimeError, match="titles already exist.*Home.*999999"):
        await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert ("create_page", "~6393203da593cb822e995262", "Home", "2163738446", "800001") not in client.calls
```

- [ ] **Step 2: Add failing stale page recreation test**

Append:

```python
@pytest.mark.asyncio
async def test_sync_project_recreates_stale_child_page(tmp_path):
    from confluence_publisher.manifest import load_manifest
    from confluence_publisher.sync import sync_project

    guide_dir = tmp_path / "guide"
    guide_dir.mkdir()
    root = tmp_path / "index.md"
    guide = guide_dir / "index.md"
    root.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    guide.write_text("# Guide\n\nPlain text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
root:
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
  guide/index.md:
    page_id: "222222"
    title: "Guide"
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
            PlannedPage("index.md", root, "111111", "Home", None, None),
            PlannedPage("guide/index.md", guide, "222222", "Guide", None, "index.md"),
        ),
        space="~6393203da593cb822e995262",
        root_page_id="111111",
        sync_enabled=True,
        recreate_stale_pages=True,
    )
    client = FakeSyncClient(
        page_errors={"222222": McpToolError("page 222222 not found")}
    )

    result = await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert result == 0
    assert ("search_pages_by_title", "~6393203da593cb822e995262", "Guide") in client.calls
    assert ("create_page", "~6393203da593cb822e995262", "Guide", "111111", "800001") in client.calls
    assert ("update_page_storage", "800001", "Guide", "Sync docs") in client.calls
    manifest = load_manifest(manifest_path)
    assert manifest.pages["guide/index.md"].page_id == "800001"
    assert manifest.pages["guide/index.md"].content_hash.startswith("sha256:")
```

- [ ] **Step 3: Run recreation tests and confirm failure**

Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/confluence_publisher/test_sync.py -q -p no:cacheprovider
```

Expected: FAIL because full-tree and stale-page recreation are not wired.

- [ ] **Step 4: Implement create preflight and page creation**

In `src/confluence_publisher/sync.py`, add:

```python
async def _preflight_titles(plan: PublishPlan, *, client, items: list[PlannedPage]) -> None:
    if plan.space is None:
        raise RuntimeError("Cannot create Confluence pages without a space")
    collisions: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item.title in seen:
            continue
        seen.add(item.title)
        matches = await client.search_pages_by_title(space_key=plan.space, title=item.title)
        if matches:
            pages = ", ".join(f"{page.page_id} ({page.title})" for page in matches)
            collisions.append(f"{item.source_path}: {item.title} already exists as {pages}")
    if collisions:
        raise RuntimeError(
            "Cannot create Confluence pages because titles already exist in the target space: "
            + "; ".join(collisions)
        )
```

Add:

```python
async def _create_page(
    plan: PublishPlan,
    *,
    client,
    item: PlannedPage,
    parent_id: str,
    prepared: PreparedSyncPage,
) -> PageInfo:
    if plan.space is None:
        raise RuntimeError("Cannot create Confluence page without a space")
    return await client.create_page(
        space_key=plan.space,
        title=item.title,
        parent_id=parent_id,
        storage=prepared.storage,
    )
```

Add parent resolution:

```python
def _parent_id_for(
    item: PlannedPage,
    *,
    root_item: PlannedPage,
    page_ids: dict[str, str],
) -> str:
    if item.parent_source_path is not None:
        return page_ids[item.parent_source_path]
    if item.source_path == root_item.source_path:
        raise RuntimeError("Root page parent must be supplied by root.parent_page_id")
    return page_ids[root_item.source_path]
```

- [ ] **Step 5: Implement full-tree recreation and stale page recreation**

Replace the `_recreate_full_tree()` stub:

```python
async def _recreate_full_tree(plan: PublishPlan, *, client, dry_run: bool, mmdc_bin: str) -> int:
    if plan.root_parent_page_id is None:
        raise RuntimeError("Cannot recreate missing root without root.parent_page_id in manifest")
    root = _root_item(plan)
    prepared_pages = prepare_sync_pages(plan, mmdc_bin=mmdc_bin)
    await _preflight_titles(plan, client=client, items=list(plan.items))
    if dry_run:
        for source_path, prepared in prepared_pages.items():
            print()
            print(f"Storage preview for {source_path}:")
            print(prepared.storage[:1200])
        return 0

    page_ids: dict[str, str] = {}
    root_prepared = prepared_pages[root.source_path]
    created_root = await _create_page(
        plan,
        client=client,
        item=root,
        parent_id=plan.root_parent_page_id,
        prepared=root_prepared,
    )
    page_ids[root.source_path] = created_root.page_id

    for item in plan.items:
        if item.source_path == root.source_path:
            continue
        parent_id = _parent_id_for(item, root_item=root, page_ids=page_ids)
        created = await _create_page(
            plan,
            client=client,
            item=item,
            parent_id=parent_id,
            prepared=prepared_pages[item.source_path],
        )
        page_ids[item.source_path] = created.page_id

    return await _publish_prepared_sync_pages(
        plan,
        prepared_pages=prepared_pages,
        client=client,
        dry_run=False,
        page_ids=page_ids,
        root_page_id=page_ids[root.source_path],
        root_url=_created_url(created_root, plan=plan, item=root),
    )
```

Add URL helper:

```python
def _created_url(page: PageInfo, *, plan: PublishPlan, item: PlannedPage) -> str | None:
    return page.url or _fallback_page_url(
        space_key=plan.space,
        page_id=page.page_id,
        title=item.title,
    )
```

Update `sync_project()` after stale page validation:

```python
    prepared_pages = prepare_sync_pages(plan, mmdc_bin=mmdc_bin)
    page_ids = {item.source_path: item.page_id for item in plan.items if item.page_id is not None}
    created_state: dict[str, PageInfo] = {}
    if stale_pages:
        await _preflight_titles(plan, client=client, items=stale_pages)
        for item in stale_pages:
            parent_id = (
                page_ids[item.parent_source_path]
                if item.parent_source_path is not None
                else plan.root_page_id
            )
            if parent_id is None:
                raise RuntimeError(f"Cannot recreate {item.source_path} without a parent page")
            created = await _create_page(
                plan,
                client=client,
                item=item,
                parent_id=parent_id,
                prepared=prepared_pages[item.source_path],
            )
            page_ids[item.source_path] = created.page_id
            created_state[item.source_path] = created
```

Pass `created_state` to `_publish_prepared_sync_pages()`.

- [ ] **Step 6: Implement manifest writes from sync**

Change `_publish_prepared_sync_pages()` signature:

```python
async def _publish_prepared_sync_pages(
    plan: PublishPlan,
    *,
    prepared_pages: dict[str, PreparedSyncPage],
    client,
    dry_run: bool,
    page_ids: dict[str, str],
    created_pages: dict[str, PageInfo] | None = None,
    root_page_id: str | None = None,
    root_url: str | None = None,
) -> int:
```

Inside it, after successful publish, write manifest state:

```python
    created_pages = created_pages or {}
    synced_at = _now_iso()
    manifest_pages: dict[str, ManifestPageSyncState] = {}
    for source_path, prepared in prepared_pages.items():
        page_id = page_ids.get(source_path)
        if page_id is None:
            continue
        created = created_pages.get(source_path)
        manifest_pages[source_path] = ManifestPageSyncState(
            page_id=page_id,
            url=(
                _created_url(created, plan=plan, item=prepared.item)
                if created is not None
                else prepared.item.url
            ),
            content_hash=prepared.content_hash,
            last_synced_at=synced_at,
        )

    if not dry_run:
        write_manifest_sync_state(
            plan.manifest_path,
            root_page_id=root_page_id,
            root_url=root_url,
            pages=manifest_pages,
        )
    return 0
```

When publishing live, use:

```python
        for rendered in prepared.rendered_diagrams:
            await client.upload_attachment(page_id, rendered.output_path)
        await client.update_page_storage(
            page_id,
            prepared.item.title,
            prepared.storage,
            version_comment=plan.version_comment,
        )
```

- [ ] **Step 7: Run sync recreation tests and commit**

Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/confluence_publisher/test_sync.py -q -p no:cacheprovider
```

Expected: PASS.

Commit:

```bash
git add src/confluence_publisher/sync.py tests/confluence_publisher/test_sync.py
git commit -m "Reconcile missing Confluence pages"
```

## Task 6: Hash-Based Skip for Unchanged Pages

**Files:**
- Modify: `src/confluence_publisher/sync.py`
- Test: `tests/confluence_publisher/test_sync.py`

- [ ] **Step 1: Add failing skip and changed-update tests**

Append:

```python
@pytest.mark.asyncio
async def test_sync_project_skips_page_with_matching_hash(tmp_path):
    from confluence_publisher.manifest import load_manifest
    from confluence_publisher.sync import prepare_sync_pages, sync_project

    source = tmp_path / "index.md"
    source.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    base_item = PlannedPage("index.md", source, "111111", "Home", None, None)
    base_plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=tmp_path / ".confluence-publish.yml",
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(base_item,),
        space="~6393203da593cb822e995262",
        root_page_id="111111",
        sync_enabled=True,
    )
    content_hash = prepare_sync_pages(base_plan, mmdc_bin="mmdc")["index.md"].content_hash
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        f"""
space: "~6393203da593cb822e995262"
root:
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
    content_hash: "{content_hash}"
    last_synced_at: "2026-04-28T12:00:00Z"
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
                "index.md",
                source,
                "111111",
                "Home",
                None,
                None,
                content_hash=content_hash,
                last_synced_at="2026-04-28T12:00:00Z",
            ),
        ),
        space="~6393203da593cb822e995262",
        root_page_id="111111",
        sync_enabled=True,
    )
    client = FakeSyncClient()

    result = await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert result == 0
    assert client.calls == [("get_page", "111111")]
    manifest = load_manifest(manifest_path)
    assert manifest.pages["index.md"].last_synced_at == "2026-04-28T12:00:00Z"


@pytest.mark.asyncio
async def test_sync_project_updates_page_with_changed_hash(tmp_path):
    from confluence_publisher.manifest import load_manifest
    from confluence_publisher.sync import sync_project

    source = tmp_path / "index.md"
    source.write_text("# Home\n\nChanged text.\n", encoding="utf-8")
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
root:
  page_id: "111111"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
    content_hash: "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    last_synced_at: "2026-04-28T12:00:00Z"
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
                "index.md",
                source,
                "111111",
                "Home",
                None,
                None,
                content_hash="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                last_synced_at="2026-04-28T12:00:00Z",
            ),
        ),
        space="~6393203da593cb822e995262",
        root_page_id="111111",
        sync_enabled=True,
    )
    client = FakeSyncClient()

    result = await sync_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert result == 0
    assert ("update_page_storage", "111111", "Home", "Sync docs") in client.calls
    manifest = load_manifest(manifest_path)
    assert manifest.pages["index.md"].content_hash.startswith("sha256:")
    assert manifest.pages["index.md"].content_hash != "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert manifest.pages["index.md"].last_synced_at != "2026-04-28T12:00:00Z"
```

- [ ] **Step 2: Run skip tests and confirm failure**

Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/confluence_publisher/test_sync.py -q -p no:cacheprovider
```

Expected: FAIL because `_publish_prepared_sync_pages()` updates every readable page.

- [ ] **Step 3: Use planned page hashes during publishing**

`PlannedPage.content_hash` and `PlannedPage.last_synced_at` were added in Task 2. In `src/confluence_publisher/sync.py`, compare `prepared.item.content_hash` with `prepared.content_hash` inside `_publish_prepared_sync_pages()`.

- [ ] **Step 4: Skip matching hashes**

In `_publish_prepared_sync_pages()`, before uploading attachments:

```python
        if (
            prepared.item.content_hash == prepared.content_hash
            and source_path not in created_pages
        ):
            continue
```

Only include pages in `manifest_pages` when they were created or updated:

```python
        changed = prepared.item.content_hash != prepared.content_hash or source_path in created_pages
        if not changed:
            continue
```

For skipped pages, do not rewrite `last_synced_at`.

- [ ] **Step 5: Run skip tests and commit**

Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/confluence_publisher/test_sync.py tests/confluence_publisher/test_planner.py tests/confluence_publisher/test_batch.py -q -p no:cacheprovider
```

Expected: PASS.

Commit:

```bash
git add src/confluence_publisher/planner.py src/confluence_publisher/sync.py tests/confluence_publisher/test_sync.py tests/confluence_publisher/test_planner.py tests/confluence_publisher/test_batch.py
git commit -m "Skip unchanged synced pages"
```

## Task 7: CLI Integration and Project-Mode Coverage

**Files:**
- Modify: `src/confluence_publisher/cli.py`
- Modify: `tests/confluence_publisher/test_cli.py`

- [ ] **Step 1: Add failing CLI sync tests**

Add to `tests/confluence_publisher/test_cli.py`:

```python
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
async def test_run_async_project_plan_only_prints_manifest_sync_settings(
    tmp_path, monkeypatch, capsys
):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
space: "~6393203da593cb822e995262"
sync:
  enabled: true
  recreate_missing_root: true
root:
  parent_page_id: "2163738446"
pages:
  index.md:
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
    assert "Sync: enabled" in output
    assert "Recreate missing root: yes" in output
    assert "index.md -> <missing>" in output
```

- [ ] **Step 2: Run CLI tests and confirm failure**

Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/confluence_publisher/test_cli.py -q -p no:cacheprovider
```

Expected: FAIL because `run_project_mode()` still always calls `publish_project()`.

- [ ] **Step 3: Route sync-enabled project mode**

In `src/confluence_publisher/cli.py`, change the project imports:

```python
    from .batch import format_publish_plan, publish_project
    from .sync import sync_project
```

In dry-run branch:

```python
        if plan.sync_enabled:
            return await sync_project(
                plan,
                client=DryRunClient(),
                dry_run=True,
                mmdc_bin=args.mmdc_bin,
            )
        return await publish_project(
            plan,
            client=DryRunClient(),
            dry_run=True,
            mmdc_bin=args.mmdc_bin,
        )
```

In live branch:

```python
    async with connect_mcp_atlassian() as client:
        if plan.sync_enabled:
            return await sync_project(
                plan,
                client=client,
                dry_run=False,
                mmdc_bin=args.mmdc_bin,
            )
        return await publish_project(
            plan,
            client=client,
            dry_run=False,
            mmdc_bin=args.mmdc_bin,
        )
```

- [ ] **Step 4: Run CLI tests and commit**

Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/confluence_publisher/test_cli.py -q -p no:cacheprovider
```

Expected: PASS.

Commit:

```bash
git add src/confluence_publisher/cli.py tests/confluence_publisher/test_cli.py
git commit -m "Route project sync publishing"
```

## Task 8: Documentation and Final Verification

**Files:**
- Modify: `README.md`
- Modify: `DESIGN.md`

- [ ] **Step 1: Update README usage**

Add a section to `README.md` near project publishing examples:

```markdown
### Sync Mode

Project manifests can enable automatic sync:

```yaml
sync:
  enabled: true
  recreate_missing_root: true
  recreate_stale_pages: true
root:
  parent_page_id: "2163738446"
```

With this config, `publish-confluence --project <docs-root> --yes` checks the
current Confluence state before writing. If the project root was deleted and
`recreate_missing_root` is enabled, the publisher recreates the root under
`root.parent_page_id`, creates the project tree, publishes content, and stores
new page IDs, URLs, hashes, and timestamps in `.confluence-publish.yml`.

The publisher does not delete or move orphaned Confluence pages. If an orphaned
page title still exists in the same space, sync fails before creation because
Confluence page titles are unique in the space.
```
```

- [ ] **Step 2: Update DESIGN sync section**

Add this to `DESIGN.md` in the project publishing design area:

```markdown
## Sync Recovery

Sync mode is separate from legacy `--create-missing-pages`. The legacy mode
creates only entries that lack `page_id`. Sync mode validates the root page,
validates existing page IDs, optionally recreates missing pages, computes content
hashes from rendered storage, and skips unchanged page updates.

Recovery policies can be set in the manifest or via CLI flags. The effective
policy is the union of both sources, while live writes still require `--yes`.
```

- [ ] **Step 3: Run the full publisher test suite**

Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:cacheprovider
```

Expected: all tests pass.

- [ ] **Step 4: Run safe plan-only validation for Whitelabel**

Run:

```bash
uv run --project /home/akhadiev/Documents/confluence-markdown-publisher publish-confluence \
  --project /home/akhadiev/Documents/iow/devops/gitlab.iponweb.net/infrastructure/iowops/projects/NEXT_Documenttaion/Whitelabel_NEXT_Deployment/en \
  --plan-only
```

Expected: prints a plan without connecting to MCP or writing to Confluence. The output includes `Sync: enabled` only after the Whitelabel manifest is updated separately.

- [ ] **Step 5: Commit documentation**

Commit:

```bash
git add README.md DESIGN.md
git commit -m "Document sync recovery mode"
```

## Task 9: Whitelabel Manifest Recovery Config

**Files:**
- Modify only after publisher tests pass: `/home/akhadiev/Documents/iow/devops/gitlab.iponweb.net/infrastructure/iowops/projects/NEXT_Documenttaion/Whitelabel_NEXT_Deployment/en/.confluence-publish.yml`

- [ ] **Step 1: Add sync defaults to Whitelabel manifest**

Add or update:

```yaml
sync:
  enabled: true
  recreate_missing_root: true
  recreate_stale_pages: true
root:
  parent_page_id: "2163738446"
```

Keep existing `space`, titles, page IDs, URLs, and the `Post-Deployment Checks` title.

- [ ] **Step 2: Run safe plan-only validation**

Run:

```bash
uv run --project /home/akhadiev/Documents/confluence-markdown-publisher publish-confluence \
  --project /home/akhadiev/Documents/iow/devops/gitlab.iponweb.net/infrastructure/iowops/projects/NEXT_Documenttaion/Whitelabel_NEXT_Deployment/en \
  --plan-only
```

Expected: output includes:

```text
Sync: enabled
Recreate missing root: yes
Recreate stale pages: yes
```

- [ ] **Step 3: Do not run live publish without explicit approval**

Do not run this command until the user confirms live writes:

```bash
set -a; source ~/.uagent/.env; set +a; \
CONFLUENCE_URL="$CRITEO_CONFLUENCE_BASE_URL/wiki" \
CONFLUENCE_USERNAME="$CRITEO_CONFLUENCE_USERNAME" \
CONFLUENCE_API_TOKEN="$CRITEO_CONFLUENCE_API_TOKEN" \
uv run --project /home/akhadiev/Documents/confluence-markdown-publisher publish-confluence \
  --project /home/akhadiev/Documents/iow/devops/gitlab.iponweb.net/infrastructure/iowops/projects/NEXT_Documenttaion/Whitelabel_NEXT_Deployment/en \
  --mmdc-bin /tmp/next-docs-mmdc/mmdc \
  --yes
```

## Final Verification

- [ ] Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run pytest -q -p no:cacheprovider
```

Expected: all tests pass.

- [ ] Run:

```bash
uv run --project /home/akhadiev/Documents/confluence-markdown-publisher publish-confluence \
  --project /home/akhadiev/Documents/iow/devops/gitlab.iponweb.net/infrastructure/iowops/projects/NEXT_Documenttaion/Whitelabel_NEXT_Deployment/en \
  --plan-only
```

Expected: no MCP writes, no credential output, plan prints effective sync policy.

- [ ] Run:

```bash
git status --short
```

Expected: only intentional publisher changes and the optional Whitelabel manifest change are present.
