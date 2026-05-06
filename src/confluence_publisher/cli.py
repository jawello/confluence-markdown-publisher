from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from .clone import (
    CloneError,
    clone_confluence_tree,
    format_clone_summary,
    parse_clone_request,
)
from .manifest import ManifestError, load_manifest
from .mcp_atlassian import connect_mcp_atlassian
from .models import RenderedDiagram
from .page import PreparedPage, prepare_page, publish_prepared_page
from .planner import PlannerError, build_publish_plan
from .project import discover_markdown_files


DEFAULT_SINGLE_PAGE_VERSION_COMMENT = "Rendered Mermaid diagrams from Markdown source"


_PreparedSinglePage = PreparedPage


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


class _StoreExplicit(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, values)
        setattr(namespace, f"{self.dest}_explicit", True)


def _add_manifest_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path)


def _add_create_recovery_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--create-missing-pages", action="store_true")
    parser.add_argument("--recreate-missing-root", action="store_true")
    parser.add_argument("--recreate-stale-pages", action="store_true")


def _add_render_arguments(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(output_format_explicit=False, diagram_width_explicit=False)
    parser.add_argument(
        "--output-format",
        choices=("svg", "png"),
        default="svg",
        action=_StoreExplicit,
    )
    parser.add_argument(
        "--diagram-width",
        type=_positive_int,
        default=1000,
        action=_StoreExplicit,
    )
    parser.add_argument("--mmdc-bin", default="mmdc")
    parser.add_argument("--allow-diagram-fallback", action="store_true")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="publish-confluence",
        description="Manage manifest-backed Markdown projects for Confluence.",
    )
    parser.set_defaults(
        source=None,
        page_id=None,
        title=None,
        plan_only=False,
        sync=False,
        check_sync=False,
        pull=False,
        create_missing_pages=False,
        recreate_missing_root=False,
        recreate_stale_pages=False,
        comment=None,
        output_format="svg",
        output_format_explicit=False,
        diagram_width=1000,
        diagram_width_explicit=False,
        mmdc_bin="mmdc",
        dry_run=False,
        yes=False,
        allow_diagram_fallback=False,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    clone_parser = subparsers.add_parser(
        "clone",
        help="Clone a Confluence page tree into a local Markdown project.",
    )
    clone_parser.add_argument("clone_source")
    clone_parser.add_argument("clone_output", type=Path)
    clone_parser.add_argument("--space", dest="clone_space")
    clone_parser.add_argument("--max-depth", dest="clone_max_depth", type=int)
    clone_parser.add_argument("--max-pages", dest="clone_max_pages", type=int, default=1000)
    clone_parser.add_argument("--dry-run", action="store_true")

    plan_parser = subparsers.add_parser("plan", help="Validate and print a local project plan.")
    plan_parser.add_argument("project", type=Path)
    plan_parser.set_defaults(plan_only=True)
    _add_manifest_argument(plan_parser)
    _add_create_recovery_arguments(plan_parser)

    status_parser = subparsers.add_parser("status", help="Check local and remote sync state.")
    status_parser.add_argument("project", type=Path)
    status_parser.set_defaults(check_sync=True)
    _add_manifest_argument(status_parser)

    pull_parser = subparsers.add_parser("pull", help="Pull safe remote-only changes into Markdown.")
    pull_parser.add_argument("project", type=Path)
    pull_parser.set_defaults(pull=True)
    _add_manifest_argument(pull_parser)
    pull_parser.add_argument("--dry-run", action="store_true")
    pull_parser.add_argument("--yes", action="store_true")

    push_parser = subparsers.add_parser("push", help="Push local Markdown changes to Confluence.")
    push_parser.add_argument("project", type=Path)
    push_parser.set_defaults(sync=True)
    _add_manifest_argument(push_parser)
    _add_create_recovery_arguments(push_parser)
    push_parser.add_argument("--dry-run", action="store_true")
    push_parser.add_argument("--yes", action="store_true")
    push_parser.add_argument("--comment")
    _add_render_arguments(push_parser)

    return parser


def validate_args(args: argparse.Namespace) -> None:
    if getattr(args, "command", None) == "clone":
        return
    if args.source is not None and args.project is not None:
        raise SystemExit("--source and --project are mutually exclusive")
    if args.source is None and args.project is None:
        raise SystemExit("Either --source or --project is required")
    if args.source is not None:
        if not args.page_id:
            raise SystemExit("--page-id is required with --source")
        if args.plan_only:
            raise SystemExit("--plan-only is only supported with --project")
        if args.create_missing_pages:
            raise SystemExit("--create-missing-pages is only supported with --project")
        if args.sync:
            raise SystemExit("--sync is only supported with --project")
        if args.check_sync:
            raise SystemExit("--check-sync is only supported with --project")
        if args.pull:
            raise SystemExit("--pull is only supported with --project")
        if args.recreate_missing_root:
            raise SystemExit("--recreate-missing-root is only supported with --project")
        if args.recreate_stale_pages:
            raise SystemExit("--recreate-stale-pages is only supported with --project")
    if args.project is not None:
        if args.page_id:
            raise SystemExit("--page-id is not supported with --project")
        if args.title:
            raise SystemExit("--title is not supported with --project")
        if args.check_sync and args.pull:
            raise SystemExit("--check-sync and --pull are mutually exclusive")


def build_preview(
    *,
    page_id: str,
    title: str,
    source: Path,
    attachments: Sequence[str],
    output_format: str,
    dry_run: bool,
    fallback_enabled: bool,
) -> str:
    attachment_lines = "\n".join(f"  - {attachment}" for attachment in attachments) or "  - none"
    return "\n".join(
        [
            "Confluence publish preview",
            f"Page ID: {page_id}",
            f"Title: {title}",
            f"Source: {source}",
            f"Format: {output_format}",
            f"Dry run: {'yes' if dry_run else 'no'}",
            f"Fallback mode: {'yes' if fallback_enabled else 'no'}",
            "Attachments:",
            attachment_lines,
        ]
    )


def _prepare_single_page(
    *,
    source: Path,
    output_format: str,
    diagram_width: int,
    mmdc_bin: str,
    allow_diagram_fallback: bool,
):
    return prepare_page(
        source=source,
        output_format=output_format,
        diagram_width=diagram_width,
        mmdc_bin=mmdc_bin,
        allow_diagram_fallback=allow_diagram_fallback,
    )


async def _publish_prepared_single_page(
    *,
    page_id: str,
    title: str,
    storage: str,
    rendered_diagrams: Sequence[RenderedDiagram],
    client,
    version_comment: str,
) -> None:
    await publish_prepared_page(
        page_id=page_id,
        title=title,
        storage=storage,
        rendered_diagrams=rendered_diagrams,
        client=client,
        version_comment=version_comment,
    )


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
    with _prepare_single_page(
        source=source,
        output_format=output_format,
        diagram_width=diagram_width,
        mmdc_bin=mmdc_bin,
        allow_diagram_fallback=allow_diagram_fallback,
    ) as prepared:
        if not dry_run:
            await _publish_prepared_single_page(
                page_id=page_id,
                title=title,
                storage=prepared.storage,
                rendered_diagrams=prepared.rendered_diagrams,
                client=client,
                version_comment=version_comment,
            )
        return prepared.storage


async def run_project_mode(args: argparse.Namespace) -> int:
    from .batch import format_publish_plan, publish_project
    from .bidirectional import (
        check_project_invariants,
        format_invariant_report,
        pull_remote_changes,
    )
    from .sync import sync_project

    manifest_path = args.manifest or args.project / ".confluence-publish.yml"
    try:
        manifest = load_manifest(
            manifest_path,
            allow_missing_page_ids=True,
        )
        sync_enabled = (
            manifest.sync.enabled
            or args.sync
            or args.check_sync
            or args.pull
            or args.recreate_missing_root
            or args.recreate_stale_pages
        )
        recreate_missing_root = (
            manifest.sync.recreate_missing_root or args.recreate_missing_root
        )
        recreate_stale_pages = (
            manifest.sync.recreate_stale_pages or args.recreate_stale_pages
        )
        try:
            discovery = discover_markdown_files(
                args.project,
                include=manifest.include,
                exclude=manifest.exclude,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        plan = build_publish_plan(
            project_root=args.project,
            manifest=manifest,
            discovered_paths=discovery.included,
            excluded_count=discovery.excluded_count,
            version_comment=args.comment,
            output_format=args.output_format if args.output_format_explicit else None,
            diagram_width=args.diagram_width if args.diagram_width_explicit else None,
            allow_diagram_fallback=True if args.allow_diagram_fallback else None,
            create_missing_pages=args.create_missing_pages,
            sync_enabled=sync_enabled,
            recreate_missing_root=recreate_missing_root,
            recreate_stale_pages=recreate_stale_pages,
        )
    except (ManifestError, PlannerError, OSError) as exc:
        raise SystemExit(str(exc)) from exc

    print(format_publish_plan(plan))

    if args.plan_only:
        return 0

    if args.check_sync or args.pull:
        if args.pull and not args.dry_run and not args.yes:
            answer = input(
                "Pull remote Confluence changes into local Markdown? "
                "Type 'yes' to continue: "
            )
            if answer != "yes":
                print("Aborted.")
                return 1

        sync_error: RuntimeError | None = None
        async with connect_mcp_atlassian() as client:
            try:
                if args.check_sync:
                    report = await check_project_invariants(plan, client=client)
                else:
                    result = await pull_remote_changes(
                        plan,
                        client=client,
                        dry_run=args.dry_run,
                    )
            except RuntimeError as exc:
                sync_error = exc

        if sync_error is not None:
            raise SystemExit(str(sync_error)) from sync_error

        if args.check_sync:
            print(format_invariant_report(report))
            return 1 if report.has_conflicts else 0

        print(format_invariant_report(result.report))
        pulled = ", ".join(result.updated_paths) if result.updated_paths else "none"
        print(f"Remote pages to pull: {pulled}")
        return 0

    if args.dry_run:
        class DryRunClient:
            async def upload_attachment(self, page_id, file_path):
                raise AssertionError("dry-run must not upload attachments")

            async def update_page_storage(self, page_id, title, storage, version_comment):
                raise AssertionError("dry-run must not update pages")

        if plan.sync_enabled:
            return await sync_project(
                plan,
                client=DryRunClient(),
                dry_run=True,
                mmdc_bin=args.mmdc_bin,
            )
        return await publish_project(
            plan,
            client=DryRunClient(),
            dry_run=True,
            mmdc_bin=args.mmdc_bin,
        )

    if not args.yes:
        answer = input("Publish project to Confluence? Type 'yes' to continue: ")
        if answer != "yes":
            print("Aborted.")
            return 1

    async with connect_mcp_atlassian() as client:
        if plan.sync_enabled:
            return await sync_project(
                plan,
                client=client,
                dry_run=False,
                mmdc_bin=args.mmdc_bin,
            )
        return await publish_project(
            plan,
            client=client,
            dry_run=False,
            mmdc_bin=args.mmdc_bin,
        )


async def run_async(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    validate_args(args)
    if getattr(args, "command", None) == "clone":
        try:
            source = args.clone_source
            is_url = "://" in source or "/pages/" in source
            request = parse_clone_request(
                url=source if is_url else None,
                page_id=None if is_url else source,
                space=args.clone_space,
                output=args.clone_output,
                max_depth=args.clone_max_depth,
                max_pages=args.clone_max_pages,
                dry_run=args.dry_run,
            )
            clone_error: CloneError | None = None
            async with connect_mcp_atlassian() as client:
                try:
                    result = await clone_confluence_tree(request, client=client)
                except CloneError as exc:
                    clone_error = exc
            if clone_error is not None:
                raise SystemExit(str(clone_error)) from clone_error
            print(format_clone_summary(result))
            return 0
        except CloneError as exc:
            raise SystemExit(str(exc)) from exc
    if args.project is not None:
        return await run_project_mode(args)

    with _prepare_single_page(
        source=args.source,
        output_format=args.output_format,
        diagram_width=args.diagram_width,
        mmdc_bin=args.mmdc_bin,
        allow_diagram_fallback=args.allow_diagram_fallback,
    ) as prepared:
        title = args.title or args.source.stem
        if not args.dry_run and args.title is None:
            async with connect_mcp_atlassian() as client:
                title = (await client.get_page(args.page_id)).title

        preview = build_preview(
            page_id=args.page_id,
            title=title,
            source=args.source,
            attachments=[rendered.block.attachment_name for rendered in prepared.rendered_diagrams],
            output_format=args.output_format,
            dry_run=args.dry_run,
            fallback_enabled=args.allow_diagram_fallback,
        )
        print(preview)
        print()
        print("Storage preview:")
        print(prepared.storage[:1200])

        if args.dry_run:
            return 0

        if not args.yes:
            answer = input("Publish to Confluence? Type 'yes' to continue: ")
            if answer != "yes":
                print("Aborted.")
                return 1

        async with connect_mcp_atlassian() as client:
            await _publish_prepared_single_page(
                page_id=args.page_id,
                title=title,
                storage=prepared.storage,
                rendered_diagrams=prepared.rendered_diagrams,
                client=client,
                version_comment=DEFAULT_SINGLE_PAGE_VERSION_COMMENT,
            )

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run_async(argv))


if __name__ == "__main__":
    sys.exit(main())
