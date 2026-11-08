"""Tests for field encoding and read-back lookup."""

from __future__ import annotations

import unittest

import _path  # noqa: F401  (adds src/ to sys.path)


class EncodeTests(unittest.TestCase):
    def test_bool(self) -> None:
        from dx5ii.fields import encode_field

        self.assertEqual(encode_field("mute", "on", {})[0][1], 1)
        self.assertEqual(encode_field("mute", "off", {})[0][1], 0)

    def test_enum_by_name_and_index(self) -> None:
        from dx5ii.fields import encode_field

        by_name = encode_field("output", "hp_balanced", {})[0]
        by_index = encode_field("output", 4, {})[0]
        self.assertEqual(by_name, by_index)
        self.assertEqual(by_name[0], 0x7105)
        self.assertEqual(by_name[1], 4)

    def test_mask(self) -> None:
        from dx5ii.fields import encode_field

        cmd, data, total, cur = encode_field("input-option", "usb,bt", {})[0]
        self.assertEqual((cmd, data, total, cur), (0x711B, 0b1001, 1, 1))

    def test_mask_index_writes_the_index_not_the_bit(self) -> None:
        from dx5ii.fields import encode_field

        # c2 is index 1; a bitmask would have produced 2.
        self.assertEqual(encode_field("crossfeed-conv", "c2", {})[0][1], 1)
        self.assertEqual(encode_field("crossfeed-simple", "s3", {})[0][1], 2)

    def test_volume_respects_step(self) -> None:
        from dx5ii.fields import encode_field

        half = encode_field("volume", -40.0, {"volume_step": "half_db"})[0]
        one = encode_field("volume", -40.0, {"volume_step": "one_db"})[0]
        self.assertEqual((half[0], half[1]), (0x7102, 80))
        self.assertEqual((one[0], one[1]), (0x7102, 40))

    def test_timeout(self) -> None:
        from dx5ii.fields import encode_field

        self.assertEqual(encode_field("screen-timeout", "30s", {})[0][1], 30)
        self.assertEqual(encode_field("screen-timeout", "off", {})[0][1], 0)

    def test_name_is_eight_words(self) -> None:
        from dx5ii.fields import encode_field

        frames = encode_field("device-name", "AB", {})
        self.assertEqual(len(frames), 8)
        self.assertEqual([f[3] for f in frames], list(range(8)))
        self.assertEqual(frames[0][1], int.from_bytes(b"AB\x00\x00", "little"))

    def test_int32_balance(self) -> None:
        from dx5ii.fields import encode_field

        self.assertEqual(encode_field("balance", -5, {})[0][1], 0xFFFFFFFB)

    def test_action(self) -> None:
        from dx5ii.fields import encode_field

        self.assertEqual(encode_field("save-c1", None, {})[0], (0x7135, 1, 1, 1))

    def test_unknown_field(self) -> None:
        from dx5ii.errors import Dx5iiError
        from dx5ii.fields import encode_field

        with self.assertRaises(Dx5iiError):
            encode_field("nope", 1, {})

    def test_bad_bool(self) -> None:
        from dx5ii.errors import Dx5iiError
        from dx5ii.fields import encode_field

        with self.assertRaises(Dx5iiError):
            encode_field("mute", "maybe", {})


class ResolveTests(unittest.TestCase):
    def test_output(self) -> None:
        from dx5ii.errors import Dx5iiError
        from dx5ii.fields import resolve_output

        self.assertEqual(resolve_output("hp_balanced"), 4)
        self.assertEqual(resolve_output("HP_BALANCED"), 4)
        self.assertEqual(resolve_output("0"), 0)
        with self.assertRaises(Dx5iiError):
            resolve_output("nope")
        with self.assertRaises(Dx5iiError):
            resolve_output(99)

    def test_config(self) -> None:
        from dx5ii.errors import Dx5iiError
        from dx5ii.fields import resolve_config

        configs = [{"index": 0, "name": "Warm"},
                   {"index": 2, "name": "IER-M7.Harman"}]
        self.assertEqual(resolve_config("ier-m7.harman", configs), 2)
        self.assertEqual(resolve_config("1", configs), 1)
        with self.assertRaises(Dx5iiError):
            resolve_config("missing", configs)


class FieldStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snap = {
            "settings": {
                "device_name": "DX5 II",
                "output": "hp_balanced",
                "output_raw": 4,
                "input_option_mask": 9,
                "crossfeed_conv_option_mask": 2,
                "auto_screen_timeout": "off",
            },
            "pushed": {"peq_enabled": True, "remote_mute_mode": 1},
        }

    def test_plain(self) -> None:
        from dx5ii.fields import field_state

        self.assertEqual(field_state(self.snap, "output"), "hp_balanced")
        self.assertTrue(field_state(self.snap, "peq"))
        self.assertEqual(field_state(self.snap, "remote-mute-mode"), "input_adaptive")

    def test_mask_is_expanded(self) -> None:
        from dx5ii.fields import field_state

        self.assertEqual(field_state(self.snap, "input-option"), ["usb", "bt"])
        self.assertEqual(field_state(self.snap, "crossfeed-conv"), ["c2"])

    def test_device_name(self) -> None:
        from dx5ii.fields import field_state

        self.assertEqual(field_state(self.snap, "device-name"), "DX5 II")


if __name__ == "__main__":
    unittest.main()
