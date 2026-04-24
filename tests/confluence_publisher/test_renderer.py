from pathlib import Path
import subprocess

import pytest

from confluence_publisher.models import DiagramBlock
from confluence_publisher.renderer import MermaidRenderer, RenderError


def _patch_mmdc(monkeypatch):
    monkeypatch.setattr("confluence_publisher.renderer.shutil.which", lambda _: "/usr/bin/mmdc")


def test_renderer_reports_missing_mmdc(monkeypatch, tmp_path):
    monkeypatch.setattr("confluence_publisher.renderer.shutil.which", lambda _: None)
    renderer = MermaidRenderer(mmdc_bin="mmdc")
    block = DiagramBlock(1, "00-overview", "graph LR\nA --> B\n", "svg")

    with pytest.raises(RenderError, match="mmdc was not found"):
        renderer.render(block, tmp_path)


def test_renderer_writes_mmd_and_invokes_mmdc(monkeypatch, tmp_path):
    _patch_mmdc(monkeypatch)

    calls = []

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.write_text("<svg></svg>", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("confluence_publisher.renderer.subprocess.run", fake_run)
    renderer = MermaidRenderer(mmdc_bin="mmdc")
    block = DiagramBlock(1, "00-overview", "graph LR\nA --> B\n", "svg")

    rendered = renderer.render(block, tmp_path)

    assert rendered.mmd_path.read_text(encoding="utf-8") == "graph LR\nA --> B\n"
    assert rendered.output_path.name == block.attachment_name
    assert rendered.output_path.read_text(encoding="utf-8") == "<svg></svg>"
    assert calls == [["/usr/bin/mmdc", "-i", str(rendered.mmd_path), "-o", str(rendered.output_path)]]


def test_renderer_uses_png_output_name_and_mmd_suffix(monkeypatch, tmp_path):
    _patch_mmdc(monkeypatch)

    def fake_run(cmd, check, capture_output, text):
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.write_text("png-bytes", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("confluence_publisher.renderer.subprocess.run", fake_run)
    block = DiagramBlock(1, "00-overview", "graph LR\nA --> B\n", "png")

    rendered = MermaidRenderer().render(block, tmp_path)

    assert rendered.output_path.name == block.attachment_name
    assert rendered.output_path.suffix == ".png"
    assert rendered.mmd_path.name == block.attachment_name.replace(".png", ".mmd")


def test_renderer_reports_non_zero_exit_with_stdout_and_stderr(monkeypatch, tmp_path):
    _patch_mmdc(monkeypatch)

    def fake_run(cmd, check, capture_output, text):
        return subprocess.CompletedProcess(cmd, 7, "render stdout", "render stderr")

    monkeypatch.setattr("confluence_publisher.renderer.subprocess.run", fake_run)
    block = DiagramBlock(1, "00-overview", "graph LR\nA --> B\n", "svg")

    with pytest.raises(RenderError) as exc_info:
        MermaidRenderer().render(block, tmp_path)

    message = str(exc_info.value)
    assert "exit=7" in message
    assert "render stdout" in message
    assert "render stderr" in message


def test_renderer_reports_missing_output_file(monkeypatch, tmp_path):
    _patch_mmdc(monkeypatch)

    def fake_run(cmd, check, capture_output, text):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("confluence_publisher.renderer.subprocess.run", fake_run)
    block = DiagramBlock(1, "00-overview", "graph LR\nA --> B\n", "svg")

    with pytest.raises(RenderError, match="empty output file"):
        MermaidRenderer().render(block, tmp_path)


def test_renderer_reports_empty_output_file(monkeypatch, tmp_path):
    _patch_mmdc(monkeypatch)

    def fake_run(cmd, check, capture_output, text):
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("confluence_publisher.renderer.subprocess.run", fake_run)
    block = DiagramBlock(1, "00-overview", "graph LR\nA --> B\n", "svg")

    with pytest.raises(RenderError, match="empty output file"):
        MermaidRenderer().render(block, tmp_path)
