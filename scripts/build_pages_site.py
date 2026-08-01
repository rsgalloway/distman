#!/usr/bin/env python3

"""Build a simple Jekyll-friendly docs site from repository Markdown files."""

import argparse
import re
import shutil
from pathlib import Path

LINK_PATTERNS = (
    (r"\(README\.md\)", "(index.html)"),
    (r"\(docs/README\.md\)", "(docs/index.html)"),
    (r"\(docs/([^)]+)\.md\)", r"(docs/\1.html)"),
    (r"\(([^:)#]+)\.md\)", r"(\1.html)"),
)


def rewrite_links(content: str) -> str:
    """Rewrite repository Markdown links for generated HTML output."""
    for pattern, replacement in LINK_PATTERNS:
        content = re.sub(pattern, replacement, content)
    return content


def extract_title(content: str, fallback: str) -> str:
    """Extract the first Markdown H1 or return a fallback title."""
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def write_page(src: Path, dst: Path, fallback_title: str) -> None:
    """Add Jekyll front matter and write one Markdown page."""
    content = rewrite_links(src.read_text(encoding="utf-8"))
    title = extract_title(content, fallback_title)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(
        f"---\nlayout: default\ntitle: {title}\n---\n\n{content}",
        encoding="utf-8",
    )


def write_site_files(output_dir: Path) -> None:
    """Write the Jekyll configuration, layout, and stylesheet."""
    (output_dir / "_config.yml").write_text(
        "title: distman\n"
        "description: Safe, versioned file distribution for production pipelines\n"
        "markdown: kramdown\n"
        "permalink: pretty\n",
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
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    write_site_files(output_dir)

    docs_dir = repo_root / "docs"
    write_page(docs_dir / "index.md", output_dir / "index.md", "distman")
    for src in docs_dir.glob("*.md"):
        if src.name == "index.md":
            continue
        destination = "index.md" if src.name == "README.md" else src.name
        fallback = "Docs" if src.name == "README.md" else src.stem.replace("-", " ").title()
        write_page(src, output_dir / "docs" / destination, fallback)

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
