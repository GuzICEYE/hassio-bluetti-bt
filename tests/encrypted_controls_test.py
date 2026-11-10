"""Controls on encrypted devices.

Everything here runs against the library's simulated encrypted device. No
test in this file talks to hardware.
"""

import asyncio
import logging
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.getcwd())

try:
    import homeassistant  # noqa: F401
except ImportError:
    raise unittest.SkipTest("Home Assistant is not installed")

try:
    from homeassistant.components import bluetooth  # noqa: F401
except ImportError:
    # The bluetooth component pulls in requirements that a plain
    # `pip install homeassistant` does not provide. Nothing here uses it.
    _stub = types.ModuleType("homeassistant.components.bluetooth")
    _stub.BluetoothServiceInfoBleak = object
    _stub.async_address_present = lambda *args, **kwargs: True
    _stub.async_ble_device_from_address = lambda *args, **kwargs: None
    sys.modules["homeassistant.components.bluetooth"] = _stub
    import homeassistant.components

    homeassistant.components.bluetooth = _stub

from homeassistant.exceptions import HomeAssistantError

from bluetti_bt_connect_lib import DeviceReader, DeviceReaderConfig, build_device
from bluetti_bt_connect_lib.bluetooth import device_reader, encryption
from bluetti_bt_connect_lib.utils.bleak_client_mock import ClientMockEncrypted

from custom_components.bluetti_bt import binary_sensor, coordinator, select, switch
from custom_components.bluetti_bt.const import DATA_COORDINATOR, DATA_LOCK, DOMAIN
from custom_components.bluetti_bt.coordinator import PollingCoordinator
from custom_components.bluetti_bt.types import FullDeviceConfig, OptionalDeviceConfig
from custom_components.bluetti_bt.types.OptionalDeviceConfig import (
    CONF_ENCRYPTED_CONTROLS,
)

MAC = "00:11:00:11:00:11"
NAME = "AP3002300000000000"


def entry_data(encrypted: bool = True, controls: bool | None = None) -> dict:
    data = {
        "address": MAC,
        "name": NAME,
        "type": "AP300",
        "use_encryption": encrypted,
    }
    if controls is not None:
        data[CONF_ENCRYPTED_CONTROLS] = controls
    return data


def make_coordinator(client, held_connection: bool = True) -> PollingCoordinator:
    """A coordinator with a real reader over the simulated device.

    Built without Home Assistant's machinery: only the write path is under
    test, and it needs the reader, the connection marker and a refresh.
    """
    coord = object.__new__(PollingCoordinator)
    coord.logger = logging.getLogger("test")
    coord.connection = object() if held_connection else None
    coord.reader = DeviceReader(
        MAC,
        build_device(NAME),
        asyncio.Future,
        DeviceReaderConfig(timeout=10, use_encryption=True),
        asyncio.Lock(),
        ble_client=client,
    )
    coord.async_request_refresh = mock.AsyncMock()
    return coord


class FakeEntry:
    entry_id = "entry"
    title = NAME

    def __init__(self, data: dict):
        self.data = data


class FakeHass:
    def __init__(self, coord):
        self.data = {DOMAIN: {"entry": {DATA_COORDINATOR: coord, DATA_LOCK: asyncio.Lock()}}}


async def setup_platform(platform, data: dict, coord) -> list:
    added = []
    await platform.async_setup_entry(FakeHass(coord), FakeEntry(data), added.extend)
    return added


