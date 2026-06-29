# MOON Neo 390 — IP Control Protocol Notes

Source: `MOON_390_IP_IR-Codes_rev1.pdf` (Protocol rev 1, Doc rev 1, May 2019).
Device: Simaudio MOON Neo 390 Digital Preamplifier / DAC / streamer ("MiND").
These are working notes for building control software. The IR (RC5) section is summarized
briefly at the end but is out of scope for now.

> ⚠️ The PDF has several internal contradictions (number-of-bytes fields, input-ID
> ordering, value ranges). Each is flagged inline below with **[INCONSISTENCY]**. Where the
> doc disagrees with itself, the recommendation is noted. **Verify against a real unit
> before trusting any flagged value.**

---

## 1. Transport

- **TCP/IP**, device's IP address, **port 50000**.
- Host and device must be on the same network segment.
- No terminal emulation. Raw ASCII byte stream.
- "Baud rate" commands exist (legacy serial heritage) but are mostly irrelevant over TCP;
  default is 9600. We can leave baud alone.
- **Every command returns a response** (command-specific, or a generic Ack/Error).
- The connection is **bidirectional and asynchronous**: if "unsolicited feedback" is enabled
  (default ON), the unit pushes status/media updates whenever its state changes (front panel,
  remote, streaming progress). So the reader must handle responses arriving at any time, not
  just request/reply. See §7.

---

## 2. Packet format

All packets, both directions:

```
#  <NN>  <CC>  [params...]  <CR>
```

| Part            | Bytes | Meaning                                                        |
|-----------------|-------|----------------------------------------------------------------|
| `#` (0x23)      | 1     | Header delimiter                                               |
| `NN`            | 2     | Number-of-bytes field, as 2 ASCII hex chars                    |
| `CC`            | 2     | Command / response code, as 2 ASCII hex chars                  |
| params          | var   | Command-dependent (HEX / BOOLEAN / ASCII / ASCII STR)          |
| `<CR>` (0x0d)   | 1     | End-of-packet delimiter                                        |

**Number-of-bytes (`NN`)** = count of all ASCII chars in the packet **excluding** the `#`
header, **excluding** the trailing `<CR>`, and **excluding** the `NN` field itself.
In practice: `NN = 2 (code) + len(params)`.

### Data types
- **HEX**: one logical byte = **two** ASCII hex chars. e.g. value `0x1F` → `"1F"`.
  Upper- and lower-case both accepted (`0x30-0x39`, `0x41-0x46`, `0x61-0x66`).
- **BOOLEAN**: a single ASCII char `'0'` or `'1'`.
- **ASCII**: single printable char.
- **ASCII STR**: a printable string. Despite the spec's name, the wire encoding is **UTF-8**
  in practice (confirmed for AF–B5 media text; assume the same for A7 input labels). NULL
  termination is spec'd but NOT emitted by this firmware for A7 (see the A7 finding above).

### Worked examples (from doc)
- `#021F<CR>` → command `0x1F`, no params. `NN="02"` = len("1F")=2. ✓
- `#041801<CR>` → command `0x18`, param `0x01`. `NN="04"` = len("1801")=4. ✓
- `#064B1101<CR>` → command `0x4B`, four BOOLEAN params `1,1,0,1`. `NN="06"`. ✓
- `#06200311<CR>` → Set comm params: 9600 baud, feedback ON, display-feedback ON.

### Code ranges (categories)
| Category          | Range       |
|-------------------|-------------|
| Status commands   | 0x01 – 0x1F |
| Setup commands    | 0x20 – 0x5F |
| User commands     | 0x60 – 0x9F |
| UNIT responses    | 0xA0 – 0xFE |

---

## 3. Input ID maps — **one scheme only (the doc's "trap" is a doc bug)**

> **[RESOLVED 2026-06-21 on hardware] There is ONE input scheme (Scheme A). No swap.**
> Probe `b` sent `0x63` id `0x0C` → unit selected **BALANCED**; id `0x0D` → **ANALOG**, and
> the A3 status echoed those same ids (`input=0C`/`0D`). So `0x63` uses the *same* ids as
> A3/A7. The §6.4 "Scheme B" body table (0C/0D swapped) is simply WRONG; Appendix A was right.
> The code has no swap: `select_input_id` and `select_input_by_id` use Scheme A directly.

