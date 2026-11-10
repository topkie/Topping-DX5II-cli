"""EQ preset management on the device.

The device stores a fixed number of presets; each one is a 78-word block (see
:mod:`dx5ii.bands`).  Creating a preset uploads a whole block with
``CMD_DOWNLOAD_CONFIG``; renaming uses ``CMD_RENAME_CONFIG_INDEX`` followed by
``CMD_RENAME_CONFIG_NAME``; deleting uses ``CMD_DELETE_CONFIG`` and then
re-selects whatever preset the caller wants active.

Writing bands *inside* an existing preset uses the 0x9000 live-parameter
commands.  Sent without a preview meta first they change the playing preset in
place; the device stores the result, so :func:`overwrite_preset` is the only
entry point and it asks for a backup first.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Optional, Sequence

from .bands import Band, decode_block, encode_block, encode_name_words, live_words
from .constants import (
    BANDS_PER_CHANNEL,
    CMD_CONFIG_BLOCK,
    CMD_CURRENT_CONFIG,
    CMD_DELETE_CONFIG,
    CMD_DOWNLOAD_CONFIG,
    CMD_GET_SETTINGS,
    CMD_RENAME_CONFIG_INDEX,
    CMD_RENAME_CONFIG_NAME,
    CMD_SWITCH_MCU_CONFIG,
    CONFIG_BLOCK_WORDS,
    CONFIG_UPLOAD_WORDS,
    PT_READ,
    PT_WRITE,
)
from .errors import Dx5iiError
from .interpreter import interpret_config_blocks
from .preview import leave_preview

__all__ = [
    "create_preset",
    "delete_preset",
    "overwrite_preset",
    "presets_equal",
    "read_preset_blocks",
    "rename_preset",
    "read_presets",
    "find_preset",
]


def read_presets(dev: Any, settle_ms: int = 1600) -> dict[str, Any]:
    """Read every preset with its EQ bands.

    The device only answers ``agreementConfig`` after a ``getSettings`` read,
    so this always issues that first.
    """
    dev.send(CMD_GET_SETTINGS, 0, PT_READ)
    dev.read_for(settle_ms)
    frames = dev.read_config_upload()
    return interpret_config_blocks(frames)


def find_preset(presets: dict[str, Any], name: str) -> Optional[dict[str, Any]]:
    """Case-insensitive name lookup over a :func:`read_presets` result."""
    needle = name.strip().lower()
    for preset in presets.get("presets", []):
        if preset["name"].strip().lower() == needle:
            return preset
    return None


def create_preset(
    dev: Any,
    name: str,
    bands_left: Iterable[Band],
    bands_right: Iterable[Band],
    head: Optional[Sequence[int]] = None,
    settle_ms: int = 400,
) -> int:
    """Upload a new preset and return its index.

    ``head`` overrides words 4..7 of the block (enabled flag + hash).  When
    omitted a zero hash is sent; the device computes its own on save.

    The device accepts exactly 74 words on upload (``totalFrameLen`` 78 is
    rejected silently), so the four padding words are dropped.
    """
    words = encode_block(name, bands_left, bands_right, head)[:CONFIG_UPLOAD_WORDS]
    for i, word in enumerate(words):
        dev.send(CMD_DOWNLOAD_CONFIG, word, PT_WRITE, total=len(words), cur=i)
        if i % 10 == 9:
            time.sleep(0.02)
    time.sleep(0.12)
    presets = read_presets(dev, settle_ms)
    for preset in presets.get("presets", []):
        if preset["name"].strip() == name.strip():
            return int(preset["index"])
    return int(presets.get("count", 0)) - 1


def delete_preset(dev: Any, index: int, switch_to: int, settle_ms: int = 300) -> None:
    """Delete preset ``index`` and re-select ``switch_to``.

    Both steps end on the ``0x1206`` frame the device pushes when the active
    preset moves, instead of sleeping a fixed span.  A device that stays quiet
    falls back to the old wait, so a dropped push cannot shorten the operation.
    """
    wait = getattr(dev, "wait_for_frame", None)
    dev.send(CMD_DELETE_CONFIG, int(index), PT_WRITE)
    if wait is not None:
        wait(CMD_CURRENT_CONFIG, None, timeout_ms=200)
    else:
        time.sleep(0.14)
    dev.send(CMD_SWITCH_MCU_CONFIG, int(switch_to), PT_WRITE)
    if wait is not None:
        if not wait(CMD_CURRENT_CONFIG,
                    lambda frame: frame["data"] == int(switch_to),
                    timeout_ms=max(settle_ms, 300)):
            time.sleep(0.12)
    else:
        time.sleep(0.12 + settle_ms / 1000.0)


def rename_preset(dev: Any, index: int, name: str, settle_ms: int = 200) -> None:
    """Rename preset ``index``; the name is truncated to 14 UTF-8 bytes.

    The device accepts four name frames per rename; sending more is ignored
    silently, so the frame count must match ``len(encode_name_words(name))``.
    Completion is the ``0x1206`` frame for this slot, with the old wait kept as
    the fallback when the device does not announce it.
    """
    words = encode_name_words(name)
    dev.send(CMD_RENAME_CONFIG_INDEX, int(index), PT_WRITE)
    time.sleep(0.05)
    for i, word in enumerate(words):
        dev.send(CMD_RENAME_CONFIG_NAME, word, PT_WRITE, total=len(words), cur=i)
    wait = getattr(dev, "wait_for_frame", None)
    if wait is not None:
        if not wait(CMD_CURRENT_CONFIG,
                    lambda frame: frame["data"] == int(index),
                    timeout_ms=max(settle_ms, 300)):
            time.sleep(0.15)
    else:
        time.sleep(0.08 + settle_ms / 1000.0)


def read_preset_blocks(dev: Any, settle_ms: int = 1600) -> tuple[dict[str, Any], list[list[int]]]:
    """Read the presets twice over: decoded, and as the raw 78-word blocks."""
    dev.send(CMD_GET_SETTINGS, 0, PT_READ)
    dev.read_for(settle_ms)
    frames = dev.read_config_upload()
    blocks: list[list[int]] = []
    for frame in frames:
        if frame["cmd"] == CMD_CONFIG_BLOCK and frame["total"] == CONFIG_BLOCK_WORDS:
            if frame["index"] == 0:
                blocks.append([])
            blocks[-1].append(frame["data"])
    blocks = [block for block in blocks if len(block) == CONFIG_BLOCK_WORDS]
    return interpret_config_blocks(frames), blocks


def presets_equal(left: dict[str, Any], right: dict[str, Any],
                  tolerance: float = 0.1) -> bool:
    """True when two preset payloads describe the same EQ.

    Accepts both decoded presets (bands as :class:`Band`) and plain dicts.
    Preamp values are compared with ``tolerance`` because the device stores
    them as a 25-bit fixed point value and the web page uses a lookup table.
    """
    for key in ("preamp_left", "preamp_right"):
        if abs(float(left.get(key) or 0.0) - float(right.get(key) or 0.0)) > tolerance:
            return False
    for key in ("enabled_left", "enabled_right"):
        if bool(left.get(key, True)) != bool(right.get(key, True)):
            return False
    for channel in ("bands_left", "bands_right"):
        have = left.get(channel) or []
        want = right.get(channel) or []
        for index in range(BANDS_PER_CHANNEL):
            mine = have[index] if index < len(have) else Band(enabled=False)
            theirs = want[index] if index < len(want) else Band(enabled=False)
            if hasattr(mine, "as_dict"):
                mine = mine.as_dict()
            if hasattr(theirs, "as_dict"):
                theirs = theirs.as_dict()
            if not mine.get("enabled") and not theirs.get("enabled"):
                continue
            if bool(mine.get("enabled")) != bool(theirs.get("enabled")):
                return False
            if mine["type"] != theirs["type"]:
                return False
            if int(round(mine["freq_hz"])) != int(round(theirs["freq_hz"])):
                return False
            if round(float(mine["gain_db"]), 1) != round(float(theirs["gain_db"]), 1):
                return False
            if round(float(mine["q"]), 4) != round(float(theirs["q"]), 4):
                return False
    return True


def overwrite_preset(
    dev: Any,
    index: int,
    preset: dict[str, Any],
    verify: bool = True,
    settle_ms: int = 300,
    presets: Optional[dict[str, Any]] = None,
    blocks: Optional[list[list[int]]] = None,
) -> dict[str, Any]:
    """Write ``preset`` in place over the stored preset at ``index``.

    The device has one filter set for playback, and the 114 live-parameter
    writes land in the playing preset unless a preview meta is active.  So the
    preview is cleared first, the target slot is selected when it is not the
    playing one, and the previous slot is restored afterwards.
    """
    if presets is None or blocks is None:
        presets, blocks = read_preset_blocks(dev)
    if not 0 <= int(index) < len(blocks):
        raise Dx5iiError("no preset at index %d" % int(index))

    leave_preview(dev)
    current = presets.get("stored_index")
    switched = current != int(index)
    if switched:
        dev.send(CMD_SWITCH_MCU_CONFIG, int(index), PT_WRITE)
        time.sleep(0.2)

    words = live_words(preset)
    for position, (cmd, value) in enumerate(words):
        dev.send(cmd, value & 0xFFFFFFFF, PT_WRITE)
        if position % 10 == 9:
            time.sleep(0.02)
    time.sleep(0.15)

    verified: Optional[bool] = None
    if verify:
        _after, after_blocks = read_preset_blocks(dev)
        verified = presets_equal(decode_block(after_blocks[int(index)]), preset)

    if switched:
        dev.send(CMD_SWITCH_MCU_CONFIG, int(current), PT_WRITE)
        time.sleep(0.2)

    return {
        "index": int(index),
        "switched": switched,
        "verified": verified,
        "commands": len(words),
    }
