from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path
from collections.abc import Sequence

from .converter import collect_mermaid_blocks, convert_markdown_to_storage
from .mcp_atlassian import connect_mcp_atlassian
from .models import RenderedDiagram
from .renderer import MermaidRenderer, RenderError
from .storage import diagram_fragment, fallback_mermaid_fragment


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="publish-confluence",
        description="Publish Markdown to Confluence storage format with rendered Mermaid diagrams.",
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--title")
    parser.add_argument("--output-format", choices=("svg", "png"), default="svg")
    parser.add_argument("--diagram-width", type=_positive_int, default=1000)
    parser.add_argument("--mmdc-bin", default="mmdc")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--allow-diagram-fallback", action="store_true")
    return parser


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


async def run_async(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    markdown_text = args.source.read_text(encoding="utf-8")
    page_slug = args.source.stem
    blocks = collect_mermaid_blocks(markdown_text, page_slug, args.output_format)
    diagram_fragments: dict[int, str] = {}
    rendered_diagrams: list[RenderedDiagram] = []

    with tempfile.TemporaryDirectory(prefix="confluence-mermaid-") as temp_path:
        temp_dir = Path(temp_path)
        renderer = MermaidRenderer(args.mmdc_bin)
        for block in blocks:
            try:
                rendered = renderer.render(block, temp_dir)
            except RenderError as exc:
                if not args.allow_diagram_fallback:
                    raise SystemExit(str(exc)) from exc
                diagram_fragments[block.ordinal] = fallback_mermaid_fragment(block.source)
                continue

            rendered_diagrams.append(rendered)
            diagram_fragments[block.ordinal] = diagram_fragment(
                block,
                width=block.display_width or args.diagram_width,
            )

        storage = convert_markdown_to_storage(
            markdown_text,
            page_slug=page_slug,
            output_format=args.output_format,
            diagram_fragments=diagram_fragments,
            allow_diagram_fallback=args.allow_diagram_fallback,
        )

        title = args.title or args.source.stem
        if not args.dry_run and args.title is None:
            async with connect_mcp_atlassian() as client:
                title = (await client.get_page(args.page_id)).title

        preview = build_preview(
            page_id=args.page_id,
            title=title,
            source=args.source,
            attachments=[rendered.block.attachment_name for rendered in rendered_diagrams],
            output_format=args.output_format,
            dry_run=args.dry_run,
            fallback_enabled=args.allow_diagram_fallback,
        )
        print(preview)
        print()
        print("Storage preview:")
        print(storage[:1200])

        if args.dry_run:
            return 0

        if not args.yes:
            answer = input("Publish to Confluence? Type 'yes' to continue: ")
            if answer != "yes":
                print("Aborted.")
                return 1

        async with connect_mcp_atlassian() as client:
            for rendered in rendered_diagrams:
                await client.upload_attachment(args.page_id, rendered.output_path)
            await client.update_page_storage(args.page_id, title, storage)

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run_async(argv))


if __name__ == "__main__":
    sys.exit(main())