### Scheme A (the only scheme) — used by ALL input commands/responses:
### Set input label (0x23), Enable/disable (0x24), Set input selection (0x63),
### UNIT status response (0xA3), Input setup response (0xA7):

| ID  | Input      |
|-----|------------|
| 00  | Invalid    |
| 01  | AES-EBU    |
| 02  | OPTICAL    |
| 03  | SPDIF      |
| 04  | USB        |
| 05  | MiND (NET) |
| 06  | BLUETOOTH  |
| 07  | HDMI 1     |
| 08  | HDMI 2     |
| 09  | HDMI 3     |
| 0A  | HDMI 4     |
| 0B  | HDMI ARC   |
| 0C  | **BALANCED** |
| 0D  | **ANALOG**   |
| 0E  | PHONO      |

### ~~Scheme B~~ — DOES NOT EXIST. The §6.4 table claiming `0C=ANALOG, 0D=BALANCED` for
### command 0x63 is a documentation error, disproven on hardware (see resolved note above).

Note the **status response §7.4 enum text** even mislabels itself: header says "00 to 08"
but lists 01–0E. The valid example `#08A3...` is also wrong (see §7.4 note).

---

## 4. Setup commands (0x20–0x5F)

| Code | Name                  | NN   | Params                                                                 |
|------|-----------------------|------|-----------------------------------------------------------------------|
| 0x20 | Set comm parameters   | 06   | baud(1B:01-06) + fb(BOOL) + dispFb(BOOL). Resp: A2. Baud changes after reply. |
| 0x21 | Reset UNIT            | 02   | none. Soft reset (like mains toggle). Resp: A3 + AA. Resets baud→9600, fb→ON. |
| 0x22 | Set factory defaults  | 02   | none. Resp: A9. Does NOT change comm params.                          |
| 0x23 | Set input label       | see  | inputID(1B) + label(ASCII STR). Resp: A7. **[INCONSISTENCY: see below]** |
| 0x24 | Enable/disable input  | 05   | inputID(1B) + enable(BOOL). Resp: A7. NOTE: MiND (05) cannot be disabled. |
| 0x2C | Set CD input          | 04   | inputID(1B). Valid: 01 AES, 04 SPDIF(default), 0C BAL, 0D ANALOG. Resp: AD. |

Baud values (param1 of 0x20 / reported by A2): 01=38400, 02=19200, **03=9600 (default)**,
04=4800, 05=2400, 06=1200.

**[INCONSISTENCY] 0x23 Set input label length & label size:**
- §4.4 body: `NN=0F`, label = "12 characters + NULL" (13 bytes).
- §4.4 example `#0F2302ANDRMEDA<NULL>`: "ANDRMEDA" is 8 chars → code(2)+id(2)+8+null(1)=13=`0x0D`, not 0F, and not 12+null.
- Appendix A: `NN=0D`, label = "8 characters + NULL".
- Input setup *response* (A7): see the RESOLVED note below — the response label is **literal
  ASCII, variable length, no NULL, no trailer** (the doc's "8 chars + NULL" is wrong).
- **`0x23` SET label (UNTESTED, deferred):** the max length you can *write* was never verified
  on hardware — the label-write probe was removed (it mutated the unit, and the HA integration
  does not write labels). If we ever need it: treat the writable label as **max 8 chars** and
  compute `NN` from actual bytes sent rather than hardcoding.

> **[RESOLVED 2026-06-21 via raw hex] A7 labels are LITERAL ASCII, not hex pairs.**
> A live 390 returned A7 params like `01AES-EBU`, `02OPTICAL`, `0DANALOG` — the label bytes
> are the literal characters, NOT the 2-hex-chars-per-byte encoding used everywhere else.
> See the full A7 layout finding below (no NUL, no trailer). `parse_input_setup` reads the
> label as literal ASCII.
>
> **[RESOLVED 2026-06-21 via raw hex] Framing is DELIMITER-driven (`#`/CR), NOT NN-driven.**
> Raw capture: `23 30 45 41 37 30 44 41 4e 41 4c 4f 47 23 30 45 41 37 30 45 50 48 4f 4e 4f`
> = `#0EA70DANALOG#0EA70EPHONO`. The A7 `NN` is a **fixed bogus `0E`** regardless of label
> length (ANALOG payload is 10 chars, PHONO 9, both claim NN=14). Frames are delimited by the
> **next `#` or CR**, with NO CR between back-to-back A7 frames. `iter_frames` now splits on
> `#`/CR (neither byte can occur inside a hex payload). A trailing frame with no following
> delimiter stays buffered until more data/CR arrives.
>
> **[RESOLVED 2026-06-21] A7 payload = `id(2 hex) + label(literal ASCII)`. NO NUL, NO trailer,
> and critically NO enabled/offset/bypass field.** Earlier "NUL + trailer" was an artifact of
> the NN-overrun reading into the next frame. Consequence: **A7 cannot report which inputs are
> enabled** — the wire simply doesn't carry it.
>
> **[GOTCHA — mutation] There is no read-only "get input setup".** A7 is only emitted as the
> *response to `0x23` (set label)* or *`0x24` (enable/disable)*. Our first enumeration sent
> `0x24 enable=1` to all 14 ids, which **enabled inputs the user had disabled** (e.g. AES-EBU).
> Do NOT enumerate by toggling enable. Open design question: how to build the HA `source_list`
> when the device won't tell us enabled state without mutating it (likely: user picks inputs in
> the HA options flow). See IMPLEMENTATION_PLAN.md §B4.

