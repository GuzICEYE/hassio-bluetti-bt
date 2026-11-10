import asyncio
import unittest
from unittest import mock

from bluetti_bt_connect_lib import (
    DeviceReader,
    DeviceReaderConfig,
    WriteOutcome,
    build_device,
)
from bluetti_bt_connect_lib.base_devices import BluettiDevice
from bluetti_bt_connect_lib.bluetooth import device_reader, encryption
from bluetti_bt_connect_lib.fields import FieldName, SwitchField, UIntField
from bluetti_bt_connect_lib.utils.bleak_client_mock import (
    ClientMockEncrypted as EncryptedClientMock,
    ClientMockNoEncryption,
    kex_frame as kex,
)

MAC = "00:11:00:11:00:11"


def _device() -> BluettiDevice:
    return BluettiDevice(
        fields=[
            UIntField(FieldName.AC_P1_POWER, 100),
            UIntField(FieldName.AC_P2_POWER, 104),
            UIntField(FieldName.AC_P3_POWER, 160),
        ],
        max_register_gap=8,
    )


def _populate(client) -> None:
    client.add_r_int(100, 11)
    client.add_r_int(104, 22)
    client.add_r_int(160, 33)


class TestEncryptedRead(unittest.IsolatedAsyncioTestCase):
    def _reader(self, client, use_encryption):
        return DeviceReader(
            MAC,
            _device(),
            asyncio.Future,
            DeviceReaderConfig(timeout=10, use_encryption=use_encryption),
            asyncio.Lock(),
            ble_client=client,
        )

    def _assert_values(self, data):
        self.assertIsNotNone(data)
        self.assertEqual(data.get(FieldName.AC_P1_POWER.value), 11)
        self.assertEqual(data.get(FieldName.AC_P2_POWER.value), 22)
        self.assertEqual(data.get(FieldName.AC_P3_POWER.value), 33)

    async def test_read_through_the_encrypted_channel(self):
        client = EncryptedClientMock()
        _populate(client)

        with mock.patch.object(encryption, "PUBLIC_KEY_K2", client.signing_pubkey_hex):
            reader = self._reader(client, True)
            data = await reader.read()

        self._assert_values(data)
        self.assertTrue(reader.encryption_detected)
        # Two merged reads, and the device saw them in the clear.
        self.assertEqual(len(client.plain_requests), 2)

    async def test_every_connection_runs_a_fresh_key_exchange(self):
        client = EncryptedClientMock()
        _populate(client)

        with mock.patch.object(encryption, "PUBLIC_KEY_K2", client.signing_pubkey_hex):
            reader = self._reader(client, True)
            self._assert_values(await reader.read())
            first_key = client.secure_key
            self._assert_values(await reader.read())

        self.assertNotEqual(first_key, client.secure_key)

    async def test_repeated_key_exchange_steps_are_answered_once(self):
        # Seen on an Apex 300: the public key arrives three times. Answering
        # each copy leaves the two sides with different keys.
        client = EncryptedClientMock(repeats=3)
        _populate(client)

        with mock.patch.object(encryption, "PUBLIC_KEY_K2", client.signing_pubkey_hex):
            data = await self._reader(client, True).read()

        self._assert_values(data)
        self.assertEqual(len(client.plain_requests), 2)

    async def test_unfragmented_replies(self):
        client = EncryptedClientMock(fragment=512)
        _populate(client)

        with mock.patch.object(encryption, "PUBLIC_KEY_K2", client.signing_pubkey_hex):
            data = await self._reader(client, True).read()

        self._assert_values(data)

    async def test_auto_mode_detects_an_encrypted_device(self):
        client = EncryptedClientMock()
        _populate(client)

        with mock.patch.object(encryption, "PUBLIC_KEY_K2", client.signing_pubkey_hex):
            reader = self._reader(client, None)
            data = await reader.read()

        self._assert_values(data)
        self.assertTrue(reader.encryption_detected)

    async def test_auto_mode_detects_a_plain_device(self):
        client = ClientMockNoEncryption()
        _populate(client)

        with mock.patch.object(device_reader, "ENCRYPTION_DETECT_TIMEOUT", 0.1):
            reader = self._reader(client, None)
            data = await reader.read()

        self._assert_values(data)
        self.assertFalse(reader.encryption_detected)

    async def test_wrong_device_signature_is_refused(self):
        # A peer that cannot prove its key was signed by BLUETTI's must not
        # be talked to.
        client = EncryptedClientMock(corrupt_signature=True)
        _populate(client)

        with mock.patch.object(
            encryption, "PUBLIC_KEY_K2", client.signing_pubkey_hex
        ), mock.patch.object(device_reader, "ENCRYPTION_HANDSHAKE_TIMEOUT", 0.3):
            reader = self._reader(client, True)
            data = await reader.read()

        self.assertIsNone(data)
        self.assertEqual(client.plain_requests, [])
        self.assertFalse(reader.encryption.is_ready_for_commands)


