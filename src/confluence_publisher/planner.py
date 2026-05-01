from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re

from .manifest import ProjectManifest


class PlannerError(ValueError):
    pass


@dataclass(frozen=True)
class PlannedPage:
    source_path: str
    absolute_path: Path
    page_id: str | None
    title: str
    url: str | None
    parent_source_path: str | None
    content_hash: str | None = None
    canonical_hash: str | None = None
    last_synced_at: str | None = None


@dataclass(frozen=True)
class PublishPlan:
    project_root: Path
    manifest_path: Path
    version_comment: str
    output_format: str
    diagram_width: int
    allow_diagram_fallback: bool
    excluded_count: int
    items: tuple[PlannedPage, ...]
    space: str | None = None
    root_source_path: str | None = None
    root_page_id: str | None = None
    root_url: str | None = None
    root_parent_page_id: str | None = None
    root_parent_url: str | None = None
    create_missing_pages: bool = False
    sync_enabled: bool = False
    recreate_missing_root: bool = False
    recreate_stale_pages: bool = False


_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def _title_from_markdown(path: Path) -> str | None:
    match = _H1_RE.search(path.read_text(encoding="utf-8"))
    return match.group(1).strip() if match else None


def _fallback_title(source_path: str) -> str:
    return PurePosixPath(source_path).stem


def _sort_key(source_path: str) -> tuple[str, int, str]:
    parent = str(PurePosixPath(source_path).parent)
    parent = "" if parent == "." else parent
    is_index = 0 if PurePosixPath(source_path).name == "index.md" else 1
    return (parent, is_index, source_path)


def _index_parent_for(source_path: str, manifest_paths: set[str]) -> str | None:
    path = PurePosixPath(source_path)
    if path.name == "index.md":
        if str(path.parent) in ("", "."):
            return None
        parent_dir = path.parent.parent
    else:
        parent_dir = path.parent

    while str(parent_dir) not in ("", "."):
        candidate = (parent_dir / "index.md").as_posix()
        if candidate in manifest_paths:
            return candidate
        parent_dir = parent_dir.parent

    if "index.md" in manifest_paths and source_path != "index.md":
        return "index.md"
    return None


def _is_top_level(source_path: str) -> bool:
    return PurePosixPath(source_path).parent.as_posix() in ("", ".")


def _parent_for(source_path: str, manifest: ProjectManifest) -> str | None:
    manifest_page = manifest.pages[source_path]
    if manifest_page.parent is not None:
        return manifest_page.parent

    manifest_paths = set(manifest.pages)
    if manifest.root_source_path is not None:
        if source_path == manifest.root_source_path:
            return None
        if _is_top_level(source_path):
            return manifest.root_source_path
        parent = _index_parent_for(source_path, manifest_paths)
        if parent is not None:
            return parent
        path = PurePosixPath(source_path)
        if path.name == "index.md" and len(path.parts) == 2:
            return manifest.root_source_path
        return None

    return _index_parent_for(source_path, manifest_paths)


def _ordered_source_paths(
    source_paths: tuple[str, ...],
    parent_by_path: dict[str, str | None],
) -> list[str]:
    ordered: list[str] = []
    visited: set[str] = set()
    stack: list[str] = []

    def visit(source_path: str) -> None:
        if source_path in visited:
            return
        if source_path in stack:
            cycle = " -> ".join(stack[stack.index(source_path) :] + [source_path])
            raise PlannerError(f"Cannot publish project: parent cycle detected: {cycle}")

        stack.append(source_path)
        parent = parent_by_path[source_path]
        if parent is not None:
            visit(parent)
        stack.pop()
        visited.add(source_path)
        ordered.append(source_path)

    for source_path in sorted(source_paths, key=_sort_key):
        visit(source_path)

    return ordered


