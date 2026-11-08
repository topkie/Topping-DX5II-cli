"""Wire format for the DX5 II HID protocol.

A frame is 16 bytes, ``"shifted-frame"`` layout with HID report id 0::

    22 33 <pt> <total> <cur> <cmdHi> <cmdLo> <d0> <d1> <d2> <d3> crcH crcL 66 77 00

Write: ``dev.write(b"\\x00" + frame)`` -- 17 bytes, the leading zero being the
report id.  Read: ``dev.read(64)`` -- 15 or 16 bytes, same layout, the trailing
byte padded with 0.  Multi-frame replies keep ``total > 1`` and carry one
``uint32`` payload word per frame.

The CRC is left at zero: the web client only fills it when a caller passes
``includeCrc``, and no command used here does.  The firmware accepts zero.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence, TypedDict

from .constants import PT_READ

FRAME_SIZE = 16
#: Bytes that must be present for a frame to be parseable from offset 0.
MIN_FRAME_BYTES = 15

_START = (0x22, 0x33)
_TRAILER = (0x66, 0x77)


class Frame(TypedDict):
    """One parsed inbound report."""

    protocol: int
    """:data:`dx5ii.constants.PT_WRITE` or ``PT_READ``."""
    total: int
    """Frame count of the logical message this frame belongs to."""
    index: int
    """Frame index inside the message (0-based, occasionally 1-based)."""
    cmd: int
    """Command number, e.g. ``0x710C``."""
    data: int
    """Unsigned 32-bit payload word."""


def build_frame(
    cmd: int,
    data: int = 0,
    protocol: int = PT_READ,
    total: int = 1,
    cur: int = 1,
) -> bytes:
    """Build the 16-byte on-the-wire frame (without the report id)."""
    frame = bytearray(FRAME_SIZE)
    frame[0], frame[1] = _START
    frame[2] = protocol & 0xFF
    frame[3] = total & 0xFF
    frame[4] = cur & 0xFF
    frame[5] = (cmd >> 8) & 0xFF
    frame[6] = cmd & 0xFF
    frame[7] = (data >> 24) & 0xFF
    frame[8] = (data >> 16) & 0xFF
    frame[9] = (data >> 8) & 0xFF
    frame[10] = data & 0xFF
    # frame[11:13] stay zero (crcH/crcL, see module docstring)
    frame[13], frame[14] = _TRAILER
    frame[15] = 0
    return bytes(frame)


def parse_frame(buf: bytes) -> Optional[Frame]:
    """Parse one received report, or return ``None``.

    Two layouts exist in the wild: trailer at ``[13]/[14]`` (no leading zero)
    and at ``[14]/[15]`` (the transport keeps the report id).  Both are
    accepted; the leading-zero form is only tried after the bare form fails.
    """
    buf = bytes(buf)
    for offset in (0, 1):
        end = offset + MIN_FRAME_BYTES
        if len(buf) < end:
            continue
        if (buf[offset], buf[offset + 1]) != _START:
            continue
        if (buf[offset + 13], buf[offset + 14]) != _TRAILER:
            continue
        return {
            "protocol": buf[offset + 2],
            "total": buf[offset + 3],
            "index": buf[offset + 4],
            "cmd": (buf[offset + 5] << 8) | buf[offset + 6],
            "data": (
                (buf[offset + 7] << 24)
                | (buf[offset + 8] << 16)
                | (buf[offset + 9] << 8)
                | buf[offset + 10]
            ),
        }
    return None


def decode_name(words: Iterable[int]) -> str:
    """Decode a zero-terminated UTF-8 string stored as little-endian words."""
    raw = bytearray()
    for word in words:
        raw += bytes((word & 0xFF, (word >> 8) & 0xFF,
                      (word >> 16) & 0xFF, (word >> 24) & 0xFF))
    end = raw.find(b"\x00")
    if end >= 0:
        raw = raw[:end]
    return raw.decode("utf-8", "replace").strip()


def volume_to_db(raw: int, step: str) -> float:
    """Convert the volume register to dB.

    ``half_db`` covers 0..198 in 0.5 dB steps, ``one_db`` covers 0..99 in
    1 dB steps.  Both are attenuations, so the result is negative.
    """
    limit = 99 if step == "one_db" else 198
    value = max(0, min(limit, int(raw)))
    divisor = 1.0 if step == "one_db" else 2.0
    return round(-value / divisor, 1)


def db_to_volume(db: float, step: str) -> int:
    """Inverse of :func:`volume_to_db`, clamped to the register range."""
    one_db = step == "one_db"
    limit = 99 if one_db else 198
    divisor = 1.0 if one_db else 2.0
    raw = int(round(-max(-99.0, min(0.0, float(db))) * divisor))
    return max(0, min(limit, raw))


def mask_to_items(mask: int, table: Sequence[str]) -> list[str]:
    """Expand a bitmask into the names of the bits that are set."""
    return [item for i, item in enumerate(table) if (mask >> i) & 1]


def format_firmware(release_number: Optional[int]) -> Optional[str]:
    """Render the USB ``bcdDevice`` value as a dotted firmware version.

    ``bcdDevice`` is binary-coded decimal: ``0x0258`` is 2.58, not 2.88.  The
    device reports the same value in settings words 45/46.  ``None`` is passed
    through because hidapi does not always expose the descriptor field.
    """
    if release_number is None:
        return None
    value = int(release_number)
    major = (value >> 8) & 0xFF
    minor = value & 0xFF
    major_dec = (major >> 4) * 10 + (major & 0xF)
    minor_dec = (minor >> 4) * 10 + (minor & 0xF)
    return "%d.%02d" % (major_dec, minor_dec)
