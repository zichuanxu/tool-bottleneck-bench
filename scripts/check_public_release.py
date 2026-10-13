#!/usr/bin/env python3
"""Fail closed when a Git snapshot contains non-public BottleneckBench material."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PARTS = {
    "receipts",
    "failed-receipts",
    "trajectories",
    "quarantine",
    "incident",
}
PRIVATE_NAMES = {"spend-ledger.json"}
TEXT_SUFFIXES = {
    ".bib",
    ".cff",
    ".cfg",
    ".csv",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".sty",
    ".tex",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
FORBIDDEN_TEXT = (
    ("removed_model", re.compile(r"\b" + "sc" + "out" + r"\b", re.IGNORECASE)),
    (
        "excluded_configuration",
        re.compile(r"gpt[-_ ]?5\.6[-_ ]?luna[-_ ]?pro", re.IGNORECASE),
    ),
    (
        "ai_use_statement",
        re.compile(
            r"(?:AI|generative AI)\s+(?:use\s+)?(?:statement|disclosure)",
            re.IGNORECASE,
        ),
    ),
    (
        "absolute_local_path",
        re.compile(r"/(?:" + "Users|var/folders" + r")/"),
    ),
)
def _git(root: Path, *args: str) -> list[str]:
    completed = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    )
    return [item for item in completed.stdout.split("\0") if item]


def audit(root: Path) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not (root / ".git").exists():
        return [{"kind": "git", "path": ".", "detail": "not a Git repository"}]

    for path in _git(root, "ls-files", "-ci", "--exclude-standard", "-z"):
        issues.append({"kind": "tracked_ignored", "path": path, "detail": ""})

    for relative in _git(root, "ls-files", "-z"):
        path = Path(relative)
        parts = {part.lower() for part in path.parts}
        name = path.name.lower()
        if parts & PRIVATE_PARTS or name in PRIVATE_NAMES:
            issues.append({"kind": "private_path", "path": relative, "detail": ""})
            continue
        if name.endswith(".pending.json"):
            issues.append({"kind": "pending_artifact", "path": relative, "detail": ""})
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = (root / path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            issues.append({"kind": "non_utf8_text", "path": relative, "detail": ""})
            continue
        for kind, pattern in FORBIDDEN_TEXT:
            match = pattern.search(text)
            if match:
                issues.append(
                    {
                        "kind": kind,
                        "path": relative,
                        "detail": f"line {text.count(chr(10), 0, match.start()) + 1}",
                    }
                )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    issues = audit(args.root.resolve())
    print(json.dumps({"passed": not issues, "issues": issues}, indent=2, sort_keys=True))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
