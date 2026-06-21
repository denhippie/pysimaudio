"""Async TCP client for the MOON Neo 390.

Owns a persistent connection, a background reader that parses pushed frames and
updates a MoonState, and high-level command methods. Listeners are notified on
any state change (Home Assistant subscribes here).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from . import models, protocol as P
from .exceptions import MoonCommandError, MoonConnectionError
from .models import MoonState

_LOGGER = logging.getLogger(__name__)

Listener = Callable[[MoonState], None]


class Moon390:
    """Async client for one MOON Neo 390 unit."""

    def __init__(
        self,
        host: str,
        port: int = P.DEFAULT_PORT,
        *,
        reconnect: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self._reconnect = reconnect

        self.state = MoonState()
        self._listeners: list[Listener] = []
        self._raw_listeners: list[Callable[[P.Frame], None]] = []
        self._byte_listeners: list[Callable[[bytes], None]] = []

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None
        self._write_lock = asyncio.Lock()
        self._closing = False

    # ----------------------------------------------------------------- #
    # Listeners
    # ----------------------------------------------------------------- #
    def add_listener(self, cb: Listener) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    def add_raw_listener(self, cb: Callable[[P.Frame], None]) -> Callable[[], None]:
        """Subscribe to every decoded frame (used by the manual test harness)."""
        self._raw_listeners.append(cb)
        return lambda: self._raw_listeners.remove(cb)

    def add_byte_listener(self, cb: Callable[[bytes], None]) -> Callable[[], None]:
        """Subscribe to raw socket chunks BEFORE frame-splitting (diagnostics)."""
        self._byte_listeners.append(cb)
        return lambda: self._byte_listeners.remove(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb(self.state)
            except Exception:  # noqa: BLE001 -- never let a listener kill the reader
                _LOGGER.exception("listener raised")

    # ----------------------------------------------------------------- #
    # Connection lifecycle
    # ----------------------------------------------------------------- #
    async def connect(self) -> None:
        self._closing = False
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self.host, self.port
            )
        except OSError as err:
            raise MoonConnectionError(
                f"cannot connect to {self.host}:{self.port}: {err}"
            ) from err
        self.state.available = True
        self._reader_task = asyncio.ensure_future(self._reader_loop())
        await self._seed_state()
        self._notify()

    async def disconnect(self) -> None:
        self._closing = True
        if self._reader_task:
            self._reader_task.cancel()
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
        self._reader = self._writer = self._reader_task = None
        self.state.available = False

    async def __aenter__(self) -> "Moon390":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.disconnect()

    async def _seed_state(self) -> None:
        """Query the unit so state reflects reality right after connecting."""
        await self.send(P.build_command(P.Cmd.GET_STATUS))
        await self.send(P.build_command(P.Cmd.GET_PRODUCT_INFO))
        await self.send(P.build_command(P.Cmd.GET_EXPANDED_INFO, 0x00))
        # No input enumeration: there is no read-only "get input setup" command
        # (A7 only comes back from 0x23/0x24, which MUTATE the unit), and A7 has
        # no enabled flag anyway. source_list is the static 14 inputs.

    # ----------------------------------------------------------------- #
    # Reader loop
    # ----------------------------------------------------------------- #
    async def _reader_loop(self) -> None:
        buffer = bytearray()
        backoff = 1
        while not self._closing:
            try:
                assert self._reader is not None
                chunk = await self._reader.read(4096)
                if not chunk:
                    raise MoonConnectionError("connection closed by peer")
                backoff = 1
                for cb in list(self._byte_listeners):
                    try:
                        cb(chunk)
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception("byte listener raised")
                buffer.extend(chunk)
                for frame in P.iter_frames(buffer):
                    self._handle_frame(frame)
            except asyncio.CancelledError:
                raise
            except (OSError, MoonConnectionError) as err:
                self.state.available = False
                self._notify()
                if self._closing or not self._reconnect:
                    return
                _LOGGER.warning("connection lost (%s); reconnecting in %ss", err, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
                buffer.clear()
                await self._try_reconnect()

    async def _try_reconnect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self.host, self.port
            )
            self.state.available = True
            await self._seed_state()
            self._notify()
        except OSError:
            pass  # loop will retry

    # ----------------------------------------------------------------- #
    # Frame dispatch
    # ----------------------------------------------------------------- #
    def _handle_frame(self, frame: P.Frame) -> None:
        for cb in list(self._raw_listeners):
            try:
                cb(frame)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("raw listener raised")

        changed = True
        code = frame.code
        s = self.state
        try:
            if code == P.Resp.STATUS:
                for k, v in models.parse_status(frame.params).items():
                    setattr(s, k, v)
            elif code == P.Resp.PRODUCT_INFO:
                for k, v in models.parse_product_info(frame.params).items():
                    setattr(s, k, v)
            elif code == P.Resp.INPUT_SETUP:
                setup = models.parse_input_setup(frame.params)
                s.inputs[setup.input_id] = setup
            elif code == P.Resp.EXPANDED_INFO:
                s.serial = self._parse_serial(frame.params)
            elif code == P.Resp.SONG_NAME:
                tag, text = models.parse_media_text(frame.params)
                s.media.source_tag, s.media.title = tag, text
            elif code == P.Resp.ARTIST_NAME:
                _, s.media.artist = models.parse_media_text(frame.params)
            elif code == P.Resp.ALBUM_NAME:
                _, s.media.album = models.parse_media_text(frame.params)
            elif code == P.Resp.GENRE_NAME:
                _, s.media.genre = models.parse_media_text(frame.params)
            elif code == P.Resp.ALBUM_ART_URL:
                _, s.media.image_url = models.parse_media_text(frame.params)
            elif code == P.Resp.TOTAL_TRACK_TIME:
                _, text = models.parse_media_text(frame.params)
                s.media.duration_s = models.parse_track_time(text)
            elif code == P.Resp.TRACK_PLAYING_TIME:
                _, text = models.parse_media_text(frame.params)
                s.media.position_s = models.parse_track_time(text)
            elif code == P.Resp.ERROR:
                try:
                    b = frame.param_bytes
                except Exception:  # noqa: BLE001 -- non-hex params (e.g. desync)
                    b = []
                if len(b) >= 2:
                    _LOGGER.warning("unit error: %s", MoonCommandError(b[0], b[1]))
                changed = False
            elif code in (P.Resp.ACK, P.Resp.WAKEUP):
                changed = False
            else:
                changed = False
        except Exception:  # noqa: BLE001 -- a bad frame must not kill the reader
            _LOGGER.exception("failed to handle %r", frame)
            changed = False

        if changed:
            self._notify()

    @staticmethod
    def _parse_serial(params: bytes) -> str | None:
        """Best-effort serial extraction from an FE payload (subsystem 00)."""
        try:
            text = params.decode("ascii", errors="replace")
        except Exception:  # noqa: BLE001
            return None
        return text or None

    # ----------------------------------------------------------------- #
    # Low-level send
    # ----------------------------------------------------------------- #
    async def send(self, frame: bytes) -> None:
        async with self._write_lock:
            if self._writer is None:
                raise MoonConnectionError("not connected")
            self._writer.write(frame)
            await self._writer.drain()

    # ----------------------------------------------------------------- #
    # High-level commands
    # ----------------------------------------------------------------- #
    async def get_status(self) -> None:
        await self.send(P.build_command(P.Cmd.GET_STATUS))

    async def set_power(self, on: bool) -> None:
        await self.send(
            P.build_command(P.Cmd.SET_POWER, P.Power.ON if on else P.Power.STANDBY)
        )

    async def set_mute(self, on: bool) -> None:
        await self.send(
            P.build_command(P.Cmd.SET_MUTE, P.OnOff.ON if on else P.OnOff.OFF)
        )

    async def toggle_mute(self) -> None:
        await self.send(P.build_command(P.Cmd.SET_MUTE, P.OnOff.TOGGLE))

    async def set_volume_level(self, level: float) -> None:
        raw = P.level_to_raw(level)
        msb, lsb = P.encode_volume_raw(raw)
        await self.send(P.build_command(P.Cmd.SET_VOLUME, P.VolumeAction.SET, msb, lsb))

    async def set_volume_raw(self, raw: int) -> None:
        msb, lsb = P.encode_volume_raw(raw)
        await self.send(P.build_command(P.Cmd.SET_VOLUME, P.VolumeAction.SET, msb, lsb))

    async def volume_up(self, *, full_db: bool = False) -> None:
        action = P.VolumeAction.UP_ONE if full_db else P.VolumeAction.UP_HALF
        await self.send(P.build_command(P.Cmd.SET_VOLUME, action, 0x00, 0x00))

    async def volume_down(self, *, full_db: bool = False) -> None:
        action = P.VolumeAction.DOWN_ONE if full_db else P.VolumeAction.DOWN_HALF
        await self.send(P.build_command(P.Cmd.SET_VOLUME, action, 0x00, 0x00))

    async def select_input(self, name: str) -> None:
        """Select an input by canonical Scheme-A name (e.g. 'BALANCED')."""
        await self.send(P.build_command(P.Cmd.SET_INPUT, P.select_input_id(name)))

    async def select_input_by_id(self, input_id: int) -> None:
        """Select an input by its id (as reported by A3/A7).

        Single scheme: 0x63 takes the same ids as A3/A7 report (HARDWARE FINDING
        2026-06-21 -- no BALANCED/ANALOG swap). This is HA's select_source path.
        """
        await self.send(P.build_command(P.Cmd.SET_INPUT, input_id))

    async def play(self) -> None:
        await self.send(P.build_command(P.Cmd.PLAY))

    async def stop(self) -> None:
        await self.send(P.build_command(P.Cmd.STOP))

    async def pause(self) -> None:
        await self.send(P.build_command(P.Cmd.PAUSE))

    async def next_track(self) -> None:
        await self.send(P.build_command(P.Cmd.NEXT))

    async def previous_track(self) -> None:
        await self.send(P.build_command(P.Cmd.PREVIOUS))
