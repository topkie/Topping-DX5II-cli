"""Exit-code contract: usage errors are 1, device errors stay 2."""

import io
import unittest
from contextlib import redirect_stderr

from dx5ii.cli import ExitCode, _fail, build_parser


class ParserTests(unittest.TestCase):
    def test_missing_argument_exits_one(self):
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(io.StringIO()):
            build_parser().parse_args(["raw"])
        self.assertEqual(ctx.exception.code, ExitCode.USAGE)

    def test_unknown_subcommand_exits_one(self):
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(io.StringIO()):
            build_parser().parse_args(["nosuchcommand"])
        self.assertEqual(ctx.exception.code, ExitCode.USAGE)

    def test_bad_choice_exits_one(self):
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(io.StringIO()):
            build_parser().parse_args(["raw", "0x710C", "--pt", "nonsense"])
        self.assertEqual(ctx.exception.code, ExitCode.USAGE)


class FailTests(unittest.TestCase):
    def test_default_is_device_error(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(_fail("boom"), ExitCode.DEVICE)

    def test_usage_code_can_be_asked_for(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(_fail("boom", ExitCode.USAGE), ExitCode.USAGE)

    def test_values_match_the_published_contract(self):
        self.assertEqual(
            (ExitCode.OK, ExitCode.USAGE, ExitCode.DEVICE,
             ExitCode.MISMATCH, ExitCode.TIMEOUT),
            (0, 1, 2, 3, 4),
        )


if __name__ == "__main__":
    unittest.main()