def build_publish_plan(
    *,
    project_root: Path,
    manifest: ProjectManifest,
    discovered_paths: tuple[str, ...],
    excluded_count: int,
    version_comment: str | None,
    output_format: str | None,
    diagram_width: int | None,
    allow_diagram_fallback: bool | None,
    create_missing_pages: bool = False,
    sync_enabled: bool = False,
    recreate_missing_root: bool = False,
    recreate_stale_pages: bool = False,
) -> PublishPlan:
    discovered = set(discovered_paths)
    manifest_paths = set(manifest.pages)

    missing_from_manifest = sorted(discovered - manifest_paths)
    if missing_from_manifest:
        entries = ", ".join(missing_from_manifest)
        raise PlannerError(
            "Cannot publish project: discovered Markdown files are missing from manifest pages: "
            f"{entries}"
        )

    missing_on_disk = sorted(
        path for path in manifest_paths if not (project_root / path).is_file()
    )
    if missing_on_disk:
        entries = ", ".join(missing_on_disk)
        raise PlannerError(
            "Cannot publish project: pages are listed in manifest but missing on disk: "
            f"{entries}"
        )
    if manifest.root_source_path is not None and manifest.root_source_path not in discovered:
        raise PlannerError(
            "Cannot publish project: root.source_path "
            f"{manifest.root_source_path} is not included in the publish plan"
        )

    missing_page_ids = sorted(
        source_path
        for source_path in discovered
        if manifest.pages[source_path].page_id is None
    )
    if missing_page_ids and not (create_missing_pages or sync_enabled):
        entries = ", ".join(missing_page_ids)
        raise PlannerError(
            "Cannot publish project: pages are missing page_id values: "
            f"{entries}"
        )
    if missing_page_ids and create_missing_pages:
        if not manifest.space:
            raise PlannerError("Cannot create missing pages without a Confluence space")
        if manifest.root_source_path is not None:
            root_page = manifest.pages[manifest.root_source_path]
            if root_page.page_id is None and not manifest.root_parent_page_id:
                raise PlannerError(
                    "Cannot create missing root page without root.parent_page_id"
                )
        elif not manifest.root_page_id:
            raise PlannerError("Cannot create missing pages without a root page")

    parent_by_path: dict[str, str | None] = {}
    for source_path in discovered_paths:
        parent_source_path = _parent_for(source_path, manifest)
        is_nested = PurePosixPath(source_path).parent.as_posix() not in ("", ".")
        if is_nested and parent_source_path is None:
            raise PlannerError(f"Cannot publish project: parent index.md missing for {source_path}")
        if parent_source_path is not None and parent_source_path not in discovered:
            raise PlannerError(
                "Cannot publish project: parent page "
                f"{parent_source_path} for {source_path} is not included in the publish plan"
            )
        parent_by_path[source_path] = parent_source_path

    items: list[PlannedPage] = []
    for source_path in _ordered_source_paths(discovered_paths, parent_by_path):
        manifest_page = manifest.pages[source_path]
        absolute_path = project_root / source_path
        title = (
            manifest_page.title
            or _title_from_markdown(absolute_path)
            or _fallback_title(source_path)
        )
        items.append(
            PlannedPage(
                source_path=source_path,
                absolute_path=absolute_path,
                page_id=manifest_page.page_id,
                title=title,
                url=manifest_page.url,
                parent_source_path=parent_by_path[source_path],
                content_hash=manifest_page.content_hash,
                canonical_hash=manifest_page.canonical_hash,
                last_synced_at=manifest_page.last_synced_at,
            )
        )

    return PublishPlan(
        project_root=project_root,
        manifest_path=manifest.path,
        version_comment=version_comment or manifest.version_comment,
        output_format=output_format or manifest.output_format,
        diagram_width=diagram_width or manifest.diagram_width,
        allow_diagram_fallback=(
            manifest.allow_diagram_fallback
            if allow_diagram_fallback is None
            else allow_diagram_fallback
        ),
        space=manifest.space,
        root_source_path=manifest.root_source_path,
        root_page_id=manifest.root_page_id,
        root_url=manifest.root_url,
        root_parent_page_id=manifest.root_parent_page_id,
        root_parent_url=manifest.root_parent_url,
        create_missing_pages=create_missing_pages,
        sync_enabled=sync_enabled,
        recreate_missing_root=recreate_missing_root,
        recreate_stale_pages=recreate_stale_pages,
        excluded_count=excluded_count,
        items=tuple(items),
    )
