from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re


def slugify(value: str) -> str:
    lowered = value.lower()
    ascii_only = lowered.encode("ascii", "ignore").decode("ascii")
    replaced = re.sub(r"[^a-z0-9]+", "-", ascii_only)
    return replaced.strip("-") or "page"


@dataclass(frozen=True)
class DiagramBlock:
    ordinal: int
    page_slug: str
    source: str
    output_format: str = "svg"
    display_width: int | None = None

    @property
    def hash8(self) -> str:
        normalized = self.source.replace("\r\n", "\n").encode("utf-8")
        return sha256(normalized).hexdigest()[:8]

    @property
    def attachment_name(self) -> str:
        return f"diagram-{slugify(self.page_slug)}-{self.ordinal:02d}-{self.hash8}.{self.output_format}"


@dataclass(frozen=True)
class RenderedDiagram:
    block: DiagramBlock
    mmd_path: Path
    output_path: Path


@dataclass(frozen=True)
class PageInfo:
    page_id: str
    title: str
    url: str | None = None
