# Confluence Clone Design

## Context

`confluence-markdown-publisher` now has bidirectional project checks and a
conservative `--pull` mode. That mode assumes a local documentation project
already exists: Markdown files are present, `.confluence-publish.yml` maps each
file to a Confluence `page_id`, and `canonical_hash` stores the baseline used to
protect local edits.

The missing bootstrap step is creating that local project from an existing
Confluence page tree. A user should be able to start with one Confluence URL,
clone the tree into Markdown, and then use the existing `check-sync`, `pull`,
and publish flows.

## Goals

- Add git-like `clone` naming for bootstrapping a local documentation project.
- Create a local Markdown tree and `.confluence-publish.yml` from a Confluence
  root URL or page ID.
- Preserve the existing `pull` contract: it updates only already initialized
  projects and remains protected by the manifest baseline.
- Store all page mappings in `.confluence-publish.yml`; do not introduce hidden
  state files.
- Refuse to silently lose unsupported Confluence content during reverse
  conversion.
- Keep the existing publish CLI behavior backward compatible.

## Non-Goals

- Re-parent, move, delete, or create Confluence pages.
- Merge cloned content into an already populated documentation tree.
- Build a complete Confluence export tool for every Confluence macro.
- Introduce hidden caches or page mapping databases outside the manifest.
- Rename the existing `publish-confluence` executable in this change.

## CLI Contract

Add a subcommand for the bootstrap workflow:

```bash
publish-confluence clone \
  --url "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/5164564861/Custom+UI+NEXT+Migration" \
  --output docs/custom-ui-next-migration
```

Equivalent page-id based usage:

```bash
publish-confluence clone \
  --page-id 5164564861 \
  --space "~6393203da593cb822e995262" \
  --output docs/custom-ui-next-migration
```

Options:

- `--url`: Confluence page URL; the command extracts page ID and space key.
- `--page-id`: root page ID when no URL is supplied.
- `--space`: required with `--page-id` when the page URL is unavailable.
- `--output`: target docs root; must not already contain publishable Markdown or
  `.confluence-publish.yml`.
- `--max-depth`: optional tree depth limit, default unlimited within client
  safety limits.
- `--max-pages`: optional safety limit, default `1000`.
- `--dry-run`: fetch and report the planned local tree without writing files.

The legacy invocation style remains valid:

```bash
publish-confluence --project docs --pull --yes
publish-confluence --project docs --check-sync
publish-confluence --project docs --yes
```

## File Tree Mapping

The cloned root page becomes `index.md`.

Children that have their own children become directory indexes:

```text
index.md
custom-ui-next-infrastructure-onboarding/index.md
custom-ui-next-infrastructure-onboarding/prerequisites-requirements.md
white-label-next-deployment/index.md
white-label-next-deployment/prerequisites.md
```

Leaf pages become `<slug>.md`. Slugs use the existing ASCII slug behavior and
must be made unique per directory by adding a stable numeric suffix when needed.

The generated manifest uses POSIX paths and includes one page entry for every
Markdown file:

```yaml
space: "~6393203da593cb822e995262"
sync:
  enabled: true
root:
  source_path: "index.md"
  page_id: "5164564861"
  url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/5164564861/Custom+UI+NEXT+Migration"
pages:
  index.md:
    title: "Custom UI NEXT Migration"
    page_id: "5164564861"
    url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/5164564861/Custom+UI+NEXT+Migration"
    canonical_hash: "sha256:..."
```

`content_hash` and `last_synced_at` are not required during clone because clone
does not publish to Confluence. `canonical_hash` is required because it becomes
the baseline for future `pull` conflict detection.

## Data Flow

1. Parse the root page ID and space from `--url`, or validate `--page-id` and
   `--space`.
2. Read the Confluence page tree from the MCP client.
3. Read storage XHTML for every page in the tree.
4. Convert storage XHTML to Markdown with the existing reverse converter.
5. If any page contains unsupported content, fail before writing files and list
   the affected page IDs and unsupported constructs.
6. Build the local path map with deterministic slugs.
7. Write Markdown files and `.confluence-publish.yml`.
8. Print a clone summary with root path, page count, and next commands.

After clone, the expected workflow is:

```bash
publish-confluence --project docs/custom-ui-next-migration --check-sync
publish-confluence --project docs/custom-ui-next-migration --pull --yes
```

## Safety And Errors

The command fails before writing when:

- both `--url` and `--page-id` are missing;
- `--page-id` is used without `--space`;
- the output path already contains `.confluence-publish.yml`;
- the output path contains existing Markdown files;
- Confluence tree retrieval fails;
- any page cannot be read;
- reverse conversion reports unsupported content;
- generated paths collide after slug normalization.

`--dry-run` performs Confluence reads and conversion checks, but writes nothing.

The command never updates Confluence.

## Testing

Tests should cover:

- URL parsing for page ID and space key.
- Tree-to-path mapping for root, nested index pages, leaf pages, and duplicate
  titles.
- Manifest generation with `root`, `sync.enabled`, page IDs, URLs, titles, and
  `canonical_hash`.
- Strict failure on unsupported remote content before any file writes.
- Refusal to clone into a non-empty Markdown project.
- `--dry-run` reporting without writes.
- Backward compatibility for existing `--project --pull` and publish flows.
