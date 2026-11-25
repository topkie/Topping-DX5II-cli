"""Contract tests the v3 test matrix asks for: warnings, wiring, read-back."""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from dx5ii import cli


class WarningContractTests(unittest.TestCase):
    def setUp(self) -> None:
        cli._WARNED.clear()
        self.addCleanup(cli._WARNED.clear)

    def test_same_old_name_warns_once_per_process(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            cli._warn("--fast", "use 'dx5ii status --fast'")
            cli._warn("--fast", "use 'dx5ii status --fast'")
        self.assertEqual(err.getvalue().count("will be removed in 0.1.2"), 1)

    def test_quiet_environment_silences_the_warning(self) -> None:
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"DX5II_QUIET": "1"}), \
                redirect_stderr(err):
            cli._warn("--raw", "use 'dx5ii status --frames'")
        self.assertEqual(err.getvalue(), "")

    def test_two_old_names_warn_separately(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            cli._warn("--fast", "a")
            cli._warn("--raw", "b")
        self.assertEqual(err.getvalue().count("will be removed in 0.1.2"), 2)


class SnapshotWiringTests(unittest.TestCase):
    def test_frames_flag_asks_for_the_frame_log(self) -> None:
        calls: dict = {}

        class Dev:
            def snapshot(self, include_configs=True, keep_raw=False,
                         light=False) -> dict:
                calls["keep_raw"] = keep_raw
                calls["include_configs"] = include_configs
                return {"device": {}, "settings": {}, "frames": []}

        class Ctx:
            def __enter__(self):
                return Dev()

            def __exit__(self, *exc):
                return False

        args = cli.build_parser().parse_args(["status", "--frames", "--fast"])
        with mock.patch.object(cli, "_open", lambda *a, **k: Ctx()), \
                redirect_stdout(io.StringIO()):
            code = cli._read_snapshot(args)
        self.assertEqual(code, 0)
        self.assertTrue(calls["keep_raw"])
        self.assertFalse(calls["include_configs"])

    def test_plain_status_does_not_keep_frames(self) -> None:
        calls: dict = {}

        class Dev:
            def snapshot(self, include_configs=True, keep_raw=False,
                         light=False) -> dict:
                calls["keep_raw"] = keep_raw
                return {"device": {}, "settings": {}}

        class Ctx:
            def __enter__(self):
                return Dev()

            def __exit__(self, *exc):
                return False

        args = cli.build_parser().parse_args(["status", "--fast"])
        with mock.patch.object(cli, "_open", lambda *a, **k: Ctx()), \
                redirect_stdout(io.StringIO()):
            cli._read_snapshot(args)
        self.assertFalse(calls["keep_raw"])


class GlobalFlagTests(unittest.TestCase):
    def test_global_flags_survive_in_front_of_a_subcommand(self) -> None:
        parser = cli.build_parser()
        self.assertTrue(parser.parse_args(["--json", "status"]).json)
        self.assertEqual(parser.parse_args(["--path", "P", "status"]).path, "P")

    def test_subcommand_flags_do_not_reach_a_leading_position(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["status", "--json"])
        self.assertTrue(args.json)


if __name__ == "__main__":
    unittest.main()
