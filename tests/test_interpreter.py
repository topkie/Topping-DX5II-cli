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
        from dx5ii.bands import encode_preamp

        words[5] = encode_preamp(-3.0)
        words[7] = encode_preamp(-3.0)
        return [frame(0x1106, w, total=78, index=base + i)
                for i, w in enumerate(words)]

    def test_two_presets(self) -> None:
        from dx5ii.interpreter import interpret_config_upload

        frames = [frame(0x1206, 1, total=1, index=0)]
        frames += self._block([0x6D726157], 0)          # "Warm"
        frames += self._block([0x616E7944, 0x0063696D], 0)  # "Dynamic"
        out = interpret_config_upload(frames)

        self.assertEqual(out["stored_index"], 1)
        self.assertEqual(out["count"], 2)
        self.assertEqual([c["name"] for c in out["slots"]], ["Warm", "Dynamic"])
        self.assertEqual(out["stored_name"], "Dynamic")
        self.assertTrue(out["slots"][1]["stored_current"])
        self.assertEqual(out["slots"][0]["preamp_left"], -3.0)
        self.assertNotIn("current_index", out)

    def test_one_based_indices(self) -> None:
        from dx5ii.interpreter import interpret_config_upload

        frames = [frame(0x1206, 0)] + self._block([0x6D726157], 1)
        out = interpret_config_upload(frames)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["slots"][0]["name"], "Warm")
        self.assertEqual(out["stored_name"], "Warm")

    def test_blocks_report_the_stored_name(self) -> None:
        from dx5ii.interpreter import interpret_config_blocks

        frames = [frame(0x1206, 1)]
        frames += self._block([0x6D726157], 0)          # "Warm"
        frames += self._block([0x616E7944, 0x0063696D], 0)  # "Dynamic"
        out = interpret_config_blocks(frames)

        self.assertEqual(out["stored_index"], 1)
        self.assertEqual(out["stored_name"], "Dynamic")
        self.assertEqual(out["presets"][0]["name"], "Warm")


class PeqSourceNameTests(unittest.TestCase):
    """The source byte, named the way Topping Home names it."""

    def _preview(self, source: int, active: int = 1, local: int = 2):
        from dx5ii.interpreter import interpret_peq_preview

        meta = (active << 24) | (source << 16) | (local << 8)
        return interpret_peq_preview([
            frame(0x7137, meta, total=3, index=0),
            frame(0x7137, 0x8E5A278A, total=3, index=1),
            frame(0x7137, 0xBF216A8E, total=3, index=2),
        ])

    def test_cloud_is_named(self) -> None:
        out = self._preview(1)
        assert out is not None
        self.assertEqual(out["source_name"], "cloud")
        self.assertTrue(out["preview_active"])

    def test_audition_is_named(self) -> None:
        out = self._preview(2)
        assert out is not None
        self.assertEqual(out["source_name"], "audition")

    def test_unknown_source_stays_verbatim(self) -> None:
        out = self._preview(7)
        assert out is not None
        self.assertEqual(out["source_name"], "code=7")


class ResolvePlayingTests(unittest.TestCase):
    """``playing`` must name what is audible, not what the device stored."""

    def _configs(self) -> dict:
        return {
            "stored_index": 2,
            "stored_name": "IER-M7.Harman",
            "count": 1,
            "slots": [{"index": 2, "name": "IER-M7.Harman", "stored_current": True}],
        }

    def _preview(self, active: bool, source_name: str) -> dict:
        return {
            "preview_active": active,
            "source_name": source_name,
            "local_index": 2,
            "source_id_hash": 0x8E5A278A if active else 0,
            "config_hash": 0xBF216A8E if active else 0,
        }

    def test_local_slot_is_playing(self) -> None:
        from dx5ii.interpreter import resolve_playing

        out = resolve_playing(self._configs(), self._preview(False, "none"))
        self.assertEqual(out["source"], "local")
        self.assertEqual(out["slot_index"], 2)
        self.assertEqual(out["slot_name"], "IER-M7.Harman")

    def test_cloud_masks_the_stored_slot(self) -> None:
        from dx5ii.interpreter import resolve_playing

        out = resolve_playing(self._configs(), self._preview(True, "cloud"))
        self.assertEqual(out["source"], "cloud")
        self.assertIsNone(out["slot_index"])
        self.assertIsNone(out["slot_name"])
        self.assertEqual(out["source_id_hash"], 0x8E5A278A)
        self.assertEqual(out["config_hash"], 0xBF216A8E)

    def test_audition_masks_the_stored_slot(self) -> None:
        from dx5ii.interpreter import resolve_playing

        out = resolve_playing(self._configs(), self._preview(True, "audition"))
        self.assertEqual(out["source"], "audition")
        self.assertIsNone(out["slot_index"])

    def test_unknown_source_is_not_a_slot(self) -> None:
        from dx5ii.interpreter import resolve_playing

        out = resolve_playing(self._configs(), self._preview(True, "code=7"))
        self.assertEqual(out["source"], "unknown")
        self.assertIsNone(out["slot_index"])

    def test_missing_preview_is_unknown(self) -> None:
        from dx5ii.interpreter import resolve_playing

        out = resolve_playing(self._configs(), None)
        self.assertEqual(out["source"], "unknown")
        self.assertIsNone(out["slot_index"])
        self.assertIsNone(out["source_id_hash"])


if __name__ == "__main__":
    unittest.main()
