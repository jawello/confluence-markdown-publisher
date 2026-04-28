from __future__ import annotations

import atexit
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from .cli import _prepare_single_page
from .models import RenderedDiagram
from .planner import PlannedPage, PublishPlan


CONTENT_HASH_VERSION = "storage:v1"
_SAFE_PATH_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_PAGE_DIR_SLUG_MAX_LENGTH = 64


@dataclass(frozen=True)
class PreparedSyncPage:
    item: PlannedPage
    storage: str
    rendered_diagrams: list[RenderedDiagram]
    content_hash: str


def compute_content_hash(
    title: str,
    storage: str,
    output_format: str,
    diagram_width: int,
    allow_diagram_fallback: bool,
) -> str:
    payload = {
        "version": CONTENT_HASH_VERSION,
        "title": title,
        "storage": storage,
        "output_format": output_format,
        "diagram_width": diagram_width,
        "allow_diagram_fallback": allow_diagram_fallback,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _safe_source_slug(source_path: str) -> str:
    return _SAFE_PATH_RE.sub("-", source_path).strip("-") or "page"


def _page_temp_dir_name(source_path: str, page_index: int) -> str:
    slug = _safe_source_slug(source_path)[:_PAGE_DIR_SLUG_MAX_LENGTH].rstrip(".-") or "page"
    hash8 = sha256(source_path.encode("utf-8")).hexdigest()[:8]
    return f"{page_index:04d}-{slug}-{hash8}"


def _ensure_temp_root(temp_root: Path | None) -> Path:
    if temp_root is not None:
        return temp_root
    created = Path(tempfile.mkdtemp(prefix="confluence-sync-"))
    atexit.register(shutil.rmtree, created, ignore_errors=True)
    return created


def _copy_rendered_diagrams(
    *,
    rendered_diagrams: list[RenderedDiagram],
    item: PlannedPage,
    page_index: int,
    temp_root: Path | None,
) -> tuple[list[RenderedDiagram], Path | None]:
    if not rendered_diagrams:
        return [], temp_root

    temp_root = _ensure_temp_root(temp_root)
    page_dir = temp_root / _page_temp_dir_name(item.source_path, page_index)
    page_dir.mkdir(parents=True, exist_ok=True)

    copied: list[RenderedDiagram] = []
    for rendered in rendered_diagrams:
        mmd_path = page_dir / rendered.mmd_path.name
        output_path = page_dir / rendered.output_path.name
        shutil.copy2(rendered.mmd_path, mmd_path)
        shutil.copy2(rendered.output_path, output_path)
        copied.append(
            RenderedDiagram(
                block=rendered.block,
                mmd_path=mmd_path,
                output_path=output_path,
            )
        )
    return copied, temp_root


def prepare_sync_pages(plan: PublishPlan, *, mmdc_bin: str) -> dict[str, PreparedSyncPage]:
    prepared_pages: dict[str, PreparedSyncPage] = {}
    temp_root: Path | None = None
    for page_index, item in enumerate(plan.items, start=1):
        with _prepare_single_page(
            source=item.absolute_path,
            output_format=plan.output_format,
            diagram_width=plan.diagram_width,
            mmdc_bin=mmdc_bin,
            allow_diagram_fallback=plan.allow_diagram_fallback,
        ) as prepared:
            content_hash = compute_content_hash(
                title=item.title,
                storage=prepared.storage,
                output_format=plan.output_format,
                diagram_width=plan.diagram_width,
                allow_diagram_fallback=plan.allow_diagram_fallback,
            )
            rendered_diagrams, temp_root = _copy_rendered_diagrams(
                rendered_diagrams=prepared.rendered_diagrams,
                item=item,
                page_index=page_index,
                temp_root=temp_root,
            )
            prepared_pages[item.source_path] = PreparedSyncPage(
                item=item,
                storage=prepared.storage,
                rendered_diagrams=rendered_diagrams,
                content_hash=content_hash,
            )
    return prepared_pages
