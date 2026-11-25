"""Tests for the frame log behind ``status --frames``."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from dx5ii import cli
from dx5ii.device import Dx5ii
from dx5ii.formatting import print_snapshot


def bare_device() -> Dx5ii:
    """A Dx5ii that never opens HID: enough for the frame log."""
    dev = object.__new__(Dx5ii)
    dev.raw_frames = []
    dev.keep_raw = False
    return dev


class FrameLogTests(unittest.TestCase):
    def test_timestamps_are_relative_milliseconds(self) -> None:
        dev = bare_device()
        dev._note_raw(bytes.fromhex("2233200101" + "20" * 12))
        dev._note_raw(bytes.fromhex("2233200101" + "20" * 12))
        stamps = [item["t_ms"] for item in dev.raw_frames]
        self.assertEqual(len(stamps), 2)
        self.assertGreaterEqual(stamps[0], 0.0)
        self.assertGreaterEqual(stamps[1], stamps[0])

    def test_hex_is_the_whole_report(self) -> None:
        dev = bare_device()
        raw = bytes.fromhex("2233200101710c00000000")
        dev._note_raw(raw)
        self.assertEqual(dev.raw_frames[0]["hex"], raw.hex())


class FramesInSnapshotTests(unittest.TestCase):
    def test_parser_accepts_frames(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["status", "--frames"])
        self.assertTrue(args.frames)
        self.assertFalse(parser.parse_args(["status"]).frames)

    def test_printer_shows_the_log(self) -> None:
        snap = {
            "device": {"product": "DX5 II", "serial": "S", "interface_number": 2,
                       "firmware": "2.58"},
            "settings": {},
            "frames": [
                {"t_ms": 12.3, "hex": "2233200101710c00000000"},
                {"t_ms": 40.0, "hex": "22" * 20},
            ],
        }
        out = io.StringIO()
        with redirect_stdout(out):
            print_snapshot(snap)
        text = out.getvalue()
        self.assertIn("[frames]", text)
        self.assertIn("12.3 ms", text)
        self.assertIn("2233200101710c00000000", text)
        self.assertIn("22" * 17 + "...", text)

    def test_printer_stays_quiet_without_frames(self) -> None:
        snap = {"device": {}, "settings": {}}
        out = io.StringIO()
        with redirect_stdout(out):
            print_snapshot(snap)
        self.assertNotIn("[frames]", out.getvalue())


if __name__ == "__main__":
    unittest.main()
