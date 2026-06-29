"""The Simaudio MOON Neo 390 integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .moon390 import Moon390, MoonConnectionError

PLATFORMS = [Platform.MEDIA_PLAYER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Simaudio MOON Neo 390 from a config entry."""
    client = Moon390(entry.data[CONF_HOST])
    try:
        await client.connect()
    except MoonConnectionError as err:
        raise ConfigEntryNotReady(
            f"Cannot connect to MOON Neo 390 at {entry.data[CONF_HOST]}"
        ) from err
    entry.runtime_data = client
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        client: Moon390 = entry.runtime_data
        await client.disconnect()
    return unload_ok
