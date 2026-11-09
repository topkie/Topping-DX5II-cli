"""Tests for the console rendering of the EQ preset list."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

import _path  # noqa: F401  (adds src/ to sys.path)


def _configs(source: str, slot_index=None, slot_name=None) -> dict:
    return {
        "playing": {
            "source": source,
            "slot_index": slot_index,
            "slot_name": slot_name,
            "source_id_hash": 0 if source == "local" else 0x8E5A278A,
            "config_hash": 0 if source == "local" else 0xBF216A8E,
        },
        "stored_index": 2,
        "stored_name": "IER-M7.Harman",
        "count": 2,
        "slots": [
            {"index": 1, "name": "Dynamic", "hash": 0x016A77C4, "stored_current": False},
            {"index": 2, "name": "IER-M7.Harman", "hash": 0x01009B95,
             "stored_current": True},
        ],
    }


class PrintConfigsTests(unittest.TestCase):
    def _render(self, configs: dict) -> list[str]:
        from dx5ii.formatting import print_configs

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_configs(configs)
        return buffer.getvalue().splitlines()

    def test_cloud_names_the_source_and_marks_storage(self) -> None:
        lines = self._render(_configs("cloud"))
        self.assertEqual(lines[0], "[eq configs]")
        self.assertEqual(lines[1], "  playing                    cloud config (Unknown)")
        self.assertEqual(lines[2], "  stored                     slot 2 (IER-M7.Harman)")
        self.assertIn("2  IER-M7.Harman       stored", lines[-1])

    def test_local_names_the_slot(self) -> None:
        lines = self._render(_configs("local", 2, "IER-M7.Harman"))
        self.assertEqual(lines[1], "  playing                    local slot 2 (IER-M7.Harman)")
        self.assertIn("<-- playing", lines[-1])

    def test_audition_is_named_too(self) -> None:
        lines = self._render(_configs("audition"))
        self.assertEqual(lines[1], "  playing                    audition config (Unknown)")

    def test_unknown_does_not_claim_a_slot(self) -> None:
        lines = self._render(_configs("unknown"))
        self.assertEqual(lines[1], "  playing                    unknown (no preview reply)")
        self.assertIn("2  IER-M7.Harman       stored", lines[-1])


def _report(actions, before_playing="cloud", after_playing="local"):
    state = {"output": "hp_balanced", "stored_index": 2,
             "stored_name": "IER-M7.Harman"}
    return {
        "ok": True,
        "changed": True,
        "before": {**state, "playing_source": before_playing},
        "after": {**state, "playing_source": after_playing},
        "actions": actions,
        "errors": [],
    }


class PrintSwitchReportTests(unittest.TestCase):
    def _render(self, report: dict) -> list[str]:
        from dx5ii.formatting import print_switch_report

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            print_switch_report(report)
        return buffer.getvalue().splitlines()

    def test_leaving_a_cloud_preview_is_named(self) -> None:
        lines = self._render(_report([{
            "what": "config", "from": 2, "to": 2, "changed": True,
            "observed": 2, "left_preview": "cloud"}]))
        self.assertIn("  config 2 -> 2  (left cloud preview)", lines)
        self.assertIn("playing=cloud", lines[0])
        self.assertIn("playing=local", lines[-1])

    def test_plain_switch_still_says_changed(self) -> None:
        lines = self._render(_report([{
            "what": "config", "from": 1, "to": 2, "changed": True, "observed": 2}],
            before_playing="local"))
        self.assertIn("  config 1 -> 2  (changed)", lines)

    def test_idempotent_switch_says_already_set(self) -> None:
        lines = self._render(_report([{
            "what": "config", "from": 2, "to": 2, "changed": False}],
            before_playing="local"))
        self.assertIn("  config 2 -> 2  (already set)", lines)


if __name__ == "__main__":
    unittest.main()
