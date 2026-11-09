"""High-level switches: "make the device look like X", with before/after.

``apply_changes`` is what the game watcher uses: it switches the output port
first, then the EQ preset, and reports both the state it found and the state
it left behind.  The ``before`` block is what a caller needs in order to roll
back later.
"""

from __future__ import annotations

from typing import Any, Optional

from .constants import PEQ_TEMP_MODES
from .device import Dx5ii
from .errors import Dx5iiError
from .fields import resolve_config, resolve_output
from .interpreter import resolve_playing


def needs_config_write(before: dict[str, Any], target: int) -> bool:
    """Whether ``0x110E`` has to be sent to make ``target`` the audible slot.

    Skipping is only safe when the device both stores ``target`` and plays a
    local slot.  While Topping Home plays a cloud or audition config the stored
    slot is kept but silent, so an equal index still needs the switch: that
    write is what ends the preview.  ``playing_source`` is ``"unknown"`` when
    ``0x7137`` did not answer and ``None`` when the snapshot was taken without
    the config block; both mean "cannot prove it is audible", so they write.
    """
    if before.get("stored_index") != target:
        return True
    return before.get("playing_source") != "local"


def state_summary(snap: dict[str, Any]) -> dict[str, Any]:
    """Condense a snapshot to the values a caller cares about.

    ``stored_*`` is the slot the device keeps selected; ``playing_*`` is what
    it is audible on.  They differ while Topping Home plays a cloud config:
    the slot is stored, the cloud config is what you hear.
    """
    settings = snap.get("settings") or {}
    configs = snap.get("configs") or {}
    playing = configs.get("playing")
    if playing is None:
        # A snapshot read without the preset list still knows what is audible;
        # the slot name is missing, which is honest rather than invented.
        playing = resolve_playing(configs, snap.get("peq"))
    return {
        "output": settings.get("output"),
        "output_raw": settings.get("output_raw"),
        "input": settings.get("input"),
        "input_raw": settings.get("input_raw"),
        "stored_index": configs.get("stored_index"),
        "stored_name": configs.get("stored_name"),
        "playing_source": playing.get("source"),
        "playing_slot_index": playing.get("slot_index"),
        "playing_slot_name": playing.get("slot_name"),
    }


def apply_changes(
    dev: Dx5ii,
    snap: dict[str, Any],
    want_output: Optional[str] = None,
    want_config: Optional[Any] = None,
) -> dict[str, Any]:
    """Switch the output port and/or the EQ preset, then read the state back.

    The output switch runs first because each port remembers its own EQ preset:
    switching the port may change the active preset, so the preset check has to
    happen afterwards.  Both switches are idempotent -- a value that already
    matches is left alone.

    Returns a report with ``ok``, ``changed``, ``before``, ``after``,
    ``actions`` and ``errors``.
    """
    configs = snap.get("configs") or {}
    known = {item["index"]: item["name"] for item in (configs.get("slots") or [])}
    settings = snap.get("settings") or {}

    report: dict[str, Any] = {
        "ok": True,
        "changed": False,
        "before": state_summary(snap),
        "after": None,
        "actions": [],
        "errors": [],
    }
    before = report["before"]

    if want_output is not None:
        try:
            raw = resolve_output(want_output)
        except Dx5iiError as exc:
            report["ok"] = False
            report["errors"].append(str(exc))
        else:
            start = settings.get("output_raw")
            if start == raw:
                report["actions"].append({
                    "what": "output", "from": start, "to": raw, "changed": False})
            else:
                got = dev.set_output(raw)
                ok = bool(got) and got.get("output_raw") == raw
                report["changed"] = report["changed"] or ok
                report["ok"] = report["ok"] and ok
                report["actions"].append({
                    "what": "output", "from": start, "to": raw, "changed": ok,
                    "observed": (got or {}).get("output"),
                })
                if ok:
                    # Each port remembers its own preset, so the value worth
                    # restoring is the one *after* the switch.
                    new_index = dev.current_config_index()
                    before.update({
                        "output": (got or {}).get("output"),
                        "output_raw": raw,
                        "stored_index": new_index,
                        "stored_name": known.get(new_index),
                    })
                else:
                    report["errors"].append(
                        f"output did not change to {raw!r} "
                        f"(observed {(got or {}).get('output_raw')!r})")

    if want_config is not None:
        try:
            target = resolve_config(want_config, configs.get("slots") or [])
        except Dx5iiError as exc:
            report["ok"] = False
            report["errors"].append(str(exc))
        else:
            start = before.get("stored_index")
            if start is None:
                start = dev.current_config_index()
            if not needs_config_write(before, target):
                report["actions"].append({
                    "what": "config", "from": start, "to": target, "changed": False})
            else:
                left_preview = before.get("playing_source")
                got = dev.set_config_index(target)
                ok = got == target
                report["changed"] = report["changed"] or ok
                report["ok"] = report["ok"] and ok
                action = {"what": "config", "from": start, "to": target,
                          "changed": ok, "observed": got}
                if left_preview in PEQ_TEMP_MODES:
                    # The slot did not move; what changed is that the device
                    # stopped playing Topping Home's temporary config.
                    action["left_preview"] = left_preview
                report["actions"].append(action)
                if not ok:
                    report["errors"].append(
                        f"config did not change to {target!r} (observed {got!r})")

    final = dev.snapshot(include_configs=False, keep_raw=False)
    summary = state_summary(final)
    if summary["stored_index"] is None:
        try:
            summary["stored_index"] = dev.current_config_index()
        except Dx5iiError:
            pass
    if summary["stored_index"] is not None:
        summary["stored_name"] = known.get(summary["stored_index"])
    if summary["playing_slot_index"] is not None:
        summary["playing_slot_name"] = known.get(summary["playing_slot_index"])
    report["after"] = summary

    if summary["output"] is None:
        report["ok"] = False
        report["errors"].append("could not read state back after the switch")
    return report


__all__ = ["apply_changes", "state_summary", "needs_config_write"]
