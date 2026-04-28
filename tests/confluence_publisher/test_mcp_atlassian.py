from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent

from confluence_publisher.mcp_atlassian import (
    McpToolError,
    REQUIRED_TOOLS,
    McpAtlassianClient,
    _mcp_env,
    _structured_content,
    build_server_command,
)


def test_build_server_command_limits_enabled_tools_and_version():
    command, args = build_server_command()

    assert command == "uvx"
    assert args[:3] == ["--from", "mcp-atlassian>=0.17.0", "mcp-atlassian"]
    assert "--enabled-tools" in args
    enabled = args[args.index("--enabled-tools") + 1]
    assert enabled.split(",") == list(REQUIRED_TOOLS)
    assert "confluence_download_attachment" not in enabled


class FakeMcpSession:
    def __init__(
        self,
        page_result=None,
        upload_result=None,
        update_result=None,
        create_result=None,
        search_result=None,
    ):
        self.calls = []
        self.page_result = page_result
        self.upload_result = upload_result
        self.update_result = update_result
        self.create_result = create_result
        self.search_result = search_result

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "confluence_get_page":
            if self.page_result is not None:
                return self.page_result
            return {"structuredContent": {"id": "5151854234", "title": "Test"}}
        if name == "confluence_upload_attachment" and self.upload_result is not None:
            return self.upload_result
        if name == "confluence_update_page" and self.update_result is not None:
            return self.update_result
        if name == "confluence_create_page":
            if self.create_result is not None:
                return self.create_result
            return {
                "structuredContent": {
                    "page": {"id": "616161", "title": arguments["title"]},
                },
            }
        if name == "confluence_search" and self.search_result is not None:
            return self.search_result
        return {"structuredContent": {"ok": True}}


def test_mcp_env_requires_credentials_without_leaking_token(monkeypatch):
    monkeypatch.delenv("CONFLUENCE_URL", raising=False)
    monkeypatch.delenv("CONFLUENCE_USERNAME", raising=False)
    monkeypatch.setenv("CONFLUENCE_API_TOKEN", "secret-token")

    with pytest.raises(RuntimeError) as exc_info:
        _mcp_env()

    message = str(exc_info.value)
    assert "CONFLUENCE_URL" in message
    assert "CONFLUENCE_USERNAME" in message
    assert "secret-token" not in message


def test_structured_content_accepts_real_call_tool_result():
    result = CallToolResult(content=[], structuredContent={"id": "5151854234", "title": "Test"})

    assert _structured_content(result) == {"id": "5151854234", "title": "Test"}


def test_structured_content_falls_back_to_json_text_content():
    result = CallToolResult(
        content=[TextContent(type="text", text='{"id": "5151854234", "title": "Test"}')],
    )

    assert _structured_content(result) == {"id": "5151854234", "title": "Test"}


def test_structured_content_accepts_json_string_structured_content():
    result = {
        "structuredContent": (
            '{"message": "Page created successfully", '
            '"page": {"id": "5155096957", "title": "Overview"}}'
        )
    }

    assert _structured_content(result) == {
        "message": "Page created successfully",
        "page": {"id": "5155096957", "title": "Overview"},
    }


def test_structured_content_accepts_fastmcp_wrapped_json_result():
    result = {
        "structuredContent": {
            "result": (
                '{"message": "Page created successfully", '
                '"page": {"id": "5155097071", "title": "Overview"}}'
            )
        }
    }

    assert _structured_content(result) == {
        "message": "Page created successfully",
        "page": {"id": "5155097071", "title": "Overview"},
    }


def test_structured_content_raises_on_mcp_error_result():
    result = CallToolResult(content=[TextContent(type="text", text="page not found")], isError=True)

    with pytest.raises(McpToolError, match="page not found"):
        _structured_content(result)


@pytest.mark.asyncio
async def test_client_gets_page_info():
    session = FakeMcpSession()
    client = McpAtlassianClient(session)

    page = await client.get_page("5151854234")

    assert page.page_id == "5151854234"
    assert page.title == "Test"
    assert session.calls == [
        (
            "confluence_get_page",
            {"page_id": "5151854234", "include_metadata": True, "convert_to_markdown": False},
        )
    ]


@pytest.mark.asyncio
async def test_client_get_page_reads_url_from_links_payload():
    session = FakeMcpSession(
        page_result={
            "structuredContent": {
                "id": "5151854234",
                "title": "Test",
                "_links": {
                    "base": "https://criteo.atlassian.net/wiki",
                    "webui": "/spaces/~6393203da593cb822e995262/pages/5151854234/Test",
                },
            }
        }
    )
    client = McpAtlassianClient(session)

    page = await client.get_page("5151854234")

    assert page.url == (
        "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/"
        "pages/5151854234/Test"
    )


@pytest.mark.asyncio
async def test_client_get_page_reads_url_from_url_payload():
    session = FakeMcpSession(
        page_result={
            "structuredContent": {
                "id": "5151854234",
                "title": "Test",
                "url": "https://criteo.atlassian.net/wiki/pages/5151854234/Test",
            }
        }
    )
    client = McpAtlassianClient(session)

    page = await client.get_page("5151854234")

    assert page.url == "https://criteo.atlassian.net/wiki/pages/5151854234/Test"