---

## 5. Status (query) commands (0x01–0x1F)

All take no params (`NN=02`) unless noted.

| Code | Name                          | Response |
|------|-------------------------------|----------|
| 0x01 | Get UNIT status               | A3       |
| 0x02 | Get product information       | A4       |
| 0x03 | Get communication setup       | A2       |
| 0x04 | Get UNIT display string       | A6  *(only listed in Appendix A, not in body)* |
| 0x07 | Get IR                        | ?   *(only in Appendix A, undocumented)*        |
| 0x0A | Get CD input                  | AD       |
| 0x1F | Get expanded product info     | NN=04, param = subsystem ID (00=main). Resp: FE |

---

## 6. User commands (0x60–0x9F)

| Code | Name              | NN | Params / values                                                          | Resp |
|------|-------------------|----|--------------------------------------------------------------------------|------|
| 0x60 | Set power state   | 04 | 01 toggle, 02 ON, 03 OFF (standby, not mains)                            | A3   |
| 0x61 | Set display state | 04 | 01 toggle, 02 ON, 03 OFF                                                 | A3   |
| 0x62 | Set display intensity | 04 | 01 scroll, 02 low, 03 medium(default), 04 high                      | A3   |
| 0x63 | Set input selection | 04 | input ID (**Scheme A** — no swap, see §3) + 80=prev, 81=next          | A3 or A1 if input unavailable |
| 0x64 | Set master volume | 08 | action(1B) + valueMSB(1B) + valueLSB(1B)                                 | A3   |
| 0x65 | Set mute          | 04 | 01 toggle, 02 ON, 03 OFF                                                 | A3   |
| 0x66 | Set balance       | 06 | action(1B: 01 left-5%, 02 right-5%, 03 set) + value(1B 00-C8)            | A3   |
| 0x67 | Play              | 02 | none. MiND/Bluetooth only.                                               | A3   |
| 0x68 | Stop              | 02 | none. MiND/BT only. (stop bit may lag in reply)                         | A3   |
| 0x69 | Pause             | 02 | none. MiND/BT only.                                                      | A3   |
| 0x6A | Next              | 02 | none. MiND/BT only.                                                      | A3   |
| 0x6B | Previous          | 02 | none. MiND/BT only.                                                      | A3   |
| 0x6C | Set repeat mode   | 04 | 00 scroll, 01 none, 02 all, 03 one. MiND only.                          | A3   |
| 0x6D | Set random mode   | 04 | 00 scroll, 01 off, 02 on. MiND only.                                    | A3   |
| 0x6E | Request media info| 04 | 06 MiND, 07 Bluetooth                                                    | AF..B5 stream |

### Volume encoding (0x64)
- Display range 0.0–80.0 = integer **000–800** (tenths of dB).
- Encoded as 16-bit value split: `MSB (00-03)` + `LSB (00-FF)`; `value = MSB*256 + LSB`.
  - 800 = 0x0320 → MSB=`03`, LSB=`20`. Example: `#0864070320<CR>` sets volume 80.0.