class TestEncryptedWrite(unittest.IsolatedAsyncioTestCase):
    """Writes on an encrypted device, against the simulated device only."""

    def _reader(self, client, bluetti_device=None):
        return DeviceReader(
            MAC,
            bluetti_device
            or BluettiDevice(fields=[SwitchField(FieldName.CTRL_AC, 2011)]),
            asyncio.Future,
            DeviceReaderConfig(timeout=10, use_encryption=True),
            asyncio.Lock(),
            ble_client=client,
        )

    async def test_write_goes_through_the_encrypted_channel(self):
        client = EncryptedClientMock()

        with mock.patch.object(encryption, "PUBLIC_KEY_K2", client.signing_pubkey_hex):
            result = await self._reader(client).write(FieldName.CTRL_AC.value, True)

        self.assertIs(result.outcome, WriteOutcome.ACCEPTED)
        self.assertEqual(result.echoed, 1)
        # What the device saw once it had decrypted the request.
        self.assertEqual(client.writes, [(1, 2011, 1)])

    async def test_refused_write_reports_the_exception(self):
        client = EncryptedClientMock(refuse={2011: 0x04})

        with mock.patch.object(encryption, "PUBLIC_KEY_K2", client.signing_pubkey_hex):
            result = await self._reader(client).write(FieldName.CTRL_AC.value, True)

        self.assertIs(result.outcome, WriteOutcome.REFUSED)
        self.assertEqual(result.exception_code, 0x04)

    async def test_nothing_is_written_when_the_key_exchange_fails(self):
        # Without keys the only thing that could be sent is the plain frame,
        # which an encrypted device must never be sent.
        client = EncryptedClientMock(corrupt_signature=True)

        with mock.patch.object(
            encryption, "PUBLIC_KEY_K2", client.signing_pubkey_hex
        ), mock.patch.object(device_reader, "ENCRYPTION_HANDSHAKE_TIMEOUT", 0.3):
            result = await self._reader(client).write(FieldName.CTRL_AC.value, True)

        self.assertIs(result.outcome, WriteOutcome.FAILED)
        self.assertEqual(client.writes, [])
        self.assertEqual(client.plain_requests, [])

    async def test_apex_300_switches_write_to_slave_0(self):
        client = EncryptedClientMock()
        apex = build_device("AP3002300000000000")

        with mock.patch.object(encryption, "PUBLIC_KEY_K2", client.signing_pubkey_hex):
            reader = self._reader(client, apex)
            result = await reader.write(FieldName.CTRL_DC.value, True)
            data = await reader.read()

        self.assertIs(result.outcome, WriteOutcome.ACCEPTED)
        self.assertEqual(client.writes, [(0, 2012, 1)])
        # The switch and the read-only state share the register.
        self.assertIs(data.get(FieldName.CTRL_DC.value), True)
        self.assertIs(data.get(FieldName.DC_OUTPUT_ON.value), True)
        self.assertIs(data.get(FieldName.CTRL_AC.value), False)


class TestKexFrameDetection(unittest.TestCase):
    def _reader(self):
        return DeviceReader(MAC, _device(), asyncio.Future, ble_client=object())

    def test_register_data_that_starts_with_the_magic_is_not_a_kex_frame(self):
        # 0x2a2a is 10794, a perfectly ordinary register value.
        self.assertFalse(self._reader()._is_kex_frame(b"\x2a\x2a\x00\x01\x00\x02\x12\x34"))

    def test_a_real_challenge_is(self):
        self.assertTrue(self._reader()._is_kex_frame(kex(b"\x01\x04\xde\xad\xbe\xef")))


if __name__ == "__main__":
    unittest.main()
