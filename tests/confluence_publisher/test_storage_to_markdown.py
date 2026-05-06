import pytest

from confluence_publisher.storage_to_markdown import storage_to_markdown


def test_storage_code_macro_converts_to_markdown_fence():
    storage = (
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">bash</ac:parameter>'
        "<ac:plain-text-body><![CDATA[echo hello\n]]></ac:plain-text-body>"
        "</ac:structured-macro>"
    )

    result = storage_to_markdown(storage)

    assert result.unsupported == ()
    assert result.markdown == "```bash\necho hello\n```\n"


def test_storage_code_macro_with_markdown_fence_is_reported_as_unsupported():
    storage = (
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">bash</ac:parameter>'
        "<ac:plain-text-body><![CDATA[echo before\n```\necho after]]></ac:plain-text-body>"
        "</ac:structured-macro>"
    )

    result = storage_to_markdown(storage)

    assert result.markdown == ""
    assert result.unsupported == (
        "Unsupported Confluence code macro: contains Markdown fence delimiter",
    )


def test_storage_mermaid_image_with_source_expand_converts_to_mermaid_fence():
    storage = (
        '<ac:image ac:alt="Mermaid diagram" ac:width="1000">'
        '<ri:attachment ri:filename="diagram-page-01-a1b2c3d4.svg" />'
        "</ac:image>"
        '<ac:structured-macro ac:name="expand">'
        '<ac:parameter ac:name="title">Mermaid source</ac:parameter>'
        "<ac:rich-text-body>"
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">mermaid</ac:parameter>'
        "<ac:plain-text-body><![CDATA[graph LR\nA --> B]]></ac:plain-text-body>"
        "</ac:structured-macro>"
        "</ac:rich-text-body>"
        "</ac:structured-macro>"
    )

    result = storage_to_markdown(storage)

    assert result.unsupported == ()
    assert result.markdown == "```mermaid\ngraph LR\nA --> B\n```\n"


def test_storage_non_publisher_image_with_mermaid_source_is_reported_as_unsupported():
    storage = (
        '<ac:image ac:alt="Architecture">'
        '<ri:attachment ri:filename="architecture.png" />'
        "</ac:image>"
        '<ac:structured-macro ac:name="expand">'
        '<ac:parameter ac:name="title">Mermaid source</ac:parameter>'
        "<ac:rich-text-body>"
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">mermaid</ac:parameter>'
        "<ac:plain-text-body><![CDATA[graph LR\nA --> B]]></ac:plain-text-body>"
        "</ac:structured-macro>"
        "</ac:rich-text-body>"
        "</ac:structured-macro>"
    )

    result = storage_to_markdown(storage)

    assert result.markdown == ""
    assert result.unsupported == (
        "Unsupported Confluence image: architecture.png",
        "Unsupported Confluence macro: expand",
    )


def test_storage_mermaid_html_image_with_source_expand_converts_to_mermaid_fence():
    storage = (
        '<img alt="diagram-page-01-a1b2c3d4.svg" '
        'src="https://example.atlassian.net/wiki/download/attachments/1/diagram.svg" />'
        '<ac:structured-macro ac:name="expand">'
        '<ac:parameter ac:name="title">Mermaid source</ac:parameter>'
        "<ac:rich-text-body>"
        '<ac:structured-macro ac:name="code">'
        '<ac:parameter ac:name="language">mermaid</ac:parameter>'
        "<ac:plain-text-body><![CDATA[graph LR\nA --> B]]></ac:plain-text-body>"
        "</ac:structured-macro>"
        "</ac:rich-text-body>"
        "</ac:structured-macro>"
    )

    result = storage_to_markdown(storage)

    assert result.unsupported == ()
    assert result.markdown == "```mermaid\ngraph LR\nA --> B\n```\n"


def test_storage_common_inline_markup_converts_to_markdown():
    storage = (
        "<p>Use <strong>bold</strong>, <em>italic</em>, "
        "<code>kubectl get pods</code>, and "
        '<a href="https://example.com/docs">docs</a>.</p>'
    )

    result = storage_to_markdown(storage)

    assert result.unsupported == ()
    assert result.markdown == (
        "Use **bold**, *italic*, `kubectl get pods`, and "
        "[docs](https://example.com/docs).\n"
    )


def test_storage_inline_code_with_backtick_is_reported_as_unsupported():
    storage = "<p>Use <code>`literal`</code>.</p>"

    result = storage_to_markdown(storage)

    assert result.unsupported == (
        "Unsupported Confluence inline code: contains Markdown backtick",
    )


def test_storage_panels_tasks_and_toc_convert_to_supported_markdown():
    storage = (
        '<ac:structured-macro ac:name="toc" />'
        '<ac:structured-macro ac:name="warning">'
        "<ac:rich-text-body><p>Check credentials.</p></ac:rich-text-body>"
        "</ac:structured-macro>"
        "<ac:task-list>"
        "<ac:task><ac:task-status>incomplete</ac:task-status>"
        "<ac:task-body>Review docs</ac:task-body></ac:task>"
        "<ac:task><ac:task-status>complete</ac:task-status>"
        "<ac:task-body>Publish draft</ac:task-body></ac:task>"
        "</ac:task-list>"
    )

    result = storage_to_markdown(storage)

    assert result.unsupported == ()
    assert result.markdown == (
        "> Warning: Check credentials.\n"
        "\n"
        "- [ ] Review docs\n"
        "- [x] Publish draft\n"
    )


