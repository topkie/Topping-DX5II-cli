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
    CMD_DEVICE_ID,
    CMD_GET_SAMPLING,
    CMD_HARDWARE_VERSION,
    CMD_PEQ_PREVIEW,
    CMD_SERIAL_NUMBER,
    CMD_SOFTWARE_VERSION,
    DEVICE_NAME_WORDS,
    INPUTS,
    OUTPUTS,
    PEQ_PREVIEW_SOURCES,
    PEQ_TEMP_MODES,
    SAMPLE_RATES,
    SETTINGS_LAYOUT,
    SETTINGS_MIN_WORDS,
)
from .bands import decode_block, decode_preamp
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


def format_version(raw: int) -> str:
    """``0x0258`` -> ``V2.58``, the way Topping Home renders it."""
    return "V%d.%02X" % ((raw >> 8) & 0xFF, raw & 0xFF)


def decode_serial(words: Sequence[int]) -> Optional[str]:
    """Unpack the ``serialNumber`` words into its ASCII text."""
    if not words:
        return None
    raw = b"".join((w & 0xFFFFFFFF).to_bytes(4, "little") for w in words)
    text = raw.split(b"\x00", 1)[0].decode("ascii", "replace").strip()
    return text or None


def interpret_identity(frames: Iterable[Frame]) -> dict[str, Any]:
    """Decode the four identity replies: versions, device id and serial.

    Every field stays ``None`` when its reply is missing, so a dropped frame
    never turns into a made-up version string.
    """
    out: dict[str, Any] = {
        "hardware_version": None,
        "software_version": None,
        "device_id": None,
        "serial_number": None,
    }
    serial: list[tuple[int, int]] = []
    for frame in frames:
        cmd = frame["cmd"]
        if cmd == CMD_HARDWARE_VERSION:
            out["hardware_version"] = format_version(frame["data"])
        elif cmd == CMD_SOFTWARE_VERSION:
            out["software_version"] = format_version(frame["data"])
        elif cmd == CMD_DEVICE_ID:
            out["device_id"] = "0x%08X" % frame["data"]
        elif cmd == CMD_SERIAL_NUMBER:
            serial.append((frame["index"], frame["data"]))
    if serial:
        out["serial_number"] = decode_serial([w for _, w in sorted(serial)])
    return out


def interpret_peq_preview(frames: Iterable[Frame]) -> Optional[dict[str, Any]]:
    """Decode the three-frame ``peqPreviewState`` reply.

    ``meta`` packs three bytes: ``active`` in bits 24..31, ``source`` in
    16..23 and ``localIndexBeforePreview`` in 8..15 (255 means "none").

    ``source_name`` names the source the way Topping Home does
    (``none``/``cloud``/``audition``); an unknown byte is kept verbatim as
    ``code=N``.  These fields describe the *device* state only -- call
    :func:`resolve_playing` to turn them into "what is audible".
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
    return {
        "meta": meta,
        "preview_active": bool(active),
        "source": source,
        "source_name": PEQ_PREVIEW_SOURCES.get(source, f"code={source}"),
        "local_index": None if local_index == 255 else local_index,
        "source_id_hash": words[1],
        "config_hash": words[2],
    }


def resolve_playing(
    configs: dict[str, Any], preview: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """Return what the device is audible on right now.

    A cloud or audition config is pushed into the device without becoming a
    preset slot: ``0x1206`` keeps reporting the stored slot, so the preset list
    alone cannot tell the two situations apart.  The result has one meaning per
    field:

    * ``source`` -- ``local``, ``cloud``, ``audition`` or ``unknown``
    * ``slot_index``/``slot_name`` -- set only for ``local``, null otherwise
    * ``source_id_hash``/``config_hash`` -- the device's identifiers for a
      temporary config; the device exposes no name for it.
    """
    if not preview:
        return {
            "source": "unknown",
            "slot_index": None,
            "slot_name": None,
            "source_id_hash": None,
            "config_hash": None,
        }
    hashes = {
        "source_id_hash": preview.get("source_id_hash"),
        "config_hash": preview.get("config_hash"),
    }
    if not preview.get("preview_active"):
        index = configs.get("stored_index")
        if index is None:
            index = preview.get("local_index")
        name = configs.get("stored_name")
        if name is None and index is not None:
            name = next(
                (slot["name"] for slot in configs.get("slots") or []
                 if slot["index"] == index),
                None,
            )
        return {"source": "local", "slot_index": index, "slot_name": name,
                **hashes}
    source = preview.get("source_name")
    if source not in PEQ_TEMP_MODES:
        source = "unknown"
    return {"source": source, "slot_index": None, "slot_name": None, **hashes}


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

    Everything here describes the device's **stored** slots: ``0x1206`` keeps
    reporting one of them even while a cloud config plays.  Use
    :func:`resolve_playing` to find out what is actually audible.
    """
    stored, blocks = _collect_config_blocks(frames)

    slots = [
        {
            "index": i,
            "name": decode_name(words[0:4]),
            "preamp_left": decode_preamp(words[5]),
            "preamp_right": decode_preamp(words[7]),
            "stored_current": i == stored,
        }
        for i, words in enumerate(blocks)
    ]
    return {
        "stored_index": stored,
        "stored_name": next(
            (slot["name"] for slot in slots if slot["index"] == stored), None),
        "count": len(slots),
        "slots": slots,
    }


def interpret_config_blocks(frames: Iterable[Frame]) -> dict[str, Any]:
    """Decode the same upload, keeping every preset's EQ bands."""
    stored, blocks = _collect_config_blocks(frames)

    presets = []
    for i, words in enumerate(blocks):
        block = decode_block(words)
        presets.append({
            "index": i,
            "name": block["name"],
            "enabled": block["enabled"],
            "enabled_left": block["enabled_left"],
            "enabled_right": block["enabled_right"],
            "preamp_left": block["preamp_left"],
            "preamp_right": block["preamp_right"],
            "stored_current": i == stored,
            "bands_left": [b.as_dict() for b in block["bands_left"]],
            "bands_right": [b.as_dict() for b in block["bands_right"]],
        })
    return {
        "stored_index": stored,
        "stored_name": (presets[stored]["name"]
                        if 0 <= stored < len(presets) else None),
        "count": len(presets),
        "presets": presets,
    }


__all__ = [
    "collect_words",
    "interpret_settings",
    "interpret_sampling",
    "format_version",
    "decode_serial",
    "interpret_identity",
    "interpret_peq_preview",
    "resolve_playing",
    "interpret_config_upload",
    "interpret_config_blocks",
]
