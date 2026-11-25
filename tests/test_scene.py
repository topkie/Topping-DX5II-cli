"""Tests for the C1/C2 scenes: recall waits for the pushed state."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dx5ii.cli import _setting_text, _settings_diff  # noqa: E402
from dx5ii.constants import (  # noqa: E402
    CMD_CALL_C1,
    CMD_CALL_C2,
    CMD_SAVE_C1,
    CMD_SAVE_C2,
    PT_READ,
)
from dx5ii.device import Dx5ii  # noqa: E402
from dx5ii.protocol import build_frame, parse_frame  # noqa: E402

SETTINGS_TOTAL = 52


def settings_reports() -> list[bytes]:
    """The whole settings block, word ``i`` in frame ``i``."""
    return [build_frame(0x710C, word, PT_READ, SETTINGS_TOTAL, index)
            for index, word in enumerate(range(SETTINGS_TOTAL))]


class FakeHID:
    """Replays canned reports and records what the CLI wrote."""

    def __init__(self, reports):
        self.reports = list(reports)
        self.written = []

    def read(self, length, timeout_ms=0):
        if not self.reports:
            time.sleep(0.002)
            return b""
        return self.reports.pop(0)

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)


def make_device(reports) -> Dx5ii:
    dev = object.__new__(Dx5ii)
    dev._hid = None
    dev._dev = FakeHID(reports)
    dev.raw_frames = []
    dev.frames = []
    dev.keep_raw = False
    dev.opened_at = time.monotonic()
    dev.last_read_state = None
    dev._trace_file = None
    return dev


def sent_commands(dev) -> list[tuple[int, int]]:
    """``(cmd, data)`` of every frame the device wrote, in order."""
    out = []
    for report in dev._dev.written:
        frame = parse_frame(report[1:])
        if frame is not None:
            out.append((frame["cmd"], frame["data"]))
    return out


class RecallTests(unittest.TestCase):
    def test_recall_reads_the_state_after_the_status_frames(self):
        reports = [build_frame(0x7102, 0x50, PT_READ, 1, 1)] + settings_reports()
        dev = make_device(reports)
        words, reacted = dev.recall_scene("c1", wait_ms=200)
        self.assertTrue(reacted)
        self.assertEqual(words, list(range(SETTINGS_TOTAL)))

    def test_recall_c1_uses_the_first_scene_command(self):
        dev = make_device(settings_reports())
        dev.recall_scene("c1", wait_ms=200)
        self.assertIn((CMD_CALL_C1, 1), sent_commands(dev))

    def test_recall_c2_uses_the_second_scene_command(self):
        dev = make_device(settings_reports())
        dev.recall_scene("c2", wait_ms=200)
        self.assertIn((CMD_CALL_C2, 1), sent_commands(dev))

    def test_silent_device_reports_no_reaction(self):
        dev = make_device([])
        words, reacted = dev.recall_scene("c1", wait_ms=60)
        self.assertFalse(reacted)
        self.assertEqual(words, [])


class SaveTests(unittest.TestCase):
    def test_any_reply_counts_as_acknowledgement(self):
        dev = make_device([build_frame(0x1206, 1, PT_READ, 1, 1)])
        self.assertTrue(dev.save_scene("c1", wait_ms=200))

    def test_save_c1_and_c2_use_their_own_commands(self):
        answer = [build_frame(0x1206, 1, PT_READ, 1, 1)]
        first = make_device(answer)
        first.save_scene("c1", wait_ms=200)
        second = make_device(answer)
        second.save_scene("c2", wait_ms=200)
        self.assertEqual(sent_commands(first), [(CMD_SAVE_C1, 1)])
        self.assertEqual(sent_commands(second), [(CMD_SAVE_C2, 1)])

    def test_silent_device_is_not_acknowledged(self):
        dev = make_device([])
        self.assertFalse(dev.save_scene("c2", wait_ms=60))

    def test_telemetry_alone_is_not_an_answer(self):
        dev = make_device([build_frame(0x7131, 0, PT_READ, 8, 0)])
        self.assertFalse(dev.save_scene("c1", wait_ms=60))


class SettingsDiffTests(unittest.TestCase):
    def test_reports_only_what_changed(self):
        changed = _settings_diff(
            {"volume_db": -30.0, "brightness": "low", "muted": False},
            {"volume_db": -40.0, "brightness": "low", "muted": False})
        self.assertEqual(changed, [
            {"field": "volume_db", "before": -30.0, "after": -40.0}])

    def test_no_change_is_an_empty_list(self):
        same = {"volume_db": -40.0}
        self.assertEqual(_settings_diff(same, dict(same)), [])

    def test_keys_present_on_one_side_still_appear(self):
        changed = _settings_diff({"a": 1}, {"a": 1, "b": 2})
        self.assertEqual(changed, [{"field": "b", "before": None, "after": 2}])

    def test_raw_companion_is_not_reported_twice(self):
        changed = _settings_diff(
            {"volume_db": -30.0, "volume_raw": 60},
            {"volume_db": -40.0, "volume_raw": 80})
        self.assertEqual([item["field"] for item in changed], ["volume_db"])


class SettingTextTests(unittest.TestCase):
    def test_booleans_read_as_yes_and_no(self):
        self.assertEqual(_setting_text(True), "yes")
        self.assertEqual(_setting_text(False), "no")

    def test_missing_values_use_a_dash(self):
        self.assertEqual(_setting_text(None), "-")

    def test_numbers_and_names_pass_through(self):
        self.assertEqual(_setting_text(-40.0), "-40.0")
        self.assertEqual(_setting_text("hp_balanced"), "hp_balanced")


if __name__ == "__main__":
    unittest.main()