- Action: 01 = −0.5 dB, 02 = −1 dB, 04 = +0.5 dB, 05 = +1 dB, 07 = set absolute. (03/06 unused.)
- Step granularity: below 30.0 the minimum step is 1 dB (10); 30.0–80.0 allows 0.5 dB (5).
  A 0.5 dB step requested below 30.0 is treated as 1 dB.
- Sending any volume **cancels MUTE**.
- MSB/LSB only used for action 07.

### Balance encoding (0x66)
- Range 0–200 (0x00–0xC8): 0 = 100% left, **100 (0x64) = center**, 200 (0xC8) = 100% right.
- Reported in A3 status balance byte the same way (00 left / 64 center / C8 right).

---

## 7. UNIT responses (0xA0–0xFE)

| Code | Name                         | NN   | Notes |
|------|------------------------------|------|-------|
| A0   | Acknowledge                  | 04   | param = acknowledged command code. Only for cmds w/o specific reply. |
| A1   | Error                        | 06   | param1 = offending command (00 if HW/corrupt), param2 = error code. |
| A2   | Communication setup          | 06   | baud(1B) + fb(BOOL) + dispFb(BOOL). |
| A3   | **UNIT status**              | 10   | main status push. See layout below. **[INCONSISTENCY on NN/example]** |
| A4   | Product information          | 08   | prodID(1B,6C=390) + swRev(1B) + commRev(1B). FFFFFF → use 0x1F instead. |
| A5   | Display string (unsolicited) | —    | listed in §8 as display-feedback push; body table mislabeled. |
| A6   | Display string response      | 0B   | NULL-terminated 8-char display string. |
| A7   | Input setup                  | 0E*  | **HARDWARE:** id(1B hex) + label(literal ASCII, var len). NO NULL, NO offset/bypass/enabled. *NN is a fixed bogus `0E`; frame ends at next `#`. Doc's "8+NULL+offset+bypass+enabled" is WRONG. |
| A9   | Factory defaults loaded      | 02   | none. |
| AA   | Wake-up                      | 02   | sent once at startup and after Reset. |
| AD   | CD setup                     | 04   | id: 01 AES, 04 SPDIF, 0C BAL, 0D ANALOG. |
| AF   | Song name                    | var  | 1-byte prefix + UTF-8 text. **[FINDING: prefix is a delimiter, not a source tag; text is UTF-8 — see below]** |
| B0   | Artist name                  | var  | 1-byte prefix + UTF-8 text. |
| B1   | Album name                   | var  | 1-byte prefix + UTF-8 text. |
| B2   | Genre name                   | var  | 1-byte prefix + UTF-8 text. |
| B3   | Album art URL                | var  | 1-byte prefix + UTF-8 URL. (URL only for MiND in practice.) |
| B4   | Total track time             | var  | 1-byte prefix + e.g. "9:10". Empty payload (prefix only) at track boundary. |
| B5   | Track playing time           | var  | 1-byte prefix + e.g. "0:01". Pushed ~1/s while playing; empty (prefix only) resets on track change. |

**[HARDWARE FINDING 2026-06-21 — media metadata, Roon] (confirmed via raw capture):**
- The 1-byte prefix on AF–B5 is **not a reliable source indicator**. In one Roon session,
  `SONG_NAME` arrived prefixed `'B'` while `TRACK_PLAYING_TIME` arrived prefixed `'M'`. Treat it
  purely as a delimiter to strip; derive the active source from A3 `input_id` instead.
- **`0x6E` (Request media info) is unreliable and should not be used.** It queries the unit's
  *internal* MiND streamer, which sits idle while Roon drives playback over RAAT, so it returns
  placeholder text (`"Media Info"` / `"Not Available"` / empty). The real now-playing data comes
  from the **unsolicited feedback stream** (§8), which DOES reflect Roon playback correctly.
- Confirmed-present over Roon: AF song, **B0 artist, B1 album, B3 album-art URL**, B4 total
  time, B5 playing time (live position). A full now-playing card is available. Same-album track
  change pushes: empty B5 (reset) → B4 total → AF song → B5 restart. A new-album change also
  pushes B0 artist, B1 album, and a B3 art URL.
- **Text is UTF-8, not ASCII.** Artist "João Gilberto" arrives as `b'MJo\xc3\xa3o Gilberto'`.
  Decode AF–B5 payloads as UTF-8 (`errors="replace"`). Decoding as ASCII produces mojibake.
