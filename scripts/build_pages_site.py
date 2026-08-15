#!/usr/bin/env python3

"""Build a simple Jekyll-friendly docs site from repository Markdown files."""

import argparse
import json
import re
import shutil
from pathlib import Path

GENERATED_SITE_MARKER = ".distman-pages-output"
MARKDOWN_LINK_RE = re.compile(r"\[(?P<label>[^\]]+)\]\((?P<target>[^)]+)\)")
MERMAID_BLOCK_RE = re.compile(r"```mermaid\s*\n(.*?)\n```", re.DOTALL)


def rewrite_links(content: str, page_kind: str) -> str:
    """Rewrite repository Markdown links for generated HTML output."""

    def replace(match: re.Match) -> str:
        label = match.group("label")
        target = match.group("target")
        if "://" in target or target.startswith(("#", "mailto:")):
            return match.group(0)

        path, separator, fragment = target.partition("#")
        if not path.endswith(".md"):
            return match.group(0)

        if page_kind == "root":
            if path in {"README.md", "./README.md"}:
                rewritten = "./"
            elif path in {"docs/README.md", "docs/index.md"}:
                rewritten = "docs/"
            elif path.startswith("docs/"):
                rewritten = path[:-3] + "/"
            else:
                rewritten = "docs/" + path[:-3] + "/"
        else:
            if path in {"README.md", "./README.md", "index.md", "./index.md"}:
                rewritten = "./"
            elif path in {"../README.md", "../docs/index.md"}:
                rewritten = "../"
            elif path.startswith("../docs/"):
                rewritten = path[len("../docs/") : -3] + "/"
            elif path.startswith("docs/"):
                rewritten = path[len("docs/") : -3] + "/"
            else:
                rewritten = path[:-3] + "/"

        if separator:
            rewritten += "#" + fragment
        return f"[{label}]({rewritten})"

    return MARKDOWN_LINK_RE.sub(replace, content)


def extract_title(content: str, fallback: str) -> str:
    """Extract the first Markdown H1 or return a fallback title."""
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def rewrite_mermaid_blocks(content: str) -> str:
    """Convert fenced Mermaid blocks into raw HTML containers."""

    def replace(match: re.Match) -> str:
        body = match.group(1).strip("\n")
        return '<div class="mermaid">\n' + body + "\n</div>"

    return MERMAID_BLOCK_RE.sub(replace, content)


def write_page(src: Path, dst: Path, fallback_title: str, page_kind: str) -> None:
    """Add Jekyll front matter and write one Markdown page."""
    content = rewrite_links(src.read_text(encoding="utf-8"), page_kind)
    content = rewrite_mermaid_blocks(content)
    title = extract_title(content, fallback_title)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(
        f"---\nlayout: default\ntitle: {json.dumps(title)}\n---\n\n{content}",
        encoding="utf-8",
    )


