"""Human-readable rendering for the CLI.

Kept apart from the device code so the library never builds console strings.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from .constants import REMOTE_MUTE_MODES

#: Fields whose boolean means something more specific than yes/no.
BOOL_TEXT: dict[str, dict[bool, str]] = {
    "high_gain": {True: "high", False: "low"},
    "dc_detect_sensitivity": {True: "high", False: "low"},
}

#: Values pushed by the device that arrive as a bare enum index.
PUSHED_TEXT: dict[str, Sequence[str]] = {
    "remote_mute_mode": REMOTE_MUTE_MODES,
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


def _playing_text(playing: dict[str, Any]) -> str:
    """Render "what is audible", independent of what the device has stored."""
    source = playing.get("source")
    if source == "local":
        index = playing.get("slot_index")
        if index is None:
            return "local (slot unknown)"
        return "local slot %s (%s)" % (index, playing.get("slot_name"))
    if source in ("cloud", "audition"):
        return "%s config (Unknown)" % source
    return "unknown (no preview reply)"


def _stored_text(configs: dict[str, Any]) -> str:
    index = configs.get("stored_index")
    if index is None:
        return "unknown"
    return "slot %s (%s)" % (index, configs.get("stored_name"))


def print_configs(configs: dict[str, Any]) -> None:
    """Print the EQ preset list with the audible source on its own line."""
    playing = configs.get("playing") or {}
    _print_rows("eq configs", [
        ("playing", _playing_text(playing)),
        ("stored", _stored_text(configs)),
        ("count", configs.get("count")),
    ])
    for slot in configs.get("slots") or []:
        if playing.get("source") == "local" \
                and slot["index"] == playing.get("slot_index"):
            mark = "  <-- playing"
        elif slot.get("stored_current"):
            mark = "  stored"
        else:
            mark = ""
        print("  %d  %-18s%s" % (slot["index"], slot["name"], mark))


def format_value(key: str, value: Any) -> str:
    """Render one field value for the console."""
    if isinstance(value, bool):
        table = BOOL_TEXT.get(key) or {True: "yes", False: "no"}
        return table[value]
    return str(value)


def _pushed_rows(pushed: dict[str, Any]) -> Iterable[tuple[str, Any]]:
    """Yield pushed values, naming the ones the device sends as raw indices."""
    for key, value in pushed.items():
        if key.endswith("_name") or key.endswith("_raw"):
            continue  # redundant next to the value it annotates
        table = PUSHED_TEXT.get(key)
        if table is not None and isinstance(value, int) and not isinstance(value, bool):
            if 0 <= value < len(table):
                value = table[value]
        yield key, value


def _print_rows(title: str, rows: Iterable[tuple[str, Any]]) -> None:
    print(f"[{title}]")
    for key, value in rows:
        if value is not None:
            print(f"  {key:<26} {format_value(key, value)}")


def print_snapshot(snap: dict[str, Any]) -> None:
    """Print a full snapshot in the same grouping ``dx5ii get`` uses."""
    device = snap.get("device") or {}
    print("device   : %s  serial=%s  iface=%s  firmware=%s"
          % (device.get("product"), device.get("serial"),
             device.get("interface_number"), device.get("firmware") or "?"))

    settings = snap.get("settings")
    if not settings:
        print("settings : <no reply>")
    else:
        for title, keys in SNAPSHOT_GROUPS:
            _print_rows(title, ((k, settings.get(k)) for k in keys if k in settings))

    pushed = snap.get("pushed") or {}
    if pushed:
        _print_rows("pushed by device", _pushed_rows(pushed))

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
        print_configs(configs)
    elif settings is not None:
        print("[eq configs] skipped")


def _state_line(label: str, state: dict[str, Any]) -> str:
    """Render one side of a switch report: port, stored slot and audible source."""
    text = "output=%s  stored=%s (%s)" % (
        state.get("output"), state.get("stored_index"), state.get("stored_name"))
    source = state.get("playing_source")
    if source:
        text += "  playing=%s" % source
    return "%s: %s" % (label, text)


def print_switch_report(report: dict[str, Any]) -> None:
    """Print the result of :func:`dx5ii.actions.apply_changes`."""
    before = report.get("before") or {}
    after = report.get("after") or {}
    print(_state_line("before   ", before))
    for action in report.get("actions") or []:
        if action.get("left_preview"):
            note = "left %s preview" % action["left_preview"]
        elif action["changed"]:
            note = "changed"
        else:
            note = "already set"
        print("  %-6s %s -> %s  (%s)"
              % (action["what"], action["from"], action["to"], note))
    print(_state_line("after    ", after))
    for error in report.get("errors") or []:
        print(f"error    : {error}")


def print_field_rows(rows: Iterable[tuple[str, Any]]) -> None:
    """Print ``name  value`` lines, as ``dx5ii get <field>`` does."""
    for name, value in rows:
        print(f"{name:<20} {format_value(name, value)}")


def format_gain(value: float) -> str:
    """Signed gain text at 0.1 dB; zero is printed without a sign."""
    if value == 0:
        return "0.0"
    return "%+.1f" % value


def format_preamp(left: float, right: float) -> str:
    """Render a stereo preamp pair, collapsing it when both sides match."""
    if left == right:
        return "L+R %4s dB" % format_gain(left)
    return "L %4s  R %4s dB" % (format_gain(left), format_gain(right))


__all__ = [
    "BOOL_TEXT",
    "SNAPSHOT_GROUPS",
    "format_gain",
    "format_preamp",
    "format_value",
    "print_configs",
    "print_snapshot",
    "print_switch_report",
    "print_field_rows",
]
