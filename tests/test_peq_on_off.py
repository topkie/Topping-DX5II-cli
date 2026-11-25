"""Tests for ``dx5ii peq on`` / ``peq off`` and the removed factory-reset field."""

from __future__ import annotations

import io
import unittest
from argparse import Namespace
from contextlib import redirect_stdout

from dx5ii import cli
from dx5ii.constants import CMD_PEQ_STATE
from dx5ii.errors import Dx5iiError
from dx5ii.fields import FIELD_ACTIONS, encode_field


class FakeDev:
    def __init__(self, before: bool, after: bool) -> None:
        self.values = [before, after]
        self.sent: list = []

    def snapshot(self, include_configs: bool = True, keep_raw: bool = False,
                 light: bool = False) -> dict:
        value = self.values.pop(0) if len(self.values) > 1 else self.values[0]
        return {"pushed": {"peq_enabled": value}}

    def send(self, cmd: int, data: int, protocol: int, total: int, cur: int) -> None:
        self.sent.append((cmd, data))

    def wait_for_frame(self, cmd, match, timeout_ms=0) -> bool:
        return True


class PeqPowerTests(unittest.TestCase):
    def _run(self, before: bool, after: bool, wanted: bool) -> tuple[int, str]:
        dev = FakeDev(before, after)
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli._peq_power(dev, Namespace(), wanted, False)
        self.dev = dev
        return code, out.getvalue()

    def test_already_on_writes_nothing(self) -> None:
        code, out = self._run(True, True, True)
        self.assertEqual(code, 0)
        self.assertEqual(self.dev.sent, [])
        self.assertIn("peq already on", out)

    def test_turning_on_sends_the_switch(self) -> None:
        code, out = self._run(False, True, True)
        self.assertEqual(code, 0)
        self.assertEqual(self.dev.sent, [(CMD_PEQ_STATE, 1)])
        self.assertIn("peq on", out)

    def test_turning_off_sends_zero(self) -> None:
        code, _ = self._run(True, False, False)
        self.assertEqual(code, 0)
        self.assertEqual(self.dev.sent, [(CMD_PEQ_STATE, 0)])

    def test_silent_device_is_reported_as_a_mismatch(self) -> None:
        code, out = self._run(False, False, True)
        self.assertEqual(code, cli.ExitCode.MISMATCH)
        self.assertIn("device still reports off", out)

    def test_json_shape(self) -> None:
        dev = FakeDev(False, True)
        out = io.StringIO()
        with redirect_stdout(out):
            cli._peq_power(dev, Namespace(), True, True)
        self.assertIn('"changed": true', out.getvalue())
        self.assertIn('"confirmed": true', out.getvalue())


class FactoryResetRemovedTests(unittest.TestCase):
    def test_field_actions_no_longer_contain_factory_reset(self) -> None:
        self.assertNotIn("factory-reset", FIELD_ACTIONS)

    def test_encoding_rejects_the_old_name(self) -> None:
        with self.assertRaises(Dx5iiError):
            encode_field("factory-reset", "1", {})


if __name__ == "__main__":
    unittest.main()
