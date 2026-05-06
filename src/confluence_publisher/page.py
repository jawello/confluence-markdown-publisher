from __future__ import annotations

import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .converter import collect_mermaid_blocks, convert_markdown_to_storage
from .models import RenderedDiagram
from .renderer import MermaidRenderer, RenderError
from .storage import diagram_fragment, fallback_mermaid_fragment


@dataclass(frozen=True)
class PreparedPage:
    storage: str
    rendered_diagrams: list[RenderedDiagram]


@contextmanager
def prepare_page(
    *,
    source: Path,
    output_format: str,
    diagram_width: int,
    mmdc_bin: str,
    allow_diagram_fallback: bool,
) -> Iterator[PreparedPage]:
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
        yield PreparedPage(storage=storage, rendered_diagrams=rendered_diagrams)


async def publish_prepared_page(
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
