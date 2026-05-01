from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from .manifest import ManifestPageSyncState, write_manifest_sync_state
from .planner import PlannedPage, PublishPlan
from .storage_to_markdown import storage_to_markdown


class SyncStatus(StrEnum):
    IN_SYNC = "in_sync"
    LOCAL_ONLY = "local_only"
    LOCAL_CHANGED = "local_changed"
    REMOTE_CHANGED = "remote_changed"
    CONFLICT = "conflict"
    UNKNOWN_DIVERGENCE = "unknown_divergence"
    UNSUPPORTED_REMOTE = "unsupported_remote"


@dataclass(frozen=True)
class PageInvariant:
    source_path: str
    page_id: str | None
    status: SyncStatus
    local_hash: str
    remote_hash: str | None
    baseline_hash: str | None
    remote_markdown: str | None = None
    unsupported: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectInvariantReport:
    pages: tuple[PageInvariant, ...]

    @property
    def has_conflicts(self) -> bool:
        unsafe = {
            SyncStatus.CONFLICT,
            SyncStatus.UNKNOWN_DIVERGENCE,
            SyncStatus.UNSUPPORTED_REMOTE,
        }
        return any(page.status in unsafe for page in self.pages)


@dataclass(frozen=True)
class PullResult:
    report: ProjectInvariantReport
    updated_paths: tuple[str, ...]


def canonicalize_markdown(markdown_text: str) -> str:
    lines = [
        line.rstrip()
        for line in markdown_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def compute_canonical_markdown_hash(markdown_text: str) -> str:
    encoded = canonicalize_markdown(markdown_text).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _status_for(
    *,
    local_hash: str,
    remote_hash: str,
    baseline_hash: str | None,
) -> SyncStatus:
    if local_hash == remote_hash:
        return SyncStatus.IN_SYNC
    if baseline_hash is None:
        return SyncStatus.UNKNOWN_DIVERGENCE

    local_changed = local_hash != baseline_hash
    remote_changed = remote_hash != baseline_hash
    if local_changed and remote_changed:
        return SyncStatus.CONFLICT
    if local_changed:
        return SyncStatus.LOCAL_CHANGED
    if remote_changed:
        return SyncStatus.REMOTE_CHANGED
    return SyncStatus.UNKNOWN_DIVERGENCE


async def _invariant_for_item(item: PlannedPage, *, client) -> PageInvariant:
    local_text = item.absolute_path.read_text(encoding="utf-8")
    local_hash = compute_canonical_markdown_hash(local_text)
    if item.page_id is None:
        return PageInvariant(
            source_path=item.source_path,
            page_id=None,
            status=SyncStatus.LOCAL_ONLY,
            local_hash=local_hash,
            remote_hash=None,
            baseline_hash=item.canonical_hash,
        )

    remote = await client.get_page_storage(item.page_id)
    converted = storage_to_markdown(remote.storage)
    if converted.unsupported:
        return PageInvariant(
            source_path=item.source_path,
            page_id=item.page_id,
            status=SyncStatus.UNSUPPORTED_REMOTE,
            local_hash=local_hash,
            remote_hash=None,
            baseline_hash=item.canonical_hash,
            remote_markdown=converted.markdown,
            unsupported=converted.unsupported,
        )

    remote_hash = compute_canonical_markdown_hash(converted.markdown)
    return PageInvariant(
        source_path=item.source_path,
        page_id=item.page_id,
        status=_status_for(
            local_hash=local_hash,
            remote_hash=remote_hash,
            baseline_hash=item.canonical_hash,
        ),
        local_hash=local_hash,
        remote_hash=remote_hash,
        baseline_hash=item.canonical_hash,
        remote_markdown=converted.markdown,
    )


async def check_project_invariants(plan: PublishPlan, *, client) -> ProjectInvariantReport:
    pages = []
    for item in plan.items:
        pages.append(await _invariant_for_item(item, client=client))
    return ProjectInvariantReport(pages=tuple(pages))


def format_invariant_report(report: ProjectInvariantReport) -> str:
    lines = ["Confluence bidirectional sync check", "", "Pages:"]
    for page in report.pages:
        lines.append(f"  - {page.source_path}: {page.status.value}")
        if page.unsupported:
            lines.append(f"    Unsupported: {'; '.join(page.unsupported)}")
    return "\n".join(lines)


async def pull_remote_changes(
    plan: PublishPlan,
    *,
    client,
    dry_run: bool,
) -> PullResult:
    report = await check_project_invariants(plan, client=client)
    unsafe_pages = [
        page
        for page in report.pages
        if page.status in {
            SyncStatus.CONFLICT,
            SyncStatus.UNKNOWN_DIVERGENCE,
            SyncStatus.UNSUPPORTED_REMOTE,
            SyncStatus.LOCAL_CHANGED,
        }
    ]
    if unsafe_pages:
        details = ", ".join(f"{page.source_path} ({page.status.value})" for page in unsafe_pages)
        raise RuntimeError(f"Remote changes are unsafe to pull: {details}")

    updated_paths: list[str] = []
    items_by_source = {item.source_path: item for item in plan.items}
    for page in report.pages:
        if page.status != SyncStatus.REMOTE_CHANGED or page.remote_markdown is None:
            continue
        item = items_by_source[page.source_path]
        if not dry_run:
            item.absolute_path.write_text(page.remote_markdown, encoding="utf-8")
            write_manifest_sync_state(
                plan.manifest_path,
                pages={
                    page.source_path: ManifestPageSyncState(
                        canonical_hash=compute_canonical_markdown_hash(page.remote_markdown),
                    )
                },
            )
        updated_paths.append(page.source_path)

    return PullResult(report=report, updated_paths=tuple(updated_paths))
