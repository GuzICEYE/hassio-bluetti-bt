import asyncio
import hashlib
import os
import struct
import unittest
from unittest import mock

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

import crcmod.predefined

from bluetti_bt_connect_lib import (
    DeviceReader,
    DeviceReaderConfig,
    WriteOutcome,
    build_device,
)
from bluetti_bt_connect_lib.base_devices import BluettiDevice
from bluetti_bt_connect_lib.bluetooth import device_reader, encryption
from bluetti_bt_connect_lib.bluetooth.encryption import (
    KEX_MAGIC,
    LOCAL_AES_KEY,
    PRIVATE_KEY_L1,
    BluettiEncryption,
    der_to_raw_ecdsa,
    hexsum,
    hexxor,
    pubkey_from_bytes,
    pubkey_to_bytes,
    raw_ecdsa_to_der,
)
from bluetti_bt_connect_lib.fields import FieldName, SwitchField, UIntField
from bluetti_bt_connect_lib.utils.bleak_client_mock import ClientMockNoEncryption

MAC = "00:11:00:11:00:11"

modbus_crc = crcmod.predefined.mkCrcFun("modbus")


def kex(body: bytes) -> bytes:
    return KEX_MAGIC + body + hexsum(body, 2)


class EncryptedClientMock(ClientMockNoEncryption):
    """The device side of BLUETTI's encrypted protocol.

    Runs the same key exchange a real encrypted power station does, then
    serves registers through the encrypted channel. Replies are delivered in
    BLE-sized fragments, the way they arrive from hardware.

    The real devices sign their public key with a key only BLUETTI holds, so
    the mock signs with its own and the tests swap the matching public key in.
    """

    def __init__(
        self,
        fragment: int = 20,
        corrupt_signature: bool = False,
        repeats: int = 1,
        refuse: dict | None = None,
    ):
        super().__init__()
        self.refuse = refuse or {}
        """Register address -> Modbus exception code to refuse a write with."""
        self.writes = []
        """Writes the device saw after decryption: (slave, address, value)."""
        self.fragment = fragment
        self.corrupt_signature = corrupt_signature
        self.repeats = repeats
        """How many times each key-exchange step is sent. Real devices
        repeat a step when the answer is slow to arrive."""
        self.signing_key = ec.generate_private_key(ec.SECP256R1())
        self.crypto = BluettiEncryption()
        self.private = None
        self.secure_key = None
        self.plain_requests = []
        self.state = "idle"

    @property
    def signing_pubkey_hex(self) -> str:
        return (
            self.signing_key.public_key()
            .public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .hex()
        )

    async def _notify(self, data: bytes) -> None:
        for i in range(0, len(data), self.fragment):
            await self._callback(0, bytearray(data[i : i + self.fragment]))

    async def start_notify(self, char_specifier, callback, **kwargs) -> None:
        self._callback = callback
        self.state = "challenge"
        self.seed = os.urandom(4)
        self.iv = hashlib.md5(self.seed[::-1]).digest()
        self.key = hexxor(self.iv, bytes.fromhex(LOCAL_AES_KEY))
        # Sent from a task, as a real device sends it after the subscribe
        # has returned.
        async def challenge():
            for _ in range(self.repeats):
                await self._callback(0, bytearray(kex(b"\x01\x04" + self.seed)))

        asyncio.ensure_future(challenge())

    async def write_gatt_char(self, char_specifier, data, response=None) -> None:
        data = bytes(data)

        if self.state == "challenge":
            assert data == kex(b"\x02\x04" + self.iv[8:12]), "bad challenge reply"
            await self._callback(0, bytearray(kex(b"\x03\x01\x00")))

            self.private = ec.generate_private_key(ec.SECP256R1())
            pub = pubkey_to_bytes(self.private.public_key())
            signature = der_to_raw_ecdsa(
                self.signing_key.sign(pub + self.iv, ec.ECDSA(hashes.SHA256()))
            )
            if self.corrupt_signature:
                signature = bytes(64)
            message = kex(b"\x04\x80" + pub + signature)
            self.state = "pubkey"
            for _ in range(self.repeats):
                await self._notify(self.crypto.aes_encrypt(message, self.key, self.iv))
            return

        if self.state == "pubkey":
            message = self.crypto.aes_decrypt(data, self.key, self.iv)
            assert message[:4] == KEX_MAGIC + b"\x05\x80", "bad pubkey message"
            peer_pub, signature = message[4:68], message[68:132]

            local = ec.derive_private_key(
                int.from_bytes(bytes.fromhex(PRIVATE_KEY_L1), "big"), ec.SECP256R1()
            )
            local.public_key().verify(
                raw_ecdsa_to_der(signature),
                peer_pub + self.iv,
                ec.ECDSA(hashes.SHA256()),
            )

            self.secure_key = self.private.exchange(
                ec.ECDH(), pubkey_from_bytes(peer_pub)
            )
            self.state = "secure"
            await self._notify(
                self.crypto.aes_encrypt(kex(b"\x06\x01\x00"), self.key, self.iv)
            )
            return

        assert self.state == "secure", "request before the key exchange finished"
        request = self.crypto.aes_decrypt(data, self.secure_key, None)
        self.plain_requests.append(request)

        if request[1] == 6:
            reply = self._write_register(request)
        else:
            cmd = struct.unpack_from("!HHHH", request)
            reply = bytes(await self._get_register(cmd[1], cmd[2]))

        await self._notify(self.crypto.aes_encrypt(reply, self.secure_key, None))

    def _write_register(self, request: bytes) -> bytes:
        """Answer a single-register write the way a device does."""
        slave = request[0]
        address, value = struct.unpack_from("!HH", request, 2)
        self.writes.append((slave, address, value))

        if address in self.refuse:
            frame = bytearray(5)
            frame[0] = slave
            frame[1] = 0x86
            frame[2] = self.refuse[address]
            struct.pack_into("<H", frame, -2, modbus_crc(frame[:-2]))
            return bytes(frame)

        # Function 6 echoes the request back verbatim.
        self.add_r_int(address, value)
        return request


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
