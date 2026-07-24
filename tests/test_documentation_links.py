"""Guard against broken repository-relative links in project documentation."""

import re
from pathlib import Path
from urllib.parse import unquote

MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
EXTERNAL_SCHEMES = ("http://", "https://", "mailto:")


def test_repository_relative_documentation_links_resolve() -> None:
    project_root = Path(__file__).resolve().parent.parent
    markdown_files = [project_root / "README.md", *sorted((project_root / "docs").rglob("*.md"))]
    broken_links: list[str] = []

    for markdown_file in markdown_files:
        content = markdown_file.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK.finditer(content):
            raw_target = match.group(1).strip()
            if not raw_target or raw_target.startswith(("#", "/", *EXTERNAL_SCHEMES)):
                continue

            target_without_fragment = raw_target.split("#", maxsplit=1)[0]
            target = markdown_file.parent / unquote(target_without_fragment)
            if not target.exists():
                relative_file = markdown_file.relative_to(project_root)
                line_number = content.count("\n", 0, match.start()) + 1
                broken_links.append(f"{relative_file}:{line_number}: {raw_target}")

    assert broken_links == []
