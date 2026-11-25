"""Command-line interface.

Two calling styles share one parser:

``dx5ii`` (no subcommand)
    read the full state, optionally switching the output port and/or EQ preset
    first; this is what the game watcher uses.
``dx5ii <command> ...``
    ``get`` / ``set`` / ``raw`` / ``watch`` / ``configs`` -- see ``--help``.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import textwrap
import time
from enum import IntEnum
from importlib import resources
from pathlib import Path
from typing import Any, Optional, Sequence

from .actions import apply_changes, state_summary
from .bands import (
    CONFIG_NAME_WORDS,
    DEVICE_NAME_MAX_BYTES,
    Band,
    encode_preamp,
    live_words,
    parse_apo_filters,
    parse_apo_preamp,
    render_apo,
)
from .constants import (
    CMD_CONNECT_STATE,
    CMD_HEARTBEAT,
    CMD_SWITCH_MCU_CONFIG,
    DESTRUCTIVE_COMMANDS,
    PT_READ,
    PT_WRITE,
)
from .device import Dx5ii, list_interfaces
from .errors import Dx5iiError
from .fields import (FIELD_ACTIONS, FIELD_SPECS, encode_field, field_raw,
                     field_state, resolve_config)
from .formatting import (
    format_gain,
    format_preamp,
    print_configs,
    print_field_rows,
    print_snapshot,
    print_switch_report,
)
from . import __version__
from .preview import enter_preview, leave_preview, preview_state
from .peq import (
    create_preset,
    delete_preset,
    find_preset,
    overwrite_preset,
    presets_equal,
    read_preset_blocks,
    read_presets,
    rename_preset,
)
from .protocol import format_firmware
from .textfmt import render_markdown


class ExitCode(IntEnum):
    """Process exit codes; the second column is what the v3 design promises."""

    OK = 0
    USAGE = 1          # bad argument, unknown field, missing value
    DEVICE = 2         # device or protocol failure, unknown preset
    MISMATCH = 3       # wrote a value, read back something else
    TIMEOUT = 4        # a read or write did not complete in time


class _Parser(argparse.ArgumentParser):
    """Usage errors exit 1, not argparse's default 2."""

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        print("%s: error: %s" % (self.prog, message), file=sys.stderr)
        raise SystemExit(int(ExitCode.USAGE))


def _open(path: Optional[str] = None, trace: Optional[str] = None) -> Dx5ii:
    return Dx5ii(path=path, trace=trace)


def _epilog() -> str:
    fields = textwrap.fill(
        ", ".join(FIELD_SPECS), width=74, break_on_hyphens=False,
        initial_indent="    ", subsequent_indent="    ")
    actions = textwrap.fill(
        ", ".join(FIELD_ACTIONS), width=74, break_on_hyphens=False,
        initial_indent="    ", subsequent_indent="    ")
    return (
        "fields (%d)\n%s\n\n"
        "actions (%d, no value; fire and forget)\n%s\n\n"
        "manual: dx5ii manual <section>  (sections: %s)\n"
        "exit codes: 0 ok, 2 device error, 3 write did not take effect"
        % (len(FIELD_SPECS), fields, len(FIELD_ACTIONS), actions,
           ", ".join(_MANUAL_SECTIONS))
    )


_MANUAL_SECTIONS = (
    "index",
    "commands",
    "fields",
    "peq",
    "observe",
    "troubleshooting",
)


def _manual_text(section: str) -> str:
    """Read one manual section from the installed package."""
    try:
        path = resources.files("dx5ii").joinpath("manual", section + ".md")
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, NotADirectoryError) as exc:
        raise Dx5iiError(
            "manual section %r is not installed; try --list" % section
        ) from exc


def _manual_fields() -> str:
    """Render the field table from the encoder table, never from prose."""
    rows = [
        "## Field list (generated from the encoder)", "",
        "| field | kind | values | command |", "|---|---|---|---|",
    ]
    for name, spec in FIELD_SPECS.items():
        cmd, kind, table = spec[0], spec[1], spec[2]
        if table is None:
            values = ""
        elif kind == "mask":
            values = "bits: " + ", ".join(table)
        else:
            values = ", ".join(table)
        rows.append("| `%s` | %s | %s | `0x%04X` |" % (name, kind, values, cmd))
    rows.extend(["", "Actions (no value, fire and forget):", ""])
    for name, cmd in FIELD_ACTIONS.items():
        rows.append("* `%s` — `0x%04X`" % (name, cmd))
    return "\n".join(rows)


