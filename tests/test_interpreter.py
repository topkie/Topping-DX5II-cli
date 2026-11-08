"""Tests for the frame-sequence interpreters, using synthetic frames."""

from __future__ import annotations

import unittest

import _path  # noqa: F401  (adds src/ to sys.path)


def frame(cmd: int, data: int = 0, total: int = 1, index: int = 0) -> dict:
    return {"cmd": cmd, "data": data, "total": total, "index": index,
            "protocol": 0x20}


def settings_words(**overrides: int) -> list[int]:
    words = [0] * 52
    words[0] = 1                       # powered
    words[1] = 0x20355844              # "DX5 "
    words[2] = 0x00004949              # "II\0"
    words[9] = 80                      # volume raw -> -40.0 dB
    words[12] = 4                      # output hp_balanced
    words[30] = 9                      # input options usb + bt
    words[32] = 0                      # volume step half_db
    for key, value in overrides.items():
        words[int(key[1:])] = value
    return words


class CollectWordsTests(unittest.TestCase):
    def test_zero_based(self) -> None:
        from dx5ii.interpreter import collect_words

        frames = [frame(0x710C, 100 + i, total=45, index=i) for i in range(45)]
        words = collect_words(frames)
        self.assertIsNotNone(words)
        assert words is not None
        self.assertEqual(words[0], 100)
        self.assertEqual(words[44], 144)

    def test_one_based(self) -> None:
        from dx5ii.interpreter import collect_words

        frames = [frame(0x710C, 200 + i, total=45, index=i + 1) for i in range(45)]
        words = collect_words(frames)
        assert words is not None
        self.assertEqual(words[0], 200)
        self.assertEqual(words[44], 244)

    def test_incomplete_returns_none(self) -> None:
        from dx5ii.interpreter import collect_words

        frames = [frame(0x710C, i, total=45, index=i) for i in range(44)]
        self.assertIsNone(collect_words(frames))

    def test_too_few_words_ignored(self) -> None:
        from dx5ii.interpreter import collect_words

        self.assertIsNone(collect_words([frame(0x710C, 1, total=3, index=0)]))


class SettingsTests(unittest.TestCase):
    def test_core_fields(self) -> None:
        from dx5ii.interpreter import interpret_settings

        out = interpret_settings(settings_words())
        self.assertEqual(out["device_name"], "DX5 II")
        self.assertTrue(out["powered"])
        self.assertEqual(out["volume_raw"], 80)
        self.assertEqual(out["volume_db"], -40.0)
        self.assertEqual(out["output"], "hp_balanced")
        self.assertEqual(out["output_raw"], 4)
        self.assertEqual(out["input_options"], ["usb", "bt"])
        self.assertEqual(out["volume_step"], "half_db")

    def test_sample_rate_and_timeout(self) -> None:
        from dx5ii.interpreter import interpret_settings

        out = interpret_settings(settings_words(w27=1, w49=0))
        self.assertEqual(out["sample_rate"], 44100)
        self.assertEqual(out["auto_screen_timeout"], "off")

    def test_one_db_volume_step(self) -> None:
        from dx5ii.interpreter import interpret_settings

        out = interpret_settings(settings_words(w32=1))
        self.assertEqual(out["volume_step"], "one_db")
        self.assertEqual(out["volume_db"], -80.0)


class SamplingTests(unittest.TestCase):
    def test_code_to_rate(self) -> None:
        from dx5ii.interpreter import interpret_sampling

        out = interpret_sampling([frame(0x710F, 1)])
        assert out is not None
        self.assertEqual(out["sample_rate"], 44100)
        self.assertEqual(out["format"], "PCM")

    def test_missing(self) -> None:
        from dx5ii.interpreter import interpret_sampling

        self.assertIsNone(interpret_sampling([frame(0x710C, 1)]))


class PeqPreviewTests(unittest.TestCase):
    def test_meta_unpacking(self) -> None:
        from dx5ii.interpreter import interpret_peq_preview

        meta = (0 << 24) | (0 << 16) | (2 << 8)   # inactive, source none, index 2
        out = interpret_peq_preview([
            frame(0x7137, meta, total=3, index=0),
            frame(0x7137, 0x11, total=3, index=1),
            frame(0x7137, 0x22, total=3, index=2),
        ])
        assert out is not None
        self.assertFalse(out["preview_active"])
        self.assertEqual(out["source_name"], "none")
        self.assertEqual(out["local_index"], 2)
        self.assertEqual(out["source_id_hash"], 0x11)

    def test_incomplete(self) -> None:
        from dx5ii.interpreter import interpret_peq_preview

        self.assertIsNone(interpret_peq_preview([frame(0x7137, 0, total=3, index=0)]))


class ConfigUploadTests(unittest.TestCase):
    def _block(self, name_words: list[int], base: int) -> list[dict]:
        words = [0] * 78
        words[0:len(name_words)] = name_words
        words[5] = 0xABCD
        return [frame(0x1106, w, total=78, index=base + i)
                for i, w in enumerate(words)]

    def test_two_presets(self) -> None:
        from dx5ii.interpreter import interpret_config_upload

        frames = [frame(0x1206, 1, total=1, index=0)]
        frames += self._block([0x6D726157], 0)          # "Warm"
        frames += self._block([0x616E7944, 0x0063696D], 0)  # "Dynamic"
        out = interpret_config_upload(frames)

        self.assertEqual(out["current_index"], 1)
        self.assertEqual(out["count"], 2)
        self.assertEqual([c["name"] for c in out["configs"]], ["Warm", "Dynamic"])
        self.assertEqual(out["current_name"], "Dynamic")
        self.assertTrue(out["configs"][1]["active"])

    def test_one_based_indices(self) -> None:
        from dx5ii.interpreter import interpret_config_upload

        frames = [frame(0x1206, 0)] + self._block([0x6D726157], 1)
        out = interpret_config_upload(frames)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["configs"][0]["name"], "Warm")
        self.assertEqual(out["current_name"], "Warm")


if __name__ == "__main__":
    unittest.main()
