import pytest

from confluence_publisher.converter import collect_mermaid_blocks, convert_markdown_to_storage
from confluence_publisher.storage import diagram_fragment


def test_collect_mermaid_blocks_assigns_ordinals_and_page_slug():
    markdown = """# Page

```mermaid
graph LR
    A --> B
```
"""

    blocks = collect_mermaid_blocks(markdown, "00-overview", "svg")

    assert len(blocks) == 1
    assert blocks[0].ordinal == 1
    assert blocks[0].page_slug == "00-overview"
    assert blocks[0].source == "graph LR\n    A --> B\n"
    assert blocks[0].display_width is None


def test_collect_mermaid_blocks_reads_confluence_width_directive():
    markdown = """```mermaid
%% confluence: width=1200 %%
graph LR
    A --> B
```
"""

    blocks = collect_mermaid_blocks(markdown, "00-overview", "svg")

    assert blocks[0].display_width == 1200
    assert blocks[0].source.startswith("%% confluence: width=1200 %%\n")


def test_collect_mermaid_blocks_rejects_invalid_confluence_width_directive():
    markdown = """```mermaid
%% confluence: width=wide %%
graph LR
    A --> B
```
"""

    with pytest.raises(ValueError, match="Invalid Mermaid Confluence directive"):
        collect_mermaid_blocks(markdown, "00-overview", "svg")


def test_collect_mermaid_blocks_handles_common_fence_variants():
    markdown = "   ``` mermaid\r\ngraph LR\r\n    A --> B\r\n   ```\r\n"

    blocks = collect_mermaid_blocks(markdown, "00-overview", "png")

    assert len(blocks) == 1
    assert blocks[0].ordinal == 1
    assert blocks[0].source == "graph LR\r\n    A --> B\r\n"
    assert blocks[0].output_format == "png"


def test_collect_mermaid_blocks_assigns_multiple_ordinals():
    markdown = """```mermaid
graph LR
    A --> B
```

```mermaid
graph LR
    C --> D
```
"""

    blocks = collect_mermaid_blocks(markdown, "00-overview", "svg")

    assert [block.ordinal for block in blocks] == [1, 2]
    assert [block.source for block in blocks] == [
        "graph LR\n    A --> B\n",
        "graph LR\n    C --> D\n",
    ]


def test_convert_mermaid_to_image_and_expand_source():
    markdown = """# Page

```mermaid
graph LR
    A --> B
```
"""
    block = collect_mermaid_blocks(markdown, "00-overview", "svg")[0]

    result = convert_markdown_to_storage(
        markdown,
        page_slug="00-overview",
        output_format="svg",
        diagram_fragments={1: diagram_fragment(block)},
    )

    assert "<h1>Page</h1>" in result
    assert '<ri:attachment ri:filename="' in result
    assert '<ac:parameter ac:name="title">Mermaid source</ac:parameter>' in result
    assert "graph LR\n    A --> B" in result


def test_convert_multiple_mermaid_blocks_to_matching_fragments():
    markdown = """```mermaid
graph LR
    A --> B
```

```mermaid
graph LR
    C --> D
```
"""

    result = convert_markdown_to_storage(
        markdown,
        page_slug="00-overview",
        output_format="svg",
        diagram_fragments={1: "FIRST_DIAGRAM", 2: "SECOND_DIAGRAM"},
    )

    assert "FIRST_DIAGRAM" in result
    assert "SECOND_DIAGRAM" in result
    assert result.index("FIRST_DIAGRAM") < result.index("SECOND_DIAGRAM")


def test_convert_missing_mermaid_fragment_raises():
    markdown = """```mermaid
graph LR
    A --> B
```
"""

    with pytest.raises(ValueError, match="No rendered fragment for Mermaid diagram #1"):
        convert_markdown_to_storage(
            markdown,
            page_slug="00-overview",
            output_format="svg",
            diagram_fragments={},
        )


def test_convert_missing_mermaid_fragment_can_fallback_to_source():
    markdown = """```mermaid
graph LR
    A --> B
```
"""

    result = convert_markdown_to_storage(
        markdown,
        page_slug="00-overview",
        output_format="svg",
        diagram_fragments={},
        allow_diagram_fallback=True,
    )

    assert '<ac:structured-macro ac:name="info">' in result
    assert "Mermaid diagram was not rendered automatically." in result
    assert '<ac:parameter ac:name="language">mermaid</ac:parameter>' in result


