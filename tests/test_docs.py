#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re
import unittest
import urllib.parse


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


class DocumentationAcceptance(unittest.TestCase):
    def test_relative_markdown_links_resolve_inside_repository(self) -> None:
        failures: list[str] = []
        for path in sorted(ROOT.rglob("*.md")):
            if any(part in (".git", ".venv", ".venv310", "build") for part in path.parts):
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for raw_target in MARKDOWN_LINK.findall(line):
                    target = raw_target.strip().split()[0].strip("<>")
                    if target.startswith(("http://", "https://", "mailto:", "#")):
                        continue
                    target = urllib.parse.unquote(target.split("#", 1)[0])
                    if not target:
                        continue
                    resolved = (path.parent / target).resolve()
                    try:
                        resolved.relative_to(ROOT)
                    except ValueError:
                        failures.append(
                            f"{path.relative_to(ROOT)}:{line_number}: target leaves repository: {raw_target}"
                        )
                        continue
                    if not resolved.exists():
                        failures.append(
                            f"{path.relative_to(ROOT)}:{line_number}: missing target: {raw_target}"
                        )
        self.assertEqual([], failures, "\n".join(failures))

    def test_bilingual_entrypoints_and_architecture_assets_exist(self) -> None:
        required = (
            "README.md",
            "README.zh-CN.md",
            "SECURITY.md",
            "SECURITY.zh-CN.md",
            "CONTRIBUTING.md",
            "CONTRIBUTING.zh-CN.md",
            "docs/architecture.html",
            "docs/assets/architecture-en.png",
            "docs/assets/architecture-zh-CN.png",
            "docs/zh-CN/README.md",
            "docs/zh-CN/architecture.html",
            "docs/zh-CN/architecture.md",
            "docs/zh-CN/command-reference.md",
            "docs/zh-CN/curation.md",
            "docs/zh-CN/quickstart.md",
            "docs/zh-CN/security.md",
            "docs/zh-CN/storage-and-backup.md",
            "docs/zh-CN/tool-adapters.md",
        )
        self.assertEqual([], [path for path in required if not (ROOT / path).is_file()])


if __name__ == "__main__":
    unittest.main()
