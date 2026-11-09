"""Turn raw :class:`~dx5ii.protocol.Frame` sequences into device state.

Each interpreter is a pure function: frames in, plain ``dict`` out.  Nothing
here touches the USB device, so the pieces can be unit-tested with recorded
frames.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from .constants import (
    AUTO_SCREEN_TIMEOUT_RAW,
    CMD_CONFIG_BLOCK,
    CMD_CURRENT_CONFIG,
    CMD_GET_SAMPLING,
    CMD_PEQ_PREVIEW,
    DEVICE_NAME_WORDS,
    INPUTS,
    OUTPUTS,
    PEQ_PREVIEW_SOURCES,
    PEQ_TEMP_MODES,
    SAMPLE_RATES,
    SETTINGS_LAYOUT,
    SETTINGS_MIN_WORDS,
)
from .bands import decode_block
from .protocol import Frame, decode_name, mask_to_items, volume_to_db

#: ``crossfeed_conv_option_mask`` bit -> option name (bits are 1, 2).
_CONV_BITS = ((1, "c1"), (2, "c2"))
#: ``crossfeed_simple_option_mask`` bit -> option name (bits are 1, 2, 4, 8).
_SIMPLE_BITS = ((1, "s1"), (2, "s2"), (4, "s3"), (8, "s4"))


def collect_words(
    frames: Iterable[Frame], min_total: int = SETTINGS_MIN_WORDS
) -> Optional[list[int]]:
    """Collect one multi-frame ``uint32`` stream (``getSettings``).

    Word indices are 0-based on current firmware and 1-based on some
    revisions; the first frame of a run decides which.  Returns ``None`` until
    every word has arrived.
    """
    state: Optional[dict[str, Any]] = None
    for frame in frames:
        total = frame["total"]
        index = frame["index"]
        if total < min_total or index > total:
            continue
        if state is None or state["total"] != total:
            state = {
                "total": total,
                "base": 1 if index == 1 else 0,
                "words": [None] * total,
            }
        position = index - state["base"]
        if not 0 <= position < total:
            continue
        state["words"][position] = frame["data"]
        if all(word is not None for word in state["words"]):
            return list(state["words"])
    return None


def _decode_enum(value: int, table: Sequence[str]) -> Any:
    return table[value] if 0 <= value < len(table) else value


def interpret_settings(words: Sequence[int]) -> dict[str, Any]:
    """Decode the ``getSettings`` word array.

    Besides the raw decoder output the result carries a few derived keys:

    ``device_name``
        UTF-8 name assembled from words 1..8.
    ``volume_db``
        Derived from ``volume_raw`` and ``volume_step``.
    ``sample_rate``
        Hz value for ``sample_rate_code``.
    ``input_options`` / ``output_options``
        Names of the bits set in the corresponding option masks.
    ``crossfeed_conv_options`` / ``crossfeed_simple_options``
        Same, for the two crossfeed option masks.
    """
    out: dict[str, Any] = {
        "device_name": decode_name(words[i] for i in DEVICE_NAME_WORDS),
    }
    for index, name, decoder in SETTINGS_LAYOUT:
        if index >= len(words):
            continue
        raw = words[index] & 0xFFFFFFFF
        if decoder == "bool":
            out[name] = raw == 1
        elif decoder == "int":
            out[name] = raw
        elif decoder == "int32":
            out[name] = raw - (1 << 32) if raw & 0x80000000 else raw
        elif decoder == "mask":
            out[name] = raw
        else:  # ("enum", table)
            out[name] = _decode_enum(raw, decoder[1])
            out[name + "_raw"] = raw

    if "volume_raw" in out:
        out["volume_db"] = volume_to_db(
            out["volume_raw"], out.get("volume_step", "half_db"))

    code = out.get("sample_rate_code")
    if code is not None:
        out["sample_rate"] = SAMPLE_RATES[code] if 0 <= code < len(SAMPLE_RATES) else None

    if "auto_screen_timeout" in out:
        out["auto_screen_timeout"] = AUTO_SCREEN_TIMEOUT_RAW.get(
            out.get("auto_screen_timeout_raw"), out.get("auto_screen_timeout"))

    if isinstance(out.get("input_option_mask"), int):
        out["input_options"] = mask_to_items(out["input_option_mask"], INPUTS)
    if isinstance(out.get("output_option_mask"), int):
        out["output_options"] = mask_to_items(out["output_option_mask"], OUTPUTS)
    if isinstance(out.get("crossfeed_conv_option_mask"), int):
        mask = out["crossfeed_conv_option_mask"]
        out["crossfeed_conv_options"] = [n for bit, n in _CONV_BITS if mask & bit]
    if isinstance(out.get("crossfeed_simple_option_mask"), int):
        mask = out["crossfeed_simple_option_mask"]
        out["crossfeed_simple_options"] = [n for bit, n in _SIMPLE_BITS if mask & bit]
    return out


def interpret_sampling(frames: Iterable[Frame]) -> Optional[dict[str, Any]]:
    """Decode the single-frame ``getSampling`` reply."""
    for frame in frames:
        if frame["cmd"] != CMD_GET_SAMPLING:
            continue
        code = frame["data"]
        rate = SAMPLE_RATES[code] if 0 <= code < len(SAMPLE_RATES) else None
        if rate is None:
            fmt = None
        else:
            fmt = "DSD" if rate > 768000 else "PCM"
        return {"code": code, "sample_rate": rate, "format": fmt}
    return None


def interpret_peq_preview(frames: Iterable[Frame]) -> Optional[dict[str, Any]]:
    """Decode the three-frame ``peqPreviewState`` reply.

    ``meta`` packs three bytes: ``active`` in bits 24..31, ``source`` in
    16..23 and ``localIndexBeforePreview`` in 8..15 (255 means "none").

    ``mode`` is the state that is actually audible: ``off`` when no preview is
    active, ``cloud``/``audition`` for the two temporary sources, and ``local``
    for anything else (including an unknown source byte, which is also kept
    verbatim in ``source_name`` as ``code=N``).
    """
    words: dict[int, int] = {}
    for frame in frames:
        if frame["cmd"] == CMD_PEQ_PREVIEW and frame["total"] == 3:
            words[frame["index"]] = frame["data"]
    if len(words) < 3:
        return None
    meta = words[0]
    active = (meta >> 24) & 0xFF
    source = (meta >> 16) & 0xFF
    local_index = (meta >> 8) & 0xFF
    source_name = PEQ_PREVIEW_SOURCES.get(source, f"code={source}")
    if not active:
        mode = "off"
    elif source_name in PEQ_TEMP_MODES:
        mode = source_name
    else:
        mode = "local"
    return {
        "meta": meta,
        "preview_active": bool(active),
        "source": source,
        "source_name": source_name,
        "mode": mode,
        "local_index": None if local_index == 255 else local_index,
        "source_id_hash": words[1],
        "config_hash": words[2],
    }


def apply_preview_mode(
    configs: dict[str, Any], preview: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """Mark which EQ source is audible in a decoded preset list.

    ``0x1206`` and the preset upload always describe the device's *local*
    slots.  While a cloud or audition preview is active the reported current
    slot is therefore not what the unit plays: ``effective_source`` names the
    real source and ``current_effective`` says whether ``current_index`` is
    audible.  The device exposes no name for a temporary config, only the
    ``source_id_hash``/``config_hash`` pair.
    """
    if preview is None:
        return configs
    mode = preview.get("mode", "local")
    configs["effective_source"] = mode
    configs["current_effective"] = mode not in PEQ_TEMP_MODES
    return configs


def _collect_config_blocks(
    frames: Iterable[Frame],
) -> tuple[Optional[int], list[list[int]]]:
    """Gather the active index and every 78-word preset block from an upload."""
    current: Optional[int] = None
    blocks: list[list[int]] = []
    buffer: Optional[list[int]] = None
    base: Optional[int] = None

    for frame in frames:
        if frame["cmd"] == CMD_CURRENT_CONFIG:
            if current is None:
                current = frame["data"]
            continue
        if frame["cmd"] != CMD_CONFIG_BLOCK or frame["total"] != 78:
            continue
        index = frame["index"]
        if base is None:
            base = 1 if index == 1 else 0
        if index - base == 0:
            if buffer is not None:
                blocks.append(buffer)
            buffer = [0] * 78
        if buffer is None:
            continue
        position = index - base
        if 0 <= position < 78:
            buffer[position] = frame["data"]
    if buffer is not None:
        blocks.append(buffer)
    return current, blocks


def interpret_config_upload(frames: Iterable[Frame]) -> dict[str, Any]:
    """Decode the ``agreementConfig`` upload into the EQ preset list.

    A full upload is one ``0x1206`` frame carrying the active index plus one
    78-word ``0x1106`` block per preset.  Each block names itself in words
    0..3 (UTF-8, little-endian).  Band details are not included here; use
    :func:`interpret_config_blocks` for those.
    """
    current, blocks = _collect_config_blocks(frames)

    configs = [
        {
            "index": i,
            "name": decode_name(words[0:4]),
            "hash": words[5],
            "active": i == current,
        }
        for i, words in enumerate(blocks)
    ]
    return {
        "current_index": current,
        "current_name": next(
            (c["name"] for c in configs if c["index"] == current), None),
        "count": len(configs),
        "configs": configs,
    }


def interpret_config_blocks(frames: Iterable[Frame]) -> dict[str, Any]:
    """Decode the same upload, keeping every preset's EQ bands."""
    current, blocks = _collect_config_blocks(frames)

    presets = []
    for i, words in enumerate(blocks):
        block = decode_block(words)
        presets.append({
            "index": i,
            "name": block["name"],
            "hash": block["hash"],
            "enabled": block["enabled"],
            "active": i == current,
            "bands_left": [b.as_dict() for b in block["bands_left"]],
            "bands_right": [b.as_dict() for b in block["bands_right"]],
        })
    return {
        "current_index": current,
        "count": len(presets),
        "presets": presets,
    }


__all__ = [
    "collect_words",
    "interpret_settings",
    "interpret_sampling",
    "interpret_peq_preview",
    "apply_preview_mode",
    "interpret_config_upload",
    "interpret_config_blocks",
]
