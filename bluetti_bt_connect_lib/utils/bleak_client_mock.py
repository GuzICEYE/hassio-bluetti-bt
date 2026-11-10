"""Bleak Mock Client for unittests."""

import asyncio
from decimal import Decimal
import hashlib
import os
import struct
import sys
from typing import Awaitable, Callable, List, Union
import uuid
from bleak.backends.characteristic import BleakGATTCharacteristic
import crcmod
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

if sys.version_info < (3, 12):
    from typing_extensions import Buffer
else:
    from collections.abc import Buffer

from ..bluetooth.encryption import (
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

modbus_crc = crcmod.predefined.mkCrcFun("modbus")


def swap_bytes(data: bytes):
    """Swaps the place of every other byte, returning a new byte array"""
    arr = bytearray(data)
    for i in range(0, len(arr) - 1, 2):
        arr[i], arr[i + 1] = arr[i + 1], arr[i]
    return arr


def r_int(value: int):
    return struct.pack("!H", value)


def r_str(value: str, max_size: int):
    return struct.pack(f"!{max_size}s", value.encode("ascii"))


def r_sstr(value: str, max_size: int):
    return swap_bytes(r_str(value, max_size))


def r_sn(value: int):
    part4 = value & 0xFFFF
    part3 = (value >> 16) & 0xFFFF
    part2 = (value >> 32) & 0xFFFF
    part1 = (value >> 48) & 0xFFFF
    return struct.pack("!4H", part4, part3, part2, part1)


class BleakClientMock:
    """Mock a BLE Client."""

    def __init__(self, packs_max: int = 0):
        self._bytemap: bytearray = bytearray(0x20000)  # every 16-bit address
        self.packs: List[bytearray] = [bytearray() for _ in range(packs_max)]

    def add_r_int(self, register: int, value: int):
        real = register * 2
        self._bytemap[real : real + 2] = r_int(value)

    def add_r_str(self, register: int, value: str, max_size: int):
        real = register * 2
        self._bytemap[real : real + max_size * 2] = r_str(value, max_size)

    def add_r_sstr(self, register: int, value: str, max_size: int):
        real = register * 2
        self._bytemap[real : real + max_size * 2] = r_sstr(value, max_size)

    def add_r_sn(self, register: int, value: int):
        real = register * 2
        self._bytemap[real : real + 8] = r_sn(value)

    def add_pack(self, n: int, b: bytearray):
        self.packs[n] = b

    async def start_notify(
        self,
        char_specifier: Union[BleakGATTCharacteristic, int, str, uuid.UUID],
        callback: Callable[
            [BleakGATTCharacteristic, bytearray], Union[None, Awaitable[None]]
        ],
        **kwargs,
    ) -> None:
        self._callback = callback

    async def stop_notify(
        self,
        char_specifier: Union[BleakGATTCharacteristic, int, str, uuid.UUID],
    ) -> None:
        return

    async def disconnect(self) -> None:
        return

    async def write_gatt_char(
        self,
        char_specifier: Union[BleakGATTCharacteristic, int, str, uuid.UUID],
        data: Buffer,
        response: bool = None,
    ) -> None:
        cmd = struct.unpack_from("!HHHH", data)
        content = await self._get_register(cmd[1], cmd[2])
        await self._callback(char_specifier, content)

    async def _get_register(self, addr: int, size: int):
        data = self._bytemap[(addr * 2) : (addr * 2 + size * 2)]
        response = bytearray(len(data) + 4)
        response[0] = 0
        response[1] = 0
        response[2] = 0
        response[3:-2] = data
        struct.pack_into("<H", response, -2, modbus_crc(response[:-2]))
        return response


class ClientMockNoEncryption(BleakClientMock):
    """Mock for unencrypted devices"""


def kex_frame(body: bytes) -> bytes:
    return KEX_MAGIC + body + hexsum(body, 2)


class ClientMockEncrypted(ClientMockNoEncryption):
    """The device side of BLUETTI's encrypted protocol.

    Runs the same key exchange a real encrypted power station does, then
    serves registers through the encrypted channel. Replies are delivered in
    BLE-sized fragments, the way they arrive from hardware.

    The real devices sign their public key with a key only BLUETTI holds, so
    the mock signs with its own. A test has to swap the matching public key
    in for the exchange to be accepted:

        with mock.patch.object(encryption, "PUBLIC_KEY_K2", client.signing_pubkey_hex):
            ...
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
                await self._callback(0, bytearray(kex_frame(b"\x01\x04" + self.seed)))

        asyncio.ensure_future(challenge())

    async def write_gatt_char(self, char_specifier, data, response=None) -> None:
        data = bytes(data)

        if self.state == "challenge":
            assert data == kex_frame(b"\x02\x04" + self.iv[8:12]), "bad challenge reply"
            await self._callback(0, bytearray(kex_frame(b"\x03\x01\x00")))

            self.private = ec.generate_private_key(ec.SECP256R1())
            pub = pubkey_to_bytes(self.private.public_key())
            signature = der_to_raw_ecdsa(
                self.signing_key.sign(pub + self.iv, ec.ECDSA(hashes.SHA256()))
            )
            if self.corrupt_signature:
                signature = bytes(64)
            message = kex_frame(b"\x04\x80" + pub + signature)
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
                self.crypto.aes_encrypt(kex_frame(b"\x06\x01\x00"), self.key, self.iv)
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
