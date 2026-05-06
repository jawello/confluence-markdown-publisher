from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString, Tag


@dataclass(frozen=True)
class StorageToMarkdownResult:
    markdown: str
    unsupported: tuple[str, ...] = ()


_PANEL_PREFIXES = {
    "warning": "Warning",
    "info": "Note",
    "note": "Note",
    "tip": "Tip",
}


def _attr(tag: Tag, name: str) -> str | None:
    value = tag.get(name)
    return value if isinstance(value, str) else None


def _macro_name(tag: Tag) -> str | None:
    return _attr(tag, "ac:name") or _attr(tag, "name")


def _plain_text(tag: Tag) -> str:
    return tag.get_text("", strip=False)


def _add_unsupported(unsupported: list[str], message: str) -> None:
    if message not in unsupported:
        unsupported.append(message)


def _normalize_inline_text(raw: str) -> str:
    return re.sub(r"[ \t\r\n]+", " ", raw).strip()


def _parameter_text(tag: Tag, name: str) -> str | None:
    for parameter in tag.find_all("ac:parameter"):
        if _attr(parameter, "ac:name") == name:
            return parameter.get_text("", strip=True)
    return None


def _code_macro_to_markdown(tag: Tag) -> str:
    language = _parameter_text(tag, "language") or "text"
    body = tag.find("ac:plain-text-body")
    source = _plain_text(body) if body is not None else ""
    source = source.rstrip("\n")
    return f"```{language}\n{source}\n```\n"


def _rich_body(tag: Tag) -> Tag | None:
    return tag.find("ac:rich-text-body")


def _inline_markdown(node, unsupported: list[str]) -> str:
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""

    name = node.name.lower()
    if name == "img":
        _image_to_markdown(node, unsupported)
        return ""
    if name.startswith(("ac:", "ri:")):
        _add_unsupported(unsupported, f"Unsupported Confluence element: {name}")
        return ""

    children = "".join(_inline_markdown(child, unsupported) for child in node.children)
    if name in {"strong", "b"}:
        return f"**{children.strip()}**"
    if name in {"em", "i"}:
        return f"*{children.strip()}*"
    if name == "code":
        return f"`{node.get_text('', strip=False)}`"
    if name == "a":
        href = _attr(node, "href")
        text = children.strip()
        return f"[{text}]({href})" if href else text
    if name == "br":
        return "\n"
    if name in {"p", "span"}:
        return children
    _add_unsupported(unsupported, f"Unsupported Confluence element: {name}")
    return children


def _inline_text(tag: Tag, unsupported: list[str]) -> str:
    raw = "".join(_inline_markdown(child, unsupported) for child in tag.children)
    return _normalize_inline_text(raw)


def _panel_to_markdown(tag: Tag, name: str, unsupported: list[str]) -> str:
    body = _rich_body(tag)
    text = _children_to_markdown(body, unsupported).strip() if body is not None else ""
    text = " ".join(text.replace("\n", " ").split())
    prefix = _PANEL_PREFIXES.get(name, "Note")
    return f"> {prefix}: {text}\n"


def _task_body_to_markdown(
    tag: Tag,
    unsupported: list[str],
    nested_indent: str = "  ",
) -> str:
    inline_parts: list[str] = []
    nested_lines: list[str] = []
    for child in tag.children:
        if isinstance(child, Tag) and child.name.lower() in {"ul", "ol"}:
            nested = _list_to_markdown(
                child,
                ordered=child.name.lower() == "ol",
                unsupported=unsupported,
                indent=nested_indent,
            ).rstrip("\n")
            if nested:
                nested_lines.extend(nested.splitlines())
            continue
        if isinstance(child, Tag) and child.name.lower() == "ac:task-list":
            nested = _task_list_to_markdown(
                child,
                unsupported=unsupported,
                indent=nested_indent,
            ).rstrip("\n")
            if nested:
                nested_lines.extend(nested.splitlines())
            continue
        inline_parts.append(_inline_markdown(child, unsupported))

    text = _normalize_inline_text("".join(inline_parts))
    lines = [text] if text else []
    lines.extend(nested_lines)
    return "\n".join(lines)


def _task_list_to_markdown(
    tag: Tag,
    unsupported: list[str],
    indent: str = "",
) -> str:
    lines: list[str] = []
    for task in tag.find_all("ac:task", recursive=False):
        status = task.find("ac:task-status")
        body = task.find("ac:task-body")
        marker = "x" if status is not None and status.get_text(strip=True) == "complete" else " "
        text = (
            _task_body_to_markdown(body, unsupported, nested_indent=f"{indent}  ")
            if body is not None
            else ""
        )
        body_lines = text.splitlines()
        first_line = body_lines[0] if body_lines else ""
        lines.append(f"{indent}- [{marker}] {first_line}".rstrip())
        lines.extend(body_lines[1:])
    return "\n".join(lines) + ("\n" if lines else "")


def _image_to_markdown(tag: Tag, unsupported: list[str]) -> str:
    if tag.name.lower() == "img":
        label = _attr(tag, "alt") or _attr(tag, "src")
    else:
        attachment = tag.find("ri:attachment")
        label = _attr(attachment, "ri:filename") if attachment is not None else None
    suffix = f": {label}" if label is not None else ""
    _add_unsupported(unsupported, f"Unsupported Confluence image{suffix}")
    return ""


