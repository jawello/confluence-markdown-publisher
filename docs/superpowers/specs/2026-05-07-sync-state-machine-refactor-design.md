# Sync State Machine Refactor Design

## Context

`confluence-markdown-publisher` currently keeps the public push/recovery workflow in
`sync_project()`. That function validates sync preconditions, checks remote page
readability, chooses the recovery path, prepares rendered storage, creates pages,
publishes page content, and writes manifest sync state.

The graph audit identified this area as the highest-value refactoring target for
reducing operational risk. `sync_project()` is a central bridge between publish
planning, MCP page operations, recovery policy, prepared storage, and manifest state
updates. It is also covered by broad integration-style tests, which makes behavior
safer but leaves the recovery decision logic harder to reason about in isolation.

The goal is not to change CLI behavior or Confluence publishing semantics. The goal is
to make push/recovery decisions explicit, deterministic, and independently testable.

## Goals

- Reduce the risk of regressions in `push` and recovery scenarios.
- Preserve the existing public `sync_project()` API and CLI contract.
- Separate recovery decision logic from side effects such as MCP calls, rendering,
  page creation, page updates, and manifest writes.
- Add focused tests for sync decision behavior without requiring fake MCP clients.
- Keep the first refactoring scoped to `src/confluence_publisher` and
  `tests/confluence_publisher`.

## Non-Goals

- No changes to command names, flags, prompts, or output format beyond incidental
  refactoring needs.
- No live Confluence publishing as part of verification.
- No broad module split unless the extracted model proves too large for `sync.py`.
- No rewrite of rendering, Markdown conversion, bidirectional pull, or MCP client code.

## Proposed Architecture

Split the current `sync_project()` responsibilities into two layers.

The first layer is a pure decision layer. It receives a `PublishPlan`, a compact
description of remote readability, and the dry-run flag. It returns a
`SyncExecutionPlan` that describes what should happen. It must not call MCP, render
pages, touch the filesystem, or write the manifest.

The second layer is an effect layer. It receives the execution plan, prepared page
storage, and the MCP client. It performs title preflight, page creation, attachment
upload, page updates, storage preview, and manifest state writes. It must not decide
which recovery scenario is active.

`sync_project()` remains the public orchestration entry point. After the refactor it
should read as a short sequence:

1. Validate sync-enabled input and creation prerequisites.
2. Collect remote readability state.
3. Build the sync execution plan.
4. Prepare only the pages required by the execution plan.
5. Execute the plan.

## Internal Model

Introduce internal dataclasses and enums with names close to the domain:

- `SyncRemoteState`
  - `root_readable: bool | None`
  - `readable_by_page_id: dict[str, bool]`
  - `missing_pages: tuple[PlannedPage, ...]`
  - `stale_pages: tuple[PlannedPage, ...]`
- `SyncExecutionMode`
  - `PREVIEW`
  - `PUBLISH_EXISTING`
  - `CREATE_MISSING`
  - `RECREATE_ROOT_TREE`
  - `RECREATE_STALE_PAGES`
- `SyncExecutionPlan`
  - `mode: SyncExecutionMode`
  - `root_item: PlannedPage`
  - `prepared_source_paths: tuple[str, ...]`
  - `create_source_paths: tuple[str, ...]`
  - `publish_source_paths: tuple[str, ...]`
  - `initial_page_ids: dict[str, str]`
  - `requires_title_preflight: bool`
- `SyncCreatedPage`
  - `source_path: str`
  - `page: PageInfo`
  - `is_root: bool`

The exact field names can be adjusted during implementation if tests show a clearer
shape, but the separation must remain: decision data in the plan, side-effect results
in execution state.

## Data Flow

For live push:

1. `sync_project()` validates missing page IDs and creation-space requirements.
2. It resolves the root item with the existing root selection rules.
3. It checks root readability and, when needed, parent readability for root recovery.
4. It checks readability for all remaining known page IDs.
5. It builds `SyncRemoteState`.
6. `build_sync_execution_plan()` chooses one of the execution modes.
7. `prepare_sync_pages()` prepares pages named by `prepared_source_paths`.
8. The executor performs creation or publication according to the execution plan.
9. Manifest identity is written immediately after page creation.
10. Manifest content and canonical hashes are written only after successful page update.

For dry-run push:

1. `sync_project()` keeps the current no-client-write behavior.
2. The execution plan uses `PREVIEW`.
3. Prepared storage is printed as a preview.
4. No page create, upload, update, or manifest write is allowed.

## Error Handling

Existing user-facing errors should remain text-compatible where tests already assert
them:

- Missing page IDs without `--create-missing-pages`.
- Missing root without `--recreate-missing-root`.
- Missing root recovery without `root.parent_page_id`.
- Unreadable root parent during root recreation.
- Stale pages without `--recreate-stale-pages`.
- Missing Confluence `space` when creation or recovery requires it.
- Duplicate local titles and remote title collisions before page creation.

New pure decision tests should assert modes and source paths rather than exact
procedural call order. Existing integration tests should continue to cover MCP call
order and manifest writes where that behavior matters.

## Testing Strategy

Add focused tests for `build_sync_execution_plan()`:

- Unreadable root with recovery disabled fails with the current root recovery error.
- Unreadable root with recovery enabled returns `RECREATE_ROOT_TREE`.
- Stale child with recovery disabled fails with the current stale page error.
- Stale child with recovery enabled returns `RECREATE_STALE_PAGES`.
- Stale ancestor includes descendants in `create_source_paths`.
- Missing child with `--create-missing-pages` returns `CREATE_MISSING`.
- Missing page IDs without creation policy fail before execution.
- Dry-run returns `PREVIEW` and does not require creation actions.

Keep the existing `test_sync.py` integration-style tests during the first refactor.
They provide regression coverage for manifest writes, client calls, and dry-run safety.
After the state machine is stable, redundant integration tests can be simplified in a
separate cleanup.

## Implementation Boundaries

The first implementation should keep the new types and pure builder in `sync.py`.
This keeps imports stable and avoids a large mechanical module split. If `sync.py`
remains difficult to navigate after the behavior is separated, a later cleanup can move
the decision model into `sync_plan.py` without changing behavior.

The executor should reuse existing helpers where possible:

- `_root_item`
- `_readable_page_ids`
- `_preflight_titles`
- `_parent_id_for`
- `_create_page`
- `_write_created_identity`
- `_with_descendants`
- `_publish_prepared_sync_pages`

Any helper signature changes should be driven by tests and kept narrow.

## Success Criteria

- `sync_project()` becomes a short orchestration function rather than a nested recovery
  procedure.
- Recovery scenario selection is testable without MCP or rendering.
- Existing public CLI behavior is preserved.
- Existing sync integration tests pass.
- Full repository verification passes with:

```bash
uv run pytest tests/confluence_publisher -v
```

Coverage may be measured after the behavior refactor:

```bash
uv run pytest tests/confluence_publisher --cov=confluence_publisher --cov-report=term-missing
```

Coverage is informational, not a hard gate.

## Risks

- Introducing the plan model can accidentally duplicate existing helper logic. Keep
  helper reuse high and test the model with source paths, not implementation details.
- Root recreation and stale-page recreation share similar creation mechanics but have
  different root identity requirements. Tests must keep these cases separate.
- Manifest writes are deliberately side effects. The pure layer must not try to model
  final manifest state beyond source paths and planned actions.
- Over-splitting modules in the first pass would add churn without reducing recovery
  risk. Keep the first pass behavior-focused.
