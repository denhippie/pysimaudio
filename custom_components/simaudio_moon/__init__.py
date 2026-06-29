"""The Simaudio MOON Neo 390 integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .moon390 import Moon390, MoonConnectionError

# The media_player platform is wired up in the next step (no entities yet).


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
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    client: Moon390 = entry.runtime_data
    await client.disconnect()
    return True
