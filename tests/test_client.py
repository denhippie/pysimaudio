"""Offline tests for Moon390 frame dispatch and the manual harness printer.

Drives the client's _handle_frame directly (no socket) to verify state updates
and listener notification. Run: python tests/test_client.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moon390 import Moon390, protocol as P  # noqa: E402


def _feed(moon: Moon390, wire: bytes):
    """Push raw wire bytes through the same path the reader loop uses."""
    buf = bytearray(wire)
    for frame in P.iter_frames(buf):
        moon._handle_frame(frame)


def test_status_updates_state_and_notifies():
    moon = Moon390("test")
    seen: list = []
    moon.add_listener(lambda s: seen.append(s.volume_raw))
    # A3: vol=400(0190), bal=64, input=05, sr=04(96k), state1=01(on), state2=00
    _feed(moon, b"#10A3" + b"019064050401" + b"00" + b"\r")
    assert moon.state.volume_raw == 400
    assert moon.state.powered is True
    assert moon.state.input_id == 0x05
    assert moon.state.sample_rate == "96 kHz"
    assert seen and seen[-1] == 400


def test_media_text_pushes():
    moon = Moon390("test")
    song = b"M" + b"Test Song"
    frame = P.build_frame(P.Resp.SONG_NAME, song)
    _feed(moon, frame)
    assert moon.state.media.title == "Test Song"
    assert moon.state.media.source_tag == "M"


def test_track_time_push_sets_position():
    moon = Moon390("test")
    _feed(moon, P.build_frame(P.Resp.TRACK_PLAYING_TIME, b"M" + b"1:30"))
    assert moon.state.media.position_s == 90


def test_input_setup_populates_inputs():
    moon = Moon390("test")
    # Literal-ASCII label + real NUL terminator + trailer.
    params = b"05" + b"Net" + b"\x00" + b"640001"
    _feed(moon, P.build_frame(P.Resp.INPUT_SETUP, params))
    assert moon.state.inputs[0x05].label == "Net"
    assert moon.state.source_list() == ["Net"]


def test_error_frame_does_not_crash_or_notify():
    moon = Moon390("test")
    calls: list = []
    moon.add_listener(lambda s: calls.append(1))
    _feed(moon, P.build_command(P.Resp.ERROR, 0x63, 0x03))  # invalid param
    assert calls == []  # error frames don't fire state-change notifications


def test_unknown_frame_ignored():
    moon = Moon390("test")
    _feed(moon, b"#04FF00\r")  # unknown code 0xFF
    # no exception == pass


def test_harness_printer_runs():
    """describe_frame must not raise on representative frames."""
    import manual_test

    samples = [
        P.build_frame(P.Resp.STATUS, b"019064050401" + b"00"),
        P.build_frame(P.Resp.SONG_NAME, b"MHello"),
        P.build_command(P.Resp.ERROR, 0x63, 0x03),
        P.build_frame(P.Resp.INPUT_SETUP, b"05" + b"4E657400" + b"640001"),
    ]
    buf = bytearray(b"".join(samples))
    for f in P.iter_frames(buf):
        out = manual_test.describe_frame(f)
        assert out.startswith("<-")


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
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
