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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
            detail = (
                f"NN(observed)={nn:#04x} bytes={len(frame.params)//2} "
                f"vol={st.get('volume_raw')} "
                f"input={st.get('input_id')}"
                f"({P.INPUTS_SCHEME_A.get(st.get('input_id', -1), '?')}) "
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


def attach_printer(moon: Moon390):
    return moon.add_raw_listener(lambda f: print(describe_frame(f)))


async def ainput(prompt: str = "") -> str:
    return await asyncio.to_thread(input, prompt)


async def confirm(question: str) -> bool:
    ans = (await ainput(f"  >> {question} [y/n] ")).strip().lower()
    return ans.startswith("y")


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #
async def listen_mode(moon: Moon390):
    print(DIVIDER)
    print("LISTEN MODE: now go press buttons / turn the volume knob / change input")
    print("on the unit (or its remote). Every pushed frame is printed below.")
    print("Press ENTER here to stop.\n")
    detach = attach_printer(moon)
    await ainput("")
    detach()
    print("(stopped listening)\n")


async def snapshot(moon: Moon390):
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


async def probe_a3_length(moon: Moon390):
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


async def probe_input_scheme(moon: Moon390):
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


async def cmd_mute(moon: Moon390):
    detach = attach_printer(moon)
    print("Toggling mute...")
    await moon.toggle_mute()
    await asyncio.sleep(1.0)
    detach()
    await confirm("did the unit mute/unmute as expected?")
    print()


async def cmd_volume(moon: Moon390):
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


async def cmd_select_input(moon: Moon390):
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


async def cmd_raw(moon: Moon390):
    line = (await ainput("  >> raw: <code-hex> [param-hex]  e.g. '60 02': ")).strip().split()
    if not line:
        return
    code = int(line[0], 16)
    params = bytes.fromhex(line[1]) if len(line) > 1 else b""
    # interpret param as already-ASCII-hex if it looks like it, else encode bytes
    frame = P.build_frame(code, line[1].encode() if len(line) > 1 else b"")
    print(f"  sending: {frame!r}")
    detach = attach_printer(moon)
    await moon.send(frame)
    await asyncio.sleep(1.0)
    detach()
    print()


MENU = """
==================  MOON Neo 390 manual test  ==================
  1  Listen mode            (you act on the unit, we report)
  2  Status snapshot        (decode A3 now)
  3  Toggle mute
  4  Set / nudge volume
  5  Select input (by name)
  6  Raw send

  -- ambiguity probes (verify against hardware) --
  a  A3 status length        (NN 08 vs 10)
  b  0x63 BALANCED/ANALOG     (Scheme A vs B)

  q  quit
================================================================
"""

ACTIONS = {
    "1": listen_mode,
    "2": snapshot,
    "3": cmd_mute,
    "4": cmd_volume,
    "5": cmd_select_input,
    "6": cmd_raw,
    "a": probe_a3_length,
    "b": probe_input_scheme,
}


async def main():
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