- B3 album-art URL points at the unit's own HTTP server, e.g.
  `http://<unit-ip>:80/file/stream//tmp/temp_data_roonAlbum_<hash>` — directly usable by HA.
- **Playback stop (end of album) pushes an all-empty media burst:** empty B5 (×2), then
  empty B1/B0/AF/B3 (album/artist/song/art). Treat an empty payload as "clear this field"
  (→ `None`). NOTE: B4 total time is NOT re-sent empty, so `duration_s` lingers stale after
  stop — HA should null it out when transport state is idle/stopped.

**[HARDWARE FINDING 2026-06-29 — transport under Roon] (raw capture):**
- All transport commands **work** under Roon and are reflected in feedback:
  `PLAY` (0x67), `PAUSE` (0x69), `STOP` (0x68), `NEXT` (0x6A), `PREVIOUS` (0x6B).
  Next/prev trigger a track-change burst (empty B5 → new metadata → B5 restart); stop triggers
  the all-empty clear burst.
- `PAUSE` (0x69) is a **play/pause toggle**: position freezes while paused and resumes on the
  next press (observed: paused at 0:29, resumed at 0:30 after a 2nd press ~3 s later).
- No `ACK`/`A1` is returned for transport commands; the effect is observed only via feedback.
- HA: advertise PLAY / PAUSE / STOP / NEXT / PREVIOUS (all functional).
| FE   | Expanded product info        | 24   | see layout below. |

### Error codes (A1 param2)
`01` unknown command · `02` hardware interface error · `03` invalid parameter ·
`04` invalid/corrupted packet · `05` UNIT in standby · `06` UNIT in mute ·
`07` option not installed.

### A3 — UNIT status response layout (`NN` should be **0x10** = 16 chars after `#NN`)
Order of HEX bytes after code `A3`:
1. Master volume MSB (00–03)
2. Master volume LSB (00–FF)   → volume = MSB*256+LSB (tenths dB, 0–800)
3. Balance (00 left / 64 center / C8 right)
4. Selected input ID (Scheme A, 01–0E)
5. Active sample rate (see table below)
6. State byte #1 (bitfield)
7. State byte #2 (bitfield)

**State byte #1 bits:** b0 UNIT ON · b1 Mute active · b2 DAC locked · b3 Display OFF ·
b4 Power-supply fault · b5 DC detected · b6/b7 unused.

**State byte #2 bits:** b0 MiND Repeat-One · b1 MiND Repeat-All · b2 MiND Shuffle · b3-7 unused.

**Active sample rate values:** 00 none · 01 44.1k · 02 48k · 03 88.2k · 04 96k · 05 176.4k ·
06 192k · 07 352.8k · 08 384k · 09 DSD64 · 0A DSD128 · 0B DSD256 · 0C DSD512 ·
0D-14 MQA (44.1…384) · 15-1C MQA Studio (44.1…384).

