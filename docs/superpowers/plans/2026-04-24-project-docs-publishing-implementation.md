# Project Docs Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manifest-first project publishing mode that republishes a documentation folder to existing Confluence pages.

**Architecture:** Keep the current single-page publisher as the rendering/conversion core, and add project-specific orchestration around it. The new flow is split into manifest loading, filesystem discovery, publish planning, reusable page publishing, batch execution, and CLI routing.

**Tech Stack:** Python 3.11, argparse, pathlib, dataclasses, PyYAML, markdown, BeautifulSoup, MCP Atlassian client, pytest, pytest-asyncio, uv.

---

## File Structure

- Create `src/confluence_publisher/manifest.py`
  - Load `.confluence-publish.yml`.
  - Validate manifest schema and defaults.
  - Normalize page paths to POSIX relative paths.
  - Validate optional Confluence URLs against `page_id`.

- Create `src/confluence_publisher/project.py`
  - Discover Markdown files under a project root.
  - Apply include and exclude glob patterns.
  - Return POSIX relative paths plus excluded count.

- Create `src/confluence_publisher/planner.py`
  - Match discovered Markdown files to manifest pages.
  - Validate missing manifest entries, missing files, duplicate page IDs, and parent `index.md` entries.
  - Resolve page titles from manifest, Markdown H1, or file stem.
  - Build immutable publish plan data.

- Create `src/confluence_publisher/batch.py`
  - Render and convert each planned page.
  - Print per-page previews for dry-run mode.
  - Upload attachments and update existing Confluence pages in live mode.

- Modify `src/confluence_publisher/cli.py`
  - Add project-mode arguments.
  - Validate mode-specific arguments.
  - Extract current single-page publishing body into reusable helpers.
  - Route `--project` to manifest loading, discovery, planning, and batch execution.

- Modify `src/confluence_publisher/mcp_atlassian.py`
  - Allow update page version comments to be passed by caller.

- Modify `src/confluence_publisher/models.py`
  - Add shared immutable data types only if needed by multiple modules.
  - Keep project-specific models in their modules unless shared broadly.

- Modify `pyproject.toml` and `uv.lock`
  - Add `PyYAML>=6,<7`.

- Create tests:
  - `tests/confluence_publisher/test_manifest.py`
  - `tests/confluence_publisher/test_project.py`
  - `tests/confluence_publisher/test_planner.py`
  - `tests/confluence_publisher/test_batch.py`
  - Extend `tests/confluence_publisher/test_cli.py`
  - Extend `tests/confluence_publisher/test_mcp_atlassian.py`

---

### Task 1: Manifest Loader

**Files:**
- Create: `src/confluence_publisher/manifest.py`
- Create: `tests/confluence_publisher/test_manifest.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Add PyYAML dependency**

Edit `pyproject.toml` dependencies to include PyYAML:

```toml
dependencies = [
    "beautifulsoup4>=4.12,<5",
    "markdown>=3.6,<4",
    "mcp>=1,<2",
    "PyYAML>=6,<7",
]
```

Run:

```bash
uv lock
```

Expected: lockfile updates successfully and includes PyYAML.

- [ ] **Step 2: Write failing tests for manifest loading**

Create `tests/confluence_publisher/test_manifest.py`:

```python
from pathlib import Path

import pytest

from confluence_publisher.manifest import (
    ManifestError,
    ProjectManifest,
    load_manifest,
)


