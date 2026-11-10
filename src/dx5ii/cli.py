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
import sys
import tempfile
import textwrap
import time
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
from .fields import FIELD_ACTIONS, FIELD_SPECS, encode_field, field_raw, field_state
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
        "exit codes: 0 ok, 2 device error, 3 write did not take effect"
        % (len(FIELD_SPECS), fields, len(FIELD_ACTIONS), actions)
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser (also used by ``--help``)."""
    parser = argparse.ArgumentParser(
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

    p_peq = sub.add_parser(
        "peq", help="manage EQ presets",
        description="Manage preset contents. The PEQ on/off switch is the "
                    "`peq` field: `dx5ii set peq on` / `dx5ii set peq off`.")
    peq_sub = p_peq.add_subparsers(dest="peq_command", required=True)

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
    p_pcreate.add_argument("--activate", action="store_true",
                           help="keep the new preset active (default: restore "
                                "whatever was active before)")
    p_pcreate.add_argument("--json", action="store_true")
    p_pcreate.add_argument("--path")
    add_trace(p_pcreate)

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
            print(f"error: unknown field {name!r}", file=sys.stderr)
            return 2
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


def _cmd_set(path: Optional[str], pairs: Sequence[str], as_json: bool,
             trace: Optional[str] = None) -> int:
    if len(pairs) % 2 != 0:
        print("error: set needs field/value pairs", file=sys.stderr)
        return 2
    items = [(pairs[i], pairs[i + 1]) for i in range(0, len(pairs), 2)]

    with _open(path, trace) as dev:
        before = dev.snapshot(include_configs=False)
        results: list[dict[str, Any]] = []
        for name, value in items:
            for cmd, data, total, cur in encode_field(name, value, before["settings"]):
                dev.send(cmd, data, PT_WRITE, total, cur)
            time.sleep(0.35)
            results.append({"field": name, "requested": value})
        time.sleep(0.25)
        after = dev.snapshot(include_configs=False)

    ok = True
    for item in results:
        name = item["field"]
        item["from"] = field_state(before, name)
        item["observed"] = field_state(after, name)
        if isinstance(item["from"], str) or isinstance(item["observed"], str):
            item["from_raw"] = field_raw(before, name)
            item["observed_raw"] = field_raw(after, name)
        item["changed"] = item["from"] != item["observed"]
        if not item["changed"] and name not in FIELD_ACTIONS:
            ok = False
            item["note"] = "read-back unchanged"

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
            else:
                note = "" if item["changed"] else "   (unchanged)"
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


def _cmd_configs(path: Optional[str], as_json: bool,
                 trace: Optional[str] = None) -> int:
    with _open(path, trace) as dev:
        snap = dev.snapshot(include_configs=True)
    configs = snap["configs"]
    if as_json:
        print(json.dumps(configs, indent=2, ensure_ascii=False))
        return 0
    print_configs(configs)
    return 0


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


def _cmd_peq(args: argparse.Namespace) -> int:
    path = getattr(args, "path", None)
    as_json = bool(getattr(args, "json", False))

    try:
        with _open(path, args.trace) as dev:
            presets, preset_blocks = read_preset_blocks(dev)

            if args.peq_command == "preview":
                if args.stop:
                    state = leave_preview(dev)
                    if as_json:
                        print(json.dumps(state, indent=2, ensure_ascii=False))
                    else:
                        print("preview stopped; back to slot %s (%s)"
                              % (presets.get("stored_index"),
                                 presets.get("stored_name")))
                    return 0 if not state.get("preview_active") else 3

                if args.from_apo:
                    text = io.open(args.from_apo, encoding="utf-8",
                                   errors="replace").read()
                    bands = parse_apo_filters(
                        text, device=dev.info.get("product"))
                    if not bands:
                        print("error: no filters parsed from %s"
                              % args.from_apo, file=sys.stderr)
                        return 2
                    preamp = parse_apo_preamp(
                        text, device=dev.info.get("product")) or 0.0
                    preset = {
                        "name": args.from_apo.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
                        "preamp_left": preamp,
                        "preamp_right": preamp,
                        "enabled_left": True,
                        "enabled_right": True,
                        "bands_left": [b.as_dict() for b in bands],
                        "bands_right": [b.as_dict() for b in bands],
                    }
                elif args.preset:
                    preset = _resolve_preset(presets, args.preset)
                    if preset is None:
                        print("error: no preset %r" % args.preset,
                              file=sys.stderr)
                        return 2
                else:
                    print("error: name a preset to preview, pass --from-apo, "
                          "or pass --stop", file=sys.stderr)
                    return 2
                try:
                    state = enter_preview(dev, preset)
                except BaseException:
                    # Ctrl-C in the middle of the handshake would leave the
                    # device playing half a config; put it back before dying.
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
                if as_json:
                    print(json.dumps(report, indent=2, ensure_ascii=False))
                else:
                    print("previewing %r (audition, %d bands); the stored slot "
                          "%s (%s) is untouched"
                          % (preset["name"], report["bands"],
                             report["stored_index"], report["stored_name"]))
                    print("run 'dx5ii peq preview --stop' to go back")
                return 0 if ok else 3

            if args.peq_command == "list":
                if as_json:
                    print(json.dumps(presets, indent=2, ensure_ascii=False))
                    return 0
                print("stored=%s (%s) count=%s"
                      % (presets.get("stored_index"),
                         presets.get("stored_name"),
                         presets.get("count")))
                for preset in presets["presets"]:
                    used = sum(1 for b in preset["bands_left"] if b["enabled"])
                    mark = "<- stored" if preset["stored_current"] else ""
                    if not preset.get("enabled_left", True):
                        mark = (mark + "  L disabled").strip()
                    print("  %d  %-24s %2d bands  preamp %s %s"
                          % (preset["index"], preset["name"], used,
                             format_preamp(preset["preamp_left"],
                                           preset["preamp_right"]), mark))
                return 0

            if args.peq_command == "show":
                preset = _resolve_preset(presets, args.preset)
                if preset is None:
                    print("error: no preset %r" % args.preset, file=sys.stderr)
                    return 2
                if as_json:
                    print(json.dumps(preset, indent=2, ensure_ascii=False))
                else:
                    _print_bands(preset, "both")
                return 0

            if args.peq_command == "create":
                name = args.name.strip()
                if not name:
                    print("error: empty preset name", file=sys.stderr)
                    return 2
                if len(name.encode("utf-8")) > CONFIG_NAME_WORDS * 4:
                    print("error: name longer than %d UTF-8 bytes"
                          % (CONFIG_NAME_WORDS * 4), file=sys.stderr)
                    return 2
                if find_preset(presets, name):
                    print("error: preset %r already exists; refusing to "
                          "overwrite an existing preset" % name, file=sys.stderr)
                    return 2

                head = None
                if args.copy:
                    source = _resolve_preset(presets, args.copy)
                    if source is None:
                        print("error: no preset %r to copy" % args.copy,
                              file=sys.stderr)
                        return 2
                    left, right = _used_bands(source)
                    head = [1, encode_preamp(source["preamp_left"]),
                            1, encode_preamp(source["preamp_right"])]
                else:
                    text = io.open(args.from_apo, encoding="utf-8",
                                   errors="replace").read()
                    left = parse_apo_filters(text,
                                             device=dev.info.get("product"))
                    if not left:
                        print("error: no filters parsed from %s" % args.from_apo,
                              file=sys.stderr)
                        return 2
                    right = list(left)
                    preamp = parse_apo_preamp(text,
                                              device=dev.info.get("product"))
                    if preamp is not None:
                        word = encode_preamp(preamp)
                        head = [1, word, 1, word]

                index = create_preset(dev, name, left, right, head=head)
                # Creating a preset makes the device activate it; put the old
                # one back unless the caller asked for the new one.
                if not args.activate:
                    now = dev.current_config_index()
                    if now != presets.get("stored_index"):
                        dev.send(CMD_SWITCH_MCU_CONFIG,
                                 presets.get("stored_index") or 0, PT_WRITE)
                        time.sleep(0.15)
                after = read_presets(dev)
                found = find_preset(after, name)
                report = {
                    "created": name,
                    "index": index,
                    "found": found is not None,
                    "count_before": presets.get("count"),
                    "count_after": after.get("count"),
                    "bands": len(left),
                    "stored_index": after.get("stored_index"),
                }
                if as_json:
                    print(json.dumps(report, indent=2, ensure_ascii=False))
                else:
                    print("created %r at index %d (count %s -> %s)"
                          % (name, index, report["count_before"],
                             report["count_after"]))
                return 0 if report["found"] else 3

            if args.peq_command == "delete":
                preset = _resolve_preset(presets, args.preset)
                if preset is None:
                    print("error: no preset %r" % args.preset, file=sys.stderr)
                    return 2
                target = _resolve_preset(presets, args.switch)
                if target is None:
                    print("error: no preset %r to switch to" % args.switch,
                          file=sys.stderr)
                    return 2
                if target["index"] == preset["index"]:
                    print("error: --switch must name a different preset",
                          file=sys.stderr)
                    return 2
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
                if as_json:
                    print(json.dumps(report, indent=2, ensure_ascii=False))
                else:
                    print("deleted %r (count %s -> %s), stored=%s"
                          % (preset["name"], report["count_before"],
                             report["count_after"], report["stored_index"]))
                return 0

            if args.peq_command == "rename":
                preset = _resolve_preset(presets, args.preset)
                if preset is None:
                    print("error: no preset %r" % args.preset, file=sys.stderr)
                    return 2
                new_name = args.new_name.strip()
                if not new_name or len(new_name.encode("utf-8")) > DEVICE_NAME_MAX_BYTES:
                    print("error: name must be 1..%d UTF-8 bytes"
                          % DEVICE_NAME_MAX_BYTES, file=sys.stderr)
                    return 2
                rename_preset(dev, preset["index"], new_name)
                after = read_presets(dev)
                renamed = any(p["name"] == new_name for p in after["presets"])
                report = {
                    "from": preset["name"],
                    "to": new_name,
                    "index": preset["index"],
                    "renamed": renamed,
                }
                if as_json:
                    print(json.dumps(report, indent=2, ensure_ascii=False))
                else:
                    print("renamed %r -> %r (%s)"
                          % (preset["name"], new_name,
                             "ok" if renamed else "not observed"))
                return 0 if renamed else 3

            if args.peq_command == "overwrite":
                target = _resolve_preset(presets, args.preset)
                if target is None:
                    print("error: no preset %r" % args.preset, file=sys.stderr)
                    return 2

                if args.last:
                    preset = _load_last_preview()
                    if preset is None:
                        print("error: nothing to reuse yet; run "
                              "'dx5ii peq preview' first", file=sys.stderr)
                        return 2
                    source_label = "last preview (%s)" % preset.get("name")
                elif args.copy:
                    source = _resolve_preset(presets, args.copy)
                    if source is None:
                        print("error: no preset %r to copy" % args.copy,
                              file=sys.stderr)
                        return 2
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
                    text = io.open(args.from_apo, encoding="utf-8",
                                   errors="replace").read()
                    bands = parse_apo_filters(text,
                                              device=dev.info.get("product"))
                    if not bands:
                        print("error: no filters parsed from %s" % args.from_apo,
                              file=sys.stderr)
                        return 2
                    preamp = parse_apo_preamp(text,
                                              device=dev.info.get("product"))
                    preamp = 0.0 if preamp is None else preamp
                    base = args.from_apo.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
                    source_label = "apo:%s" % base
                    preset = {
                        "name": target["name"],
                        "preamp_left": preamp,
                        "preamp_right": preamp,
                        "enabled_left": True,
                        "enabled_right": True,
                        "bands_left": [b.as_dict() for b in bands],
                        "bands_right": [b.as_dict() for b in bands],
                    }

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
                    if as_json:
                        print(json.dumps(report, indent=2, ensure_ascii=False))
                    else:
                        print("dry run: %d commands would go into slot %d (%s) "
                              "from %s" % (len(words), target["index"],
                                           target["name"], source_label))
                        for cmd, value in words[:4]:
                            print("  0x%04X = 0x%08X" % (cmd, value & 0xFFFFFFFF))
                    return 0

                if presets_equal(target, preset):
                    report = {
                        "slot": target["index"],
                        "name": target["name"],
                        "source": source_label,
                        "changed": False,
                        "written": False,
                    }
                    if as_json:
                        print(json.dumps(report, indent=2, ensure_ascii=False))
                    else:
                        print("slot %d (%s) already matches %s; nothing written"
                              % (target["index"], target["name"],
                                 source_label))
                    return 0

                result = overwrite_preset(dev, target["index"], preset,
                                          presets=presets,
                                          blocks=preset_blocks)
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
                if as_json:
                    print(json.dumps(report, indent=2, ensure_ascii=False))
                else:
                    print("overwrote slot %d (%s) with %d bands from %s"
                          % (target["index"], target["name"], report["bands"],
                             source_label))
                    print("  preamp %s" % format_preamp(preset["preamp_left"],
                                                        preset["preamp_right"]))
                    print("  verified: %s"
                          % ("yes" if result["verified"] else
                             "skipped" if result["verified"] is None else "no"))
                    if result["switched"]:
                        print("  note: the output switched to slot %d and back"
                              % target["index"])
                    if not args.backup:
                        print("hint: use --backup to keep a copy before "
                              "overwriting")
                return 0 if result["verified"] is not False else 3
    except Dx5iiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    raise AssertionError(f"unhandled peq command {args.peq_command!r}")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def _run_subcommand(args: argparse.Namespace) -> int:
    if args.command == "get":
        return _cmd_get(args.path, args.fields, args.json, args.trace)
    if args.command == "set":
        return _cmd_set(args.path, args.pairs, args.json, args.trace)
    if args.command == "raw":
        return _cmd_raw(args.path, args)
    if args.command == "watch":
        return _cmd_watch(args)
    if args.command == "configs":
        return _cmd_configs(args.path, args.json, args.trace)
    if args.command == "peq":
        return _cmd_peq(args)
    raise AssertionError(f"unhandled command {args.command!r}")


def _read_snapshot(args: argparse.Namespace) -> int:
    """No subcommand: read the state, optionally switching first."""
    write_mode = bool(args.set_output or args.set_config)
    try:
        with _open(args.path, args.trace) as dev:
            if write_mode:
                snap = dev.snapshot(include_configs=True, keep_raw=args.raw)
                report = apply_changes(dev, snap, args.set_output, args.set_config)
            else:
                snap = dev.snapshot(include_configs=not args.fast,
                                    keep_raw=args.raw)
                report = None
    except Dx5iiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if report is None:
        if args.json:
            print(json.dumps(snap, indent=2, ensure_ascii=False))
        else:
            print_snapshot(snap)
        return 0

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_switch_report(report)
    return 0 if report["ok"] else 3


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
            return 2

    if args.devices:
        interfaces = list_interfaces()
        printable = [{k: v for k, v in item.items() if k != "path_bytes"}
                     for item in interfaces]
        if args.json:
            print(json.dumps(printable, indent=2, ensure_ascii=False))
        else:
            for item in printable:
                marker = " <- protocol" if item["interface_number"] == 2 else ""
                print("iface %-2s usage_page=%-3s usage=%-2s %s  firmware=%s%s"
                      % (item["interface_number"], item["usage_page"],
                         item["usage"], item["product"],
                         format_firmware(item.get("release_number")),
                         marker))
        return 0

    return _read_snapshot(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
