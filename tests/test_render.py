"""The markdown-to-terminal renderer used by ``dx5ii manual``."""

from __future__ import annotations

import unittest

from dx5ii.textfmt import display_width, render_markdown


class DisplayWidthTests(unittest.TestCase):
    def test_ascii_is_one_column(self):
        self.assertEqual(display_width("abc"), 3)

    def test_cjk_is_two_columns(self):
        self.assertEqual(display_width("标题"), 4)
        self.assertEqual(display_width("a标"), 3)


class HeadingTests(unittest.TestCase):
    def test_h1_gets_an_equals_rule(self):
        out = render_markdown("# PEQ presets\n")
        self.assertEqual(out.splitlines()[0], "PEQ presets")
        self.assertEqual(out.splitlines()[1], "=" * len("PEQ presets"))

    def test_h2_gets_a_dash_rule(self):
        out = render_markdown("## Preamp\n")
        self.assertEqual(out.splitlines()[0], "Preamp")
        self.assertEqual(out.splitlines()[1], "-" * len("Preamp"))


class InlineTests(unittest.TestCase):
    def test_markers_are_removed(self):
        out = render_markdown("Use `dx5ii get` for **one** field.\n")
        self.assertIn("Use dx5ii get for one field.", out)
        self.assertNotIn("`", out)
        self.assertNotIn("**", out)

    def test_link_becomes_text_and_url(self):
        out = render_markdown("See [the docs](https://example.com/x).\n")
        self.assertIn("See the docs (https://example.com/x).", out)

    def test_italic_underscores_are_removed(self):
        out = render_markdown("_Note_ this.\n")
        self.assertIn("Note this.", out)


class CodeTests(unittest.TestCase):
    def test_fenced_block_is_indented_verbatim(self):
        out = render_markdown("```\ndx5ii get\n```\n")
        self.assertIn("    dx5ii get", out)
        self.assertNotIn("```", out)


class ListTests(unittest.TestCase):
    def test_bullet_uses_a_star_in_ascii_mode(self):
        out = render_markdown("- first\n- second\n", ascii_only=True)
        self.assertIn("  * first", out)
        self.assertIn("  * second", out)

    def test_bullet_uses_a_dot_in_unicode_mode(self):
        out = render_markdown("- first\n", ascii_only=False)
        self.assertIn("  • first", out)

    def test_continuation_lines_join_the_item(self):
        out = render_markdown("- a long item that\n  continues here\n")
        self.assertEqual(out.count("*"), 1)
        self.assertIn("a long item that continues here", out)

    def test_ordered_list_keeps_its_number(self):
        out = render_markdown("1. open the page\n")
        self.assertIn("  1. open the page", out)


class ParagraphTests(unittest.TestCase):
    def test_paragraph_is_rewrapped(self):
        source = "one two three four five six seven eight nine ten eleven twelve\n"
        out = render_markdown(source, width=40)
        for line in out.splitlines():
            self.assertLessEqual(display_width(line), 40, line)
        self.assertIn("one two three", " ".join(out.split()))

    def test_blank_line_separates_paragraphs(self):
        out = render_markdown("first para\n\nsecond para\n", width=40)
        self.assertIn("", out.splitlines())


class TableTests(unittest.TestCase):
    SOURCE = (
        "| field | values |\n"
        "|---|---|\n"
        "| `pcm-filter` | `f1` … `f8` |\n"
        "| `line-mode` | `preamp`, `dac` |\n"
    )

    def test_columns_are_aligned(self):
        lines = render_markdown(self.SOURCE).splitlines()
        self.assertTrue(lines[0].startswith("field"))
        self.assertTrue(lines[1].startswith("---"))
        self.assertIn("pcm-filter", lines[2])
        self.assertIn("f1 … f8", lines[2])

    def test_markers_are_removed_from_cells(self):
        out = render_markdown(self.SOURCE)
        self.assertNotIn("`", out)
        self.assertNotIn("|", out)

    def test_wide_table_wraps_instead_of_overflowing(self):
        source = (
            "| name | values |\n"
            "|---|---|\n"
            "| `double-key` | alpha, beta, gamma, delta, epsilon, zeta, eta, theta |\n"
        )
        out = render_markdown(source, width=50)
        for line in out.splitlines():
            self.assertLessEqual(display_width(line), 50, line)
        joined = " ".join(out.split())
        self.assertIn("theta", joined)


class IdempotenceTests(unittest.TestCase):
    def test_plain_text_survives(self):
        out = render_markdown("A sentence without markup.\n")
        self.assertEqual(out.strip(), "A sentence without markup.")

    def test_trailing_blank_lines_are_trimmed(self):
        out = render_markdown("text\n\n\n")
        self.assertEqual(out, "text\n")


if __name__ == "__main__":
    unittest.main()