@pytest.mark.asyncio
async def test_client_get_page_parses_fastmcp_metadata_wrapper():
    result = CallToolResult(
        content=[],
        structuredContent={
            "result": (
                '{"metadata": {'
                '"id": "2163738446", '
                '"title": "Overview", '
                '"type": "page", '
                '"url": "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/pages/2163738446"'
                "}}"
            )
        },
    )
    session = FakeMcpSession(page_result=result)
    client = McpAtlassianClient(session)

    page = await client.get_page("2163738446")

    assert page.page_id == "2163738446"
    assert page.title == "Overview"
    assert page.url == (
        "https://criteo.atlassian.net/wiki/spaces/"
        "~6393203da593cb822e995262/pages/2163738446"
    )


@pytest.mark.asyncio
async def test_client_get_page_parses_metadata_wrapper_from_json_text_content():
    result = CallToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    '{"metadata": {'
                    '"id": "2163738446", '
                    '"title": "Overview", '
                    '"url": "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/overview"'
                    "}}"
                ),
            )
        ],
    )
    session = FakeMcpSession(page_result=result)
    client = McpAtlassianClient(session)

    page = await client.get_page("2163738446")

    assert page.page_id == "2163738446"
    assert page.title == "Overview"
    assert page.url == "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/overview"


@pytest.mark.asyncio
async def test_client_get_page_rejects_missing_title():
    session = FakeMcpSession(page_result={"structuredContent": {"id": "5151854234"}})
    client = McpAtlassianClient(session)

    with pytest.raises(McpToolError, match="5151854234"):
        await client.get_page("5151854234")


@pytest.mark.asyncio
async def test_client_get_page_raises_mcp_json_error_message():
    result = CallToolResult(
        content=[
            TextContent(
                type="text",
                text='{"error": "Failed to retrieve page by ID \'5155097071\': no access"}',
            )
        ],
    )
    session = FakeMcpSession(page_result=result)
    client = McpAtlassianClient(session)

    with pytest.raises(McpToolError, match="no access"):
        await client.get_page("5155097071")


@pytest.mark.asyncio
async def test_client_get_page_raises_fastmcp_wrapped_json_error_message():
    result = CallToolResult(
        content=[],
        structuredContent={
            "result": (
                '{"error": "Failed to retrieve page by ID '
                "'5155096957': Error retrieving page content: no access\"}"
            )
        },
    )
    session = FakeMcpSession(page_result=result)
    client = McpAtlassianClient(session)

    with pytest.raises(McpToolError, match="no access"):
        await client.get_page("5155096957")


@pytest.mark.asyncio
async def test_client_creates_page_under_parent_as_storage():
    session = FakeMcpSession()
    client = McpAtlassianClient(session)

    page = await client.create_page(
        space_key="~6393203da593cb822e995262",
        title="Guide",
        parent_id="999999",
        storage="<p>Created by publisher.</p>",
    )

    assert page.page_id == "616161"
    assert page.title == "Guide"
    assert session.calls == [
        (
            "confluence_create_page",
            {
                "space_key": "~6393203da593cb822e995262",
                "title": "Guide",
                "content": "<p>Created by publisher.</p>",
                "parent_id": "999999",
                "content_format": "storage",
                "include_content": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_client_create_page_parses_json_text_response():
    result = CallToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    '{"message": "Page created successfully", '
                    '"page": {"id": "5155096957", "title": "Overview"}}'
                ),
            )
        ],
    )
    session = FakeMcpSession(create_result=result)
    client = McpAtlassianClient(session)

    page = await client.create_page(
        space_key="~6393203da593cb822e995262",
        title="Overview",
        parent_id="999999",
        storage="<p>Created by publisher.</p>",
    )

    assert page.page_id == "5155096957"
    assert page.title == "Overview"


@pytest.mark.asyncio
async def test_client_create_page_parses_fastmcp_wrapped_json_result():
    result = CallToolResult(
        content=[],
        structuredContent={
            "result": (
                '{"message": "Page created successfully", '
                '"page": {"id": "5155097071", "title": "Overview"}}'
            )
        },
    )
    session = FakeMcpSession(create_result=result)
    client = McpAtlassianClient(session)

    page = await client.create_page(
        space_key="~6393203da593cb822e995262",
        title="Overview",
        parent_id="999999",
        storage="<p>Created by publisher.</p>",
    )

    assert page.page_id == "5155097071"
    assert page.title == "Overview"


@pytest.mark.asyncio
async def test_create_page_reads_url_from_links_payload():
    session = FakeMcpSession(
        create_result={
            "structuredContent": {
                "page": {
                    "id": "5155099000",
                    "title": "Overview",
                    "_links": {
                        "base": "https://criteo.atlassian.net/wiki",
                        "webui": "/spaces/~6393203da593cb822e995262/pages/5155099000/Overview",
                    },
                }
            }
        }
    )
    client = McpAtlassianClient(session)

    page = await client.create_page(
        space_key="~6393203da593cb822e995262",
        title="Overview",
        parent_id="2163738446",
        storage="<p>Created.</p>",
    )

    assert page.page_id == "5155099000"
    assert page.url == (
        "https://criteo.atlassian.net/wiki/spaces/~6393203da593cb822e995262/"
        "pages/5155099000/Overview"
    )


