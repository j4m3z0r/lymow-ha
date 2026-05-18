"""LawnMowerEntity for Lymow."""
from __future__ import annotations

from homeassistant.components.lawn_mower import (
    LawnMowerActivity,
    LawnMowerEntity,
    LawnMowerEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .lymow_api.models import RobotStatus

from .const import DOMAIN
from .coordinator import LymowCoordinator

_STATUS_TO_ACTIVITY = {
    RobotStatus.CLEANING: LawnMowerActivity.MOWING,
    RobotStatus.PAUSE: LawnMowerActivity.PAUSED,
    RobotStatus.PAUSE_DOCKING: LawnMowerActivity.PAUSED,
    RobotStatus.DOCKING: LawnMowerActivity.RETURNING,
    RobotStatus.CHARGING: LawnMowerActivity.DOCKED,
    RobotStatus.CHARGING_FULL: LawnMowerActivity.DOCKED,
    RobotStatus.WAITING: LawnMowerActivity.DOCKED,
    RobotStatus.ERROR: LawnMowerActivity.ERROR,
    RobotStatus.EMERGENCY_STOP: LawnMowerActivity.ERROR,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LymowMowerEntity(coordinator, entry)])


class LymowMowerEntity(CoordinatorEntity[LymowCoordinator], LawnMowerEntity):
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING
        | LawnMowerEntityFeature.PAUSE
        | LawnMowerEntityFeature.DOCK
    )
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator: LymowCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_mower"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Lymow Mower",
            manufacturer="Lymow",
        )

    @property
    def activity(self) -> LawnMowerActivity:
        if self.coordinator.data is None:
            return LawnMowerActivity.DOCKED
        status = self.coordinator.data.state.robot_status
        return _STATUS_TO_ACTIVITY.get(status, LawnMowerActivity.DOCKED)

    async def async_start_mowing(self) -> None:
        status = self.coordinator.data.state.robot_status if self.coordinator.data else None
        if status in {RobotStatus.ERROR, RobotStatus.EMERGENCY_STOP}:
            # Send PAUSE to acknowledge/clear the error; mower transitions to paused state.
            # User must press Start again (or use resume service) to actually begin mowing.
            await self.coordinator.client.clear_error()
        elif status in {RobotStatus.PAUSE, RobotStatus.PAUSE_DOCKING}:
            await self.coordinator.client.resume_mowing()
        else:
            await self.coordinator.client.start_mowing()
        await self.coordinator.async_request_refresh()

    async def async_pause(self) -> None:
        await self.coordinator.client.stop_mowing()
        await self.coordinator.async_request_refresh()

    async def async_dock(self) -> None:
        await self.coordinator.client.return_to_dock()
        await self.coordinator.async_request_refresh()
