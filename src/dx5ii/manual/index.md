# dx5ii - user manual

`dx5ii` reads and writes the settings of a TOPPING DX5 II DAC over USB HID
(VID `0x152A`, PID `0x8750`, control protocol on interface `MI_02`).

This manual is shipped inside the package. Print a section with
`dx5ii manual <section>`; list the sections with `dx5ii manual --list`.

| section | contents |
|---|---|
| `commands` | every subcommand, its arguments and exit codes |
| `fields` | the writable settings and how to name their values |
| `peq` | EQ presets: list, show, create, rename, delete, audition, overwrite |
| `observe` | raw frames, `watch`, `--trace` |
| `troubleshooting` | when the device is not found, or a write seems ignored |

## Install

```
pip install dx5ii-cli
```

Requires Python 3.9 or newer and a 64-bit Windows install with the HID driver
the device already uses. No extra driver is needed.

## First run

```
dx5ii                  # print the current state as text
dx5ii --json           # the same as one JSON document
dx5ii get volume       # one field only, faster than a full read
```

A full read takes about 1.5 seconds. Most of that is the device sending its
preset list: 5 presets of 78 words each, at roughly 2 ms per frame. Reading a
single field with `get` skips the preset list and takes about 0.4 seconds.

## Command overview

| command | what it does |
|---|---|
| `dx5ii` | read everything; optionally switch port and preset first |
| `dx5ii get <field>...` | read named fields |
| `dx5ii set <field> <value>...` | write fields, then read back |
| `dx5ii configs` | list the EQ presets stored on the device |
| `dx5ii peq <subcommand>` | preset contents (see the peq section) |
| `dx5ii raw <cmd> [data]` | send one frame, print the replies; `--force` for dangerous commands |
| `dx5ii watch [seconds]` | count frames for a while, or `-f` to stream them |
| `dx5ii manual [section]` | this manual |
| `dx5ii --devices` | list matching HID interfaces |
| `dx5ii --version` | print the version |

## Two calling styles

`dx5ii` with no subcommand reads the whole state and is what the game watcher
calls. `dx5ii <command> ...` is for scripts and one-off changes.

Options that switch state before reading (`--set-output`, `--set-config`) are
accepted on the bare form only, because they change what the read reports.

## Exit codes

| code | meaning |
|---|---|
| 0 | success |
| 2 | the device rejected the request, or an argument was invalid |
| 3 | a write was sent but reading the state back shows it did not take effect |

`set` also reports per-field `echo` and `confirmed` in `--json`. `echo` means
the device acknowledged the frame; only `confirmed` means the stored value
matches what was sent.

## What this tool does not do

- It never issues `0x712E` (firmware update) or `0x710B` (factory reset);
  `raw` refuses them unless `--force` is given.
- It cannot write the cloud PEQ configuration. The device plays it, but the
  content lives on the TOPPING account, not on the device.
- It does not decode the VU / FFT telemetry frames into numbers; `watch` shows
  them raw.
