"""Media player platform for the Simaudio MOON Neo 390."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .moon390 import Moon390, MoonState

_SUPPORTED = (
    MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.SELECT_SOURCE
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
)

# Seconds without a B5 position push (while media is loaded) before we treat the
# unit as paused. The protocol has no explicit pause flag -- only the ~1 Hz B5
# pushes, which stop while paused. ~3s tolerates a couple of missed pushes.
_PAUSE_TIMEOUT = 3.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the MOON Neo 390 media player from a config entry."""
    client: Moon390 = entry.runtime_data
    async_add_entities([MoonMediaPlayer(client, entry)])


class MoonMediaPlayer(MediaPlayerEntity):
    """A Simaudio MOON Neo 390 exposed as a media player."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_supported_features = _SUPPORTED

    def __init__(self, client: Moon390, entry: ConfigEntry) -> None:
        """Initialise the entity for one unit."""
        self._client = client
        self._attr_unique_id = entry.unique_id or entry.data[CONF_HOST]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            manufacturer="Simaudio",
            model="MOON Neo 390",
            name="MOON Neo 390",
        )
        # The unit pushes B5 position ~1/s; remember when it last changed so HA
        # can interpolate the progress bar between pushes.
        self._position_updated_at: datetime | None = None
        self._last_position: int | None = None
        # Pause is inferred from B5 pushes ceasing (no explicit protocol flag).
        self._paused = False
        self._pause_timer: CALLBACK_TYPE | None = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to the client's push updates."""
        self.async_on_remove(self._client.add_listener(self._handle_update))
        self.async_on_remove(self._cancel_pause_timer)

    @callback
    def _handle_update(self, state: MoonState) -> None:
        """Reflect a pushed state change in HA, tracking play/pause via B5."""
        position = state.media.position_s
        if position != self._last_position:
            self._last_position = position
            if position is not None:
                # Position advanced -> playing; (re)arm the pause watchdog.
                self._position_updated_at = dt_util.utcnow()
                self._paused = False
                self._arm_pause_timer()
            else:
                self._position_updated_at = None
        if not (state.powered and state.media.title):
            # No media loaded (or powered off): nothing to pause-track.
            self._cancel_pause_timer()
            self._paused = False
        self.async_write_ha_state()

    def _arm_pause_timer(self) -> None:
        """(Re)start the watchdog that flips to PAUSED when B5 pushes stop."""
        self._cancel_pause_timer()
        self._pause_timer = async_call_later(
            self.hass, _PAUSE_TIMEOUT, self._pause_timeout
        )

    @callback
    def _cancel_pause_timer(self) -> None:
        if self._pause_timer is not None:
            self._pause_timer()
            self._pause_timer = None

    @callback
    def _pause_timeout(self, _now: datetime) -> None:
        """No B5 for _PAUSE_TIMEOUT while media is loaded -> treat as paused."""
        self._pause_timer = None
        if self._state.powered and self._state.media.title:
            self._paused = True
            self.async_write_ha_state()

    @property
    def _state(self) -> MoonState:
        return self._client.state

    @property
    def available(self) -> bool:
        """Whether the TCP connection to the unit is up."""
        return self._state.available

    @property
    def state(self) -> MediaPlayerState:
        """OFF in standby; IDLE with no media; PAUSED when B5 stops; else PLAYING.

        The protocol has no explicit play/pause flag, so PAUSED is inferred from
        the ~1 Hz B5 position pushes ceasing (see _pause_timeout).
        """
        if not self._state.powered:
            return MediaPlayerState.OFF
        if not self._state.media.title:
            return MediaPlayerState.IDLE
        if self._paused:
            return MediaPlayerState.PAUSED
        return MediaPlayerState.PLAYING

    @property
    def volume_level(self) -> float | None:
        return self._state.volume_level

    @property
    def is_volume_muted(self) -> bool:
        return self._state.muted

    @property
    def source(self) -> str | None:
        return self._state.input_name

    @property
    def source_list(self) -> list[str]:
        return self._state.source_list()

    @property
    def media_content_type(self) -> MediaType | None:
        return MediaType.MUSIC if self._state.media.title else None

    @property
    def media_title(self) -> str | None:
        return self._state.media.title

    @property
    def media_artist(self) -> str | None:
        return self._state.media.artist

    @property
    def media_album_name(self) -> str | None:
        return self._state.media.album

    @property
    def media_image_url(self) -> str | None:
        return self._state.media.image_url

    @property
    def media_image_remotely_accessible(self) -> bool:
        # The art URL is served by the unit itself on the LAN.
        return True

    @property
    def media_duration(self) -> int | None:
        # The unit leaves a stale duration after stop, so clear it when idle.
        if not self._state.media.title:
            return None
        return self._state.media.duration_s

    @property
    def media_position(self) -> int | None:
        if not self._state.media.title:
            return None
        return self._state.media.position_s

    @property
    def media_position_updated_at(self) -> datetime | None:
        if not self._state.media.title:
            return None
        return self._position_updated_at

    async def async_turn_on(self) -> None:
        await self._client.set_power(True)

    async def async_turn_off(self) -> None:
        await self._client.set_power(False)

    async def async_set_volume_level(self, volume: float) -> None:
        await self._client.set_volume_level(volume)

    async def async_volume_up(self) -> None:
        await self._client.volume_up()

    async def async_volume_down(self) -> None:
        await self._client.volume_down()

    async def async_mute_volume(self, mute: bool) -> None:
        await self._client.set_mute(mute)

    async def async_select_source(self, source: str) -> None:
        await self._client.select_input(source)

    async def async_media_play(self) -> None:
        await self._client.play()

    async def async_media_pause(self) -> None:
        await self._client.pause()

    async def async_media_stop(self) -> None:
        await self._client.stop()

    async def async_media_next_track(self) -> None:
        await self._client.next_track()

    async def async_media_previous_track(self) -> None:
        await self._client.previous_track()