def test_convert_non_mermaid_fence_to_code_macro():
    markdown = """# Page

```bash
echo hello
```
"""

    result = convert_markdown_to_storage(
        markdown,
        page_slug="00-overview",
        output_format="svg",
        diagram_fragments={},
    )

    assert '<ac:parameter ac:name="language">bash</ac:parameter>' in result
    assert "<![CDATA[echo hello]]>" in result


def test_convert_crlf_fence_strips_crlf_from_code_macro_body():
    markdown = "``` bash\r\necho hello\r\n```\r\n"

    result = convert_markdown_to_storage(
        markdown,
        page_slug="00-overview",
        output_format="svg",
        diagram_fragments={},
    )

    assert "<![CDATA[echo hello]]>" in result
    assert "<![CDATA[echo hello\r]]>" not in result


def test_convert_does_not_replace_literal_placeholder_text():
    markdown = """CONFLUENCE_BLOCK_PLACEHOLDER_0

```bash
echo hello
```
"""

    result = convert_markdown_to_storage(
        markdown,
        page_slug="00-overview",
        output_format="svg",
        diagram_fragments={},
    )

    assert "CONFLUENCE_BLOCK_PLACEHOLDER_0" in result
    assert result.count('<ac:structured-macro ac:name="code">') == 1


def test_convert_blockquotes_to_confluence_panels():
    markdown = "> Warning: check this first\n"

    result = convert_markdown_to_storage(
        markdown,
        page_slug="00-overview",
        output_format="svg",
        diagram_fragments={},
    )

    assert '<ac:structured-macro ac:name="warning">' in result
    assert "<ac:rich-text-body>" in result
    assert "Warning: check this first" in result


def test_convert_unchecked_task_item_to_confluence_incomplete_task():
    markdown = "* [ ] GCP APIs enabled:\n"

    result = convert_markdown_to_storage(
        markdown,
        page_slug="00-overview",
        output_format="svg",
        diagram_fragments={},
    )

    assert "<ac:task-list>" in result
    assert "<ac:task-status>incomplete</ac:task-status>" in result
    assert "<ac:task-body>GCP APIs enabled:</ac:task-body>" in result
    assert "[ ]" not in result


def test_convert_checked_task_item_to_confluence_complete_task():
    markdown = "- [X] Database migration reviewed\n"

    result = convert_markdown_to_storage(
        markdown,
        page_slug="00-overview",
        output_format="svg",
        diagram_fragments={},
    )

    assert "<ac:task-list>" in result
    assert "<ac:task-status>complete</ac:task-status>" in result
    assert "<ac:task-body>Database migration reviewed</ac:task-body>" in result
    assert "[X]" not in result


def test_convert_nested_api_bullets_remain_inside_task_body():
    markdown = """- [ ] GCP APIs enabled:
  - `sqladmin.googleapis.com`
  - `container.googleapis.com`
"""

    result = convert_markdown_to_storage(
        markdown,
        page_slug="00-overview",
        output_format="svg",
        diagram_fragments={},
    )

    assert "<ac:task-body>GCP APIs enabled:<ul>" in result
    assert "<li><code>sqladmin.googleapis.com</code></li>" in result
    assert "<li><code>container.googleapis.com</code></li>" in result
    assert result.index("<ul>") > result.index("<ac:task-body>")
    assert result.index("</ul>") < result.index("</ac:task-body>")


def test_convert_normal_unordered_list_is_unchanged():
    markdown = "- GCP APIs enabled\n- Database migration reviewed\n"

    result = convert_markdown_to_storage(
        markdown,
        page_slug="00-overview",
        output_format="svg",
        diagram_fragments={},
    )

    assert result == "<ul>\n<li>GCP APIs enabled</li>\n<li>Database migration reviewed</li>\n</ul>"
    assert "<ac:task-list>" not in result


def test_three_headings_adds_toc_macro():
    markdown = "# One\n\n## Two\n\n## Three\n"

    result = convert_markdown_to_storage(
        markdown,
        page_slug="00-overview",
        output_format="svg",
        diagram_fragments={},
    )

    assert result.startswith('<ac:structured-macro ac:name="toc" />')
