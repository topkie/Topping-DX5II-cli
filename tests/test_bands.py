"""Tests for the 78-word preset block codec."""

from __future__ import annotations

import unittest

import _path  # noqa: F401  (adds src/ to sys.path)

from dx5ii.bands import (
    Band,
    SPARE_BAND,
    decode_band,
    decode_block,
    encode_band,
    encode_block,
    encode_name_words,
    parse_apo_filters,
)


class BandCodecTests(unittest.TestCase):
    def test_factory_low_shelf(self) -> None:
        # IER-M7.Harman band 1: LSC 105 Hz +0.6 dB Q 0.70
        band = Band(type="LS", gain_db=0.6, freq_hz=105, q=0.7)
        self.assertEqual(encode_band(band), [0x00060401, 105, 7000])

    def test_factory_peaking_cut(self) -> None:
        # IER-M7.Harman band 2: PK 188 Hz -4.0 dB Q 0.61
        band = Band(type="PK", gain_db=-4.0, freq_hz=188, q=0.61)
        self.assertEqual(encode_band(band), [0x00D80101, 188, 6100])

    def test_spare_band_words(self) -> None:
        self.assertEqual(encode_band(SPARE_BAND), [0x00000100, 632, 7070])

    def test_decode_roundtrip(self) -> None:
        band = Band(type="HS", gain_db=-2.5, freq_hz=10024, q=0.707)
        back = decode_band(*encode_band(band))
        self.assertEqual(back.type, "HS")
        self.assertAlmostEqual(back.gain_db, -2.5)
        self.assertAlmostEqual(back.freq_hz, 10024)
        self.assertAlmostEqual(back.q, 0.707)

    def test_disabled_band(self) -> None:
        back = decode_band(0x00000100, 632, 7070)
        self.assertFalse(back.enabled)

    def test_clamps_out_of_range(self) -> None:
        band = Band(type="PK", gain_db=99.0, freq_hz=5, q=99.0)
        self.assertEqual(band.gain_db, 12.0)
        self.assertEqual(band.freq_hz, 20.0)
        self.assertEqual(band.q, 15.0)


class BlockTests(unittest.TestCase):
    def test_length_and_spare_slots(self) -> None:
        words = encode_block("TEST", [Band()], [Band()])
        self.assertEqual(len(words), 78)
        self.assertEqual(words[38:41], [0x00000100, 632, 7070])   # left spare
        self.assertEqual(words[71:74], [0x00000100, 632, 7070])   # right spare
        self.assertEqual(words[74:78], [0, 0, 0, 0])

    def test_name_encoding(self) -> None:
        words = encode_block("IER-M7.Harman", [], [])
        self.assertEqual(words[0], 0x2D524549)   # "IER-"
        self.assertEqual(words[3], 0x0000006E)   # "n\0\0\0"

    def test_head_override(self) -> None:
        words = encode_block("X", [], [], head=[1, 0x01009B95, 1, 0x01009B95])
        self.assertEqual(words[5], 0x01009B95)

    def test_decode_block_roundtrip(self) -> None:
        left = [Band(type="LS", gain_db=0.6, freq_hz=105, q=0.7)]
        right = [Band(type="PK", gain_db=-4.0, freq_hz=188, q=0.61)]
        words = encode_block("ROUND", left, right)
        block = decode_block(words)
        self.assertEqual(block["name"], "ROUND")
        self.assertEqual(block["bands_left"][0].type, "LS")
        self.assertAlmostEqual(block["bands_right"][0].gain_db, -4.0)
        self.assertEqual(len(block["bands_left"]), 11)

    def test_decode_rejects_short_input(self) -> None:
        with self.assertRaises(ValueError):
            decode_block([0] * 10)


class NameWordsTests(unittest.TestCase):
    def test_four_words(self) -> None:
        words = encode_name_words("ProbeTmp")
        self.assertEqual(len(words), 4)
        self.assertEqual(words[0], int.from_bytes(b"Prob", "little"))

    def test_truncates_to_fourteen_bytes(self) -> None:
        words = encode_name_words("A" * 40)
        self.assertEqual(len(words), 4)
        self.assertEqual(words[3], int.from_bytes(b"AA\x00\x00", "little"))

    def test_truncation_keeps_utf8_boundary(self) -> None:
        # 13 bytes of ASCII plus a 3-byte character would be cut at byte 14.
        words = encode_name_words("A" * 13 + "字")
        raw = b"".join(word.to_bytes(4, "little") for word in words)
        self.assertEqual(raw[:13], b"A" * 13)
        self.assertEqual(raw[13:], b"\x00\x00\x00")


class ApoParserTests(unittest.TestCase):
    TEXT = (
        "Preamp: -6.0 dB\n"
        "Filter 1: ON LSC Fc 105 Hz Gain +0.6 dB Q 0.70\n"
        "Filter 2: ON PK Fc 188 Hz Gain -4.0 dB Q 0.61\n"
        "Filter 3: OFF PK Fc 1000 Hz Gain +1.5 dB Q 1.00\n"
    )

    def test_parses_filters(self) -> None:
        bands = parse_apo_filters(self.TEXT)
        self.assertEqual(len(bands), 3)
        self.assertEqual(bands[0].type, "LS")
        self.assertAlmostEqual(bands[0].gain_db, 0.6)
        self.assertAlmostEqual(bands[1].gain_db, -4.0)
        self.assertFalse(bands[2].enabled)

    def test_ignores_noise(self) -> None:
        self.assertEqual(parse_apo_filters("Preamp: -6.0 dB\n"), [])


if __name__ == "__main__":
    unittest.main()
