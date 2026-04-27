from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .models import PageInfo

REQUIRED_TOOLS = (
    "confluence_get_page",
    "confluence_get_attachments",
    "confluence_create_page",
    "confluence_upload_attachment",
    "confluence_update_page",
)


class McpToolError(RuntimeError):
    """Raised when an MCP tool response cannot be used safely."""


class McpSession(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> object:
        ...


def build_server_command() -> tuple[str, list[str]]:
    return (
        "uvx",
        [
            "--from",
            "mcp-atlassian>=0.17.0",
            "mcp-atlassian",
            "--enabled-tools",
            ",".join(REQUIRED_TOOLS),
        ],
    )


def _mcp_env() -> dict[str, str]:
    required = ("CONFLUENCE_URL", "CONFLUENCE_USERNAME", "CONFLUENCE_API_TOKEN")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"Missing required Confluence environment variables: {', '.join(missing)}"
        )
    env = os.environ.copy()
    return env


def _content_texts(content: object) -> list[str]:
    if not content:
        return []
    if isinstance(content, str):
        return [content]
    items = content if isinstance(content, list) else [content]
    texts = []
    for item in items:
        text = item.get("text") if isinstance(item, dict) else getattr(item, "text", None)
        if isinstance(text, str):
            texts.append(text)
    return texts


def _content_message(content: object) -> str:
    message = "\n".join(_content_texts(content)).strip()
    if len(message) > 500:
        return f"{message[:497]}..."
    return message


def _json_text_content(content: object) -> dict[str, Any]:
    for text in _content_texts(content):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _mapping_content(content: object) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    return _json_text_content(content)


def _unwrap_wrapped_result(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != {"result"}:
        return payload
    wrapped = _mapping_content(payload.get("result"))
    return wrapped or payload


def _raise_mcp_error(content: object) -> None:
    message = _content_message(content)
    if message:
        raise McpToolError(f"MCP tool returned an error: {message}")
    raise McpToolError("MCP tool returned an error")


def _structured_content(result: object) -> dict[str, Any]:
    if isinstance(result, dict):
        if result.get("isError"):
            _raise_mcp_error(result.get("content"))
        structured = _mapping_content(result.get("structuredContent"))
        if structured:
            return _unwrap_wrapped_result(structured)
        return _json_text_content(result.get("content"))

    if getattr(result, "isError", False):
        _raise_mcp_error(getattr(result, "content", None))

    structured = _mapping_content(getattr(result, "structuredContent", None))
    if structured:
        return _unwrap_wrapped_result(structured)
    return _json_text_content(getattr(result, "content", None))


class McpAtlassianClient:
    def __init__(self, session: McpSession) -> None:
        self.session = session

    async def get_page(self, page_id: str) -> PageInfo:
        result = await self.session.call_tool(
            "confluence_get_page",
            {"page_id": page_id, "include_metadata": True, "convert_to_markdown": False},
        )
        payload = _structured_content(result)
        title = payload.get("title")
        if not isinstance(title, str) or not title:
            raise McpToolError(f"confluence_get_page returned no title for page {page_id}")
        return PageInfo(page_id=str(payload.get("id", page_id)), title=title)

    async def create_page(
        self,
        *,
        space_key: str,
        title: str,
        parent_id: str,
        storage: str,
    ) -> PageInfo:
        result = await self.session.call_tool(
            "confluence_create_page",
            {
                "space_key": space_key,
                "title": title,
                "content": storage,
                "parent_id": parent_id,
                "content_format": "storage",
                "include_content": False,
            },
        )
        payload = _structured_content(result)
        page_payload = payload.get("page", payload)
        if not isinstance(page_payload, dict):
            raise McpToolError("confluence_create_page returned no page object")
        page_id = page_payload.get("id")
        if not isinstance(page_id, str) or not page_id:
            raise McpToolError("confluence_create_page returned no page id")
        page_title = page_payload.get("title")
        return PageInfo(
            page_id=page_id,
            title=page_title if isinstance(page_title, str) and page_title else title,
        )

    async def upload_attachment(self, page_id: str, file_path: Path) -> None:
        result = await self.session.call_tool(
            "confluence_upload_attachment",
            {
                "content_id": page_id,
                "file_path": str(file_path),
                "comment": "Rendered Mermaid diagram",
                "minor_edit": True,
            },
        )
        _structured_content(result)

    async def update_page_storage(
        self,
        page_id: str,
        title: str,
        storage: str,
        version_comment: str = "Rendered Mermaid diagrams from Markdown source",
    ) -> None:
        result = await self.session.call_tool(
            "confluence_update_page",
            {
                "page_id": page_id,
                "title": title,
                "content": storage,
                "content_format": "storage",
                "is_minor_edit": True,
                "version_comment": version_comment,
            },
        )
        _structured_content(result)


@asynccontextmanager
async def connect_mcp_atlassian() -> AsyncIterator[McpAtlassianClient]:
    command, args = build_server_command()
    server_params = StdioServerParameters(command=command, args=args, env=_mcp_env())
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield McpAtlassianClient(session)