@pytest.mark.asyncio
async def test_create_page_reads_url_from_wrapper_payload():
    session = FakeMcpSession(
        create_result={
            "structuredContent": {
                "page": {
                    "id": "5155099001",
                    "title": "Overview",
                },
                "url": "https://criteo.atlassian.net/wiki/pages/5155099001/Overview",
            }
        }
    )
    client = McpAtlassianClient(session)

    page = await client.create_page(
        space_key="~6393203da593cb822e995262",
        title="Overview",
        parent_id="2163738446",
        storage="<p>Created.</p>",
    )

    assert page.page_id == "5155099001"
    assert page.url == "https://criteo.atlassian.net/wiki/pages/5155099001/Overview"


@pytest.mark.asyncio
async def test_client_search_pages_by_title_parses_fastmcp_wrapped_json_result():
    result = CallToolResult(
        content=[],
        structuredContent={
            "result": (
                '[{"id": "5155817687", "title": "Post-Deployment Verification"}]'
            )
        },
    )
    session = FakeMcpSession(search_result=result)
    client = McpAtlassianClient(session)

    pages = await client.search_pages_by_title(
        space_key="~6393203da593cb822e995262",
        title="Post-Deployment Verification",
    )

    assert [(page.page_id, page.title) for page in pages] == [
        ("5155817687", "Post-Deployment Verification")
    ]
    assert session.calls == [
        (
            "confluence_search",
            {
                "query": (
                    'title = "Post-Deployment Verification" '
                    'AND type = page AND space = "~6393203da593cb822e995262"'
                ),
                "limit": 10,
                "spaces_filter": "~6393203da593cb822e995262",
            },
        )
    ]


@pytest.mark.asyncio
async def test_client_search_pages_by_title_rejects_mcp_error():
    result = CallToolResult(content=[TextContent(type="text", text="search failed")], isError=True)
    session = FakeMcpSession(search_result=result)
    client = McpAtlassianClient(session)

    with pytest.raises(McpToolError, match="search failed"):
        await client.search_pages_by_title(
            space_key="~6393203da593cb822e995262",
            title="Post-Deployment Verification",
        )


@pytest.mark.asyncio
async def test_client_uploads_attachment_with_minor_edit(tmp_path):
    session = FakeMcpSession()
    client = McpAtlassianClient(session)
    image = tmp_path / "diagram.svg"
    image.write_text("<svg></svg>", encoding="utf-8")

    await client.upload_attachment("5151854234", image)

    assert session.calls == [
        (
            "confluence_upload_attachment",
            {
                "content_id": "5151854234",
                "file_path": str(image),
                "comment": "Rendered Mermaid diagram",
                "minor_edit": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_client_upload_attachment_rejects_mcp_error(tmp_path):
    result = CallToolResult(content=[TextContent(type="text", text="upload failed")], isError=True)
    session = FakeMcpSession(upload_result=result)
    client = McpAtlassianClient(session)
    image = tmp_path / "diagram.svg"
    image.write_text("<svg></svg>", encoding="utf-8")

    with pytest.raises(McpToolError, match="upload failed"):
        await client.upload_attachment("5151854234", image)


@pytest.mark.asyncio
async def test_client_updates_page_as_storage():
    session = FakeMcpSession()
    client = McpAtlassianClient(session)

    await client.update_page_storage("5151854234", "Test", "<p>hello</p>")

    assert session.calls == [
        (
            "confluence_update_page",
            {
                "page_id": "5151854234",
                "title": "Test",
                "content": "<p>hello</p>",
                "content_format": "storage",
                "is_minor_edit": True,
                "version_comment": "Rendered Mermaid diagrams from Markdown source",
            },
        )
    ]


@pytest.mark.asyncio
async def test_client_updates_page_with_custom_version_comment():
    session = FakeMcpSession()
    client = McpAtlassianClient(session)

    await client.update_page_storage(
        "5151854234",
        "Test",
        "<p>hello</p>",
        version_comment="Sync docs",
    )

    assert session.calls == [
        (
            "confluence_update_page",
            {
                "page_id": "5151854234",
                "title": "Test",
                "content": "<p>hello</p>",
                "content_format": "storage",
                "is_minor_edit": True,
                "version_comment": "Sync docs",
            },
        )
    ]


@pytest.mark.asyncio
async def test_client_update_page_storage_rejects_mcp_error():
    result = CallToolResult(content=[TextContent(type="text", text="update failed")], isError=True)
    session = FakeMcpSession(update_result=result)
    client = McpAtlassianClient(session)

    with pytest.raises(McpToolError, match="update failed"):
        await client.update_page_storage("5151854234", "Test", "<p>hello</p>")
