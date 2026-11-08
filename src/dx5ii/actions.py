"""High-level switches: "make the device look like X", with before/after.

``apply_changes`` is what the game watcher uses: it switches the output port
first, then the EQ preset, and reports both the state it found and the state
it left behind.  The ``before`` block is what a caller needs in order to roll
back later.
"""

from __future__ import annotations

from typing import Any, Optional

from .device import Dx5ii
from .errors import Dx5iiError
from .fields import resolve_config, resolve_output


def state_summary(snap: dict[str, Any]) -> dict[str, Any]:
    """Condense a snapshot to the four values a caller cares about."""
    settings = snap.get("settings") or {}
    configs = snap.get("configs") or {}
    preview = snap.get("peq") or {}

    index = configs.get("current_index")
    if index is None and preview.get("preview_active") is False:
        index = preview.get("local_index")
    return {
        "output": settings.get("output"),
        "output_raw": settings.get("output_raw"),
        "input": settings.get("input"),
        "input_raw": settings.get("input_raw"),
        "config_index": index,
        "config_name": configs.get("current_name"),
        "preview_active": preview.get("preview_active"),
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
    known = {item["index"]: item["name"] for item in (configs.get("configs") or [])}
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
                        "config_index": new_index,
                        "config_name": known.get(new_index),
                    })
                else:
                    report["errors"].append(
                        f"output did not change to {raw!r} "
                        f"(observed {(got or {}).get('output_raw')!r})")

    if want_config is not None:
        try:
            target = resolve_config(want_config, configs.get("configs") or [])
        except Dx5iiError as exc:
            report["ok"] = False
            report["errors"].append(str(exc))
        else:
            start = before.get("config_index")
            if start is None:
                start = dev.current_config_index()
            if start == target:
                report["actions"].append({
                    "what": "config", "from": start, "to": target, "changed": False})
            else:
                got = dev.set_config_index(target)
                ok = got == target
                report["changed"] = report["changed"] or ok
                report["ok"] = report["ok"] and ok
                report["actions"].append({
                    "what": "config", "from": start, "to": target, "changed": ok,
                    "observed": got,
                })
                if not ok:
                    report["errors"].append(
                        f"config did not change to {target!r} (observed {got!r})")

    final = dev.snapshot(include_configs=False, keep_raw=False)
    summary = state_summary(final)
    if summary["config_index"] is None:
        try:
            summary["config_index"] = dev.current_config_index()
        except Dx5iiError:
            pass
    if summary["config_index"] is not None:
        summary["config_name"] = known.get(summary["config_index"])
    report["after"] = summary

    if summary["output"] is None:
        report["ok"] = False
        report["errors"].append("could not read state back after the switch")
    return report


__all__ = ["apply_changes", "state_summary"]