> **[RESOLVED 2026-06-21 on hardware] A3 size = `NN=10`, 7-field layout.**
> A live 390 returns e.g. `#10A301906405000100<CR>` → params `01906405000100` = 7 bytes:
> vol `0190`=400 (40.0 dB), balance `64`=center, input `05`=MiND, sr `00`=none,
> state1 `01` (powered), state2 `00`. The `NN=08`/3-byte figures in Appendix A and the
> §7.4 "valid example" are stale — ignore them. Field order above is confirmed.
> We still **parse by the declared NN** (don't hardcode length) so a future firmware that
> appends fields won't break us, but 7 fields is what this unit sends.

### FE — Expanded product info (main system, `NN=24` = 36)
Params after code `FE`:
1. Sub-system ID (`00` = main)
2. Serial number `aaabbbbccccc`: `aaa`=date code (ASCII, "000"=unknown), `bbbb`=product number (hex), `ccccc`=serial (hex)
3. Quantity of sub-systems (1B)
4. Product identification number (2B, 0000–FFFF)
5. Main hardware revision (1B)
6. Main firmware revision `xxyyzzzz`: xx major, yy minor, zzzz build
7. Communication software revision (1B)
8. Boot code revision (1B)

Example: `#24FE0000J00710123401007101010112340202<CR>`.

**[HARDWARE FINDING 2026-06-29 — device identity] (raw capture, IDENTICAL in standby and
powered-on — these are the unit's real values, not a standby artifact):**
- `FE` payload: `0000000000000003006C02011500000100` → subsystem `00`, **serial all zeros**
  (date `000`=unknown / product `0000` / serial `00000`), qty `03`, product-id `006C` (108),
  HW rev `02`, firmware `01150000` (v1.21), comm `01`, boot `00`.
- **This unit reports NO usable serial** (blank/unknown), powered or not → it cannot be the HA
  `unique_id`. **Fall back to host/IP** (DHCP-reserve recommended). `_parse_serial` extracts the
  `aaabbbbccccc` field and returns `None` on the all-zero sentinel (done).
- **All 3 sub-systems swept (FE 00/01/02) — every serial field is blank**, so there is no serial
  anywhere, not just in `main`. Sub `01`: product `016C`, fw v1.02; sub `02`: product `026C`, fw
  v1.01 (the product-id high byte mirrors the sub-system index). Confirms host `unique_id`.
- **`A4` product-info returns `FFFFFF`** (unimplemented on this model, powered or not). Use
  **`FE`** for `device_info` (product-id `0x6C`, firmware v1.21); model name is a static string.

---

## 8. Unsolicited feedback (push)

Controlled by Set comm params (0x20):
- **Unsolicited feedback** (default **ON**): pushes A3, A6*, A9, AA, AF, B0, B1, B2, B3, B4, B5
  when corresponding state changes. **B5 (playing time) is pushed every second.**
- **Unsolicited display feedback** (default **OFF**): pushes A5 (display string) on any display
  change, even when display is off.
- **Regardless of settings**, the unit sends A3 + AA at initial startup.

Implication for our software:
- The TCP reader must be a continuous parser **delimiting frames on `#`/CR** (NOT on NN — see
  the framing finding in §3; some bursts have no CR and a bogus NN), dispatching by response
  code — it cannot assume one-reply-per-command.
- Consider whether to keep feedback ON (nice for a live UI; noisy due to per-second B5) or
  turn it OFF and poll. For a custom automation integration the doc warns against also using
  SimLink simultaneously (control conflicts).

---

## 9. Infrared (RC5) — out of scope for now

Philips RC5, **system number 16**, some codes in the extended range. Command codes incl.:
00-09 input select (AES/OPT/SPDIF/USB/MiND/BT/HDMI1-4), 12 power toggle, 13 mute toggle,
15 display intensity scroll, 61 power toggle (SimLink broadcast), 62/63 prev/next input,
88/89 mute on/off, 123/124 power on/off. (Detailed RC5 framing: contact NXP.)

---

## 10. Implementation checklist / gotchas

Status as of 2026-06-21 (Part A library built; 34 offline tests passing).

- [x] Frame codec: build `#` + 2-hex NN + 2-hex code + params + CR; **compute NN from params** on send.
- [x] Hex param helper: byte → 2 ASCII hex; accept upper/lower on decode.
- [x] Streaming frame splitter — **delimiter-based on `#`/CR** (NOT NN-driven; NN is unreliable).
      Handles partial reads, back-to-back no-CR bursts, leading junk. Validated on real A7 bytes.
- [x] Dispatch table keyed by response code (A0–FE) in `client.Moon390._handle_frame`.
- [x] Volume: 0–800 ↔ MSB/LSB; display value = raw/10 dB.
- [x] Balance: 0–200, 100=center.
- [x] Input ID maps: **single Scheme A everywhere** including 0x63. **Confirmed on hardware:
      no BALANCED/ANALOG swap** (probe `b`: 0x0C→BALANCED, 0x0D→ANALOG).
- [x] A3 status — confirmed on hardware: **NN=10, 7 fields**; parsed length-defensively by content
      (NN ignored for framing).
- [x] A7 input labels — **RESOLVED on hardware: literal ASCII, variable length, no NULL/trailer,
      no enabled flag.** (`0x23` write-length untested/deferred — HA doesn't write labels.)
- [x] Source list decision: **expose all 14 canonical inputs** (device can't report enabled state
      without mutating). No A7 enumeration in normal flow.
- [x] Feedback: keep **ON** (local_push); expect ~1 Hz B5 traffic — coalesce into media_position.
- [ ] ⚠️ Never enumerate inputs via `0x24` enable — it MUTATES enabled state (gated behind a
      confirm in the harness).
- [ ] Map error codes; handle A1 (esp. 05 standby / 06 mute / 03 invalid param).
