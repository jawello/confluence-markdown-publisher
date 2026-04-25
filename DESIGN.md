# Design

## Purpose

`confluence-markdown-publisher` is a local CLI tool for publishing Markdown documents to Confluence storage format.

The main design goal is to make Mermaid diagrams publishable without manual Confluence actions. Confluence receives a rendered diagram image as a page attachment, while the original Mermaid source remains available under a collapsed "Mermaid source" block.

This keeps Markdown as the source of truth and avoids depending on manual Gliffy rendering or unsupported Confluence-side Mermaid rendering.

## Goals

- Convert Markdown into Confluence storage XHTML.
- Publish either one Markdown file or a documentation project folder.
- Use a project manifest as the source of truth for Confluence page IDs.
- Discover Markdown files recursively from a project folder with include and exclude rules.
- Validate documentation hierarchy with `index.md` as the logical page for a directory.
- Print a project publish plan before rendering or publishing.
- Render Mermaid fenced blocks locally with `mmdc`.
- Upload rendered diagrams as Confluence page attachments.
- Insert Confluence image macros that reference those attachments.
- Keep each Mermaid source block in the page under a collapsed expand macro.
- Support dry-run previews before live publishing.
- Use existing MCP/CLI tooling for Confluence writes instead of a custom Confluence REST client.
- Keep credentials outside the project and read them only from environment variables.

## Non-Goals

- This tool is not a full Markdown-to-Confluence implementation.
- It does not try to render Mermaid inside Confluence.
- It does not create Confluence pages.
- It does not move, reorder, or repair Confluence page tree relationships.
- It does not provide a hosted service, daemon, or scheduler.
- It does not store rendered diagrams permanently in the repository.
- It does not currently make publishing atomic. Attachments are uploaded before the page body is updated.

## High-Level Architecture

```mermaid
flowchart LR
    A[Markdown file] --> B[CLI]
    B --> C[Markdown converter]
    B --> D[Mermaid renderer]
    D --> E[mmdc]
    E --> F[Temporary SVG/PNG files]
    C --> G[Confluence storage XHTML]
    F --> H[MCP Atlassian client]
    G --> H
    H --> I[Confluence page]
```

The CLI orchestrates the whole publish operation. Most modules are intentionally small and have one primary responsibility:

| Module | Responsibility |
|---|---|
| `cli.py` | Parse arguments, coordinate rendering, conversion, preview, confirmation, and publish. |
| `manifest.py` | Load and validate project manifests. |
| `project.py` | Discover Markdown files under a project root with include and exclude patterns. |
| `planner.py` | Build validated project publish plans from discovered files and manifest pages. |
| `batch.py` | Execute project publish plans by reusing the single-page publishing pipeline. |
| `converter.py` | Find fenced code blocks, collect Mermaid metadata, convert Markdown to storage-ready HTML fragments. |
| `renderer.py` | Call `mmdc` and validate that a rendered output file was produced. |
| `storage.py` | Build Confluence storage macros for code, images, expand blocks, fallback panels, and TOC. |
| `mcp_atlassian.py` | Start `mcp-atlassian` through `uvx` and call the Confluence MCP tools. |
| `models.py` | Define immutable data objects and stable diagram attachment names. |

## Single-Page Publish Flow

1. The user runs `publish-confluence` with a source Markdown file and target Confluence page ID.
2. The CLI reads the Markdown file as UTF-8 text.
3. `converter.collect_mermaid_blocks()` scans triple-backtick fenced code blocks with language `mermaid`.
4. Each Mermaid block becomes a `DiagramBlock` with:
   - ordinal position within the page
   - page slug derived from the Markdown filename
   - original Mermaid source
   - output format, currently `svg` or `png`
   - optional display width from a first-line Confluence directive
5. The CLI creates a temporary directory for render inputs and outputs.
6. `MermaidRenderer` writes each Mermaid source to a temporary `.mmd` file and calls `mmdc`.
7. Rendered files are named deterministically with page slug, ordinal, and an 8-character source hash.
8. `storage.diagram_fragment()` creates a Confluence image macro plus a collapsed expand macro containing the Mermaid source.
9. `converter.convert_markdown_to_storage()` converts the rest of the Markdown with the Python `markdown` library and replaces temporary placeholders with Confluence storage fragments.
10. The CLI prints a preview with page ID, title, source path, output format, fallback mode, attachment names, and the beginning of the generated storage.
11. In `--dry-run` mode, the process stops before any Confluence connection.
12. In live mode, the CLI asks for confirmation unless `--yes` is passed.
13. The MCP client uploads diagram attachments first.
14. The MCP client updates the Confluence page with `content_format: "storage"`.

