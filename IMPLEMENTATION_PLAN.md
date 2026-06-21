# MOON Neo 390 → Home Assistant — Implementation Plan

Goal: control the Simaudio MOON Neo 390 cleanly from Home Assistant as a single
`media_player` entity, driven by a persistent async TCP connection with real-time
push updates.

Decisions (locked):
- **Structure:** standalone async library (`moon390`) + thin HA integration (`custom_components/moon390`).
- **State model:** `local_push`, unsolicited feedback **ON**, `should_poll = False`.
- **Protocol source of truth:** `PROTOCOL_NOTES.md` (heed every `[INCONSISTENCY]`).

---

## Part A — `moon390` async client library

Pure-Python, no HA imports, asyncio-based. Publishable to PyPI later; for now lives in
its own folder and is referenced from the integration's `manifest.json` requirements.

### A1. Package layout
```
moon390/
  __init__.py          # exports Moon390, MoonState, enums, exceptions
  protocol.py          # frame codec + constants (codes, enums, maps)
  client.py            # Moon390: connection, reader task, command methods, events
  models.py            # MoonState dataclass, MediaInfo dataclass
  exceptions.py        # MoonError, MoonConnectionError, MoonCommandError(code)
tests/
  test_protocol.py     # codec + NN + inconsistency edge cases (no socket)
  test_client.py       # fake-stream driven parser/dispatch tests
```

### A2. `protocol.py` — framing + tables
- `build_frame(code: int, params: bytes = b"") -> bytes`
  - body = `f"{code:02X}".encode() + params`; `NN = len(body)`; emit `b"#" + f"{NN:02X}" + body + b"\r"`.
  - **NN is always computed**, never hardcoded.
- `iter_frames(buffer: bytearray) -> list[Frame]` — streaming splitter:
  - find `#` … `\r`, yield `(code:int, params:bytes)`, leave partial tail in buffer.
  - tolerate junk before `#`; tolerate back-to-back frames; never assume one frame per read.
- Hex helpers: `hexbyte(v)->b"XX"`, `unhex(b)->int`; decode accepts upper/lower case.
- Constants & enums:
  - `Cmd` (0x60 power, 0x64 volume, …), `Resp` (0xA0–0xFE).
  - `INPUTS_SCHEME_A` (id→name) — the **single** input map, used for display, decode, AND the
    `0x63` select command. (No "Scheme B"/swap — disproven on hardware 2026-06-21.)
    `select_input_id(name)` and `select_input_by_id(id)` use it directly.
  - `SAMPLE_RATES` (00–0x1C incl. DSD/MQA), `ERROR_CODES` (A1 param2).

### A3. `models.py`
```
@dataclass
class MediaInfo:
    title/artist/album/genre: str|None
    image_url: str|None
    duration_s: int|None          # parsed from "M:SS"
    position_s: int|None          # from B5
    source_tag: str|None          # 'M' (MiND) / 'B' (Bluetooth)

@dataclass
class MoonState:
    available: bool
    powered: bool                 # A3 byte#1 b0
    muted: bool                   # A3 byte#1 b1
    dac_locked: bool
    display_off: bool
    fault: bool                   # b4 PSU fault / b5 DC detected
    volume_raw: int               # 0..800
    balance_raw: int              # 0..200 (100=center)
    input_id: int                 # Scheme A
    sample_rate: str
    repeat: 'none|all|one'        # A3 byte#2 b0/b1
    shuffle: bool                 # A3 byte#2 b2
    media: MediaInfo
    serial: str|None              # from FE, for unique_id
    product_id/sw_rev/comm_rev    # from A4/FE
```
Helpers: `volume_db = volume_raw/10`, `volume_level = volume_raw/800`.

### A4. `client.py` — `Moon390`
- **Lifecycle:** `connect()`, `disconnect()`, `async with`.
  - `asyncio.open_connection(host, 50000)`.
  - On connect: register a `_reader_loop()` task; send `0x01` (Get UNIT status) and
    `0x02`/`0x1F` (product info → serial) to seed state; confirm feedback is ON via `0x20`.
- **Reader loop:** read into buffer → `iter_frames` → `_dispatch(code, params)`.
  - Dispatch table updates `MoonState` and fires `self._notify()` callback(s).
  - **`A3` parsed by declared NN, length-defensive** (doc says 08 *and* 10; read what's there,
    fill only the fields present).
  - `B5` (track time, ~1 Hz): update `position_s` + a monotonic timestamp; coalesce notifications.
  - `A1` (error): map code; if it corresponds to a pending command, raise `MoonCommandError`.
