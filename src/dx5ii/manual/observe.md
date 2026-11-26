# Frames, watch and trace

Most of the time you should use `get`, `set` and `peq`. This section is for when
you need to see the bytes.

## Frame layout

Every frame is 17 bytes on the wire, because the HID report has no report ID:

```
00 22 33 <protocol> <total> <cur> <cmdHi> <cmdLo> <data:4 bytes, big-endian> <crcHi> <crcLo> 66 77
```

| offset | meaning |
|---|---|
| 0 | HID report ID, always `00` |
| 1-2 | `22 33`, fixed |
| 3 | protocol type |
| 4 | total frames in this reply |
| 5 | index of this frame, 0-based |
| 6-7 | command, big-endian |
| 8-11 | data, big-endian 32-bit |
| 12-13 | CRC-16, polynomial `0xA001`, initial value `0xFFFF`, over bytes 3-11 |
| 14-15 | `66 77`, fixed |

Protocol types: `0x10` readNack, `0x11` readAck, `0x20` writeNack, `0x21`
writeAck. **The device answers every frame with `0x20`**, whichever type was
sent, so do not use the protocol byte to tell a reply from a push.

A multi-frame reply is complete when `cur == total - 1`. Frames must arrive in
order; the collector drops a partial run when the index jumps.

## raw

```
dx5ii raw 0x710C --wait 400
dx5ii raw 0x1101 1 --json
```

`raw` sends a single frame and prints every reply for `--wait` milliseconds.
Destructive commands (`0x712E` firmware update, `0x710B` factory reset) are
refused unless `--force` is passed; both are irreversible and `0x712E` makes the
device disappear from USB.

Two commands behave differently from what their name suggests: `0x110A`
(delete preset) and `0x110B` (create preset) execute even when sent with the
read protocol type, and `0x7142` executes the multifunction key. There is no
way to probe them without changing the device.

## Frames behind a read

```
dx5ii status --frames
dx5ii status --frames --json
```

`--frames` keeps every report the read consumed and prints it under `[frames]`,
with milliseconds since the device was opened. In JSON the same list is
`frames[]` with `t_ms` and `hex`; the older key `raw_frames` still carries the
same data and goes away in 0.1.2.

This is the view for "what did the device actually send while I read the state":
requested replies and pushed frames both land here, so the timestamps show what
arrived on its own and what came back because of a request.

## watch

```
dx5ii watch 5                # count frames for 5 seconds
dx5ii watch -f               # stream them until Ctrl-C
dx5ii watch -f --cmd 0x7137  # only one command
dx5ii watch -f --all --timestamps --json
```

`watch` behaves differently depending on whether the device is pushing.

**Request-reply commands** (`0x710C` settings, `0x7137` preview state, the long
`0x1120` preset upload) only answer when asked.

**Telemetry** (`0x7130` VU, `0x7131` FFT) is pushed continuously, about 490
frames per second, but **only while a push session is open**. A session opens
with `0x1101 data=2` and closes on its own after roughly 20 seconds without a
request from the host; send `0x7134 data=1` every few seconds to keep it open
(the web page uses 5 seconds). `watch` opens the session and keeps it alive
itself, which is why it produces output even with the TOPPING page closed.

`--all` stops hiding the two telemetry commands. Without `-f`, the summary lists
how many frames of each command arrived.

## trace

```
dx5ii get volume --trace run.jsonl
dx5ii --trace run.jsonl --json
```

`--trace` appends one JSON object per line:

```json
{"t": 12.345, "dir": "tx", "hex": "002233200101710c0000000000..."}
{"t": 12.401, "dir": "rx", "hex": "00223320013f710c..."}
```

`tx` is what the program sent (17 bytes, report ID included), `rx` is the raw
64-byte buffer read back. The file is flushed after every line, so it is usable
up to the moment the process is killed.

`--trace` is accepted at the top level and after every subcommand.

## Numbers you may see

| command | meaning |
|---|---|
| `0x1101` | connect state; `data=1` requests a full report, `data=2` opens the push session |
| `0x1106` | preset upload frame; `total=2` carries the header, `total=78` the preset words |
| `0x1120` | agreement config; the preset upload must be requested after `0x710C` |
| `0x1204` | MCU EQ enable state |
| `0x1206` | current preset index, read-only |
| `0x7132` | PEQ on/off |
| `0x7137` | preview state: three frames, `active` / `source` / slot index, then two FNV-1a hashes |
| `0x713F` `0x7140` `0x7141` | PEQ / PCM / crossfeed option masks |
