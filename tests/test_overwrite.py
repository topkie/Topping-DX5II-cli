"""Overwriting a stored preset with the live-parameter command set."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dx5ii.bands import Band, encode_block, encode_preamp  # noqa: E402
from dx5ii.constants import (  # noqa: E402
    CMD_CONFIG_BLOCK,
    CMD_CURRENT_CONFIG,
    CMD_SWITCH_MCU_CONFIG,
    CONFIG_BLOCK_WORDS,
)
from dx5ii.errors import Dx5iiError  # noqa: E402
from dx5ii.peq import overwrite_preset  # noqa: E402

PREVIEW_CMD = 0x7137
LIVE_PREAMP = (0x9C01, 0x9C02, 0x9C03, 0x9C04)


def preset(name="Demo", preamp=0.0, gain=-2.5):
    band = {"enabled": True, "type": "PK", "gain_db": gain,
            "freq_hz": 1000.0, "q": 0.707}
    return {
        "name": name,
        "preamp_left": preamp,
        "preamp_right": preamp,
        "enabled_left": True,
        "enabled_right": True,
        "bands_left": [band],
        "bands_right": [dict(band)],
    }


def block_for(spec):
    return encode_block(
        spec["name"],
        [Band(**b) for b in spec["bands_left"]],
        [Band(**b) for b in spec["bands_right"]],
        head=[1, encode_preamp(spec["preamp_left"]),
              1, encode_preamp(spec["preamp_right"])],
    )


class FakeDev:
    """A device whose stored blocks are whatever the caller says they are."""

    def __init__(self, blocks, current=1):
        self.blocks = [list(b) for b in blocks]
        self.current = current
        self.writes = []
        self.meta = [0, 0, 0]

    def send(self, cmd, data, protocol, total=1, cur=0):
        self.writes.append((cmd, data & 0xFFFFFFFF, total, cur))
        if cmd == PREVIEW_CMD:
            self.meta[cur] = data & 0xFFFFFFFF
        elif cmd == CMD_SWITCH_MCU_CONFIG:
            self.current = int(data)

    def read_for(self, _ms):
        return None

    def read_config_upload(self):
        frames = [{"cmd": CMD_CURRENT_CONFIG, "data": self.current,
                   "total": 1, "index": 0, "protocol": 0x20}]
        for block in self.blocks:
            for index, word in enumerate(block):
                frames.append({"cmd": CMD_CONFIG_BLOCK, "data": word,
                               "total": CONFIG_BLOCK_WORDS,
                               "index": index, "protocol": 0x20})
        return frames

    def read_peq_preview(self):
        return [{"cmd": PREVIEW_CMD, "data": word, "total": 3,
                 "index": index, "protocol": 0x20}
                for index, word in enumerate(self.meta)]

    def current_config_index(self):
        return self.current


class OverwriteTests(unittest.TestCase):
    def setUp(self):
        self.other = block_for(preset("Other", gain=1.0))
        self.target = block_for(preset("Demo", gain=-2.5))
        self.dev = FakeDev([self.other, self.target], current=1)

    def live_writes(self):
        return [w for w in self.dev.writes
                if w[0] in LIVE_PREAMP or 0x9101 <= w[0] <= 0x9B0A]

    def test_sends_the_full_live_set(self):
        overwrite_preset(self.dev, 0, preset(), verify=False)
        live = self.live_writes()
        self.assertEqual(len(live), 114)
        self.assertEqual(live[0][0], 0x9C01)
        self.assertEqual(live[1], (0x9C02, 1, 1, 0))
        self.assertEqual(live[-1][0], 0x9B0A)

    def test_preview_is_cleared_before_writing(self):
        overwrite_preset(self.dev, 0, preset(), verify=False)
        first_preview = [i for i, w in enumerate(self.dev.writes)
                         if w[0] == PREVIEW_CMD]
        first_live = [i for i, w in enumerate(self.dev.writes)
                      if w[0] == 0x9C01][0]
        self.assertTrue(first_preview)
        self.assertLess(max(first_preview), first_live)
        self.assertEqual(self.dev.meta[0] >> 24, 0)

    def test_switches_to_the_target_and_back(self):
        overwrite_preset(self.dev, 0, preset(), verify=False)
        switches = [w[1] for w in self.dev.writes
                    if w[0] == CMD_SWITCH_MCU_CONFIG]
        self.assertEqual(switches, [0, 1])
        self.assertEqual(self.dev.current, 1)

    def test_playing_slot_is_not_switched(self):
        overwrite_preset(self.dev, 1, preset(), verify=False)
        switches = [w for w in self.dev.writes
                    if w[0] == CMD_SWITCH_MCU_CONFIG]
        self.assertEqual(switches, [])

    def test_read_back_match_is_reported(self):
        result = overwrite_preset(self.dev, 1, preset("Demo", gain=-2.5))
        self.assertTrue(result["verified"])
        self.assertFalse(result["switched"])

    def test_read_back_mismatch_is_reported(self):
        result = overwrite_preset(self.dev, 1, preset("Demo", gain=-9.0))
        self.assertFalse(result["verified"])

    def test_missing_slot_raises(self):
        with self.assertRaises(Dx5iiError):
            overwrite_preset(self.dev, 9, preset(), verify=False)



if __name__ == "__main__":
    unittest.main()
