#!/usr/bin/env python3
"""Semi-manual integration harness for a real MOON Neo 390.

Two test directions, plus targeted probes for the PROTOCOL_NOTES.md ambiguities:

  * "I send -> you confirm":  the harness performs an action; you watch the unit
    (front panel / speakers) and confirm it did the thing.
  * "You act -> I confirm":   the LISTEN mode prints every frame the unit pushes;
    you press buttons / turn the knob and we report what we saw.

Usage:
    python manual_test.py <device-ip>
    python manual_test.py            # will prompt for the IP
    MOON_HOST=192.168.1.50 python manual_test.py

Nothing here is destructive beyond normal control (volume/mute/input). The label
probe restores the original label afterwards.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom_components", "simaudio_moon")
)

from moon390 import Moon390, protocol as P  # noqa: E402
from moon390 import models  # noqa: E402

DIVIDER = "-" * 64


# --------------------------------------------------------------------------- #
# Pretty-printing incoming frames
# --------------------------------------------------------------------------- #
def describe_frame(frame: P.Frame) -> str:
    code = frame.code
    name = P.Resp(code).name if code in P.Resp._value2member_map_ else f"0x{code:02X}"
    detail = ""
    try:
        if code == P.Resp.STATUS:
            st = models.parse_status(frame.params)
            nn = len(frame.params) + 2  # +2 for the code itself
            iid = st.get("input_id")
            iname = P.INPUTS_SCHEME_A.get(iid, "?") if isinstance(iid, int) else "?"
            detail = (
                f"NN(observed)={nn:#04x} bytes={len(frame.params)//2} "
                f"vol={st.get('volume_raw')} "
                f"input={iid}({iname}) "
                f"sr={st.get('sample_rate')} pwr={st.get('powered')} "
                f"mute={st.get('muted')} rpt={st.get('repeat')} shf={st.get('shuffle')}"
            )
        elif code == P.Resp.INPUT_SETUP:
            s = models.parse_input_setup(frame.params)
            detail = (
                f"id=0x{s.input_id:02X} label={s.label!r} "
                f"enabled={s.enabled} bypass={s.bypass} offset={s.offset_raw} "
                f"(label_len={len(s.label)})"
            )
        elif code in (
            P.Resp.SONG_NAME,
            P.Resp.ARTIST_NAME,
            P.Resp.ALBUM_NAME,
            P.Resp.GENRE_NAME,
            P.Resp.ALBUM_ART_URL,
            P.Resp.TOTAL_TRACK_TIME,
            P.Resp.TRACK_PLAYING_TIME,
        ):
            tag, text = models.parse_media_text(frame.params)
            detail = f"[{tag}] {text!r}"
        elif code == P.Resp.ERROR:
            b = frame.param_bytes
            if len(b) >= 2:
                detail = (
                    f"cmd=0x{b[0]:02X} err=0x{b[1]:02X} "
                    f"({P.ERROR_CODES.get(b[1], '?')})"
                )
        elif code == P.Resp.PRODUCT_INFO:
            detail = str(models.parse_product_info(frame.params))
        elif code == P.Resp.EXPANDED_INFO:
            detail = f"raw={frame.params!r}"
    except Exception as err:  # noqa: BLE001
        detail = f"<decode error: {err}>"
    return f"<- {name:<18} raw={frame.params!r}  {detail}"


def attach_printer(moon: Moon390) -> Callable[[], None]:
    return moon.add_raw_listener(lambda f: print(describe_frame(f)))


async def ainput(prompt: str = "") -> str:
    return await asyncio.to_thread(input, prompt)


async def confirm(question: str) -> bool:
    ans = (await ainput(f"  >> {question} [y/n] ")).strip().lower()
    return ans.startswith("y")


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #
async def listen_mode(moon: Moon390) -> None:
    print(DIVIDER)
    print("LISTEN MODE: now go press buttons / turn the volume knob / change input")
    print("on the unit (or its remote). Every pushed frame is printed below.")
    print("Press ENTER here to stop.\n")
    detach = attach_printer(moon)
    await ainput("")
    detach()
    print("(stopped listening)\n")


async def snapshot(moon: Moon390) -> None:
    print(DIVIDER)
    print("Requesting A3 status (watch the raw bytes + observed NN)...")
    detach = attach_printer(moon)
    await moon.get_status()
    await asyncio.sleep(1.0)
    detach()
    s = moon.state
    print(
        f"\nDecoded: power={s.powered} vol={s.volume_db}dB ({s.volume_raw}) "
        f"mute={s.muted} input={s.input_name} sr={s.sample_rate} "
        f"rpt={s.repeat} shf={s.shuffle}\n"
    )


async def probe_a3_length(moon: Moon390) -> None:
    """Ambiguity #1: is A3's NN 0x08 (3 bytes) or 0x10 (7 bytes)?"""
    print(DIVIDER)
    print("PROBE: A3 status length (doc says both NN=08 and NN=10).")
    captured: list[P.Frame] = []
    detach = moon.add_raw_listener(
        lambda f: captured.append(f) if f.code == P.Resp.STATUS else None
    )
    await moon.get_status()
    await asyncio.sleep(1.0)
    detach()
    if not captured:
        print("  No A3 received!\n")
        return
    f = captured[-1]
    nbytes = len(f.params) // 2
    nn = len(f.params) + 2
    print(f"  Observed: NN={nn:#04x}, {nbytes} payload bytes, raw={f.params!r}")
    print(f"  => This unit uses the {'7-field (NN=10)' if nbytes >= 7 else '3-field (NN=08)'} layout.\n")


