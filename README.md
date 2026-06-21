# moon390 — Simaudio MOON Neo 390 control library (+ planned Home Assistant integration)

Specs: https://simaudio.com/wp-content/uploads/2019/11/MOON_390_IP_IR-Codes_rev1.pdf

Async Python library for controlling a **Simaudio MOON Neo 390** preamp/DAC/streamer
over its IP control protocol (ASCII-framed, UTF-8 text fields, over TCP port 50000). The end goal is a clean
Home Assistant `media_player` integration; this repo currently contains the standalone
protocol/transport library it will be built on.

> **Status (2026-06-21):** Part A (the `moon390` library) is **built and hardware-verified**
> against a real unit for the core paths. The Home Assistant integration (Part B) is **not
> started yet**. See `IMPLEMENTATION_PLAN.md` for the full design and build order.

## Repository layout

```
moon390/                 # the standalone async library (no Home Assistant imports)
  protocol.py            # frame codec, command/response codes, input maps, hex helpers
  models.py              # MoonState / MediaInfo / InputSetup + A3/A7/media decoders
  client.py              # Moon390: async TCP, reader loop, frame dispatch, commands
  exceptions.py
tests/
  test_protocol.py       # offline: codec, framing, maps, decoders (no socket)
  test_client.py         # offline: frame dispatch -> state, media pushes
manual_test.py           # interactive hardware harness (see below)
PROTOCOL_NOTES.md        # the protocol, reverse-engineered + every hardware finding
IMPLEMENTATION_PLAN.md   # architecture, HA integration design, build order, status
```

## Requirements

- Python 3.11+ (developed on 3.13). Standard library only — no third-party deps for the
  library or tests.

## Running the tests

Offline, no hardware needed:

```sh
python tests/test_protocol.py
python tests/test_client.py
```

Both are plain scripts (also pytest-collectable). 34 tests total, all passing.

## The manual hardware harness

`manual_test.py` is an interactive tool for exercising a real unit. It supports both
directions of testing (the harness acts and you confirm; or you act on the unit and the
harness reports what it saw) plus targeted protocol probes.

```sh
python manual_test.py <device-ip>      # e.g. python manual_test.py 192.168.2.19
```

> ⚠️ **On Windows, use `python` (or `py`), NOT `py.test`** — the latter runs pytest and
> treats the IP as a path.

Menu: `1` listen, `2` status, `3` mute, `4` volume, `5` select input, `6` raw send,
`a` A3-length probe, `b` BALANCED/ANALOG scheme probe. The harness is non-mutating except
for the obvious direct commands (mute/volume/input/raw). Input-enumeration and
label-write probes were **removed** because the only way to read input setup (`0x24`) also
*changes* the unit's enabled inputs — see "Gotchas" below.

## Key protocol findings (full detail in `PROTOCOL_NOTES.md`)

The vendor PDF has several self-contradictions; these were settled on hardware:

- **Framing is delimiter-based (`#`/CR), NOT length-based.** The `NN` byte-count field is
  unreliable — A7 input-setup frames stream back-to-back with no CR and a fixed bogus
  `NN=0E`. `iter_frames` splits on `#`/CR (neither byte can appear in a hex payload).
- **A3 status** = `NN=10`, 7 one-byte fields; parsed length-defensively.
- **A7 input setup** = `id + literal label (UTF-8)`, nothing else — **no enabled flag**, no
  NUL, no trailer. The device therefore cannot report which inputs are enabled.
- **Input selection (`0x63`) uses a single scheme (Scheme A).** The PDF's "Scheme B"
  BALANCED/ANALOG swap does not exist; `0x0C`→BALANCED, `0x0D`→ANALOG, same as everywhere.
- **Volume**: integer 0–800 = 0.0–80.0 dB; absolute set via `0x64` action 07 + MSB/LSB.

## Gotchas

- **No read-only "get input setup".** A7 only comes back as a response to `0x23` (set label)
  or `0x24` (enable/disable) — both mutate the unit. There is no way to enumerate inputs or
  read their labels without changing enabled state. (An early enumeration accidentally
  re-enabled inputs the owner had disabled.) Consequence: the HA source list will be the
  static 14 canonical inputs, not a device-derived list.
- **SimLink conflict**: the vendor warns against driving the unit over IP while SimLink is
  also active.
- **~1 Hz B5 traffic** when a track is playing (track-time push every second).

## What's verified on hardware vs. built-but-untested

See `IMPLEMENTATION_PLAN.md` → "Hardware verification status". Short version: status read,
mute, volume, and input selection are verified; transport/media-metadata/power/balance
commands and the product-info/serial parsing are implemented but **not yet exercised on a
real unit** (nothing was playing during testing, and the FE serial parse is a stub).

## License / status

Personal project, work in progress. Not affiliated with Simaudio.
