from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import unquote, urlparse

import yaml

from .bidirectional import compute_canonical_markdown_hash
from .manifest import ManifestError, load_manifest
from .models import ConfluenceTreeNode, slugify
from .storage_to_markdown import storage_to_markdown


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

    if page_id is None or page_id.strip() == "":
        raise CloneError("--page-id must be a non-empty string")
    if space is None or space.strip() == "":
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
    markdown_files = sorted(
        path for path in output.rglob("*") if path.is_file() and path.suffix.lower() == ".md"
    )
    if markdown_files:
        raise CloneError(f"Clone output already contains Markdown files: {output}")


@dataclass(frozen=True)
class ClonePlannedPage:
    page_id: str
    title: str
    source_path: str
    url: str | None
    has_children: bool


@dataclass(frozen=True)
class CloneFilePlan:
    root: ConfluenceTreeNode
    pages: tuple[ClonePlannedPage, ...]


def _unique_slug(title: str, used: set[str]) -> str:
    base = slugify(title)
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def build_clone_file_plan(root: ConfluenceTreeNode) -> CloneFilePlan:
    pages: list[ClonePlannedPage] = []

    def visit(node: ConfluenceTreeNode, directory: PurePosixPath) -> None:
        has_children = bool(node.children)
        if not pages:
            source_path = "index.md"
            child_directory = PurePosixPath("")
        elif has_children:
            source_path = (directory / "index.md").as_posix()
            child_directory = directory
        else:
            source_path = (directory.parent / f"{directory.name}.md").as_posix()
            child_directory = directory.parent

        pages.append(
            ClonePlannedPage(
                page_id=node.page_id,
                title=node.title,
                source_path=source_path,
                url=node.url,
                has_children=has_children,
            )
        )

        used_child_slugs: set[str] = set()
        for child in node.children:
            child_slug = _unique_slug(child.title, used_child_slugs)
            visit(child, child_directory / child_slug)

    visit(root, PurePosixPath(""))
    return CloneFilePlan(root=root, pages=tuple(pages))


@dataclass(frozen=True)
class CloneContentPage:
    markdown: str


def _manifest_for_clone(
    *,
    space: str,
    root_url: str | None,
    file_plan: CloneFilePlan,
    content: dict[str, CloneContentPage],
) -> dict[str, Any]:
    root_page = file_plan.pages[0]
    manifest: dict[str, Any] = {
        "space": space,
        "sync": {"enabled": True},
        "root": {
            "source_path": "index.md",
            "page_id": root_page.page_id,
        },
        "pages": {},
    }
    if root_url is not None:
        manifest["root"]["url"] = root_url

    pages_block: dict[str, dict[str, str]] = {}
    for page in file_plan.pages:
        converted = content.get(page.page_id)
        if converted is None:
            raise CloneError(f"Missing converted content for page {page.page_id}")
        page_data = {
            "title": page.title,
            "page_id": page.page_id,
            "canonical_hash": compute_canonical_markdown_hash(converted.markdown),
        }
        page_url = root_url if page is root_page and root_url is not None else page.url
        if page_url is not None:
            page_data["url"] = page_url
        pages_block[page.source_path] = page_data
    manifest["pages"] = pages_block
    return manifest


def _validate_clone_source_path(source_path: str) -> None:
    path = PurePosixPath(source_path)
    if source_path == "" or path == PurePosixPath(".") or path.is_absolute() or ".." in path.parts:
        raise CloneError(f"Invalid clone source path: {source_path}")


def _target_for_clone_page(output: Path, source_path: str) -> Path:
    _validate_clone_source_path(source_path)
    output_root = output.resolve()
    target = (output / source_path).resolve()
    if not target.is_relative_to(output_root):
        raise CloneError(f"Invalid clone source path: {source_path}")
    return target


def _validate_unique_clone_source_paths(file_plan: CloneFilePlan) -> None:
    seen: set[str] = set()
    for page in file_plan.pages:
        if page.source_path in seen:
            raise CloneError(f"Duplicate clone source path: {page.source_path}")
        seen.add(page.source_path)


def _validate_clone_target_paths(output: Path, file_plan: CloneFilePlan) -> None:
    output_root = output.resolve()
    targets = [_target_for_clone_page(output, page.source_path) for page in file_plan.pages]
    target_set = set(targets)
    for target in targets:
        if target.exists():
            raise CloneError(f"Clone output path already exists: {target}")
        parent = target.parent
        while parent != output_root:
            if parent in target_set or (parent.exists() and not parent.is_dir()):
                raise CloneError(f"Clone output path parent is a file: {parent}")
            parent = parent.parent


