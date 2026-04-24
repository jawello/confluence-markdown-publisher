from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .converter import collect_mermaid_blocks, convert_markdown_to_storage
from .manifest import ManifestError, load_manifest
from .mcp_atlassian import connect_mcp_atlassian
from .models import RenderedDiagram
from .planner import PlannerError, build_publish_plan
from .project import discover_markdown_files
from .renderer import MermaidRenderer, RenderError
from .storage import diagram_fragment, fallback_mermaid_fragment


DEFAULT_SINGLE_PAGE_VERSION_COMMENT = "Rendered Mermaid diagrams from Markdown source"


@dataclass(frozen=True)
class _PreparedSinglePage:
    storage: str
    rendered_diagrams: list[RenderedDiagram]


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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="publish-confluence",
        description="Publish Markdown to Confluence storage format with rendered Mermaid diagrams.",
    )
    parser.set_defaults(output_format_explicit=False, diagram_width_explicit=False)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--page-id")
    parser.add_argument("--title")
    parser.add_argument("--project", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--comment")
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
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--allow-diagram-fallback", action="store_true")
    return parser


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


@contextmanager
def _prepare_single_page(
    *,
    source: Path,
    output_format: str,
    diagram_width: int,
    mmdc_bin: str,
    allow_diagram_fallback: bool,
) -> Iterator[_PreparedSinglePage]:
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
        yield _PreparedSinglePage(storage=storage, rendered_diagrams=rendered_diagrams)


async def _publish_prepared_single_page(
    *,
    page_id: str,
    title: str,
    storage: str,
    rendered_diagrams: Sequence[RenderedDiagram],
    client,
    version_comment: str,
) -> None:
    for rendered in rendered_diagrams:
        await client.upload_attachment(page_id, rendered.output_path)
    await client.update_page_storage(page_id, title, storage, version_comment=version_comment)


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
            output_format=args.output_format if args.output_format_explicit else None,
            diagram_width=args.diagram_width if args.diagram_width_explicit else None,
            allow_diagram_fallback=True if args.allow_diagram_fallback else None,
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
        return await publish_project(
            plan,
            client=client,
            dry_run=False,
            mmdc_bin=args.mmdc_bin,
        )


async def run_async(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    validate_args(args)
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
