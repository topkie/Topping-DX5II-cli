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
import sys
import textwrap
import time
from typing import Any, Optional, Sequence

from .actions import apply_changes, state_summary
from .bands import CONFIG_NAME_WORDS, DEVICE_NAME_MAX_BYTES, Band, parse_apo_filters
from .constants import (
    CMD_SWITCH_MCU_CONFIG,
    DESTRUCTIVE_COMMANDS,
    PT_READ,
    PT_WRITE,
)
from .device import Dx5ii, list_interfaces
from .errors import Dx5iiError
from .fields import FIELD_ACTIONS, FIELD_SPECS, encode_field, field_raw, field_state
from .formatting import (
    print_configs,
    print_field_rows,
    print_snapshot,
    print_switch_report,
)
from . import __version__
from .peq import (
    create_preset,
    delete_preset,
    find_preset,
    read_presets,
    rename_preset,
)
from .protocol import format_firmware


def _open(path: Optional[str] = None) -> Dx5ii:
    return Dx5ii(path=path)


def _epilog() -> str:
    fields = textwrap.fill(
        ", ".join(FIELD_SPECS), width=74,
        initial_indent="    ", subsequent_indent="    ")
    actions = textwrap.fill(
        ", ".join(FIELD_ACTIONS), width=74,
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
    parser.add_argument("--set-output", metavar="NAME",
                        help="switch the output port, e.g. hp_balanced")
    parser.add_argument("--set-config", metavar="NAME|INDEX",
                        help="switch the active EQ preset, e.g. IER-M7.Harman")

    sub = parser.add_subparsers(dest="command", metavar="command")

    def add_common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--json", action="store_true", help="emit JSON")
        target.add_argument("--path", help="explicit HID path (interface 2)")

    p_get = sub.add_parser("get", help="print current state")
    p_get.add_argument("fields", nargs="*", metavar="field",
                       help="field names (default: all)")
    add_common(p_get)

    p_set = sub.add_parser("set", help="write one or more fields, then read back")
    p_set.add_argument("pairs", nargs="+", metavar="field value")
    add_common(p_set)

    p_raw = sub.add_parser("raw", help="send one frame and print the reply")
    p_raw.add_argument("cmd", help="command number, e.g. 0x7137")
    p_raw.add_argument("data", nargs="?", help="payload word")
    p_raw.add_argument("--pt", choices=("read", "write"), default="write")
    p_raw.add_argument("--total", type=lambda s: int(s, 0), default=1)
    p_raw.add_argument("--cur", type=lambda s: int(s, 0), default=1)
    p_raw.add_argument("--path")
    p_raw.add_argument("--force", action="store_true",
                       help="allow commands that cannot be undone "
                            "(factory reset, firmware update mode)")

    p_watch = sub.add_parser("watch", help="stream frames the device pushes")
    p_watch.add_argument("seconds", nargs="?", type=int, default=3)
    p_watch.add_argument("--path")

    p_cfg = sub.add_parser("configs", help="list the EQ presets on the device")
    add_common(p_cfg)

    p_peq = sub.add_parser("peq", help="manage EQ presets")
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

    p_pdel = peq_sub.add_parser("delete", help="delete a preset")
    p_pdel.add_argument("preset", help="preset name or index")
    p_pdel.add_argument("--switch", required=True, metavar="PRESET",
                        help="preset to activate after deletion")
    p_pdel.add_argument("--json", action="store_true")
    p_pdel.add_argument("--path")

    p_pren = peq_sub.add_parser("rename", help="rename a preset")
    p_pren.add_argument("preset", help="preset name or index")
    p_pren.add_argument("new_name")
    p_pren.add_argument("--json", action="store_true")
    p_pren.add_argument("--path")

    return parser


# ---------------------------------------------------------------------------
# subcommand implementations
# ---------------------------------------------------------------------------

def _cmd_get(path: Optional[str], fields: Sequence[str], as_json: bool) -> int:
    # A named field comes from ``settings`` or ``pushed`` only, so skip the
    # sampling and preview requests; ``get all`` keeps the full snapshot.
    light = bool(fields) and list(fields) != ["all"]
    with _open(path) as dev:
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


def _cmd_set(path: Optional[str], pairs: Sequence[str], as_json: bool) -> int:
    if len(pairs) % 2 != 0:
        print("error: set needs field/value pairs", file=sys.stderr)
        return 2
    items = [(pairs[i], pairs[i + 1]) for i in range(0, len(pairs), 2)]

    with _open(path) as dev:
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
    with _open(path) as dev:
        dev.send(cmd, data, protocol, args.total, args.cur)
        frames = dev.read_for(800)

    for frame in frames:
        print("cmd=0x%04X pt=0x%02X total=%d cur=%d data=%d (0x%X)"
              % (frame["cmd"], frame["protocol"], frame["total"],
                 frame["index"], frame["data"], frame["data"]))
    print(f"{len(frames)} frames")
    return 0


def _cmd_watch(path: Optional[str], seconds: int) -> int:
    with _open(path) as dev:
        print(f"watching push frames for {seconds}s ...")
        frames = dev.read_for(seconds * 1000)
    counts: dict[tuple[int, int], int] = {}
    for frame in frames:
        key = (frame["cmd"], frame["protocol"])
        counts[key] = counts.get(key, 0) + 1
    for (cmd, protocol), count in sorted(counts.items()):
        print("cmd=0x%04X pt=0x%02X  x%d" % (cmd, protocol, count))
    print(f"{len(frames)} frames total")
    return 0


def _cmd_configs(path: Optional[str], as_json: bool) -> int:
    with _open(path) as dev:
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


def _print_bands(preset: dict[str, Any], which: str) -> None:
    print("%s  (index %d%s)" % (preset["name"], preset["index"],
                               ", stored" if preset["stored_current"] else ""))
    for channel, key in (("L", "bands_left"), ("R", "bands_right")):
        if which not in ("both", channel.lower()):
            continue
        for i, band in enumerate(preset[key]):
            if not band["enabled"] and i >= 10:
                continue
            kind = band["type"]
            state = "ON " if band["enabled"] else "OFF"
            print("  %s%2d  %s %-2s  Fc %7.1f Hz  Gain %+5.1f dB  Q %.4f"
                  % (channel, i + 1, state, kind, band["freq_hz"],
                     band["gain_db"], band["q"]))


def _cmd_peq(args: argparse.Namespace) -> int:
    path = getattr(args, "path", None)
    as_json = bool(getattr(args, "json", False))

    try:
        with _open(path) as dev:
            presets = read_presets(dev)

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
                    print("  %d  %-24s %2d bands  hash=0x%08X %s"
                          % (preset["index"], preset["name"], used,
                             preset["hash"], mark))
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

                if args.copy:
                    source = _resolve_preset(presets, args.copy)
                    if source is None:
                        print("error: no preset %r to copy" % args.copy,
                              file=sys.stderr)
                        return 2
                    left, right = _used_bands(source)
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

                index = create_preset(dev, name, left, right)
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
    except Dx5iiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    raise AssertionError(f"unhandled peq command {args.peq_command!r}")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def _run_subcommand(args: argparse.Namespace) -> int:
    if args.command == "get":
        return _cmd_get(args.path, args.fields, args.json)
    if args.command == "set":
        return _cmd_set(args.path, args.pairs, args.json)
    if args.command == "raw":
        return _cmd_raw(args.path, args)
    if args.command == "watch":
        return _cmd_watch(args.path, args.seconds)
    if args.command == "configs":
        return _cmd_configs(args.path, args.json)
    if args.command == "peq":
        return _cmd_peq(args)
    raise AssertionError(f"unhandled command {args.command!r}")


def _read_snapshot(args: argparse.Namespace) -> int:
    """No subcommand: read the state, optionally switching first."""
    write_mode = bool(args.set_output or args.set_config)
    try:
        with _open(args.path) as dev:
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