def _validate_generated_manifest(manifest: dict[str, Any]) -> None:
    with TemporaryDirectory(prefix="confluence-clone-manifest-") as temp_dir:
        manifest_path = Path(temp_dir) / ".confluence-publish.yml"
        manifest_path.write_text(
            yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        try:
            load_manifest(manifest_path)
        except ManifestError as exc:
            raise CloneError(f"Generated clone manifest is invalid: {exc}") from exc


def _validate_clone_project(
    *,
    output: Path,
    space: str,
    root_url: str | None,
    file_plan: CloneFilePlan,
    content: dict[str, CloneContentPage],
) -> dict[str, Any]:
    assert_clone_output_safe(output)
    _validate_unique_clone_source_paths(file_plan)
    _validate_clone_target_paths(output, file_plan)
    manifest = _manifest_for_clone(
        space=space,
        root_url=root_url,
        file_plan=file_plan,
        content=content,
    )
    _validate_generated_manifest(manifest)
    return manifest


def write_clone_project(
    *,
    output: Path,
    space: str,
    root_url: str | None,
    file_plan: CloneFilePlan,
    content: dict[str, CloneContentPage],
) -> None:
    manifest = _validate_clone_project(
        output=output,
        space=space,
        root_url=root_url,
        file_plan=file_plan,
        content=content,
    )
    planned_writes = [
        (page, _target_for_clone_page(output, page.source_path)) for page in file_plan.pages
    ]
    output.mkdir(parents=True, exist_ok=True)
    for page, target in planned_writes:
        converted = content.get(page.page_id)
        if converted is None:
            raise CloneError(f"Missing converted content for page {page.page_id}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(converted.markdown, encoding="utf-8")

    (output / ".confluence-publish.yml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


@dataclass(frozen=True)
class CloneResult:
    output: Path
    page_count: int
    file_plan: CloneFilePlan
    written: bool


async def clone_confluence_tree(request: CloneRequest, *, client) -> CloneResult:
    assert_clone_output_safe(request.output)
    tree = await client.get_page_tree(
        request.root_page_id,
        max_depth=request.max_depth,
        max_pages=request.max_pages,
    )
    file_plan = build_clone_file_plan(tree)
    content: dict[str, CloneContentPage] = {}
    unsupported_messages: list[str] = []
    for planned_page in file_plan.pages:
        remote = await client.get_page_storage(planned_page.page_id)
        converted = storage_to_markdown(remote.storage)
        if converted.unsupported:
            unsupported_messages.append(
                f"{planned_page.page_id} {planned_page.title}: "
                + "; ".join(converted.unsupported)
            )
        content[planned_page.page_id] = CloneContentPage(markdown=converted.markdown)

    if unsupported_messages:
        raise CloneError("Unsupported Confluence content: " + " | ".join(unsupported_messages))

    root_url = request.root_url or tree.url
    _validate_clone_project(
        output=request.output,
        space=request.space,
        root_url=root_url,
        file_plan=file_plan,
        content=content,
    )

    if not request.dry_run:
        write_clone_project(
            output=request.output,
            space=request.space,
            root_url=root_url,
            file_plan=file_plan,
            content=content,
        )

    return CloneResult(
        output=request.output,
        page_count=len(file_plan.pages),
        file_plan=file_plan,
        written=not request.dry_run,
    )


def format_clone_summary(result: CloneResult) -> str:
    status = "complete" if result.written else "dry run"
    file_lines = "\n".join(f"  - {page.source_path}" for page in result.file_plan.pages)
    if not result.written:
        return "\n".join(
            [
                f"Confluence clone {status}",
                f"Output: {result.output}",
                f"Pages: {result.page_count}",
                "Files:",
                file_lines,
                "Run clone without --dry-run to write files.",
            ]
        )
    return "\n".join(
        [
            f"Confluence clone {status}",
            f"Output: {result.output}",
            f"Pages: {result.page_count}",
            "Files:",
            file_lines,
            "Next commands:",
            f"  uv run publish-confluence --project {result.output} --check-sync",
            f"  uv run publish-confluence --project {result.output} --pull --yes",
        ]
    )
