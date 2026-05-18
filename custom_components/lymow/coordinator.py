"""DataUpdateCoordinator for Lymow."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .lymow_api import LymowApiError, LymowAuthError, LymowClient, LymowConnectionError
from .lymow_api.models import LymowData

from .const import DOMAIN, POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)


class LymowCoordinator(DataUpdateCoordinator[LymowData]):
    def __init__(self, hass: HomeAssistant, client: LymowClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL),
        )
        self.client = client

    def _on_device_state_update(self) -> None:
        """Called from MQTT thread when position updates arrive — schedules a refresh."""
        self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> LymowData:
        # Hourly map refresh (no-op if fetched recently); also detects dead connection.
        try:
            await self.client.request_map_refresh()
        except LymowConnectionError:
            try:
                await self.client.reconnect()
                self.client._state_listener = self._on_device_state_update
            except LymowConnectionError as exc:
                raise UpdateFailed(f"MQTT reconnect failed: {exc}") from exc
        except (LymowAuthError, LymowApiError) as exc:
            raise UpdateFailed(str(exc)) from exc

        try:
            return LymowData(
                state=await self.client.get_state(),
                zones=await self.client.get_zones(),
                channels=await self.client.get_channels(),
                zone_statuses={},
            )
        except (LymowAuthError, LymowApiError) as exc:
            raise UpdateFailed(str(exc)) from exc