def test_storage_standalone_image_is_reported_as_unsupported():
    storage = (
        '<ac:image ac:alt="Architecture">'
        '<ri:attachment ri:filename="architecture.png" />'
        "</ac:image>"
    )

    result = storage_to_markdown(storage)

    assert result.markdown == ""
    assert result.unsupported == ("Unsupported Confluence image: architecture.png",)


def test_storage_standalone_html_image_is_reported_as_unsupported():
    storage = '<p><img alt="Architecture" src="https://example.com/architecture.png" /></p>'

    result = storage_to_markdown(storage)

    assert result.markdown == ""
    assert result.unsupported == ("Unsupported Confluence image: Architecture",)


def test_storage_confluence_link_is_reported_as_unsupported():
    storage = '<p>See <ac:link><ri:page ri:content-title="Target" /></ac:link>.</p>'

    result = storage_to_markdown(storage)

    assert result.unsupported == ("Unsupported Confluence element: ac:link",)


def test_storage_confluence_mention_is_reported_as_unsupported():
    storage = '<p>Owner: <ri:user ri:account-id="abc123" /></p>'

    result = storage_to_markdown(storage)

    assert result.unsupported == ("Unsupported Confluence element: ri:user",)


def test_storage_lists_and_simple_table_convert_to_markdown():
    storage = (
        "<ul><li>First</li><li>Second</li></ul>"
        "<ol><li>One</li><li>Two</li></ol>"
        "<table><tr><th>Name</th><th>Value</th></tr>"
        "<tr><td>Region</td><td>us-east1</td></tr></table>"
    )

    result = storage_to_markdown(storage)

    assert result.unsupported == ()
    assert result.markdown == (
        "- First\n"
        "- Second\n"
        "\n"
        "1. One\n"
        "2. Two\n"
        "\n"
        "| Name | Value |\n"
        "| --- | --- |\n"
        "| Region | us-east1 |\n"
    )


def test_storage_nested_lists_convert_to_markdown():
    storage = "<ul><li>Parent<ul><li>Child</li></ul></li><li>Next</li></ul>"

    result = storage_to_markdown(storage)

    assert result.unsupported == ()
    assert result.markdown == "- Parent\n  - Child\n- Next\n"


def test_storage_task_body_nested_lists_convert_to_markdown_children():
    storage = (
        "<ac:task-list>"
        "<ac:task><ac:task-status>incomplete</ac:task-status>"
        "<ac:task-body>GCP APIs enabled:<ul>"
        "<li><code>sqladmin.googleapis.com</code></li>"
        "<li><code>container.googleapis.com</code></li>"
        "</ul></ac:task-body></ac:task>"
        "</ac:task-list>"
    )

    result = storage_to_markdown(storage)

    assert result.unsupported == ()
    assert result.markdown == (
        "- [ ] GCP APIs enabled:\n"
        "  - `sqladmin.googleapis.com`\n"
        "  - `container.googleapis.com`\n"
    )


def test_storage_task_body_nested_task_list_converts_to_markdown_children():
    storage = (
        "<ac:task-list>"
        "<ac:task><ac:task-status>incomplete</ac:task-status>"
        "<ac:task-body>config.yaml updated:<ac:task-list>"
        "<ac:task><ac:task-status>incomplete</ac:task-status>"
        "<ac:task-body>IAM profiles point to <code>{GCP_PROJECT}</code></ac:task-body>"
        "</ac:task>"
        "<ac:task><ac:task-status>complete</ac:task-status>"
        "<ac:task-body>Service accounts are correct</ac:task-body>"
        "</ac:task>"
        "</ac:task-list></ac:task-body></ac:task>"
        "</ac:task-list>"
    )

    result = storage_to_markdown(storage)

    assert result.unsupported == ()
    assert result.markdown == (
        "- [ ] config.yaml updated:\n"
        "  - [ ] IAM profiles point to `{GCP_PROJECT}`\n"
        "  - [x] Service accounts are correct\n"
    )


def test_storage_non_mermaid_expand_is_reported_as_unsupported():
    storage = (
        '<ac:structured-macro ac:name="expand">'
        '<ac:parameter ac:name="title">Details</ac:parameter>'
        "<ac:rich-text-body><p>Hidden text.</p></ac:rich-text-body>"
        "</ac:structured-macro>"
    )

    result = storage_to_markdown(storage)

    assert result.markdown == ""
    assert result.unsupported == ("Unsupported Confluence macro: expand",)


def test_storage_unknown_macro_is_reported_as_unsupported():
    storage = '<ac:structured-macro ac:name="jira"><ac:rich-text-body /></ac:structured-macro>'

    result = storage_to_markdown(storage)

    assert result.markdown == ""
    assert result.unsupported == ("Unsupported Confluence macro: jira",)