class TestControlsOption(unittest.TestCase):
    def test_encrypted_device_has_no_controls_by_default(self):
        self.assertFalse(FullDeviceConfig.from_dict(entry_data()).controls_enabled)

    def test_encrypted_device_gets_controls_when_opted_in(self):
        config = FullDeviceConfig.from_dict(entry_data(controls=True))
        self.assertTrue(config.controls_enabled)

    def test_plain_device_always_has_controls(self):
        config = FullDeviceConfig.from_dict(entry_data(encrypted=False))
        self.assertTrue(config.controls_enabled)

    def test_option_survives_a_round_trip(self):
        stored = OptionalDeviceConfig.from_dict({CONF_ENCRYPTED_CONTROLS: True}).as_dict
        self.assertTrue(OptionalDeviceConfig.from_dict(stored).encrypted_controls)

    def test_toggle_is_only_offered_for_an_encrypted_device(self):
        options = OptionalDeviceConfig.from_dict({})

        def keys(schema):
            return {str(key) for key in schema.schema}

        self.assertIn(CONF_ENCRYPTED_CONTROLS, keys(options.schema_for(True)))
        self.assertNotIn(CONF_ENCRYPTED_CONTROLS, keys(options.schema_for(False)))


class TestOptionsFlow(unittest.IsolatedAsyncioTestCase):
    SUBMIT = {"polling_interval": 20, "polling_timeout": 45, "max_retries": 5}

    def _flow(self, data: dict):
        from custom_components.bluetti_bt.config_flow import OptionsFlowHandler

        entry = FakeEntry(data)
        flow = OptionsFlowHandler()
        flow.handler = entry.entry_id
        flow.flow_id = "flow"
        flow.hass = types.SimpleNamespace(
            config_entries=types.SimpleNamespace(
                async_get_known_entry=lambda entry_id: entry,
                async_update_entry=lambda e, data: setattr(e, "data", data),
            )
        )
        return flow, entry

    async def test_form_offers_the_toggle_for_an_encrypted_device(self):
        flow, _ = self._flow(entry_data())
        result = await flow.async_step_init()
        keys = {str(key) for key in result["data_schema"].schema}
        self.assertIn(CONF_ENCRYPTED_CONTROLS, keys)

    async def test_form_hides_the_toggle_for_a_plain_device(self):
        flow, _ = self._flow(entry_data(encrypted=False))
        result = await flow.async_step_init()
        keys = {str(key) for key in result["data_schema"].schema}
        self.assertNotIn(CONF_ENCRYPTED_CONTROLS, keys)

    async def test_submitting_the_toggle_stores_it(self):
        flow, entry = self._flow(entry_data())
        await flow.async_step_init({**self.SUBMIT, CONF_ENCRYPTED_CONTROLS: True})
        self.assertIs(entry.data[CONF_ENCRYPTED_CONTROLS], True)
        self.assertTrue(FullDeviceConfig.from_dict(entry.data).controls_enabled)

    async def test_switching_it_off_again_stores_that(self):
        flow, entry = self._flow(entry_data(controls=True))
        await flow.async_step_init({**self.SUBMIT, CONF_ENCRYPTED_CONTROLS: False})
        self.assertIs(entry.data[CONF_ENCRYPTED_CONTROLS], False)


class TestEntities(unittest.IsolatedAsyncioTestCase):
    async def test_controls_off_gives_read_only_entities(self):
        coord = make_coordinator(ClientMockEncrypted())

        self.assertEqual(await setup_platform(switch, entry_data(), coord), [])
        self.assertEqual(await setup_platform(select, entry_data(), coord), [])

        sensors = await setup_platform(binary_sensor, entry_data(), coord)
        self.assertEqual(
            sorted(s._response_key for s in sensors),
            ["ac_output_on", "ctrl_ac", "ctrl_dc", "dc_output_on"],
        )

    async def test_controls_on_gives_switches(self):
        coord = make_coordinator(ClientMockEncrypted())
        data = entry_data(controls=True)

        switches = await setup_platform(switch, data, coord)
        self.assertEqual(sorted(s._response_key for s in switches), ["ctrl_ac", "ctrl_dc"])

        # The switch fields are no longer duplicated as binary sensors.
        sensors = await setup_platform(binary_sensor, data, coord)
        self.assertEqual(
            sorted(s._response_key for s in sensors), ["ac_output_on", "dc_output_on"]
        )