- **Command methods** (each builds a frame, awaits ack/specific reply or fire-and-forget):
  - `set_power(on)`, `set_mute(on)`, `set_display(on)`
  - `set_volume_level(0..1)` → raw 0–800 → `0x64` action 07 + MSB/LSB
  - `volume_up()/down()` → `0x64` action 04/01 (±0.5 dB); note <30 dB forced to 1 dB by unit
  - `set_balance`, `select_input(name)` / `select_input_by_id(id)` (`0x63`, single Scheme A)
  - `play/stop/pause/next/previous` (`0x67/68/69/6A/6B`)
  - `set_repeat(mode)` (`0x6C`), `set_shuffle(on)` (`0x6D`)
- **Reconnection:** exponential backoff (1→2→4…≤30 s); on drop set `available=False`, notify,
  keep retrying; on reconnect re-seed with `0x01`.
- **Concurrency:** single writer behind an `asyncio.Lock`; the device returns a response per
  command, but pushes interleave — never block the reader waiting on a writer.
- **Events:** `add_listener(callback)` / `remove_listener`; callback invoked on any state change.
  HA entity subscribes here.

### A5. Tests (no hardware)
- Codec round-trips incl. the doc's worked examples (`#021F`, `#041801`, `#064B1101`).
- NN computed correctly for ASCII-STR (label) frames.
- Parser: partial reads, concatenated frames, leading junk, real A7 burst bytes.
- Input-select uses single Scheme A (no swap); decode uses Scheme A.
- Volume 800↔`0320`, balance center `64`, sample-rate + error-code maps.

---

## Part B — `custom_components/moon390` integration

### B1. Files
```
custom_components/moon390/
  __init__.py          # setup/unload entry; create client, store in hass.data
  manifest.json        # domain, requirements=[moon390], iot_class=local_push, config_flow=true
  config_flow.py       # user step: host (IP); validate by connecting + reading FE serial
  const.py             # DOMAIN, defaults
  media_player.py      # Moon390MediaPlayer entity
  strings.json / translations/en.json
```

### B2. `__init__.py`
- `async_setup_entry`: instantiate `Moon390(host)`, `await connect()`, store on `entry.runtime_data`
  (or `hass.data[DOMAIN][entry_id]`), forward to `media_player` platform.
- `async_unload_entry`: `await client.disconnect()`, unload platform.
- Register update-listener so an options change can reconfigure.

### B3. `config_flow.py`
- Single `user` step: `vol.Schema({host: str})`.
- Validate: connect, read `0xFE` for serial → `await self.async_set_unique_id(serial)`;
  `_abort_if_unique_id_configured()`. Title = "MOON Neo 390".
- (Optional later) DHCP/zeroconf discovery; manual entry is enough for v1.

### B4. `media_player.py` — `Moon390MediaPlayer(MediaPlayerEntity)`
- `_attr_should_poll = False`; subscribe to client events in `async_added_to_hass`,
  unsubscribe on remove; each event → `self.async_write_ha_state()` (coalesced for B5).
- `_attr_unique_id` = serial; `_attr_device_info` from product info.
- **Property mapping:**
  - `state`: ON if powered else `STANDBY`; `unavailable` when client not connected.
  - `volume_level` = `raw/800`; `is_volume_muted`.
  - `source` / `source_list`: **expose all 14 canonical Scheme-A inputs** (decision
    2026-06-21). The device CANNOT report enabled state or custom labels without mutating
    itself: A7 carries only `id + literal label` (no enabled flag), and the only way to
    elicit A7 is `0x23`/`0x24`, both of which change the unit (HARDWARE FINDING, see
    PROTOCOL_NOTES). So we do **no A7 enumeration**. `source` reflects A3's selected input id.
    Selection out via `select_input_by_id(id)` → `0x63` (single Scheme A, no swap).
    *Future option:* a HA options flow could let the user hide unused inputs / set custom
    names locally, without ever touching the device. Not in v1.
  - `media_*` from `MediaInfo`; `media_position` + `media_position_updated_at` from B5 for
    smooth interpolation (no per-second state writes).
  - `repeat` (RepeatMode), `shuffle`.
- **`supported_features`** built dynamically:
  - always: power on/off, volume set/step/mute, select source.
  - add play/pause/stop/next/previous/shuffle/repeat **only when active source is MiND or Bluetooth**.
- **Service handlers** call the matching client method; rely on the resulting push to update state
  (optimistic write optional for snappier UI).

