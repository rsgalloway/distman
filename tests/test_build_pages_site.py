#!/usr/bin/env python3

"""Tests for the GitHub Pages site builder."""

from pathlib import Path

import pytest

from scripts import build_pages_site


def test_rewrite_root_page_links_for_pretty_permalinks():
    content = "[Guide](getting-started.md) [Config](docs/distribution-config.md#paths)"

    assert build_pages_site.rewrite_links(content, page_kind="root") == (
        "[Guide](docs/getting-started/) " "[Config](docs/distribution-config/#paths)"
    )


def test_rewrite_docs_page_links_for_pretty_permalinks():
    content = "[Cache](caching.md) [Home](../README.md)"

    assert build_pages_site.rewrite_links(content, page_kind="docs") == (
        "[Cache](caching/) [Home](../)"
    )


def test_write_page_quotes_yaml_title(tmp_path: Path):
    source = tmp_path / "source.md"
    destination = tmp_path / "output" / "page.md"
    source.write_text('# Configure: "Safely"\n', encoding="utf-8")

    build_pages_site.write_page(source, destination, "Fallback", page_kind="docs")

    assert 'title: "Configure: \\"Safely\\""' in destination.read_text(encoding="utf-8")


def test_write_page_rewrites_mermaid_fences(tmp_path: Path):
    source = tmp_path / "source.md"
    destination = tmp_path / "output" / "page.md"
    source.write_text(
        "# Diagram\n\n```mermaid\nflowchart TD\n    A --> B\n```\n",
        encoding="utf-8",
    )

    build_pages_site.write_page(source, destination, "Fallback", page_kind="docs")

    content = destination.read_text(encoding="utf-8")
    assert '<div class="mermaid">\nflowchart TD\n    A --> B\n</div>' in content
    assert "```mermaid" not in content


def test_build_site_refuses_unrecognized_output_directory(tmp_path: Path):
    output = tmp_path / "unrelated"
    output.mkdir()
    (output / "keep.txt").write_text("important", encoding="utf-8")

    with pytest.raises(ValueError, match="Refusing to replace"):
        build_pages_site.build_site(tmp_path, output)

    assert (output / "keep.txt").read_text(encoding="utf-8") == "important"


def test_build_site_writes_mermaid_rouge_and_anchor_support(tmp_path: Path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "index.md").write_text("# distman\n", encoding="utf-8")
    (docs_dir / "guide.md").write_text(
        "# Guide\n\n## Section\n\n```mermaid\nflowchart TD\n    A --> B\n```\n",
        encoding="utf-8",
    )

    output = tmp_path / "site"
    build_pages_site.build_site(tmp_path, output)

    config = (output / "_config.yml").read_text(encoding="utf-8")
    layout = (output / "_layouts" / "default.html").read_text(encoding="utf-8")
    css = (output / "assets" / "site.css").read_text(encoding="utf-8")
    guide = (output / "docs" / "guide.md").read_text(encoding="utf-8")

    assert "highlighter: rouge" in config
    assert "syntax_highlighter: rouge" in config
    assert "cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs" in layout
    assert "header-anchor" in layout
    assert ".highlight" in css
    assert ".mermaid" in css
    assert '<div class="mermaid">\nflowchart TD\n    A --> B\n</div>' in guide
