# Confluence Sync Recovery Design

## Context

`confluence-markdown-publisher` currently supports first-time project creation with
`--create-missing-pages`. That mode assumes any existing `page_id` values in
`.confluence-publish.yml` are valid and usable as parent pages.

The Whitelabel documentation exposed a recovery gap:

- the previously created root page was deleted from Confluence;
- the manifest still contained the stale root `page_id`;
- pages that had already been created were not reliable children of that root;
- the next publish failed when trying to create missing pages under the stale root.

The utility needs an explicit synchronization mode that can reconcile local
documentation state with Confluence state, recreate deleted pages when allowed,
and keep the manifest as the source of truth for page IDs and content hashes.

## Goals

- Provide an automatic sync flow for standalone documentation projects.
- Recreate an entire project when the root page is missing or deleted, but only
  when the user explicitly enables that recovery policy.
- Recreate individual missing pages when their `page_id` is stale.
- Skip updates for unchanged pages by comparing content hashes stored in the
  manifest.
- Store all page IDs, URLs, hashes, and sync timestamps directly in
  `.confluence-publish.yml`.
- Preserve the existing safety behavior: no live writes without `--yes`.
- Keep title collision checks before any create operations.

## Non-Goals

- Automatically delete orphan pages left behind by previous failed runs.
- Move existing pages to repair a tree. Re-parenting can be a later explicit
  feature.
- Infer recovery roots from arbitrary stale URLs when no stable parent is
  configured.
- Replace the existing `--create-missing-pages` behavior in this change.

## Manifest Changes

The manifest remains the single source of truth. It gains explicit root recovery
metadata and per-page sync metadata:

```yaml
space: "~6393203da593cb822e995262"
sync:
  enabled: true
  recreate_missing_root: true
  recreate_stale_pages: true
root:
  parent_page_id: "2163738446"
  page_id: "5155097071"
  url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/5155097071/White-label+NEXT+Deployment+-+Overview"
pages:
  00-overview.md:
    title: "White-label NEXT Deployment - Overview"
    page_id: "5155097071"
    url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/5155097071/White-label+NEXT+Deployment+-+Overview"
    content_hash: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    last_synced_at: "2026-04-28T12:00:00Z"
```

`root.parent_page_id` is the stable parent used when the project root must be
recreated. `root.page_id` and `root.url` describe the current project root and
may be replaced by sync recovery.

The optional `sync` block stores project-level defaults for the sync command:

- `sync.enabled`: run project publishing through the sync state machine by
  default.
- `sync.recreate_missing_root`: allow full project recreation when the configured
  root is missing or deleted.
- `sync.recreate_stale_pages`: allow recreation of individual pages whose
  `page_id` is missing, deleted, or unreadable.

All three values default to `false` when omitted. Keeping the recovery policy in
the manifest makes documentation projects self-contained; command-line flags
remain available for one-off overrides.

For backward compatibility, current `root.url`, `root_page_id`, and `root_url`
loading behavior remains valid. Automatic root recreation requires a configured
stable parent (`root.parent_page_id` or `root.parent_url`).

## CLI Flow

Add a new project mode:

```bash
publish-confluence --project <docs-root> --sync --yes
```

Add explicit recovery policy flags:

```bash
--recreate-missing-root
--recreate-stale-pages
```

The same policies can be enabled in `.confluence-publish.yml`:

```yaml
sync:
  enabled: true
  recreate_missing_root: true
  recreate_stale_pages: true
```

Effective sync settings are the union of manifest settings and CLI flags. For
example, either `sync.enabled: true` or `--sync` enables sync mode. Either
`sync.recreate_missing_root: true` or `--recreate-missing-root` enables root
recreation. Live writes still require `--yes`; manifest settings never bypass
the confirmation gate.

Default `--sync` behavior is conservative:

- it checks Confluence state;
- it updates changed pages whose `page_id` is valid;
- it creates pages with no `page_id`;
- it fails before writes when the root is stale unless
  `--recreate-missing-root` is provided;
