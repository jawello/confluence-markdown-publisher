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
