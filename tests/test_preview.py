"""Tests for the temporary (audition) preview handshake."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dx5ii import preview as preview_module  # noqa: E402
from dx5ii.constants import PT_WRITE  # noqa: E402
from dx5ii.bands import live_words  # noqa: E402
from dx5ii.preview import (  # noqa: E402
    _fnv1a,
    _meta,
    enter_preview,
    leave_preview,
)

PREVIEW_CMD = 0x7137


class FakeDev:
    """Records writes and replays a ``peqPreviewState`` made of those writes."""

    def __init__(self, stored=1):
        self.stored = stored
        self.meta = [0, 0, 0]
        self.writes = []

    def send(self, cmd, data, protocol, total=1, cur=0):
        self.writes.append((cmd, data, protocol, total, cur))
        if cmd == PREVIEW_CMD:
            self.meta[cur] = data & 0xFFFFFFFF

    def current_config_index(self):
        return self.stored

    def read_peq_preview(self):
        return [
            {"cmd": PREVIEW_CMD, "data": word, "total": 3, "index": index, "protocol": 0x20}
            for index, word in enumerate(self.meta)
        ]


def preset(name="Demo", preamp=0.0):
    return {
        "name": name,
        "preamp_left": preamp,
        "preamp_right": preamp,
        "bands_left": [
            {"enabled": True, "type": "PK", "gain_db": -2.5, "freq_hz": 1000.0, "q": 0.707}
        ],
        "bands_right": [
            {"enabled": True, "type": "PK", "gain_db": -2.5, "freq_hz": 1000.0, "q": 0.707}
        ],
    }


class MetaTests(unittest.TestCase):
    def test_active_source_and_slot_are_packed_in_three_bytes(self):
        self.assertEqual(_meta(True, 2, 1), 0x01020100)
        self.assertEqual(_meta(False, 0, 1), 0x00000100)
        self.assertEqual(_meta(True, 1, 255), 0x0101FF00)

    def test_source_hash_matches_the_web_page(self):
        self.assertEqual(_fnv1a("audition"), 0x7926FC6E)


class BandWordTests(unittest.TestCase):
    def test_one_preamp_and_eleven_stereo_bands(self):
        words = list(live_words(preset()))
        self.assertEqual(len(words), 4 + 11 * 10)
        self.assertEqual(words[0][0], 0x9C01)
        self.assertEqual(words[1][0], 0x9C02)
        # the second band (index 1) starts at 0x9200
        second = [cmd for cmd, _ in words if (cmd & 0xFF00) == 0x9200]
        self.assertEqual(len(second), 10)

    def test_gain_is_written_in_tenths_of_a_db(self):
        words = dict(live_words(preset()))
        self.assertEqual(words[0x9103], (-25) & 0xFFFFFFFF)


class PreviewTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(preview_module.time, "sleep", lambda seconds: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_enter_writes_the_meta_twice_around_the_bands(self):
        dev = FakeDev(stored=3)
        state = enter_preview(dev, preset())

        metas = [w for w in dev.writes if w[0] == PREVIEW_CMD]
        self.assertEqual(len(metas), 6)
        self.assertEqual(metas[0][1], 0x01020300)
        self.assertEqual(metas[3][1], 0x01020300)
        self.assertTrue(state["preview_active"])
        self.assertEqual(state["source_name"], "audition")

    def test_leave_clears_the_active_byte(self):
        dev = FakeDev(stored=3)
        dev.meta = [0x01020300, 1, 2]
        state = leave_preview(dev)

        self.assertEqual(dev.writes[0][1], 0x00000300)
        self.assertFalse(state["preview_active"])

    def test_unknown_source_is_refused(self):
        with self.assertRaises(ValueError):
            enter_preview(FakeDev(), preset(), source="cloud")


if __name__ == "__main__":
    unittest.main()
