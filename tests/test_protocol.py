"""Offline tests for the moon390 frame codec, maps, and decoders.

Run from the project root:  python -m pytest    (or: python tests/test_protocol.py)
No hardware or sockets involved.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moon390 import models, protocol as P  # noqa: E402


# --------------------------------------------------------------------------- #
# build_frame / NN computation
# --------------------------------------------------------------------------- #
def test_worked_examples_from_doc():
    # #021F  -> command 0x1F, no params, NN=02
    assert P.build_frame(0x1F) == b"#021F\r"
    # #041801 -> command 0x18, param 0x01, NN=04
    assert P.build_frame(0x18, b"01") == b"#041801\r"
    # #064B1101 -> command 0x4B, four BOOLEAN params, NN=06
    assert P.build_frame(0x4B, b"1101") == b"#064B1101\r"
    # #06200311 -> set comm params 9600/fb on/disp on
    assert P.build_frame(0x20, b"0311") == b"#06200311\r"


def test_build_command_hex_params():
    # Volume set to 80.0 == 800 == 0x0320 -> MSB 03, LSB 20
    assert P.build_command(0x64, 0x07, 0x03, 0x20) == b"#0864070320\r"


def test_nn_is_computed_for_ascii_label():
    # 0x23 set-label, id 0x02, label "ANDRMEDA" + NULL.
    label = b"".join(P.hexbyte(ord(c)) for c in "ANDRMEDA") + b"00"
    frame = P.build_frame(0x23, P.hexbyte(0x02) + label)
    # body = code(2) + id(2) + 8*2 chars + NULL(2) = 22 chars == 0x16
    assert frame.startswith(b"#16")
    assert frame.endswith(b"\r")
    # NN reflects actual bytes, not a hardcoded 0F/0D.
    nn = int(frame[1:3], 16)
    assert nn == len(frame) - 4  # minus '#', NN(2), CR


def test_volume_too_long_or_out_of_range():
    try:
        P.encode_volume_raw(801)
        assert False, "expected ValueError"
    except ValueError:
        pass


# --------------------------------------------------------------------------- #
# Hex helpers
# --------------------------------------------------------------------------- #
def test_hex_helpers_roundtrip_and_case():
    assert P.hexbyte(0x1F) == b"1F"
    assert P.unhex(b"1f") == 0x1F  # lower-case accepted
    assert P.unhex(b"1F") == 0x1F
    assert P.unhex_pairs(b"0320") == [0x03, 0x20]


# --------------------------------------------------------------------------- #
# iter_frames -- streaming splitter
# --------------------------------------------------------------------------- #
def test_iter_single_frame():
    buf = bytearray(b"#021F\r")
    frames = P.iter_frames(buf)
    assert len(frames) == 1
    assert frames[0].code == 0x1F
    assert frames[0].params == b""
    assert buf == b""  # fully consumed


def test_iter_back_to_back_frames():
    buf = bytearray(b"#0864070320\r#021F\r")
    frames = P.iter_frames(buf)
    assert [f.code for f in frames] == [0x64, 0x1F]


def test_iter_partial_read_leaves_tail():
    buf = bytearray(b"#0864070320\r#02")  # second frame incomplete
    frames = P.iter_frames(buf)
    assert [f.code for f in frames] == [0x64]
    assert buf == b"#02"  # partial retained
    # finish the frame on the next read
    buf.extend(b"1F\r")
    frames = P.iter_frames(buf)
    assert [f.code for f in frames] == [0x1F]
    assert buf == b""


def test_iter_strips_leading_junk():
    buf = bytearray(b"garbage\x00#021F\r")
    frames = P.iter_frames(buf)
    assert [f.code for f in frames] == [0x1F]


def test_iter_real_a7_burst_from_hardware():
    # The EXACT bytes captured from a 390: #0EA70DANALOG#0EA70EPHONO
    # NN=0E is bogus/fixed; frames are delimited by the next '#'. The final
    # frame (PHONO) has no following delimiter so it stays buffered.
    buf = bytearray(bytes.fromhex(
        "23 30 45 41 37 30 44 41 4e 41 4c 4f 47 "
        "23 30 45 41 37 30 45 50 48 4f 4e 4f".replace(" ", "")
    ))
    frames = P.iter_frames(buf)
    assert [f.code for f in frames] == [0xA7]  # ANALOG; PHONO awaits a delimiter
    setup = models.parse_input_setup(frames[0].params)
    assert setup.input_id == 0x0D
    assert setup.label == "ANALOG"
    assert bytes(buf) == b"#0EA70EPHONO"  # last frame retained until terminator
    # A trailing CR (or next frame) flushes the final one.
    buf.extend(b"\r")
    frames = P.iter_frames(buf)
    assert models.parse_input_setup(frames[0].params).label == "PHONO"


def test_iter_a7_followed_by_cr_terminated_frame():
    # A7 burst then a normal CR-terminated frame -> all flush.
    buf = bytearray(b"#0EA70DANALOG#0EA70EPHONO#021F\r")
    frames = P.iter_frames(buf)
    labels = [
        models.parse_input_setup(f.params).label
        for f in frames
        if f.code == 0xA7
    ]
    assert labels == ["ANALOG", "PHONO"]
    assert any(f.code == 0x1F for f in frames)


def test_iter_partial_a7_then_more():
    buf = bytearray(b"#0EA70DANA")  # label mid-stream, no delimiter yet
    assert P.iter_frames(buf) == []  # nothing emitted; waiting
    buf.extend(b"LOG#0EA70EPHONO\r")
    frames = P.iter_frames(buf)
    labels = [models.parse_input_setup(f.params).label for f in frames]
    assert labels == ["ANALOG", "PHONO"]


# --------------------------------------------------------------------------- #
# A3 status -- length-defensive (NN=08 vs NN=10 inconsistency)
# --------------------------------------------------------------------------- #
def test_status_full_seven_fields():
    # vol=800(0320), bal=64(center), input=05(MiND), sr=01(44.1),
    # state1=0x05 (ON + DAC locked), state2=0x02 (repeat all)
    params = b"032064" + b"05" + b"01" + b"05" + b"02"
    out = models.parse_status(params)
    assert out["volume_raw"] == 800
    assert out["balance_raw"] == 0x64
    assert out["input_id"] == 0x05
    assert out["sample_rate"] == "44.1 kHz"
    assert out["powered"] is True
    assert out["muted"] is False
    assert out["dac_locked"] is True
    assert out["repeat"] == "all"
    assert out["shuffle"] is False


def test_status_short_three_bytes_does_not_crash():
    # The stale NN=08 variant: only 3 bytes present.
    params = b"032064"
    out = models.parse_status(params)
    assert out["volume_raw"] == 800
    assert out["balance_raw"] == 0x64
    assert "input_id" not in out  # nothing fabricated beyond what's present


def test_status_state_bits():
    params = b"000000" + b"00" + b"00" + b"0A" + b"05"
    # state1=0x0A -> mute(b1) + display_off(b3); state2=0x05 -> repeat-one + shuffle
    out = models.parse_status(params)
    assert out["muted"] is True
    assert out["display_off"] is True
    assert out["powered"] is False
    assert out["repeat"] == "one"
    assert out["shuffle"] is True


# --------------------------------------------------------------------------- #
# Input scheme A/B trap
# --------------------------------------------------------------------------- #
def test_select_input_single_scheme_no_swap():
    # HARDWARE 2026-06-21: 0x63 uses Scheme A exactly -- NO BALANCED/ANALOG swap.
    assert P.INPUTS_SCHEME_A[0x0C] == "BALANCED"
    assert P.INPUTS_SCHEME_A[0x0D] == "ANALOG"
    assert P.select_input_id("BALANCED") == 0x0C
    assert P.select_input_id("ANALOG") == 0x0D
    assert P.select_input_id("MiND") == 0x05
    # The bogus Scheme-B swap map/function must be gone.
    assert not hasattr(P, "scheme_a_to_b")
    assert not hasattr(P, "INPUTS_SCHEME_B")


def test_select_input_unknown_name():
    try:
        P.select_input_id("NOPE")
        assert False, "expected ValueError"
    except ValueError:
        pass


# --------------------------------------------------------------------------- #
# A7 input setup -- label NULL-termination (8 vs 12 char ambiguity)
# --------------------------------------------------------------------------- #
def test_input_setup_literal_ascii_label():
    # HARDWARE: label is LITERAL ASCII, NUL-terminated (not hex pairs).
    # id=0B, label "TV", NUL, then trailer (hex, format TBD).
    params = b"0B" + b"TV" + b"\x00" + b"640001"
    setup = models.parse_input_setup(params)
    assert setup.input_id == 0x0B
    assert setup.label == "TV"
    assert setup.display_name == "TV"


def test_input_setup_real_world_blob_does_not_crash():
    # The exact param blob that crashed the old hex-pair parser. Must not raise;
    # id and literal label parse out, trailing junk is tolerated.
    params = b"01AES-EBU#0EA702OPTICAL 1A001"
    setup = models.parse_input_setup(params)
    assert setup.input_id == 0x01
    assert setup.label.startswith("AES-EBU")


def test_input_setup_no_nul_does_not_crash():
    params = b"06BLUETOOTHA001"
    setup = models.parse_input_setup(params)
    assert setup.input_id == 0x06
    assert "BLUETOOTH" in setup.label


def test_input_setup_empty_label_falls_back_to_scheme_a():
    params = b"03" + b"\x00" + b"640001"
    setup = models.parse_input_setup(params)
    assert setup.label == ""
    assert setup.display_name == "SPDIF"  # Scheme A name for 0x03


# --------------------------------------------------------------------------- #
# Volume / level conversions
# --------------------------------------------------------------------------- #
def test_volume_encoding():
    assert P.encode_volume_raw(800) == (0x03, 0x20)
    assert P.encode_volume_raw(0) == (0x00, 0x00)
    assert P.encode_volume_raw(255) == (0x00, 0xFF)
    assert P.encode_volume_raw(256) == (0x01, 0x00)


def test_level_to_raw_clamps():
    assert P.level_to_raw(0.0) == 0
    assert P.level_to_raw(1.0) == 800
    assert P.level_to_raw(0.5) == 400
    assert P.level_to_raw(2.0) == 800  # clamped
    assert P.level_to_raw(-1.0) == 0


# --------------------------------------------------------------------------- #
# Media text + track time
# --------------------------------------------------------------------------- #
def test_media_text_prefix():
    tag, text = models.parse_media_text(b"M" + b"Bohemian Rhapsody")
    assert tag == "M"
    assert text == "Bohemian Rhapsody"


def test_track_time_parsing():
    assert models.parse_track_time("3:45") == 225
    assert models.parse_track_time("1:02:03") == 3723
    assert models.parse_track_time("") is None
    assert models.parse_track_time("--") is None


# --------------------------------------------------------------------------- #
# MoonState convenience
# --------------------------------------------------------------------------- #
def test_state_volume_conversions():
    s = models.MoonState(volume_raw=400)
    assert s.volume_db == 40.0
    assert s.volume_level == 0.5


def test_source_list_fallback_and_a7():
    s = models.MoonState()
    # No A7 data -> full Scheme A list.
    assert "MiND" in s.source_list()
    assert len(s.source_list()) == len(P.INPUTS_SCHEME_A)
    # With A7 data -> only enabled, custom labels.
    s.inputs[0x05] = models.InputSetup(0x05, "Streamer", enabled=True)
    s.inputs[0x07] = models.InputSetup(0x07, "", enabled=False)
    assert s.source_list() == ["Streamer"]


if __name__ == "__main__":
    import traceback

    ns = dict(globals())
    tests = [v for k, v in ns.items() if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
            print(f"  PASS {t.__name__}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"  FAIL {t.__name__}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
