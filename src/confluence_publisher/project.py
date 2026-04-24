from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiscoveryResult:
    included: tuple[str, ...]
    excluded_count: int


def _relative_posix(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _matches_any(root: Path, relative_path: str, patterns: tuple[str, ...]) -> bool:
    path = root / relative_path
    return any(path.match(pattern) or Path(relative_path).match(pattern) for pattern in patterns)


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

    discovered: set[str] = set()
    for pattern in include:
        for path in project_root.glob(pattern):
            if path.is_file() and path.suffix.lower() == ".md":
                discovered.add(_relative_posix(project_root, path))

    included: list[str] = []
    excluded_count = 0
    for relative_path in sorted(discovered):
        if _matches_any(project_root, relative_path, exclude):
            excluded_count += 1
            continue
        included.append(relative_path)

    return DiscoveryResult(included=tuple(included), excluded_count=excluded_count)
