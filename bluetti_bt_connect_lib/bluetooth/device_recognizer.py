import asyncio
import logging
from typing import Any, Callable, List

from ..base_devices import BluettiDevice, BaseDeviceV1, BaseDeviceV2
from ..bluetooth import DeviceConnection, DeviceReader, DeviceReaderConfig
from ..devices import DEVICE_NAME_RE
from ..fields import FieldName

_LOGGER = logging.getLogger(__name__)

RECOGNIZER_KEEP_ALIVE = 20
"""Seconds the link is held between the recognizer's reads, so they share
one connection - and, on an encrypted device, one key exchange."""


class DeviceRecognizerResult:
    def __init__(
        self,
        name: str,
        iot_version: int,
        sn: int | None = None,
        encrypted: bool = False,
    ):
        self.name = name
        self.iot_version = iot_version
        self.sn = sn
        self.encrypted = encrypted
        """Whether the device answered over the encrypted protocol."""
        self.full_name = name + str(sn)


async def recognize_device(
    mac: str,
    future_builder_method: Callable[[], asyncio.Future[Any]],
) -> DeviceRecognizerResult | None:
    # Since we don't know the type we use the base devices. The last one is
    # for 2nd-generation IoT devices (Apex 300), which answer at slave 1 with
    # every register zero and serve the real data at slave 0.
    bluetti_devices: List[BluettiDevice] = [
        BaseDeviceV2(),
        BaseDeviceV1(),
        BaseDeviceV2(slave=0),
    ]

    # One connection for every read: connecting dominates the time this
    # takes, and an encrypted device runs a key exchange per connection.
    connection = DeviceConnection(mac)
    device_reader = DeviceReader(
        mac,
        bluetti_devices[0],
        future_builder_method,
        # None finds out whether the device is encrypted.
        DeviceReaderConfig(
            timeout=30,
            use_encryption=None,
            keep_alive_seconds=RECOGNIZER_KEEP_ALIVE,
        ),
        asyncio.Lock(),
        connection=connection,
    )

    try:
        for bluetti_device in bluetti_devices:
            encrypted = bool(device_reader.encryption_detected)

            # Only V2 devices are encrypted. An encrypted one may not answer
            # a V1 request at all, which would cost a full response timeout.
            if encrypted and isinstance(bluetti_device, BaseDeviceV1):
                continue

            device_reader.bluetti_device = bluetti_device

            # We only need 6 registers to get the device type
            data = await device_reader.read(
                bluetti_device.get_device_type_registers(),
            )

            encrypted = bool(device_reader.encryption_detected)

            if data is None:
                continue

            type_data = data.get(FieldName.DEVICE_TYPE.value)

            if type_data is None:
                _LOGGER.error("No data in device type type_data")
                continue

            if not isinstance(type_data, str):
                _LOGGER.error("Invalid data in device type type_data")
                continue

            if type_data == "":
                continue

            if DEVICE_NAME_RE.match(type_data + "12345678") is None:
                _LOGGER.warning("Device has populated type_data with trash data")
                continue

            data = await device_reader.read(
                bluetti_device.get_device_sn_registers(),
            )

            if data is None:
                return DeviceRecognizerResult(
                    type_data,
                    bluetti_device.get_iot_version(),
                    "000000000000",
                    encrypted,
                )

            sn_data = data.get(FieldName.DEVICE_SN.value)

            if not isinstance(sn_data, int) or sn_data == "":
                return DeviceRecognizerResult(
                    type_data,
                    bluetti_device.get_iot_version(),
                    "000000000000",
                    encrypted,
                )

            return DeviceRecognizerResult(
                type_data,
                bluetti_device.get_iot_version(),
                sn_data,
                encrypted,
            )

        return None
    finally:
        await connection.disconnect()
