# Sync State Machine Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor Confluence push/recovery selection into an explicit, pure sync execution plan so recovery behavior is easier to test and less likely to regress.

**Architecture:** Keep the public `sync_project()` API and CLI behavior unchanged. Add internal sync decision dataclasses/enums in `sync.py`, test `build_sync_execution_plan()` without MCP or rendering, then route the existing effectful helpers through the generated execution plan.

**Tech Stack:** Python 3.11+, dataclasses, `enum.StrEnum`, pytest, pytest-asyncio, uv, existing `confluence_publisher` modules.

---

## Workflow Requirements

- Use `superpowers:test-driven-development` for each task: write the failing test, run it, implement the smallest change, run it passing.
- Prefer `superpowers:subagent-driven-development` when executing this plan. Dispatch one worker per task and review each task before continuing.
- Commit after each task with a short English message. Do not mention AI tools in commits.
- Do not run live Confluence publishing.
- Keep implementation changes scoped to `src/confluence_publisher` and tests under `tests/confluence_publisher`.

## File Map

- Modify `src/confluence_publisher/sync.py`: add sync decision model, pure execution-plan builder, remote-state collection helpers, and refactor `sync_project()` to execute the plan.
- Modify `tests/confluence_publisher/test_sync.py`: add focused pure decision tests while keeping the existing integration-style sync tests.
- No documentation contract changes are expected during implementation.

## Task 1: Add Pure Sync Decision Model

**Files:**
- Modify: `tests/confluence_publisher/test_sync.py`
- Modify: `src/confluence_publisher/sync.py`

- [ ] **Step 1: Add failing tests for basic execution-plan modes**

In `tests/confluence_publisher/test_sync.py`, extend the import from `confluence_publisher.sync` near the top of the file:

```python
from confluence_publisher.sync import (
    CONTENT_HASH_VERSION,
    PreparedSyncPage,
    SyncExecutionMode,
    SyncRemoteState,
    build_sync_execution_plan,
    compute_content_hash,
    prepare_sync_pages,
)
```

Then add these tests after `sync_plan()`:

```python
def test_build_sync_execution_plan_returns_preview_for_dry_run(tmp_path):
    plan = sync_plan(tmp_path)
    state = SyncRemoteState(
        root_readable=None,
        readable_by_page_id={},
        missing_pages=(),
        stale_pages=(),
    )

    execution = build_sync_execution_plan(plan, remote_state=state, dry_run=True)

    assert execution.mode is SyncExecutionMode.PREVIEW
    assert execution.root_item.source_path == "index.md"
    assert execution.prepared_source_paths == ("index.md",)
    assert execution.create_source_paths == ()
    assert execution.publish_source_paths == ("index.md",)
    assert execution.initial_page_ids == {}
    assert execution.requires_title_preflight is False


def test_build_sync_execution_plan_returns_publish_existing_for_readable_pages(tmp_path):
    plan = sync_plan(tmp_path)
    state = SyncRemoteState(
        root_readable=True,
        readable_by_page_id={"111111": True},
        missing_pages=(),
        stale_pages=(),
    )

    execution = build_sync_execution_plan(plan, remote_state=state, dry_run=False)

    assert execution.mode is SyncExecutionMode.PUBLISH_EXISTING
    assert execution.root_item.source_path == "index.md"
    assert execution.prepared_source_paths == ("index.md",)
    assert execution.create_source_paths == ()
    assert execution.publish_source_paths == ("index.md",)
    assert execution.initial_page_ids == {"index.md": "111111"}
    assert execution.requires_title_preflight is False
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_returns_preview_for_dry_run tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_returns_publish_existing_for_readable_pages -v
```

Expected: FAIL during import with `ImportError` or `AttributeError` because `SyncExecutionMode`, `SyncRemoteState`, and `build_sync_execution_plan` do not exist yet.

- [ ] **Step 3: Add the minimal model and builder**

In `src/confluence_publisher/sync.py`, add this import:

```python
from enum import StrEnum
```

Add these definitions after `PreparedSyncPage`:

```python
class SyncExecutionMode(StrEnum):
    PREVIEW = "preview"
    PUBLISH_EXISTING = "publish_existing"
    CREATE_MISSING = "create_missing"
    RECREATE_ROOT_TREE = "recreate_root_tree"
    RECREATE_STALE_PAGES = "recreate_stale_pages"


@dataclass(frozen=True)
class SyncRemoteState:
    root_readable: bool | None
    readable_by_page_id: dict[str, bool]
    missing_pages: tuple[PlannedPage, ...]
    stale_pages: tuple[PlannedPage, ...]


@dataclass(frozen=True)
class SyncExecutionPlan:
    mode: SyncExecutionMode
    root_item: PlannedPage
    prepared_source_paths: tuple[str, ...]
    create_source_paths: tuple[str, ...]
    publish_source_paths: tuple[str, ...]
    initial_page_ids: dict[str, str]
    requires_title_preflight: bool


@dataclass(frozen=True)
class SyncCreatedPage:
    source_path: str
    page: PageInfo
    is_root: bool = False
```

Add these helpers after `_requires_space_for_creation()`:

```python
def _all_source_paths(plan: PublishPlan) -> tuple[str, ...]:
    return tuple(item.source_path for item in plan.items)


def _initial_page_ids(plan: PublishPlan) -> dict[str, str]:
    return {item.source_path: item.page_id for item in plan.items if item.page_id}


def build_sync_execution_plan(
    plan: PublishPlan,
    *,
    remote_state: SyncRemoteState,
    dry_run: bool,
) -> SyncExecutionPlan:
    root = _root_item(plan)
    all_source_paths = _all_source_paths(plan)

    if dry_run:
        return SyncExecutionPlan(
            mode=SyncExecutionMode.PREVIEW,
            root_item=root,
            prepared_source_paths=all_source_paths,
            create_source_paths=(),
            publish_source_paths=all_source_paths,
            initial_page_ids={},
            requires_title_preflight=False,
        )

    return SyncExecutionPlan(
        mode=SyncExecutionMode.PUBLISH_EXISTING,
        root_item=root,
        prepared_source_paths=all_source_paths,
        create_source_paths=(),
        publish_source_paths=all_source_paths,
        initial_page_ids=_initial_page_ids(plan),
        requires_title_preflight=False,
    )
```

- [ ] **Step 4: Run the task tests and verify they pass**

Run:

```bash
uv run pytest tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_returns_preview_for_dry_run tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_returns_publish_existing_for_readable_pages -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/confluence_publisher/sync.py tests/confluence_publisher/test_sync.py
git commit -m "Add sync execution plan model"
```

## Task 2: Cover Missing And Recovery Decision Modes

**Files:**
- Modify: `tests/confluence_publisher/test_sync.py`
- Modify: `src/confluence_publisher/sync.py`

- [ ] **Step 1: Add failing tests for missing pages and recovery decisions**

Add these tests after the Task 1 execution-plan tests:

