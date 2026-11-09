"""Tests for the multi-frame collector state machine."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dx5ii.stream import ReadSession, ReadState, StreamCollector  # noqa: E402


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


class FakeClock:
    """A clock the test moves by hand, in milliseconds."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, ms: float) -> None:
        self.now += ms / 1000.0


class ReadSessionTests(unittest.TestCase):
    def make(self, shapes, timeout_ms=1200, idle_ms=250, settle_ms=0, extra=()) -> tuple:
        clock = FakeClock()
        collector = StreamCollector(shapes, extra=extra)
        session = ReadSession(collector, timeout_ms=timeout_ms, idle_ms=idle_ms,
                              settle_ms=settle_ms, clock=clock)
        return session, collector, clock

    def test_times_out_without_frames(self) -> None:
        session, _, clock = self.make({(0x710C, 52): 1}, timeout_ms=500)
        self.assertIs(session.state, ReadState.WAITING)
        clock.advance(400)
        self.assertIs(session.step(), ReadState.WAITING)
        clock.advance(200)
        self.assertIs(session.step(), ReadState.TIMED_OUT)
        self.assertFalse(session.running)
        self.assertFalse(session.complete)

    def test_stalls_when_frames_stop(self) -> None:
        session, _, clock = self.make({(0x710C, 52): 1}, idle_ms=250)
        for f in run(0x710C, 3, 52):
            session.step(f)
            clock.advance(2)
        self.assertIs(session.state, ReadState.COLLECTING)
        clock.advance(240)
        self.assertIs(session.step(), ReadState.COLLECTING)
        clock.advance(20)
        self.assertIs(session.step(), ReadState.STALLED)

    def test_completes_then_settles(self) -> None:
        session, _, clock = self.make({(0x7137, 3): 1}, settle_ms=60)
        for i, data in enumerate([1, 2, 3]):
            session.step(frame(0x7137, data, 3, i))
            clock.advance(2)
        self.assertIs(session.state, ReadState.SETTLING)
        clock.advance(30)
        session.step()
        self.assertIs(session.state, ReadState.SETTLING)
        clock.advance(40)
        self.assertIs(session.step(), ReadState.COMPLETE)
        self.assertTrue(session.complete)

    def test_zero_settle_completes_on_the_last_frame(self) -> None:
        session, _, _ = self.make({(0x7132, 1): 1}, settle_ms=0)
        self.assertIs(session.step(frame(0x7132, 1, 1, 1)), ReadState.COMPLETE)

    def test_settle_still_records_late_frames(self) -> None:
        session, collector, clock = self.make({(0x7137, 3): 1}, settle_ms=60,
                                              extra={(0x7132, 1)})
        for i, data in enumerate([1, 2, 3]):
            session.step(frame(0x7137, data, 3, i))
        session.step(frame(0x7132, 1, 1, 1))     # push frame during settle
        clock.advance(61)
        session.step()
        self.assertIs(session.state, ReadState.COMPLETE)
        self.assertEqual([f["cmd"] for f in collector.frames], [0x7137] * 3 + [0x7132])

    def test_telemetry_does_not_start_the_collect(self) -> None:
        session, _, clock = self.make({(0x710C, 52): 1}, timeout_ms=900)
        for _ in range(100):
            session.step(frame(0x7131, 0, 8, 0))
            clock.advance(5)
        self.assertIs(session.state, ReadState.WAITING)
        clock.advance(1000)
        self.assertIs(session.step(), ReadState.TIMED_OUT)

    def test_stall_ignores_unwatched_frames(self) -> None:
        session, _, clock = self.make({(0x710C, 52): 1}, timeout_ms=2000,
                                      idle_ms=250)
        for f in run(0x710C, 3, 52):
            session.step(f)
            clock.advance(2)
        self.assertIs(session.state, ReadState.COLLECTING)
        for _ in range(100):
            session.step(frame(0x7130, 0, 1, 0))
            clock.advance(5)
        self.assertIs(session.state, ReadState.STALLED)

    def test_poll_interval_shrinks_while_settling(self) -> None:
        session, _, _ = self.make({(0x7132, 1): 1}, settle_ms=60)
        self.assertEqual(session.poll_ms, 20)
        session.step(frame(0x7132, 1, 1, 1))
        self.assertIs(session.state, ReadState.SETTLING)
        self.assertEqual(session.poll_ms, 10)

    def test_step_is_inert_after_a_terminal_state(self) -> None:
        session, _, clock = self.make({(0x7132, 1): 1}, timeout_ms=100)
        clock.advance(200)
        session.step()
        self.assertIs(session.state, ReadState.TIMED_OUT)
        clock.advance(5000)
        self.assertIs(session.step(frame(0x7132, 1, 1, 1)), ReadState.TIMED_OUT)


if __name__ == "__main__":
    unittest.main()
