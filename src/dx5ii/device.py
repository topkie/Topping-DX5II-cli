"""HID transport handle for the DX5 II.

``Dx5ii`` opens the protocol interface (``MI_02``), sends frames and collects
replies.  Interpretation lives in :mod:`dx5ii.interpreter`; field encoding in
:mod:`dx5ii.fields`; the "when is a reply whole" rule in :mod:`dx5ii.stream`.

Replies are timed by their own bounds, not by a stopwatch: a multi-frame answer
is complete at ``curFrame == totalFrameLen - 1``, and the preset upload also
announces its block count in a header, so nothing here has to wait out a fixed
window.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Final, Mapping, Optional

from .constants import (
    CMD_AGREEMENT_CONFIG,
    CMD_BLUETOOTH_STATE,
    CMD_CONFIG_BLOCK,
    CMD_CROSSFEED_OPTION_MASK,
    CMD_CURRENT_CONFIG,
    CMD_GET_SAMPLING,
    CMD_GET_SETTINGS,
    CMD_PCM_OPTION_MASK,
    CMD_PEQ_OPTION_MASK,
    CMD_PEQ_PREVIEW,
    CMD_PEQ_STATE,
    CMD_REMOTE_MUTE_MODE,
    CMD_SWITCH_MCU_CONFIG,
    CMD_OUTPUT,
    CONFIG_BLOCK_WORDS,
    CONFIG_HEADER_FRAMES,
    MAX_CONFIG_BLOCKS,
    PEQ_PREVIEW_FRAMES,
    PID,
    PROTOCOL_INTERFACE,
    PT_READ,
    PT_WRITE,
    REMOTE_MUTE_MODES,
    SETTINGS_FRAME_TOTAL,
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
    resolve_playing,
)
from .protocol import Frame, build_frame, format_firmware, parse_frame
from .stream import ReadSession, ReadState, Shape, StreamCollector

#: Give up on an answer that has started but stopped arriving for this long.
DEFAULT_IDLE_MS: Final = 250
#: Pushed frames (PEQ switch, option masks, remote-mute mode) trail the
#: ``getSettings`` reply by up to ~40 ms on firmware 2.58; keep reading briefly
#: after the reply itself is complete so they are not lost.
PUSH_SETTLE_MS: Final = 60

#: ``totalFrameLen`` of the single-frame pushes decoded into ``pushed``.
_PUSH_SHAPES: Final[tuple[Shape, ...]] = (
    (CMD_PEQ_STATE, 1),
    (CMD_PEQ_OPTION_MASK, 1),
    (CMD_PCM_OPTION_MASK, 1),
    (CMD_CROSSFEED_OPTION_MASK, 1),
    (CMD_BLUETOOTH_STATE, 1),
    (CMD_REMOTE_MUTE_MODE, 1),
)

_SETTINGS_SHAPE: Final[Shape] = (CMD_GET_SETTINGS, SETTINGS_FRAME_TOTAL)
_SAMPLING_SHAPE: Final[Shape] = (CMD_GET_SAMPLING, 1)
_PREVIEW_SHAPE: Final[Shape] = (CMD_PEQ_PREVIEW, PEQ_PREVIEW_FRAMES)
_UPLOAD_HEADER: Final[Shape] = (CMD_CONFIG_BLOCK, CONFIG_HEADER_FRAMES)
_UPLOAD_BLOCK: Final[Shape] = (CMD_CONFIG_BLOCK, CONFIG_BLOCK_WORDS)
_UPLOAD_CURRENT: Final[Shape] = (CMD_CURRENT_CONFIG, 1)


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
        #: Terminal state of the most recent :meth:`_collect` session.
        self.last_read_state: Optional[ReadState] = None

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

    def _parse(self, buf: Any) -> Optional[Frame]:
        """Parse one inbound report, recording it when ``keep_raw`` is on."""
        raw = bytes(buf)
        if not any(raw):
            return None
        if self.keep_raw:
            self.raw_frames.append({"t": round(time.time(), 3), "hex": raw.hex()})
        return parse_frame(raw)

    def _collect(
        self,
        shapes: Mapping[Shape, int],
        timeout_ms: int,
        idle_ms: int = DEFAULT_IDLE_MS,
        settle_ms: int = 0,
        extra: tuple[Shape, ...] = (),
        hook: Optional[Callable[[StreamCollector], None]] = None,
    ) -> StreamCollector:
        """Read frames until the session decides the reply is over.

        All decisions live in :class:`~dx5ii.stream.ReadSession`; this method
        only pumps the device and hands over what came back.  ``timeout_ms`` is
        the hard ceiling, ``idle_ms`` abandons a reply that stalled, and
        ``settle_ms`` keeps reading briefly after success so trailing push
        frames still land in ``collector.frames``.  ``extra`` shapes are
        recorded but not required.
        """
        collector = StreamCollector(shapes, extra=extra)
        session = ReadSession(collector, timeout_ms=timeout_ms,
                              idle_ms=idle_ms, settle_ms=settle_ms)
        while session.running:
            buf = self._dev.read(64, session.poll_ms)
            frame = self._parse(buf) if buf else None
            session.step(frame)
            if hook is not None:
                hook(collector)
        self.last_read_state = session.state
        return collector

    def read_for(self, ms: int, keep_raw: Optional[bool] = None) -> list[Frame]:
        """Collect parsed frames for ``ms`` milliseconds.

        Only for settling writes, where no reply shape is known in advance.
        """
        keep = self.keep_raw if keep_raw is None else keep_raw
        frames: list[Frame] = []
        deadline = time.monotonic() + ms / 1000.0
        while time.monotonic() < deadline:
            buf = self._dev.read(64, 25)
            if not buf:
                continue
            raw = bytes(buf)
            if not any(raw):
                continue
            if keep:
                self.raw_frames.append(
                    {"t": round(time.time(), 3), "hex": raw.hex()})
            parsed = parse_frame(raw)
            if parsed:
                frames.append(parsed)
        return frames

    def wait_for_frame(
        self,
        cmd: int,
        match: Optional[Callable[[Frame], bool]] = None,
        timeout_ms: int = 400,
        idle_ms: Optional[int] = None,
    ) -> bool:
        """Wait until the device pushes a frame of ``cmd`` that ``match`` accepts.

        The firmware announces the result of a write on its own: ``0x1206``
        follows ``0x110E``/``0x110A``/``0x110D``.  Waiting for that frame ends
        the write as soon as the device has acted instead of sleeping a fixed
        span.

        Only frames of ``cmd`` count as progress.  The device streams
        ``0x7130``/``0x7131`` telemetry at roughly 490 frames per second, and
        letting those reset the idle timer would hide a stalled reply -- the
        same trap :class:`~dx5ii.stream.ReadSession` had.

        Returns ``True`` when the frame arrived, ``False`` on timeout or when
        ``cmd`` went quiet for ``idle_ms``.
        """
        deadline = time.monotonic() + timeout_ms / 1000.0
        last_progress = time.monotonic()
        idle_s = (timeout_ms if idle_ms is None else idle_ms) / 1000.0
        while True:
            now = time.monotonic()
            if now >= deadline or now - last_progress > idle_s:
                return False
            buf = self._dev.read(64, 10)
            if not buf:
                continue
            frame = self._parse(buf)
            if frame is None or frame["cmd"] != cmd:
                continue
            last_progress = time.monotonic()
            if match is None or match(frame):
                return True

    # -- readers -----------------------------------------------------------

    def read_core(
        self, include_sampling: bool = True, include_preview: bool = True
    ) -> StreamCollector:
        """Read settings, sampling and preview state in one window.

        The three requests are independent, so they go out together and the
        reply shape decides when to stop -- the same order the vendor web page
        uses on connect (``getSettings`` then ``getSampling`` then
        ``peqPreviewState``).
        """
        shapes: dict[Shape, int] = {_SETTINGS_SHAPE: 1}
        if include_sampling:
            shapes[_SAMPLING_SHAPE] = 1
        if include_preview:
            shapes[_PREVIEW_SHAPE] = 1

        self.send(CMD_GET_SETTINGS, 0, PT_READ)
        if include_sampling:
            self.send(CMD_GET_SAMPLING, 0, PT_WRITE)
        if include_preview:
            self.send(CMD_PEQ_PREVIEW, 0, PT_READ)

        return self._collect(
            shapes,
            timeout_ms=1500,
            settle_ms=PUSH_SETTLE_MS,
            extra=_PUSH_SHAPES,
        )

    def read_settings(self, wait_ms: Optional[int] = None) -> list[Frame]:
        """``getSettings``: 52 word frames, plus the pushes that follow them."""
        self.send(CMD_GET_SETTINGS, 0, PT_READ)
        collector = self._collect(
            {_SETTINGS_SHAPE: 1},
            timeout_ms=wait_ms if wait_ms is not None else 1200,
            settle_ms=PUSH_SETTLE_MS,
            extra=_PUSH_SHAPES,
        )
        return collector.frames

    def read_sampling(self, wait_ms: Optional[int] = None) -> list[Frame]:
        """``getSampling``: one frame carrying the sample-rate code."""
        self.send(CMD_GET_SAMPLING, 0, PT_WRITE)
        collector = self._collect(
            {_SAMPLING_SHAPE: 1},
            timeout_ms=wait_ms if wait_ms is not None else 600,
        )
        return collector.frames

    def read_peq_preview(self, wait_ms: Optional[int] = None) -> list[Frame]:
        """``peqPreviewState``: three frames (meta / hashes)."""
        self.send(CMD_PEQ_PREVIEW, 0, PT_READ)
        collector = self._collect(
            {_PREVIEW_SHAPE: 1},
            timeout_ms=wait_ms if wait_ms is not None else 600,
        )
        return collector.frames

    def read_config_upload(self, wait_ms: Optional[int] = None) -> list[Frame]:
        """``agreementConfig``: the EQ preset list. Needs a prior getSettings.

        The reply opens with a two-frame header: the preset-block count and the
        ``TPDZ`` magic.  The count is applied to the collector as soon as the
        header lands, so the read stops after the last announced block.
        """
        def apply_header(collector: StreamCollector) -> None:
            headers = collector.values(_UPLOAD_HEADER)
            if not headers:
                return
            announced = int(headers[0][0])
            if 0 < announced <= MAX_CONFIG_BLOCKS:
                collector.require(_UPLOAD_BLOCK, announced)

        self.send(CMD_AGREEMENT_CONFIG, 1, PT_WRITE)
        collector = self._collect(
            {_UPLOAD_HEADER: 1, _UPLOAD_BLOCK: MAX_CONFIG_BLOCKS,
             _UPLOAD_CURRENT: 1},
            timeout_ms=wait_ms if wait_ms is not None else 2500,
            hook=apply_header,
        )
        return collector.frames

    # -- writers -----------------------------------------------------------

    def set_output(self, value: Any, settle_ms: int = 300) -> Optional[dict[str, Any]]:
        """Switch the output port and return the settings read back after it.

        Note that every output port remembers its own volume and EQ preset, so
        the returned settings describe the *new* port.
        """
        raw = resolve_output(value)
        self.send(CMD_OUTPUT, raw, PT_WRITE)
        self.read_for(settle_ms)
        words = collect_words(self.read_settings(), min_total=SETTINGS_MIN_WORDS)
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
        # The device pushes 0x1206 once the switch has happened (32-38 ms in
        # practice).  When it stays quiet the switch may still have taken
        # effect, so the read-back below -- not the silence -- decides.
        self.wait_for_frame(
            CMD_CURRENT_CONFIG, lambda frame: frame["data"] == idx,
            timeout_ms=min(settle_ms, 250))
        if not verify:
            return idx
        return self.current_config_index()

    def current_config_index(self) -> Optional[int]:
        """Read the stored EQ preset index.

        This is the slot the device has selected, not necessarily what it is
        playing: while a cloud config is active the slot is stored but silent.
        The cheap path asks ``peqPreviewState``; the expensive one uploads the
        preset list.  The preview is only trustworthy while no preview is
        active.
        """
        preview = interpret_peq_preview(self.read_peq_preview())
        if preview and not preview.get("preview_active") \
                and preview.get("local_index") is not None:
            return preview["local_index"]
        self.read_settings()
        return interpret_config_upload(self.read_config_upload())["stored_index"]

    # -- state assembly ----------------------------------------------------

    def snapshot(
        self,
        include_configs: bool = True,
        keep_raw: bool = False,
        light: bool = False,
    ) -> dict[str, Any]:
        """Read the full device state.

        ``include_configs`` adds the EQ preset list (about 1 s).  ``light``
        skips sampling and preview state and reads settings only, for callers
        that need a single field.  ``getSettings`` always runs first: the
        device only answers ``agreementConfig`` while that session is
        established.
        """
        self.keep_raw = keep_raw
        self.raw_frames = []

        core = self.read_core(
            include_sampling=not light, include_preview=not light)
        frames = core.frames

        result: dict[str, Any] = {
            "device": {
                "product": self.info.get("product"),
                "serial": self.info.get("serial"),
                "interface_number": self.info.get("interface_number"),
                "path": self.info.get("path"),
                "release_number": self.info.get("release_number"),
                "firmware": format_firmware(self.info.get("release_number")),
            },
            "settings": None,
            "sampling": None,
            "peq": None,
            "configs": None,
            "pushed": _interpret_pushed(frames),
        }

        words = core.values(_SETTINGS_SHAPE)
        if words and len(words[0]) >= SETTINGS_MIN_WORDS:
            result["settings"] = interpret_settings(words[0])

        if not light:
            result["sampling"] = interpret_sampling(frames)
            result["peq"] = interpret_peq_preview(frames)

        if include_configs:
            configs = interpret_config_upload(self.read_config_upload())
            result["configs"] = {"playing": resolve_playing(configs, result["peq"]),
                                 **configs}

        if keep_raw:
            result["raw_frames"] = self.raw_frames
        return result


def _interpret_pushed(frames: list[Frame]) -> dict[str, Any]:
    """Decode the single-frame pushes that accompany ``getSettings``."""
    pushed: dict[str, Any] = {}
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
            value = frame["data"]
            pushed["remote_mute_mode_raw"] = value
            pushed["remote_mute_mode"] = (
                REMOTE_MUTE_MODES[value]
                if 0 <= value < len(REMOTE_MUTE_MODES) else value)
    return pushed


__all__ = ["Dx5ii", "Dx5iiError", "list_interfaces"]
