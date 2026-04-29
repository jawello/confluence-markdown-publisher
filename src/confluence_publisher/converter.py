from __future__ import annotations

import re
from typing import Mapping

import markdown as markdown_lib
from bs4 import BeautifulSoup, NavigableString

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
_TASK_ITEM_RE = re.compile(r"^(?P<indent> *)[-*][ \t]+\[(?P<status>[ xX])\][ \t]+")
_TASK_MARKER_RE = re.compile(r"^(?P<prefix>\s*)\[(?P<status>[ xX])\][ \t]+")
_BULLET_ITEM_RE = re.compile(r"^(?P<indent> *)(?P<bullet>[-*+])(?P<space>[ \t]+)")


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


def _normalize_task_list_child_indentation(markdown_text: str) -> str:
    lines = markdown_text.splitlines(keepends=True)
    normalized: list[str] = []
    active_task_indents: list[int] = []

    for line in lines:
        content = line.rstrip("\r\n")
        newline = line[len(content) :]
        stripped = content.lstrip(" ")

        if stripped:
            indent = len(content) - len(stripped)
            while active_task_indents and indent <= active_task_indents[-1]:
                active_task_indents.pop()

            if active_task_indents and indent == active_task_indents[-1] + 2:
                child_match = _BULLET_ITEM_RE.match(content)
                if child_match is not None:
                    content = f"{child_match.group('indent')}  {content[indent:]}"

            task_match = _TASK_ITEM_RE.match(content)
            if task_match is not None:
                active_task_indents.append(len(task_match.group("indent")))

        normalized.append(f"{content}{newline}")

    return "".join(normalized)


def _task_status_from_list_item(list_item) -> str | None:
    for descendant in list_item.descendants:
        if not isinstance(descendant, NavigableString):
            continue
        text = str(descendant)
        if not text.strip():
            continue
        match = _TASK_MARKER_RE.match(text)
        if match is None:
            return None
        return "complete" if match.group("status").lower() == "x" else "incomplete"
    return None


def _remove_task_marker(list_item) -> None:
    for descendant in list_item.descendants:
        if not isinstance(descendant, NavigableString):
            continue
        text = str(descendant)
        if not text.strip():
            continue
        match = _TASK_MARKER_RE.match(text)
        if match is None:
            return
        descendant.replace_with(f"{match.group('prefix')}{text[match.end():]}")
        return


def _replace_task_lists_with_confluence_tasks(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for bullet_list in list(soup.find_all("ul")):
        list_items = bullet_list.find_all("li", recursive=False)
        if not list_items:
            continue

        statuses = [_task_status_from_list_item(list_item) for list_item in list_items]
        if any(status is None for status in statuses):
            continue

        task_list = soup.new_tag("ac:task-list")
        for list_item, status in zip(list_items, statuses):
            _remove_task_marker(list_item)

            task = soup.new_tag("ac:task")
            task_status = soup.new_tag("ac:task-status")
            task_status.string = status
            task_body = soup.new_tag("ac:task-body")
            for child in list(list_item.contents):
                task_body.append(child.extract())

            task.append(task_status)
            task.append(task_body)
            task_list.append(task)

        bullet_list.replace_with(task_list)

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
    normalized_markdown = _normalize_task_list_child_indentation(without_fences)
    html = markdown_lib.markdown(normalized_markdown, extensions=["tables", "sane_lists"])
    html = _replace_blockquotes_with_panels(html)
    html = _replace_task_lists_with_confluence_tasks(html)

    for index, fragment in code_fragments.items():
        marker = f"{_PLACEHOLDER}{index}"
        html = html.replace(f"<p>{marker}</p>", fragment)

    if _heading_count(markdown_text) >= 3:
        html = toc_macro() + html
    return html
