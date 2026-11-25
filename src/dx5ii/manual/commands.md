# Commands

Every command accepts `--path PATH` (the HID interface) and `--trace FILE`
(write a JSONL log of every byte sent and received). Passing `--json` prints a
single JSON document instead of text.

## status

```
dx5ii status [--fast] [--frames] [--json]
```

Reads the whole state and prints it in groups. `--fast` skips the preset upload
(about 1.1 s shorter). `--frames` adds the log of every report the read
consumed, with milliseconds since the device was opened.

## devices

```
dx5ii devices [--json]
```

Lists the HID interfaces this unit exposes and marks the one the protocol uses.
This is the connectivity check: it sends no protocol frames.

## output

```
dx5ii output <port> [--json]
```

Switches the output port and reports the state before and after. Ports:
`all`, `line_single`, `line_balanced`, `line_all`, `hp_single`, `hp_balanced`,
`hp_all`. Both channels' EQ and volume are remembered per port.

## scene

```
dx5ii scene recall {c1,c2} [--json]
dx5ii scene save {c1,c2} [--json]
```

The unit stores two scenes, `C1` and `C2`. Each is a snapshot of every
setting it keeps -- volume, ports, screen, EQ selection, crossfeed and the
rest -- taken on the device itself. `recall` restores one, `save` overwrites
it with the current state.

`recall` reports the settings it changed, because the device cannot say what
a scene holds. The unit pushes the whole settings block after a recall, so the
new state arrives without a second request:

```
$ dx5ii scene recall c1
recalled C1: 1 setting changed
  volume_db  -30.0 -> -40.0
```

When nothing had to change it prints `recalled C1: no settings changed`. If the
device pushed nothing, the command says so and reads the state explicitly.

`save` is confirmed only through that same push. There is no undo: the device
cannot read a scene back, so an overwritten scene is gone.

## dx5ii (no subcommand)

```
dx5ii [--set-output PORT] [--set-config PRESET] [--fast] [--raw] [--json]
```

Reads the whole state. `--fast` skips the preset list (about 1.1 s shorter);
`--raw` keeps every frame that was received. `--set-output` and `--set-config`
apply one change first and report it as `before` / `actions` / `after`.

```
dx5ii --set-output hp_balanced
dx5ii --set-config "IER-M7.Harman" --json
```

## get

```
dx5ii get <field> [<field>...] [--json]
```

Reads only what is needed for the named fields. With one field this costs about
0.4 s; `get all` costs the same as a full read.

```
dx5ii get output volume stored_index --json
```

## set

```
dx5ii set <field> <value> [<field> <value> ...] [--json]
```

Writes the values, then reads the state back. All values are encoded before any
frame is sent, so an invalid argument leaves the device untouched. Each field
then waits for its own echo frame instead of sleeping a fixed time.

Values are written the way `get` prints them: names (`hp_balanced`), lists
(`usb,bt`), numbers (`-40.0`), booleans (`on` / `off` / `true` / `false`).

```
dx5ii set volume -40.0 input usb output-option hp_single,hp_balanced
```

Fields with `mask` take a comma-separated list of names or a raw integer.
Fields with `mask_index` take one name and send its index, not its bit.

The JSON report has one entry per field with `echo` and `confirmed`, plus a
top-level `ok` and `changed`. Setting a field to the value it already has is not
an error: it reports `changed: false`.

## configs

```
dx5ii configs [--json]
```

Prints the presets stored on the device: name, channel preamp, band count, and
which one is currently stored as active. With PEQ switched off the list says so
(`enabled no`), the selected slot is marked `selected` instead of `playing`, and
a note says it is stored but not applied. This is the same information as
`configs` inside the full read.

## peq

```
dx5ii peq on
dx5ii peq off
dx5ii peq list
dx5ii peq show <preset>
dx5ii peq import <file> [--name <name>]
dx5ii peq copy <preset> <name>
dx5ii peq export <preset> <file>
dx5ii peq create <name> [--copy <preset>] [--from-apo FILE]
dx5ii peq delete <preset> [--switch <preset>]
dx5ii peq rename <preset> <new-name>
dx5ii peq preview [<preset>] [--from-apo FILE] [--stop]
dx5ii peq overwrite <preset> (--from-apo FILE | --copy <preset> | --last)
dx5ii peq switch <preset>
```

See `dx5ii manual peq` for the details and the limits of each one.

## raw

```
dx5ii raw <cmd> [data] [--wait MS] [--json] [--force]
```

Sends one frame with protocol type `writeNack` and prints what comes back. `cmd`
accepts hex (`0x710C`) or decimal. `--wait` sets how long to keep reading
(default 800 ms). Destructive commands are refused unless `--force` is given.

```
dx5ii raw 0x710C --wait 400
```

## watch

```
dx5ii watch [seconds] [--json]
dx5ii watch -f [--cmd CMD] [--all] [--timestamps] [--json]
```

Without `-f` it counts frames for the given time and prints a per-command
summary. With `-f` it streams them until interrupted. Telemetry frames
(`0x7130` VU, `0x7131` FFT, about 490 per second) are hidden unless `--all` is
given; `--cmd` keeps one command.

`watch` starts the device's push session itself, so it works even when the
TOPPING web page is closed.

## manual

```
dx5ii manual [section] [--list]
dx5ii manual fields
```

Prints a section of this manual. `fields` is generated from the code that
encodes the fields, so it cannot drift from the actual accepted values.

## --devices

```
dx5ii --devices
```

Lists every HID interface that matches the VID/PID, with its interface number.
The control protocol is on interface 2; interface 3 is a consumer-control
interface and does not answer the protocol frames.
