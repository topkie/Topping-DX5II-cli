"""EQ preset management on the device.

The device stores a fixed number of presets; each one is a 78-word block (see
:mod:`dx5ii.bands`).  Creating a preset uploads a whole block with
``CMD_DOWNLOAD_CONFIG``; renaming uses ``CMD_RENAME_CONFIG_INDEX`` followed by
``CMD_RENAME_CONFIG_NAME``; deleting uses ``CMD_DELETE_CONFIG`` and then
re-selects whatever preset the caller wants active.

Writing bands *inside* an existing preset is intentionally not exposed: a
write goes through the 0x9000 live-parameter commands and would modify the
preset in place.  Callers that want to change an EQ create a new preset.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Optional, Sequence

from .bands import Band, encode_block, encode_name_words
from .constants import (
    CMD_DELETE_CONFIG,
    CMD_DOWNLOAD_CONFIG,
    CMD_GET_SETTINGS,
    CMD_RENAME_CONFIG_INDEX,
    CMD_RENAME_CONFIG_NAME,
    CMD_SWITCH_MCU_CONFIG,
    CONFIG_UPLOAD_WORDS,
    PT_READ,
    PT_WRITE,
)
from .errors import Dx5iiError
from .interpreter import interpret_config_blocks

__all__ = [
    "create_preset",
    "delete_preset",
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
    """Delete preset ``index`` and re-select ``switch_to``."""
    dev.send(CMD_DELETE_CONFIG, int(index), PT_WRITE)
    time.sleep(0.14)
    dev.send(CMD_SWITCH_MCU_CONFIG, int(switch_to), PT_WRITE)
    time.sleep(0.12 + settle_ms / 1000.0)


def rename_preset(dev: Any, index: int, name: str, settle_ms: int = 200) -> None:
    """Rename preset ``index``; the name may be up to 32 UTF-8 bytes."""
    words = encode_name_words(name)
    dev.send(CMD_RENAME_CONFIG_INDEX, int(index), PT_WRITE)
    time.sleep(0.05)
    for i, word in enumerate(words):
        dev.send(CMD_RENAME_CONFIG_NAME, word, PT_WRITE, total=len(words), cur=i)
    time.sleep(0.08 + settle_ms / 1000.0)
