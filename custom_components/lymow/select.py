"""Zone select entity for Lymow."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LymowCoordinator

_ALL_ZONES = "All zones"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LymowZoneSelect(coordinator, entry)])


class LymowZoneSelect(CoordinatorEntity[LymowCoordinator], SelectEntity):
    _attr_name = "Mow zone"
    _attr_has_entity_name = True
    _attr_current_option = _ALL_ZONES

    def __init__(self, coordinator: LymowCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_zone"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @property
    def _go_zones(self) -> list:
        if self.coordinator.data:
            return [z for z in self.coordinator.data.zones if z.zone_type == "go"]
        return []

    @property
    def options(self) -> list[str]:
        names = [z.name or z.hash_id for z in self._go_zones]
        return [_ALL_ZONES] + names

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        if option == _ALL_ZONES:
            zone_id = None
        else:
            zone = next((z for z in self._go_zones if (z.name or z.hash_id) == option), None)
            zone_id = zone.hash_id if zone else None
        await self.coordinator.client.start_mowing(zone_hash_id=zone_id)
        await self.coordinator.async_request_refresh()