def write_site_files(output_dir: Path) -> None:
    """Write the Jekyll configuration, layout, and stylesheet."""
    (output_dir / "_config.yml").write_text(
        "title: distman\n"
        "description: Safe, versioned file distribution for production pipelines\n"
        "markdown: kramdown\n"
        "permalink: pretty\n"
        "highlighter: rouge\n"
        "kramdown:\n"
        "  input: GFM\n"
        "  syntax_highlighter: rouge\n"
        "  syntax_highlighter_opts:\n"
        "    css_class: highlight\n",
        encoding="utf-8",
    )

    layouts = output_dir / "_layouts"
    layouts.mkdir()
    (layouts / "default.html").write_text(
        """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{% if page.title %}{{ page.title }} | {% endif %}{{ site.title }}</title>
    <meta name="description" content="{{ site.description }}">
    <link rel="stylesheet" href="{{ '/assets/site.css' | relative_url }}">
    <script type="module">
      import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
      mermaid.initialize({ startOnLoad: true, theme: "dark" });
    </script>
    <script>
      document.addEventListener("DOMContentLoaded", () => {
        for (const heading of document.querySelectorAll(".site-main h2, .site-main h3")) {
          if (!heading.id || heading.querySelector(".header-anchor")) {
            continue;
          }

          const anchor = document.createElement("a");
          anchor.className = "header-anchor";
          anchor.href = `#${encodeURIComponent(heading.id)}`;
          anchor.setAttribute("aria-label", `Link to section: ${heading.textContent.trim()}`);
          anchor.textContent = "#";
          heading.appendChild(anchor);
        }
      });
    </script>
  </head>
  <body>
    <div class="site-shell">
      <header class="site-header">
        <a class="site-brand" href="{{ '/' | relative_url }}">distman</a>
        <nav class="site-nav">
          <a href="{{ '/' | relative_url }}">Home</a>
          <a href="{{ '/docs/getting-started/' | relative_url }}">Get Started</a>
          <a href="{{ '/docs/distribution-config/' | relative_url }}">Configuration</a>
          <a href="{{ '/docs/cli-reference/' | relative_url }}">CLI</a>
          <a href="{{ '/docs/transform-pipelines/' | relative_url }}">Pipelines</a>
          <a href="{{ '/docs/caching/' | relative_url }}">Caching</a>
          <a href="https://github.com/rsgalloway/distman">GitHub</a>
          <a href="https://pypi.org/project/distman/">PyPI</a>
        </nav>
      </header>
      <main class="site-main">{{ content }}</main>
    </div>
  </body>
</html>
""",
        encoding="utf-8",
    )

    assets = output_dir / "assets"
    assets.mkdir()
    (assets / "site.css").write_text(
        """:root {
  --bg: #0a0f19;
  --bg-elev: #111827;
  --panel: #131c2a;
  --border: #243244;
  --text: #ebf2ff;
  --muted: #aebbd1;
  --accent: #36c784;
  --accent-2: #2db7ff;
  --code: #0f1724;
}

* { box-sizing: border-box; }

html, body {
  margin: 0;
  padding: 0;
  background:
    radial-gradient(circle at top, rgba(45,183,255,0.10), transparent 30%),
    linear-gradient(180deg, #0a0f19 0%, #0b111b 100%);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  line-height: 1.65;
}

a { color: var(--accent-2); text-decoration: none; }
a:hover { color: #74d4ff; }

.site-shell { max-width: 980px; margin: 0 auto; padding: 32px 24px 72px; }
.site-header {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 40px;
}
.site-brand { color: var(--text); font-size: 0.98rem; font-weight: 700; letter-spacing: 0.02em; }
.site-nav { display: flex; flex-wrap: wrap; gap: 16px; }
.site-nav a { color: var(--muted); font-size: 0.9rem; }
.site-nav a:hover { color: var(--text); }
.site-main h1:first-child { margin-top: 0; }

h1, h2, h3 { color: var(--text); line-height: 1.15; }
h1 { font-size: clamp(1.9rem, 5.4vw, 3.3rem); margin: 0 0 18px; }
h2 { font-size: 1.6rem; margin-top: 46px; margin-bottom: 16px; }
h3 { font-size: 1.08rem; margin-top: 28px; margin-bottom: 10px; }
.site-main h2, .site-main h3 { display: flex; align-items: center; gap: 0.5rem; }
.header-anchor { color: var(--accent-2); font-weight: 500; opacity: 0; transition: opacity 120ms ease; }
.site-main h2:hover .header-anchor,
.site-main h2:focus-within .header-anchor,
.site-main h3:hover .header-anchor,
.site-main h3:focus-within .header-anchor { opacity: 1; }
p, li { color: var(--muted); font-size: 0.98rem; }
strong { color: var(--text); }

blockquote {
  margin: 24px 0;
  padding: 16px 20px;
  border-left: 4px solid var(--accent);
  background: rgba(19, 28, 42, 0.85);
  color: var(--text);
}
code, pre { font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; }
code {
  padding: 0.15em 0.35em;
  border-radius: 0.35rem;
  background: rgba(255,255,255,0.06);
  color: var(--text);
}
pre {
  overflow-x: auto;
  padding: 18px 20px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--code);
}
pre code { padding: 0; background: transparent; }
.highlight { margin: 0; }
.highlight pre,
pre.highlight {
  overflow-x: auto;
  padding: 18px 20px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--code);
}
.highlight .hll { background: rgba(255,255,255,0.05); }
.highlight .c,
.highlight .cm,
.highlight .c1,
.highlight .cs { color: #7f8ea3; font-style: italic; }
.highlight .k,
.highlight .kd,
.highlight .kn,
.highlight .kp,
.highlight .kr,
.highlight .nt { color: #7cc7ff; }
.highlight .s,
.highlight .sa,
.highlight .sb,
.highlight .sc,
.highlight .dl,
.highlight .sd,
.highlight .s2 { color: #9be38c; }
.highlight .si,
.highlight .se,
.highlight .sh,
.highlight .sx { color: #ffd580; }
.highlight .m,
.highlight .mb,
.highlight .mf,
.highlight .mh,
.highlight .mi,
.highlight .mo { color: #ffb86b; }
.highlight .na,
.highlight .nb,
.highlight .bp,
.highlight .nc,
.highlight .nf,
.highlight .fm,
.highlight .ne,
.highlight .nn { color: #f7d774; }
.highlight .nv,
.highlight .vc,
.highlight .vg,
.highlight .vi { color: #ff9ecb; }
.highlight .o,
.highlight .ow { color: #ff8f70; }
.highlight .p,
.highlight .w { color: #d9e2f2; }
.mermaid {
  margin: 24px 0;
  padding: 18px 16px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--panel);
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  margin: 24px 0;
  background: rgba(19, 28, 42, 0.7);
}
th, td { border: 1px solid var(--border); padding: 12px 14px; text-align: left; }
th { color: var(--text); background: rgba(255,255,255,0.04); }

@media (max-width: 720px) {
  .site-shell { padding: 24px 18px 56px; }
  .site-header { margin-bottom: 28px; }
}
""",
        encoding="utf-8",
    )


def build_site(repo_root: Path, output_dir: Path) -> None:
    """Generate the complete Jekyll source tree."""
    if output_dir.exists():
        marker = output_dir / GENERATED_SITE_MARKER
        legacy_site = (output_dir / "_config.yml").is_file() and (
            output_dir / "_layouts" / "default.html"
        ).is_file()
        if not marker.is_file() and not legacy_site:
            raise ValueError(f"Refusing to replace unrecognized output directory: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    (output_dir / GENERATED_SITE_MARKER).write_text(
        "Generated by scripts/build_pages_site.py\n", encoding="utf-8"
    )
    write_site_files(output_dir)

    docs_dir = repo_root / "docs"
    write_page(
        docs_dir / "index.md",
        output_dir / "index.md",
        "distman",
        page_kind="root",
    )
    for src in docs_dir.glob("*.md"):
        if src.name == "index.md":
            continue
        destination = "index.md" if src.name == "README.md" else src.name
        fallback = "Docs" if src.name == "README.md" else src.stem.replace("-", " ").title()
        write_page(
            src,
            output_dir / "docs" / destination,
            fallback,
            page_kind="docs",
        )

    cname = repo_root / "CNAME"
    if cname.exists():
        shutil.copy2(cname, output_dir / "CNAME")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    build_site(Path(args.repo_root).resolve(), Path(args.output).resolve())


if __name__ == "__main__":
    main()
