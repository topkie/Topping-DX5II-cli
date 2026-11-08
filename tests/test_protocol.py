"""Tests for the wire codec and small conversions."""

from __future__ import annotations

import unittest

import _path  # noqa: F401  (adds src/ to sys.path)


class BuildFrameTests(unittest.TestCase):
    def test_layout(self) -> None:
        from dx5ii.constants import PT_READ
        from dx5ii.protocol import build_frame

        frame = build_frame(0x710C, 0x01020304, PT_READ, 3, 1)
        self.assertEqual(len(frame), 16)
        self.assertEqual(frame[0:2], b"\x22\x33")
        self.assertEqual(frame[2], PT_READ)
        self.assertEqual(frame[3], 3)
        self.assertEqual(frame[4], 1)
        self.assertEqual(frame[5:7], b"\x71\x0c")
        self.assertEqual(frame[7:11], b"\x01\x02\x03\x04")
        self.assertEqual(frame[11:13], b"\x00\x00")
        self.assertEqual(frame[13:15], b"\x66\x77")
        self.assertEqual(frame[15], 0)

    def test_roundtrip_through_parser(self) -> None:
        from dx5ii.protocol import build_frame, parse_frame

        parsed = parse_frame(build_frame(0x7137, 512, 0x20, 3, 2))
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["cmd"], 0x7137)
        self.assertEqual(parsed["data"], 512)
        self.assertEqual(parsed["protocol"], 0x20)
        self.assertEqual(parsed["total"], 3)
        self.assertEqual(parsed["index"], 2)


class ParseFrameTests(unittest.TestCase):
    def test_rejects_short_or_malformed(self) -> None:
        from dx5ii.protocol import build_frame, parse_frame

        good = build_frame(0x710F)
        self.assertIsNone(parse_frame(b""))
        self.assertIsNone(parse_frame(good[:10]))
        self.assertIsNone(parse_frame(b"\x00" * 16))
        self.assertIsNone(parse_frame(b"\x22\x33" + b"\x00" * 13))

    def test_accepts_leading_zero_transport_form(self) -> None:
        from dx5ii.protocol import build_frame, parse_frame

        frame = build_frame(0x710F, 7)
        parsed = parse_frame(b"\x00" + frame)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual((parsed["cmd"], parsed["data"]), (0x710F, 7))


class VolumeTests(unittest.TestCase):
    def test_half_db_scale(self) -> None:
        from dx5ii.protocol import db_to_volume, volume_to_db

        self.assertEqual(volume_to_db(0, "half_db"), 0.0)
        self.assertEqual(volume_to_db(80, "half_db"), -40.0)
        self.assertEqual(volume_to_db(198, "half_db"), -99.0)
        self.assertEqual(db_to_volume(-40.0, "half_db"), 80)

    def test_one_db_scale(self) -> None:
        from dx5ii.protocol import db_to_volume, volume_to_db

        self.assertEqual(volume_to_db(40, "one_db"), -40.0)
        self.assertEqual(db_to_volume(-40.0, "one_db"), 40)

    def test_clamped(self) -> None:
        from dx5ii.protocol import db_to_volume, volume_to_db

        self.assertEqual(volume_to_db(-5, "half_db"), 0.0)
        self.assertEqual(volume_to_db(999, "half_db"), -99.0)
        self.assertEqual(db_to_volume(10.0, "half_db"), 0)
        self.assertEqual(db_to_volume(-200.0, "half_db"), 198)


class NameAndMaskTests(unittest.TestCase):
    def test_decode_name(self) -> None:
        from dx5ii.protocol import decode_name

        words = [0x20355844, 0x00004949]  # "DX5 " + "II\0"
        self.assertEqual(decode_name(words), "DX5 II")

    def test_mask_to_items(self) -> None:
        from dx5ii.constants import INPUTS
        from dx5ii.protocol import mask_to_items

        self.assertEqual(mask_to_items(9, INPUTS), ["usb", "bt"])
        self.assertEqual(mask_to_items(0, INPUTS), [])


if __name__ == "__main__":
    unittest.main()
