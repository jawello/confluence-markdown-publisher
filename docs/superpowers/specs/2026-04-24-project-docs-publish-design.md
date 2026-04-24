# Project Documentation Publishing Design

## Purpose

Extend `confluence-markdown-publisher` from a single-page Markdown publisher into a documentation-project publisher.

The new project mode should let the user pass a documentation folder, have the tool discover Markdown files from that folder, validate their Confluence mapping from a manifest, and republish the mapped pages to Confluence with a consistent version comment.

The current single-page mode remains supported.

## Goals

- Add a manifest-first project mode for publishing a folder of Markdown documents.
- Keep Confluence `page_id` values in one manifest file.
- Discover Markdown files recursively from the project folder using include and exclude patterns.
- Treat `index.md` as the page for its directory and as the logical parent for nested documents.
- Refuse to create Confluence pages automatically.
- Fail before any rendering or Confluence write when a publishable Markdown file has no `page_id`.
- Update Confluence pages with a version comment.
- Keep Mermaid rendering, attachment upload, and Markdown conversion behavior consistent with the existing single-page publisher.
- Preserve the existing `publish-confluence --source ... --page-id ...` workflow.

## Non-Goals

- Do not create Confluence pages in the first version.
- Do not move Confluence pages or enforce parent-child relationships through Confluence writes.
- Do not use Markdown front matter for Confluence metadata.
- Do not implement full bidirectional sync between Confluence and the local docs tree.
- Do not add a hosted service, daemon, scheduler, or background sync.

## Chosen Approach

Use a separate documentation-project model on top of the existing single-page publishing pipeline.

New modules:

- `manifest.py`: load and validate `.confluence-publish.yml`.
- `project.py`: scan the documentation folder and apply include and exclude patterns.
- `planner.py`: match discovered files to manifest pages, resolve titles and logical parents, and build a publish plan.
- `batch.py`: execute a publish plan by reusing the existing rendering, conversion, attachment upload, and page update behavior.
- `cli.py`: add project-mode arguments while keeping the current single-page mode.

Existing modules remain the core single-page machinery:

- `converter.py`
- `renderer.py`
- `storage.py`
- `mcp_atlassian.py`
- `models.py`

Manifest parsing should use `yaml.safe_load` from PyYAML, added as a project dependency.

## Manifest Format

The default manifest path is:

```text
<project>/.confluence-publish.yml
```

Example:

```yaml
space: "~6393203da593cb822e995262"

include:
  - "**/*.md"

exclude:
  - "**/drafts/**"
  - "**/.*/**"

defaults:
  version_comment: "Sync documentation from Markdown"
  output_format: "svg"
  diagram_width: 1000
  allow_diagram_fallback: false

pages:
  index.md:
    page_id: "111111"
    title: "Project Documentation"
    url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/111111/Project+Documentation"

  guide/index.md:
    page_id: "222222"
    title: "Guide"
    url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/222222/Guide"

  guide/getting-started.md:
    page_id: "333333"
    title: "Getting Started"
    url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/333333/Getting+Started"
```

Rules:

- `pages` is the only place where Confluence `page_id` values are stored.
- Manifest page keys are POSIX-style paths relative to the documentation project root.
- `page_id` is required for every publishable page.
- `title` is optional. If omitted, the planner resolves it from the first Markdown H1. If there is no H1, it falls back to the file stem.
- `url` is optional and used for review, navigation, and reporting.
- If `url` is present, it must contain the same `page_id`.
- `page_id` remains the source of truth for publishing.
- `space` is recorded for context and future validation; first-version page updates still use `page_id`.
- `defaults.version_comment` can be overridden by CLI `--comment`.

## Documentation Tree Rules

- Markdown discovery starts from the project folder.
- Discovery uses `include` patterns first, then removes paths matching `exclude`.
- If no `include` is configured, the default is `**/*.md`.
- `index.md` represents the page for its directory.
- A non-index document's logical parent is the nearest ancestor directory that has an `index.md` entry in the manifest.
- A nested document without a logical parent entry is a planning error.
- Parent resolution is used for validation and publish-plan output only in the first version.
- The tool does not create, move, or reorder Confluence pages.

## CLI Contract

Existing single-page mode remains valid:

```bash
uv run publish-confluence \
  --source docs/guide/getting-started.md \
  --page-id 333333 \
  --title "Getting Started" \
  --dry-run
```

New project mode:

```bash
uv run publish-confluence \
  --project docs \
  --plan-only
```

Project dry run:

```bash
uv run publish-confluence \
  --project docs \
  --dry-run
```

