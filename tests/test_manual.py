"""The manual shipped inside the package."""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from dx5ii.cli import _MANUAL_SECTIONS, _cmd_manual, _manual_fields, _manual_text
from dx5ii.fields import FIELD_ACTIONS, FIELD_SPECS


def run(section=None, want_list=False, markdown=False):
    args = argparse.Namespace(section=section, list=want_list, markdown=markdown)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = _cmd_manual(args)
    return code, out.getvalue(), err.getvalue()


class SectionTests(unittest.TestCase):
    def test_every_section_is_installed(self):
        for name in _MANUAL_SECTIONS:
            text = _manual_text(name)
            self.assertGreater(len(text), 200, name)
            self.assertTrue(text.startswith("# "), name)

    def test_default_is_the_index(self):
        code, out, _ = run()
        self.assertEqual(code, 0)
        self.assertIn("dx5ii — user manual", out)
        self.assertIn("=" * 10, out)

    def test_named_section(self):
        code, out, _ = run("peq")
        self.assertEqual(code, 0)
        self.assertIn("PEQ presets", out)
        self.assertIn("===========", out)

    def test_unknown_section_is_an_error(self):
        code, _, err = run("nope")
        self.assertEqual(code, 1)
        self.assertIn("unknown manual section", err)

    def test_list_names_every_section(self):
        code, out, _ = run(want_list=True)
        self.assertEqual(code, 0)
        for name in _MANUAL_SECTIONS:
            self.assertIn(name, out)


class GeneratedFieldsTests(unittest.TestCase):
    def test_field_section_appends_the_generated_table(self):
        code, out, _ = run("fields", markdown=True)
        self.assertEqual(code, 0)
        self.assertIn("generated from the encoder", out)
        for name in FIELD_SPECS:
            self.assertIn("`%s`" % name, out)
        for name in FIELD_ACTIONS:
            self.assertIn("`%s`" % name, out)

    def test_table_lists_masks_as_bits(self):
        text = _manual_fields()
        self.assertIn("bits: usb, fiber, coax, bt", text)
        self.assertIn("0x%04X" % FIELD_SPECS["output-option"][0], text)


class RenderingTests(unittest.TestCase):
    def test_default_output_is_not_markdown(self):
        code, out, _ = run("peq")
        self.assertEqual(code, 0)
        self.assertNotIn("```", out)
        self.assertNotIn("| ---", out)
        self.assertIn("PEQ presets\n===========", out)

    def test_markdown_flag_returns_the_file(self):
        code, out, _ = run("peq", markdown=True)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# PEQ presets\n"))
        self.assertIn("```", out)

    def test_generated_table_is_rendered_too(self):
        code, out, _ = run("fields")
        self.assertEqual(code, 0)
        self.assertNotIn("| `power` |", out)
        self.assertIn("power", out)


class HelpTests(unittest.TestCase):
    def test_top_level_help_points_at_the_manual(self):
        from dx5ii.cli import build_parser

        text = build_parser().format_help()
        self.assertIn("dx5ii manual", text)
        self.assertIn("peq", text)


if __name__ == "__main__":
    unittest.main()