def test_load_manifest_reads_pages_defaults_and_url(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
space: "~6393203da593cb822e995262"
include:
  - "**/*.md"
exclude:
  - "**/drafts/**"
defaults:
  version_comment: "Sync docs"
  output_format: "png"
  diagram_width: 1400
  allow_diagram_fallback: true
pages:
  guide/getting-started.md:
    page_id: "333333"
    title: "Getting Started"
    url: "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/333333/Getting+Started"
""".strip(),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert manifest == ProjectManifest(
        path=manifest_path,
        space="~6393203da593cb822e995262",
        include=("**/*.md",),
        exclude=("**/drafts/**",),
        version_comment="Sync docs",
        output_format="png",
        diagram_width=1400,
        allow_diagram_fallback=True,
        pages={
            "guide/getting-started.md": manifest.pages["guide/getting-started.md"],
        },
    )
    page = manifest.pages["guide/getting-started.md"]
    assert page.page_id == "333333"
    assert page.title == "Getting Started"
    assert page.url.endswith("/333333/Getting+Started")


def test_load_manifest_applies_defaults(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
pages:
  index.md:
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert manifest.include == ("**/*.md",)
    assert manifest.exclude == ()
    assert manifest.version_comment == "Sync documentation from Markdown"
    assert manifest.output_format == "svg"
    assert manifest.diagram_width == 1000
    assert manifest.allow_diagram_fallback is False
    assert manifest.pages["index.md"].page_id == "111111"


def test_load_manifest_rejects_missing_file(tmp_path):
    with pytest.raises(ManifestError, match="Manifest not found"):
        load_manifest(tmp_path / ".confluence-publish.yml")


def test_load_manifest_rejects_page_without_page_id(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
pages:
  index.md:
    title: "Home"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="index.md.*page_id"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_url_that_does_not_contain_page_id(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
pages:
  index.md:
    page_id: "111111"
    url: "https://criteo.atlassian.net/wiki/pages/222222/Home"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="url.*111111"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_duplicate_page_ids(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
pages:
  index.md:
    page_id: "111111"
  guide/index.md:
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="Duplicate page_id.*111111"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_invalid_output_format(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
defaults:
  output_format: "pdf"
pages:
  index.md:
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ManifestError, match="output_format"):
        load_manifest(manifest_path)
```

- [ ] **Step 3: Run manifest tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_manifest.py -v
```

Expected: FAIL because `confluence_publisher.manifest` does not exist.

- [ ] **Step 4: Implement manifest loader**

Create `src/confluence_publisher/manifest.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


DEFAULT_VERSION_COMMENT = "Sync documentation from Markdown"
DEFAULT_INCLUDE = ("**/*.md",)
SUPPORTED_OUTPUT_FORMATS = {"svg", "png"}


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ManifestPage:
    path: str
    page_id: str
    title: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class ProjectManifest:
    path: Path
    space: str | None
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    version_comment: str
    output_format: str
    diagram_width: int
    allow_diagram_fallback: bool
    pages: dict[str, ManifestPage]


def _as_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{field_name} must be a mapping")
    return value


def _as_string_list(value: Any, field_name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ManifestError(f"{field_name} must be a list of non-empty strings")
    return tuple(value)


def _normalize_page_path(value: str) -> str:
    normalized = PurePosixPath(value.replace("\\", "/")).as_posix().lstrip("/")
    if normalized in ("", ".") or normalized.startswith("../") or "/../" in normalized:
        raise ManifestError(f"Invalid page path: {value}")
    return normalized


def _optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{field_name} must be a non-empty string")
    return value


def _positive_int(value: Any, field_name: str, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or value <= 0:
        raise ManifestError(f"{field_name} must be a positive integer")
    return value


def load_manifest(path: Path) -> ProjectManifest:
    if not path.exists():
        raise ManifestError(f"Manifest not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ManifestError(f"Invalid YAML in manifest {path}: {exc}") from exc

    if raw is None:
        raw = {}
    root = _as_mapping(raw, "manifest")
    defaults = _as_mapping(root.get("defaults", {}), "defaults")
    pages_raw = _as_mapping(root.get("pages"), "pages")

    output_format = defaults.get("output_format", "svg")
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        raise ManifestError("defaults.output_format must be one of: png, svg")

    version_comment = defaults.get("version_comment", DEFAULT_VERSION_COMMENT)
    if not isinstance(version_comment, str) or not version_comment:
        raise ManifestError("defaults.version_comment must be a non-empty string")

    fallback = defaults.get("allow_diagram_fallback", False)
    if not isinstance(fallback, bool):
        raise ManifestError("defaults.allow_diagram_fallback must be a boolean")

    pages: dict[str, ManifestPage] = {}
    seen_page_ids: dict[str, str] = {}
    for raw_page_path, raw_page in pages_raw.items():
        if not isinstance(raw_page_path, str):
            raise ManifestError("pages keys must be strings")
        page_path = _normalize_page_path(raw_page_path)
        page_data = _as_mapping(raw_page, f"pages.{page_path}")
        page_id = page_data.get("page_id")
        if not isinstance(page_id, str) or not page_id:
            raise ManifestError(f"pages.{page_path}.page_id must be a non-empty string")
        if page_id in seen_page_ids:
            raise ManifestError(
                f"Duplicate page_id {page_id} for {seen_page_ids[page_id]} and {page_path}"
            )
        seen_page_ids[page_id] = page_path

        title = _optional_str(page_data.get("title"), f"pages.{page_path}.title")
        url = _optional_str(page_data.get("url"), f"pages.{page_path}.url")
        if url is not None and page_id not in url:
            raise ManifestError(f"pages.{page_path}.url must contain page_id {page_id}")

        pages[page_path] = ManifestPage(path=page_path, page_id=page_id, title=title, url=url)

    return ProjectManifest(
        path=path,
        space=_optional_str(root.get("space"), "space"),
        include=_as_string_list(root.get("include"), "include", DEFAULT_INCLUDE),
        exclude=_as_string_list(root.get("exclude"), "exclude", ()),
        version_comment=version_comment,
        output_format=output_format,
        diagram_width=_positive_int(defaults.get("diagram_width"), "defaults.diagram_width", 1000),
        allow_diagram_fallback=fallback,
        pages=pages,
    )
```

- [ ] **Step 5: Run manifest tests and verify they pass**

Run:

```bash
uv run pytest tests/confluence_publisher/test_manifest.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit manifest loader**

Run:

```bash
git add pyproject.toml uv.lock src/confluence_publisher/manifest.py tests/confluence_publisher/test_manifest.py
git commit -m "Add project manifest loader"
```

Expected: commit succeeds.

---

### Task 2: Project Markdown Discovery

**Files:**
- Create: `src/confluence_publisher/project.py`
- Create: `tests/confluence_publisher/test_project.py`

- [ ] **Step 1: Write failing discovery tests**

Create `tests/confluence_publisher/test_project.py`:

```python
from confluence_publisher.project import discover_markdown_files


def test_discover_markdown_files_returns_posix_relative_paths(tmp_path):
    (tmp_path / "guide").mkdir()
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "guide" / "getting-started.md").write_text("# Start\n", encoding="utf-8")
    (tmp_path / "guide" / "notes.txt").write_text("ignore\n", encoding="utf-8")

    result = discover_markdown_files(tmp_path, include=("**/*.md",), exclude=())

    assert result.included == ("guide/getting-started.md", "index.md")
    assert result.excluded_count == 0


def test_discover_markdown_files_applies_include_and_exclude(tmp_path):
    (tmp_path / "guide" / "drafts").mkdir(parents=True)
    (tmp_path / "reference").mkdir()
    (tmp_path / "guide" / "index.md").write_text("# Guide\n", encoding="utf-8")
    (tmp_path / "guide" / "drafts" / "idea.md").write_text("# Draft\n", encoding="utf-8")
    (tmp_path / "reference" / "api.md").write_text("# API\n", encoding="utf-8")

    result = discover_markdown_files(
        tmp_path,
        include=("guide/**/*.md",),
        exclude=("**/drafts/**",),
    )

    assert result.included == ("guide/index.md",)
    assert result.excluded_count == 1


def test_discover_markdown_files_deduplicates_overlapping_includes(tmp_path):
    (tmp_path / "guide").mkdir()
    (tmp_path / "guide" / "index.md").write_text("# Guide\n", encoding="utf-8")

    result = discover_markdown_files(
        tmp_path,
        include=("**/*.md", "guide/*.md"),
        exclude=(),
    )

    assert result.included == ("guide/index.md",)
```

- [ ] **Step 2: Run discovery tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_project.py -v
```

Expected: FAIL because `confluence_publisher.project` does not exist.

- [ ] **Step 3: Implement discovery**

Create `src/confluence_publisher/project.py`:

```python
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
```

- [ ] **Step 4: Run discovery tests and verify they pass**

Run:

```bash
uv run pytest tests/confluence_publisher/test_project.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit discovery**

Run:

```bash
git add src/confluence_publisher/project.py tests/confluence_publisher/test_project.py
git commit -m "Add project markdown discovery"
```

Expected: commit succeeds.

---

### Task 3: Publish Planner

**Files:**
- Create: `src/confluence_publisher/planner.py`
- Create: `tests/confluence_publisher/test_planner.py`

- [ ] **Step 1: Write failing planner tests**

Create `tests/confluence_publisher/test_planner.py`:

```python
import pytest

from confluence_publisher.manifest import ManifestPage, ProjectManifest
from confluence_publisher.planner import PlannerError, build_publish_plan


def manifest(tmp_path, pages):
    return ProjectManifest(
        path=tmp_path / ".confluence-publish.yml",
        space="~6393203da593cb822e995262",
        include=("**/*.md",),
        exclude=(),
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        pages=pages,
    )


def page(path, page_id, title=None, url=None):
    return ManifestPage(path=path, page_id=page_id, title=title, url=url)


def test_build_publish_plan_resolves_titles_and_parents(tmp_path):
    (tmp_path / "guide").mkdir()
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "guide" / "index.md").write_text("# Guide\n", encoding="utf-8")
    (tmp_path / "guide" / "getting-started.md").write_text("# Getting Started\n", encoding="utf-8")
    project_manifest = manifest(
        tmp_path,
        {
            "index.md": page("index.md", "111111", title="Root"),
            "guide/index.md": page("guide/index.md", "222222"),
            "guide/getting-started.md": page(
                "guide/getting-started.md",
                "333333",
                url="https://criteo.atlassian.net/wiki/pages/333333/Getting+Started",
            ),
        },
    )

    plan = build_publish_plan(
        project_root=tmp_path,
        manifest=project_manifest,
        discovered_paths=("guide/getting-started.md", "guide/index.md", "index.md"),
        excluded_count=2,
        version_comment="CLI comment",
        output_format="png",
        diagram_width=1200,
        allow_diagram_fallback=True,
    )

    assert plan.project_root == tmp_path
    assert plan.manifest_path == project_manifest.path
    assert plan.excluded_count == 2
    assert plan.version_comment == "CLI comment"
    assert plan.output_format == "png"
    assert plan.diagram_width == 1200
    assert plan.allow_diagram_fallback is True
    assert [item.source_path for item in plan.items] == (
        ["index.md", "guide/index.md", "guide/getting-started.md"]
    )
    assert plan.items[0].title == "Root"
    assert plan.items[1].title == "Guide"
    assert plan.items[2].title == "Getting Started"
    assert plan.items[2].parent_source_path == "guide/index.md"
    assert plan.items[2].url.endswith("/333333/Getting+Started")


def test_build_publish_plan_errors_for_discovered_file_missing_from_manifest(tmp_path):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "guide.md").write_text("# Guide\n", encoding="utf-8")
    project_manifest = manifest(tmp_path, {"index.md": page("index.md", "111111")})

    with pytest.raises(PlannerError, match="missing from manifest pages.*guide.md"):
        build_publish_plan(
            project_root=tmp_path,
            manifest=project_manifest,
            discovered_paths=("index.md", "guide.md"),
            excluded_count=0,
            version_comment=None,
            output_format=None,
            diagram_width=None,
            allow_diagram_fallback=None,
        )


def test_build_publish_plan_errors_for_manifest_file_missing_on_disk(tmp_path):
    project_manifest = manifest(tmp_path, {"index.md": page("index.md", "111111")})

    with pytest.raises(PlannerError, match="listed in manifest but missing on disk.*index.md"):
        build_publish_plan(
            project_root=tmp_path,
            manifest=project_manifest,
            discovered_paths=(),
            excluded_count=0,
            version_comment=None,
            output_format=None,
            diagram_width=None,
            allow_diagram_fallback=None,
        )


def test_build_publish_plan_errors_for_nested_page_without_parent_index(tmp_path):
    (tmp_path / "guide").mkdir()
    (tmp_path / "guide" / "getting-started.md").write_text("# Getting Started\n", encoding="utf-8")
    project_manifest = manifest(
        tmp_path,
        {"guide/getting-started.md": page("guide/getting-started.md", "333333")},
    )

    with pytest.raises(PlannerError, match="parent index.md.*guide/getting-started.md"):
        build_publish_plan(
            project_root=tmp_path,
            manifest=project_manifest,
            discovered_paths=("guide/getting-started.md",),
            excluded_count=0,
            version_comment=None,
            output_format=None,
            diagram_width=None,
            allow_diagram_fallback=None,
        )


def test_build_publish_plan_falls_back_to_file_stem_for_title(tmp_path):
    (tmp_path / "api-reference.md").write_text("No h1 here\n", encoding="utf-8")
    project_manifest = manifest(
        tmp_path,
        {"api-reference.md": page("api-reference.md", "444444")},
    )

    plan = build_publish_plan(
        project_root=tmp_path,
        manifest=project_manifest,
        discovered_paths=("api-reference.md",),
        excluded_count=0,
        version_comment=None,
        output_format=None,
        diagram_width=None,
        allow_diagram_fallback=None,
    )

    assert plan.items[0].title == "api-reference"
```

- [ ] **Step 2: Run planner tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_planner.py -v
```

Expected: FAIL because `confluence_publisher.planner` does not exist.

- [ ] **Step 3: Implement planner**

Create `src/confluence_publisher/planner.py`:

```python
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
        entries = "\n".join(f"  - {path}" for path in missing_from_manifest)
        raise PlannerError(
            "Cannot publish project: discovered Markdown files are missing from manifest pages:\n"
            f"{entries}"
        )

    missing_on_disk = sorted(
        path for path in manifest_paths if not (project_root / path).is_file()
    )
    if missing_on_disk:
        entries = "\n".join(f"  - {path}" for path in missing_on_disk)
        raise PlannerError(
            "Cannot publish project: pages are listed in manifest but missing on disk:\n"
            f"{entries}"
        )

    items: list[PlannedPage] = []
    for source_path in sorted(discovered_paths, key=_sort_key):
        manifest_page = manifest.pages[source_path]
        parent_source_path = _parent_for(source_path, manifest_paths)
        if PurePosixPath(source_path).parent.as_posix() not in ("", ".") and parent_source_path is None:
            raise PlannerError(f"Cannot publish project: parent index.md missing for {source_path}")

        absolute_path = project_root / source_path
        title = manifest_page.title or _title_from_markdown(absolute_path) or _fallback_title(source_path)
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
```

- [ ] **Step 4: Run planner tests and verify they pass**

Run:

```bash
uv run pytest tests/confluence_publisher/test_planner.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit planner**

Run:

```bash
git add src/confluence_publisher/planner.py tests/confluence_publisher/test_planner.py
git commit -m "Add project publish planner"
```

Expected: commit succeeds.

---

### Task 4: Reusable Page Publishing

**Files:**
- Modify: `src/confluence_publisher/cli.py`
- Modify: `src/confluence_publisher/mcp_atlassian.py`
- Modify: `tests/confluence_publisher/test_cli.py`
- Modify: `tests/confluence_publisher/test_mcp_atlassian.py`

- [ ] **Step 1: Write failing test for custom version comment**

Extend `tests/confluence_publisher/test_mcp_atlassian.py`:

```python
@pytest.mark.asyncio
async def test_client_updates_page_with_custom_version_comment():
    session = FakeMcpSession()
    client = McpAtlassianClient(session)

    await client.update_page_storage(
        "5151854234",
        "Test",
        "<p>hello</p>",
        version_comment="Sync docs",
    )

    assert session.calls == [
        (
            "confluence_update_page",
            {
                "page_id": "5151854234",
                "title": "Test",
                "content": "<p>hello</p>",
                "content_format": "storage",
                "is_minor_edit": True,
                "version_comment": "Sync docs",
            },
        )
    ]
```

- [ ] **Step 2: Run MCP test and verify it fails**

Run:

```bash
uv run pytest tests/confluence_publisher/test_mcp_atlassian.py::test_client_updates_page_with_custom_version_comment -v
```

Expected: FAIL because `update_page_storage` does not accept `version_comment`.

- [ ] **Step 3: Implement custom version comment**

Change `McpAtlassianClient.update_page_storage` in `src/confluence_publisher/mcp_atlassian.py`:

```python
    async def update_page_storage(
        self,
        page_id: str,
        title: str,
        storage: str,
        version_comment: str = "Rendered Mermaid diagrams from Markdown source",
    ) -> None:
        result = await self.session.call_tool(
            "confluence_update_page",
            {
                "page_id": page_id,
                "title": title,
                "content": storage,
                "content_format": "storage",
                "is_minor_edit": True,
                "version_comment": version_comment,
            },
        )
        _structured_content(result)
```

- [ ] **Step 4: Run MCP tests and verify they pass**

Run:

```bash
uv run pytest tests/confluence_publisher/test_mcp_atlassian.py -v
```

Expected: PASS.

- [ ] **Step 5: Write failing tests for reusable page publish helper**

Extend `tests/confluence_publisher/test_cli.py`:

```python
@pytest.mark.asyncio
async def test_publish_single_page_uses_supplied_version_comment(tmp_path, monkeypatch):
    source = tmp_path / "overview.md"
    source.write_text("# Overview\n\nPlain text.\n", encoding="utf-8")
    client = FakeClient()

    await cli.publish_single_page(
        page_id="5151854234",
        title="Test",
        source=source,
        output_format="svg",
        diagram_width=1000,
        mmdc_bin="mmdc",
        allow_diagram_fallback=False,
        dry_run=False,
        client=client,
        version_comment="Sync docs",
    )

    assert client.calls == [
        ("update_page_storage", "5151854234", "Test", False, True, False, "Sync docs")
    ]
```

Also update `FakeClient.update_page_storage` in the same test file:

```python
    async def update_page_storage(
        self,
        page_id: str,
        title: str,
        storage: str,
        version_comment: str = "Rendered Mermaid diagrams from Markdown source",
    ) -> None:
        self.calls.append(
            (
                "update_page_storage",
                page_id,
                title,
                "<ac:image" in storage,
                'ac:width="1000"' in storage,
                'ac:width="1200"' in storage,
                version_comment,
            )
        )
```

Update existing assertions in `tests/confluence_publisher/test_cli.py` that check `update_page_storage` calls:

```python
assert client.calls[1] == (
    "update_page_storage",
    "5151854234",
    "Test",
    True,
    True,
    False,
    "Rendered Mermaid diagrams from Markdown source",
)
```

- [ ] **Step 6: Run helper test and verify it fails**

Run:

```bash
uv run pytest tests/confluence_publisher/test_cli.py::test_publish_single_page_uses_supplied_version_comment -v
```

Expected: FAIL because `publish_single_page` does not exist.

- [ ] **Step 7: Extract reusable single-page helper**

In `src/confluence_publisher/cli.py`, add:

```python
DEFAULT_SINGLE_PAGE_VERSION_COMMENT = "Rendered Mermaid diagrams from Markdown source"


async def publish_single_page(
    *,
    page_id: str,
    title: str,
    source: Path,
    output_format: str,
    diagram_width: int,
    mmdc_bin: str,
    allow_diagram_fallback: bool,
    dry_run: bool,
    client,
    version_comment: str = DEFAULT_SINGLE_PAGE_VERSION_COMMENT,
) -> str:
    markdown_text = source.read_text(encoding="utf-8")
    page_slug = source.stem
    blocks = collect_mermaid_blocks(markdown_text, page_slug, output_format)
    diagram_fragments: dict[int, str] = {}
    rendered_diagrams: list[RenderedDiagram] = []

    with tempfile.TemporaryDirectory(prefix="confluence-mermaid-") as temp_path:
        temp_dir = Path(temp_path)
        renderer = MermaidRenderer(mmdc_bin)
        for block in blocks:
            try:
                rendered = renderer.render(block, temp_dir)
            except RenderError as exc:
                if not allow_diagram_fallback:
                    raise SystemExit(str(exc)) from exc
                diagram_fragments[block.ordinal] = fallback_mermaid_fragment(block.source)
                continue

            rendered_diagrams.append(rendered)
            diagram_fragments[block.ordinal] = diagram_fragment(
                block,
                width=block.display_width or diagram_width,
            )

        storage = convert_markdown_to_storage(
            markdown_text,
            page_slug=page_slug,
            output_format=output_format,
            diagram_fragments=diagram_fragments,
            allow_diagram_fallback=allow_diagram_fallback,
        )

        if not dry_run:
            for rendered in rendered_diagrams:
                await client.upload_attachment(page_id, rendered.output_path)
            await client.update_page_storage(page_id, title, storage, version_comment=version_comment)

    return storage
```

Then change the live path in `run_async` to call this helper instead of duplicating rendering and update logic. Keep preview printing behavior equivalent to the current CLI.

- [ ] **Step 8: Run CLI tests and verify they pass**

Run:

```bash
uv run pytest tests/confluence_publisher/test_cli.py tests/confluence_publisher/test_mcp_atlassian.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit reusable page publishing**

Run:

```bash
git add src/confluence_publisher/cli.py src/confluence_publisher/mcp_atlassian.py tests/confluence_publisher/test_cli.py tests/confluence_publisher/test_mcp_atlassian.py
git commit -m "Extract reusable page publishing"
```

Expected: commit succeeds.

---

### Task 5: Batch Publisher

**Files:**
- Create: `src/confluence_publisher/batch.py`
- Create: `tests/confluence_publisher/test_batch.py`

- [ ] **Step 1: Write failing batch tests**

Create `tests/confluence_publisher/test_batch.py`:

```python
from pathlib import Path

import pytest

from confluence_publisher import batch
from confluence_publisher.planner import PlannedPage, PublishPlan


class FakeClient:
    def __init__(self):
        self.calls = []

    async def upload_attachment(self, page_id: str, file_path: Path) -> None:
        self.calls.append(("upload_attachment", page_id, file_path.name))

    async def update_page_storage(
        self,
        page_id: str,
        title: str,
        storage: str,
        version_comment: str = "Rendered Mermaid diagrams from Markdown source",
    ) -> None:
        self.calls.append(("update_page_storage", page_id, title, version_comment, "<h1>" in storage))


def make_plan(tmp_path):
    source = tmp_path / "index.md"
    source.write_text("# Home\n\nPlain text.\n", encoding="utf-8")
    return PublishPlan(
        project_root=tmp_path,
        manifest_path=tmp_path / ".confluence-publish.yml",
        version_comment="Sync docs",
        output_format="svg",
        diagram_width=1000,
        allow_diagram_fallback=False,
        excluded_count=0,
        items=(
            PlannedPage(
                source_path="index.md",
                absolute_path=source,
                page_id="111111",
                title="Home",
                url="https://criteo.atlassian.net/wiki/pages/111111/Home",
                parent_source_path=None,
            ),
        ),
    )


def test_format_publish_plan_includes_urls_and_comment(tmp_path):
    plan = make_plan(tmp_path)

    text = batch.format_publish_plan(plan)

    assert f"Project root: {tmp_path}" in text
    assert f"Manifest: {tmp_path / '.confluence-publish.yml'}" in text
    assert "Updates: 1" in text
    assert "Excluded markdown files: 0" in text
    assert "Version comment: Sync docs" in text
    assert "index.md -> 111111" in text
    assert "Title: Home" in text
    assert "URL: https://criteo.atlassian.net/wiki/pages/111111/Home" in text


def test_format_publish_plan_warns_for_missing_url(tmp_path):
    plan = make_plan(tmp_path)
    item = plan.items[0]
    plan = PublishPlan(
        project_root=plan.project_root,
        manifest_path=plan.manifest_path,
        version_comment=plan.version_comment,
        output_format=plan.output_format,
        diagram_width=plan.diagram_width,
        allow_diagram_fallback=plan.allow_diagram_fallback,
        excluded_count=plan.excluded_count,
        items=(
            PlannedPage(
                source_path=item.source_path,
                absolute_path=item.absolute_path,
                page_id=item.page_id,
                title=item.title,
                url=None,
                parent_source_path=item.parent_source_path,
            ),
        ),
    )

    text = batch.format_publish_plan(plan)

    assert "URL: missing" in text


@pytest.mark.asyncio
async def test_publish_project_dry_run_does_not_write_to_confluence(tmp_path):
    plan = make_plan(tmp_path)
    client = FakeClient()

    result = await batch.publish_project(plan, client=client, dry_run=True, mmdc_bin="mmdc")

    assert result == 0
    assert client.calls == []


@pytest.mark.asyncio
async def test_publish_project_live_updates_each_page_with_comment(tmp_path):
    plan = make_plan(tmp_path)
    client = FakeClient()

    result = await batch.publish_project(plan, client=client, dry_run=False, mmdc_bin="mmdc")

    assert result == 0
    assert client.calls == [("update_page_storage", "111111", "Home", "Sync docs", True)]
```

- [ ] **Step 2: Run batch tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_batch.py -v
```

Expected: FAIL because `confluence_publisher.batch` does not exist.

- [ ] **Step 3: Implement batch publisher**

Create `src/confluence_publisher/batch.py`:

```python
from __future__ import annotations

from .cli import publish_single_page
from .planner import PublishPlan


def format_publish_plan(plan: PublishPlan) -> str:
    lines = [
        "Confluence project publish plan",
        f"Project root: {plan.project_root}",
        f"Manifest: {plan.manifest_path}",
        f"Updates: {len(plan.items)}",
        f"Excluded markdown files: {plan.excluded_count}",
        f"Version comment: {plan.version_comment}",
        "",
        "Pages:",
    ]
    for item in plan.items:
        lines.extend(
            [
                f"  - {item.source_path} -> {item.page_id}",
                f"    Title: {item.title}",
                f"    URL: {item.url or 'missing'}",
            ]
        )
        if item.parent_source_path is not None:
            lines.append(f"    Parent source: {item.parent_source_path}")
    return "\n".join(lines)


async def publish_project(plan: PublishPlan, *, client, dry_run: bool, mmdc_bin: str) -> int:
    for item in plan.items:
        storage = await publish_single_page(
            page_id=item.page_id,
            title=item.title,
            source=item.absolute_path,
            output_format=plan.output_format,
            diagram_width=plan.diagram_width,
            mmdc_bin=mmdc_bin,
            allow_diagram_fallback=plan.allow_diagram_fallback,
            dry_run=dry_run,
            client=client,
            version_comment=plan.version_comment,
        )
        if dry_run:
            print()
            print(f"Storage preview for {item.source_path}:")
            print(storage[:1200])
    return 0
```

- [ ] **Step 4: Run batch tests and verify they pass**

Run:

```bash
uv run pytest tests/confluence_publisher/test_batch.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit batch publisher**

Run:

```bash
git add src/confluence_publisher/batch.py tests/confluence_publisher/test_batch.py
git commit -m "Add project batch publisher"
```

Expected: commit succeeds.

---

### Task 6: CLI Project Mode

**Files:**
- Modify: `src/confluence_publisher/cli.py`
- Modify: `tests/confluence_publisher/test_cli.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing CLI parser tests**

Extend `tests/confluence_publisher/test_cli.py`:

```python
def test_arg_parser_accepts_project_mode():
    args = build_arg_parser().parse_args(["--project", "docs", "--plan-only"])

    assert args.project == Path("docs")
    assert args.plan_only is True
    assert args.manifest is None


def test_validate_args_rejects_source_and_project_together():
    args = build_arg_parser().parse_args(
        ["--source", "page.md", "--page-id", "111111", "--project", "docs"]
    )

    with pytest.raises(SystemExit):
        cli.validate_args(args)


def test_validate_args_requires_page_id_for_single_page():
    args = build_arg_parser().parse_args(["--source", "page.md"])

    with pytest.raises(SystemExit):
        cli.validate_args(args)


def test_validate_args_rejects_page_id_in_project_mode():
    args = build_arg_parser().parse_args(["--project", "docs", "--page-id", "111111"])

    with pytest.raises(SystemExit):
        cli.validate_args(args)
```

- [ ] **Step 2: Run CLI parser tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_cli.py::test_arg_parser_accepts_project_mode tests/confluence_publisher/test_cli.py::test_validate_args_rejects_source_and_project_together tests/confluence_publisher/test_cli.py::test_validate_args_requires_page_id_for_single_page tests/confluence_publisher/test_cli.py::test_validate_args_rejects_page_id_in_project_mode -v
```

Expected: FAIL because parser and `validate_args` do not support project mode.

- [ ] **Step 3: Add CLI arguments and validation**

Modify `build_arg_parser` in `src/confluence_publisher/cli.py`:

```python
    parser.add_argument("--source", type=Path)
    parser.add_argument("--page-id")
    parser.add_argument("--title")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--comment")
```

Add:

```python
def validate_args(args: argparse.Namespace) -> None:
    if args.source is not None and args.project is not None:
        raise SystemExit("--source and --project are mutually exclusive")
    if args.source is None and args.project is None:
        raise SystemExit("Either --source or --project is required")
    if args.source is not None:
        if not args.page_id:
            raise SystemExit("--page-id is required with --source")
        if args.plan_only:
            raise SystemExit("--plan-only is only supported with --project")
    if args.project is not None:
        if args.page_id:
            raise SystemExit("--page-id is not supported with --project")
        if args.title:
            raise SystemExit("--title is not supported with --project")
```

Call `validate_args(args)` at the start of `run_async`.

- [ ] **Step 4: Run CLI parser tests and verify they pass**

Run:

```bash
uv run pytest tests/confluence_publisher/test_cli.py::test_arg_parser_accepts_project_mode tests/confluence_publisher/test_cli.py::test_validate_args_rejects_source_and_project_together tests/confluence_publisher/test_cli.py::test_validate_args_requires_page_id_for_single_page tests/confluence_publisher/test_cli.py::test_validate_args_rejects_page_id_in_project_mode -v
```

Expected: PASS.

- [ ] **Step 5: Write failing project-mode orchestration tests**

Extend `tests/confluence_publisher/test_cli.py`:

```python
@pytest.mark.asyncio
async def test_run_async_project_plan_only_does_not_connect_to_mcp(tmp_path, monkeypatch, capsys):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
pages:
  index.md:
    page_id: "111111"
    title: "Home"
""".strip(),
        encoding="utf-8",
    )

    def fail_connect():
        raise AssertionError("plan-only must not connect to MCP")

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fail_connect)

    result = await cli.run_async(["--project", str(tmp_path), "--plan-only"])

    assert result == 0
    output = capsys.readouterr().out
    assert "Confluence project publish plan" in output
    assert "index.md -> 111111" in output


