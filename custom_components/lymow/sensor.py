"""Sensors for Lymow."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .lymow_api.models import Channel, LymowData, RobotStatus, ZoneStatus

from .const import DOMAIN
from .coordinator import LymowCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            LymowBatterySensor(coordinator, entry),
            LymowStatusSensor(coordinator, entry),
            LymowErrorSensor(coordinator, entry),
            LymowMapSensor(coordinator, entry),
        ]
    )


class _LymowSensorBase(CoordinatorEntity[LymowCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: LymowCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})


class LymowBatterySensor(_LymowSensorBase):
    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: LymowCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "battery")

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.state.battery if self.coordinator.data else None


class LymowStatusSensor(_LymowSensorBase):
    _attr_name = "Status"

    def __init__(self, coordinator: LymowCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "status")

    @property
    def native_value(self) -> str:
        if not self.coordinator.data:
            return "unknown"
        return RobotStatus.name(self.coordinator.data.state.robot_status)


class LymowErrorSensor(_LymowSensorBase):
    _attr_name = "Error"

    def __init__(self, coordinator: LymowCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "error")

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data or not self.coordinator.data.state.error_codes:
            return None
        return ", ".join(str(c) for c in self.coordinator.data.state.error_codes)


class LymowMapSensor(_LymowSensorBase):
    _attr_name = "Map"

    def __init__(self, coordinator: LymowCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "map")

    @property
    def native_value(self) -> str:
        if not self.coordinator.data or not self.coordinator.data.zones:
            return "unavailable"
        return "ok"

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {
                "zones": [],
                "channels": [],
                "mower_position": None,
                "robot_status": "unknown",
                "is_mowing": False,
            }
        data = self.coordinator.data
        zones_payload = [
            {
                "hash_id": z.hash_id,
                "name": z.name,
                "zone_type": z.zone_type,
                "polygon": [list(p) for p in z.polygon],
                "status": data.zone_statuses.get(z.hash_id, ZoneStatus.UNKNOWN),
            }
            for z in data.zones
        ]
        channels_payload = [
            {"points": [list(p) for p in ch.points]}
            for ch in data.channels
        ]
        pos = data.state.position
        return {
            "zones": zones_payload,
            "channels": channels_payload,
            "mower_position": list(pos) if pos else None,
            "mower_heading": data.state.heading,
            "robot_status": data.state.status_name,
            "is_mowing": data.state.is_mowing,
        }
