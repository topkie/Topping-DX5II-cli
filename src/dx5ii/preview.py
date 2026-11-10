"""Temporary (preview) PEQ on the device.

The device keeps one scratch filter set next to the stored presets.  A two step
handshake switches playback to it: the ``peqPreviewState`` meta says "active,
source = cloud/audition", then the per-band live-parameter commands fill the
scratch set.  Writing the meta back with ``active = 0`` returns to the stored
preset.

Only ``audition`` is offered here.  ``cloud`` means "the config the web page
pushed", which the CLI cannot reproduce: the device stores just two hashes, not
the cloud config itself.
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterable

from .bands import Band, encode_preamp
from .constants import BAND_TYPE_IDS, CMD_PEQ_PREVIEW, PT_WRITE
from .interpreter import interpret_peq_preview

__all__ = [
    "PREVIEW_SOURCES",
    "enter_preview",
    "leave_preview",
    "preview_state",
]

#: Source codes accepted by the meta word, minus ``cloud``.
PREVIEW_SOURCES = {"audition": 2}

#: The scratch set is filled one parameter at a time; sending faster than this
#: makes the device drop commands (the web page throttles the same way).
_BATCH = 10
_BATCH_SLEEP = 0.01
_PREAMP_COMMANDS = (0x9C01, 0x9C02, 0x9C03, 0x9C04)


def _fnv1a(text: str) -> int:
    value = 0x811C9DC5
    for char in text:
        value = ((value ^ ord(char)) * 0x01000193) & 0xFFFFFFFF
    return value


def _meta(active: bool, source: int, local_index: int) -> int:
    return ((1 if active else 0) << 24) | ((source & 0xFF) << 16) | ((local_index & 0xFF) << 8)


def _write_meta(dev: Any, meta: int, source_hash: int = 0, config_hash: int = 0) -> None:
    for index, word in enumerate((meta, source_hash, config_hash)):
        dev.send(CMD_PEQ_PREVIEW, word & 0xFFFFFFFF, PT_WRITE, total=3, cur=index)


def _band_words(preset: dict[str, Any]) -> Iterable[tuple[int, int]]:
    """Yield ``(cmd, data)`` for the preamp and the 11 stereo bands."""
    left = list(preset.get("bands_left") or [])
    right = list(preset.get("bands_right") or [])
    words = [
        (_PREAMP_COMMANDS[0], encode_preamp(preset.get("preamp_left") or 0.0)),
        (_PREAMP_COMMANDS[1], 1),
        (_PREAMP_COMMANDS[2], encode_preamp(preset.get("preamp_right") or 0.0)),
        (_PREAMP_COMMANDS[3], 1),
    ]
    for index in range(11):
        band_l = left[index] if index < len(left) else Band(enabled=False).as_dict()
        band_r = right[index] if index < len(right) else Band(enabled=False).as_dict()
        base = (0x9000 | ((index + 1) & 0xFF) << 8) & 0xFFFFFFFF
        words.extend([
            (base | 1, BAND_TYPE_IDS.get(band_l["type"], 1)),
            (base | 2, int(round(band_l["freq_hz"]))),
            (base | 3, int(round(10 * band_l["gain_db"])) & 0xFFFFFFFF),
            (base | 4, int(round(10000 * band_l["q"]))),
            (base | 5, 1 if band_l["enabled"] else 0),
            (base | 6, BAND_TYPE_IDS.get(band_r["type"], 1)),
            (base | 7, int(round(band_r["freq_hz"]))),
            (base | 8, int(round(10 * band_r["gain_db"])) & 0xFFFFFFFF),
            (base | 9, int(round(10000 * band_r["q"]))),
            (base | 10, 1 if band_r["enabled"] else 0),
        ])
    return words


def preview_state(dev: Any) -> dict[str, Any]:
    """Read and decode ``peqPreviewState``."""
    return interpret_peq_preview(dev.read_peq_preview()) or {}


def enter_preview(dev: Any, preset: dict[str, Any],
                  source: str = "audition") -> dict[str, Any]:
    """Play ``preset`` from the scratch set until :func:`leave_preview`.

    ``preset`` is one entry of :func:`dx5ii.peq.read_presets`.
    """
    code = PREVIEW_SOURCES.get(source)
    if code is None:
        raise ValueError("source must be one of %s" % ", ".join(sorted(PREVIEW_SOURCES)))

    # The meta keeps the currently stored slot as "what to come back to".
    stored = dev.current_config_index()
    local = stored if stored is not None else 0
    source_hash = _fnv1a(source)
    config_hash = _fnv1a(json.dumps(preset.get("bands_left") or [], sort_keys=True))

    _write_meta(dev, _meta(True, code, local), source_hash, config_hash)
    time.sleep(0.06)
    for index, (cmd, data) in enumerate(_band_words(preset)):
        dev.send(cmd, data & 0xFFFFFFFF, PT_WRITE)
        if index % _BATCH == _BATCH - 1:
            time.sleep(_BATCH_SLEEP)
    time.sleep(0.02)
    _write_meta(dev, _meta(True, code, local), source_hash, config_hash)
    time.sleep(0.04)
    return preview_state(dev)


def leave_preview(dev: Any) -> dict[str, Any]:
    """Return playback to the stored preset."""
    stored = dev.current_config_index()
    local = stored if stored is not None else 0
    _write_meta(dev, _meta(False, 0, local))
    time.sleep(0.04)
    return preview_state(dev)
