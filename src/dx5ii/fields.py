"""Writable fields: name -> command mapping, encoding and read-back lookup.

Every field maps to a ``(cmd, kind, table, settings_key)`` spec.  ``kind``
decides how a user-supplied value becomes a frame payload:

``bool``       on/off, sent as 1/0
``enum``       name or index, sent as the table index
``int32``      signed decimal, sent two's-complement
``mask``       comma-separated names, sent as ``1 << index``
``mask_index`` comma-separated names, sent as the *index* (see below)
``volume``     dB, converted with the current volume step
``name``       UTF-8, sent as eight little-endian words
``timeout``    ``off``/``10s``/``30s``/``60s``, sent as the raw value

``mask_index`` exists because the crossfeed option commands read back a
bitmask but accept an index on write (web bundle
``595e76df36318426.js:1047-1056``: ``values: [0,1]`` / ``[0,1,2,3]``).
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from .constants import (
    AUTO_SCREEN_TIMEOUTS,
    BLUETOOTH_MODES,
    BRIGHTNESS,
    CLASSIC_VU_LEVELS,
    CMD_BALANCE,
    CMD_BRIGHTNESS,
    CMD_BT_APTX,
    CMD_BT_AUDIO,
    CMD_BT_CLEAR_PAIRING,
    CMD_CALL_C1,
    CMD_CALL_C2,
    CMD_CROSSFEED_CONV,
    CMD_CROSSFEED_MEMORY,
    CMD_CROSSFEED_SIMPLE,
    CMD_CROSSFEED_TYPE,
    CMD_DC_DETECT,
    CMD_DEVICE_NAME,
    CMD_DIM_SCREEN_TYPE,
    CMD_DISABLE_CTRL,
    CMD_EXEC_REMOTE_A,
    CMD_EXEC_REMOTE_B,
    CMD_FACTORY_RESET,
    CMD_HEADPHONE_GAIN,
    CMD_HEARTBEAT,
    CMD_HOME_PAGE,
    CMD_INPUT,
    CMD_INPUT_MODE,
    CMD_INPUT_OPTION,
    CMD_LANGUAGE,
    CMD_LINE_MODE,
    CMD_MULTIFUNCTION_DBL,
    CMD_MULTIFUNCTION_KEY,
    CMD_MUTE,
    CMD_OUTPUT,
    CMD_OUTPUT_OPTION,
    CMD_PCM_FILTER,
    CMD_PEQ_MEMORY,
    CMD_PEQ_STATE,
    CMD_POLARITY,
    CMD_POWER,
    CMD_POWER_TRIGGER,
    CMD_REMOTE,
    CMD_REMOTE_A_KEY,
    CMD_REMOTE_B_KEY,
    CMD_REMOTE_MUTE_MODE,
    CMD_SAVE_C1,
    CMD_SAVE_C2,
    CMD_SCREEN_TIMEOUT,
    CMD_SPDIF_MODE,
    CMD_THEME,
    CMD_TRIGGER_OUT,
    CMD_UAC,
    CMD_VOLUME,
    CMD_VOLUME_MEMORY,
    CMD_VOLUME_STEP,
    CMD_VU_BAR_MODE,
    CMD_VU_LEVEL,
    CROSSFEED_CONV_OPTIONS,
    CROSSFEED_SIMPLE_OPTIONS,
    CROSSFEED_TYPES,
    DC_DETECT_LEVELS,
    DIM_SCREEN_TYPES,
    HOME_PAGES,
    INPUT_MODES,
    INPUTS,
    LANGUAGES,
    LINE_MODES,
    MEMORY_MODES,
    MULTIFUNCTION_KEYS,
    OUTPUTS,
    PCM_FILTERS,
    POLARITIES,
    POWER_TRIGGERS,
    REMOTE_MUTE_MODES,
    SPDIF_MODES,
    THEMES,
    TIMEOUT_RAW,
    TRIGGER_OUTS,
    UAC_MODES,
    VOLUME_STEPS,
    VU_BAR_MODES,
)
from .errors import Dx5iiError
from .protocol import db_to_volume, mask_to_items

#: ``name -> (cmd, kind, table, settings key)``
FIELD_SPECS: dict[str, tuple[int, str, Optional[Sequence[str]], str]] = {
    "power":             (CMD_POWER, "bool", None, "powered"),
    "volume":            (CMD_VOLUME, "volume", None, "volume_db"),
    "mute":              (CMD_MUTE, "bool", None, "muted"),
    "input":             (CMD_INPUT, "enum", INPUTS, "input"),
    "output":            (CMD_OUTPUT, "enum", OUTPUTS, "output"),
    "bluetooth":         (CMD_BT_AUDIO, "enum", BLUETOOTH_MODES, "bluetooth_mode"),
    "aptx":              (CMD_BT_APTX, "bool", None, "bluetooth_aptx"),
    "remote":            (CMD_REMOTE, "bool", None, "remote_enabled"),
    "uac":               (CMD_UAC, "enum", UAC_MODES, "uac_mode"),
    "brightness":        (CMD_BRIGHTNESS, "enum", BRIGHTNESS, "brightness"),
    "pcm-filter":        (CMD_PCM_FILTER, "enum", PCM_FILTERS, "pcm_filter"),
    "line-mode":         (CMD_LINE_MODE, "enum", LINE_MODES, "line_mode"),
    "home-page":         (CMD_HOME_PAGE, "enum", HOME_PAGES, "home_page"),
    "theme":             (CMD_THEME, "enum", THEMES, "theme"),
    "power-trigger":     (CMD_POWER_TRIGGER, "enum", POWER_TRIGGERS, "power_trigger"),
    "balance":           (CMD_BALANCE, "int32", None, "balance"),
    "headphone-gain":    (CMD_HEADPHONE_GAIN, "bool", None, "high_gain"),
    "multifunction-key": (CMD_MULTIFUNCTION_KEY, "enum", MULTIFUNCTION_KEYS,
                          "multifunction_key"),
    "language":          (CMD_LANGUAGE, "enum", LANGUAGES, "language"),
    "input-mode":        (CMD_INPUT_MODE, "enum", INPUT_MODES, "input_mode"),
    "input-option":      (CMD_INPUT_OPTION, "mask", INPUTS, "input_option_mask"),
    "output-option":     (CMD_OUTPUT_OPTION, "mask", OUTPUTS, "output_option_mask"),
    "polarity":          (CMD_POLARITY, "enum", POLARITIES, "polarity"),
    "volume-step":       (CMD_VOLUME_STEP, "enum", VOLUME_STEPS, "volume_step"),
    "vu-level":          (CMD_VU_LEVEL, "enum", CLASSIC_VU_LEVELS, "classic_vu_level"),
    "vu-bar-mode":       (CMD_VU_BAR_MODE, "enum", VU_BAR_MODES, "vu_bar_mode"),
    "volume-memory":     (CMD_VOLUME_MEMORY, "enum", MEMORY_MODES, "volume_memory"),
    "peq-memory":        (CMD_PEQ_MEMORY, "enum", MEMORY_MODES, "peq_memory"),
    "remote-a-key":      (CMD_REMOTE_A_KEY, "enum", MULTIFUNCTION_KEYS, "remote_a_key"),
    "remote-b-key":      (CMD_REMOTE_B_KEY, "enum", MULTIFUNCTION_KEYS, "remote_b_key"),
    # Read-only in practice: the firmware has no handler accepting a name.
    "device-name":       (CMD_DEVICE_NAME, "name", None, "name"),
    "spdif-mode":        (CMD_SPDIF_MODE, "enum", SPDIF_MODES, "spdif_mode"),
    "crossfeed-memory":  (CMD_CROSSFEED_MEMORY, "enum", MEMORY_MODES,
                          "crossfeed_memory"),
    "dc-detect":         (CMD_DC_DETECT, "enum", DC_DETECT_LEVELS,
                          "dc_detect_sensitivity"),
    "crossfeed-type":    (CMD_CROSSFEED_TYPE, "enum", CROSSFEED_TYPES, "crossfeed_type"),
    "crossfeed-conv":    (CMD_CROSSFEED_CONV, "mask_index", CROSSFEED_CONV_OPTIONS,
                          "crossfeed_conv_option_mask"),
    "crossfeed-simple":  (CMD_CROSSFEED_SIMPLE, "mask_index",
                          CROSSFEED_SIMPLE_OPTIONS, "crossfeed_simple_option_mask"),
    "peq":               (CMD_PEQ_STATE, "bool", None, "peq_enabled"),
    "screen-timeout":    (CMD_SCREEN_TIMEOUT, "timeout", AUTO_SCREEN_TIMEOUTS,
                          "auto_screen_timeout"),
    "dim-screen-type":   (CMD_DIM_SCREEN_TYPE, "enum", DIM_SCREEN_TYPES,
                          "dim_screen_type"),
    "trigger-out":       (CMD_TRIGGER_OUT, "enum", TRIGGER_OUTS, "trigger_out"),
    "double-key":        (CMD_MULTIFUNCTION_DBL, "enum", MULTIFUNCTION_KEYS,
                          "multifunction_double_key"),
    "remote-mute-mode":  (CMD_REMOTE_MUTE_MODE, "enum", REMOTE_MUTE_MODES,
                          "remote_mute_mode"),
}

#: One-shot actions: no value, no read-back.
FIELD_ACTIONS: dict[str, int] = {
    "factory-reset": CMD_FACTORY_RESET,
    "disable-controller": CMD_DISABLE_CTRL,
    "call-c1": CMD_CALL_C1,
    "call-c2": CMD_CALL_C2,
    "execute-remote-a": CMD_EXEC_REMOTE_A,
    "execute-remote-b": CMD_EXEC_REMOTE_B,
    "heartbeat": CMD_HEARTBEAT,
    "save-c1": CMD_SAVE_C1,
    "save-c2": CMD_SAVE_C2,
    "clear-pairing": CMD_BT_CLEAR_PAIRING,
}

_TRUTHY = ("1", "on", "true", "yes", "y", "enable", "enabled")
_FALSY = ("0", "off", "false", "no", "n", "disable", "disabled")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY:
        return False
    raise Dx5iiError(f"expected on/off, got {value!r}")


def _index_of(table: Sequence[str], value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value < len(table):
            return value
        raise Dx5iiError(f"index {value!r} out of range 0..{len(table) - 1}")
    text = str(value).strip().lower()
    for i, item in enumerate(table):
        if item.lower() == text:
            return i
    raise Dx5iiError(
        f"unknown value {value!r}; expected one of: {', '.join(table)}")


def _mask_of(table: Sequence[str], value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    total = 0
    for part in str(value).split(","):
        part = part.strip().lower()
        if not part or part in ("none", "off", "0"):
            continue
        total |= 1 << _index_of(table, part)
    return total


def resolve_output(value: Any) -> int:
    """Accept an output name (``hp_balanced``) or a raw device index."""
    if isinstance(value, bool):
        raise Dx5iiError("output must be a name or an index, not a bool")
    if isinstance(value, int):
        raw = value
    else:
        text = str(value).strip().lower()
        if text in OUTPUTS:
            raw = OUTPUTS.index(text)
        elif text.lstrip("-").isdigit():
            raw = int(text)
        else:
            raise Dx5iiError(
                f"unknown output {value!r} (valid names: {', '.join(OUTPUTS)})")
    if not 0 <= raw < len(OUTPUTS):
        raise Dx5iiError(f"output index {raw!r} out of range 0..{len(OUTPUTS) - 1}")
    return raw


def resolve_config(value: Any, configs: Sequence[dict[str, Any]]) -> int:
    """Accept an EQ preset name (case-insensitive) or a numeric index."""
    if isinstance(value, bool):
        raise Dx5iiError("config must be a name or an index, not a bool")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    for item in configs:
        if (item.get("name") or "").lower() == text.lower():
            return int(item["index"])
    names = ", ".join(c.get("name") or "?" for c in configs)
    raise Dx5iiError(f"no EQ config named {value!r} (available: {names})")


def encode_field(
    name: str, value: Any, settings: Optional[dict[str, Any]]
) -> list[tuple[int, int, int, int]]:
    """Encode one field write as ``(cmd, data, total, cur)`` frames."""
    if name in FIELD_ACTIONS:
        data = 1 if value is None else (1 if _as_bool(value) else 0)
        return [(FIELD_ACTIONS[name], data, 1, 1)]

    if name not in FIELD_SPECS:
        known = ", ".join(sorted(FIELD_SPECS) + sorted(FIELD_ACTIONS))
        raise Dx5iiError(f"unknown field {name!r}; try: {known}")

    cmd, kind, table, _key = FIELD_SPECS[name]

    if kind == "bool":
        return [(cmd, 1 if _as_bool(value) else 0, 1, 1)]
    if kind == "enum":
        assert table is not None
        return [(cmd, _index_of(table, value), 1, 1)]
    if kind == "int32":
        return [(cmd, int(value) & 0xFFFFFFFF, 1, 1)]
    if kind == "mask":
        assert table is not None
        return [(cmd, _mask_of(table, value), 1, 1)]
    if kind == "mask_index":
        assert table is not None
        return [(cmd, _index_of(table, value), 1, 1)]

    if kind == "timeout":
        if isinstance(value, int) and value in TIMEOUT_RAW.values():
            return [(cmd, value, 1, 1)]
        text = str(value).strip().lower()
        if text in TIMEOUT_RAW:
            return [(cmd, TIMEOUT_RAW[text], 1, 1)]
        raise Dx5iiError(f"unknown timeout {value!r}; expected off/10s/30s/60s")

    if kind == "volume":
        step = (settings or {}).get("volume_step", "half_db")
        step = "one_db" if step == "one_db" else "half_db"
        return [(cmd, db_to_volume(float(value), step), 1, 1)]

    if kind == "name":
        raw = str(value).encode("utf-8")[:32].ljust(32, b"\x00")
        words = [int.from_bytes(raw[i:i + 4], "little") for i in range(0, 32, 4)]
        return [(cmd, word, 8, i) for i, word in enumerate(words)]

    raise Dx5iiError(f"unhandled kind {kind!r} for field {name!r}")


def field_state(snap: dict[str, Any], name: str) -> Any:
    """Read one field out of a :meth:`Dx5ii.snapshot` result."""
    spec = FIELD_SPECS.get(name)
    if spec is None:
        return None
    kind, table = spec[1], spec[2]
    key = spec[3]

    settings = snap.get("settings") or {}
    pushed = snap.get("pushed") or {}
    if key == "name":
        return settings.get("device_name")
    if key == "peq_enabled":
        return pushed.get("peq_enabled")
    if key == "remote_mute_mode":
        value = pushed.get("remote_mute_mode")
    else:
        value = settings.get(key)

    if kind in ("mask", "mask_index") and isinstance(value, int) and table:
        return mask_to_items(value, table)
    if kind == "enum" and isinstance(value, int) and table:
        return table[value] if 0 <= value < len(table) else value
    return value


def field_raw(snap: dict[str, Any], name: str) -> Any:
    """Return the numeric value behind :func:`field_state`.

    Enums decode to their display name, so JSON consumers that want the device
    index use this alongside the name: ``{"output": "hp_balanced",
    "output_raw": 4}``.  Fields that are already numeric (masks, volumes,
    counters) return the same value as :func:`field_state`.
    """
    spec = FIELD_SPECS.get(name)
    if spec is None:
        return None
    kind, key = spec[1], spec[3]

    settings = snap.get("settings") or {}
    pushed = snap.get("pushed") or {}
    if key == "name":
        return None
    if key == "peq_enabled":
        value = pushed.get("peq_enabled")
        return None if value is None else int(bool(value))
    if key == "remote_mute_mode":
        return pushed.get("remote_mute_mode_raw")
    if kind == "enum":
        raw = settings.get(key + "_raw")
        if raw is not None:
            return raw
    return settings.get(key)


__all__ = [
    "FIELD_SPECS",
    "FIELD_ACTIONS",
    "encode_field",
    "field_state",
    "field_raw",
    "resolve_output",
    "resolve_config",
]