## Project Publish Flow

Project mode starts from a documentation folder instead of a single Markdown file:

```bash
publish-confluence --project docs --plan-only
```

The default manifest path is `<project>/.confluence-publish.yml`. The manifest stores all Confluence page IDs and optional titles or URLs:

```yaml
space: "~6393203da593cb822e995262"

include:
  - "**/*.md"

exclude:
  - "**/drafts/**"

defaults:
  version_comment: "Sync documentation from Markdown"
  output_format: "svg"
  diagram_width: 1000
  allow_diagram_fallback: false

pages:
  index.md:
    page_id: "111111"
    title: "Project Documentation"
    url: "https://criteo.atlassian.net/wiki/pages/111111/Project+Documentation"
```

Project mode flow:

1. The CLI loads the manifest.
2. `project.discover_markdown_files()` discovers Markdown files under the project root.
3. Include and exclude patterns are validated so they cannot use parent traversal or absolute paths.
4. Symlinked files and files under symlinked directories are skipped to avoid publishing outside-root content.
5. `planner.build_publish_plan()` checks that every discovered Markdown file exists in `pages`.
6. Manifest entries that point to missing local files are rejected.
7. `index.md` is treated as the logical page for its directory.
8. Nested documents require a nearest ancestor `index.md` entry in the manifest.
9. Titles come from manifest `title`, then the first Markdown H1, then the file stem.
10. Optional `url` values are validated by exact page ID path segment.
11. The CLI prints the publish plan before rendering or connecting to Confluence.
12. `--plan-only` exits after validation and plan output.
13. `--dry-run` renders and converts pages, prints storage previews, and never connects to Confluence.
14. Live project publishing asks for confirmation unless `--yes` is passed.
15. `batch.publish_project()` publishes each planned page through the same single-page rendering and update pipeline.

Project mode never creates Confluence pages. Missing `page_id` mappings are planning errors that stop the run before rendering or MCP startup.

## Mermaid Handling

Mermaid diagrams are rendered locally because Confluence does not provide a reliable automatic Mermaid rendering path for this workflow.

For each Mermaid block, the rendered file name is stable:

```text
diagram-<page-slug>-<ordinal>-<hash8>.<format>
```

The hash is based on normalized Mermaid source text, so the attachment name changes when the diagram source changes. This helps avoid stale browser or Confluence attachment caching while keeping names predictable.

The default image width is controlled by `--diagram-width` and defaults to `1000`.

A document can override one diagram width with a first-line directive inside the Mermaid block:

````markdown
```mermaid
%% confluence: width=1400 %%
graph TD
    A --> B
```
````

The directive remains in the Mermaid source stored under the collapsed Confluence block. `mmdc` accepts Mermaid comments, so the same source can be rendered without stripping the directive.

## Markdown Conversion

The converter uses a two-step approach:

1. Replace fenced code blocks with unique placeholders before Markdown-to-HTML conversion.
2. Convert the remaining Markdown to HTML with `markdown` extensions for tables and sane lists.
3. Replace placeholder paragraphs with Confluence storage fragments.

This avoids the Markdown library escaping generated Confluence storage macros as plain text.

Current conversion behavior:

- Mermaid fences become rendered image macros plus collapsed source blocks.
- Non-Mermaid fences become Confluence `code` macros.
- Markdown tables become HTML tables through the Markdown library.
- Blockquotes become Confluence panel macros:
  - `warning` or `important` prefix -> warning panel
  - `tip` prefix -> tip panel
  - `note` prefix -> info panel
  - otherwise -> note panel
- Documents with three or more Markdown headings receive a Confluence TOC macro at the top.

The fenced block scanner currently targets triple-backtick fences. Tilde fences and indented code blocks are outside the current scope.

## Confluence Integration

The tool intentionally delegates Confluence writes to `mcp-atlassian` rather than implementing its own REST client.

`mcp_atlassian.py` starts the server with:

```text
uvx --from mcp-atlassian>=0.17.0 mcp-atlassian --enabled-tools ...
```

The enabled tool set is limited to Confluence page and attachment operations:

- `confluence_get_page`
- `confluence_get_attachments`
- `confluence_upload_attachment`
- `confluence_update_page`

Runtime publishing uses:

- `confluence_get_page` when `--title` is omitted in live mode
- `confluence_upload_attachment` for rendered diagrams
- `confluence_update_page` for the final storage-format page body

Required environment variables:

```bash
CONFLUENCE_URL
CONFLUENCE_USERNAME
CONFLUENCE_API_TOKEN
```

The tool validates that these variables exist before starting the MCP server. It does not read credentials from files and does not print credential values.

## Error Handling

The default behavior is fail-fast:

- Missing `mmdc` stops publishing before conversion.
- A non-zero `mmdc` exit stops publishing unless fallback mode is enabled.
- Missing rendered output or an empty output file stops publishing.
- Invalid Mermaid width directives stop conversion.
- Invalid project manifests and unsafe include or exclude patterns stop project publishing before side effects.
- Missing project `page_id` mappings stop project publishing before rendering or MCP startup.
- Missing Confluence environment variables stop live publishing before MCP startup.
- MCP tool errors are converted into Python exceptions with bounded error messages.

Fallback mode is explicit:

```bash
publish-confluence --allow-diagram-fallback ...
```

When fallback mode is enabled and a diagram fails to render, the page receives a visible Confluence info panel with the Mermaid source instead of an image attachment.

## Safety Model

The tool is designed for local, operator-driven publishing.

Safety controls:

- `--dry-run` performs rendering and conversion without connecting to Confluence.
- `--plan-only` validates a project and prints the publish plan without rendering or connecting to Confluence.
- Live publishing requires an interactive `yes` confirmation unless `--yes` is passed.
- Attachment names are listed in the preview before publishing.
- The first part of generated storage is printed before publishing.
- Project mode fails before side effects when discovered Markdown files are missing manifest `page_id` mappings.
- Project discovery rejects unsafe include and exclude patterns and skips symlinked Markdown paths.
- Confluence credentials stay in environment variables.
- Rendered diagrams are written only to a temporary directory and removed when the process exits.

Known safety limitation:

- Publishing is not transactional. If attachments upload successfully but the page update fails, those attachments may remain on the Confluence page. The stable attachment naming scheme limits confusion, but cleanup is currently a manual or future-tooling concern.

## Testing Strategy

Tests are unit-focused and avoid live Confluence writes.

Coverage areas:

- CLI argument parsing and orchestration.
- Mermaid collection and width directive parsing.
- Markdown-to-storage conversion.
- Confluence storage macro generation and escaping.
- Mermaid renderer subprocess behavior.
- MCP client command construction, response parsing, and error handling.
- Attachment naming stability.
- Project manifest loading and validation.
- Markdown project discovery with include, exclude, and safety boundaries.
- Publish-plan construction and project-mode CLI orchestration.
- Batch publishing behavior in dry-run and live modes.

The standard verification command is:

```bash
uv run pytest tests/confluence_publisher -v
```

Live Confluence publishing is treated as an integration check because it depends on user credentials, network access, current Confluence permissions, and the external MCP server.

## Operational Usage

Typical dry run:

```bash
uv run publish-confluence \
  --source /path/to/document.md \
  --page-id 5151854234 \
  --dry-run
```

Typical publish:

```bash
uv run publish-confluence \
  --source /path/to/document.md \
  --page-id 5151854234 \
  --title "Page title" \
  --yes
```

Project plan preview:

```bash
uv run publish-confluence \
  --project docs \
  --plan-only
```

Project publish:

```bash
uv run publish-confluence \
  --project docs \
  --comment "Sync guide updates" \
  --yes
```

When the CLI is used from another repository:

```bash
uv run --project ~/Documents/confluence-markdown-publisher publish-confluence \
  --source en/00-overview.md \
  --page-id 5123638121 \
  --dry-run
```

If `mmdc` is installed outside the active `PATH`, pass it explicitly:

```bash
uv run publish-confluence \
  --source /path/to/document.md \
  --page-id 5151854234 \
  --mmdc-bin /path/to/mmdc \
  --dry-run
```

## Extension Points

The current boundaries leave several incremental extension points:

- Generating a manifest skeleton from a documentation folder.
- Filling missing manifest URLs from Confluence page IDs.
- Checking local Markdown links between project documents.
- Optional cleanup or replacement policy for old diagram attachments.
- Support for more Markdown constructs if needed.
- Additional renderers if diagrams other than Mermaid become necessary.
- A second backend that uses Confluence REST directly, while keeping the CLI and converter unchanged.

These are intentionally outside the current project-mode scope because the operational need is still local, operator-driven publishing to existing Confluence pages.
