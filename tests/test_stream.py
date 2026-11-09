"""Tests for the multi-frame collector state machine."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dx5ii.stream import StreamCollector  # noqa: E402


def frame(cmd: int, data: int = 0, total: int = 1, index: int = 0) -> dict:
    return {"cmd": cmd, "data": data, "total": total, "index": index,
            "protocol": 0x20}


def run(cmd: int, count: int, total: int, start: int = 0) -> list[dict]:
    return [frame(cmd, 1000 + i, total, i + start) for i in range(count)]


class RunTests(unittest.TestCase):
    def test_complete_at_last_frame(self) -> None:
        c = StreamCollector({(0x710C, 52): 1})
        for f in run(0x710C, 51, 52):
            c.feed(f)
        self.assertFalse(c.is_complete())
        self.assertTrue(c.feed(frame(0x710C, 99, 52, 51)))
        self.assertTrue(c.is_complete())
        self.assertEqual(c.values((0x710C, 52))[0][51], 99)
        self.assertEqual(len(c.values((0x710C, 52))[0]), 52)

    def test_single_frame_run_index_one(self) -> None:
        c = StreamCollector({(0x710F, 1): 1})
        self.assertTrue(c.feed(frame(0x710F, 7, 1, 1)))
        self.assertEqual(c.values((0x710F, 1)), [[7]])

    def test_single_frame_run_index_zero(self) -> None:
        c = StreamCollector({(0x7132, 1): 1})
        self.assertTrue(c.feed(frame(0x7132, 1, 1, 0)))

    def test_one_based_firmware(self) -> None:
        c = StreamCollector({(0x7137, 3): 1})
        for i, data in enumerate([1, 2, 3], start=1):
            c.feed(frame(0x7137, data, 3, i))
        self.assertTrue(c.is_complete())
        self.assertEqual(c.values((0x7137, 3)), [[1, 2, 3]])

    def test_gap_drops_the_partial_run(self) -> None:
        c = StreamCollector({(0x710C, 52): 1})
        c.feed_all(run(0x710C, 3, 52))          # 0, 1, 2
        c.feed(frame(0x710C, 9, 52, 5))         # 4 missing -> reset
        self.assertFalse(c.pending((0x710C, 52)))
        c.feed_all(run(0x710C, 52, 52))         # a clean run
        self.assertTrue(c.is_complete())
        self.assertEqual(len(c.values((0x710C, 52))), 1)

    def test_repeated_slot_resets(self) -> None:
        c = StreamCollector({(0x710C, 52): 1})
        c.feed(frame(0x710C, 1, 52, 0))
        c.feed(frame(0x710C, 2, 52, 1))
        c.feed(frame(0x710C, 3, 52, 1))         # duplicate index 1
        self.assertFalse(c.pending((0x710C, 52)))
        c.feed_all(run(0x710C, 52, 52))
        self.assertTrue(c.is_complete())

    def test_ignores_unknown_shapes(self) -> None:
        c = StreamCollector({(0x710C, 52): 1})
        self.assertFalse(c.feed(frame(0x7131, 1, 8, 0)))
        self.assertFalse(c.has_started())
        self.assertEqual(c.frames, [])


class BatchTests(unittest.TestCase):
    def test_counts_multiple_runs(self) -> None:
        c = StreamCollector({(0x1106, 78): 3})
        c.feed_all(run(0x1106, 78, 78))
        c.feed_all(run(0x1106, 78, 78))
        self.assertFalse(c.is_complete())
        self.assertEqual(c.complete_count((0x1106, 78)), 2)
        c.feed_all(run(0x1106, 78, 78))
        self.assertTrue(c.is_complete())
        self.assertEqual(len(c.values((0x1106, 78))), 3)

    def test_require_raises_the_count(self) -> None:
        block = (0x1106, 78)
        header = (0x1106, 2)
        c = StreamCollector({header: 1, block: 16})
        c.feed_all([frame(0x1106, 5, 2, 0), frame(0x1106, 0x5450445A, 2, 1)])
        c.require(block, c.values(header)[0][0])
        self.assertFalse(c.is_complete())
        for _ in range(5):
            c.feed_all(run(0x1106, 78, 78))
        self.assertTrue(c.is_complete())

    def test_extra_shapes_are_recorded_not_required(self) -> None:
        c = StreamCollector({(0x710C, 52): 1}, extra={(0x7132, 1)})
        c.feed(frame(0x7132, 1, 1, 1))
        self.assertFalse(c.is_complete())
        self.assertTrue(c.has_started())
        c.feed_all(run(0x710C, 52, 52))
        self.assertTrue(c.is_complete())
        self.assertEqual([f["cmd"] for f in c.frames].count(0x7132), 1)

    def test_missing_reports_the_deficit(self) -> None:
        c = StreamCollector({(0x1106, 78): 5, (0x1206, 1): 1})
        c.feed(frame(0x1206, 2, 1, 1))
        self.assertEqual(c.missing(), {(0x1106, 78): 5})


if __name__ == "__main__":
    unittest.main()
