"""EQ preset blocks: the 78-word payload the device stores per preset.

Layout (verified 2026-11-09 against the five factory presets; the
``IER-M7.Harman`` block matches ``harman_10band.txt`` exactly):

    word 0..3     preset name, UTF-8, little-endian, NUL padded
    word 4        left channel enabled flag (1)
    word 5        left preamp, fixed point: round(0x02000000 * 10**(dB/20))
    word 6        right channel enabled flag (1)
    word 7        right preamp, same encoding

The preamp words are the plain gain applied before the bands, 0.1 dB steps
over ±12 dB: ``0x02000000`` is 0 dB and ``0x01009B95`` is -6.0 dB.  The
web UI writes the same value through ``0x9C01`` / ``0x9C03`` when previewing
a config that is not stored yet.
    word 8..40    left channel, 11 bands x 3 words
    word 41..73   right channel, 11 bands x 3 words
    word 74..77   zero

Each band is three words::

    packed = enabled | type << 8 | gain_tenth_db << 16   (gain is signed 8-bit)
    freq_hz
    q * 10000

``type`` is 1 = PK, 4 = LS (low shelf), 5 = HS (high shelf); 3 also appears in
the factory ``SPEAKER`` preset and is kept verbatim.  Band 11 is the device's
spare slot: disabled, 632 Hz, Q 0.707.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .constants import (
    BAND_TYPE_IDS,
    BAND_TYPES,
    BANDS_PER_CHANNEL,
    BAND_WORDS,
    CONFIG_BLOCK_WORDS,
    CONFIG_HEAD_WORDS,
    PREAMP_MAX_DB,
    PREAMP_MIN_DB,
    PREAMP_UNITY,
)
from .errors import Dx5iiError
from .protocol import decode_name

DEFAULT_Q = 0.707
DEFAULT_FREQ = 632
MIN_FREQ = 20
MAX_FREQ = 20000
MIN_Q = 0.1
MAX_Q = 15.0
MAX_GAIN_DB = 12.0

#: Renames carry four name frames; the web client truncates names to 14 bytes.
CONFIG_NAME_WORDS = 4
DEVICE_NAME_MAX_BYTES = 14


@dataclass
class Band:
    """One parametric EQ band."""

    enabled: bool = True
    type: str = "PK"
    gain_db: float = 0.0
    freq_hz: float = DEFAULT_FREQ
    q: float = DEFAULT_Q

    def __post_init__(self) -> None:
        self.gain_db = max(-MAX_GAIN_DB, min(MAX_GAIN_DB, round(self.gain_db, 1)))
        self.freq_hz = float(max(MIN_FREQ, min(MAX_FREQ, round(self.freq_hz))))
        self.q = max(MIN_Q, min(MAX_Q, round(self.q, 4)))

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "type": self.type,
            "gain_db": self.gain_db,
            "freq_hz": self.freq_hz,
            "q": self.q,
        }

    def to_ap_filter(self, index: int) -> str:
        """Render as an Equalizer APO line (``PK``/``LSC``/``HSC``)."""
        kind = {"PK": "PK", "LS": "LSC", "HS": "HSC",
                "LP": "LP", "HP": "HP"}.get(self.type, "PK")
        state = "ON" if self.enabled else "OFF"
        return (
            f"Filter {index}: {state} {kind} Fc {self.freq_hz:g} Hz "
            f"Gain {self.gain_db:+g} dB Q {self.q:g}"
        )


SPARE_BAND = Band(enabled=False, type="PK", gain_db=0.0, freq_hz=DEFAULT_FREQ)
SPARE_WORDS: list[int] = [0x00000100, DEFAULT_FREQ, round(DEFAULT_Q * 10000)]


def encode_preamp(db: float) -> int:
    """Encode a preamp gain as the fixed-point word the block stores."""
    clamped = max(PREAMP_MIN_DB, min(PREAMP_MAX_DB, db))
    return int(round(PREAMP_UNITY * 10.0 ** (clamped / 20.0))) & 0xFFFFFFFF


def decode_preamp(word: int) -> float:
    """Decode a preamp word back to dB, rounded to the stored 0.1 dB step.

    A zero word means "not set" -- the block stores it for a channel that is
    disabled -- and reads as 0.0 dB, the same fallback the web page uses.
    """
    if word <= 0:
        return 0.0
    return round(20.0 * math.log10(word / PREAMP_UNITY), 1)


def _signed_byte(value: int) -> int:
    return value - 256 if value & 0x80 else value


def decode_band(packed: int, freq: int, q: int) -> Band:
    type_id = (packed >> 8) & 0xFF
    return Band(
        enabled=bool(packed & 0xFF),
        type=BAND_TYPES.get(type_id, f"type{type_id}"),
        gain_db=_signed_byte((packed >> 16) & 0xFF) / 10.0,
        freq_hz=float(freq),
        q=q / 10000.0,
    )


def encode_band(band: Band) -> list[int]:
    type_id = BAND_TYPE_IDS.get(band.type, 1)
    gain = max(-128, min(127, int(round(band.gain_db * 10)))) & 0xFF
    packed = (gain << 16) | (type_id << 8) | (1 if band.enabled else 0)
    return [
        packed & 0xFFFFFFFF,
        int(round(band.freq_hz)),
        int(round(band.q * 10000)),
    ]


_APO_DEVICE = re.compile(r"^Device\s*:\s*(?P<name>.+?)\s*$", re.IGNORECASE)
_APO_FILTER_LINE = re.compile(r"^Filter\b", re.IGNORECASE)
_APO_FILTER = re.compile(
    r"^Filter(?:\s+\d+)?\s*:\s*(?P<state>ON|OFF)\s+(?P<kind>[A-Za-z]+)"
    r"\s+Fc\s+(?P<freq>[0-9.]+)\s*Hz"
    r"(?:\s+Gain\s+(?P<gain>[+-]?[0-9.]+)\s*dB)?"
    r"(?:\s+Q\s+(?P<q>[0-9.]+))?",
    re.IGNORECASE,
)
_APO_KIND = {"PK": "PK", "LSC": "LS", "LS": "LS", "HSC": "HS", "HS": "HS",
             "LP": "LP", "HP": "HP"}


def _apo_sections(text: str) -> list[tuple[str | None, list[str]]]:
    """Group the ``Filter:`` lines of ``text`` under their ``Device:`` names."""
    sections: list[tuple[str | None, list[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        device = _APO_DEVICE.match(line)
        if device:
            sections.append((device.group("name").strip(), []))
            continue
        if not sections:
            sections.append((None, []))
        sections[-1][1].append(line)
    return sections


_APO_PREAMP = re.compile(
    r"^Preamp\s*:\s*(?P<db>[+-]?[0-9.]+)\s*dB", re.IGNORECASE)


def _choose_section(
    sections: list[tuple[str | None, list[str]]],
    device: str | None,
) -> tuple[str | None, list[str]]:
    named = [(name, lines) for name, lines in sections if name is not None]
    if not named:
        # No ``Device:`` line at all: the file describes one device.
        return sections[0] if sections else (None, [])
    if device is not None:
        wanted = device.strip().casefold()
        hits = [entry for entry in named if entry[0].casefold() == wanted]
        if not hits:
            found = ", ".join(repr(name) for name, _ in named) or "none"
            raise Dx5iiError(
                "no 'Device: %s' section in the APO config (found: %s)"
                % (device, found))
        if len(hits) > 1:
            raise Dx5iiError(
                "the APO config has %d 'Device: %s' sections; keep only one"
                % (len(hits), device))
        return hits[0]
    if len(named) > 1:
        found = ", ".join(repr(name) for name, _ in named)
        raise Dx5iiError(
            "the APO config has %d Device sections (%s); pass device= to "
            "pick one" % (len(named), found))
    return named[0]


def parse_apo_filters(text: str, device: str | None = None) -> list[Band]:
    """Parse the ``Filter:`` lines of one ``Device:`` section.

    Equalizer APO writes unnumbered lines (``Filter: ON PK Fc ...``); a
    numbered ``Filter N:`` from an older export is accepted too.  Only the
    section named ``device`` is read; a single unnamed section is used when
    the file carries no ``Device:`` line.

    Raises ``Dx5iiError`` when the requested section is missing, when the file
    has several sections and none was named, when a line cannot be mapped to
    one of the device's band types, or when a section holds more bands
    than the device stores.  Nothing is skipped silently.

    Shelf filters without an explicit ``Q`` fall back to the device default.
    ``LP`` / ``HP`` lines carry no gain in Equalizer APO and are written as
    0 dB; every other type needs an explicit ``Gain``.
    """
    _, lines = _choose_section(_apo_sections(text), device)
    bands: list[Band] = []
    for line in lines:
        if not _APO_FILTER_LINE.match(line):
            continue
        match = _APO_FILTER.match(line)
        if not match:
            raise Dx5iiError("unsupported APO filter line: %r" % line)
        kind = match.group("kind").upper()
        if kind not in _APO_KIND:
            raise Dx5iiError(
                "APO filter type %s cannot be stored on the device (PK, LP, "
                "HP, LS and HS exist): %r" % (kind, line))
        gain = match.group("gain")
        if gain is None:
            # Equalizer APO writes pass filters without a gain field:
            # ``Filter: ON HP Fc 90 Hz``.  Everything else needs one.
            if _APO_KIND[kind] not in ("LP", "HP"):
                raise Dx5iiError("APO filter line has no Gain: %r" % line)
            gain = "0"
        q = match.group("q")
        bands.append(Band(
            enabled=match.group("state").upper() == "ON",
            type=_APO_KIND[kind],
            gain_db=float(gain),
            freq_hz=float(match.group("freq")),
            q=float(q) if q else DEFAULT_Q,
        ))
    if len(bands) > BANDS_PER_CHANNEL:
        raise Dx5iiError(
            "the APO section has %d filters but the device stores %d per "
            "channel" % (len(bands), BANDS_PER_CHANNEL))
    return bands


def parse_apo_preamp(text: str, device: str | None = None) -> float | None:
    """Return the ``Preamp:`` gain of the selected section, in dB.

    ``None`` means the section carries no preamp line, which is different
    from a preamp of 0 dB.
    """
    _, lines = _choose_section(_apo_sections(text), device)
    for line in lines:
        match = _APO_PREAMP.match(line)
        if match:
            return float(match.group("db"))
    return None


def encode_name_words(name: str) -> list[int]:
    """Encode a preset name as the four UTF-8 words used by the rename command.

    The device accepts exactly four name frames per rename.  Truncation stops
    on a UTF-8 character boundary so a multi-byte name is never cut in half.
    """
    raw = name.encode("utf-8")[:DEVICE_NAME_MAX_BYTES]
    while raw:
        try:
            raw.decode("utf-8")
            break
        except UnicodeDecodeError:
            raw = raw[:-1]
    padded = raw.ljust(CONFIG_NAME_WORDS * 4, b"\x00")
    return [int.from_bytes(padded[i * 4:(i + 1) * 4], "little")
            for i in range(CONFIG_NAME_WORDS)]


def decode_bands(words: Sequence[int], channel: int) -> list[Band]:
    base = CONFIG_HEAD_WORDS + channel * BANDS_PER_CHANNEL * BAND_WORDS
    out: list[Band] = []
    for i in range(BANDS_PER_CHANNEL):
        offset = base + i * BAND_WORDS
        out.append(decode_band(words[offset], words[offset + 1], words[offset + 2]))
    return out


def decode_block(words: Sequence[int]) -> dict:
    """Decode one 78-word preset block."""
    if len(words) < CONFIG_BLOCK_WORDS:
        raise ValueError(f"need {CONFIG_BLOCK_WORDS} words, got {len(words)}")
    return {
        "name": decode_name(list(words[0:4])),
        "enabled": bool(words[4]),
        "enabled_left": bool(words[4]),
        "enabled_right": bool(words[6]),
        "preamp_left": decode_preamp(words[5]),
        "preamp_right": decode_preamp(words[7]),
        "bands_left": decode_bands(words, 0),
        "bands_right": decode_bands(words, 1),
    }


def _pad_bands(bands: Iterable[Band]) -> list[Band]:
    items = list(bands)[:BANDS_PER_CHANNEL]
    while len(items) < BANDS_PER_CHANNEL:
        items.append(SPARE_BAND)
    return items


def encode_block(
    name: str,
    bands_left: Iterable[Band],
    bands_right: Iterable[Band],
    head: Sequence[int] | None = None,
) -> list[int]:
    """Build a 78-word preset block ready for ``CMD_DOWNLOAD_CONFIG``.

    ``head`` supplies words 4..7 (enabled flag + hash, twice).  When omitted a
    zero hash is sent; the device fills it in on save.
    """
    words = [0] * CONFIG_BLOCK_WORDS
    raw = name.encode("utf-8")[:16].ljust(16, b"\x00")
    for i in range(4):
        words[i] = int.from_bytes(raw[i * 4:(i + 1) * 4], "little")

    words[4:8] = list(head)[:4] if head else [1, PREAMP_UNITY, 1, PREAMP_UNITY]

    for channel, bands in enumerate((bands_left, bands_right)):
        base = CONFIG_HEAD_WORDS + channel * BANDS_PER_CHANNEL * BAND_WORDS
        for i, band in enumerate(_pad_bands(bands)):
            words[base + i * BAND_WORDS: base + (i + 1) * BAND_WORDS] = encode_band(band)
    return words


#: The four live-parameter commands that carry the preamp gain and the
#: per-channel enable flag, in left/right order.
PREAMP_COMMANDS: tuple[int, int, int, int] = (0x9C01, 0x9C02, 0x9C03, 0x9C04)


def live_words(preset: dict[str, Any]) -> list[tuple[int, int]]:
    """Build the 114 ``(cmd, data)`` writes that fill the device's live PEQ.

    The device has a single filter set for playback.  Written without a
    preview meta first, these commands change the playing preset in place;
    written while ``peqPreviewState`` is active, they fill the scratch set
    instead and nothing is stored.  Four preamp words come first, then eleven
    bands of five words for each channel.
    """
    left = list(preset.get("bands_left") or [])
    right = list(preset.get("bands_right") or [])
    words = [
        (PREAMP_COMMANDS[0], encode_preamp(preset.get("preamp_left") or 0.0)),
        (PREAMP_COMMANDS[1], 1 if preset.get("enabled_left", True) else 0),
        (PREAMP_COMMANDS[2], encode_preamp(preset.get("preamp_right") or 0.0)),
        (PREAMP_COMMANDS[3], 1 if preset.get("enabled_right", True) else 0),
    ]
    for index in range(BANDS_PER_CHANNEL):
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


__all__ = [
    "Band",
    "PREAMP_COMMANDS",
    "SPARE_BAND",
    "SPARE_WORDS",
    "decode_band",
    "decode_bands",
    "decode_block",
    "encode_band",
    "encode_block",
    "encode_name_words",
    "live_words",
    "parse_apo_filters",
    "parse_apo_preamp",
]
