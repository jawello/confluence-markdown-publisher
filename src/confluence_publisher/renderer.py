from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from .models import DiagramBlock, RenderedDiagram


class RenderError(RuntimeError):
    pass


class MermaidRenderer:
    def __init__(self, mmdc_bin: str = "mmdc") -> None:
        self.mmdc_bin = mmdc_bin

    def render(self, block: DiagramBlock, temp_dir: Path) -> RenderedDiagram:
        executable = shutil.which(self.mmdc_bin)
        if executable is None:
            raise RenderError(
                "mmdc was not found in PATH. Install @mermaid-js/mermaid-cli or pass --mmdc-bin."
            )

        temp_dir.mkdir(parents=True, exist_ok=True)
        mmd_path = temp_dir / Path(block.attachment_name).with_suffix(".mmd").name
        output_path = temp_dir / block.attachment_name
        mmd_path.write_text(block.source, encoding="utf-8")

        cmd = [executable, "-i", str(mmd_path), "-o", str(output_path)]
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RenderError(
                "mmdc failed for "
                f"{mmd_path}: exit={completed.returncode}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RenderError(f"mmdc produced an empty output file: {output_path}")
        return RenderedDiagram(block=block, mmd_path=mmd_path, output_path=output_path)
