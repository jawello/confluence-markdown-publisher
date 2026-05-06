from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from . import bidirectional, sync
from .clone import (
    CloneError,
    clone_confluence_tree,
    format_clone_summary,
    parse_clone_request,
)
from .manifest import ManifestError, load_manifest
from .mcp_atlassian import connect_mcp_atlassian
from .planner import PlannerError, build_publish_plan
from .project import discover_markdown_files


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


def _manifest_path_for(project: Path, manifest: Path | None) -> Path:
    return manifest or project / ".confluence-publish.yml"


def _build_project_plan(
    args: argparse.Namespace,
    *,
    create_missing_pages: bool = False,
    recreate_missing_root: bool = False,
    recreate_stale_pages: bool = False,
):
    manifest_path = _manifest_path_for(args.project, args.manifest)
    manifest = load_manifest(manifest_path, allow_missing_page_ids=True)
    discovery = discover_markdown_files(
        args.project,
        include=manifest.include,
        exclude=manifest.exclude,
    )
    return build_publish_plan(
        project_root=args.project,
        manifest=manifest,
        discovered_paths=discovery.included,
        excluded_count=discovery.excluded_count,
        version_comment=getattr(args, "comment", None),
        output_format=args.output_format if getattr(args, "output_format_explicit", False) else None,
        diagram_width=args.diagram_width if getattr(args, "diagram_width_explicit", False) else None,
        allow_diagram_fallback=True if getattr(args, "allow_diagram_fallback", False) else None,
        create_missing_pages=create_missing_pages,
        sync_enabled=True,
        recreate_missing_root=manifest.sync.recreate_missing_root or recreate_missing_root,
        recreate_stale_pages=manifest.sync.recreate_stale_pages or recreate_stale_pages,
    )


async def run_plan_command(args: argparse.Namespace) -> int:
    from .batch import format_publish_plan

    try:
        plan = _build_project_plan(
            args,
            create_missing_pages=args.create_missing_pages,
            recreate_missing_root=args.recreate_missing_root,
            recreate_stale_pages=args.recreate_stale_pages,
        )
    except (ManifestError, PlannerError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(format_publish_plan(plan))
    return 0


async def run_status_command(args: argparse.Namespace) -> int:
    from .batch import format_publish_plan

    try:
        plan = _build_project_plan(args)
    except (ManifestError, PlannerError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(format_publish_plan(plan))
    async with connect_mcp_atlassian() as client:
        report = await bidirectional.check_project_invariants(plan, client=client)
    print(bidirectional.format_invariant_report(report))
    return 1 if report.has_conflicts else 0


async def run_pull_command(args: argparse.Namespace) -> int:
    from .batch import format_publish_plan

    try:
        plan = _build_project_plan(args)
    except (ManifestError, PlannerError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(format_publish_plan(plan))
    if not args.dry_run and not args.yes:
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
            result = await bidirectional.pull_remote_changes(
                plan,
                client=client,
                dry_run=args.dry_run,
            )
        except RuntimeError as exc:
            sync_error = exc

    if sync_error is not None:
        raise SystemExit(str(sync_error)) from sync_error

    print(bidirectional.format_invariant_report(result.report))
    pulled = ", ".join(result.updated_paths) if result.updated_paths else "none"
    print(f"Remote pages to pull: {pulled}")
    return 0


async def run_push_command(args: argparse.Namespace) -> int:
    from .batch import format_publish_plan

    try:
        plan = _build_project_plan(
            args,
            create_missing_pages=args.create_missing_pages,
            recreate_missing_root=args.recreate_missing_root,
            recreate_stale_pages=args.recreate_stale_pages,
        )
    except (ManifestError, PlannerError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(format_publish_plan(plan))

    if args.dry_run:
        class DryRunClient:
            async def upload_attachment(self, page_id, file_path):
                raise AssertionError("dry-run must not upload attachments")

            async def update_page_storage(self, page_id, title, storage, version_comment):
                raise AssertionError("dry-run must not update pages")

        return await sync.sync_project(
            plan,
            client=DryRunClient(),
            dry_run=True,
            mmdc_bin=args.mmdc_bin,
        )

    if not args.yes:
        answer = input("Push local Markdown changes to Confluence? Type 'yes' to continue: ")
        if answer != "yes":
            print("Aborted.")
            return 1

    async with connect_mcp_atlassian() as client:
        return await sync.sync_project(
            plan,
            client=client,
            dry_run=False,
            mmdc_bin=args.mmdc_bin,
        )


async def run_clone_command(args: argparse.Namespace) -> int:
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


async def run_async(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.command == "clone":
        return await run_clone_command(args)
    if args.command == "plan":
        return await run_plan_command(args)
    if args.command == "status":
        return await run_status_command(args)
    if args.command == "pull":
        return await run_pull_command(args)
    if args.command == "push":
        return await run_push_command(args)
    raise SystemExit(f"Unsupported command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run_async(argv))


if __name__ == "__main__":
    sys.exit(main())
