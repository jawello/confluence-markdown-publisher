from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import yaml


DEFAULT_VERSION_COMMENT = "Sync documentation from Markdown"
DEFAULT_INCLUDE = ("**/*.md",)
SUPPORTED_OUTPUT_FORMATS = {"svg", "png"}


class ManifestError(ValueError):
    pass


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader, node: Any, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate mapping key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class ManifestPage:
    path: str
    page_id: str | None
    title: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class ProjectManifest:
    path: Path
    space: str | None
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    version_comment: str
    output_format: str
    diagram_width: int
    allow_diagram_fallback: bool
    pages: dict[str, ManifestPage]
    root_page_id: str | None = None
    root_url: str | None = None


def _as_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{field_name} must be a mapping")
    return value


def _as_string_list(value: Any, field_name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ManifestError(f"{field_name} must be a list of non-empty strings")
    return tuple(value)


def _normalize_page_path(value: str) -> str:
    if (
        len(value) >= 2
        and value[0].isalpha()
        and value[1] == ":"
    ):
        raise ManifestError(f"Invalid page path: {value}")
    normalized = PurePosixPath(value.replace("\\", "/")).as_posix()
    if PurePosixPath(normalized).is_absolute():
        raise ManifestError(f"Invalid page path: {value}")
    if normalized in ("", ".") or ".." in PurePosixPath(normalized).parts:
        raise ManifestError(f"Invalid page path: {value}")
    return normalized


def _optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{field_name} must be a non-empty string")
    return value


def _positive_int(value: Any, field_name: str, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ManifestError(f"{field_name} must be a positive integer")
    return value


def _url_contains_page_id_segment(url: str, page_id: str) -> bool:
    path_segments = urlparse(url).path.split("/")
    return any(unquote(segment) == page_id for segment in path_segments)


def _page_id_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    query_page_ids = parse_qs(parsed.query).get("pageId", [])
    for page_id in query_page_ids:
        if page_id:
            return page_id

    segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
    for index, segment in enumerate(segments[:-1]):
        if segment == "pages" and segments[index + 1]:
            return segments[index + 1]
    return None


def _space_from_url(url: str) -> str | None:
    segments = [unquote(segment) for segment in urlparse(url).path.split("/") if segment]
    for index, segment in enumerate(segments[:-1]):
        if segment == "spaces" and segments[index + 1]:
            return segments[index + 1]
    return None


def _root_metadata(root: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    root_raw = root.get("root")
    root_page_id = _optional_str(root.get("root_page_id"), "root_page_id")
    root_url = _optional_str(root.get("root_url"), "root_url")

    if root_raw is not None:
        root_data = _as_mapping(root_raw, "root")
        nested_page_id = _optional_str(root_data.get("page_id"), "root.page_id")
        nested_url = _optional_str(root_data.get("url"), "root.url")
        if (
            root_page_id is not None
            and nested_page_id is not None
            and root_page_id != nested_page_id
        ):
            raise ManifestError("root.page_id conflicts with root_page_id")
        if root_url is not None and nested_url is not None and root_url != nested_url:
            raise ManifestError("root.url conflicts with root_url")
        root_page_id = root_page_id or nested_page_id
        root_url = root_url or nested_url

    if root_url is not None:
        url_page_id = _page_id_from_url(root_url)
        if url_page_id is None:
            raise ManifestError("root.url must contain a Confluence page ID")
        if root_page_id is not None and root_page_id != url_page_id:
            raise ManifestError(f"root.url must contain root page_id {root_page_id}")
        root_page_id = root_page_id or url_page_id

    return root_page_id, root_url, _space_from_url(root_url) if root_url is not None else None


def load_manifest(path: Path, *, allow_missing_page_ids: bool = False) -> ProjectManifest:
    if not path.exists():
        raise ManifestError(f"Manifest not found: {path}")

    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise ManifestError(f"Invalid YAML in manifest {path}: {exc}") from exc

    if raw is None:
        raw = {}
    root = _as_mapping(raw, "manifest")
    defaults = _as_mapping(root.get("defaults", {}), "defaults")
    pages_raw = _as_mapping(root.get("pages"), "pages")
    root_page_id, root_url, root_space = _root_metadata(root)

    output_format = defaults.get("output_format", "svg")
    if not isinstance(output_format, str) or output_format not in SUPPORTED_OUTPUT_FORMATS:
        raise ManifestError("defaults.output_format must be one of: png, svg")

    version_comment = defaults.get("version_comment", DEFAULT_VERSION_COMMENT)
    if not isinstance(version_comment, str) or not version_comment:
        raise ManifestError("defaults.version_comment must be a non-empty string")

    fallback = defaults.get("allow_diagram_fallback", False)
    if not isinstance(fallback, bool):
        raise ManifestError("defaults.allow_diagram_fallback must be a boolean")

    pages: dict[str, ManifestPage] = {}
    seen_page_ids: dict[str, str] = {}
    for raw_page_path, raw_page in pages_raw.items():
        if not isinstance(raw_page_path, str):
            raise ManifestError("pages keys must be strings")
        page_path = _normalize_page_path(raw_page_path)
        if page_path in pages:
            raise ManifestError(f"Duplicate page path {page_path}")
        page_data = _as_mapping(raw_page, f"pages.{page_path}")
        page_id = page_data.get("page_id")
        if page_id is None and allow_missing_page_ids:
            page_id = None
        elif not isinstance(page_id, str) or not page_id:
            raise ManifestError(f"pages.{page_path}.page_id must be a non-empty string")
        if page_id is not None and page_id in seen_page_ids:
            raise ManifestError(
                f"Duplicate page_id {page_id} for {seen_page_ids[page_id]} and {page_path}"
            )
        if page_id is not None:
            seen_page_ids[page_id] = page_path

        title = _optional_str(page_data.get("title"), f"pages.{page_path}.title")
        url = _optional_str(page_data.get("url"), f"pages.{page_path}.url")
        if (
            page_id is not None
            and url is not None
            and not _url_contains_page_id_segment(url, page_id)
        ):
            raise ManifestError(f"pages.{page_path}.url must contain page_id {page_id}")

        pages[page_path] = ManifestPage(path=page_path, page_id=page_id, title=title, url=url)

    return ProjectManifest(
        path=path,
        space=_optional_str(root.get("space"), "space") or root_space,
        include=_as_string_list(root.get("include"), "include", DEFAULT_INCLUDE),
        exclude=_as_string_list(root.get("exclude"), "exclude", ()),
        version_comment=version_comment,
        output_format=output_format,
        diagram_width=_positive_int(defaults.get("diagram_width"), "defaults.diagram_width", 1000),
        allow_diagram_fallback=fallback,
        pages=pages,
        root_page_id=root_page_id,
        root_url=root_url,
    )


def write_manifest_page_ids(path: Path, page_ids: dict[str, str]) -> None:
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise ManifestError(f"Invalid YAML in manifest {path}: {exc}") from exc

    if raw is None:
        raw = {}
    root = _as_mapping(raw, "manifest")
    pages = _as_mapping(root.get("pages"), "pages")
    for page_path, page_id in page_ids.items():
        normalized_path = _normalize_page_path(page_path)
        page_data = _as_mapping(pages.get(normalized_path), f"pages.{normalized_path}")
        page_data["page_id"] = page_id

    path.write_text(
        yaml.safe_dump(root, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
