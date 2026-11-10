"""Coordinator for Bluetti integration."""

from __future__ import annotations
import asyncio
from datetime import timedelta
import logging
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from bluetti_bt_connect_lib import (
    build_device,
    DeviceConnection,
    DeviceReader,
    DeviceReaderConfig,
)

from .utils import mac_loggable
from .types import FullDeviceConfig


ENCRYPTED_KEEP_ALIVE_MARGIN = 15
"""Seconds the link to an encrypted device is held beyond the polling
interval, so the next poll finds it still open."""


class PollingCoordinator(DataUpdateCoordinator):
    """Polling coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: FullDeviceConfig,
        lock: asyncio.Lock,
    ):
        """Initialize coordinator."""
        super().__init__(
            hass,
            logging.getLogger(
                f"{__name__}.{mac_loggable(config.address).replace(':', '_')}"
            ),
            name="Bluetti polling coordinator",
            update_interval=timedelta(seconds=config.polling_interval),
        )

        self.config = config
        self.reader = None
        self.connection = None

        # Create client
        self.logger.info("Creating client for %s", config.name)
        bluetti_device = build_device(config.name)

        if bluetti_device is None:
            self.logger.error("Device is unknown type: %s", config.name)
            return

        # An encrypted device runs a key exchange on every new connection, on
        # top of the connection setup itself. Holding the link between polls
        # pays for both once instead of on every poll. Plain devices keep the
        # connect-read-disconnect cycle, which leaves them free for the
        # BLUETTI app in between.
        keep_alive = 0

        if config.use_encryption:
            self.connection = DeviceConnection(
                config.address,
                device_provider=lambda: bluetooth.async_ble_device_from_address(
                    hass, config.address, connectable=True
                ),
            )
            keep_alive = config.polling_interval + ENCRYPTED_KEEP_ALIVE_MARGIN

        self.reader = DeviceReader(
            config.address,
            bluetti_device,
            self.hass.loop.create_future,
            DeviceReaderConfig(
                config.polling_timeout,
                config.use_encryption,
                keep_alive,
            ),
            lock,
            connection=self.connection,
        )

    async def async_disconnect(self) -> None:
        """Release a held connection, when the entry is unloaded."""
        if self.connection is not None:
            await self.connection.disconnect()

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """

        # Check if device is connected
        if (
            bluetooth.async_address_present(
                self.hass, self.config.address, connectable=True
            )
            is False
        ):
            self.logger.warning("Device not connected")
            self.last_update_success = False
            return None

        if self.reader is None:
            self.logger.error(
                "Reader not initialized - device type may be unsupported: %s",
                self.config.name,
            )
            self.last_update_success = False
            return None

        return await self.reader.read()
