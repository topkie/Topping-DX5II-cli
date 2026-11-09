"""Tests for the "do I need to send 0x110E?" decision.

The device keeps the stored slot while Topping Home plays a cloud config, so
an equal index does not mean the slot is audible.  These cases pin that down
without touching hardware.
"""

from __future__ import annotations

import unittest

import _path  # noqa: F401  (adds src/ to sys.path)

from dx5ii.actions import needs_config_write, state_summary


def _before(stored, playing):
    return {"stored_index": stored, "playing_source": playing}


class NeedsConfigWriteTests(unittest.TestCase):
    def test_local_slot_already_playing_is_skipped(self) -> None:
        self.assertFalse(needs_config_write(_before(2, "local"), 2))

    def test_cloud_preview_needs_the_switch(self) -> None:
        # The case that was silently skipped: stored 2, audible cloud config.
        self.assertTrue(needs_config_write(_before(2, "cloud"), 2))

    def test_audition_preview_needs_the_switch(self) -> None:
        self.assertTrue(needs_config_write(_before(2, "audition"), 2))

    def test_unknown_preview_needs_the_switch(self) -> None:
        self.assertTrue(needs_config_write(_before(2, "unknown"), 2))

    def test_missing_preview_information_needs_the_switch(self) -> None:
        # No config block in the snapshot: cannot prove the slot is audible.
        self.assertTrue(needs_config_write(_before(2, None), 2))

    def test_different_slot_always_needs_the_switch(self) -> None:
        self.assertTrue(needs_config_write(_before(2, "local"), 1))

    def test_unknown_stored_slot_needs_the_switch(self) -> None:
        self.assertTrue(needs_config_write(_before(None, "local"), 2))


class StateSummaryTests(unittest.TestCase):
    def test_fast_snapshot_still_reports_the_audible_source(self) -> None:
        # include_configs=False leaves no configs block; the preview frames are
        # still there, so the report can say what is playing.
        snap = {
            "settings": {"output": "hp_balanced", "output_raw": 4,
                         "input": "usb", "input_raw": 1},
            "peq": {"meta": 0x00000200, "preview_active": False, "source": 0,
                    "source_name": "none", "local_index": 2,
                    "source_id_hash": 0, "config_hash": 0},
        }
        summary = state_summary(snap)
        self.assertEqual(summary["playing_source"], "local")
        self.assertEqual(summary["playing_slot_index"], 2)
        self.assertIsNone(summary["stored_index"])

    def test_cloud_preview_without_the_preset_list(self) -> None:
        snap = {
            "settings": {"output": "hp_balanced"},
            "peq": {"meta": 0x01010200, "preview_active": True, "source": 1,
                    "source_name": "cloud", "local_index": 2,
                    "source_id_hash": 0x8E5A278A, "config_hash": 0xBF216A8E},
        }
        summary = state_summary(snap)
        self.assertEqual(summary["playing_source"], "cloud")
        self.assertIsNone(summary["playing_slot_index"])

    def test_snapshot_without_a_preview_reply_is_unknown(self) -> None:
        self.assertEqual(state_summary({})["playing_source"], "unknown")


if __name__ == "__main__":
    unittest.main()
