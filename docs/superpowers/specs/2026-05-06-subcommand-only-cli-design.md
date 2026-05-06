# Subcommand-Only CLI Design

## Goal

Replace the mixed legacy command surface with a strict git-like, subcommand-only CLI for manifest-backed Confluence documentation projects.

The tool should keep the current safe project workflow, but remove legacy entry points that no longer match the current model:

- single-page publishing through `--source` and `--page-id`;
- project mode through top-level `--project`;
- bidirectional sync through top-level `--check-sync`, `--pull`, and `--sync`.

After this change, every public operation is a subcommand and every publishable project is manifest-backed.

## Non-Goals

- Do not keep deprecated aliases for the removed command forms.
- Do not preserve public single-page publishing.
- Do not change the manifest format except where documentation examples need updated commands.
- Do not rewrite historical implementation plans or specs; they remain historical records.
- Do not run live Confluence publishing as part of verification.

## Command Contract

Supported public commands:

```bash
publish-confluence clone <url-or-page-id> <output> [--space SPACE] [--max-depth N] [--max-pages N] [--dry-run]
publish-confluence plan <project> [--manifest PATH] [--create-missing-pages] [--recreate-missing-root] [--recreate-stale-pages]
publish-confluence status <project> [--manifest PATH]
publish-confluence pull <project> [--manifest PATH] [--dry-run] [--yes]
publish-confluence push <project> [--manifest PATH] [--create-missing-pages] [--recreate-missing-root] [--recreate-stale-pages] [--dry-run] [--yes] [--comment TEXT] [--output-format svg|png] [--diagram-width N] [--mmdc-bin PATH] [--allow-diagram-fallback]
```

Removed public forms:

```bash
publish-confluence --source ...
publish-confluence --project ...
publish-confluence --check-sync
publish-confluence --pull
publish-confluence --sync
```

`<project>` is a positional path. `--manifest` remains available for project commands and defaults to `<project>/.confluence-publish.yml`.

## Command Behavior

### `clone`

`clone` bootstraps a local Markdown project from an existing Confluence page tree.

For URL input, the command derives the page ID and space from the URL:

```bash
publish-confluence clone https://example.atlassian.net/wiki/spaces/SPACE/pages/123/Title docs
```

For page ID input, the operator must supply the space:

```bash
publish-confluence clone 123 docs --space SPACE
```

`clone` reads Confluence and writes a new local Markdown project and manifest unless `--dry-run` is passed. Existing output safety checks remain: cloning refuses unsafe outputs and existing Markdown projects.

### `plan`

`plan` is a cheap local preflight.

It loads the manifest, discovers Markdown files, validates hierarchy and mappings, builds the publish plan, and prints effective create/recovery settings. It never renders Mermaid, never connects to MCP, and never writes local files or Confluence.

`plan` accepts one-off creation and recovery overrides:

- `--create-missing-pages`;
- `--recreate-missing-root`;
- `--recreate-stale-pages`.

### `status`

`status` replaces `--check-sync`.

It reads mapped Confluence pages, converts supported storage XHTML back to canonical Markdown, compares local/remote state against the stored `canonical_hash`, and prints the invariant report. It never renders Mermaid and never writes local files or Confluence.

Exit code is CI-friendly: `0` only when every mapped page is in sync. Conflicts, remote-only changes, unsupported remote content, unknown divergence, validation errors, and runtime errors return non-zero.

### `pull`

`pull` is the only local-write command.

It reads remote Confluence storage and writes local Markdown only when the remote-only change is safe under the stored `canonical_hash`. It refuses to overwrite local changes, unsupported remote content, and unknown divergence.

`pull --dry-run` prints the report and affected paths without writing. Live `pull` requires `--yes` or interactive confirmation.

### `push`

`push` is the only Confluence-write command.

It always uses the sync-aware manifest workflow with canonical hashes, rendered content hashes, and create/recovery policy. The older non-sync project publishing path is removed from the public behavior.

`push` renders and converts local Markdown, uploads attachments, updates Confluence only when content changed or pages are created/recreated, and writes manifest identity/hash/timestamp state after successful writes.

`push --dry-run` renders and converts locally and prints storage previews. It does not connect to MCP and does not write local files or Confluence.

