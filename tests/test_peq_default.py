"""``dx5ii peq`` prints the configs view; ``peq list`` and ``configs`` follow."""

import io
import os
import unittest
from contextlib import redirect_stderr
from unittest import mock

from dx5ii import cli


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = cli.build_parser()

    def test_peq_alone_has_no_subcommand(self):
        args = self.parser.parse_args(["peq"])
        self.assertIsNone(args.peq_command)

    def test_peq_list_is_still_accepted(self):
        args = self.parser.parse_args(["peq", "list"])
        self.assertEqual(args.peq_command, "list")

    def test_peq_accepts_json(self):
        args = self.parser.parse_args(["peq", "--json"])
        self.assertTrue(args.json)


class DefaultOutputTests(unittest.TestCase):
    def setUp(self):
        cli._WARNED.clear()

    def test_peq_alone_calls_the_configs_view(self):
        parser = cli.build_parser()
        args = parser.parse_args(["peq"])
        with mock.patch.object(cli, "_cmd_configs", return_value=0) as view:
            code = cli._cmd_peq(args)
        self.assertEqual(code, 0)
        view.assert_called_once()

    def test_peq_list_warns_and_uses_the_same_view(self):
        parser = cli.build_parser()
        args = parser.parse_args(["peq", "list"])
        err = io.StringIO()
        with mock.patch.object(cli, "_cmd_configs", return_value=0) as view, \
                redirect_stderr(err):
            code = cli._cmd_peq(args)
        self.assertEqual(code, 0)
        view.assert_called_once()
        self.assertIn("peq list will be removed in 0.1.2", err.getvalue())


class LegacyKeyTests(unittest.TestCase):
    def test_legacy_keys_are_on_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DX5II_NO_LEGACY_KEYS", None)
            self.assertTrue(cli._legacy_keys())

    def test_env_switch_turns_them_off(self):
        with mock.patch.dict(os.environ, {"DX5II_NO_LEGACY_KEYS": "1"}):
            self.assertFalse(cli._legacy_keys())


if __name__ == "__main__":
    unittest.main()
