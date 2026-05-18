"""Button entities for Lymow."""
from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LymowCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        LymowClearErrorButton(coordinator, entry),
        LymowResumeButton(coordinator, entry),
    ])


class LymowClearErrorButton(CoordinatorEntity[LymowCoordinator], ButtonEntity):
    _attr_name = "Clear error"
    _attr_has_entity_name = True
    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(self, coordinator: LymowCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_clear_error"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_press(self) -> None:
        await self.coordinator.client.clear_error()
        await self.coordinator.async_request_refresh()


class LymowResumeButton(CoordinatorEntity[LymowCoordinator], ButtonEntity):
    _attr_name = "Resume mowing"
    _attr_has_entity_name = True

    def __init__(self, coordinator: LymowCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_resume"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_press(self) -> None:
        await self.coordinator.client.resume_mowing()
        await self.coordinator.async_request_refresh()
