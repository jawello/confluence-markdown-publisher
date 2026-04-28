from __future__ import annotations

import atexit
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote

from .cli import _prepare_single_page
from .manifest import ManifestPageSyncState, write_manifest_sync_state
from .mcp_atlassian import McpToolError
from .models import PageInfo, RenderedDiagram
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


def _root_item(plan: PublishPlan) -> PlannedPage:
    if plan.root_page_id is not None:
        for item in plan.items:
            if item.page_id == plan.root_page_id:
                return item
    if plan.root_url is not None:
        for item in plan.items:
            if item.url == plan.root_url:
                return item
    if not plan.items:
        raise RuntimeError("Cannot sync project without pages")
    return plan.items[0]


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fallback_page_url(*, space_key: str | None, page_id: str, title: str) -> str | None:
    if space_key is None:
        return None
    encoded_space = quote(space_key, safe="~")
    encoded_title = quote(title.replace(" ", "+"), safe="+")
    return f"/spaces/{encoded_space}/pages/{page_id}/{encoded_title}"


async def _readable_page_ids(
    plan: PublishPlan,
    *,
    client,
    checked: dict[str, bool] | None = None,
) -> dict[str, bool]:
    readable = dict(checked or {})
    for item in plan.items:
        if item.page_id is None or item.page_id in readable:
            continue
        try:
            await client.get_page(item.page_id)
        except McpToolError:
            readable[item.page_id] = False
        else:
            readable[item.page_id] = True
    return readable


async def sync_project(plan: PublishPlan, *, client, dry_run: bool, mmdc_bin: str) -> int:
    if not plan.sync_enabled:
        raise RuntimeError("sync_project requires a sync-enabled plan")
    if plan.space is None:
        raise RuntimeError("Cannot sync project without a Confluence space")

    root = _root_item(plan)
    checked: dict[str, bool] = {}
    if root.page_id is not None:
        try:
            await client.get_page(root.page_id)
        except McpToolError as exc:
            if not plan.recreate_missing_root:
                raise RuntimeError(
                    f"Cannot sync project because root page {root.page_id} for "
                    f"{root.source_path} is not readable. Enable --recreate-missing-root "
                    "or set sync.recreate_missing_root: true."
                ) from exc
            if plan.root_parent_page_id is None:
                raise RuntimeError(
                    "Cannot recreate missing root without root.parent_page_id in manifest"
                ) from exc
            try:
                await client.get_page(plan.root_parent_page_id)
            except McpToolError as parent_exc:
                raise RuntimeError(
                    f"Cannot recreate missing root because root.parent_page_id "
                    f"{plan.root_parent_page_id} is not readable: {parent_exc}"
                ) from parent_exc
            return await _recreate_full_tree(
                plan,
                client=client,
                dry_run=dry_run,
                mmdc_bin=mmdc_bin,
            )
        else:
            checked[root.page_id] = True

    readable = await _readable_page_ids(plan, client=client, checked=checked)
    stale_pages = [
        item
        for item in plan.items
        if item.page_id is not None and readable.get(item.page_id) is False
    ]
    if stale_pages and not plan.recreate_stale_pages:
        details = ", ".join(f"{item.source_path} ({item.page_id})" for item in stale_pages)
        raise RuntimeError(
            "Cannot sync project because existing page IDs are not readable: "
            f"{details}. Enable --recreate-stale-pages or set sync.recreate_stale_pages: true."
        )
    if stale_pages:
        return await _recreate_stale_pages(
            plan,
            stale_pages=stale_pages,
            client=client,
            dry_run=dry_run,
            mmdc_bin=mmdc_bin,
        )

    missing_pages = [item for item in plan.items if item.page_id is None]
    if missing_pages:
        details = ", ".join(item.source_path for item in missing_pages)
        raise RuntimeError(
            "Cannot sync project because pages are missing page_id values: "
            f"{details}. Page creation requires the page recovery task or create-missing support."
        )

    prepared_pages = prepare_sync_pages(plan, mmdc_bin=mmdc_bin)
    return await _publish_prepared_sync_pages(
        plan,
        prepared_pages=prepared_pages,
        client=client,
        dry_run=dry_run,
        page_ids={item.source_path: item.page_id for item in plan.items if item.page_id},
    )


async def _recreate_full_tree(plan: PublishPlan, *, client, dry_run: bool, mmdc_bin: str) -> int:
    raise RuntimeError("Full tree recreation requires the root recovery task")


async def _recreate_stale_pages(
    plan: PublishPlan,
    *,
    stale_pages: list[PlannedPage],
    client,
    dry_run: bool,
    mmdc_bin: str,
) -> int:
    raise RuntimeError("Stale page recreation requires the page recovery task")


async def _publish_prepared_sync_pages(
    plan: PublishPlan,
    *,
    prepared_pages: dict[str, PreparedSyncPage],
    client,
    dry_run: bool,
    page_ids: dict[str, str],
) -> int:
    for source_path, prepared in prepared_pages.items():
        page_id = page_ids.get(source_path)
        if page_id is None:
            continue
        if dry_run:
            print()
            print(f"Storage preview for {source_path}:")
            print(prepared.storage[:1200])
            continue
        for rendered in prepared.rendered_diagrams:
            await client.upload_attachment(page_id, rendered.output_path)
        await client.update_page_storage(
            page_id,
            prepared.item.title,
            prepared.storage,
            version_comment=plan.version_comment,
        )
    return 0
