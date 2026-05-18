"""Lymow Robot Mower integration."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er

from .lymow_api import LymowClient

from .const import CONF_REGION, DEFAULT_REGION, DOMAIN
from .coordinator import LymowCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.LAWN_MOWER, Platform.SENSOR, Platform.SELECT, Platform.BUTTON]

_SERVICE_START_MOWING = "start_mowing"
_SERVICE_RESUME = "resume_mowing"
_SERVICE_CLEAR_ERROR = "clear_error"
_SERVICE_DOCK = "dock"
_SERVICE_CANCEL_TASK = "cancel_task"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = LymowClient(
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        region_key=entry.data.get(CONF_REGION, DEFAULT_REGION),
    )

    await client.authenticate()

    thing_name = entry.data.get("thing_name", "")
    if thing_name:
        await client.connect(thing_name)
    else:
        _LOGGER.warning("No thing_name in config entry; MQTT not connected")

    coordinator = LymowCoordinator(hass, client)
    client._state_listener = coordinator._on_device_state_update
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.services.has_service(DOMAIN, _SERVICE_START_MOWING):
        def _norm(s: str) -> str:
            return s.replace("\u2019", "'").replace("\u2018", "'").casefold()

        def _get_coordinators(call: ServiceCall) -> list[LymowCoordinator]:
            """Return coordinators targeted by the call's entity_id, or all if omitted."""
            entity_id = call.data.get("entity_id")
            if entity_id:
                entity = er.async_get(hass).async_get(entity_id)
                if entity and entity.config_entry_id in hass.data[DOMAIN]:
                    return [hass.data[DOMAIN][entity.config_entry_id]]
                _LOGGER.warning("entity_id %r not found or not a Lymow entity", entity_id)
                return []
            return list(hass.data[DOMAIN].values())

        _ENTITY_SCHEMA = vol.Schema({vol.Optional("entity_id"): str}, extra=vol.ALLOW_EXTRA)

        async def handle_start_mowing(call: ServiceCall) -> None:
            zone_names: list[str] = call.data["zones"]
            for coordinator in _get_coordinators(call):
                zones = await coordinator.client.get_zones()
                go_zones = [z for z in zones if z.zone_type == "go"]
                hash_ids = []
                for name in zone_names:
                    zone = next(
                        (z for z in go_zones if
                         _norm(z.name) == _norm(name) or z.hash_id == name),
                        None,
                    )
                    if zone:
                        hash_ids.append(zone.hash_id)
                    else:
                        _LOGGER.warning("start_mowing: zone %r not found, skipping", name)
                if hash_ids:
                    await coordinator.client.start_mowing_zones(hash_ids)

        hass.services.async_register(
            DOMAIN,
            _SERVICE_START_MOWING,
            handle_start_mowing,
            schema=_ENTITY_SCHEMA.extend({vol.Required("zones"): [str]}),
        )

        async def handle_resume(call: ServiceCall) -> None:
            for coordinator in _get_coordinators(call):
                await coordinator.client.resume_mowing()
                _LOGGER.debug("resume_mowing sent")

        hass.services.async_register(DOMAIN, _SERVICE_RESUME, handle_resume,
                                     schema=_ENTITY_SCHEMA)

        async def handle_clear_error(call: ServiceCall) -> None:
            for coordinator in _get_coordinators(call):
                await coordinator.client.clear_error()
                _LOGGER.debug("clear_error sent (PAUSE command)")

        hass.services.async_register(DOMAIN, _SERVICE_CLEAR_ERROR, handle_clear_error,
                                     schema=_ENTITY_SCHEMA)

        async def handle_dock(call: ServiceCall) -> None:
            for coordinator in _get_coordinators(call):
                await coordinator.client.return_to_dock()

        hass.services.async_register(DOMAIN, _SERVICE_DOCK, handle_dock,
                                     schema=_ENTITY_SCHEMA)

        async def handle_cancel_task(call: ServiceCall) -> None:
            for coordinator in _get_coordinators(call):
                await coordinator.client.cancel_task()

        hass.services.async_register(DOMAIN, _SERVICE_CANCEL_TASK, handle_cancel_task,
                                     schema=_ENTITY_SCHEMA)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: LymowCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.disconnect()
        if not hass.data[DOMAIN]:
            for svc in (_SERVICE_START_MOWING, _SERVICE_RESUME,
                        _SERVICE_CLEAR_ERROR, _SERVICE_DOCK, _SERVICE_CANCEL_TASK):
                hass.services.async_remove(DOMAIN, svc)
        return True
    return False
