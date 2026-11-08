"""HID transport handle for the DX5 II.

``Dx5ii`` opens the protocol interface (``MI_02``), sends frames and collects
replies.  Interpretation lives in :mod:`dx5ii.interpreter`; field encoding in
:mod:`dx5ii.fields`.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from .constants import (
    CMD_AGREEMENT_CONFIG,
    CMD_BLUETOOTH_STATE,
    CMD_CROSSFEED_OPTION_MASK,
    CMD_GET_SAMPLING,
    CMD_GET_SETTINGS,
    CMD_PCM_OPTION_MASK,
    CMD_PEQ_OPTION_MASK,
    CMD_PEQ_PREVIEW,
    CMD_PEQ_STATE,
    CMD_REMOTE_MUTE_MODE,
    CMD_SWITCH_MCU_CONFIG,
    CMD_OUTPUT,
    PID,
    PROTOCOL_INTERFACE,
    PT_READ,
    PT_WRITE,
    SETTINGS_MIN_WORDS,
    VID,
)
from .errors import Dx5iiError
from .fields import resolve_output
from .interpreter import (
    collect_words,
    interpret_config_upload,
    interpret_peq_preview,
    interpret_sampling,
    interpret_settings,
)
from .protocol import Frame, build_frame, parse_frame


def list_interfaces() -> list[dict[str, Any]]:
    """Enumerate every HID interface of the DX5 II.

    Each entry keeps the raw ``path`` bytes under ``path_bytes``, because
    ``hidapi`` 0.15 ``open_path()`` only accepts bytes.
    """
    import hid

    interfaces: list[dict[str, Any]] = []
    for info in hid.enumerate(VID, PID):
        raw_path = info.get("path", b"")
        if isinstance(raw_path, str):
            raw_path = raw_path.encode("utf-8", "surrogateescape")
        interfaces.append({
            "interface_number": info.get("interface_number"),
            "path": raw_path.decode("utf-8", "replace"),
            "path_bytes": raw_path,
            "product": info.get("product_string"),
            "manufacturer": info.get("manufacturer_string"),
            "serial": info.get("serial_number"),
            "usage_page": info.get("usage_page"),
            "usage": info.get("usage"),
            "release_number": info.get("release_number"),
        })
    return interfaces


class Dx5ii:
    """Context manager around the protocol interface.

    ``Dx5ii()`` picks the interface with ``interface_number == 2``;
    ``Dx5ii(path=...)`` selects an explicit HID path.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        import hid  # fail early with an actionable error

        self._hid = hid
        self._dev: Any = None
        self.raw_frames: list[dict[str, Any]] = []
        self.keep_raw: bool = False

        candidates = list_interfaces()
        if path:
            matches = [c for c in candidates if c["path"] == path]
            if not matches:
                raise Dx5iiError(f"no HID interface with path {path!r}")
            self.info: dict[str, Any] = matches[0]
        else:
            matches = [c for c in candidates
                       if c["interface_number"] == PROTOCOL_INTERFACE]
            if not matches:
                numbers = [c["interface_number"] for c in candidates]
                raise Dx5iiError(
                    "DX5 II protocol interface (MI_02) not found; "
                    f"{len(candidates)} candidate interface(s): {numbers}")
            self.info = matches[0]

        self._dev = hid.device()
        self._dev.open_path(self.info["path_bytes"])

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Close the HID handle; safe to call twice."""
        if self._dev is not None:
            try:
                self._dev.close()
            finally:
                self._dev = None

    def __enter__(self) -> "Dx5ii":
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.close()
        return False

    # -- transport ---------------------------------------------------------

    def send(
        self,
        cmd: int,
        data: int = 0,
        protocol: int = PT_READ,
        total: int = 1,
        cur: int = 1,
    ) -> None:
        """Write one frame; raises :class:`Dx5iiError` on a short write."""
        frame = build_frame(cmd, data, protocol, total, cur)
        written = self._dev.write(b"\x00" + frame)
        if written != len(frame) + 1:
            raise Dx5iiError(
                f"short HID write for cmd 0x{cmd:04X}: {written!r}")

    def read_for(self, ms: int, keep_raw: Optional[bool] = None) -> list[Frame]:
        """Collect parsed frames for ``ms`` milliseconds."""
        keep = self.keep_raw if keep_raw is None else keep_raw
        frames: list[Frame] = []
        deadline = time.time() + ms / 1000.0
        while time.time() < deadline:
            buf = self._dev.read(64, 25)
            if not buf:
                continue
            buf = bytes(buf)
            if not any(buf):
                continue
            if keep:
                self.raw_frames.append(
                    {"t": round(time.time(), 3), "hex": buf.hex()})
            parsed = parse_frame(buf)
            if parsed:
                frames.append(parsed)
        return frames

    # -- readers -----------------------------------------------------------

    def read_settings(self, wait_ms: int = 1600) -> list[Frame]:
        """``getSettings``: 52 frames of one ``uint32`` word each."""
        self.send(CMD_GET_SETTINGS, 0, PT_READ)
        return self.read_for(wait_ms)

    def read_sampling(self, wait_ms: int = 600) -> list[Frame]:
        """``getSampling``: one frame carrying the sample-rate code."""
        self.send(CMD_GET_SAMPLING, 0, PT_WRITE)
        return self.read_for(wait_ms)

    def read_peq_preview(self, wait_ms: int = 600) -> list[Frame]:
        """``peqPreviewState``: three frames (meta / hashes)."""
        self.send(CMD_PEQ_PREVIEW, 0, PT_READ)
        return self.read_for(wait_ms)

    def read_config_upload(self, wait_ms: int = 3000) -> list[Frame]:
        """``agreementConfig``: the EQ preset list. Needs a prior getSettings."""
        self.send(CMD_AGREEMENT_CONFIG, 1, PT_WRITE)
        return self.read_for(wait_ms)

    # -- writers -----------------------------------------------------------

    def set_output(self, value: Any, settle_ms: int = 300) -> Optional[dict[str, Any]]:
        """Switch the output port and return the settings read back after it.

        Note that every output port remembers its own volume and EQ preset, so
        the returned settings describe the *new* port.
        """
        raw = resolve_output(value)
        self.send(CMD_OUTPUT, raw, PT_WRITE)
        self.read_for(settle_ms)
        words = collect_words(self.read_settings(1600), min_total=SETTINGS_MIN_WORDS)
        return interpret_settings(words) if words else None

    def set_config_index(
        self, index: int, settle_ms: int = 700, verify: bool = True
    ) -> Optional[int]:
        """Select the active EQ preset by index.

        ``0x110E`` is the command that actually switches presets; ``0x1206``
        only reports the active one and is ignored when written.  Returns the
        index reported afterwards, or ``None`` when it cannot be read.
        """
        idx = int(index)
        if not 0 <= idx <= 0xFF:
            raise Dx5iiError(f"config index {index!r} out of range")
        self.send(CMD_SWITCH_MCU_CONFIG, idx, PT_WRITE)
        self.read_for(settle_ms)
        if not verify:
            return idx
        return self.current_config_index()

    def current_config_index(self) -> Optional[int]:
        """Read the active EQ preset index.

        The cheap path asks ``peqPreviewState``; the expensive one uploads the
        preset list.  The preview is only trustworthy while no preview is
        active.
        """
        preview = interpret_peq_preview(self.read_peq_preview(600))
        if preview and not preview.get("preview_active") \
                and preview.get("local_index") is not None:
            return preview["local_index"]
        self.read_settings(1600)
        return interpret_config_upload(self.read_config_upload(3000))["current_index"]

    # -- state assembly ----------------------------------------------------

    def snapshot(
        self, include_configs: bool = True, keep_raw: bool = False
    ) -> dict[str, Any]:
        """Read the full device state.

        ``include_configs`` adds the EQ preset list (about 3 extra seconds).
        ``getSettings`` always runs first: the device only answers
        ``agreementConfig`` while that session is established.
        """
        self.keep_raw = keep_raw
        self.raw_frames = []

        frames = self.read_settings(wait_ms=1600)

        result: dict[str, Any] = {
            "device": {
                "product": self.info.get("product"),
                "serial": self.info.get("serial"),
                "interface_number": self.info.get("interface_number"),
                "path": self.info.get("path"),
            },
            "settings": None,
            "sampling": None,
            "peq": None,
            "configs": None,
            "pushed": {},
        }

        words = collect_words(frames, min_total=SETTINGS_MIN_WORDS)
        if words:
            result["settings"] = interpret_settings(words)

        result["sampling"] = interpret_sampling(self.read_sampling())
        result["peq"] = interpret_peq_preview(self.read_peq_preview())

        pushed = result["pushed"]
        for frame in frames:
            cmd = frame["cmd"]
            if cmd == CMD_PEQ_STATE:
                pushed["peq_enabled"] = frame["data"] != 0
            elif cmd == CMD_PEQ_OPTION_MASK:
                pushed["peq_option_mask"] = frame["data"]
            elif cmd == CMD_PCM_OPTION_MASK:
                pushed["pcm_option_mask"] = frame["data"]
            elif cmd == CMD_CROSSFEED_OPTION_MASK:
                pushed["crossfeed_option_mask"] = frame["data"]
            elif cmd == CMD_BLUETOOTH_STATE:
                pushed["bluetooth_connected"] = frame["data"] != 0
            elif cmd == CMD_REMOTE_MUTE_MODE:
                pushed["remote_mute_mode"] = frame["data"]

        if include_configs:
            result["configs"] = interpret_config_upload(self.read_config_upload())

        if keep_raw:
            result["raw_frames"] = self.raw_frames
        return result


__all__ = ["Dx5ii", "Dx5iiError", "list_interfaces"]
