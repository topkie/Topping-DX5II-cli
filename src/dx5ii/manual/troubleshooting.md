# Troubleshooting

## The device is not found

`dx5ii --devices` lists every matching interface. The control protocol is on
**interface 2** (`MI_02`, usage page 1). Interface 3 is a consumer-control
interface: it appears in the enumeration but does not answer protocol frames.

If `--devices` finds nothing, the DAC is not connected, or another program holds
the interface. Close the TOPPING web page and any other tool using it, then
retry; a closed page is not required, but it removes one suspect.

## "Failed to open" or a write has no effect

The interface can only be opened by one process at a time on some systems. If
another program holds it, opening fails or reads return nothing. Check with
`--devices` and close the other program.

## Output changed after I wrote `output-option`

That command sets which ports the front-panel button cycles through. The device
re-chooses its current port when the mask is written, and it does not change
back when you restore the mask. Read `output` afterwards and switch it back:

```
dx5ii set output-option hp_single,hp_balanced
dx5ii get output
dx5ii set output hp_balanced
```

## Volume or preset changed after I switched output

Each output port remembers its own volume and EQ preset. Switching the port
loads the values stored for the new port; this is how the device works, not a
side effect of the tool.

## A write reports `echo: true` but `confirmed: false`

The device acknowledged the frame but stored a different value. The usual cause
is rounding: volume has a 0.5 dB (or 1 dB) step, filter gain has a 0.1 dB step,
and frequency is stored as a whole number of Hz. The `after` values in the JSON
show what the device actually holds.

A different cause is a value the device clamps, such as a Q above the maximum.

## The sound is still the cloud configuration

The device can play an EQ configuration that lives on the TOPPING account
instead of one of its stored presets. In that state the stored preset is not
audible, and this tool cannot bring the cloud configuration back: the content is
not on the device. When the device leaves that state — for example because you
ran `dx5ii --set-config` — you have to select the cloud configuration again in
TOPPING Home.

## The sound is not the stored preset and `peq preview --stop` fixes it

An audition (preview) loads a scratch filter set that the device keeps until
something clears it. It has no timer. If a preview was left running, clear it:

```
dx5ii peq preview --stop
```

`dx5ii configs` shows `playing: cloud` or `playing: audition` when the device is
not playing a stored preset.

## I deleted a preset by accident

There is no undo. The device can restore its factory state with a factory reset,
which erases every preset and setting; this tool refuses that command unless you
send it explicitly with `raw 0x710B --force`. If you saved a backup with
`peq overwrite --backup`, you can create a new preset from it with `--from-apo`
after converting the JSON, or write it back with the same command sequence.

## `set uac` makes the device disappear

Changing the USB audio class re-enumerates the device. The command succeeds, but
the HID interface is gone for a moment. Wait three seconds before the next
command.

## The text output looks garbled on Windows

The console defaults to a legacy code page. Set UTF-8 for the session:

```
chcp 65001
set PYTHONUTF8=1
```

The JSON output is unaffected; only the box drawing and accented characters in
the text report depend on the code page.

## A full read takes 1.5 seconds

Most of that is the preset list: the device sends five presets of 78 words each
at roughly 2 ms per frame, about 1.1 s. There is no command to fetch a single
preset. `--fast` skips the list entirely, and `get <field>` skips everything the
field does not need.
