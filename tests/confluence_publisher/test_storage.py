from confluence_publisher.models import DiagramBlock
from confluence_publisher.storage import (
    code_macro,
    diagram_fragment,
    expand_macro,
    fallback_mermaid_fragment,
    image_macro,
    toc_macro,
)


def test_code_macro_uses_cdata_and_language():
    fragment = code_macro("graph LR\nA --> B", "mermaid")

    assert '<ac:structured-macro ac:name="code">' in fragment
    assert '<ac:parameter ac:name="language">mermaid</ac:parameter>' in fragment
    assert "<![CDATA[graph LR\nA --> B]]>" in fragment


def test_code_macro_escapes_language_and_splits_embedded_cdata_end():
    fragment = code_macro("a ]]> b", 'bad&lang"<value>')

    assert (
        '<ac:parameter ac:name="language">'
        "bad&amp;lang&quot;&lt;value&gt;"
        "</ac:parameter>"
    ) in fragment
    assert "<![CDATA[a ]]]]><![CDATA[> b]]>" in fragment


def test_image_macro_escapes_filename_and_alt_attributes():
    fragment = image_macro('diagram&"<one>.svg', 'Mermaid & "diagram"')

    assert 'ri:filename="diagram&amp;&quot;&lt;one&gt;.svg"' in fragment
    assert 'ac:alt="Mermaid &amp; &quot;diagram&quot;"' in fragment


def test_image_macro_can_set_display_width():
    fragment = image_macro("diagram.svg", width=1000)

    assert '<ac:image ac:alt="Mermaid diagram" ac:width="1000">' in fragment


def test_expand_macro_escapes_title_text():
    fragment = expand_macro("Mermaid & <source>", "<p>body</p>")

    assert "<ac:parameter ac:name=\"title\">Mermaid &amp; &lt;source&gt;</ac:parameter>" in fragment


def test_diagram_fragment_contains_image_and_collapsed_source():
    block = DiagramBlock(1, "00-overview", "graph LR\nA --> B\n", "svg")

    fragment = diagram_fragment(block, width=1000)

    assert '<ac:image ac:alt="Mermaid diagram" ac:width="1000">' in fragment
    assert f'ri:filename="{block.attachment_name}"' in fragment
    assert '<ac:structured-macro ac:name="expand">' in fragment
    assert '<ac:parameter ac:name="title">Mermaid source</ac:parameter>' in fragment
    assert '<ac:parameter ac:name="language">mermaid</ac:parameter>' in fragment
    assert "<![CDATA[graph LR\nA --> B]]>" in fragment
    assert "<![CDATA[graph LR\nA --> B\n]]>" not in fragment


def test_fallback_fragment_is_visible_panel_with_source():
    fragment = fallback_mermaid_fragment("graph LR\nA --> B\n")

    assert '<ac:structured-macro ac:name="info">' in fragment
    assert "Mermaid diagram was not rendered automatically." in fragment
    assert '<ac:structured-macro ac:name="code">' in fragment
    assert '<ac:parameter ac:name="language">mermaid</ac:parameter>' in fragment
    assert "<![CDATA[graph LR\nA --> B]]>" in fragment


def test_toc_macro_is_single_storage_macro():
    assert toc_macro() == '<ac:structured-macro ac:name="toc" />'
