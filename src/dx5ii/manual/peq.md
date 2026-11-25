# PEQ presets

The device holds a small set of EQ presets. Each one is a name, a channel
preamp, and up to 11 bands per channel. A band is enabled or disabled, has a
type (PK, LP, HP, LS, HS), a frequency, a gain and a Q.

```
dx5ii peq list
dx5ii peq show SPEAKER
```

`peq show` prints one `Filter N:` line per band, in the same shape Equalizer APO
uses. When the two channels differ, the line appears twice with an `L` or `R`
prefix; when they are equal, one line covers both.

## Preamp

Each channel has its own preamp, from −12 dB to +12 dB. `peq list` prints
`preamp L+R 0.0 dB` when both channels match, and `L … R …` when they do not. A
preset whose left channel is disabled is flagged as `L disabled`.

## Band types and what the device stores

| type | code | gain | Q |
|---|---|---|---|
| `PK` | 1 | used | used |
| `LP` | 2 | fixed 0 dB | used |
| `HP` | 3 | fixed 0 dB | used |
| `LS` | 4 | used | used as shelf slope |
| `HS` | 5 | used | used as shelf slope |

The gain is stored in 0.1 dB steps, the frequency as a whole number of Hz, and
the Q as `round(Q × 10000)`. Values you send are rounded the same way, so an
exact read-back of a fractional value is not expected.

## Creating presets

```
dx5ii peq create "My EQ"
dx5ii peq create "My EQ" --copy SPEAKER
dx5ii peq create "Desk" --from-apo eq.txt
```

`--copy` starts from an existing preset, which keeps its preamp and enables.
`--from-apo` reads an Equalizer APO text file; see below.

## Importing an Equalizer APO file

```
dx5ii peq create "Desk" --from-apo "C:\Program Files\EqualizerAPO\config\config.txt"
dx5ii peq preview --from-apo eq.txt          # listen without creating anything
dx5ii peq overwrite SPEAKER --from-apo eq.txt # replace an existing preset
```

Rules for `--from-apo`:

- Only the `Filter:` lines belonging to the section named after this device
  (`Device: DX5 II`) are read. Other sections in the same file are ignored.
- If the file has no `Device:` line at all, the whole file is read.
- `Preamp: -6.0 dB` becomes the channel preamp. Without it the preamp is 0 dB.
- Numbered (`Filter 3: ON PK …`) and unnumbered (`Filter: ON PK …`) lines are
  both accepted.
- `HP` and `LP` need no `Gain`; Equalizer APO does not write one for them.
- The whole file must map onto the device. An unknown type, a missing gain on a
  type that needs one, or more than 11 bands is an error — nothing is written.

## Renaming

```
dx5ii peq rename SPEAKER "Desk"
```

Names are limited to 14 UTF-8 bytes (the device stores 16 with a terminator).

## Deleting

```
dx5ii peq delete "Old EQ"
dx5ii peq delete SPEAKER --switch "Desk"
```

Deleting the preset that is currently playing leaves the device on index 0.
`--switch` moves to another preset first, so the sound continues.

## Audition (preview)

```
dx5ii peq preview SPEAKER          # play an existing preset from a scratch buffer
dx5ii peq preview --from-apo eq.txt
dx5ii peq preview --stop           # go back to the stored preset
```

The device has a scratch filter set next to the stored presets. A preview loads
that scratch set and leaves every stored preset untouched; `--stop` clears it.

Two things to know:

- **A preview does not end by itself.** The device has no timeout; it stays in
  the scratch state until something clears it. If you close the terminal, or
  the process is killed, the device keeps playing the scratch set. Run
  `dx5ii peq preview --stop` (or switch presets) to leave it.
- **While a preview is active, every band write goes into the scratch set.** A
  `peq overwrite` issued during a preview would silently change the scratch copy
  and leave the stored preset unchanged. The commands that write presets clear
  the preview first for this reason.

`--from-apo` with `preview` creates no preset and writes nothing to the device's
flash, so it is the way to try an EQ curve without using a slot.

## Overwrite

```
dx5ii peq overwrite SPEAKER --from-apo eq.txt
dx5ii peq overwrite "Desk" --copy "SPEAKER"
dx5ii peq overwrite "Desk" --last
dx5ii peq overwrite SPEAKER --copy SPEAKER --dry-run
```

`overwrite` replaces the **contents** of an existing preset and keeps its slot
index and name. The source is an APO file, another preset, or the last thing
auditioned (`--last`).

Behaviour:

- It writes with the same command sequence the web page uses: four channel
  preamp/enable words followed by up to 110 per-band words, then it reads the
  preset back and compares.
- If the stored contents already match the source, nothing is written and it
  reports `already matches`.
- To write a preset that is not the current one, it switches to it first and
  switches back afterwards; the sound lands on the target slot for about half a
  second.
- `--backup PATH` writes the previous contents as JSON before touching anything.
- `--dry-run` prints what would be sent and exits.
- Exit code 3 means the read-back did not match what was sent.

## Where presets live

Presets are stored on the device; nothing is cached on the PC. The only state
this tool keeps is the `--last` record of the most recent preview, in
`~/.dx5ii/last-preview.json` (falling back to `%LOCALAPPDATA%\dx5ii` and then
the temporary directory when that location is not writable). `pip uninstall`
does not remove it.