async def probe_input_scheme(moon: Moon390) -> None:
    """Ambiguity #2: does 0x63 use Scheme A or B for BALANCED/ANALOG (0C/0D)?"""
    print(DIVIDER)
    print("PROBE: 0x63 BALANCED/ANALOG scheme. We'll send raw id 0x0C, then 0x0D,")
    print("and you tell us which physical input the unit actually selects.")
    print("(CONFIRMED single Scheme A: 0x0C selects BALANCED, 0x0D selects ANALOG.)\n")
    detach = attach_printer(moon)
    for raw_id in (0x0C, 0x0D):
        print(f"  Sending 0x63 with id 0x{raw_id:02X} ...")
        await moon.select_input_by_id(raw_id)
        await asyncio.sleep(1.5)
        which = (await ainput(f"  >> Which input lit up on the unit for 0x{raw_id:02X}? ")).strip()
        print(f"     recorded: 0x{raw_id:02X} -> {which!r}")
    detach()
    print()


async def probe_media_info(moon: Moon390) -> None:
    """Capture the AF..B5 now-playing stream from the unit's feedback pushes.

    Verifies PROTOCOL_NOTES.md §media: B4/B5 track-time format, whether B3
    album-art URL appears, UTF-8 text, and the B5 (playing time) cadence.

    We deliberately do NOT send 0x6E -- it queries the idle internal streamer and
    returns placeholders during Roon/RAAT playback. The feedback stream (ON by
    default) is the source of truth, so just play/skip a track and watch.
    """
    print(DIVIDER)
    print("PROBE: media info (passive). Make sure something is PLAYING, then")
    print("SKIP A TRACK during the window so the unit pushes fresh AF..B5 frames.")
    print("B5 (playing time) should tick about once a second. Capturing for 15s.\n")

    detach = attach_printer(moon)
    await asyncio.sleep(15.0)
    detach()

    m = moon.state.media
    print(
        "\nAssembled MediaInfo (what HA would see):\n"
        f"  title={m.title!r}\n"
        f"  artist={m.artist!r}\n"
        f"  album={m.album!r}\n"
        f"  genre={m.genre!r}\n"
        f"  image_url={m.image_url!r}\n"
        f"  duration_s={m.duration_s}  position_s={m.position_s}\n"
    )


async def probe_device_info(moon: Moon390) -> None:
    """Request product (A4) and expanded (FE) info; show raw bytes + parsed serial.

    Sweeps FE sub-systems 00..02 (this unit reports 3) in case a usable serial
    lives outside the main sub-system -- main is blank (HARDWARE FINDING 2026-06-29),
    so HA's unique_id currently falls back to host. Watch each EXPANDED_INFO raw line.
    """
    print(DIVIDER)
    print("PROBE: device info -- A4 product + FE expanded, sub-systems 00..02.\n")
    detach = attach_printer(moon)
    await moon.send(P.build_command(P.Cmd.GET_PRODUCT_INFO))
    for subsystem in (0x00, 0x01, 0x02):
        print(f"  -> requesting FE sub-system {subsystem:#04x}")
        await moon.send(P.build_command(P.Cmd.GET_EXPANDED_INFO, subsystem))
        await asyncio.sleep(0.8)
    detach()
    s = moon.state
    print(
        "\nParsed (state reflects the LAST EXPANDED_INFO handled):\n"
        f"  product_id={s.product_id}  sw_rev={s.sw_rev}  comm_rev={s.comm_rev}\n"
        f"  serial(parsed)={s.serial!r}\n"
        "  (compare the raw FE lines per sub-system above for a non-blank serial)\n"
    )


async def cmd_mute(moon: Moon390) -> None:
    detach = attach_printer(moon)
    print("Toggling mute...")
    await moon.toggle_mute()
    await asyncio.sleep(1.0)
    detach()
    await confirm("did the unit mute/unmute as expected?")
    print()


