from __future__ import annotations

from .cli import publish_single_page
from .manifest import write_manifest_page_ids
from .planner import PublishPlan


CREATED_PAGE_STORAGE = "<p>Created by confluence-markdown-publisher.</p>"


def format_publish_plan(plan: PublishPlan) -> str:
    lines = [
        "Confluence project publish plan",
        f"Project root: {plan.project_root}",
        f"Manifest: {plan.manifest_path}",
        f"Updates: {len(plan.items)}",
        f"Creates: {sum(1 for item in plan.items if item.page_id is None)}",
        f"Excluded markdown files: {plan.excluded_count}",
        f"Version comment: {plan.version_comment}",
    ]
    if plan.root_page_id is not None:
        lines.append(f"Root page: {plan.root_page_id}")
    lines.extend(["", "Pages:"])
    for item in plan.items:
        lines.extend(
            [
                f"  - {item.source_path} -> {item.page_id or '<missing>'}",
                f"    Title: {item.title}",
                f"    URL: {item.url or 'missing'}",
            ]
        )
        if item.parent_source_path is not None:
            lines.append(f"    Parent source: {item.parent_source_path}")
    return "\n".join(lines)


async def publish_project(plan: PublishPlan, *, client, dry_run: bool, mmdc_bin: str) -> int:
    page_ids = {item.source_path: item.page_id for item in plan.items if item.page_id is not None}

    if plan.create_missing_pages and not dry_run:
        if plan.root_page_id is None or plan.space is None:
            raise RuntimeError("Cannot create missing pages without root page and space")
        created_page_ids: dict[str, str] = {}
        for item in plan.items:
            if item.page_id is not None:
                continue
            parent_id = (
                page_ids[item.parent_source_path]
                if item.parent_source_path is not None
                else plan.root_page_id
            )
            created = await client.create_page(
                space_key=plan.space,
                title=item.title,
                parent_id=parent_id,
                storage=CREATED_PAGE_STORAGE,
            )
            page_ids[item.source_path] = created.page_id
            created_page_ids[item.source_path] = created.page_id
            write_manifest_page_ids(plan.manifest_path, created_page_ids)

    for item in plan.items:
        page_id = page_ids.get(item.source_path) or "<missing>"
        storage = await publish_single_page(
            page_id=page_id,
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
