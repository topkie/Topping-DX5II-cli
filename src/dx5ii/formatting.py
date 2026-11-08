"""Human-readable rendering for the CLI.

Kept apart from the device code so the library never builds console strings.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

#: Fields whose boolean means something more specific than yes/no.
BOOL_TEXT: dict[str, dict[bool, str]] = {
    "high_gain": {True: "high", False: "low"},
    "dc_detect_sensitivity": {True: "high", False: "low"},
}

#: ``(group title, field names)`` for the full ``get`` listing.
SNAPSHOT_GROUPS: list[tuple[str, Sequence[str]]] = [
    ("power", ["powered", "volume_db", "volume_raw", "muted", "balance"]),
    ("input/output", ["input", "output", "high_gain", "input_mode",
                      "input_options", "output_options", "uac_mode",
                      "bluetooth_mode", "bluetooth_aptx", "spdif_mode"]),
    ("audio", ["pcm_filter", "line_mode", "sample_rate", "polarity",
               "crossfeed_type", "crossfeed_conv_options",
               "crossfeed_simple_options", "dc_detect_sensitivity"]),
    ("display", ["theme", "home_page", "brightness", "language",
                 "auto_screen_timeout", "dim_screen_type",
                 "classic_vu_level", "vu_bar_mode", "remote_enabled"]),
    ("memory/keys", ["volume_memory", "peq_memory", "crossfeed_memory",
                     "power_trigger", "trigger_out", "multifunction_key",
                     "multifunction_double_key", "remote_a_key",
                     "remote_b_key"]),
]


def format_value(key: str, value: Any) -> str:
    """Render one field value for the console."""
    if isinstance(value, bool):
        table = BOOL_TEXT.get(key) or {True: "yes", False: "no"}
        return table[value]
    return str(value)


def _print_rows(title: str, rows: Iterable[tuple[str, Any]]) -> None:
    print(f"[{title}]")
    for key, value in rows:
        if value is not None:
            print(f"  {key:<26} {format_value(key, value)}")


def print_snapshot(snap: dict[str, Any]) -> None:
    """Print a full snapshot in the same grouping ``dx5ii get`` uses."""
    device = snap.get("device") or {}
    print("device   : %s  serial=%s  iface=%s"
          % (device.get("product"), device.get("serial"),
             device.get("interface_number")))

    settings = snap.get("settings")
    if not settings:
        print("settings : <no reply>")
    else:
        for title, keys in SNAPSHOT_GROUPS:
            _print_rows(title, ((k, settings.get(k)) for k in keys if k in settings))

    pushed = snap.get("pushed") or {}
    if pushed:
        _print_rows("pushed by device", pushed.items())

    sampling = snap.get("sampling")
    if sampling:
        _print_rows("sampling", [
            ("sample_rate", sampling.get("sample_rate")),
            ("format", sampling.get("format")),
        ])

    preview = snap.get("peq")
    if preview:
        _print_rows("peq preview", [
            ("preview_active", preview.get("preview_active")),
            ("source_name", preview.get("source_name")),
            ("local_index", preview.get("local_index")),
            ("source_id_hash", preview.get("source_id_hash")),
            ("config_hash", preview.get("config_hash")),
        ])

    configs = snap.get("configs")
    if configs:
        print("[eq configs] current=%s (%s) count=%s"
              % (configs.get("current_index"), configs.get("current_name"),
                 configs.get("count")))
        for item in configs.get("configs") or []:
            mark = "  <-- active" if item.get("active") else ""
            print("  %d  %-18s%s" % (item["index"], item["name"], mark))
    elif settings is not None:
        print("[eq configs] skipped")


def print_switch_report(report: dict[str, Any]) -> None:
    """Print the result of :func:`dx5ii.actions.apply_changes`."""
    before = report.get("before") or {}
    after = report.get("after") or {}
    print("before   : output=%s  config=%s (%s)"
          % (before.get("output"), before.get("config_index"),
             before.get("config_name")))
    for action in report.get("actions") or []:
        print("  %-6s %s -> %s  (%s)"
              % (action["what"], action["from"], action["to"],
                 "changed" if action["changed"] else "already set"))
    print("after    : output=%s  config=%s (%s)"
          % (after.get("output"), after.get("config_index"),
             after.get("config_name")))
    for error in report.get("errors") or []:
        print(f"error    : {error}")


def print_field_rows(rows: Iterable[tuple[str, Any]]) -> None:
    """Print ``name  value`` lines, as ``dx5ii get <field>`` does."""
    for name, value in rows:
        print(f"{name:<20} {format_value(name, value)}")


__all__ = [
    "BOOL_TEXT",
    "SNAPSHOT_GROUPS",
    "format_value",
    "print_snapshot",
    "print_switch_report",
    "print_field_rows",
]