def _cmd_manual(args: argparse.Namespace) -> int:
    if args.list:
        print("sections:")
        for name in _MANUAL_SECTIONS:
            print("  " + name)
        return 0
    section = args.section or "index"
    if section not in _MANUAL_SECTIONS:
        return _fail("error: unknown manual section %r; try --list" % section,
                     ExitCode.USAGE)
    try:
        text = _manual_text(section)
    except Dx5iiError as exc:
        return _fail("error: %s" % exc)
    if section == "fields":
        text = text.rstrip() + "\n\n" + _manual_fields() + "\n"
    if getattr(args, "markdown", False):
        print(text, end="" if text.endswith("\n") else "\n")
        return 0
    columns = shutil.get_terminal_size((80, 24)).columns
    width = max(60, min(columns, 100))
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    print(render_markdown(text, width=width, ascii_only="utf" not in encoding),
          end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (also used by ``--help``)."""
    parser = _Parser(
        prog="dx5ii",
        description="Control a Topping DX5 II over its HID interface.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_epilog(),
    )
    parser.add_argument("--version", action="version",
                        version=f"dx5ii {__version__}")
    parser.add_argument("--json", action="store_true",
                        help="emit JSON instead of text")
    parser.add_argument("--devices", action="store_true",
                        help="list matching HID interfaces and exit")
    parser.add_argument("--raw", action="store_true",
                        help="include every received frame in the snapshot")
    parser.add_argument("--fast", action="store_true",
                        help="skip the EQ preset list (saves ~3 s)")
    parser.add_argument("--path", help="explicit HID path (interface 2)")
    parser.add_argument("--trace", metavar="PATH",
                        help="append every frame sent and received to PATH "
                             "as JSON lines")
    parser.add_argument("--set-output", metavar="NAME",
                        help="switch the output port, e.g. hp_balanced")
    parser.add_argument("--set-config", metavar="NAME|INDEX",
                        help="switch the active EQ preset, e.g. IER-M7.Harman")

    sub = parser.add_subparsers(dest="command", metavar="command")

    def add_trace(target: argparse.ArgumentParser) -> None:
        target.add_argument("--trace", metavar="PATH", default=argparse.SUPPRESS,
                            help="append every frame sent and received to "
                                 "PATH as JSON lines")

    def add_common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--json", action="store_true", help="emit JSON")
        target.add_argument("--path", help="explicit HID path (interface 2)")
        add_trace(target)

    def add_switch(target: argparse.ArgumentParser) -> None:
        group = target.add_mutually_exclusive_group()
        group.add_argument("--activate", action="store_true",
                           help="stay on the new preset (the default)")
        group.add_argument("--no-switch", action="store_true",
                           help="go back to the preset that was active before")

    p_status = sub.add_parser("status", help="print the device state",
                              description="Read and print the device state; "
                                          "same output as `dx5ii` with no "
                                          "subcommand.")
    p_status.add_argument("--fast", action="store_true",
                          help="skip the EQ preset list")
    add_common(p_status)

    p_devices = sub.add_parser("devices", help="list matching HID interfaces",
                               description="List the HID interfaces this "
                                           "device exposes and mark the one "
                                           "used for the protocol.")
    p_devices.add_argument("--json", action="store_true", help="emit JSON")

    p_output = sub.add_parser("output", help="switch the output port",
                              description="Switch the output port and report "
                                          "the state before and after.")
    p_output.add_argument("name", help="port name, e.g. hp_balanced")
    add_common(p_output)

    p_get = sub.add_parser("get", help="print current state",
                           description="Print named fields. The field list "
                                       "is at the bottom of `dx5ii --help`.")
    p_get.add_argument("fields", nargs="*", metavar="field",
                       help="field names (default: all)")
    add_common(p_get)

    p_set = sub.add_parser("set", help="write one or more fields, then read back",
                           description="Write fields, then read them back. The "
                                       "field list is at the bottom of "
                                       "`dx5ii --help`.")
    p_set.add_argument("pairs", nargs="+", metavar="field value")
    add_common(p_set)

    p_raw = sub.add_parser("raw", help="send one frame and print the reply")
    p_raw.add_argument("cmd", help="command number, e.g. 0x7137")
    p_raw.add_argument("data", nargs="?", help="payload word")
    p_raw.add_argument("--pt", choices=("read", "write"), default="write")
    p_raw.add_argument("--total", type=lambda s: int(s, 0), default=1)
    p_raw.add_argument("--cur", type=lambda s: int(s, 0), default=1)
    p_raw.add_argument("--wait", type=int, default=800, metavar="MS",
                       help="how long to collect replies (default: 800)")
    p_raw.add_argument("--path")
    add_trace(p_raw)
    p_raw.add_argument("--force", action="store_true",
                       help="allow commands that cannot be undone "
                            "(factory reset, firmware update mode)")

    p_watch = sub.add_parser("watch", help="stream frames the device pushes")
    p_watch.add_argument("seconds", nargs="?", type=int, default=3,
                         help="seconds to summarise (ignored with -f)")
    p_watch.add_argument("-f", "--follow", action="store_true",
                         help="print each frame as it arrives until Ctrl-C")
    p_watch.add_argument("--cmd",
                         help="only print frames with this command, e.g. 0x7137")
    p_watch.add_argument("--all", action="store_true",
                         help="include the 0x7130/0x7131 telemetry streams")
    p_watch.add_argument("--timestamps", action="store_true",
                         help="prefix each follow line with hh:mm:ss.mmm")
    p_watch.add_argument("--json", action="store_true",
                         help="print one JSON object per frame")
    p_watch.add_argument("--path")
    add_trace(p_watch)

    p_cfg = sub.add_parser("configs", help="list the EQ presets on the device")
    add_common(p_cfg)

    p_manual = sub.add_parser(
        "manual", help="print a section of the user manual",
        description="Print a section of the user manual. The same manual is "
                    "shipped inside the package, so it works without a "
                    "network connection.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p_manual.add_argument("section", nargs="?",
                          help="section to print (default: index)")
    p_manual.add_argument("--list", action="store_true",
                          help="list the sections")
    p_manual.add_argument("--markdown", action="store_true",
                          help="print the raw markdown instead of formatted text")

    p_peq = sub.add_parser(
        "peq", help="manage EQ presets",
        description="Manage preset contents. The PEQ on/off switch is the "
                    "`peq` field: `dx5ii set peq on` / `dx5ii set peq off`.")
    add_common(p_peq)
    peq_sub = p_peq.add_subparsers(dest="peq_command")

    p_plist = peq_sub.add_parser("list", help="list presets with band counts")
    add_common(p_plist)

    p_pshow = peq_sub.add_parser("show", help="print one preset's bands")
    p_pshow.add_argument("preset", help="preset name or index")
    add_common(p_pshow)

    p_pcreate = peq_sub.add_parser(
        "create", help="upload a new preset (never modifies existing ones)")
    p_pcreate.add_argument("name", help="new preset name (max 32 UTF-8 bytes)")
    source = p_pcreate.add_mutually_exclusive_group(required=True)
    source.add_argument("--copy", metavar="PRESET",
                        help="copy the bands of an existing preset")
    source.add_argument("--from-apo", metavar="FILE",
                        help="read the Filter lines of the section named after "
                             "this device in an Equalizer APO config")
    p_pcreate.add_argument("--json", action="store_true")
    p_pcreate.add_argument("--path")
    add_switch(p_pcreate)
    add_trace(p_pcreate)

    p_pimport = peq_sub.add_parser(
        "import", help="create a preset from an Equalizer APO file")
    p_pimport.add_argument("file", metavar="FILE",
                           help="Equalizer APO file")
    p_pimport.add_argument("--name",
                           help="preset name (default: the file name)")
    add_common(p_pimport)
    add_switch(p_pimport)

    p_pcopy = peq_sub.add_parser(
        "copy", help="create a preset from another preset")
    p_pcopy.add_argument("source", help="preset to copy")
    p_pcopy.add_argument("name", help="new preset name")
    add_common(p_pcopy)
    add_switch(p_pcopy)

    p_pexport = peq_sub.add_parser(
        "export", help="write a preset as Equalizer APO text")
    p_pexport.add_argument("preset", help="preset name or index")
    p_pexport.add_argument("file", metavar="FILE", help="file to write")
    add_common(p_pexport)

    p_pdel = peq_sub.add_parser("delete", help="delete a preset")
    p_pdel.add_argument("preset", help="preset name or index")
    p_pdel.add_argument("--switch", required=True, metavar="PRESET",
                        help="preset to activate after deletion")
    p_pdel.add_argument("--json", action="store_true")
    p_pdel.add_argument("--path")
    add_trace(p_pdel)

    p_pprev = peq_sub.add_parser(
        "preview", help="play a preset from the scratch set without storing it")
    p_pprev.add_argument("preset", nargs="?", help="preset name or index")
    p_pprev.add_argument("--from-apo", metavar="FILE",
                         help="audition an Equalizer APO config instead of a "
                              "stored preset (nothing is created)")
    p_pprev.add_argument("--stop", action="store_true",
                         help="leave the preview and return to the stored preset")
    p_pprev.add_argument("--json", action="store_true")
    p_pprev.add_argument("--path")
    add_trace(p_pprev)

    p_pren = peq_sub.add_parser("rename", help="rename a preset")
    p_pren.add_argument("preset", help="preset name or index")
    p_pren.add_argument("new_name")
    p_pren.add_argument("--json", action="store_true")
    p_pren.add_argument("--path")
    add_trace(p_pren)

    p_psw = peq_sub.add_parser(
        "switch", help="switch to another preset",
        description="Switch the device to another preset. Same action as the "
                    "top-level --set-config. This also ends a Topping Home "
                    "cloud preview, and the device cannot go back to the cloud "
                    "config by itself.")
    p_psw.add_argument("slot", help="preset name or index")
    p_psw.add_argument("--json", action="store_true")
    p_psw.add_argument("--path")
    add_trace(p_psw)

    p_pover = peq_sub.add_parser(
        "overwrite", help="replace the contents of an existing preset")
    p_pover.add_argument("preset", help="preset name or index to overwrite")
    over_source = p_pover.add_mutually_exclusive_group(required=True)
    over_source.add_argument("--copy", metavar="PRESET",
                             help="take the bands of another stored preset")
    over_source.add_argument("--from-apo", metavar="FILE",
                             help="take the Filter lines of the section named "
                                  "after this device in an Equalizer APO config")
    over_source.add_argument("--last", action="store_true",
                             help="reuse the content of the last peq preview")
    p_pover.add_argument("--backup", metavar="PATH",
                         help="write the current 78-word block here first")
    p_pover.add_argument("--dry-run", action="store_true",
                         help="print the command list without sending it")
    p_pover.add_argument("--json", action="store_true")
    p_pover.add_argument("--path")
    add_trace(p_pover)

    return parser


# ---------------------------------------------------------------------------
# subcommand implementations
# ---------------------------------------------------------------------------

def _cmd_get(path: Optional[str], fields: Sequence[str], as_json: bool,
             trace: Optional[str] = None) -> int:
    # A named field comes from ``settings`` or ``pushed`` only, so skip the
    # sampling and preview requests; ``get all`` keeps the full snapshot.
    light = bool(fields) and list(fields) != ["all"]
    with _open(path, trace) as dev:
        snap = dev.snapshot(include_configs=False, light=light)

    if not fields or list(fields) == ["all"]:
        if as_json:
            print(json.dumps(snap, indent=2, ensure_ascii=False))
        else:
            print_snapshot(snap)
        return 0

    selected: dict[str, Any] = {}
    for name in fields:
        if name not in FIELD_SPECS:
            return _fail(f"error: unknown field {name!r}", ExitCode.USAGE)
        value = field_state(snap, name)
        selected[name] = value
        raw = field_raw(snap, name)
        if isinstance(value, str) and raw is not None:
            selected[name + "_raw"] = raw

    if as_json:
        print(json.dumps(selected, indent=2, ensure_ascii=False))
    else:
        print_field_rows(selected.items())
    return 0


#: Fields whose command never echoes back; they get a short wait instead of
#: the echo timeout. Both were measured on firmware 2.58.
_SILENT_FIELDS = {"line-mode", "crossfeed-type"}
_ECHO_TIMEOUT_MS = 300
_SILENT_WAIT_S = 0.1


def _encoded_words(name: str, value: Any, settings: Optional[dict[str, Any]]) -> Optional[list[int]]:
    """Encode a field value to the words a write would send.

    Comparing these instead of the display values makes the check insensitive
    to how the device rounds or clamps: both sides go through the same encoder.
    """
    try:
        return [frame[1] for frame in encode_field(name, value, settings)]
    except Dx5iiError:
        return None


def _cmd_set(path: Optional[str], pairs: Sequence[str], as_json: bool,
             trace: Optional[str] = None) -> int:
    if len(pairs) % 2 != 0:
        return _fail("error: set needs field/value pairs", ExitCode.USAGE)
    items = [(pairs[i], pairs[i + 1]) for i in range(0, len(pairs), 2)]

    with _open(path, trace) as dev:
        before = dev.snapshot(include_configs=False)

        # Encode everything first: a bad value must not leave the device with
        # half of the batch already written.
        encoded: list[tuple[str, str, list[tuple[int, int, int, int]]]] = []
        for name, value in items:
            try:
                encoded.append((name, value,
                                list(encode_field(name, value, before["settings"]))))
            except Dx5iiError as exc:
                return _fail("error: %s" % exc, ExitCode.USAGE)

        echoes: dict[str, bool] = {}
        for name, value, commands in encoded:
            for cmd, data, total, cur in commands:
                dev.send(cmd, data, PT_WRITE, total, cur)
            if name in _SILENT_FIELDS or len(commands) > 1:
                # A multi-frame field has no single frame to wait for, and
                # these two commands never answer.
                time.sleep(_SILENT_WAIT_S)
                echoes[name] = name not in _SILENT_FIELDS
            else:
                echoes[name] = dev.wait_for_frame(
                    commands[0][0], None, timeout_ms=_ECHO_TIMEOUT_MS)

        time.sleep(0.25)
        after = dev.snapshot(include_configs=False)

    results: list[dict[str, Any]] = []
    ok = True
    for name, value, commands in encoded:
        requested_words = _encoded_words(name, value, after.get("settings"))
        observed_words = _encoded_words(name, field_state(after, name),
                                        after.get("settings"))
        state_before = field_state(before, name)
        state_after = field_state(after, name)
        raw_before = field_raw(before, name)
        raw_after = field_raw(after, name)
        changed = state_before != state_after
        confirmed = (None if requested_words is None or observed_words is None
                     else requested_words == observed_words)
        item: dict[str, Any] = {
            "field": name,
            "requested": value,
            "from": state_before,
            "observed": state_after,
            "changed": changed,
            "echo": echoes.get(name),
            "confirmed": confirmed,
        }
        if isinstance(state_before, str) or isinstance(state_after, str):
            item["from_raw"] = raw_before
            item["observed_raw"] = raw_after
        if not changed and name not in FIELD_ACTIONS:
            ok = False
            item["note"] = "read-back unchanged"
        elif confirmed is False and name not in FIELD_ACTIONS:
            # The device answered but stored something else, usually because
            # it clamps or rounds the value; report it rather than hide it.
            item["note"] = "device stored %r" % (state_after,)
        results.append(item)

    report = {
        "ok": ok,
        "actions": results,
        "before": state_summary(before, include_slots=False),
        "after": state_summary(after, include_slots=False),
    }
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for item in results:
            name = item["field"]
            if name in FIELD_ACTIONS:
                print(f"{name:<20} sent")
                continue
            note = ""
            if not item["changed"]:
                note = "   (unchanged)"
            elif item.get("note"):
                note = "   (%s)" % item["note"]
            elif item["echo"] is False:
                note = "   (no echo)"
            print(f"{name:<20} {item['from']} -> {item['observed']}{note}")
    return 0 if ok else 3


def _cmd_raw(path: Optional[str], args: argparse.Namespace) -> int:
    cmd = int(args.cmd, 0)
    data = int(args.data, 0) if args.data else 0

    danger = DESTRUCTIVE_COMMANDS.get(cmd)
    if danger and not args.force:
        print("refusing 0x%04X: %s.\nPass --force if you really mean it."
              % (cmd, danger), file=sys.stderr)
        return 2

    protocol = PT_READ if args.pt == "read" else PT_WRITE
    with _open(path, args.trace) as dev:
        dev.send(cmd, data, protocol, args.total, args.cur)
        frames = dev.read_for(args.wait)

    print("tx cmd=0x%04X pt=0x%02X total=%d cur=%d data=0x%X (%d)"
          % (cmd, protocol, args.total, args.cur, data, data))
    for frame in frames:
        print("cmd=0x%04X pt=0x%02X total=%d cur=%d data=%d (0x%X)"
              % (frame["cmd"], frame["protocol"], frame["total"],
                 frame["index"], frame["data"], frame["data"]))
    print(f"{len(frames)} frames")
    return 0


TELEMETRY_COMMANDS: Final = (0x7130, 0x7131)


def _frame_line(frame: dict[str, Any], *, timestamps: bool, as_json: bool,
                when: float) -> str:
    if as_json:
        return json.dumps({
            "t": round(when, 3),
            "cmd": "0x%04X" % frame["cmd"],
            "protocol": "0x%02X" % frame["protocol"],
            "total": frame["total"],
            "cur": frame["index"],
            "data": frame["data"],
        })
    body = "cmd=0x%04X pt=0x%02X total=%d cur=%d data=0x%X (%d)" % (
        frame["cmd"], frame["protocol"], frame["total"], frame["index"],
        frame["data"], frame["data"])
    if not timestamps:
        return body
    stamp = time.strftime("%H:%M:%S", time.localtime(when))
    return "%s.%03d  %s" % (stamp, int(when * 1000) % 1000, body)


def _watch_loop(read_frame, write, *, cmd_filter: Optional[int] = None,
                include_telemetry: bool = False, timestamps: bool = False,
                as_json: bool = False, clock=time.time,
                max_frames: Optional[int] = None) -> int:
    """Print frames as they arrive.  Returns 0, including on Ctrl-C."""
    shown = 0
    try:
        while max_frames is None or shown < max_frames:
            frame = read_frame()
            if frame is None:
                continue
            cmd = frame["cmd"]
            if cmd_filter is not None and cmd != cmd_filter:
                continue
            if not include_telemetry and cmd in TELEMETRY_COMMANDS:
                continue
            write(_frame_line(frame, timestamps=timestamps, as_json=as_json,
                              when=clock()))
            shown += 1
    except KeyboardInterrupt:
        return 0
    return 0


def _follow_reader(dev, interval: float = 5.0, clock=time.monotonic):
    """Return a frame reader that renews the push session every ``interval``."""
    last = clock()

    def read():
        nonlocal last
        now = clock()
        if now - last >= interval:
            dev.send(CMD_HEARTBEAT, 1, PT_WRITE, 1, 1)
            last = now
        return dev.read_frame(200)

    return read


def _cmd_watch(args: argparse.Namespace) -> int:
    cmd_filter = int(args.cmd, 0) if args.cmd else None
    with _open(args.path, args.trace) as dev:
        dev.send(CMD_CONNECT_STATE, 2, PT_WRITE, 1, 1)
        if args.follow:
            return _watch_loop(
                _follow_reader(dev),
                lambda line: print(line, flush=True),
                cmd_filter=cmd_filter,
                include_telemetry=args.all,
                timestamps=args.timestamps,
                as_json=args.json,
            )
        print(f"watching push frames for {args.seconds}s ...")
        frames = dev.read_for(args.seconds * 1000)

    counts: dict[tuple[int, int], int] = {}
    for frame in frames:
        key = (frame["cmd"], frame["protocol"])
        counts[key] = counts.get(key, 0) + 1
    for (cmd, protocol), count in sorted(counts.items()):
        print("cmd=0x%04X pt=0x%02X  x%d" % (cmd, protocol, count))
    print(f"{len(frames)} frames total")
    return 0


def _legacy_keys() -> bool:
    """Whether the JSON payloads still carry the pre-0.1.2 key spellings."""
    return not os.environ.get("DX5II_NO_LEGACY_KEYS")


def _cmd_configs(path: Optional[str], as_json: bool,
                 trace: Optional[str] = None) -> int:
    """``dx5ii peq`` / ``dx5ii configs``: the preset view plus what plays."""
    try:
        with _open(path, trace) as dev:
            snap = dev.snapshot(include_configs=True)
    except Dx5iiError as exc:
        return _fail("error: %s" % exc)
    configs = snap["configs"]
    if as_json:
        payload = dict(configs)
        if _legacy_keys():
            payload["presets"] = configs.get("slots") or []
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return int(ExitCode.OK)
    print_configs(configs)
    return int(ExitCode.OK)


# ---------------------------------------------------------------------------
# eq preset management
# ---------------------------------------------------------------------------

def _resolve_preset(presets: dict[str, Any], ref: str) -> Optional[dict[str, Any]]:
    """Resolve a preset by index or case-insensitive name."""
    text = ref.strip()
    if text.lstrip("-").isdigit():
        index = int(text)
        for preset in presets.get("presets", []):
            if preset["index"] == index:
                return preset
        return None
    return find_preset(presets, text)


def _used_bands(preset: dict[str, Any]) -> tuple[list[Band], list[Band]]:
    """Drop the device's spare 11th band from a decoded preset."""
    return (
        [Band(**b) for b in preset["bands_left"]],
        [Band(**b) for b in preset["bands_right"]],
    )


def _state_candidates() -> list[Path]:
    """Ordered places the preview hand-off may live."""
    candidates: list[Path] = []
    override = os.environ.get("DX5II_STATE_DIR")
    if override:
        candidates.append(Path(override))
    candidates.append(Path.home() / ".dx5ii")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "dx5ii")
    candidates.append(Path(tempfile.gettempdir()) / "dx5ii")
    return candidates