```python
def test_build_sync_execution_plan_rejects_missing_pages_without_create_policy(tmp_path):
    child_dir = tmp_path / "guide"
    child_dir.mkdir()
    root = tmp_path / "index.md"
    child = child_dir / "index.md"
    root.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    child.write_text("# Guide\n\nPlain text.\n", encoding="utf-8")
    missing_child = PlannedPage("guide/index.md", child, None, "Guide", None, "index.md")
    plan = sync_plan(
        tmp_path,
        items=(
            PlannedPage("index.md", root, "111111", "Home", None, None),
            missing_child,
        ),
    )
    state = SyncRemoteState(
        root_readable=True,
        readable_by_page_id={"111111": True},
        missing_pages=(missing_child,),
        stale_pages=(),
    )

    with pytest.raises(RuntimeError, match="--create-missing-pages.*guide/index.md"):
        build_sync_execution_plan(plan, remote_state=state, dry_run=False)


def test_build_sync_execution_plan_creates_missing_child_under_root(tmp_path):
    child_dir = tmp_path / "guide"
    child_dir.mkdir()
    root = tmp_path / "index.md"
    child = child_dir / "index.md"
    root.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    child.write_text("# Guide\n\nPlain text.\n", encoding="utf-8")
    missing_child = PlannedPage("guide/index.md", child, None, "Guide", None, "index.md")
    plan = sync_plan(
        tmp_path,
        items=(
            PlannedPage("index.md", root, "111111", "Home", None, None),
            missing_child,
        ),
    )
    plan = PublishPlan(**{**plan.__dict__, "create_missing_pages": True})
    state = SyncRemoteState(
        root_readable=True,
        readable_by_page_id={"111111": True},
        missing_pages=(missing_child,),
        stale_pages=(),
    )

    execution = build_sync_execution_plan(plan, remote_state=state, dry_run=False)

    assert execution.mode is SyncExecutionMode.CREATE_MISSING
    assert execution.create_source_paths == ("guide/index.md",)
    assert execution.initial_page_ids == {"index.md": "111111"}
    assert execution.requires_title_preflight is True


def test_build_sync_execution_plan_recreates_missing_root_when_policy_enabled(tmp_path):
    plan = sync_plan(tmp_path)
    plan = PublishPlan(**{**plan.__dict__, "recreate_missing_root": True})
    state = SyncRemoteState(
        root_readable=False,
        readable_by_page_id={},
        missing_pages=(),
        stale_pages=(),
    )

    execution = build_sync_execution_plan(plan, remote_state=state, dry_run=False)

    assert execution.mode is SyncExecutionMode.RECREATE_ROOT_TREE
    assert execution.create_source_paths == ("index.md",)
    assert execution.initial_page_ids == {}
    assert execution.requires_title_preflight is True


def test_build_sync_execution_plan_rejects_missing_root_without_policy(tmp_path):
    plan = sync_plan(tmp_path)
    state = SyncRemoteState(
        root_readable=False,
        readable_by_page_id={},
        missing_pages=(),
        stale_pages=(),
    )

    with pytest.raises(RuntimeError, match="root page.*111111.*--recreate-missing-root"):
        build_sync_execution_plan(plan, remote_state=state, dry_run=False)


def test_build_sync_execution_plan_requires_parent_for_missing_root_recreation(tmp_path):
    plan = sync_plan(tmp_path, root_parent_page_id=None)
    plan = PublishPlan(**{**plan.__dict__, "recreate_missing_root": True})
    state = SyncRemoteState(
        root_readable=False,
        readable_by_page_id={},
        missing_pages=(),
        stale_pages=(),
    )

    with pytest.raises(RuntimeError, match="root.parent_page_id"):
        build_sync_execution_plan(plan, remote_state=state, dry_run=False)


def test_build_sync_execution_plan_recreates_stale_ancestor_with_descendants(tmp_path):
    guide_dir = tmp_path / "guide"
    guide_dir.mkdir()
    root = tmp_path / "index.md"
    guide = guide_dir / "index.md"
    details = guide_dir / "details.md"
    root.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    guide.write_text("# Guide\n\nPlain text.\n", encoding="utf-8")
    details.write_text("# Details\n\nPlain text.\n", encoding="utf-8")
    stale_guide = PlannedPage("guide/index.md", guide, "222222", "Guide", None, "index.md")
    plan = PublishPlan(
        project_root=tmp_path,
        manifest_path=tmp_path / ".confluence-publish.yml",
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(
            PlannedPage("index.md", root, "111111", "Home", None, None),
            stale_guide,
            PlannedPage("guide/details.md", details, "333333", "Details", None, "guide/index.md"),
        ),
        space="~6393203da593cb822e995262",
        root_page_id="111111",
        sync_enabled=True,
        recreate_stale_pages=True,
    )
    state = SyncRemoteState(
        root_readable=True,
        readable_by_page_id={"111111": True, "222222": False, "333333": True},
        missing_pages=(),
        stale_pages=(stale_guide,),
    )

    execution = build_sync_execution_plan(plan, remote_state=state, dry_run=False)

    assert execution.mode is SyncExecutionMode.RECREATE_STALE_PAGES
    assert execution.create_source_paths == ("guide/index.md", "guide/details.md")
    assert execution.initial_page_ids == {"index.md": "111111"}
    assert execution.requires_title_preflight is True
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_rejects_missing_pages_without_create_policy tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_creates_missing_child_under_root tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_recreates_missing_root_when_policy_enabled tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_rejects_missing_root_without_policy tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_requires_parent_for_missing_root_recreation tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_recreates_stale_ancestor_with_descendants -v
```

