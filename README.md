# dx5ii

[![PyPI](https://img.shields.io/pypi/v/dx5ii-cli.svg)](https://pypi.org/project/dx5ii-cli/)
[![Python](https://img.shields.io/pypi/pyversions/dx5ii-cli.svg)](https://pypi.org/project/dx5ii-cli/)
[![CI](https://github.com/topkie/Topping-DX5II-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/topkie/Topping-DX5II-cli/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**English** | [简体中文](README.zh-CN.md)

Python package and CLI for the Topping DX5 II (VID `0x152A` / PID `0x8750`, control protocol on
HID `MI_02`): read every device setting, write individual fields, switch the output port and the EQ
preset, and manage the PEQ presets stored on the device.

The protocol has no public documentation. It was reverse engineered from the Topping Home web
bundle and then verified field by field on a real device (firmware 2.58). Frame layout, command
numbers and field maps live in the module docstrings of `src/dx5ii/constants.py` and
`src/dx5ii/protocol.py`.

Web Home support starts with DX5 II firmware `V2.39`. This CLI has only been verified against
firmware 2.58, so confirm the device is at 2.39 or newer — or update it to 2.58 — before relying
on it.

## Install

```bash
pip install dx5ii-cli
```

Only `hidapi` is required — the device just has to be reachable from Topping Web Home. The
official app itself does not need to be running.

Confirm the install:

```bash
dx5ii --version        # dx5ii version
dx5ii --devices        # list HID interfaces, check the DX5 II is visible
dx5ii --fast           # read the state once
```

`--devices` is the quickest connectivity check: it sends no protocol frames, it only enumerates HID.

From source:

```bash
git clone <repo> && cd dx5ii
pip install -e .
```

Without installing:

```bash
export PYTHONPATH=/path/to/dx5ii/src   # Windows PowerShell: $env:PYTHONPATH = '...\dx5ii\src'
python -m dx5ii get
```

## CLI

```bash
dx5ii                                   # full state, including the preset list
dx5ii --fast                            # same, skip the preset upload
dx5ii --json                            # JSON output
dx5ii --devices                         # list HID interfaces (+ firmware), mark the protocol one
dx5ii --set-output hp_balanced          # switch output only, then read back
dx5ii --set-config Dynamic              # switch preset by name or index
dx5ii --set-output hp_balanced --set-config Dynamic
```

The first line of `dx5ii` output carries the device's firmware version, read from the USB
`bcdDevice` descriptor (`release_number` in JSON), e.g. `firmware=2.58`.

`--json` keeps the readable name as the value and puts the device index beside it under
`<key>_raw`: `{"output": "hp_balanced", "output_raw": 4}`. The same holds for the `before` /
`after` blocks of a switch report. Option-name arrays carry their mask in the settings block
(`output_option_mask`), and booleans stay booleans. Scripts that prefer numbers read the `*_raw`
key; nothing has to be parsed out of a display string.

`--set-config` also ends a Topping Home cloud preview. While a cloud config is playing, the
device keeps its stored slot, so an index that already matches the stored slot still needs the
switch — that write is what returns the device to a local preset:

```
before   : output=hp_balanced  stored=2 (IER-M7.Harman)  playing=cloud
  config 2 -> 2  (left cloud preview)
after    : output=hp_balanced  stored=2 (IER-M7.Harman)  playing=local
```

Three rules for scripts:

- `changed` means the device state really changed. Leaving a preview counts, so `changed` is
  `true` even when `from == to`; what changed is `playing`, not `stored`.
- `left_preview` appears on the action only when a preview was ended; its value is `"cloud"` or
  `"audition"`. Ordinary switches do not carry the key.
- The top-level `changed` follows the same rule, and `ok` still means "the value read back after
  the switch equals the target".

Skipping is only safe when the device both stores the target and plays a local slot. If
`0x7137` does not answer, `playing_source` is `"unknown"` and the switch is sent anyway.

Field access:

```bash
dx5ii get                               # every field, grouped
dx5ii get output theme volume           # selected fields
dx5ii set brightness medium             # write one field, print before -> after
dx5ii set theme blue language en        # write several at once
dx5ii raw 0x7137 --pt read              # raw frame, print the reply
dx5ii raw 0x710C --pt read --total 1    # raw frame with explicit total/cur
dx5ii watch 5                           # dump unsolicited frames for 5 s
dx5ii watch -f                          # follow new frames until Ctrl-C (opens the push session)
dx5ii watch -f --cmd 0x7137 --json      # one JSON object per matching frame
```

`raw` prints the frames the device sends back: each line decodes `cmd`, `pt`, `total`, `cur`
and `data`, but nothing is interpreted into named fields and no scaling is applied. A `0x710C`
reply is 52 lines and `0x7137` is three, one 32-bit word each; `data` is shown in both decimal
and hex. When you send a multi-frame command yourself, set `--total` and `--cur`, because the
device only accepts consecutive frame numbers.

There are 43 fields; `dx5ii --help` lists them all. Grouped:

| Group | Fields |
|---|---|
| Power & host | `power` `power-trigger` `home-page` `theme` `brightness` `language` `screen-timeout` `dim-screen-type` `trigger-out` |
| I/O | `input` `output` `input-option` `output-option` `input-mode` `spdif-mode` `headphone-gain` `balance` `polarity` |
| Audio | `volume` `mute` `pcm-filter` `line-mode` `uac` `volume-step` `vu-bar-mode` `vu-level` `dc-detect` `crossfeed-type` `crossfeed-conv` `crossfeed-simple` `bluetooth` `aptx` `remote` |
| Memory | `volume-memory` `peq-memory` `crossfeed-memory` |
| Keys | `multifunction-key` `double-key` `remote-a-key` `remote-b-key` `remote-mute-mode` |
| Device | `device-name` (read-only) `peq` (on/off) |

Sample rate is not a field: it is reported by `0x710F` on its own and shows up in the `sampling`
section of the full `dx5ii` / `dx5ii get` output.

Accepted values: `on`/`off`, `true`/`false`, enum names (`hp_balanced`, `aurora`), indices, mask
lists (`input-option usb,bt`), volumes (`-40.0`) and timeouts (`off`/`10s`/`30s`/`60s`).

## PEQ presets

```bash
dx5ii configs                                    # current source, stored slot and slots
dx5ii peq list                                   # presets, stored one marked with ←
dx5ii peq show Dynamic                           # per-band freq/gain/Q, both channels
dx5ii peq create MyPreset --copy Dynamic         # duplicate (not activated by default)
dx5ii peq create MyPreset --from-apo delta.txt   # import one Device section of an APO config
dx5ii peq create MyPreset --copy SPEAKER --activate
dx5ii peq rename MyPreset Better                 # names are limited to 14 UTF-8 bytes
dx5ii peq delete MyPreset --switch Dynamic
dx5ii peq preview MyPreset                      # audition it without storing it
dx5ii peq preview --from-apo delta.txt           # audition a file, create nothing
dx5ii peq preview --stop                         # go back to the stored slot
```

`peq create` and `peq delete` only ever add or remove a preset. **There is no way to edit a band of
an existing preset**: re-uploading the block overwrites it, and that is deliberate.

`peq preview` plays a preset from the unit's scratch filter set without storing it;
`--from-apo` does the same for an Equalizer APO file, parsing it exactly like `peq create --from-apo`
but creating no preset. The bands go
through the per-band live-parameter commands (`0x9101`-`0x9B0A`), which the device only accepts
inside a preview; one of them sent on its own does nothing. The stored slot is untouched, and
`--stop` writes the preview meta back to inactive. A preset whose left channel is disabled (`peq list`
marks it `L disabled`) keeps that bit in the preview, so the audition sounds the way the preset does. If the process dies before `--stop` the unit
keeps playing the scratch set: `dx5ii --set-config <slot>` ends it, but the preview itself cannot
be reopened afterwards.

`--from-apo` reads the `Device:` section named after the connected unit (the name `dx5ii --devices`
prints); a file with no `Device:` line is taken as describing that one device. The device stores
PK, LP, HP, LS and HS bands; any other type (`BP`, `NE`, ...) stops the import with an error
instead of being dropped, and more than 11 filters in one section is an error too.
`HP` / `LP` lines carry no `Gain` in Equalizer APO and are imported at 0 dB. A `Preamp:` line
is written into the preset's preamp words (`-12` to `+12` dB). Nothing is imported silently.

Exit codes: `0` OK, `2` device/argument error, `3` write not observed on read-back.

## Library

```python
from dx5ii import Dx5ii, apply_changes

with Dx5ii() as dev:
    state = dev.snapshot()                 # device/settings/identity/sampling/peq/configs/pushed
    print(state["settings"]["volume_db"])

    dev.set_output("hp_balanced")          # returns the settings after the switch
    dev.set_config_index(2)                # 0x110E, returns the index read back

with Dx5ii() as dev:                       # switch both at once, keep a rollback point
    snap = dev.snapshot()
    report = apply_changes(dev, snap, want_output="hp_balanced",
                           want_config="IER-M7.Harman")
    print(report["before"]["stored_index"], report["after"]["stored_index"])
```

PEQ:

```python
from dx5ii import Dx5ii, read_presets, create_preset, parse_apo_filters

with Dx5ii() as dev:
    presets = read_presets(dev)
    print(presets["stored_index"], [p["name"] for p in presets["presets"]])

    left = parse_apo_filters(open("delta.txt").read())
    create_preset(dev, "Imported", left)       # right channel copies the left one
```

Field encoding works on its own:

```python
from dx5ii import encode_field

frames = encode_field("volume", -40.0, {"volume_step": "half_db"})
# [(28034, 80, 1, 1)]  -> cmd, data, total, cur
```

Every exported function and method carries type hints and ships `py.typed`.

## Protocol notes

- **Frame**: 16 bytes `22 33 <pt> <total> <cur> <cmdHi> <cmdLo> <data32 BE> <crcHi> <crcLo> 66 77 00`,
  prefixed with a `0x00` report id when written through hidapi. `pt` is `0x10` for reads and `0x20`
  for writes; replies always carry `0x20`.
- **Multi-frame**: `total` is the frame count and `cur` the index. `0x710C` (read settings) answers
  with 52 frames and `0x1120` (preset upload) with 393; reading a single frame looks like "the
  device is not responding".
- **PEQ block**: 78 uint32 words — `0..3` name (UTF-8, little-endian, NUL padded), `4` enabled flag,
  `5` and `7` hashes, `8..40` left channel 11 bands × 3 words, `41..73` right channel, `74..77`
  padding. Each band: `packed = enabled | type<<8 | gain_tenth_db<<16`, then `freq_hz`, then
  `q*10000`; types are `1=PK 4=LS 5=HS`.
- **Uploads must use 74 frames, not 78.** The device only consumes the first 74 words; sending 78 is
  silently ignored.

## Layout

| Module | Responsibility |
|---|---|
| `constants.py` | command ids, enum tables, settings word layout |
| `protocol.py` | frame codec, name decoding, volume conversion |
| `stream.py` | multi-frame collector: a reply is done at `curFrame == totalFrameLen - 1` |
| `device.py` | HID handle: open, send/receive, per-command reads, snapshot assembly |
| `interpreter.py` | frame sequences → state dict (pure, testable offline) |
| `fields.py` | field table, value encoding, read-back extraction |
| `bands.py` | PEQ band codec, APO text parser |
| `peq.py` | preset read/create/delete/rename |
| `actions.py` | high-level "switch port + switch preset" with rollback info |
| `formatting.py` | terminal output |
| `cli.py` | argparse subcommands and entry point |

Everything except `device.py` is hardware-free, so recorded frames are enough for unit tests.

## Known limitations

- **Device name is read-only.** `0x7127` has no receive handler in the firmware.
- **Switching UAC re-enumerates the device.** After `uac1` the USB descriptors are rebuilt and the
  next write returns `-1`; wait ~3 s and reopen.
- **Ten actions only work through `raw`** (`factory-reset`, `disable-controller`, `call-c1/c2`,
  `execute-remote-a/b`, `heartbeat`, `save-c1/c2`, `clear-pairing`) because they have no read-back.
- **Switching the output port also changes EQ and volume.** Both are remembered per port.
- **`0x1206` is read-only**; use `0x110E` to switch presets.
- **`raw` refuses two commands**: `0x710B` (factory reset) and `0x712E` (firmware update mode).
  The second one drops the device off USB and it does not come back until a firmware image is
  written. Pass `--force` only when that is what you want.
- **A cloud config's name and bands are not readable.** While Topping Home plays a cloud config the
  device reports only a `source` byte and two hashes through `0x7137`; `0x1206` keeps naming the
  stored local slot. `configs.playing` says what is audible (`local` / `cloud` / `audition` /
  `unknown`) and `configs.stored_index` / `stored_name` say what is stored, so the two cannot be
  confused. `playing.slot_index` is null unless the device plays a local slot.

## Tests

```bash
python -m unittest discover -s tests -t tests -v
```

158 cases, pure functions only (frame codec, field encoding, PEQ block codec, sequence
interpretation, the multi-frame collector and read-session state machines, the write
confirmation wait, the temporary preview handshake). No device needed.

## License

MIT — see [LICENSE](LICENSE).
