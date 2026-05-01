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
