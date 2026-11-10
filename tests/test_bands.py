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
    decode_preamp,
    encode_preamp,
    parse_apo_filters,
    parse_apo_preamp,
)
from dx5ii.errors import Dx5iiError


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


class ApoDeviceSectionTests(unittest.TestCase):
    """The real Equalizer APO shape: unnumbered lines, several Device sections."""

    REAL = (
        "# Equalizer APO - game delta\n"
        "Device: mic\n"
        "Preamp: -2 dB\n"
        "Filter: ON HP Fc 90 Hz\n"
        "Filter: ON PK Fc 130 Hz Gain -4 dB Q 5\n"
        "Device: DX5 II\n"
        "Preamp: -1.8 dB\n"
        "Filter: ON PK Fc 79.0 Hz Gain -5.57 dB Q 1.30\n"
        "Filter: ON LSC Fc 110.0 Hz Gain -5.0 dB Q 0.50\n"
        "Filter: ON PK Fc 434.7 Hz Gain +1.95 dB Q 0.61\n"
        "Filter: ON PK Fc 1707.0 Hz Gain -0.85 dB Q 0.68\n"
        "Filter: ON PK Fc 4867.7 Hz Gain -4.62 dB Q 1.14\n"
        "Filter: ON HSC Fc 10300.0 Hz Gain -6.00 dB Q 0.65\n"
        "Filter: ON PK Fc 12247.0 Hz Gain -4.46 dB Q 2.57\n"
    )

    def test_unnumbered_lines_are_parsed(self) -> None:
        text = (
            "Filter: ON PK Fc 79.0 Hz Gain -5.57 dB Q 1.30\n"
            "Filter: ON LSC Fc 110.0 Hz Gain -5.0 dB Q 0.50\n"
        )
        bands = parse_apo_filters(text)
        self.assertEqual([b.type for b in bands], ["PK", "LS"])
        self.assertAlmostEqual(bands[0].gain_db, -5.6)
        self.assertAlmostEqual(bands[0].q, 1.30)

    def test_reads_only_the_named_device(self) -> None:
        bands = parse_apo_filters(self.REAL, device="DX5 II")
        self.assertEqual(len(bands), 7)
        self.assertAlmostEqual(bands[0].freq_hz, 79.0)
        self.assertEqual(bands[1].type, "LS")
        self.assertEqual(bands[5].type, "HS")
        self.assertNotIn("HP", [b.type for b in bands])

    def test_device_name_is_ignored_without_sections(self) -> None:
        text = "Filter: ON PK Fc 100 Hz Gain 1 dB Q 1\n"
        self.assertEqual(len(parse_apo_filters(text, device="DX5 II")), 1)

    def test_single_named_section_needs_no_device(self) -> None:
        text = "Device: DX5 II\nFilter: ON PK Fc 100 Hz Gain 1 dB Q 1\n"
        self.assertEqual(len(parse_apo_filters(text)), 1)

    def test_several_sections_without_device_raise(self) -> None:
        with self.assertRaises(Dx5iiError) as caught:
            parse_apo_filters(self.REAL)
        self.assertIn("Device sections", str(caught.exception))

    def test_missing_device_section_raises(self) -> None:
        with self.assertRaises(Dx5iiError) as caught:
            parse_apo_filters(self.REAL, device="DX7")
        self.assertIn("no 'Device: DX7'", str(caught.exception))

    def test_unmappable_filter_type_raises(self) -> None:
        text = "Filter: ON BP Fc 100 Hz Gain 1 dB Q 1\n"
        with self.assertRaises(Dx5iiError) as caught:
            parse_apo_filters(text)
        self.assertIn("BP", str(caught.exception))

    def test_lowpass_and_highpass_are_stored(self) -> None:
        text = (
            "Filter: ON LP Fc 632 Hz Gain 0.0 dB Q 0.707\n"
            "Filter: ON HP Fc 632 Hz Gain 0.0 dB Q 0.707\n"
        )
        bands = parse_apo_filters(text)
        self.assertEqual([b.type for b in bands], ["LP", "HP"])
        self.assertEqual([encode_band(b)[0] >> 8 & 0xFF for b in bands], [2, 3])

    def test_pass_filters_need_no_gain(self) -> None:
        text = "Filter: ON HP Fc 90 Hz\nFilter: ON LP Fc 16000 Hz\n"
        bands = parse_apo_filters(text)
        self.assertEqual([b.type for b in bands], ["HP", "LP"])
        self.assertEqual([b.gain_db for b in bands], [0.0, 0.0])

    def test_missing_gain_raises(self) -> None:
        with self.assertRaises(Dx5iiError) as caught:
            parse_apo_filters("Filter: ON PK Fc 100 Hz Q 1\n")
        self.assertIn("no Gain", str(caught.exception))

    def test_too_many_bands_raise(self) -> None:
        text = "".join(
            "Filter: ON PK Fc %d Hz Gain 1 dB Q 1\n" % (100 + i)
            for i in range(12)
        )
        with self.assertRaises(Dx5iiError) as caught:
            parse_apo_filters(text)
        self.assertIn("12 filters", str(caught.exception))