class TestEncryptedWrite(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = mock.patch.object(coordinator, "WRITE_SETTLE_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _switch(self, client, field: str):
        coord = make_coordinator(client)
        switches = await setup_platform(switch, entry_data(controls=True), coord)
        return coord, next(s for s in switches if s._response_key == field)

    async def test_switch_writes_through_the_encrypted_session(self):
        client = ClientMockEncrypted()
        coord, entity = await self._switch(client, "ctrl_ac")

        with mock.patch.object(encryption, "PUBLIC_KEY_K2", client.signing_pubkey_hex):
            await entity.async_turn_on()
            await entity.async_turn_off()

        # (slave, register, value) as the device saw them after decrypting.
        self.assertEqual(client.writes, [(0, 2011, 1), (0, 2011, 0)])
        self.assertEqual(coord.async_request_refresh.await_count, 2)

    async def test_refused_write_is_an_error_not_a_success(self):
        client = ClientMockEncrypted(refuse={2012: 0x04})
        coord, entity = await self._switch(client, "ctrl_dc")

        with mock.patch.object(encryption, "PUBLIC_KEY_K2", client.signing_pubkey_hex):
            with self.assertRaises(HomeAssistantError) as raised:
                await entity.async_turn_on()

        self.assertIn("0x04", str(raised.exception))
        coord.async_request_refresh.assert_not_awaited()

    async def test_nothing_is_sent_when_the_key_exchange_fails(self):
        client = ClientMockEncrypted(corrupt_signature=True)
        coord, entity = await self._switch(client, "ctrl_ac")

        with mock.patch.object(
            encryption, "PUBLIC_KEY_K2", client.signing_pubkey_hex
        ), mock.patch.object(device_reader, "ENCRYPTION_HANDSHAKE_TIMEOUT", 0.3):
            with self.assertRaises(HomeAssistantError):
                await entity.async_turn_on()

        self.assertEqual(client.writes, [])
        self.assertEqual(client.plain_requests, [])

    async def test_plain_devices_keep_their_own_write_path(self):
        coord = make_coordinator(ClientMockEncrypted(), held_connection=False)
        self.assertFalse(coord.writes_through_reader)


class TestReadRegistersService(unittest.IsolatedAsyncioTestCase):
    async def _call(self, client, **data):
        from custom_components.bluetti_bt import async_read_registers

        coord = make_coordinator(client)
        call = types.SimpleNamespace(data={"entry_id": "entry", **data})

        with mock.patch.object(encryption, "PUBLIC_KEY_K2", client.signing_pubkey_hex):
            return await async_read_registers(FakeHass(coord), call)

    async def test_reads_raw_words_from_the_devices_own_slave(self):
        client = ClientMockEncrypted()
        client.add_r_int(102, 93)

        result = await self._call(client, address=102, count=2)

        self.assertEqual(result["outcome"], "ok")
        # An Apex 300 serves its data at slave 0.
        self.assertEqual(result["slave"], 0)
        self.assertEqual([r["value"] for r in result["registers"]], [93, 0])
        # A read, and nothing but a read, reached the device.
        self.assertEqual([request[1] for request in client.plain_requests], [3])
        self.assertEqual(client.writes, [])

    async def test_slave_can_be_chosen(self):
        client = ClientMockEncrypted()
        result = await self._call(client, address=110, count=6, slave=2)
        self.assertEqual(result["slave"], 2)
        self.assertEqual(client.plain_requests[0][0], 2)

    async def test_unknown_entry_is_rejected(self):
        from homeassistant.exceptions import ServiceValidationError
        from custom_components.bluetti_bt import async_read_registers

        call = types.SimpleNamespace(data={"entry_id": "nope", "address": 1})

        with self.assertRaises(ServiceValidationError):
            await async_read_registers(FakeHass(None), call)


if __name__ == "__main__":
    unittest.main()
