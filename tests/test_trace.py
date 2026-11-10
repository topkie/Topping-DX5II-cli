"""Tests for the JSONL frame trace."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dx5ii.constants import CMD_GET_SETTINGS, PT_READ  # noqa: E402
from dx5ii.device import Dx5ii  # noqa: E402
from dx5ii.protocol import build_frame  # noqa: E402


class FakeHID:
    def __init__(self, reports=()):
        self.reports = list(reports)
        self.written: list[bytes] = []

    def read(self, length, timeout_ms=0):
        return self.reports.pop(0) if self.reports else b""

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)


def make_device(reports=()) -> Dx5ii:
    dev = object.__new__(Dx5ii)
    dev._hid = None
    dev._dev = FakeHID(reports)
    dev.raw_frames = []
    dev.keep_raw = False
    dev.last_read_state = None
    dev.info = {}
    dev._trace_file = io.StringIO()
    return dev


def lines(dev) -> list[dict]:
    return [json.loads(line) for line in dev._trace_file.getvalue().splitlines()]


class TraceTests(unittest.TestCase):
    def test_send_records_the_written_report(self):
        dev = make_device()
        dev.send(CMD_GET_SETTINGS, 0, PT_READ, 1, 1)

        [entry] = lines(dev)
        self.assertEqual(entry["dir"], "tx")
        payload = bytes.fromhex(entry["hex"])
        self.assertEqual(len(payload), 17)
        self.assertEqual(payload[0], 0x00)
        self.assertEqual(payload[1:3], b"\x22\x33")
        self.assertIsInstance(entry["t"], (int, float))

    def test_parse_records_the_received_report(self):
        report = build_frame(CMD_GET_SETTINGS, 7, PT_READ, 1, 1)
        dev = make_device()

        dev._parse(report)

        [entry] = lines(dev)
        self.assertEqual(entry["dir"], "rx")
        self.assertEqual(bytes.fromhex(entry["hex"]), report)

    def test_empty_report_is_not_recorded(self):
        dev = make_device()
        self.assertIsNone(dev._parse(b"\x00" * 64))
        self.assertEqual(lines(dev), [])

    def test_read_for_records_each_reply(self):
        report = build_frame(CMD_GET_SETTINGS, 1, PT_READ, 1, 1)
        dev = make_device([report])
        dev.read_for(60)

        self.assertEqual([entry["dir"] for entry in lines(dev)], ["rx"])

    def test_read_frame_records_the_reply(self):
        report = build_frame(CMD_GET_SETTINGS, 2, PT_READ, 1, 1)
        dev = make_device([report])

        dev.read_frame(10)

        [entry] = lines(dev)
        self.assertEqual(entry["dir"], "rx")

    def test_without_a_trace_file_nothing_breaks(self):
        dev = make_device()
        dev._trace_file = None
        dev.send(CMD_GET_SETTINGS, 0, PT_READ, 1, 1)
        dev._parse(build_frame(CMD_GET_SETTINGS, 0, PT_READ, 1, 1))


if __name__ == "__main__":
    unittest.main()