if __name__ == "__main__":
    unittest.main()


class PreampTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        for db in (-12.0, -6.0, -3.0, 0.0, 2.0, 12.0):
            self.assertAlmostEqual(decode_preamp(encode_preamp(db)), db,
                                   places=1)

    def test_zero_is_the_unity_word(self) -> None:
        self.assertEqual(encode_preamp(0.0), 0x02000000)

    def test_clamps_beyond_the_stored_range(self) -> None:
        self.assertEqual(encode_preamp(-40.0), encode_preamp(-12.0))
        self.assertEqual(encode_preamp(40.0), encode_preamp(12.0))

    def test_factory_value_decodes_to_minus_six(self) -> None:
        self.assertAlmostEqual(decode_preamp(0x01009B95), -6.0, places=1)

    def test_a_zero_word_reads_as_unity_not_minus_twelve(self) -> None:
        # The block stores 0 for a channel that does not take part; the web
        # page reads it as 0.0 dB, and so do we.
        self.assertEqual(decode_preamp(0), 0.0)

    def test_block_keeps_both_channel_enable_bits(self) -> None:
        words = encode_block("P", [Band()], [Band()], head=[0, 0, 1, 0])
        block = decode_block(words)
        self.assertFalse(block["enabled_left"])
        self.assertTrue(block["enabled_right"])

    def test_block_carries_the_preamp_words(self) -> None:
        word = encode_preamp(-6.0)
        words = encode_block(
            "P", [Band()], [Band()], head=[1, word, 1, word]
        )
        self.assertEqual(words[5], word)
        self.assertEqual(words[7], word)
        block = decode_block(words)
        self.assertAlmostEqual(block["preamp_left"], -6.0, places=1)
        self.assertAlmostEqual(block["preamp_right"], -6.0, places=1)


class ApoPreampTests(unittest.TestCase):
    def test_reads_the_selected_section(self) -> None:
        text = (
            "Device: mic\nPreamp: -3.0 dB\nFilter: ON PK Fc 100 Hz Gain 1 dB Q 1\n"
            "Device: DX5 II\nPreamp: -6.5 dB\nFilter: ON PK Fc 100 Hz Gain 1 dB Q 1\n"
        )
        self.assertEqual(parse_apo_preamp(text, device="DX5 II"), -6.5)
        self.assertEqual(len(parse_apo_filters(text, device="DX5 II")), 1)

    def test_absent_line_is_none(self) -> None:
        self.assertIsNone(
            parse_apo_preamp("Filter: ON PK Fc 100 Hz Gain 1 dB Q 1\n"))

    def test_zero_is_not_none(self) -> None:
        text = "Preamp: 0.0 dB\nFilter: ON PK Fc 100 Hz Gain 1 dB Q 1\n"
        self.assertEqual(parse_apo_preamp(text), 0.0)
