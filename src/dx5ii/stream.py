"""State machine for the device's multi-frame replies.

Every inbound frame carries its own bounds: byte 3 is ``totalFrameLen`` and
byte 4 is ``curFrame``.  A run of frames is therefore complete at
``curFrame == totalFrameLen - 1``, and waiting for a fixed time is unnecessary.

:class:`StreamCollector` models exactly that rule.  It knows nothing about the
USB device, so it is unit-tested with constructed frames and reused by the
transport loop in :mod:`dx5ii.device`.  Interpretation stays in
:mod:`dx5ii.interpreter`: the collector only decides *when* a reply is whole.

The rule matches the vendor web client (``595e76df36318426.js``, module
16004): a run starts at frame 0, indices must be contiguous, an out-of-order
frame drops the partial run, and the run is handed over once the last index
arrives.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Callable, Final, Iterable, Mapping, Optional

from .protocol import Frame

#: Key of one reply shape: ``(command, totalFrameLen)``.
Shape = tuple[int, int]


class StreamCollector:
    """Collect frame runs until every requested shape is satisfied.

    ``shapes`` maps a :data:`Shape` to the number of complete runs wanted.  A
    shape may be raised later with :meth:`require` -- the preset upload
    announces its block count in a header frame, so the count is only known
    once the first frames arrive.

    ``frames`` keeps every parsed frame of a watched shape in arrival order, so
    the existing interpreters can keep working on the raw sequence.
    """

    def __init__(
        self,
        shapes: Mapping[Shape, int],
        extra: Iterable[Shape] = (),
    ) -> None:
        self._required: dict[Shape, int] = {key: int(n) for key, n in shapes.items()}
        self._extra: set[Shape] = {tuple(shape) for shape in extra}
        self._done: dict[Shape, list[list[int]]] = {key: [] for key in shapes}
        self._partial: dict[Shape, Optional[list[Optional[int]]]] = {
            key: None for key in shapes}
        self._base: dict[Shape, int] = {}
        self._next: dict[Shape, int] = {key: 0 for key in shapes}
        self._extra_seen: set[Shape] = set()
        self.frames: list[Frame] = []

    # -- configuration -----------------------------------------------------

    def require(self, shape: Shape, count: int) -> None:
        """Set how many complete runs of ``shape`` are wanted."""
        if shape not in self._required:
            raise KeyError(shape)
        self._required[shape] = max(0, int(count))

    @property
    def shapes(self) -> tuple[Shape, ...]:
        return tuple(self._required)

    # -- feeding -----------------------------------------------------------

    def feed(self, frame: Frame) -> bool:
        """Feed one parsed frame; return ``True`` if it completed a run."""
        shape: Shape = (frame["cmd"], frame["total"])
        if shape not in self._required and shape not in self._extra:
            return False
        self.frames.append(frame)
        if shape not in self._required:
            self._extra_seen.add(shape)
            return False  # recorded for its side effects only
        if len(self._done[shape]) >= self._required[shape]:
            return False  # already have every copy we asked for

        total = frame["total"]
        index = frame["index"]
        words = self._partial[shape]
        if words is None:
            base = self._run_base(index)
            if base is None:
                return False  # mid-run frame without a known start
            self._base[shape] = base
            self._next[shape] = 0
            words = [None] * total
            self._partial[shape] = words
        position = index - self._base[shape]
        if not 0 <= position < total or position != self._next[shape]:
            # Out of order, or a repeated slot: drop the partial run.  The
            # vendor client resets in the same situation.
            self._partial[shape] = None
            return False
        words[position] = frame["data"]
        self._next[shape] = position + 1
        if any(word is None for word in words):
            return False
        self._done[shape].append([int(word) for word in words])
        self._partial[shape] = None
        return True

    def feed_all(self, frames: Iterable[Frame]) -> None:
        """Feed a batch; useful in tests and when replaying a capture."""
        for frame in frames:
            self.feed(frame)

    @staticmethod
    def _run_base(index: int) -> Optional[int]:
        """Frame number of the run's first frame, or ``None`` if unknown.

        Firmware 2.58 is 0-based.  Some revisions count from 1, including the
        single-frame replies where the only index is 1.
        """
        if index == 0:
            return 0
        if index == 1:
            return 1
        return None

    # -- inspection --------------------------------------------------------

    def accepts(self, frame: Frame) -> bool:
        """True when ``frame`` belongs to a watched shape."""
        shape: Shape = (frame["cmd"], frame["total"])
        return shape in self._required or shape in self._extra

    def is_complete(self) -> bool:
        """True when every shape has all the runs it asked for."""
        return all(len(self._done[key]) >= self._required[key]
                   for key in self._required)

    def extras_done(self) -> bool:
        """True when every side-effect shape has arrived at least once."""
        return self._extra <= self._extra_seen

    def complete_count(self, shape: Shape) -> int:
        return len(self._done[shape])

    def pending(self, shape: Shape) -> bool:
        """True while a run of ``shape`` has started but not finished."""
        return self._partial.get(shape) is not None

    def has_started(self) -> bool:
        """True once any watched frame has arrived (progress was made)."""
        return bool(self.frames)

    def values(self, shape: Shape) -> list[list[int]]:
        """Complete runs of ``shape``, in arrival order."""
        return [list(run) for run in self._done[shape]]

    def missing(self) -> dict[Shape, int]:
        """Shapes still short of their requested count, with the deficit."""
        return {key: self._required[key] - len(self._done[key])
                for key in self._required
                if len(self._done[key]) < self._required[key]}


class ReadState(str, Enum):
    """Where a read session is in its life cycle."""

    WAITING = "waiting"
    """Request sent, no watched frame has arrived yet."""
    COLLECTING = "collecting"
    """Frames are arriving, some run is still short."""
    SETTLING = "settling"
    """Every run is complete; reading on briefly to catch push frames."""
    COMPLETE = "complete"
    """The reply arrived whole."""
    STALLED = "stalled"
    """Frames started arriving but then stopped before the reply was whole."""
    TIMED_OUT = "timed_out"
    """No frame arrived before the hard deadline."""


_TERMINAL: Final = frozenset({ReadState.COMPLETE, ReadState.STALLED,
                              ReadState.TIMED_OUT})


class ReadSession:
    """Drive one read from "request sent" to a terminal state.

    The transport loop only feeds frames and a clock into :meth:`step`; every
    decision (whole reply, stalled, timed out, settle finished) lives here and
    is unit-tested with a fake clock.
    """

    def __init__(
        self,
        collector: StreamCollector,
        timeout_ms: int,
        idle_ms: int = 250,
        settle_ms: int = 0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.collector = collector
        self.state = ReadState.WAITING
        self._clock = clock
        self._started = clock()
        self._last_progress = self._started
        self._timeout = timeout_ms / 1000.0
        self._idle = idle_ms / 1000.0
        self._settle = settle_ms / 1000.0
        self._settle_until: Optional[float] = None

    @property
    def running(self) -> bool:
        """False once the session reached a terminal state."""
        return self.state not in _TERMINAL

    @property
    def complete(self) -> bool:
        return self.state is ReadState.COMPLETE

    @property
    def poll_ms(self) -> int:
        """How long one ``read`` may block in the current state."""
        return 10 if self.state is ReadState.SETTLING else 20

    def step(self, frame: Optional[Frame] = None, now: Optional[float] = None) -> ReadState:
        """Advance one step; feed a frame if one arrived, else ``None``."""
        now = self._clock() if now is None else now
        if not self.running:
            return self.state

        if frame is not None:
            watched = self.collector.accepts(frame)
            self.collector.feed(frame)
            if watched:
                # Only frames we asked for count as progress.  Telemetry that
                # the device streams on its own must not hide a reply that
                # never arrives, and its absence must not fake a stall.
                self._last_progress = now
                if self.state is ReadState.WAITING:
                    self.state = ReadState.COLLECTING

        if self.state is ReadState.SETTLING:
            # The trailing pushes usually all arrive within about 40 ms of the
            # reply itself; stop as soon as they are in, and only wait the
            # full settle window when one of them never comes.
            if self.collector.extras_done() or now >= self._settle_until:
                self.state = ReadState.COMPLETE
            return self.state

        if self.collector.is_complete():
            self._enter_settling(now)
            return self.state

        if now - self._started > self._timeout:
            self.state = ReadState.TIMED_OUT
        elif self.state is ReadState.COLLECTING and now - self._last_progress > self._idle:
            self.state = ReadState.STALLED
        return self.state

    def _enter_settling(self, now: float) -> None:
        if self._settle <= 0:
            self.state = ReadState.COMPLETE
            return
        self.state = ReadState.SETTLING
        self._settle_until = now + self._settle


__all__ = ["ReadSession", "ReadState", "Shape", "StreamCollector"]