async def cmd_volume(moon: Moon390) -> None:
    val = (await ainput("  >> set volume dB (0-80), or +/- to nudge 0.5dB: ")).strip()
    detach = attach_printer(moon)
    if val == "+":
        await moon.volume_up()
    elif val == "-":
        await moon.volume_down()
    else:
        try:
            await moon.set_volume_raw(int(round(float(val) * 10)))
        except ValueError:
            print("  bad value")
            detach()
            return
    await asyncio.sleep(1.0)
    detach()
    await confirm("did the volume change as expected?")
    print()


async def cmd_select_input(moon: Moon390) -> None:
    # IMPORTANT: we do NOT auto-enumerate here. The only way to fetch A7 labels
    # is to send 0x24 (enable/disable), which MUTATES the unit's enabled inputs.
    # So menu 5 uses labels only if a prior explicit probe ('d') populated them;
    # otherwise it falls back to canonical Scheme-A names.
    if moon.state.inputs:
        choices = {
            s.display_name: s.input_id
            for s in moon.state.inputs.values()
            if s.enabled
        }
    else:
        choices = {name: i for i, name in P.INPUTS_SCHEME_A.items()}

    print("  inputs (on-device labels):")
    for label, iid in choices.items():
        canon = P.INPUTS_SCHEME_A.get(iid, "?")
        suffix = f"  [id 0x{iid:02X}, {canon}]" if label != canon else f"  [id 0x{iid:02X}]"
        print(f"    - {label}{suffix}")

    name = (await ainput("  >> input label: ")).strip()
    if name not in choices:
        print("  unknown input\n")
        return
    input_id = choices[name]
    detach = attach_printer(moon)
    await moon.select_input_by_id(input_id)  # applies Scheme A->B swap by id
    await asyncio.sleep(1.5)
    detach()
    await confirm(f"did the unit switch to {name}?")
    print()


async def cmd_raw(moon: Moon390) -> None:
    line = (await ainput("  >> raw: <code-hex> [param-hex]  e.g. '60 02': ")).strip().split()
    if not line:
        return
    code = int(line[0], 16)
    # Param is passed through as literal ASCII (build_frame hex-wraps as needed).
    frame = P.build_frame(code, line[1].encode() if len(line) > 1 else b"")
    print(f"  sending: {frame!r}")
    detach = attach_printer(moon)
    await moon.send(frame)
    await asyncio.sleep(1.0)
    detach()
    print()


async def cmd_transport(moon: Moon390) -> None:
    """Send transport commands and watch what the unit pushes back.

    UNVERIFIED on hardware: with Roon driving playback over RAAT, these IP commands
    may or may not take effect. Watch the unit / Roon app and the printed frames.
    """
    transports: dict[str, tuple[str, Callable[[], Awaitable[None]]]] = {
        "p": ("play", moon.play),
        "u": ("pause", moon.pause),
        "s": ("stop", moon.stop),
        "n": ("next", moon.next_track),
        "b": ("previous", moon.previous_track),
    }
    print(DIVIDER)
    print("TRANSPORT: p=play  u=pause  s=stop  n=next  b=previous  (ENTER to stop)")
    detach = attach_printer(moon)
    try:
        while True:
            key = (await ainput("  transport> ")).strip().lower()
            if not key:
                break
            entry = transports.get(key)
            if entry is None:
                print("  ?")
                continue
            name, fn = entry
            print(f"  sending {name} ...")
            await fn()
            await asyncio.sleep(1.0)
    finally:
        detach()
    print()


async def cmd_power(moon: Moon390) -> None:
    choice = (await ainput("  >> power [on / standby / toggle]: ")).strip().lower()
    detach = attach_printer(moon)
    if choice == "toggle":
        await moon.send(P.build_command(P.Cmd.SET_POWER, P.Power.TOGGLE))
    elif choice in ("on", "standby"):
        await moon.set_power(choice == "on")
    else:
        print("  ?")
        detach()
        return
    await asyncio.sleep(1.0)
    detach()
    await confirm("did the unit power state change as expected?")
    print()


async def cmd_repeat_shuffle(moon: Moon390) -> None:
    print(DIVIDER)
    print("REPEAT 0x6C: none/all/one   RANDOM 0x6D: on/off   (blank = skip)")
    repeats = {"none": 0x01, "all": 0x02, "one": 0x03}
    randoms = {"off": 0x01, "on": 0x02}
    detach = attach_printer(moon)
    r = (await ainput("  >> repeat [none/all/one]: ")).strip().lower()
    if r in repeats:
        await moon.send(P.build_command(P.Cmd.SET_REPEAT, repeats[r]))
        await asyncio.sleep(0.8)
    sh = (await ainput("  >> shuffle [on/off]: ")).strip().lower()
    if sh in randoms:
        await moon.send(P.build_command(P.Cmd.SET_RANDOM, randoms[sh]))
        await asyncio.sleep(0.8)
    detach()
    print("  (check A3 state byte 2: repeat/shuffle bits)\n")


