from __future__ import annotations

from html import escape

from .models import DiagramBlock


def _cdata(value: str) -> str:
    return "<![CDATA[" + value.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def toc_macro() -> str:
    return '<ac:structured-macro ac:name="toc" />'


def code_macro(source: str, language: str) -> str:
    language_xml = escape(language, quote=True)
    return (
        '<ac:structured-macro ac:name="code">'
        f'<ac:parameter ac:name="language">{language_xml}</ac:parameter>'
        f"<ac:plain-text-body>{_cdata(source)}</ac:plain-text-body>"
        "</ac:structured-macro>"
    )


def image_macro(filename: str, alt: str = "Mermaid diagram", width: int | None = None) -> str:
    filename_xml = escape(filename, quote=True)
    alt_xml = escape(alt, quote=True)
    width_xml = f' ac:width="{width}"' if width is not None else ""
    return (
        f'<ac:image ac:alt="{alt_xml}"{width_xml}>'
        f'<ri:attachment ri:filename="{filename_xml}" />'
        "</ac:image>"
    )


def expand_macro(title: str, body: str) -> str:
    title_xml = escape(title, quote=False)
    return (
        '<ac:structured-macro ac:name="expand">'
        f'<ac:parameter ac:name="title">{title_xml}</ac:parameter>'
        f"<ac:rich-text-body>{body}</ac:rich-text-body>"
        "</ac:structured-macro>"
    )


def diagram_fragment(block: DiagramBlock, width: int | None = None) -> str:
    source_block = code_macro(block.source.rstrip("\n"), "mermaid")
    return image_macro(block.attachment_name, width=width) + expand_macro("Mermaid source", source_block)


def fallback_mermaid_fragment(source: str) -> str:
    source_block = code_macro(source.rstrip("\n"), "mermaid")
    body = (
        "<ac:rich-text-body>"
        "<p>Mermaid diagram was not rendered automatically.</p>"
        f"{source_block}"
        "</ac:rich-text-body>"
    )
    return f'<ac:structured-macro ac:name="info">{body}</ac:structured-macro>'
