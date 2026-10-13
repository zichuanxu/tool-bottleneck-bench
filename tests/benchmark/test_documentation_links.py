"""Every repository path named in the public documentation must resolve.

A hash recorded against a file that is not shipped is decoration, not
provenance. Paths that are deliberately retained privately are exempt, and the
exemption is only granted where the documentation says so on the same line with
the literal marker ``(private)``. A documented generated PDF may be absent from
a source-only supplement when its build entry point exists.
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = ("README.md", "results/README.md", "paper/README.md", "docs/*.md")
PRIVATE_MARKER = "(private)"
TOP_LEVEL = (
    "configs",
    "docs",
    "experiments",
    "paper",
    "protocols",
    "results",
    "scripts",
    "src",
    "tests",
    "vendor",
)
REPOSITORY_SUFFIXES = (
    "bib", "bst", "cff", "json", "jsonl", "lock", "md", "py", "sty", "tex", "toml",
)
PATH_RE = re.compile(
    r"`(?:" + "|".join(TOP_LEVEL) + r")/[A-Za-z0-9_./*+@:-]+`"
)
NAME_RE = re.compile(
    r"`[A-Za-z0-9_][A-Za-z0-9_.*-]*\.(?:" + "|".join(REPOSITORY_SUFFIXES) + r")`"
)
# Shell globs and directory placeholders are named as families, not as files.
GLOB_CHARS = "*"


def _documents() -> list[Path]:
    found: list[Path] = []
    for pattern in DOCUMENTS:
        found.extend(sorted(ROOT.glob(pattern)))
    return found


def _resolves(raw: str) -> bool:
    if raw == "paper/main.pdf" and (ROOT / "paper/main.tex").is_file():
        return True  # Produced by make paper; not shipped in anonymous sources.
    if "/" not in raw:
        # A bare file name is a claim that the file is somewhere in the release.
        return any(ROOT.rglob(raw))
    if GLOB_CHARS in raw:
        parent = ROOT / str(Path(raw).parent)
        return parent.is_dir() and any(parent.glob(Path(raw).name))
    return (ROOT / raw).exists()


class DocumentationLinkTest(unittest.TestCase):
    def test_documents_exist(self) -> None:
        self.assertTrue(_documents())
        for path in _documents():
            self.assertTrue(path.is_file(), path)

    def test_named_repository_paths_resolve(self) -> None:
        unresolved: list[str] = []
        checked = 0
        for document in _documents():
            for number, line in enumerate(
                document.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if PRIVATE_MARKER in line:
                    continue
                found = {match.group(0) for match in PATH_RE.finditer(line)}
                found.update(match.group(0) for match in NAME_RE.finditer(line))
                for token in sorted(found):
                    raw = token.strip("`")
                    checked += 1
                    if not _resolves(raw):
                        unresolved.append(
                            f"{document.relative_to(ROOT).as_posix()}:{number}: {raw}"
                        )
        self.assertGreater(checked, 20, "the link check found almost no paths")
        self.assertEqual(unresolved, [], "documented paths do not exist")


if __name__ == "__main__":
    unittest.main()
