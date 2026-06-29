"""Config flow for the Simaudio MOON Neo 390 integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST

from .const import DOMAIN
from .moon390 import Moon390, MoonConnectionError

STEP_USER_DATA_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


class SimaudioMoonConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the Simaudio MOON Neo 390."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the host, then validate it by opening a connection.

        The unit reports no usable serial (HARDWARE FINDING 2026-06-29), so the
        host is the unique_id. A DHCP reservation for the unit is recommended.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            await self.async_set_unique_id(host)
            self._abort_if_unique_id_configured()
            client = Moon390(host)
            try:
                await client.connect()
            except MoonConnectionError:
                errors["base"] = "cannot_connect"
            else:
                await client.disconnect()
                return self.async_create_entry(
                    title="MOON Neo 390", data={CONF_HOST: host}
                )
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )
