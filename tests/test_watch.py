"""Tests for ``dx5ii watch -f``."""

import json
import unittest

from dx5ii.cli import _follow_reader, _frame_line, _watch_loop


def frame(cmd, data=0, total=1, cur=1, protocol=0x20):
    return {"cmd": cmd, "data": data, "total": total, "index": cur,
            "protocol": protocol}


class FollowTests(unittest.TestCase):
    def collect(self, frames, **kwargs):
        """Run the loop over ``frames``; end it by raising Ctrl-C."""
        out = []
        pending = iter(frames)

        def read():
            try:
                return next(pending)
            except StopIteration:
                raise KeyboardInterrupt

        code = _watch_loop(read, out.append, clock=lambda: 1700000000.25,
                           **kwargs)
        self.assertEqual(code, 0)
        return out

    def test_telemetry_is_hidden_by_default(self):
        out = self.collect([frame(0x7131, total=8), frame(0x7132),
                            frame(0x7130, total=2)])
        self.assertEqual(len(out), 1)
        self.assertIn("0x7132", out[0])

    def test_all_shows_telemetry(self):
        out = self.collect(
            [frame(0x7131, total=8), frame(0x7132), frame(0x7130, total=2)],
            include_telemetry=True)
        self.assertEqual(len(out), 3)

    def test_cmd_filter_keeps_one_command(self):
        out = self.collect([frame(0x7132), frame(0x7137, total=3),
                            frame(0x7137, total=3, cur=0)], cmd_filter=0x7137)
        self.assertEqual(len(out), 2)
        for line in out:
            self.assertIn("0x7137", line)

    def test_json_lines_parse(self):
        out = self.collect([frame(0x7132, data=1)], as_json=True)
        payload = json.loads(out[0])
        self.assertEqual(payload["cmd"], "0x7132")
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["cur"], 1)
        self.assertEqual(payload["data"], 1)
        self.assertEqual(payload["t"], 1700000000.25)

    def test_timestamps_prefix(self):
        out = self.collect([frame(0x7132)], timestamps=True)
        self.assertRegex(out[0], r"^\d\d:\d\d:\d\d\.250  cmd=0x7132")
        self.assertIn("data=0x0 (0)", out[0])

    def test_single_frame_line_has_no_timestamp_by_default(self):
        out = self.collect([frame(0x1206, data=3)])
        self.assertTrue(out[0].startswith("cmd=0x1206"))
        self.assertIn("total=1 cur=1", out[0])


class FollowReaderTests(unittest.TestCase):
    def test_heartbeat_keeps_the_session_alive(self):
        class Dev:
            def __init__(self):
                self.sent = []

            def send(self, cmd, data, protocol, total, cur):
                self.sent.append((cmd, data))

            def read_frame(self, ms):
                return None

        dev = Dev()
        now = [0.0]
        read = _follow_reader(dev, interval=5.0, clock=lambda: now[0])
        read()
        now[0] = 4.9
        read()
        self.assertEqual(dev.sent, [])
        now[0] = 5.1
        read()
        now[0] = 10.2
        read()
        self.assertEqual(dev.sent, [(0x7134, 1), (0x7134, 1)])


class FrameLineTests(unittest.TestCase):
    def test_hex_and_decimal_both_present(self):
        line = _frame_line(frame(0x7130, data=0xFFE9FFEB, total=2, cur=0),
                           timestamps=False, as_json=False, when=0.0)
        self.assertEqual(
            line, "cmd=0x7130 pt=0x20 total=2 cur=0 "
                  "data=0xFFE9FFEB (4293525483)")


if __name__ == "__main__":
    unittest.main()
