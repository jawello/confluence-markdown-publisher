from __future__ import annotations

import re
from typing import Mapping

import markdown as markdown_lib
from bs4 import BeautifulSoup

from .models import DiagramBlock
from .storage import code_macro, fallback_mermaid_fragment, toc_macro

_FENCE_RE = re.compile(
    r"^[ \t]{0,3}```[ \t]*(?P<lang>[A-Za-z0-9_-]*)[^\n\r]*(?:\r?\n)"
    r"(?P<body>.*?)"
    r"(?:^[ \t]{0,3}```[ \t]*(?:\r?$))",
    re.MULTILINE | re.DOTALL,
)
_PLACEHOLDER = "CONFLUENCE_PUBLISHER_BLOCK_PLACEHOLDER_9f8d8a1f_"
_CONFLUENCE_DIRECTIVE_RE = re.compile(r"^%%\s*confluence:\s*width=(?P<width>\d+)\s*%%$")


def _display_width_from_source(source: str) -> int | None:
    first_line = source.splitlines()[0].strip() if source.splitlines() else ""
    if not first_line.startswith("%%"):
        return None
    if "confluence:" not in first_line:
        return None
    match = _CONFLUENCE_DIRECTIVE_RE.fullmatch(first_line)
    if match is None:
        raise ValueError(f"Invalid Mermaid Confluence directive: {first_line}")
    width = int(match.group("width"))
    if width <= 0:
        raise ValueError(f"Invalid Mermaid Confluence directive: {first_line}")
    return width


def collect_mermaid_blocks(markdown_text: str, page_slug: str, output_format: str) -> list[DiagramBlock]:
    blocks: list[DiagramBlock] = []
    for match in _FENCE_RE.finditer(markdown_text):
        if match.group("lang").strip().lower() != "mermaid":
            continue
        blocks.append(
            DiagramBlock(
                ordinal=len(blocks) + 1,
                page_slug=page_slug,
                source=match.group("body"),
                output_format=output_format,
                display_width=_display_width_from_source(match.group("body")),
            )
        )
    return blocks


def _heading_count(markdown_text: str) -> int:
    return len(re.findall(r"^#{1,6}\s+\S", markdown_text, flags=re.MULTILINE))


def _replace_blockquotes_with_panels(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for blockquote in soup.find_all("blockquote"):
        text = blockquote.get_text(" ", strip=True).lower()
        macro_name = "note"
        if text.startswith("warning") or text.startswith("important"):
            macro_name = "warning"
        elif text.startswith("tip"):
            macro_name = "tip"
        elif text.startswith("note"):
            macro_name = "info"
        replacement = BeautifulSoup(
            f'<ac:structured-macro ac:name="{macro_name}"><ac:rich-text-body>{blockquote.decode_contents()}</ac:rich-text-body></ac:structured-macro>',
            "html.parser",
        )
        blockquote.replace_with(replacement)
    return str(soup)


def convert_markdown_to_storage(
    markdown_text: str,
    page_slug: str,
    output_format: str,
    diagram_fragments: Mapping[int, str],
    allow_diagram_fallback: bool = False,
) -> str:
    code_fragments: dict[int, str] = {}
    mermaid_ordinal = 0

    def replace_fence(match: re.Match[str]) -> str:
        nonlocal mermaid_ordinal
        index = len(code_fragments)
        language = match.group("lang").strip().lower() or "text"
        body = match.group("body").rstrip("\r\n")
        if language == "mermaid":
            mermaid_ordinal += 1
            if mermaid_ordinal in diagram_fragments:
                code_fragments[index] = diagram_fragments[mermaid_ordinal]
            elif allow_diagram_fallback:
                code_fragments[index] = fallback_mermaid_fragment(match.group("body"))
            else:
                raise ValueError(f"No rendered fragment for Mermaid diagram #{mermaid_ordinal} in {page_slug}")
        else:
            code_fragments[index] = code_macro(body, language)
        return f"\n\n{_PLACEHOLDER}{index}\n\n"

    without_fences = _FENCE_RE.sub(replace_fence, markdown_text)
    html = markdown_lib.markdown(without_fences, extensions=["tables", "sane_lists"])
    html = _replace_blockquotes_with_panels(html)

    for index, fragment in code_fragments.items():
        marker = f"{_PLACEHOLDER}{index}"
        html = html.replace(f"<p>{marker}</p>", fragment)

    if _heading_count(markdown_text) >= 3:
        html = toc_macro() + html
    return html