### B5. Quality / robustness
- Availability flips with socket state; entity shows `unavailable` cleanly on power-pull.
- Re-sync (`0x01`) on every reconnect so HA never shows stale state.
- Log (don't crash) on unknown response codes or malformed frames.
- Document the **SimLink conflict** warning in the integration README (don't drive over IP while
  SimLink is active).

---

## Part C — Build / verify order
1. [x] `protocol.py` + `test_protocol.py` (codec & maps green, offline).
2. [x] `models.py` + `client.py` reader/dispatch + `test_client.py` against a fake stream.
3. [x] Live smoke test against the real unit — DONE: connect, A3 status, mute, volume, input
   select all verified. Framing rewritten (delimiter-based) after A7 burst capture. `0x63`
   scheme confirmed (single Scheme A, no swap). **All protocol ambiguities resolved.**
4. [ ] HA integration files; load via HACS/custom_components; confirm entity, sources, transport,
   metadata, and front-panel-change reflection.
5. [ ] README + `strings.json`; optional discovery.

## Open items to confirm on hardware (from PROTOCOL_NOTES §10)
- [x] `A3` actual `NN` and field order → **NN=10, 7 fields, confirmed.**
- [x] `0x63` BALANCED/ANALOG ids → **confirmed single Scheme A, no swap** (probe `b`:
  0x0C→BALANCED, 0x0D→ANALOG).
- [x] Input label format → A7 *response* is **literal ASCII, var length, no NULL** (confirmed).
  `0x23` write-length untested/deferred (probe removed; HA doesn't write labels).
- [x] `A7` enabled reporting → **resolved: A7 carries no enabled flag.** Source list = all 14
  inputs (no enumeration). Closes this item.

## Hardware verification status (as of 2026-06-21)

What was actually exercised against a real MOON Neo 390 this session vs. implemented but
not yet observed on hardware. **Anything "unverified" is a candidate to test before/with the
HA integration** — especially media metadata and the serial parse.

**Verified on hardware ✅**
- TCP connect (port 50000) + reader loop + delimiter framing against a real A7 burst.
- `A3` status decode: volume, balance, selected input, sample-rate field, power/mute bits.
- Mute toggle (`0x65`).
- Volume set + nudge (`0x64` action 07 / 04 / 01); observed sane dB readback in `A3`.
- Input selection (`0x63`) incl. BALANCED/ANALOG; `A3` echoes the selected id.
- `A7` input-setup wire format (literal label, no enabled flag, bogus NN).

**Implemented but NOT yet verified on hardware ⏳ — test these next**
- Power on/off (`0x60`) — only the power *bit* in `A3` was observed, never toggled via IP.
- Display on/off + intensity (`0x61`/`0x62`).
- Balance *set* (`0x66`) — only decoded from `A3`, never written.
- Transport: play/stop/pause/next/previous (`0x67`–`0x6B`) — nothing was playing.
- Repeat/shuffle *set* (`0x6C`/`0x6D`).
- Media metadata pushes `AF`–`B5` (song/artist/album/art/duration/position) — **untested;
  nothing was streaming.** The ~1 Hz `B5` position push is likewise unobserved.
- Sample-rate values other than `00` (none) — nothing was playing, so the rate table
  (44.1k…DSD…MQA) is unconfirmed in practice.
- `A4` product-info parse (prodID/swRev/commRev).
- `A1` error handling — one `A1` was seen incidentally during the (now-removed) buggy
  enumeration; the normal error path is otherwise unexercised.
- Reconnection / exponential backoff on socket loss.

## Known gaps / tech debt to address before or during Part B
- **`_parse_serial` is a STUB.** It returns the whole `FE` payload decoded as ASCII, NOT the
  structured serial (`aaabbbbccccc`: date code / product no / serial — see PROTOCOL_NOTES
  §7 FE layout). This is the intended HA `unique_id` source, so it must be parsed and
  hardware-verified before the config flow relies on it. If `FE`/serial proves unreliable,
  fall back to `A4` product id + host, or the host alone.
- **Media metadata parsing is unverified** (see above) — the `M`/`B` prefix handling and the
  `B4`/`B5` `M:SS` time parse need a real streaming session to confirm.
- **`add_byte_listener`** on the client is now unused (it supported the removed raw-capture
  probe). Keep as a debug hook or drop — reviewer's call.
- Library is **not yet packaged** (no `pyproject.toml`); it's imported by path. Packaging is
  needed before the HA `manifest.json` can list it as a requirement.
- No `ruff`/type-checker config committed yet; code is written to be clean but unlinted here.

## Minimum HA / Python targets
- Target current HA (Python 3.12+), asyncio throughout, type hints, `ruff`-clean.
- No blocking I/O on the event loop; all socket work in the library's async methods.