@pytest.mark.asyncio
async def test_run_async_project_missing_manifest_entry_exits_before_mcp(
    tmp_path,
    monkeypatch,
):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
pages:
  index.md:
    page_id: "111111"
""".strip(),
        encoding="utf-8",
    )

    def fail_connect():
        raise AssertionError("planning error must not connect to MCP")

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fail_connect)

    with pytest.raises(SystemExit, match="guide.md"):
        await cli.run_async(["--project", str(tmp_path), "--plan-only"])


@pytest.mark.asyncio
async def test_run_async_project_live_updates_with_comment(tmp_path, monkeypatch):
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / ".confluence-publish.yml").write_text(
        """
defaults:
  version_comment: "Manifest comment"
pages:
  index.md:
    page_id: "111111"
    title: "Home"
""".strip(),
        encoding="utf-8",
    )
    client = FakeClient()

    monkeypatch.setattr(cli, "connect_mcp_atlassian", fake_connect_for(client))

    result = await cli.run_async(
        ["--project", str(tmp_path), "--comment", "CLI comment", "--yes"]
    )

    assert result == 0
    assert client.calls == [
        ("update_page_storage", "111111", "Home", False, True, False, "CLI comment")
    ]
```

- [ ] **Step 6: Run project orchestration tests and verify they fail**

Run:

```bash
uv run pytest tests/confluence_publisher/test_cli.py::test_run_async_project_plan_only_does_not_connect_to_mcp tests/confluence_publisher/test_cli.py::test_run_async_project_missing_manifest_entry_exits_before_mcp tests/confluence_publisher/test_cli.py::test_run_async_project_live_updates_with_comment -v
```

Expected: FAIL because `run_async` has no project-mode branch.

- [ ] **Step 7: Implement project-mode branch**

Modify `src/confluence_publisher/cli.py` imports:

```python
from .batch import format_publish_plan, publish_project
from .manifest import ManifestError, load_manifest
from .planner import PlannerError, build_publish_plan
from .project import discover_markdown_files
```

Add helper:

```python
async def run_project_mode(args: argparse.Namespace) -> int:
    manifest_path = args.manifest or args.project / ".confluence-publish.yml"
    try:
        manifest = load_manifest(manifest_path)
        discovery = discover_markdown_files(
            args.project,
            include=manifest.include,
            exclude=manifest.exclude,
        )
        plan = build_publish_plan(
            project_root=args.project,
            manifest=manifest,
            discovered_paths=discovery.included,
            excluded_count=discovery.excluded_count,
            version_comment=args.comment,
            output_format=args.output_format,
            diagram_width=args.diagram_width,
            allow_diagram_fallback=(
                True if args.allow_diagram_fallback else None
            ),
        )
    except (ManifestError, PlannerError, OSError) as exc:
        raise SystemExit(str(exc)) from exc

    print(format_publish_plan(plan))

    if args.plan_only:
        return 0

    if args.dry_run:
        class DryRunClient:
            async def upload_attachment(self, page_id, file_path):
                raise AssertionError("dry-run must not upload attachments")

            async def update_page_storage(self, page_id, title, storage, version_comment):
                raise AssertionError("dry-run must not update pages")

        return await publish_project(plan, client=DryRunClient(), dry_run=True, mmdc_bin=args.mmdc_bin)

    if not args.yes:
        answer = input("Publish project to Confluence? Type 'yes' to continue: ")
        if answer != "yes":
            print("Aborted.")
            return 1

    async with connect_mcp_atlassian() as client:
        return await publish_project(plan, client=client, dry_run=False, mmdc_bin=args.mmdc_bin)
```

Then in `run_async`:

```python
    args = build_arg_parser().parse_args(argv)
    validate_args(args)
    if args.project is not None:
        return await run_project_mode(args)
```

Keep the existing single-page behavior after this branch.

- [ ] **Step 8: Run project orchestration tests and verify they pass**

Run:

```bash
uv run pytest tests/confluence_publisher/test_cli.py::test_run_async_project_plan_only_does_not_connect_to_mcp tests/confluence_publisher/test_cli.py::test_run_async_project_missing_manifest_entry_exits_before_mcp tests/confluence_publisher/test_cli.py::test_run_async_project_live_updates_with_comment -v
```

Expected: PASS.

- [ ] **Step 9: Update README**

Add this section to `README.md`:

```markdown
## Project Mode

Publish a documentation folder with a manifest:

```bash
uv run publish-confluence \
  --project docs \
  --plan-only
```

The default manifest is `docs/.confluence-publish.yml`:

```yaml
space: "~6393203da593cb822e995262"

include:
  - "**/*.md"

exclude:
  - "**/drafts/**"

defaults:
  version_comment: "Sync documentation from Markdown"

pages:
  index.md:
    page_id: "111111"
    title: "Project Documentation"
    url: "https://criteo.atlassian.net/wiki/pages/111111/Project+Documentation"
```

Project mode never creates Confluence pages. Every discovered Markdown file must have a `page_id` in the manifest.

Publish with a version comment:

```bash
uv run publish-confluence \
  --project docs \
  --comment "Sync guide updates" \
  --yes
```
```

- [ ] **Step 10: Run all CLI tests and verify they pass**

Run:

```bash
uv run pytest tests/confluence_publisher/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 11: Commit CLI project mode**

Run:

```bash
git add src/confluence_publisher/cli.py tests/confluence_publisher/test_cli.py README.md
git commit -m "Add project publishing CLI mode"
```

Expected: commit succeeds.

---

### Task 7: Full Verification And Cleanup

**Files:**
- Modify only files needed for fixes discovered by verification.

- [ ] **Step 1: Run the full test suite**

Run:

```bash
uv run pytest tests/confluence_publisher -v
```

Expected: PASS for all tests.

- [ ] **Step 2: Run a smoke plan-only command against a temporary docs project**

Create a temporary docs project with:

```text
/tmp/confluence-publisher-smoke-docs/
  .confluence-publish.yml
  index.md
  guide/
    index.md
    getting-started.md
```

Use this manifest:

```yaml
pages:
  index.md:
    page_id: "111111"
    title: "Home"
    url: "https://criteo.atlassian.net/wiki/pages/111111/Home"
  guide/index.md:
    page_id: "222222"
    title: "Guide"
    url: "https://criteo.atlassian.net/wiki/pages/222222/Guide"
  guide/getting-started.md:
    page_id: "333333"
    title: "Getting Started"
    url: "https://criteo.atlassian.net/wiki/pages/333333/Getting+Started"
```

Run:

```bash
uv run publish-confluence --project /tmp/confluence-publisher-smoke-docs --plan-only
```

Expected output includes:

```text
Confluence project publish plan
Updates: 3
index.md -> 111111
guide/index.md -> 222222
guide/getting-started.md -> 333333
```

- [ ] **Step 3: Check git diff**

Run:

```bash
git diff --check
git status --short
```

Expected:

- `git diff --check` prints no whitespace errors.
- `git status --short` only shows intended changed files and any pre-existing unrelated files such as `.codex`.

- [ ] **Step 4: Commit verification fixes if needed**

If Step 1, Step 2, or Step 3 required code or test fixes, commit them:

```bash
git add src tests README.md pyproject.toml uv.lock
git commit -m "Stabilize project publishing mode"
```

Expected: commit succeeds, or there are no verification fixes to commit.

---

## Self-Review

Spec coverage:

- Manifest-first project mode: Task 1 and Task 6.
- Include and exclude discovery: Task 2.
- `index.md` parent behavior: Task 3.
- No automatic page creation and missing `page_id` failures: Task 1 and Task 3.
- Version comments: Task 4, Task 5, and Task 6.
- Full URL in manifest and plan output: Task 1 and Task 5.
- Existing single-page compatibility: Task 4 and Task 6.
- TDD workflow: every implementation task starts with failing tests, then implementation, then passing tests.

Placeholder scan:

- No placeholder markers or unspecified test-writing steps are intentionally left in the plan.
- Each task names exact files and commands.

Type consistency:

- Manifest data types are defined in Task 1 and consumed by Task 3.
- `PublishPlan` and `PlannedPage` are defined in Task 3 and consumed by Task 5.
- `publish_single_page` is defined in Task 4 and consumed by Task 5.
- CLI project mode composes `load_manifest`, `discover_markdown_files`, `build_publish_plan`, `format_publish_plan`, and `publish_project` using the signatures defined earlier.
