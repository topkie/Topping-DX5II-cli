"""Tests for the timeout path: a silent device must exit with code 4."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from dx5ii import cli
from dx5ii.device import Dx5ii
from dx5ii.errors import Dx5iiError, Dx5iiTimeout


class SilentHID:
    def read(self, size: int, timeout_ms: int) -> bytes:
        return b""


def silent_device() -> Dx5ii:
    dev = object.__new__(Dx5ii)
    dev._dev = SilentHID()
    dev._trace_file = None
    dev.raw_frames = []
    dev.frames = []
    dev.keep_raw = False
    return dev


class TimeoutTests(unittest.TestCase):
    def test_collect_raises_when_nothing_arrives(self) -> None:
        dev = silent_device()
        with self.assertRaises(Dx5iiTimeout):
            dev._collect({(0x710C, 52): 52}, timeout_ms=60)

    def test_timeout_is_a_dx5ii_error(self) -> None:
        # Handlers that catch the base class still catch the timeout; the
        # dedicated handlers must come first to report exit code 4.
        self.assertTrue(issubclass(Dx5iiTimeout, Dx5iiError))

    def test_exit_code_four_exists(self) -> None:
        self.assertEqual(int(cli.ExitCode.TIMEOUT), 4)

    def test_main_reports_four_for_a_timeout(self) -> None:
        original = cli._run_subcommand

        def boom(args):
            raise Dx5iiTimeout("the device sent no reply for 1500 ms")

        cli._run_subcommand = boom
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(["status"])
        finally:
            cli._run_subcommand = original
        self.assertEqual(code, 4)
        self.assertIn("no reply", err.getvalue())


if __name__ == "__main__":
    unittest.main()
