# Fields

`dx5ii get` and `dx5ii set` address settings by name. `dx5ii manual fields`
prints the complete list, generated from the same table the encoder uses, so it
always matches what the program accepts.

## How values are written

| kind | what to pass | example |
|---|---|---|
| enum | the name, or its device index | `output hp_balanced` |
| bool | `on` / `off`, `true` / `false`, `1` / `0` | `mute on` |
| int | a number | `balance -3` |
| int32 | a number, stored as a signed 32-bit word | `volume -40.0` |
| mask | comma-separated names, or a raw integer | `input-option usb,bt` |
| mask_index | one name; the device gets its index, not its bit | `crossfeed-conv c1` |
| timeout | `off`, `10s`, `30s`, `60s` | `screen-timeout 30s` |
| volume | dB, rounded to the device's step | `volume -40.0` |
| name | 1-8 characters, UTF-8 | `device-name Desk` |

`get` prints the name for enums and lists, and the number under a `_raw` key
when a script needs the device index: `{"output": "hp_balanced", "output_raw": 4}`.

## Value tables

Output ports (`output`, `output-option`):

| index | name | meaning |
|---|---|---|
| 0 | `all` | every output |
| 1 | `hp_all` | both headphone outputs |
| 2 | `line_all` | both line outputs |
| 3 | `hp_single` | 6.35 mm single-ended headphone |
| 4 | `hp_balanced` | 4.4 mm / XLR-4 balanced headphone |
| 5 | `line_single` | RCA single-ended line |
| 6 | `line_balanced` | XLR balanced line |

Inputs (`input`, `input-option`): index 0 `usb`, 1 `fiber`, 2 `coax`, 3 `bt`.

Other tables:

| field | values |
|---|---|
| `pcm-filter` | `f1` ... `f8` |
| `line-mode` | `preamp`, `dac` |
| `uac` | `uac1`, `uac2` |
| `brightness` | `low`, `medium`, `high` (the automatic step is not reachable) |
| `language` | `en`, `zh`, `ja`, `zh_tw` |
| `input-mode` | `auto`, `manual` |
| `volume-step` | `half_db`, `one_db` |
| `polarity` | `normal`, `reverse` |
| `volume-memory`, `peq-memory`, `crossfeed-memory` | `input`, `output`, `disabled` |
| `theme` | `aurora`, `orange`, `peru`, `pea_green`, `dark_khaki`, `rosy_brown`, `blue`, `purple_magic`, `white` |
| `home-page` | `normal`, `vu`, `fft` |
| `crossfeed-type` | `convolution`, `simple`, `off` |
| `crossfeed-conv` | `c1`, `c2` |
| `crossfeed-simple` | `s1` ... `s4` |
| `screen-timeout` | `off`, `10s`, `30s`, `60s` |

## Two masks that are easy to confuse

`input-option` and `output-option` are the **set of ports the front-panel button
cycles through**. They are not the current port; that is `output`.

```
dx5ii set output hp_balanced                       # switch now
dx5ii set output-option hp_single,hp_balanced      # what the button cycles
```

Writing `output-option` makes the device re-choose the current output port. If
you change the mask and expect to stay on the same port, read `output` back and
switch again if needed.

## Volume

`volume` is in dB and the device stores it in 0.5 dB steps (or 1 dB steps when
`volume-step` is `one_db`). A value between two steps is rounded. The JSON
report shows `confirmed: false` when the stored value differs from what was
sent, which is the normal outcome for such a value.

Every output port remembers its own volume and preset, so switching `output`
also changes the volume you read back.

## Fields that cannot be written

- `device-name` is read-only (the device ignores the write).
- `stored_index` is read-only; switch presets with `--set-config` or
  `dx5ii peq` commands instead.