Expected: FAIL because `build_sync_execution_plan()` always returns `PUBLISH_EXISTING`.

- [ ] **Step 3: Implement missing-page and recovery decisions**

Replace `build_sync_execution_plan()` in `src/confluence_publisher/sync.py` with:

```python
def build_sync_execution_plan(
    plan: PublishPlan,
    *,
    remote_state: SyncRemoteState,
    dry_run: bool,
) -> SyncExecutionPlan:
    root = _root_item(plan)
    all_source_paths = _all_source_paths(plan)

    if remote_state.missing_pages and not plan.create_missing_pages:
        details = ", ".join(item.source_path for item in remote_state.missing_pages)
        raise RuntimeError(
            "Cannot create missing Confluence pages without --create-missing-pages: "
            f"{details}"
        )

    if plan.space is None and _requires_space_for_creation(plan):
        raise RuntimeError("Cannot sync project without a Confluence space")

    if dry_run:
        return SyncExecutionPlan(
            mode=SyncExecutionMode.PREVIEW,
            root_item=root,
            prepared_source_paths=all_source_paths,
            create_source_paths=(),
            publish_source_paths=all_source_paths,
            initial_page_ids={},
            requires_title_preflight=False,
        )

    if remote_state.root_readable is False:
        if not plan.recreate_missing_root:
            raise RuntimeError(
                f"Cannot sync project because root page {root.page_id} for "
                f"{root.source_path} is not readable. Enable --recreate-missing-root "
                "or set sync.recreate_missing_root: true."
            )
        if plan.root_parent_page_id is None:
            raise RuntimeError("Cannot recreate missing root without root.parent_page_id in manifest")
        return SyncExecutionPlan(
            mode=SyncExecutionMode.RECREATE_ROOT_TREE,
            root_item=root,
            prepared_source_paths=all_source_paths,
            create_source_paths=all_source_paths,
            publish_source_paths=all_source_paths,
            initial_page_ids={},
            requires_title_preflight=True,
        )

    if remote_state.stale_pages:
        if not plan.recreate_stale_pages:
            details = ", ".join(
                f"{item.source_path} ({item.page_id})" for item in remote_state.stale_pages
            )
            raise RuntimeError(
                "Cannot sync project because existing page IDs are not readable: "
                f"{details}. Enable --recreate-stale-pages or set sync.recreate_stale_pages: true."
            )
        create_items = _with_descendants(
            plan,
            list(remote_state.stale_pages + remote_state.missing_pages),
        )
        page_ids = _initial_page_ids(plan)
        for item in create_items:
            page_ids.pop(item.source_path, None)
        return SyncExecutionPlan(
            mode=SyncExecutionMode.RECREATE_STALE_PAGES,
            root_item=root,
            prepared_source_paths=all_source_paths,
            create_source_paths=tuple(item.source_path for item in create_items),
            publish_source_paths=all_source_paths,
            initial_page_ids=page_ids,
            requires_title_preflight=True,
        )

    if remote_state.missing_pages:
        root_page_id = _initial_page_ids(plan).get(root.source_path)
        if root_page_id is None and root not in remote_state.missing_pages:
            raise RuntimeError("Cannot create missing pages without a readable root page")
        create_items = _with_descendants(plan, list(remote_state.missing_pages))
        page_ids = _initial_page_ids(plan)
        for item in create_items:
            page_ids.pop(item.source_path, None)
        return SyncExecutionPlan(
            mode=SyncExecutionMode.CREATE_MISSING,
            root_item=root,
            prepared_source_paths=all_source_paths,
            create_source_paths=tuple(item.source_path for item in create_items),
            publish_source_paths=all_source_paths,
            initial_page_ids=page_ids,
            requires_title_preflight=True,
        )

    return SyncExecutionPlan(
        mode=SyncExecutionMode.PUBLISH_EXISTING,
        root_item=root,
        prepared_source_paths=all_source_paths,
        create_source_paths=(),
        publish_source_paths=all_source_paths,
        initial_page_ids=_initial_page_ids(plan),
        requires_title_preflight=False,
    )
```

- [ ] **Step 4: Run the pure decision tests**

Run:

```bash
uv run pytest tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_returns_preview_for_dry_run tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_returns_publish_existing_for_readable_pages tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_rejects_missing_pages_without_create_policy tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_creates_missing_child_under_root tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_recreates_missing_root_when_policy_enabled tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_rejects_missing_root_without_policy tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_requires_parent_for_missing_root_recreation tests/confluence_publisher/test_sync.py::test_build_sync_execution_plan_recreates_stale_ancestor_with_descendants -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/confluence_publisher/sync.py tests/confluence_publisher/test_sync.py
git commit -m "Plan sync recovery decisions explicitly"
```

## Task 3: Collect Remote State Separately From Execution

**Files:**
- Modify: `src/confluence_publisher/sync.py`
- Modify: `tests/confluence_publisher/test_sync.py`

- [ ] **Step 1: Add a focused async test for remote-state collection**

Add this test after the pure decision tests:

```python
@pytest.mark.asyncio
async def test_collect_sync_remote_state_marks_stale_pages_after_root_is_readable(tmp_path):
    from confluence_publisher.sync import collect_sync_remote_state

    child_dir = tmp_path / "guide"
    child_dir.mkdir()
    root = tmp_path / "index.md"
    child = child_dir / "index.md"
    root.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    child.write_text("# Guide\n\nPlain text.\n", encoding="utf-8")
    child_page = PlannedPage("guide/index.md", child, "222222", "Guide", None, "index.md")
    plan = sync_plan(
        tmp_path,
        items=(
            PlannedPage("index.md", root, "111111", "Home", None, None),
            child_page,
        ),
    )
    client = FakeSyncClient(page_errors={"222222": McpToolError("page 222222 not found")})

    state = await collect_sync_remote_state(plan, client=client)

    assert state.root_readable is True
    assert state.readable_by_page_id == {"111111": True, "222222": False}
    assert state.missing_pages == ()
    assert state.stale_pages == (child_page,)
    assert client.calls == [("get_page", "111111"), ("get_page", "222222")]
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
uv run pytest tests/confluence_publisher/test_sync.py::test_collect_sync_remote_state_marks_stale_pages_after_root_is_readable -v
```

Expected: FAIL because `collect_sync_remote_state` does not exist.

- [ ] **Step 3: Implement remote-state collection**

Add this helper after `build_sync_execution_plan()`:

```python
async def collect_sync_remote_state(plan: PublishPlan, *, client) -> SyncRemoteState:
    root = _root_item(plan)
    missing_pages = tuple(item for item in plan.items if item.page_id is None)
    checked: dict[str, bool] = {}
    root_readable: bool | None = None

    if root.page_id is not None:
        try:
            await client.get_page(root.page_id)
        except McpToolError:
            root_readable = False
            return SyncRemoteState(
                root_readable=root_readable,
                readable_by_page_id={root.page_id: False},
                missing_pages=missing_pages,
                stale_pages=(),
            )
        else:
            root_readable = True
            checked[root.page_id] = True

    readable = await _readable_page_ids(plan, client=client, checked=checked)
    stale_pages = tuple(
        item
        for item in plan.items
        if item.page_id is not None and readable.get(item.page_id) is False
    )
    return SyncRemoteState(
        root_readable=root_readable,
        readable_by_page_id=readable,
        missing_pages=missing_pages,
        stale_pages=stale_pages,
    )
```

- [ ] **Step 4: Run the remote-state test**

Run:

```bash
uv run pytest tests/confluence_publisher/test_sync.py::test_collect_sync_remote_state_marks_stale_pages_after_root_is_readable -v
```

Expected: PASS.

- [ ] **Step 5: Run all pure decision tests**

Run:

```bash
uv run pytest tests/confluence_publisher/test_sync.py -k "build_sync_execution_plan or collect_sync_remote_state" -v
```

Expected: PASS for all selected tests.

- [ ] **Step 6: Commit**

```bash
git add src/confluence_publisher/sync.py tests/confluence_publisher/test_sync.py
git commit -m "Collect sync remote state explicitly"
```

## Task 4: Route `sync_project()` Through The Execution Plan

**Files:**
- Modify: `src/confluence_publisher/sync.py`
- Test: `tests/confluence_publisher/test_sync.py`
- Test: `tests/confluence_publisher/test_cli.py`