async def cmd_display(moon: Moon390) -> None:
    print(DIVIDER)
    print("DISPLAY 0x61: on/off/toggle   INTENSITY 0x62: scroll/low/med/high   (blank = skip)")
    disp = {"toggle": P.OnOff.TOGGLE, "on": P.OnOff.ON, "off": P.OnOff.OFF}
    inten = {"scroll": 0x01, "low": 0x02, "med": 0x03, "high": 0x04}
    detach = attach_printer(moon)
    d = (await ainput("  >> display [on/off/toggle]: ")).strip().lower()
    if d in disp:
        await moon.send(P.build_command(P.Cmd.SET_DISPLAY, disp[d]))
        await asyncio.sleep(0.8)
    i = (await ainput("  >> intensity [scroll/low/med/high]: ")).strip().lower()
    if i in inten:
        await moon.send(P.build_command(P.Cmd.SET_DISPLAY_INTENSITY, inten[i]))
        await asyncio.sleep(0.8)
    detach()
    await confirm("did the display change as expected?")
    print()


async def cmd_balance(moon: Moon390) -> None:
    # 0x66 = action(01 left-5% / 02 right-5% / 03 set) + value(00 left .. 64 center .. C8 right).
    print(DIVIDER)
    print("BALANCE 0x66: left (nudge) / right (nudge) / center (set 0x64)")
    detach = attach_printer(moon)
    b = (await ainput("  >> balance [left/right/center]: ")).strip().lower()
    actions = {
        "left": (0x01, 0x00),
        "right": (0x02, 0x00),
        "center": (0x03, 0x64),
    }
    if b not in actions:
        print("  ?")
        detach()
        return
    action, value = actions[b]
    await moon.send(P.build_command(P.Cmd.SET_BALANCE, action, value))
    await asyncio.sleep(1.0)
    detach()
    await confirm("did the balance change as expected (check A3 balance byte)?")
    print()


async def probe_error(moon: Moon390) -> None:
    """Provoke an A1 error to exercise the error path (unexercised on hardware)."""
    print(DIVIDER)
    print("PROBE: error handling. Sending an unknown command code (0x7F) -> expect A1.\n")
    detach = attach_printer(moon)
    await moon.send(P.build_command(0x7F))  # undefined command -> 'unknown command'
    await asyncio.sleep(1.0)
    detach()
    print("  (an ERROR frame with cmd=0x7F should appear above)\n")


MENU = """
==================  MOON Neo 390 manual test  ==================
  1  Listen mode            (you act on the unit, we report)
  2  Status snapshot        (decode A3 now)
  3  Toggle mute
  4  Set / nudge volume
  5  Select input (by name)
  6  Raw send
  7  Transport               (play/pause/stop/next/prev)
  8  Power on / standby
  9  Repeat / shuffle set
  e  Display on/off + intensity
  f  Balance (left/center/right)

  -- ambiguity probes (verify against hardware) --
  a  A3 status length        (NN 08 vs 10)
  b  0x63 BALANCED/ANALOG     (Scheme A vs B)
  c  Media info stream        (AF..B5 -- play music first)
  d  Device info             (A4 product + FE serial)
  g  Provoke error           (invalid cmd -> A1)

  q  quit
================================================================
"""

ACTIONS: dict[str, Callable[[Moon390], Awaitable[None]]] = {
    "1": listen_mode,
    "2": snapshot,
    "3": cmd_mute,
    "4": cmd_volume,
    "5": cmd_select_input,
    "6": cmd_raw,
    "7": cmd_transport,
    "8": cmd_power,
    "9": cmd_repeat_shuffle,
    "e": cmd_display,
    "f": cmd_balance,
    "a": probe_a3_length,
    "b": probe_input_scheme,
    "c": probe_media_info,
    "d": probe_device_info,
    "g": probe_error,
}


async def main() -> None:
    host = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get("MOON_HOST")
        or input("Device IP: ").strip()
    )
    print(f"\nConnecting to {host}:{P.DEFAULT_PORT} ...")
    moon = Moon390(host)
    try:
        await moon.connect()
    except Exception as err:  # noqa: BLE001
        print(f"FAILED: {err}")
        return
    print("Connected. (initial seed queries sent)\n")

    try:
        while True:
            print(MENU)
            choice = (await ainput("choice> ")).strip().lower()
            if choice == "q":
                break
            action = ACTIONS.get(choice)
            if action is None:
                print("  ?\n")
                continue
            try:
                await action(moon)
            except Exception as err:  # noqa: BLE001
                print(f"  action error: {err}\n")
    finally:
        await moon.disconnect()
        print("Disconnected.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