- it fails before writes when an existing page `page_id` is stale unless
  `--recreate-stale-pages` is provided.

The Whitelabel recovery command becomes:

```bash
publish-confluence \
  --project /home/akhadiev/Documents/iow/devops/gitlab.iponweb.net/infrastructure/iowops/projects/NEXT_Documenttaion/Whitelabel_NEXT_Deployment/en \
  --yes
```

That command is sufficient when the Whitelabel manifest contains:

```yaml
sync:
  enabled: true
  recreate_missing_root: true
```

## Sync State Machine

The sync command performs these phases:

1. Load and validate the manifest.
2. Build the publish plan from the local Markdown tree.
3. Check root state.
4. Check existing page IDs.
5. Check title collisions for pages that may be created.
6. Compute local content hashes.
7. Apply the chosen reconciliation action.
8. Write updated page IDs, URLs, hashes, and timestamps back to the manifest.

Root reconciliation:

- `root.page_id` readable: use it as the project root.
- `root.page_id` missing or unreadable without `--recreate-missing-root`: fail
  before writes with a clear diagnostic.
- `root.page_id` missing or unreadable with `--recreate-missing-root`: create a
  new root under `root.parent_page_id`, treat every project page ID as stale, and
  recreate the full project tree.

Page reconciliation:

- Page has no `page_id`: create it under the expected parent.
- Page has a readable `page_id`: compare hashes and update only when changed.
- Page has an unreadable or deleted `page_id` without `--recreate-stale-pages`:
  fail before writes.
- Page has an unreadable or deleted `page_id` with `--recreate-stale-pages`:
  recreate that page and rewrite its manifest entry.

When the root is recreated, all project pages are recreated. Existing orphaned
Confluence pages are not deleted automatically.

## Content Hashes

Each page stores a deterministic hash in the manifest:

```yaml
content_hash: "sha256:<hex>"
last_synced_at: "2026-04-28T12:00:00Z"
```

The hash should cover the rendered storage payload inputs, not only raw Markdown:

- Markdown source content;
- page title used for publishing;
- output format and diagram width settings;
- rendered Mermaid attachment references or source blocks;
- publisher content transformation version.

If hashing cannot be computed for a page, sync should update the page and omit
or replace the hash with the newly computed value. Hash mismatch leads to an
update; hash match skips `confluence_update_page`.

## Safety And Errors

The sync command must fail before writes for these cases:

- no stable root parent is configured and root recreation is required;
- target root parent is unreadable by the current credentials;
- title collision exists for any page that would be created;
- existing stale pages require recreation but `--recreate-stale-pages` is not set;
- `--yes` is missing for live writes.

Errors should identify the affected source path, current page ID, intended title,
and recommended flag or manifest change.

Plan output should display the effective sync settings so a user can see when a
project config enables root or page recreation without scanning YAML manually.

## Testing

Unit and integration-style tests should cover:

- root exists and page hash matches: skip update;
- root exists and page hash differs: update and rewrite hash;
- page has no `page_id`: create and write ID, URL, hash, timestamp;
- page ID is stale without `--recreate-stale-pages`: fail before writes;
- page ID is stale with `--recreate-stale-pages`: recreate that page;
- root page ID is stale without `--recreate-missing-root`: fail before writes;
- root page ID is stale with `--recreate-missing-root`: recreate root and full tree;
- manifest `sync.enabled` enables sync mode without the `--sync` flag;
- manifest recovery settings enable the same behavior as matching CLI flags;
- title collision still blocks before create operations;
- manifest writes preserve existing user-authored fields where possible.

## Rollout

Implement in two increments:

1. Add `--sync`, root parent metadata, full-project recreation for missing root,
   and manifest rewrite of IDs and URLs.
2. Add manifest hashes and skip unchanged page updates.

This keeps the immediate Whitelabel recovery small while preserving the target
automatic sync design.
