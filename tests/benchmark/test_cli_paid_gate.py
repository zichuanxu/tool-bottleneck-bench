from __future__ import annotations

import unittest

from bottleneckbench.cli import _parser, main


class PaidCommandGateTest(unittest.TestCase):
    def test_every_hosted_command_fails_closed(self) -> None:
        commands = (
            ["calibrate-transport", "--model-panel", "x", "--output", "x"],
            ["calibrate-concurrency", "--model-panel", "x", "--output", "x"],
            [
                "run-development",
                "--model-panel",
                "x",
                "--protocol",
                "x",
                "--plan-dir",
                "x",
                "--output-dir",
                "x",
                "--status-dir",
                "x",
            ],
            [
                "run-study",
                "--split",
                "main",
                "--model-panel",
                "x",
                "--protocol",
                "x",
                "--plan-dir",
                "x",
                "--output-dir",
                "x",
                "--status-dir",
                "x",
            ],
            [
                "run",
                "--kind",
                "capacity",
                "--plan",
                "x",
                "--output",
                "x",
                "--protocol",
                "x",
                "--model-panel",
                "x",
            ],
        )
        for command in commands:
            with self.subTest(command=command[0]):
                with self.assertRaisesRegex(ValueError, "explicit --allow-paid"):
                    main(command)
                self.assertTrue(_parser().parse_args([*command, "--allow-paid"]).allow_paid)


if __name__ == "__main__":
    unittest.main()
