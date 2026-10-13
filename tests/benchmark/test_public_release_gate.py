from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.check_public_release import audit


class PublicReleaseGateTest(unittest.TestCase):
    def test_clean_snapshot_passes_and_private_path_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "README.md").write_text("public\n", encoding="utf-8")
            (root / "requirements.lock").write_text("numpy==2.2.4\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "README.md", "requirements.lock"],
                cwd=root,
                check=True,
            )
            self.assertEqual(audit(root), [])

            private = root / "results" / "quarantine" / "row.jsonl"
            private.parent.mkdir(parents=True)
            private.write_text("{}\n", encoding="utf-8")
            subprocess.run(["git", "add", "-f", str(private.relative_to(root))], cwd=root, check=True)
            self.assertEqual(audit(root)[0]["kind"], "private_path")


if __name__ == "__main__":
    unittest.main()
