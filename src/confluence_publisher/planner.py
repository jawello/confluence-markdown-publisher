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
    page_id: str
    title: str
    url: str | None
    parent_source_path: str | None


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


def _parent_for(source_path: str, manifest_paths: set[str]) -> str | None:
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

    items: list[PlannedPage] = []
    for source_path in sorted(discovered_paths, key=_sort_key):
        manifest_page = manifest.pages[source_path]
        parent_source_path = _parent_for(source_path, manifest_paths)
        is_nested = PurePosixPath(source_path).parent.as_posix() not in ("", ".")
        if is_nested and parent_source_path is None:
            raise PlannerError(f"Cannot publish project: parent index.md missing for {source_path}")

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
                parent_source_path=parent_source_path,
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
        excluded_count=excluded_count,
        items=tuple(items),
    )