def _table_to_markdown(tag: Tag, unsupported: list[str]) -> str:
    rows = tag.find_all("tr")
    if not rows:
        return ""
    rendered_rows: list[list[str]] = []
    for row in rows:
        cells = row.find_all(["th", "td"], recursive=False)
        rendered_rows.append([_inline_text(cell, unsupported) for cell in cells])
    if not rendered_rows:
        return ""
    width = max(len(row) for row in rendered_rows)
    normalized = [row + [""] * (width - len(row)) for row in rendered_rows]
    header = normalized[0]
    separator = ["---"] * width
    body = normalized[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines) + "\n"


def _list_to_markdown(
    tag: Tag,
    ordered: bool,
    unsupported: list[str],
    indent: str = "",
) -> str:
    lines: list[str] = []
    for index, item in enumerate(tag.find_all("li", recursive=False), start=1):
        marker = f"{index}." if ordered else "-"
        inline_parts: list[str] = []
        nested_lines: list[str] = []
        for child in item.children:
            if isinstance(child, Tag) and child.name.lower() in {"ul", "ol"}:
                nested = _list_to_markdown(
                    child,
                    ordered=child.name.lower() == "ol",
                    unsupported=unsupported,
                    indent=f"{indent}  ",
                ).rstrip("\n")
                if nested:
                    nested_lines.extend(nested.splitlines())
                continue
            inline_parts.append(_inline_markdown(child, unsupported))
        text = _normalize_inline_text("".join(inline_parts))
        lines.append(f"{indent}{marker} {text}".rstrip())
        lines.extend(nested_lines)
    return "\n".join(lines) + ("\n" if lines else "")


def _expand_to_markdown(tag: Tag, unsupported: list[str]) -> str:
    title = _parameter_text(tag, "title")
    body = _rich_body(tag)
    code = body.find("ac:structured-macro", attrs={"ac:name": "code"}) if body else None
    language = _parameter_text(code, "language") if code is not None else None
    if title == "Mermaid source" and code is not None and language == "mermaid":
        return _code_macro_to_markdown(code)
    unsupported.append("Unsupported Confluence macro: expand")
    return ""


def _macro_to_markdown(tag: Tag, unsupported: list[str]) -> str:
    name = _macro_name(tag)
    if name == "toc":
        return ""
    if name == "code":
        return _code_macro_to_markdown(tag)
    if name in _PANEL_PREFIXES:
        return _panel_to_markdown(tag, name, unsupported)
    if name == "expand":
        return _expand_to_markdown(tag, unsupported)
    unsupported.append(f"Unsupported Confluence macro: {name or 'unknown'}")
    return ""


def _tag_to_markdown(tag: Tag, unsupported: list[str]) -> str:
    name = tag.name.lower()
    if name == "ac:structured-macro":
        return _macro_to_markdown(tag, unsupported)
    if name == "ac:task-list":
        return _task_list_to_markdown(tag, unsupported)
    if name in {"ac:image", "img"}:
        return _image_to_markdown(tag, unsupported)
    if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(name[1])
        return f"{'#' * level} {_inline_text(tag, unsupported)}\n"
    if name == "p":
        text = _inline_text(tag, unsupported)
        return f"{text}\n" if text else ""
    if name == "ul":
        return _list_to_markdown(tag, ordered=False, unsupported=unsupported)
    if name == "ol":
        return _list_to_markdown(tag, ordered=True, unsupported=unsupported)
    if name == "table":
        return _table_to_markdown(tag, unsupported)
    if name in {"div", "body", "html", "[document]"}:
        return _children_to_markdown(tag, unsupported)
    _add_unsupported(unsupported, f"Unsupported Confluence element: {name}")
    return ""


def _children_to_markdown(tag: Tag | None, unsupported: list[str]) -> str:
    if tag is None:
        return ""
    parts: list[str] = []
    children = list(tag.children)
    for index, child in enumerate(children):
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                parts.append(text + "\n")
            continue
        if not isinstance(child, Tag):
            continue
        if child.name.lower() in {"ac:image", "img"}:
            next_tags = [item for item in children[index + 1 :] if isinstance(item, Tag)]
            if next_tags and next_tags[0].name == "ac:structured-macro":
                next_name = _macro_name(next_tags[0])
                next_title = _parameter_text(next_tags[0], "title")
                if next_name == "expand" and next_title == "Mermaid source":
                    continue
        parts.append(_tag_to_markdown(child, unsupported))
    return "\n\n".join(part.strip("\n") for part in parts if part.strip("\n")) + (
        "\n" if any(part.strip("\n") for part in parts) else ""
    )


def _normalize_markdown(markdown: str) -> str:
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def storage_to_markdown(storage: str) -> StorageToMarkdownResult:
    soup = BeautifulSoup(f"<div>{storage}</div>", "html.parser")
    root = soup.find("div")
    unsupported: list[str] = []
    markdown = _children_to_markdown(root, unsupported)
    return StorageToMarkdownResult(
        markdown=_normalize_markdown(markdown),
        unsupported=tuple(unsupported),
    )
