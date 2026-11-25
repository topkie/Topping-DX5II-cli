"""Tests for ``peq import``, ``peq copy`` and ``peq export``."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from dx5ii import cli

APO = "Preamp: -6.0 dB\nFilter 1: ON LS Fc 105 Hz Gain +0.6 dB Q 0.70\n"

BAND = {"enabled": True, "type": "LS", "gain_db": 0.6, "freq_hz": 105, "q": 0.7}


def preset(index: int, name: str) -> dict:
    return {"index": index, "name": name, "preamp_left": -6.0,
            "preamp_right": -6.0, "bands_left": [dict(BAND)],
            "bands_right": [dict(BAND)]}


def presets_of(*names: str) -> dict:
    rows = [preset(i + 1, name) for i, name in enumerate(names)]
    return {"stored_index": 1, "stored_name": names[0], "count": len(rows),
            "presets": rows, "slots": []}


class FakeDev:
    info = {"product": "DX5 II"}

    def __init__(self) -> None:
        self.current = 1
        self.sent: list = []

    def current_config_index(self) -> int:
        return self.current

    def send(self, cmd: int, data: int, protocol: int) -> None:
        self.sent.append((cmd, data))


class TransferTests(unittest.TestCase):
    def setUp(self) -> None:
        handle = tempfile.TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        self.dir = handle.name
        self.apo = os.path.join(self.dir, "harman.txt")
        with io.open(self.apo, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(APO)
        self.dev = FakeDev()
        self.presets = presets_of("IER-M7.Harman")
        self.created: list = []

    def _create(self, dev, name, left, right, head=None):
        self.created.append((name, len(left), head))
        dev.current = 2
        rows = self.presets["presets"]
        rows.append(preset(len(rows) + 1, name))
        self.presets["count"] = len(rows)
        return 2

    def _args(self, **kwargs) -> Namespace:
        base = {"json": False, "file": self.apo, "name": None, "source": None,
                "preset": None, "no_switch": False}
        base.update(kwargs)
        return Namespace(**base)

    def _run(self, handler, args) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            with mock.patch.object(cli, "create_preset", self._create), \
                    mock.patch.object(cli, "read_presets",
                                      lambda dev, settle_ms=1600: self.presets):
                code = handler(self.dev, args, self.presets, [], args.json)
        return code, out.getvalue()

    def test_import_names_the_preset_after_the_file(self) -> None:
        code, out = self._run(cli._peq_import, self._args())
        self.assertEqual(code, 0)
        self.assertEqual(self.created[0][0], "harman.txt")
        self.assertIn("now playing slot 2", out)

    def test_import_writes_the_parsed_preamp(self) -> None:
        self._run(cli._peq_import, self._args(name="ZIMP"))
        name, bands, head = self.created[0]
        self.assertEqual(name, "ZIMP")
        self.assertEqual(bands, 1)
        self.assertIsNotNone(head)
        self.assertEqual(head[1], head[3])

    def test_import_refuses_an_existing_name(self) -> None:
        code, _ = self._run(cli._peq_import, self._args(name="IER-M7.Harman"))
        self.assertEqual(code, cli.ExitCode.USAGE)
        self.assertEqual(self.created, [])

    def test_import_reports_a_missing_file(self) -> None:
        code, _ = self._run(
            cli._peq_import, self._args(name="ZIMP", file=os.path.join(self.dir, "nope.txt")))
        self.assertEqual(code, cli.ExitCode.USAGE)
        self.assertEqual(self.created, [])

    def test_copy_refuses_an_unknown_source(self) -> None:
        code, _ = self._run(cli._peq_copy, self._args(name="ZCP", source="nope"))
        self.assertEqual(code, cli.ExitCode.DEVICE)
        self.assertEqual(self.created, [])

    def test_copy_carries_the_source_bands(self) -> None:
        code, _ = self._run(
            cli._peq_copy, self._args(name="ZCP", source="IER-M7.Harman"))
        self.assertEqual(code, 0)
        self.assertEqual(self.created[0][1], 1)

    def test_no_switch_goes_back(self) -> None:
        code, out = self._run(
            cli._peq_import, self._args(name="ZIMP", no_switch=True))
        self.assertEqual(code, 0)
        self.assertEqual(self.dev.sent, [(cli.CMD_SWITCH_MCU_CONFIG, 1)])
        self.assertIn("went back to slot 1", out)

    def test_export_writes_apo_text(self) -> None:
        target = os.path.join(self.dir, "out.txt")
        code, out = self._run(
            cli._peq_export, self._args(preset="IER-M7.Harman", file=target))
        self.assertEqual(code, 0)
        with io.open(target, encoding="utf-8") as stream:
            text = stream.read()
        self.assertIn("Preamp: -6.0 dB", text)
        self.assertIn("Filter 1: ON LS Fc 105 Hz", text)
        self.assertIn("exported 'IER-M7.Harman'", out)

    def test_export_refuses_an_unknown_preset(self) -> None:
        target = os.path.join(self.dir, "out.txt")
        code, _ = self._run(
            cli._peq_export, self._args(preset="nope", file=target))
        self.assertEqual(code, cli.ExitCode.DEVICE)
        self.assertFalse(os.path.exists(target))


if __name__ == "__main__":
    unittest.main()
