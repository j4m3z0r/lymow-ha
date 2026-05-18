"""Config flow for Lymow integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .const import CONF_REGION, DEFAULT_REGION, DOMAIN

REGION_OPTIONS = ["ap", "eu", "us", "cn"]

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_REGION, default=DEFAULT_REGION): vol.In(REGION_OPTIONS),
    }
)


async def _try_authenticate(username: str, password: str, region_key: str):
    """Return (client, devices) on success, raise on failure."""
    from .lymow_api import LymowClient
    client = LymowClient(username=username, password=password, region_key=region_key)
    await client.authenticate()
    devices = await client.get_devices()
    return client, devices


class LymowConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            from .lymow_api import LymowAuthError

            preferred_region = user_input[CONF_REGION]
            # Try preferred region first; fall back to all others if user not found there
            regions_to_try = [preferred_region] + [r for r in REGION_OPTIONS if r != preferred_region]

            client = None
            devices = None
            found_region = None

            for region_key in regions_to_try:
                try:
                    client, devices = await _try_authenticate(
                        user_input[CONF_USERNAME], user_input[CONF_PASSWORD], region_key
                    )
                    found_region = region_key
                    break
                except LymowAuthError:
                    continue
                except Exception:
                    errors["base"] = "cannot_connect"
                    break

            if found_region is None and "base" not in errors:
                errors["base"] = "invalid_auth"

            if not errors:
                if not devices:
                    errors["base"] = "cannot_connect"
                else:
                    device = devices[0]
                    thing_name = device.get("deviceThingName", "")
                    await self.async_set_unique_id(user_input[CONF_USERNAME])
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=device.get("deviceName") or user_input[CONF_USERNAME],
                        data={
                            CONF_USERNAME: user_input[CONF_USERNAME],
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                            CONF_REGION: found_region,
                            "thing_name": thing_name,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )
