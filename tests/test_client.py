"""Offline tests for Moon390 frame dispatch and the manual harness printer.

Drives the client's _handle_frame directly (no socket) to verify state updates
and listener notification. Run: python tests/test_client.py
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)  # repo root, for `import manual_test`
sys.path.insert(0, os.path.join(_ROOT, "custom_components", "simaudio_moon"))  # for `moon390`

from moon390 import Moon390, protocol as P  # noqa: E402


def _feed(moon: Moon390, wire: bytes) -> None:
    """Push raw wire bytes through the same path the reader loop uses."""
    buf = bytearray(wire)
    for frame in P.iter_frames(buf):
        moon._handle_frame(frame)


def test_status_updates_state_and_notifies() -> None:
    moon = Moon390("test")
    seen: list[int | None] = []
    moon.add_listener(lambda s: seen.append(s.volume_raw))
    # A3: vol=400(0190), bal=64, input=05, sr=04(96k), state1=01(on), state2=00
    _feed(moon, b"#10A3" + b"019064050401" + b"00" + b"\r")
    assert moon.state.volume_raw == 400
    assert moon.state.powered is True
    assert moon.state.input_id == 0x05
    assert moon.state.sample_rate == "96 kHz"
    assert seen and seen[-1] == 400


def test_media_text_pushes() -> None:
    moon = Moon390("test")
    song = b"M" + b"Test Song"
    frame = P.build_frame(P.Resp.SONG_NAME, song)
    _feed(moon, frame)
    assert moon.state.media.title == "Test Song"


def test_track_time_push_sets_position() -> None:
    moon = Moon390("test")
    _feed(moon, P.build_frame(P.Resp.TRACK_PLAYING_TIME, b"M" + b"1:30"))
    assert moon.state.media.position_s == 90


def test_empty_track_time_resets_position() -> None:
    """Track boundary pushes an empty B5 (prefix only) -> position clears."""
    moon = Moon390("test")
    _feed(moon, P.build_frame(P.Resp.TRACK_PLAYING_TIME, b"M1:30"))
    _feed(moon, P.build_frame(P.Resp.TRACK_PLAYING_TIME, b"M"))  # boundary
    assert moon.state.media.position_s is None


def test_stop_burst_clears_media_fields_to_none() -> None:
    """End of playback pushes empty AF/B0/B1/B3 -> fields clear to None (not '')."""
    moon = Moon390("test")
    _feed(moon, P.build_frame(P.Resp.SONG_NAME, b"MO Pato"))
    _feed(moon, P.build_frame(P.Resp.ARTIST_NAME, b"MJoao"))
    assert moon.state.media.title == "O Pato"
    for code in (P.Resp.ALBUM_NAME, P.Resp.ARTIST_NAME, P.Resp.SONG_NAME, P.Resp.ALBUM_ART_URL):
        _feed(moon, P.build_frame(code, b"M"))  # empty payload
    media = moon.state.media
    assert media.title is None
    assert media.artist is None
    assert media.album is None
    assert media.image_url is None


def test_media_text_decodes_utf8() -> None:
    """AF-B5 text is UTF-8 (real capture: b'MJo\\xc3\\xa3o Gilberto')."""
    moon = Moon390("test")
    _feed(moon, P.build_frame(P.Resp.ARTIST_NAME, b"MJo\xc3\xa3o Gilberto"))
    assert moon.state.media.artist == "João Gilberto"


def test_album_art_url_push() -> None:
    moon = Moon390("test")
    url = "http://192.168.2.19:80/file/stream//tmp/temp_data_roonAlbum_abc"
    _feed(moon, P.build_frame(P.Resp.ALBUM_ART_URL, b"M" + url.encode("ascii")))
    assert moon.state.media.image_url == url


def test_input_setup_populates_inputs() -> None:
    moon = Moon390("test")
    # Literal-text label + real NUL terminator + trailer.
    params = b"05" + b"Net" + b"\x00" + b"640001"
    _feed(moon, P.build_frame(P.Resp.INPUT_SETUP, params))
    assert moon.state.inputs[0x05].label == "Net"
    assert moon.state.source_list() == ["Net"]


def test_error_frame_does_not_crash_or_notify() -> None:
    moon = Moon390("test")
    calls: list[int] = []
    moon.add_listener(lambda s: calls.append(1))
    _feed(moon, P.build_command(P.Resp.ERROR, 0x63, 0x03))  # invalid param
    assert calls == []  # error frames don't fire state-change notifications


def test_expanded_info_blank_serial_is_none() -> None:
    # Real capture (2026-06-29): the all-zero serial field is the "unknown"
    # sentinel and must parse to None so HA falls back to host for unique_id.
    moon = Moon390("test")
    _feed(moon, P.build_frame(P.Resp.EXPANDED_INFO, b"0000000000000003006C02011500000100"))
    assert moon.state.serial is None


def test_expanded_info_real_serial_parsed() -> None:
    # subsystem "00" + serial "00J007101234" (date 00J / product 0071 / serial 01234) + trailer.
    moon = Moon390("test")
    _feed(moon, P.build_frame(P.Resp.EXPANDED_INFO, b"0000J0071012340100"))
    assert moon.state.serial == "00J007101234"


def test_expanded_info_short_payload_is_none() -> None:
    moon = Moon390("test")
    _feed(moon, P.build_frame(P.Resp.EXPANDED_INFO, b"0000"))
    assert moon.state.serial is None


def test_unknown_frame_ignored() -> None:
    moon = Moon390("test")
    _feed(moon, b"#04FF00\r")  # unknown code 0xFF
    # no exception == pass


def test_connect_timeout_raises_moon_connection_error() -> None:
    """A hung TCP connect must surface as MoonConnectionError, not hang."""
    import asyncio
    from unittest.mock import patch

    from moon390 import MoonConnectionError, client as client_mod

    async def _hang(*_args: object, **_kwargs: object) -> tuple[object, object]:
        await asyncio.sleep(10)
        return None, None

    async def _run() -> None:
        moon = Moon390("test")
        try:
            await moon.connect()
        except MoonConnectionError:
            return
        raise AssertionError("expected MoonConnectionError")

    with (
        patch("asyncio.open_connection", _hang),
        patch.object(client_mod, "_CONNECT_TIMEOUT", 0.01),
    ):
        asyncio.run(_run())


def test_harness_printer_runs() -> None:
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
