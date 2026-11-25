"""Tests for the frame log behind ``status --frames``."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from dx5ii import cli
from dx5ii.device import Dx5ii
from dx5ii.formatting import print_frames, print_snapshot
from dx5ii.interpreter import decode_frame_hex

REPORT = "2233200101710c000000020010667700"


def bare_device() -> Dx5ii:
    """A Dx5ii that never opens HID: enough for the frame log."""
    dev = object.__new__(Dx5ii)
    dev.raw_frames = []
    dev.frames = []
    dev.keep_raw = False
    return dev


class DecodeTests(unittest.TestCase):
    def test_decodes_a_full_report(self) -> None:
        self.assertEqual(
            decode_frame_hex(REPORT),
            {"cmd": "0x710C", "cur": 1, "total": 1, "data": 2})

    def test_decodes_the_report_id_layout(self) -> None:
        # The transport may keep the report id in front of the frame.
        self.assertEqual(
            decode_frame_hex("00" + REPORT),
            {"cmd": "0x710C", "cur": 1, "total": 1, "data": 2})

    def test_rejects_junk(self) -> None:
        self.assertIsNone(decode_frame_hex("zz"))
        self.assertIsNone(decode_frame_hex("00112233"))


class FrameLogTests(unittest.TestCase):
    def test_both_shapes_are_recorded(self) -> None:
        dev = bare_device()
        dev._note_raw(bytes.fromhex(REPORT))
        self.assertEqual(dev.frames[0]["cmd"], "0x710C")
        self.assertGreaterEqual(dev.frames[0]["ms"], 0.0)
        self.assertEqual(dev.raw_frames[0]["hex"], REPORT)
        self.assertIn("t", dev.raw_frames[0])

    def test_unparseable_reports_only_land_in_the_legacy_list(self) -> None:
        dev = bare_device()
        dev._note_raw(b"\x00" * 4)
        self.assertEqual(dev.frames, [])
        self.assertEqual(len(dev.raw_frames), 1)


class FramesInSnapshotTests(unittest.TestCase):
    def test_parser_accepts_frames(self) -> None:
        parser = cli.build_parser()
        self.assertTrue(parser.parse_args(["status", "--frames"]).frames)
        self.assertFalse(parser.parse_args(["status"]).frames)

    def test_printer_shows_the_header_and_rows(self) -> None:
        frames = [
            {"ms": 0.0, "cmd": "0x7137", "cur": 1, "total": 3, "data": 0x200},
            {"ms": 232.9, "cmd": "0x710C", "cur": 52, "total": 52, "data": 0x5054},
        ]
        out = io.StringIO()
        with redirect_stdout(out):
            print_frames(frames)
        text = out.getvalue()
        self.assertIn("frames  2 in 0.23 s", text)
        self.assertIn("0x7137  1/3", text)
        self.assertIn("data=0x00000200", text)
        self.assertIn("232.9  0x710C 52/52", text)

    def test_printer_stays_quiet_without_frames(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            print_frames([])
        self.assertEqual(out.getvalue(), "")

    def test_snapshot_calls_the_frame_printer(self) -> None:
        snap = {
            "device": {"product": "DX5 II", "serial": "S", "interface_number": 2,
                       "firmware": "2.58"},
            "settings": {},
            "frames": [{"ms": 1.0, "cmd": "0x7130", "cur": 1, "total": 2,
                        "data": 1}],
        }
        out = io.StringIO()
        with redirect_stdout(out):
            print_snapshot(snap)
        self.assertIn("frames  1 in 0.00 s", out.getvalue())


if __name__ == "__main__":
    unittest.main()
