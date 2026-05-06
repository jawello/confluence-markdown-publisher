from __future__ import annotations

from .planner import PublishPlan


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
    if plan.sync_enabled:
        lines.extend(
            [
                "Sync: enabled",
                f"Recreate missing root: {'yes' if plan.recreate_missing_root else 'no'}",
                f"Recreate stale pages: {'yes' if plan.recreate_stale_pages else 'no'}",
            ]
        )
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
