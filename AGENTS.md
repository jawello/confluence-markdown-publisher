# Repository Agent Instructions

## Project

`confluence-markdown-publisher` is a local Python CLI for publishing Markdown to Confluence storage format.

It supports:

- single-page publishing with `--source` and `--page-id`;
- project publishing with `--project` and `.confluence-publish.yml`;
- Mermaid rendering through `mmdc`;
- Confluence writes through `mcp-atlassian`.

Keep Markdown and project manifests as the source of truth. Do not add hidden state files for page mappings.

## Development Rules

- Use Python 3.11+ and `uv`.
- Keep implementation changes scoped to `src/confluence_publisher`.
- Keep tests under `tests/confluence_publisher`.
- Use TDD for behavior changes: write the failing test, run it, implement the smallest fix, then run the passing test.
- Preserve existing single-page CLI behavior when changing project mode.
- Do not include credentials, tokens, or copied secret values in code, tests, docs, commits, or logs.
- Do not mention AI tools in commits, branches, merge requests, or user-facing project text.
- This repository does not require ticket IDs in commit messages.
- After behavior, workflow, CLI, manifest, or documentation contract changes, check whether related local skills need updates. Update the corresponding skill files when the change affects future agent workflows, especially `confluence-docs-publishing`.

## Verification

Run the full test suite before considering work complete:

```bash
uv run pytest tests/confluence_publisher -v
```

For whitespace checks:

```bash
git diff --check
```

For project-mode smoke testing, create a temporary docs tree and run:

```bash
uv run publish-confluence --project /tmp/confluence-publisher-smoke-docs --plan-only
```

Do not run live Confluence publishing unless the user explicitly asks.

## Architecture Map

- `cli.py`: argument parsing and orchestration for single-page and project modes.
- `manifest.py`: `.confluence-publish.yml` loading and validation.
- `project.py`: Markdown discovery with include/exclude and filesystem safety checks.
- `planner.py`: publish plan construction and hierarchy validation.
- `batch.py`: project publish-plan execution.
- `converter.py`: Markdown-to-Confluence storage conversion.
- `renderer.py`: Mermaid rendering with `mmdc`.
- `storage.py`: Confluence storage macro builders.
- `mcp_atlassian.py`: MCP client wrapper for Confluence tools.
- `models.py`: shared data models.

## Project Mode Contract

Project mode uses `.confluence-publish.yml` from the docs root unless `--manifest` is provided.

Rules:

- Every discovered Markdown file must have a `pages` entry. Normal mode also requires `page_id`.
- Project mode does not create Confluence pages unless `--create-missing-pages` is explicitly passed.
- `--create-missing-pages` requires manifest root page metadata and writes new `page_id` values back to `.confluence-publish.yml`.
- `index.md` is the logical page for a directory.
- Nested documents require a nearest ancestor `index.md` manifest entry.
- Manifest page keys must be POSIX relative paths.
- Include and exclude patterns must not be absolute or use parent traversal.
- Symlinked Markdown files and Markdown under symlinked directories are skipped.
- Optional `url` values must contain the exact Confluence page ID path segment.
- `--plan-only` must not render diagrams or connect to MCP.
- Project `--dry-run` may render and convert content, but must not connect to MCP.

## Documentation Preparation

When preparing docs for this tool, use:

- [docs/documentation-style.md](docs/documentation-style.md)
- [docs/documentation-agent-prompt.md](docs/documentation-agent-prompt.md)

The style guide is the stable documentation code-style contract. The prompt is the reusable task text for agents that need to organize Markdown documentation and create `.confluence-publish.yml`.
