from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

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
    page_id: str
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


def load_manifest(path: Path) -> ProjectManifest:
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
        if not isinstance(page_id, str) or not page_id:
            raise ManifestError(f"pages.{page_path}.page_id must be a non-empty string")
        if page_id in seen_page_ids:
            raise ManifestError(
                f"Duplicate page_id {page_id} for {seen_page_ids[page_id]} and {page_path}"
            )
        seen_page_ids[page_id] = page_path

        title = _optional_str(page_data.get("title"), f"pages.{page_path}.title")
        url = _optional_str(page_data.get("url"), f"pages.{page_path}.url")
        if url is not None and not _url_contains_page_id_segment(url, page_id):
            raise ManifestError(f"pages.{page_path}.url must contain page_id {page_id}")

        pages[page_path] = ManifestPage(path=page_path, page_id=page_id, title=title, url=url)

    return ProjectManifest(
        path=path,
        space=_optional_str(root.get("space"), "space"),
        include=_as_string_list(root.get("include"), "include", DEFAULT_INCLUDE),
        exclude=_as_string_list(root.get("exclude"), "exclude", ()),
        version_comment=version_comment,
        output_format=output_format,
        diagram_width=_positive_int(defaults.get("diagram_width"), "defaults.diagram_width", 1000),
        allow_diagram_fallback=fallback,
        pages=pages,
    )
