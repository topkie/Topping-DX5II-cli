"""Tests that every command and field is reachable from --help."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dx5ii.cli import build_parser  # noqa: E402
from dx5ii.fields import FIELD_ACTIONS, FIELD_SPECS  # noqa: E402


def options(parser) -> list[str]:
    found: list[str] = []
    for action in parser._actions:
        found.extend(action.option_strings)
    return found


def walk(parser, path: str = "dx5ii"):
    yield path, parser
    for action in parser._actions:
        if action.__class__.__name__ != "_SubParsersAction":
            continue
        for name, sub in action.choices.items():
            yield from walk(sub, f"{path} {name}")


class HelpSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser()
        self.commands = list(walk(self.parser))

    def test_every_command_can_trace(self):
        missing = [name for name, parser in self.commands
                   if name != "dx5ii peq" and "--trace" not in options(parser)]
        self.assertEqual(missing, [], "commands without --trace")

    def test_every_command_accepts_an_explicit_path(self):
        missing = [name for name, parser in self.commands
                   if name != "dx5ii peq" and "--path" not in options(parser)]
        self.assertEqual(missing, [], "commands without --path")

    def test_every_field_is_listed_in_the_epilog(self):
        epilog = self.parser.epilog
        self.assertEqual([f for f in FIELD_SPECS if f not in epilog], [])

    def test_every_action_is_listed_in_the_epilog(self):
        epilog = self.parser.epilog
        self.assertEqual([f for f in FIELD_ACTIONS if f not in epilog], [])

    def test_the_peq_help_points_at_the_on_off_field(self):
        peq = dict(self.commands)["dx5ii peq"]
        text = peq.description or ""
        self.assertIn("set peq on", text)


if __name__ == "__main__":
    unittest.main()
