"""Bluetti Bluetooth Integration"""

from __future__ import annotations
import asyncio
import re
import logging
from typing import List
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
import voluptuous as vol
from bluetti_bt_connect_lib import MAX_RAW_READ_COUNT

from .utils import mac_loggable
from .const import (
    DATA_COORDINATOR,
    DATA_LOCK,
    DOMAIN,
    MANUFACTURER,
)
from .types import FullDeviceConfig
from .coordinator import PollingCoordinator

PLATFORMS: List[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
]


SERVICE_READ_REGISTERS = "read_registers"

READ_REGISTERS_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required("address"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=0xFFFF)
        ),
        vol.Optional("count", default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_RAW_READ_COUNT)
        ),
        vol.Optional("slave"): vol.All(vol.Coerce(int), vol.Range(min=0, max=247)),
    }
)


async def async_read_registers(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Read a block of registers once and return the raw words.

    For exploring addresses the device definition does not cover. Strictly a
    read (Modbus function 3). It goes through the device's reader, so it
    shares the connection, the encrypted session and the lock with polling
    and never interleaves with it.
    """
    data = hass.data.get(DOMAIN, {}).get(call.data["entry_id"])
    coordinator = data.get(DATA_COORDINATOR) if data else None

    if coordinator is None or coordinator.reader is None:
        raise ServiceValidationError("No Bluetti BT device with that config entry id")

    slave = call.data.get("slave")

    if slave is None:
        # Where this device serves its data: 0 on an Apex 300, 1 elsewhere.
        slave = getattr(coordinator.reader.bluetti_device, "slave", 1)

    result = await coordinator.reader.read_raw(
        call.data["address"], call.data.get("count", 1), slave
    )

    return result.as_dict()


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_READ_REGISTERS):
        return

    async def read_registers(call: ServiceCall) -> ServiceResponse:
        return await async_read_registers(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_READ_REGISTERS,
        read_registers,
        schema=READ_REGISTERS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Bluetti Powerstation from a config entry."""

    config = FullDeviceConfig.from_dict(entry.data)

    if config is None:
        logging.getLogger(__name__).error(
            "Failed to parse config entry data for '%s'. "
            "This may happen when upgrading from v0.1.6 to v0.2.x "
            "due to missing 'use_encryption' field.",
            entry.title,
        )
        return False

    logger = logging.getLogger(
        f"{__name__}.{mac_loggable(config.address).replace(':', '_')}"
    )

    logger.debug("Init Bluetti BT Integration")

    if not bluetooth.async_address_present(hass, config.address):
        raise ConfigEntryNotReady("Bluetti device not present")

    # Create data structure
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(entry.entry_id, {})

    # Create lock
    lock = asyncio.Lock()

    # Create coordinator for polling
    logger.debug("Creating coordinator")
    coordinator = PollingCoordinator(
        hass,
        config,
        lock,
    )
    entry.async_on_unload(coordinator.async_disconnect)
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id].setdefault(DATA_COORDINATOR, coordinator)
    hass.data[DOMAIN][entry.entry_id].setdefault(DATA_LOCK, lock)

    _async_register_services(hass)

    logger.debug("Creating entities")
    # Setup platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    logger.debug("Setup done")

    return True


def device_info(entry: ConfigEntry):
    """Device info."""
    config = FullDeviceConfig.from_dict(entry.data)

    if config is None:
        return None

    return DeviceInfo(
        identifiers={(DOMAIN, config.address)},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model=config.dev_type,
    )


def get_unique_id(name: str, sensor_type: str | None = None):
    """Generate an unique id."""
    res = re.sub("[^A-Za-z0-9]+", "_", name).lower()
    if sensor_type is not None:
        return f"{sensor_type}.{res}"
    return res
