"""EQ preset blocks: the 78-word payload the device stores per preset.

Layout (verified 2026-11-09 against the five factory presets; the
``IER-M7.Harman`` block matches ``harman_10band.txt`` exactly):

    word 0..3     preset name, UTF-8, little-endian, NUL padded
    word 4        enabled flag (1)
    word 5        content hash / revision
    word 6        enabled flag (1)
    word 7        content hash / revision (same as word 5)
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

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from .constants import (
    BAND_TYPE_IDS,
    BAND_TYPES,
    BANDS_PER_CHANNEL,
    BAND_WORDS,
    CONFIG_BLOCK_WORDS,
    CONFIG_HEAD_WORDS,
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
        kind = {"PK": "PK", "LS": "LSC", "HS": "HSC"}.get(self.type, "PK")
        state = "ON" if self.enabled else "OFF"
        return (
            f"Filter {index}: {state} {kind} Fc {self.freq_hz:g} Hz "
            f"Gain {self.gain_db:+g} dB Q {self.q:g}"
        )


SPARE_BAND = Band(enabled=False, type="PK", gain_db=0.0, freq_hz=DEFAULT_FREQ)
SPARE_WORDS: list[int] = [0x00000100, DEFAULT_FREQ, round(DEFAULT_Q * 10000)]


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
_APO_KIND = {"PK": "PK", "LSC": "LS", "LS": "LS", "HSC": "HS", "HS": "HS"}


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
        if _APO_FILTER_LINE.match(line):
            if not sections:
                sections.append((None, []))
            sections[-1][1].append(line)
    return sections


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
    one of the device's three band types, or when a section holds more bands
    than the device stores.  Nothing is skipped silently.

    Shelf filters without an explicit ``Q`` fall back to the device default.
    """
    _, lines = _choose_section(_apo_sections(text), device)
    bands: list[Band] = []
    for line in lines:
        match = _APO_FILTER.match(line)
        if not match:
            raise Dx5iiError("unsupported APO filter line: %r" % line)
        kind = match.group("kind").upper()
        if kind not in _APO_KIND:
            raise Dx5iiError(
                "APO filter type %s cannot be stored on the device (only PK, "
                "LS and HS exist): %r" % (kind, line))
        gain = match.group("gain")
        if gain is None:
            raise Dx5iiError("APO filter line has no Gain: %r" % line)
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
        "hash": words[5],
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

    words[4:8] = list(head)[:4] if head else [1, 0, 1, 0]

    for channel, bands in enumerate((bands_left, bands_right)):
        base = CONFIG_HEAD_WORDS + channel * BANDS_PER_CHANNEL * BAND_WORDS
        for i, band in enumerate(_pad_bands(bands)):
            words[base + i * BAND_WORDS: base + (i + 1) * BAND_WORDS] = encode_band(band)
    return words


__all__ = [
    "Band",
    "SPARE_BAND",
    "SPARE_WORDS",
    "decode_band",
    "decode_bands",
    "decode_block",
    "encode_band",
    "encode_block",
    "encode_name_words",
    "parse_apo_filters",
]
