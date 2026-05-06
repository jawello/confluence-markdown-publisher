from pathlib import Path

import pytest

from confluence_publisher.bidirectional import (
    SyncStatus,
    check_project_invariants,
    compute_canonical_markdown_hash,
    pull_remote_changes,
)
from confluence_publisher.manifest import load_manifest
from confluence_publisher.models import PageStorage
from confluence_publisher.planner import PlannedPage, PublishPlan


class FakeContentClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def get_page_storage(self, page_id: str) -> PageStorage:
        self.calls.append(("get_page_storage", page_id))
        return self.pages[page_id]


def make_plan(tmp_path, *, local_text, remote_page_id="111111", canonical_hash=None):
    source = tmp_path / "index.md"
    source.write_text(local_text, encoding="utf-8")
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
                page_id=remote_page_id,
                title="Home",
                url=None,
                parent_source_path=None,
                canonical_hash=canonical_hash,
            ),
        ),
        space="~6393203da593cb822e995262",
    )


def page_storage(storage: str) -> PageStorage:
    return PageStorage(page_id="111111", title="Home", storage=storage, version=3)


@pytest.mark.asyncio
async def test_check_project_invariants_reports_in_sync_without_baseline(tmp_path):
    local = "# Home\n\nPlain text.\n"
    plan = make_plan(tmp_path, local_text=local)
    client = FakeContentClient({"111111": page_storage("<h1>Home</h1><p>Plain text.</p>")})

    report = await check_project_invariants(plan, client=client)

    assert [page.status for page in report.pages] == [SyncStatus.IN_SYNC]
    assert report.has_conflicts is False
    assert client.calls == [("get_page_storage", "111111")]


@pytest.mark.asyncio
async def test_check_project_invariants_reports_local_only_pages(tmp_path):
    plan = make_plan(tmp_path, local_text="# Home\n\nLocal only.\n", remote_page_id=None)
    client = FakeContentClient({})

    report = await check_project_invariants(plan, client=client)

    assert [page.status for page in report.pages] == [SyncStatus.LOCAL_ONLY]
    assert report.pages[0].remote_hash is None
    assert report.has_conflicts is True
    assert client.calls == []


@pytest.mark.asyncio
async def test_check_project_invariants_reports_unknown_divergence_without_baseline(tmp_path):
    plan = make_plan(tmp_path, local_text="# Home\n\nLocal text.\n")
    client = FakeContentClient({"111111": page_storage("<h1>Home</h1><p>Remote text.</p>")})

    report = await check_project_invariants(plan, client=client)

    assert [page.status for page in report.pages] == [SyncStatus.UNKNOWN_DIVERGENCE]
    assert report.has_conflicts is True


@pytest.mark.asyncio
async def test_check_project_invariants_uses_baseline_to_detect_remote_change(tmp_path):
    baseline = compute_canonical_markdown_hash("# Home\n\nBase text.\n")
    plan = make_plan(
        tmp_path,
        local_text="# Home\n\nBase text.\n",
        canonical_hash=baseline,
    )
    client = FakeContentClient({"111111": page_storage("<h1>Home</h1><p>Remote text.</p>")})

    report = await check_project_invariants(plan, client=client)

    assert [page.status for page in report.pages] == [SyncStatus.REMOTE_CHANGED]
    assert report.has_conflicts is False


@pytest.mark.asyncio
async def test_check_project_invariants_uses_baseline_to_detect_conflict(tmp_path):
    baseline = compute_canonical_markdown_hash("# Home\n\nBase text.\n")
    plan = make_plan(
        tmp_path,
        local_text="# Home\n\nLocal text.\n",
        canonical_hash=baseline,
    )
    client = FakeContentClient({"111111": page_storage("<h1>Home</h1><p>Remote text.</p>")})

    report = await check_project_invariants(plan, client=client)

    assert [page.status for page in report.pages] == [SyncStatus.CONFLICT]
    assert report.has_conflicts is True


@pytest.mark.asyncio
async def test_pull_remote_changes_writes_only_safe_remote_changes(tmp_path):
    manifest_path = tmp_path / ".confluence-publish.yml"
    manifest_path.write_text(
        """
pages:
  index.md:
    page_id: "111111"
    canonical_hash: "sha256:placeholder"
""".strip(),
        encoding="utf-8",
    )
    baseline = compute_canonical_markdown_hash("# Home\n\nBase text.\n")
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace("sha256:placeholder", baseline),
        encoding="utf-8",
    )
    plan = make_plan(
        tmp_path,
        local_text="# Home\n\nBase text.\n",
        canonical_hash=baseline,
    )
    plan = PublishPlan(**{**plan.__dict__, "manifest_path": manifest_path})
    client = FakeContentClient({"111111": page_storage("<h1>Home</h1><p>Remote text.</p>")})

    result = await pull_remote_changes(plan, client=client, dry_run=False)

    assert result.updated_paths == ("index.md",)
    assert (tmp_path / "index.md").read_text(encoding="utf-8") == "# Home\n\nRemote text.\n"
    manifest = load_manifest(manifest_path)
    assert manifest.pages["index.md"].canonical_hash == compute_canonical_markdown_hash(
        "# Home\n\nRemote text.\n"
    )


@pytest.mark.asyncio
async def test_pull_remote_changes_keeps_markdown_when_manifest_update_fails(
    tmp_path,
    monkeypatch,
):
    manifest_path = tmp_path / ".confluence-publish.yml"
    baseline = compute_canonical_markdown_hash("# Home\n\nBase text.\n")
    manifest_path.write_text(
        f"""
pages:
  index.md:
    page_id: "111111"
    canonical_hash: "{baseline}"
""".strip(),
        encoding="utf-8",
    )
    plan = make_plan(
        tmp_path,
        local_text="# Home\n\nBase text.\n",
        canonical_hash=baseline,
    )
    plan = PublishPlan(**{**plan.__dict__, "manifest_path": manifest_path})
    client = FakeContentClient({"111111": page_storage("<h1>Home</h1><p>Remote text.</p>")})

    def fail_manifest_update(*args, **kwargs):
        raise RuntimeError("manifest write failed")

    monkeypatch.setattr(
        "confluence_publisher.bidirectional.write_manifest_sync_state",
        fail_manifest_update,
    )

    with pytest.raises(RuntimeError, match="manifest write failed"):
        await pull_remote_changes(plan, client=client, dry_run=False)

    assert (tmp_path / "index.md").read_text(encoding="utf-8") == "# Home\n\nBase text.\n"
    assert list(tmp_path.glob(".*.tmp")) == []


@pytest.mark.asyncio
async def test_pull_remote_changes_refuses_unknown_divergence(tmp_path):
    plan = make_plan(tmp_path, local_text="# Home\n\nLocal text.\n")
    client = FakeContentClient({"111111": page_storage("<h1>Home</h1><p>Remote text.</p>")})

    with pytest.raises(RuntimeError, match="unsafe to pull"):
        await pull_remote_changes(plan, client=client, dry_run=False)


@pytest.mark.asyncio
async def test_pull_remote_changes_refuses_local_only_pages(tmp_path):
    plan = make_plan(tmp_path, local_text="# Home\n\nLocal only.\n", remote_page_id=None)
    client = FakeContentClient({})

    with pytest.raises(RuntimeError, match=r"index\.md \(local_only\)"):
        await pull_remote_changes(plan, client=client, dry_run=False)
