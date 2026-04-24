from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiscoveryResult:
    included: tuple[str, ...]
    excluded_count: int


def _relative_posix(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _validate_patterns(patterns: tuple[str, ...]) -> None:
    for pattern in patterns:
        normalized = pattern.replace("\\", "/")
        if normalized.startswith("/") or (
            len(normalized) >= 2 and normalized[0].isalpha() and normalized[1] == ":"
        ):
            raise ValueError(f"Pattern must be project-relative, not absolute: {pattern}")
        if ".." in normalized.split("/"):
            raise ValueError(f"Pattern contains parent traversal: {pattern}")


def _markdown_glob_pattern(pattern: str) -> str:
    if pattern.lower().endswith(".md"):
        return f"{pattern[:-3]}.[mM][dD]"
    return pattern


def _has_project_symlink(project_root: Path, path: Path) -> bool:
    current = path
    while current != project_root:
        if current.is_symlink():
            return True
        current = current.parent
    return False


def _discover_paths(project_root: Path, patterns: tuple[str, ...]) -> set[str]:
    discovered: set[str] = set()
    for pattern in patterns:
        for path in project_root.glob(_markdown_glob_pattern(pattern)):
            if (
                not _has_project_symlink(project_root, path)
                and path.is_file()
                and path.suffix.lower() == ".md"
            ):
                discovered.add(_relative_posix(project_root, path))
    return discovered


def discover_markdown_files(
    project_root: Path,
    *,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
) -> DiscoveryResult:
    if not project_root.exists():
        raise FileNotFoundError(f"Project root not found: {project_root}")
    if not project_root.is_dir():
        raise NotADirectoryError(f"Project root is not a directory: {project_root}")

    _validate_patterns(include)
    _validate_patterns(exclude)

    discovered = _discover_paths(project_root, include)
    excluded = _discover_paths(project_root, exclude)

    included: list[str] = []
    excluded_count = 0
    for relative_path in sorted(discovered):
        if relative_path in excluded:
            excluded_count += 1
            continue
        included.append(relative_path)

    return DiscoveryResult(included=tuple(included), excluded_count=excluded_count)