- [ ] **Step 1: Run the current integration tests before refactoring**

Run:

```bash
uv run pytest tests/confluence_publisher/test_sync.py tests/confluence_publisher/test_cli.py -v
```

Expected: PASS before editing this task. If it fails, stop and fix the current task branch before refactoring.

- [ ] **Step 2: Add execution helpers**

In `src/confluence_publisher/sync.py`, add these helpers after `collect_sync_remote_state()`:

```python
def _selected_prepared_pages(
    prepared_pages: dict[str, PreparedSyncPage],
    source_paths: tuple[str, ...],
) -> dict[str, PreparedSyncPage]:
    return {source_path: prepared_pages[source_path] for source_path in source_paths}


async def _ensure_root_parent_readable(plan: PublishPlan, *, client) -> None:
    if plan.root_parent_page_id is None:
        raise RuntimeError("Cannot recreate missing root without root.parent_page_id in manifest")
    try:
        await client.get_page(plan.root_parent_page_id)
    except McpToolError as parent_exc:
        raise RuntimeError(
            f"Cannot recreate missing root because root.parent_page_id "
            f"{plan.root_parent_page_id} is not readable: {parent_exc}"
        ) from parent_exc


async def _create_planned_pages(
    plan: PublishPlan,
    *,
    execution: SyncExecutionPlan,
    prepared_pages: dict[str, PreparedSyncPage],
    client,
    page_ids: dict[str, str],
) -> tuple[dict[str, PageInfo], dict[str, str], str | None, str | None]:
    created_pages: dict[str, PageInfo] = {}
    root_page_id: str | None = None
    root_url: str | None = None
    create_paths = set(execution.create_source_paths)

    if execution.requires_title_preflight:
        create_items = [item for item in plan.items if item.source_path in create_paths]
        await _preflight_titles(plan, client=client, items=create_items)

    for item in plan.items:
        if item.source_path not in create_paths:
            continue
        parent_id = _parent_id_for(
            item,
            plan=plan,
            root_item=execution.root_item,
            page_ids=page_ids,
        )
        page = await _create_page(
            plan,
            client=client,
            item=item,
            parent_id=parent_id,
            prepared=prepared_pages[item.source_path],
        )
        is_root = item.source_path == execution.root_item.source_path
        _write_created_identity(plan, item=item, page=page, is_root=is_root)
        created_pages[item.source_path] = page
        page_ids[item.source_path] = page.page_id
        if is_root:
            root_page_id = page.page_id
            root_url = _created_url(page, plan=plan, item=item)

    return created_pages, page_ids, root_page_id, root_url
```

- [ ] **Step 3: Replace `sync_project()` with plan-driven orchestration**

Replace the body of `sync_project()` with this implementation:

```python
async def sync_project(plan: PublishPlan, *, client, dry_run: bool, mmdc_bin: str) -> int:
    if not plan.sync_enabled:
        raise RuntimeError("sync_project requires a sync-enabled plan")

    missing_pages = tuple(item for item in plan.items if item.page_id is None)
    if missing_pages and not plan.create_missing_pages:
        details = ", ".join(item.source_path for item in missing_pages)
        raise RuntimeError(
            "Cannot create missing Confluence pages without --create-missing-pages: "
            f"{details}"
        )
    if plan.space is None and _requires_space_for_creation(plan):
        raise RuntimeError("Cannot sync project without a Confluence space")

    if dry_run:
        remote_state = SyncRemoteState(
            root_readable=None,
            readable_by_page_id={},
            missing_pages=missing_pages,
            stale_pages=(),
        )
        execution = build_sync_execution_plan(plan, remote_state=remote_state, dry_run=True)
        prepared_pages = prepare_sync_pages(plan, mmdc_bin=mmdc_bin)
        return await _publish_prepared_sync_pages(
            plan,
            prepared_pages=_selected_prepared_pages(
                prepared_pages,
                execution.publish_source_paths,
            ),
            client=client,
            dry_run=True,
            page_ids={},
        )

    remote_state = await collect_sync_remote_state(plan, client=client)
    execution = build_sync_execution_plan(plan, remote_state=remote_state, dry_run=False)

    if execution.mode is SyncExecutionMode.RECREATE_ROOT_TREE:
        await _ensure_root_parent_readable(plan, client=client)

    prepared_pages = prepare_sync_pages(plan, mmdc_bin=mmdc_bin)
    page_ids = dict(execution.initial_page_ids)
    created_pages: dict[str, PageInfo] = {}
    root_page_id: str | None = None
    root_url: str | None = None

    if execution.create_source_paths:
        created_pages, page_ids, root_page_id, root_url = await _create_planned_pages(
            plan,
            execution=execution,
            prepared_pages=prepared_pages,
            client=client,
            page_ids=page_ids,
        )

    return await _publish_prepared_sync_pages(
        plan,
        prepared_pages=_selected_prepared_pages(
            prepared_pages,
            execution.publish_source_paths,
        ),
        client=client,
        dry_run=False,
        page_ids=page_ids,
        created_pages=created_pages,
        root_page_id=root_page_id,
        root_url=root_url,
    )
```