Project publish:

```bash
uv run publish-confluence \
  --project docs \
  --comment "Sync guide updates" \
  --yes
```

New options:

- `--project PATH`: enable project mode.
- `--manifest PATH`: use a manifest path other than `<project>/.confluence-publish.yml`.
- `--plan-only`: validate and print the publish plan without Mermaid rendering or Confluence connection.
- `--comment TEXT`: set the Confluence version comment for all page updates in the project run.

Existing options also apply to project mode and override manifest defaults:

- `--output-format`
- `--diagram-width`
- `--mmdc-bin`
- `--allow-diagram-fallback`
- `--dry-run`
- `--yes`

CLI validation:

- `--source` and `--project` are mutually exclusive.
- Single-page mode requires `--page-id`.
- Project mode rejects `--page-id` and `--title`.
- `--plan-only` is intended for project mode only.

## Project Publish Flow

1. CLI parses arguments and selects single-page or project mode.
2. Project mode loads the manifest.
3. Scanner discovers Markdown files using include and exclude patterns.
4. Planner validates the manifest and discovered files.
5. Planner builds a publish plan with source path, page ID, URL, resolved title, and logical parent source.
6. CLI prints the publish plan.
7. `--plan-only` exits before rendering and before any MCP connection.
8. `--dry-run` renders diagrams and builds storage previews, but does not connect to Confluence.
9. Live mode asks for confirmation unless `--yes` is passed.
10. Batch publisher processes pages in plan order.
11. For each page, the publisher reads Markdown, renders Mermaid diagrams, converts Markdown to Confluence storage, uploads rendered attachments, and updates the Confluence page.
12. Page updates use the resolved title and the selected version comment.

Default failure behavior is fail-fast. If a page fails during rendering or Confluence update, the batch stops and returns a non-zero exit code.

## Publish Plan Output

The project plan should be readable before any writes happen.

It should include:

- project root;
- manifest path;
- update count;
- excluded count when available;
- version comment;
- per-page source path;
- page ID;
- title;
- URL when present;
- logical parent source when present.

Missing `url` values are informational warnings, not errors.

## Error Handling

Planning errors happen before Mermaid rendering, attachment upload, or page update.

Planning errors include:

- missing manifest;
- invalid YAML or invalid manifest schema;
- discovered Markdown file missing from `pages`;
- manifest page missing on disk;
- missing `page_id`;
- `url` present but not containing the same `page_id`;
- nested document without a parent `index.md` manifest entry;
- duplicate `page_id`;
- unsupported output format;
- invalid diagram width.

Error messages should be actionable. For example:

```text
Cannot publish project: 2 discovered Markdown files are missing from manifest pages:
  - guide/install.md
  - reference/api.md

Add entries to docs/.confluence-publish.yml:
  pages:
    guide/install.md:
      page_id: "<confluence-page-id>"
```

## Confluence Version Comments

Project mode uses a Confluence page version comment for every updated page.

Precedence:

1. CLI `--comment`
2. manifest `defaults.version_comment`
3. built-in default: `Sync documentation from Markdown`

Attachment upload comments keep the existing Mermaid-specific behavior unless a later design changes attachment reporting.

## Testing Strategy

Development should use TDD.

Test areas:

- `manifest.py`: valid manifest, defaults, schema errors, URL and page ID mismatch, duplicate page IDs.
- `project.py`: recursive discovery, include and exclude handling, POSIX relative paths.
- `planner.py`: page mapping, missing manifest entries, missing files, parent `index.md` resolution, title resolution.
- `batch.py`: plan-only has no MCP connection, dry-run has no MCP connection, live mode uploads attachments and updates each page with the selected comment.
- `cli.py`: single-page behavior remains unchanged, project-mode option validation, CLI overrides for manifest defaults.
- Existing converter, renderer, storage, and MCP tests continue to protect current behavior.

The standard verification command remains:

```bash
uv run pytest tests/confluence_publisher -v
```

## Future Improvements

These are useful follow-ups but not part of the first implementation:

- `init-manifest`: generate a manifest skeleton from discovered Markdown files with empty `page_id` values.
- `check-links`: validate local Markdown links between project documents.
- `changed-only`: publish only files changed relative to a git ref.
- attachment cleanup report: show old rendered diagram attachments that are no longer referenced.
- generated URL fill: populate missing manifest `url` values from known `page_id` and Confluence base URL.
- prettier plan output: table-formatted project plan.
- optional `--continue-on-error` for large projects after fail-fast behavior has proven reliable.

## Open Decisions

No open design decisions remain for the first implementation.
