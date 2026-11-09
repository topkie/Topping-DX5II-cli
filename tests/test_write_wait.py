"""Tests for ending a write on the frame the device pushes back."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dx5ii.constants import (  # noqa: E402
    CMD_CURRENT_CONFIG,
    CMD_DELETE_CONFIG,
    CMD_RENAME_CONFIG_INDEX,
    CMD_RENAME_CONFIG_NAME,
    CMD_SWITCH_MCU_CONFIG,
    PT_READ,
    PT_WRITE,
)
from dx5ii.device import Dx5ii  # noqa: E402
from dx5ii.peq import delete_preset, rename_preset  # noqa: E402
from dx5ii.protocol import build_frame  # noqa: E402

TELEMETRY = 0x7131


def frame(cmd: int, data: int = 0, total: int = 1, cur: int = 1) -> bytes:
    return build_frame(cmd, data, PT_READ, total, cur)


class FakeHID:
    """Replays canned reports; an empty read models an idle device."""

    def __init__(self, reports):
        self.reports = list(reports)
        self.reads = 0

    def read(self, length, timeout_ms=0):
        self.reads += 1
        if not self.reports:
            time.sleep(0.002)
            return b""
        return self.reports.pop(0)

    def write(self, data):
        return len(data)


def make_device(reports) -> Dx5ii:
    dev = object.__new__(Dx5ii)
    dev._hid = None
    dev._dev = FakeHID(reports)
    dev.raw_frames = []
    dev.keep_raw = False
    dev.last_read_state = None
    dev.info = {}
    return dev


class WaitForFrameTests(unittest.TestCase):
    def test_returns_true_on_the_matching_frame(self):
        dev = make_device([frame(CMD_CURRENT_CONFIG, 9),
                           frame(CMD_CURRENT_CONFIG, 3)])
        self.assertTrue(dev.wait_for_frame(
            CMD_CURRENT_CONFIG, lambda f: f["data"] == 3, timeout_ms=200))

    def test_ignores_a_value_that_does_not_match(self):
        dev = make_device([])
        started = time.monotonic()
        ok = dev.wait_for_frame(
            CMD_CURRENT_CONFIG, lambda f: f["data"] == 3, timeout_ms=60)
        self.assertFalse(ok)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_any_frame_of_the_command_is_enough_without_a_matcher(self):
        dev = make_device([frame(CMD_CURRENT_CONFIG, 7)])
        self.assertTrue(dev.wait_for_frame(CMD_CURRENT_CONFIG, timeout_ms=200))

    def test_telemetry_does_not_reset_the_idle_timer(self):
        """The device streams ~490 telemetry frames per second.

        If those counted as progress a stalled reply would never time out, the
        same trap ReadSession had.  Fifty telemetry reports with a 60 ms idle
        budget must give up in about 60 ms, not run to the 400 ms ceiling.
        """
        reports = [frame(TELEMETRY, i, total=8, cur=i % 8) for i in range(50)]
        dev = make_device(reports)
        started = time.monotonic()
        ok = dev.wait_for_frame(CMD_CURRENT_CONFIG, timeout_ms=400, idle_ms=60)
        elapsed = time.monotonic() - started
        self.assertFalse(ok)
        self.assertLess(elapsed, 0.25)

    def test_telemetry_between_matches_does_not_hide_the_match(self):
        dev = make_device([frame(TELEMETRY, 1, total=8, cur=0),
                           frame(TELEMETRY, 2, total=8, cur=1),
                           frame(CMD_CURRENT_CONFIG, 4)])
        self.assertTrue(dev.wait_for_frame(
            CMD_CURRENT_CONFIG, lambda f: f["data"] == 4, timeout_ms=200))


class RecordingDev:
    """Records the order of writes and whether a confirmation was awaited."""

    def __init__(self, confirm: bool = True) -> None:
        self.sent: list[tuple[int, int]] = []
        self.waits: list[tuple[int, int]] = []
        self.confirm = confirm

    def send(self, cmd, data=0, protocol=PT_READ, total=1, cur=1) -> None:
        self.sent.append((cmd, data))

    def wait_for_frame(self, cmd, match=None, timeout_ms=400, idle_ms=None):
        self.waits.append((cmd, timeout_ms))
        return self.confirm


class DeletePresetTests(unittest.TestCase):
    def test_delete_then_switch_each_wait_for_the_announcement(self):
        dev = RecordingDev()
        delete_preset(dev, 2, 0)
        self.assertEqual([cmd for cmd, _ in dev.sent],
                         [CMD_DELETE_CONFIG, CMD_SWITCH_MCU_CONFIG])
        self.assertEqual([cmd for cmd, _ in dev.waits],
                         [CMD_CURRENT_CONFIG, CMD_CURRENT_CONFIG])

    def test_quiet_device_still_reacts(self):
        dev = RecordingDev(confirm=False)
        started = time.monotonic()
        delete_preset(dev, 2, 0)
        self.assertGreaterEqual(time.monotonic() - started, 0.1)
        self.assertEqual(len(dev.sent), 2)


class RenamePresetTests(unittest.TestCase):
    def test_rename_index_then_four_name_frames(self):
        dev = OffsetDevice()
        rename_preset(dev, 3, "SPEAKER")
        self.assertEqual(dev.sent[0][0], CMD_RENAME_CONFIG_INDEX)
        self.assertEqual(len(dev.sent), 5)
        name_totals = [t for (cmd, _), t in
                       zip(dev.sent, dev.totals) if cmd == CMD_RENAME_CONFIG_NAME]
        self.assertEqual(name_totals, [4, 4, 4, 4])
        self.assertEqual(dev.waits[0][0], CMD_CURRENT_CONFIG)


class OffsetDevice(RecordingDev):
    def send(self, cmd, data=0, protocol=PT_READ, total=1, cur=1) -> None:
        self.sent.append((cmd, data))
        self.totals = getattr(self, "totals", [])
        self.totals.append(total)


if __name__ == "__main__":
    unittest.main()