- [ ] **Step 4: Remove now-dead recovery functions only if unused**

After replacing `sync_project()`, run:

```bash
rg -n "_recreate_full_tree|_recreate_stale_pages" src tests
```

If both functions are referenced only by their definitions in `src/confluence_publisher/sync.py`, delete `_recreate_full_tree()` and `_recreate_stale_pages()` from `sync.py`. Do not delete helpers still used by `sync_project()` or tests.

- [ ] **Step 5: Run sync integration tests**

Run:

```bash
uv run pytest tests/confluence_publisher/test_sync.py -v
```

Expected: PASS. If call-order assertions fail, adjust helper order to match the existing behavior unless the spec explicitly allows the behavior change.

- [ ] **Step 6: Run CLI tests that route to sync**

Run:

```bash
uv run pytest tests/confluence_publisher/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/confluence_publisher/sync.py tests/confluence_publisher/test_sync.py
git commit -m "Route sync project through execution plan"
```

## Task 5: Final Verification And Cleanup

**Files:**
- Modify only files touched by previous tasks if verification exposes issues.

- [ ] **Step 1: Run the full test suite**

Run:

```bash
uv run pytest tests/confluence_publisher -v
```

Expected: PASS.

- [ ] **Step 2: Measure coverage**

Run:

```bash
uv run pytest tests/confluence_publisher --cov=confluence_publisher --cov-report=term-missing
```

Expected: PASS. Coverage is informational; compare the total to the repository baseline around 91%.

- [ ] **Step 3: Run whitespace checks**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 4: Review the refactored `sync.py` shape**

Run:

```bash
python -c 'import ast; from pathlib import Path
tree = ast.parse(Path("src/confluence_publisher/sync.py").read_text())
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and hasattr(node, "end_lineno"):
        if node.name == "sync_project":
            print(node.end_lineno - node.lineno + 1, node.name)'
```

Expected: `sync_project` is materially shorter than the previous 108-line function and reads as orchestration rather than nested recovery logic.

- [ ] **Step 5: Check whether local workflow skills need updates**

Run:

```bash
rg -n "sync_project|recreate_missing_root|recreate_stale_pages|push" /home/akhadiev/.codex/skills/confluence-docs-publishing /home/akhadiev/.agents/skills 2>/dev/null
```

Expected: If the output only describes the CLI workflow and not `sync.py` internals, no skill update is needed. If a skill documents internals that changed, update the relevant skill file after asking for filesystem approval when required.

- [ ] **Step 6: Commit verification cleanup if any files changed**

If verification required code or test edits, commit them:

```bash
git add src/confluence_publisher/sync.py tests/confluence_publisher/test_sync.py
git commit -m "Tighten sync state machine verification"
```

If no files changed after Task 4, skip this commit.

## Execution Notes

- The first behavior-changing task must use TDD and verify the failing test before implementation.
- Preserve current error messages where tests assert them.
- Preserve dry-run safety: no client calls, no manifest writes, storage preview only.
- Preserve manifest identity ordering: newly created page IDs and URLs are written immediately after create, before publishing content.
- Do not broaden the refactor into `manifest.py`, `planner.py`, `mcp_atlassian.py`, rendering, or storage conversion unless a test exposes a direct need.