def _state_dir() -> Path:
    """First writable place for the hand-off file; creates it if needed."""
    for directory in _state_candidates():
        try:
            directory.mkdir(parents=True, exist_ok=True)
            return directory
        except OSError:
            continue
    raise OSError("no writable state directory for the preview hand-off")


def _last_preview_path() -> Path:
    return _state_dir() / "last-preview.json"


def _save_last_preview(preset: dict[str, Any]) -> bool:
    """Remember what ``peq preview`` last played, for ``peq overwrite --last``."""
    try:
        path = _last_preview_path()
        path.write_text(json.dumps(preset, ensure_ascii=False), encoding="utf-8")
        return True
    except OSError:
        return False


def _load_last_preview() -> Optional[dict[str, Any]]:
    """Read the hand-off file without creating any directory."""
    for directory in _state_candidates():
        try:
            return json.loads(
                (directory / "last-preview.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
    return None


def _print_bands(preset: dict[str, Any], which: str) -> None:
    print("%s  (index %d%s)" % (preset["name"], preset["index"],
                               ", stored" if preset["stored_current"] else ""))
    if not preset.get("enabled_left", True):
        print("  left channel disabled")
    preamp_l = preset["preamp_left"]
    preamp_r = preset["preamp_right"]
    if preamp_l == preamp_r:
        print("  Preamp: %s dB" % format_gain(preamp_l))
    else:
        if which in ("both", "l"):
            print("  L Preamp: %s dB" % format_gain(preamp_l))
        if which in ("both", "r"):
            print("  R Preamp: %s dB" % format_gain(preamp_r))

    left = preset["bands_left"]
    right = preset["bands_right"]
    for i in range(max(len(left), len(right))):
        band_l = left[i] if i < len(left) else None
        band_r = right[i] if i < len(right) else None
        if i >= 10 and not (band_l and band_l["enabled"]) and not (
                band_r and band_r["enabled"]):
            continue
        rows = []
        if which in ("both", "l") and band_l is not None:
            rows.append(("" if band_l == band_r else "L ", band_l))
        if which in ("both", "r") and band_r is not None and band_r != band_l:
            rows.append(("R ", band_r))
        for prefix, band in rows:
            print("  %sFilter %d: %s %s Fc %d Hz Gain %s dB Q %.4f"
                  % (prefix, i + 1, "ON" if band["enabled"] else "OFF",
                     band["type"], round(band["freq_hz"]),
                     format_gain(band["gain_db"]), band["q"]))


_WARNED: set[str] = set()


def _warn(old: str, new: str) -> None:
    """Tell the user which spelling replaces ``old``, once per process."""
    if os.environ.get("DX5II_QUIET"):
        return
    if old in _WARNED:
        return
    _WARNED.add(old)
    print("dx5ii: %s will be removed in 0.1.2; %s" % (old, new), file=sys.stderr)


def _fail(message: str, code: int = int(ExitCode.DEVICE)) -> int:
    """Print an error line and return the exit code for it."""
    print(message, file=sys.stderr)
    return code


def _emit(payload: Any, text: list[str], as_json: bool) -> None:
    """Print either the JSON payload or the prepared text lines."""
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for line in text:
            print(line)


def _load_apo(path: str, product: Optional[str]) -> tuple[list[Band], Optional[float]]:
    """Read an Equalizer APO file and return its bands and preamp."""
    try:
        text = io.open(path, encoding="utf-8", errors="replace").read()
    except OSError as exc:
        raise ValueError("cannot read %s: %s" % (path, exc.strerror or exc))
    bands = parse_apo_filters(text, device=product)
    if not bands:
        raise ValueError("no filters parsed from %s" % path)
    return bands, parse_apo_preamp(text, device=product)


def _apo_preset(path: str, product: Optional[str], name: str) -> dict[str, Any]:
    """Build a preset payload from an APO file, both channels identical."""
    bands, preamp = _load_apo(path, product)
    value = 0.0 if preamp is None else preamp
    return {
        "name": name,
        "preamp_left": value,
        "preamp_right": value,
        "enabled_left": True,
        "enabled_right": True,
        "bands_left": [b.as_dict() for b in bands],
        "bands_right": [b.as_dict() for b in bands],
    }


def _peq_preview(dev, args, presets, preset_blocks, as_json) -> int:
    if args.stop:
        state = leave_preview(dev)
        _emit(state, ["preview stopped; back to slot %s (%s)"
                      % (presets.get("stored_index"),
                         presets.get("stored_name"))], as_json)
        return 0 if not state.get("preview_active") else 3

    if args.from_apo:
        try:
            preset = _apo_preset(args.from_apo, dev.info.get("product"),
                                 args.from_apo.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
        except ValueError as exc:
            return _fail("error: %s" % exc, ExitCode.USAGE)
    elif args.preset:
        preset = _resolve_preset(presets, args.preset)
        if preset is None:
            return _fail("error: no preset %r" % args.preset)
    else:
        return _fail("error: name a preset to preview, pass --from-apo, "
                     "or pass --stop", ExitCode.USAGE)

    try:
        state = enter_preview(dev, preset)
    except BaseException:
        # Ctrl-C in the middle of the handshake would leave the device
        # playing half a config; put it back before dying.
        try:
            leave_preview(dev)
        except Exception:
            pass
        raise

    ok = bool(state.get("preview_active"))
    if ok and not _save_last_preview(preset):
        print("warning: could not record this preview for --last",
              file=sys.stderr)
    report = {
        "preview_active": state.get("preview_active"),
        "source": state.get("source_name"),
        "preset": preset["name"],
        "bands": len(preset.get("bands_left") or []),
        "stored_index": presets.get("stored_index"),
        "stored_name": presets.get("stored_name"),
    }
    _emit(report, [
        "previewing %r (audition, %d bands); the stored slot %s (%s) is untouched"
        % (preset["name"], report["bands"], report["stored_index"],
           report["stored_name"]),
        "run 'dx5ii peq preview --stop' to go back",
    ], as_json)
    return 0 if ok else 3


def _peq_list(dev, args, presets, preset_blocks, as_json) -> int:
    rows = []
    for preset in presets["presets"]:
        used = sum(1 for b in preset["bands_left"] if b["enabled"])
        mark = "<- stored" if preset["stored_current"] else ""
        if not preset.get("enabled_left", True):
            mark = (mark + "  L disabled").strip()
        rows.append("  %d  %-24s %2d bands  preamp %s %s"
                    % (preset["index"], preset["name"], used,
                       format_preamp(preset["preamp_left"],
                                     preset["preamp_right"]), mark))
    _emit(presets, ["stored=%s (%s) count=%s"
                    % (presets.get("stored_index"),
                       presets.get("stored_name"),
                       presets.get("count"))] + rows, as_json)
    return 0


def _peq_show(dev, args, presets, preset_blocks, as_json) -> int:
    preset = _resolve_preset(presets, args.preset)
    if preset is None:
        return _fail("error: no preset %r" % args.preset)
    if as_json:
        print(json.dumps(preset, indent=2, ensure_ascii=False))
    else:
        _print_bands(preset, "both")
    return 0


def _peq_create(dev, args, presets, preset_blocks, as_json) -> int:
    name = args.name.strip()
    if not name:
        return _fail("error: empty preset name", ExitCode.USAGE)
    if len(name.encode("utf-8")) > CONFIG_NAME_WORDS * 4:
        return _fail("error: name longer than %d UTF-8 bytes"
                     % (CONFIG_NAME_WORDS * 4), ExitCode.USAGE)
    if find_preset(presets, name):
        return _fail("error: preset %r already exists; refusing to overwrite "
                     "an existing preset" % name, ExitCode.USAGE)

    head = None
    if args.copy:
        source = _resolve_preset(presets, args.copy)
        if source is None:
            return _fail("error: no preset %r to copy" % args.copy)
        left, right = _used_bands(source)
        head = [1, encode_preamp(source["preamp_left"]),
                1, encode_preamp(source["preamp_right"])]
    else:
        try:
            left, preamp = _load_apo(args.from_apo, dev.info.get("product"))
        except ValueError as exc:
            return _fail("error: %s" % exc, ExitCode.USAGE)
        right = list(left)
        if preamp is not None:
            word = encode_preamp(preamp)
            head = [1, word, 1, word]

    if args.from_apo:
        _warn("peq create --from-apo", "use 'dx5ii peq import FILE --name NAME'")
    if args.copy:
        _warn("peq create --copy", "use 'dx5ii peq copy SRC NAME'")

    index = create_preset(dev, name, left, right, head=head)
    return _finish_create(dev, args, presets, name, index, len(left), as_json)


def _finish_create(dev, args, presets, name, index, bands, as_json) -> int:
    """Common tail of create/import/copy: optional switch back, verify, report."""
    switched_back = False
    if getattr(args, "no_switch", False):
        now = dev.current_config_index()
        if now != presets.get("stored_index"):
            dev.send(CMD_SWITCH_MCU_CONFIG, presets.get("stored_index") or 0, PT_WRITE)
            time.sleep(0.15)
            switched_back = True
    after = read_presets(dev)
    found = find_preset(after, name)
    report = {
        "created": name,
        "index": index,
        "found": found is not None,
        "count_before": presets.get("count"),
        "count_after": after.get("count"),
        "bands": bands,
        "stored_index": after.get("stored_index"),
        "switched_back": switched_back,
    }
    text = ["created %r at index %d (count %s -> %s)"
            % (name, index, report["count_before"], report["count_after"])]
    if switched_back:
        text.append("  went back to slot %s (%s)"
                    % (after.get("stored_index"), after.get("stored_name")))
    else:
        text.append("  now playing slot %d (%s)" % (index, name))
    _emit(report, text, as_json)
    return int(ExitCode.OK) if report["found"] else int(ExitCode.MISMATCH)


def _peq_import(dev, args, presets, preset_blocks, as_json) -> int:
    raw = args.name or args.file.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    name = raw.strip()
    if not name:
        return _fail("error: empty preset name", ExitCode.USAGE)
    if len(name.encode("utf-8")) > CONFIG_NAME_WORDS * 4:
        return _fail("error: name longer than %d UTF-8 bytes"
                     % (CONFIG_NAME_WORDS * 4), ExitCode.USAGE)
    if find_preset(presets, name):
        return _fail("error: preset %r already exists; refusing to overwrite "
                     "an existing preset" % name, ExitCode.USAGE)
    try:
        left, preamp = _load_apo(args.file, dev.info.get("product"))
    except ValueError as exc:
        return _fail("error: %s" % exc, ExitCode.USAGE)
    right = list(left)
    head = None
    if preamp is not None:
        word = encode_preamp(preamp)
        head = [1, word, 1, word]
    index = create_preset(dev, name, left, right, head=head)
    return _finish_create(dev, args, presets, name, index, len(left), as_json)


def _peq_copy(dev, args, presets, preset_blocks, as_json) -> int:
    name = args.name.strip()
    if not name:
        return _fail("error: empty preset name", ExitCode.USAGE)
    if len(name.encode("utf-8")) > CONFIG_NAME_WORDS * 4:
        return _fail("error: name longer than %d UTF-8 bytes"
                     % (CONFIG_NAME_WORDS * 4), ExitCode.USAGE)
    if find_preset(presets, name):
        return _fail("error: preset %r already exists; refusing to overwrite "
                     "an existing preset" % name, ExitCode.USAGE)
    source = _resolve_preset(presets, args.source)
    if source is None:
        return _fail("error: no preset %r to copy" % args.source)
    left, right = _used_bands(source)
    head = [1, encode_preamp(source["preamp_left"]),
            1, encode_preamp(source["preamp_right"])]
    index = create_preset(dev, name, left, right, head=head)
    return _finish_create(dev, args, presets, name, index, len(left), as_json)


def _peq_export(dev, args, presets, preset_blocks, as_json) -> int:
    preset = _resolve_preset(presets, args.preset)
    if preset is None:
        return _fail("error: no preset %r" % args.preset)
    text = render_apo(preset)
    try:
        with io.open(args.file, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError as exc:
        return _fail("error: cannot write %s: %s" % (args.file, exc))
    used = sum(1 for band in preset["bands_left"] if band["enabled"])
    report = {"exported": preset["name"], "path": args.file, "bands": used}
    _emit(report, ["exported %r to %s (%d bands)"
                   % (preset["name"], args.file, used)], as_json)
    return int(ExitCode.OK)


def _peq_delete(dev, args, presets, preset_blocks, as_json) -> int:
    preset = _resolve_preset(presets, args.preset)
    if preset is None:
        return _fail("error: no preset %r" % args.preset)
    target = _resolve_preset(presets, args.switch)
    if target is None:
        return _fail("error: no preset %r to switch to" % args.switch)
    if target["index"] == preset["index"]:
        return _fail("error: --switch must name a different preset", ExitCode.USAGE)
    delete_preset(dev, preset["index"], target["index"])
    after = read_presets(dev)
    report = {
        "deleted": preset["name"],
        "index": preset["index"],
        "count_before": presets.get("count"),
        "count_after": after.get("count"),
        "stored_index": after.get("stored_index"),
        "presets": [p["name"] for p in after["presets"]],
    }
    _emit(report, ["deleted %r (count %s -> %s), stored=%s"
                   % (preset["name"], report["count_before"],
                      report["count_after"], report["stored_index"])], as_json)
    return 0


def _peq_rename(dev, args, presets, preset_blocks, as_json) -> int:
    preset = _resolve_preset(presets, args.preset)
    if preset is None:
        return _fail("error: no preset %r" % args.preset)
    new_name = args.new_name.strip()
    if not new_name or len(new_name.encode("utf-8")) > DEVICE_NAME_MAX_BYTES:
        return _fail("error: name must be 1..%d UTF-8 bytes" % DEVICE_NAME_MAX_BYTES,
                     ExitCode.USAGE)
    rename_preset(dev, preset["index"], new_name)
    after = read_presets(dev)
    renamed = any(p["name"] == new_name for p in after["presets"])
    report = {
        "from": preset["name"],
        "to": new_name,
        "index": preset["index"],
        "renamed": renamed,
    }
    _emit(report, ["renamed %r -> %r (%s)"
                   % (preset["name"], new_name,
                      "ok" if renamed else "not observed")], as_json)
    return 0 if renamed else 3


def _peq_overwrite(dev, args, presets, preset_blocks, as_json) -> int:
    target = _resolve_preset(presets, args.preset)
    if target is None:
        return _fail("error: no preset %r" % args.preset)

    if args.last:
        preset = _load_last_preview()
        if preset is None:
            return _fail("error: nothing to reuse yet; run 'dx5ii peq preview' first")
        source_label = "last preview (%s)" % preset.get("name")
    elif args.copy:
        source = _resolve_preset(presets, args.copy)
        if source is None:
            return _fail("error: no preset %r to copy" % args.copy)
        source_label = "copy of %s" % source["name"]
        preset = {
            "name": target["name"],
            "preamp_left": source["preamp_left"],
            "preamp_right": source["preamp_right"],
            "enabled_left": source["enabled_left"],
            "enabled_right": source["enabled_right"],
            "bands_left": source["bands_left"],
            "bands_right": source["bands_right"],
        }
    else:
        base = args.from_apo.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        try:
            preset = _apo_preset(args.from_apo, dev.info.get("product"),
                                 target["name"])
        except ValueError as exc:
            return _fail("error: %s" % exc, ExitCode.USAGE)
        source_label = "apo:%s" % base

    words = live_words(preset)
    if args.backup and target["index"] < len(preset_blocks):
        with io.open(args.backup, "w", encoding="utf-8") as handle:
            json.dump({"index": target["index"],
                       "name": target["name"],
                       "words": preset_blocks[target["index"]]},
                      handle, indent=2)
            handle.write("\n")

    if args.dry_run:
        report = {
            "slot": target["index"],
            "name": target["name"],
            "source": source_label,
            "commands": len(words),
            "dry_run": True,
            "backup": args.backup,
        }
        text = ["dry run: %d commands would go into slot %d (%s) from %s"
                % (len(words), target["index"], target["name"], source_label)]
        text += ["  0x%04X = 0x%08X" % (cmd, value & 0xFFFFFFFF)
                 for cmd, value in words[:4]]
        _emit(report, text, as_json)
        return 0

    if presets_equal(target, preset):
        report = {
            "slot": target["index"],
            "name": target["name"],
            "source": source_label,
            "changed": False,
            "written": False,
        }
        _emit(report, ["slot %d (%s) already matches %s; nothing written"
                       % (target["index"], target["name"], source_label)], as_json)
        return 0

    result = overwrite_preset(dev, target["index"], preset,
                              presets=presets, blocks=preset_blocks)
    report = {
        "slot": target["index"],
        "name": target["name"],
        "source": source_label,
        "bands": sum(1 for b in preset["bands_left"] if b["enabled"]),
        "preamp_left": preset["preamp_left"],
        "preamp_right": preset["preamp_right"],
        "changed": True,
        "verified": result["verified"],
        "switched": result["switched"],
        "commands": result["commands"],
    }
    text = ["overwrote slot %d (%s) with %d bands from %s"
            % (target["index"], target["name"], report["bands"], source_label),
            "  preamp %s" % format_preamp(preset["preamp_left"],
                                          preset["preamp_right"]),
            "  verified: %s"
            % ("yes" if result["verified"] else
               "skipped" if result["verified"] is None else "no")]
    if result["switched"]:
        text.append("  note: the output switched to slot %d and back"
                    % target["index"])
    if not args.backup:
        text.append("hint: use --backup to keep a copy before overwriting")
    _emit(report, text, as_json)
    return 0 if result["verified"] is not False else 3


def _peq_switch(dev, args, snap, as_json) -> int:
    """Switch to another preset; the same report ``--set-config`` prints."""
    configs = snap.get("configs") or {}
    try:
        target = resolve_config(args.slot, configs.get("slots") or [])
    except Dx5iiError as exc:
        return _fail(f"error: {exc}")
    report = apply_changes(dev, snap, None, target)
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_switch_report(report)
    return 0 if report["ok"] else 3


_PEQ_COMMANDS = {
    "preview": _peq_preview,
    "list": _peq_list,
    "show": _peq_show,
    "create": _peq_create,
    "import": _peq_import,
    "copy": _peq_copy,
    "export": _peq_export,
    "delete": _peq_delete,
    "rename": _peq_rename,
    "overwrite": _peq_overwrite,
    "switch": _peq_switch,
}


def _cmd_peq(args: argparse.Namespace) -> int:
    path = getattr(args, "path", None)
    as_json = bool(getattr(args, "json", False))
    trace = getattr(args, "trace", None)
    if args.peq_command is None:
        return _cmd_configs(path, as_json, trace)
    if args.peq_command == "list":
        _warn("peq list", "use 'dx5ii peq'")
        return _cmd_configs(path, as_json, trace)
    handler = _PEQ_COMMANDS.get(args.peq_command)
    if handler is None:
        raise AssertionError(f"unhandled peq command {args.peq_command!r}")
    try:
        with _open(path, trace) as dev:
            if args.peq_command == "switch":
                # Switching needs the whole state (settings, preview, slots) to
                # produce the same report --set-config prints, and reading the
                # slots twice would cost another full upload.
                return _peq_switch(dev, args, dev.snapshot(include_configs=True), as_json)
            presets, preset_blocks = read_preset_blocks(dev)
            return handler(dev, args, presets, preset_blocks, as_json)
    except Dx5iiError as exc:
        return _fail(f"error: {exc}")




# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def _run_subcommand(args: argparse.Namespace) -> int:
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "devices":
        return _cmd_devices(args)
    if args.command == "output":
        return _cmd_output(args)
    if args.command == "get":
        return _cmd_get(args.path, args.fields, args.json, args.trace)
    if args.command == "set":
        return _cmd_set(args.path, args.pairs, args.json, args.trace)
    if args.command == "raw":
        return _cmd_raw(args.path, args)
    if args.command == "watch":
        return _cmd_watch(args)
    if args.command == "configs":
        _warn("configs", "use 'dx5ii peq'")
        return _cmd_configs(args.path, args.json, args.trace)
    if args.command == "peq":
        return _cmd_peq(args)
    if args.command == "manual":
        return _cmd_manual(args)
    raise AssertionError(f"unhandled command {args.command!r}")


def _read_snapshot(args: argparse.Namespace) -> int:
    """Read the state, optionally switching the port and/or preset first."""
    set_output = getattr(args, "set_output", None)
    set_config = getattr(args, "set_config", None)
    fast = getattr(args, "fast", False)
    raw = getattr(args, "raw", False)
    write_mode = bool(set_output or set_config)
    try:
        with _open(args.path, args.trace) as dev:
            if write_mode:
                snap = dev.snapshot(include_configs=True, keep_raw=raw)
                report = apply_changes(dev, snap, set_output, set_config)
            else:
                snap = dev.snapshot(include_configs=not fast, keep_raw=raw)
                report = None
    except Dx5iiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return int(ExitCode.DEVICE)

    if report is None:
        if args.json:
            print(json.dumps(snap, indent=2, ensure_ascii=False))
        else:
            print_snapshot(snap)
        return int(ExitCode.OK)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_switch_report(report)
    return int(ExitCode.OK) if report["ok"] else int(ExitCode.MISMATCH)


def _cmd_status(args: argparse.Namespace) -> int:
    """``dx5ii status``: same output as the bare ``dx5ii``."""
    return _read_snapshot(args)


def _cmd_devices(args: argparse.Namespace) -> int:
    """``dx5ii devices``: list the matching HID interfaces."""
    printable = [{k: v for k, v in item.items() if k != "path_bytes"}
                 for item in list_interfaces()]
    if args.json:
        print(json.dumps(printable, indent=2, ensure_ascii=False))
        return int(ExitCode.OK)
    for item in printable:
        marker = " <- protocol" if item["interface_number"] == 2 else ""
        print("iface %-2s usage_page=%-3s usage=%-2s %s  firmware=%s%s"
              % (item["interface_number"], item["usage_page"],
                 item["usage"], item["product"],
                 format_firmware(item.get("release_number")),
                 marker))
    return int(ExitCode.OK)


def _cmd_output(args: argparse.Namespace) -> int:
    """``dx5ii output NAME``: switch the output port and report both sides."""
    try:
        with _open(args.path, args.trace) as dev:
            snap = dev.snapshot(include_configs=True)
            report = apply_changes(dev, snap, args.name, None)
    except Dx5iiError as exc:
        return _fail("error: %s" % exc)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_switch_report(report)
    return int(ExitCode.OK) if report["ok"] else int(ExitCode.MISMATCH)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point; returns the process exit code."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - stream may be closed
            pass

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command:
        try:
            return _run_subcommand(args)
        except Dx5iiError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return int(ExitCode.DEVICE)

    # Legacy flag spellings; they go away in 0.1.2.
    if args.devices:
        _warn("--devices", "use 'dx5ii devices'")
        return _cmd_devices(args)
    if args.set_output:
        _warn("--set-output", "use 'dx5ii output <name>'")
    if args.set_config:
        _warn("--set-config", "use 'dx5ii peq switch <preset>'")
    if args.fast:
        _warn("--fast", "use 'dx5ii status --fast'")
    if args.raw:
        _warn("--raw", "use 'dx5ii status --frames'")

    return _read_snapshot(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
