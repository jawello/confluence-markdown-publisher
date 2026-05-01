from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .models import ConfluenceTreeNode, PageInfo, PageStorage

REQUIRED_TOOLS = (
    "confluence_search",
    "confluence_get_page",
    "confluence_get_page_children",
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


def _json_content(content: object) -> object | None:
    for text in _content_texts(content):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    return None


def _content_payload(content: object) -> object | None:
    if isinstance(content, dict):
        if set(content) == {"result"}:
            wrapped = _content_payload(content.get("result"))
            if wrapped is not None:
                return wrapped
        return content
    return _json_content(content)


def _result_payload(result: object) -> object | None:
    if isinstance(result, dict):
        if result.get("isError"):
            _raise_mcp_error(result.get("content"))
        structured = _content_payload(result.get("structuredContent"))
        if structured is not None:
            return structured
        return _json_content(result.get("content"))

    if getattr(result, "isError", False):
        _raise_mcp_error(getattr(result, "content", None))

    structured = _content_payload(getattr(result, "structuredContent", None))
    if structured is not None:
        return structured
    return _json_content(getattr(result, "content", None))


def _cql_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _page_url(payload: dict[str, Any]) -> str | None:
    url = payload.get("url")
    if isinstance(url, str) and url:
        return url
    links = payload.get("_links")
    if not isinstance(links, dict):
        return None
    webui = links.get("webui")
    if not isinstance(webui, str) or not webui:
        return None
    base = links.get("base")
    if isinstance(base, str) and base:
        return f"{base.rstrip('/')}/{webui.lstrip('/')}"
    return webui


def _page_infos(payload: object, tool_name: str) -> list[PageInfo]:
    if not isinstance(payload, list):
        raise McpToolError(f"{tool_name} returned no page list")
    pages: list[PageInfo] = []
    for item in payload:
        if not isinstance(item, dict):
            raise McpToolError(f"{tool_name} returned invalid page item")
        page_id = item.get("id")
        title = item.get("title")
        if page_id is None or str(page_id) == "" or not isinstance(title, str) or not title:
            raise McpToolError(f"{tool_name} returned invalid page item")
        pages.append(PageInfo(page_id=str(page_id), title=title, url=_page_url(item)))
    return pages


def _tree_nodes(payload: object, tool_name: str) -> tuple[ConfluenceTreeNode, ...]:
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        payload = payload["results"]
    if not isinstance(payload, list):
        raise McpToolError(f"{tool_name} returned no child page list")
    nodes = []
    for item in payload:
        if not isinstance(item, dict):
            raise McpToolError(f"{tool_name} returned invalid child page item")
        page_id = item.get("id")
        title = item.get("title")
        if page_id is None or str(page_id) == "" or not isinstance(title, str) or not title:
            raise McpToolError(f"{tool_name} returned invalid child page item")
        nodes.append(
            ConfluenceTreeNode(page_id=str(page_id), title=title, url=_page_url(item))
        )
    return tuple(nodes)


def _page_payload(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("page", "metadata"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return nested
    return payload


def _storage_value(payload: dict[str, Any]) -> str | None:
    body = payload.get("body")
    if isinstance(body, dict):
        storage = body.get("storage")
        if isinstance(storage, dict):
            value = storage.get("value")
            if isinstance(value, str):
                return value
        value = body.get("value")
        if isinstance(value, str):
            return value

    for key in ("storage", "content"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            nested = value.get("value")
            if isinstance(nested, str):
                return nested
    return None


def _version_number(payload: dict[str, Any]) -> int | None:
    version = payload.get("version")
    if isinstance(version, dict):
        number = version.get("number")
        if isinstance(number, int):
            return number
        if isinstance(number, str) and number.isdigit():
            return int(number)
    return None


def _raise_payload_error(payload: dict[str, Any], tool_name: str) -> None:
    error = payload.get("error")
    if isinstance(error, str) and error:
        raise McpToolError(f"{tool_name} returned an error: {error}")


def _raise_mcp_error(content: object) -> None:
    message = _content_message(content)
    if message:
        raise McpToolError(f"MCP tool returned an error: {message}")
    raise McpToolError("MCP tool returned an error")


def _structured_content(result: object) -> dict[str, Any]:
    payload = _result_payload(result)
    if isinstance(payload, dict):
        return payload
    return {}


class McpAtlassianClient:
    def __init__(self, session: McpSession) -> None:
        self.session = session

    async def get_page(self, page_id: str) -> PageInfo:
        result = await self.session.call_tool(
            "confluence_get_page",
            {"page_id": page_id, "include_metadata": True, "convert_to_markdown": False},
        )
        payload = _structured_content(result)
        _raise_payload_error(payload, "confluence_get_page")
        page_payload = _page_payload(payload)
        title = page_payload.get("title")
        if not isinstance(title, str) or not title:
            raise McpToolError(f"confluence_get_page returned no title for page {page_id}")
        return PageInfo(
            page_id=str(page_payload.get("id", page_id)),
            title=title,
            url=_page_url(page_payload) or _page_url(payload),
        )

    async def get_page_storage(self, page_id: str) -> PageStorage:
        result = await self.session.call_tool(
            "confluence_get_page",
            {
                "page_id": page_id,
                "include_metadata": True,
                "convert_to_markdown": False,
                "include_content": True,
            },
        )
        payload = _structured_content(result)
        _raise_payload_error(payload, "confluence_get_page")
        page_payload = _page_payload(payload)
        title = page_payload.get("title")
        if not isinstance(title, str) or not title:
            raise McpToolError(f"confluence_get_page returned no title for page {page_id}")
        storage = _storage_value(page_payload) or _storage_value(payload)
        if storage is None:
            raise McpToolError(f"confluence_get_page returned no storage body for page {page_id}")
        return PageStorage(
            page_id=str(page_payload.get("id", page_id)),
            title=title,
            storage=storage,
            version=_version_number(page_payload) or _version_number(payload),
            url=_page_url(page_payload) or _page_url(payload),
        )

    async def get_page_children(
        self,
        page_id: str,
        *,
        max_children: int | None = None,
    ) -> tuple[ConfluenceTreeNode, ...]:
        limit = 50
        start = 0
        children: list[ConfluenceTreeNode] = []
        while max_children is None or len(children) < max_children:
            result = await self.session.call_tool(
                "confluence_get_page_children",
                {
                    "parent_id": page_id,
                    "expand": "version",
                    "limit": limit,
                    "include_content": False,
                    "convert_to_markdown": False,
                    "start": start,
                    "include_folders": False,
                },
            )
            payload = _result_payload(result)
            if isinstance(payload, dict):
                _raise_payload_error(payload, "confluence_get_page_children")
            batch = _tree_nodes(payload, "confluence_get_page_children")
            remaining = None if max_children is None else max_children - len(children)
            children.extend(batch if remaining is None else batch[:remaining])
            if len(batch) < limit:
                break
            start += len(batch)
        return tuple(children)

    async def get_page_tree(
        self,
        page_id: str,
        *,
        max_depth: int | None,
        max_pages: int,
    ) -> ConfluenceTreeNode:
        seen: set[str] = set()

        async def visit(current_page_id: str, depth: int) -> ConfluenceTreeNode:
            if current_page_id in seen:
                raise McpToolError(
                    f"Confluence page tree contains duplicate page {current_page_id}"
                )
            if len(seen) >= max_pages:
                raise McpToolError(f"Confluence page tree exceeds --max-pages {max_pages}")
            seen.add(current_page_id)
            page = await self.get_page(current_page_id)
            if max_depth is not None and depth >= max_depth:
                return ConfluenceTreeNode(
                    page_id=page.page_id,
                    title=page.title,
                    url=page.url,
                )
            children = []
            remaining_budget = max_pages - len(seen)
            page_children = await self.get_page_children(
                current_page_id,
                max_children=remaining_budget + 1,
            )
            child_ids: set[str] = set()
            for child in page_children:
                if child.page_id in seen:
                    raise McpToolError(
                        f"Confluence page tree contains duplicate page {child.page_id}"
                    )
                if child.page_id in child_ids:
                    raise McpToolError(
                        f"Confluence page tree contains duplicate page {child.page_id}"
                    )
                child_ids.add(child.page_id)
            if len(page_children) > remaining_budget:
                raise McpToolError(f"Confluence page tree exceeds --max-pages {max_pages}")
            for child in page_children:
                children.append(await visit(child.page_id, depth + 1))
            return ConfluenceTreeNode(
                page_id=page.page_id,
                title=page.title,
                url=page.url,
                children=tuple(children),
            )

        return await visit(page_id, 0)

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
        page_payload = _page_payload(payload)
        if not isinstance(page_payload, dict):
            raise McpToolError("confluence_create_page returned no page object")
        page_id = page_payload.get("id")
        if not isinstance(page_id, str) or not page_id:
            raise McpToolError("confluence_create_page returned no page id")
        page_title = page_payload.get("title")
        page_url = _page_url(page_payload) or _page_url(payload)
        return PageInfo(
            page_id=page_id,
            title=page_title if isinstance(page_title, str) and page_title else title,
            url=page_url,
        )

    async def search_pages_by_title(
        self,
        *,
        space_key: str,
        title: str,
        limit: int = 10,
    ) -> list[PageInfo]:
        result = await self.session.call_tool(
            "confluence_search",
            {
                "query": (
                    f"title = {_cql_string(title)} "
                    f"AND type = page AND space = {_cql_string(space_key)}"
                ),
                "limit": limit,
                "spaces_filter": space_key,
            },
        )
        return _page_infos(_result_payload(result), "confluence_search")

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
