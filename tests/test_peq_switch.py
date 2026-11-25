"""Tests for ``dx5ii peq switch``, the named form of ``--set-config``."""

from __future__ import annotations

import io
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout

from dx5ii.cli import _peq_switch

NAMES = {1: "Dynamic", 2: "IER-M7.Harman"}


def snapshot(current: int) -> dict:
    return {
        "configs": {
            "playing": {
                "source": "local",
                "slot_index": current,
                "slot_name": NAMES[current],
                "source_id_hash": 0,
                "config_hash": 0,
            },
            "enabled": True,
            "stored_index": current,
            "stored_name": NAMES[current],
            "count": 2,
            "slots": [
                {"index": 1, "name": NAMES[1], "stored_current": current == 1},
                {"index": 2, "name": NAMES[2], "stored_current": current == 2},
            ],
        },
        "settings": {
            "output": "hp_balanced",
            "output_raw": 4,
            "input": "usb",
            "input_raw": 0,
        },
        "pushed": {"peq_enabled": True},
    }


class FakeDev:
    """Records the switch and answers snapshots from its own state."""

    def __init__(self, current: int = 1) -> None:
        self.current = current
        self.calls: list[int] = []

    def set_config_index(self, index: int, settle_ms: int = 0) -> int:
        self.calls.append(index)
        self.current = index
        return index

    def snapshot(self, include_configs: bool = True, keep_raw: bool = False,
                 light: bool = False) -> dict:
        return snapshot(self.current)


def run(slot: str, current: int = 1, as_json: bool = False):
    dev = FakeDev(current)
    args = Namespace(slot=slot)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = _peq_switch(dev, args, snapshot(current), as_json)
    return code, out.getvalue(), err.getvalue(), dev


class SwitchTests(unittest.TestCase):
    def test_switches_by_name(self) -> None:
        code, out, _, dev = run("IER-M7.Harman")
        self.assertEqual(code, 0)
        self.assertEqual(dev.calls, [2])
        self.assertIn("config 1 -> 2  (changed)", out)
        self.assertIn("stored=2 (IER-M7.Harman)", out)

    def test_switches_by_index(self) -> None:
        code, _, _, dev = run("2")
        self.assertEqual(code, 0)
        self.assertEqual(dev.calls, [2])

    def test_same_slot_sends_nothing(self) -> None:
        code, out, _, dev = run("1")
        self.assertEqual(code, 0)
        self.assertEqual(dev.calls, [])
        self.assertIn("(already set)", out)

    def test_unknown_name_is_refused_before_writing(self) -> None:
        code, _, err, dev = run("Nope")
        self.assertEqual(code, 2)
        self.assertEqual(dev.calls, [])
        self.assertIn("no EQ config named 'Nope'", err)

    def test_json_reports_the_same_action(self) -> None:
        code, out, _, dev = run("IER-M7.Harman", as_json=True)
        self.assertEqual(code, 0)
        self.assertEqual(dev.calls, [2])
        self.assertIn('"what": "config"', out)
        self.assertIn('"to": 2', out)


if __name__ == "__main__":
    unittest.main()
