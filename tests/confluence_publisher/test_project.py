import pytest

from confluence_publisher.project import discover_markdown_files


def test_discover_markdown_files_returns_posix_relative_paths(tmp_path):
    (tmp_path / "guide").mkdir()
    (tmp_path / "index.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "guide" / "getting-started.md").write_text("# Start\n", encoding="utf-8")
    (tmp_path / "guide" / "notes.txt").write_text("ignore\n", encoding="utf-8")

    result = discover_markdown_files(tmp_path, include=("**/*.md",), exclude=())

    assert result.included == ("guide/getting-started.md", "index.md")
    assert result.excluded_count == 0


def test_discover_markdown_files_applies_include_and_exclude(tmp_path):
    (tmp_path / "guide" / "drafts").mkdir(parents=True)
    (tmp_path / "reference").mkdir()
    (tmp_path / "guide" / "index.md").write_text("# Guide\n", encoding="utf-8")
    (tmp_path / "guide" / "drafts" / "idea.md").write_text("# Draft\n", encoding="utf-8")
    (tmp_path / "reference" / "api.md").write_text("# API\n", encoding="utf-8")

    result = discover_markdown_files(
        tmp_path,
        include=("guide/**/*.md",),
        exclude=("**/drafts/**",),
    )

    assert result.included == ("guide/index.md",)
    assert result.excluded_count == 1


def test_discover_markdown_files_deduplicates_overlapping_includes(tmp_path):
    (tmp_path / "guide").mkdir()
    (tmp_path / "guide" / "index.md").write_text("# Guide\n", encoding="utf-8")

    result = discover_markdown_files(
        tmp_path,
        include=("**/*.md", "guide/*.md"),
        exclude=(),
    )

    assert result.included == ("guide/index.md",)


def test_discover_markdown_files_discovers_md_suffix_case_insensitively(tmp_path):
    (tmp_path / "README.MD").write_text("# Home\n", encoding="utf-8")

    result = discover_markdown_files(tmp_path, include=("**/*.md",), exclude=())

    assert result.included == ("README.MD",)


def test_discover_markdown_files_rejects_parent_traversal_patterns(tmp_path):
    (tmp_path.parent / "outside.md").write_text("# Outside\n", encoding="utf-8")

    with pytest.raises(ValueError, match="parent traversal"):
        discover_markdown_files(tmp_path, include=("../*.md",), exclude=())


def test_discover_markdown_files_excludes_with_project_glob_semantics(tmp_path):
    (tmp_path / "guide").mkdir()
    (tmp_path / "guide" / "index.md").write_text("# Guide\n", encoding="utf-8")

    result = discover_markdown_files(
        tmp_path,
        include=("**/*.md",),
        exclude=("guide/**/*.md",),
    )

    assert result.included == ()
    assert result.excluded_count == 1


def test_discover_markdown_files_excludes_match_project_relative_paths_only(tmp_path):
    project_root = tmp_path / "ancestor-name" / "project"
    project_root.mkdir(parents=True)
    (project_root / "index.md").write_text("# Home\n", encoding="utf-8")

    result = discover_markdown_files(
        project_root,
        include=("**/*.md",),
        exclude=("ancestor-name/**/*.md",),
    )

    assert result.included == ("index.md",)
    assert result.excluded_count == 0


def test_discover_markdown_files_preserves_precise_include_glob_shape(tmp_path):
    (tmp_path / "README.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "README.MD").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "README.extra.md").write_text("# Extra\n", encoding="utf-8")

    result = discover_markdown_files(tmp_path, include=("README.md",), exclude=())

    assert result.included == ("README.MD", "README.md")


def test_discover_markdown_files_preserves_precise_exclude_glob_shape(tmp_path):
    (tmp_path / "README.md").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "README.MD").write_text("# Home\n", encoding="utf-8")
    (tmp_path / "README.extra.md").write_text("# Extra\n", encoding="utf-8")

    result = discover_markdown_files(
        tmp_path,
        include=("**/*.md",),
        exclude=("README.md",),
    )

    assert result.included == ("README.extra.md",)
    assert result.excluded_count == 2