Live `push` requires `--yes` or interactive confirmation.

`push` accepts the same one-off create/recovery overrides as `plan`.

## Internal Architecture

`cli.py` should parse one required subcommand and dispatch to focused command handlers:

```text
run_async()
  parse args
  clone  -> run_clone_command(args)
  plan   -> run_plan_command(args)
  status -> run_status_command(args)
  pull   -> run_pull_command(args)
  push   -> run_push_command(args)
```

Shared project setup should be factored behind a helper used by `plan`, `status`, `pull`, and `push`. That helper owns:

- manifest path defaulting;
- `load_manifest(..., allow_missing_page_ids=True)`;
- Markdown discovery;
- `build_publish_plan(...)`;
- command-specific create/recovery option mapping.

The implementation should verify actual call sites before deleting internals:

- delete the old single-page CLI branch;
- delete `build_preview()` if no supported command or test still needs it;
- delete `publish_single_page()` and the old non-sync `batch.publish_project()` path if `push` no longer uses them;
- keep or move the lower-level page preparation primitive only if sync push still uses it for rendering, conversion, and hash calculation.

The current import from `sync.py` into `cli.py` for `_prepare_single_page()` is backwards after the CLI cleanup. If the primitive remains used, move it out of `cli.py` into a non-CLI module such as `publisher.py` or `page.py`.

## Testing Strategy

Implementation must follow test-driven development:

1. Write failing behavior tests first.
2. Verify each new test fails for the expected reason.
3. Implement the smallest passing change.
4. Re-run the focused tests.
5. Refactor only after green.

Public contract tests should cover:

- parser accepts `clone`, `plan`, `status`, `pull`, and `push`;
- parser rejects missing subcommand and old top-level legacy forms;
- `clone URL docs` and `clone PAGE_ID docs --space SPACE` dispatch correctly;
- `plan docs` does not connect to MCP and prints the project plan;
- `status docs` reads remote storage and returns non-zero on conflicts/divergence;
- `pull docs --dry-run` reads remote storage but does not write Markdown;
- `pull docs --yes` writes only safe remote-only changes;
- `push docs --dry-run` renders/converts locally but does not connect to MCP;
- `push docs --yes` uses sync-aware publishing and does not route through non-sync `publish_project()`;
- old single-page tests and old `--project` / `--check-sync` / `--pull` / `--sync` parser tests are removed or rewritten as rejection tests.

Required verification:

```bash
uv run pytest tests/confluence_publisher -v
uv run pytest tests/confluence_publisher --cov=confluence_publisher --cov-report=term-missing
git diff --check
uv run publish-confluence plan /tmp/confluence-publisher-smoke-docs
```

The smoke test should use a temporary docs tree and must not publish to Confluence.

## Documentation Updates

Update current documentation that describes the public command surface:

- `README.md`;
- `DESIGN.md`;
- `AGENTS.md`;
- the local `confluence-docs-publishing` skill if it contains stale command examples or workflow text.

Historical specs and plans under `docs/superpowers/` should remain historical unless a future workflow directly reuses them as current guidance.

## Implementation Workflow

After this design is approved and an implementation plan is written, implementation should use the requested workflow skills:

- `superpowers:test-driven-development` for every behavior change;
- `superpowers:subagent-driven-development` when executing the implementation plan in this session;
- `superpowers:requesting-code-review` after implementation tasks and for the final review.

Subagents should receive focused task text, relevant file paths, and explicit write ownership. Code review feedback marked Critical or Important must be addressed before moving to the next task.

## Acceptance Criteria

- The public CLI is subcommand-only.
- Public single-page publishing is removed.
- Top-level legacy project and sync flags are rejected.
- `clone`, `plan`, `status`, `pull`, and `push` cover the supported project workflow.
- `push` is the only Confluence-write command.
- `pull` is the only local Markdown-write command.
- `plan` remains local and side-effect free.
- `status` is read-only and returns non-zero for conflicts/divergence.
- Dead legacy internals are deleted after call-site verification.
- Current docs and relevant local skills show the new command scheme.
- The full test suite, coverage command, whitespace check, and plan smoke test pass.
