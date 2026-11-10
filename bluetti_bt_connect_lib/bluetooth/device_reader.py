import asyncio
import logging
import struct
import time
import async_timeout
from typing import Any, Callable, Iterable, List, cast
from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from ..base_devices import BluettiDevice
from ..const import NOTIFY_UUID, WRITE_UUID
from ..registers import (
    ReadableRegisters,
    DeviceRegister,
    WriteableRegister,
    WriteableRegisters,
)
from ..exceptions import ModbusError, ParseError
from ..fields import FieldName, Node, WriteableUIntField, parse_node_list
from ..schedule import (
    SCHEDULE_ADDRESS,
    ScheduleError,
    SchedulePeriod,
    encode_schedule,
)
from ..utils.privacy import mac_loggable
from .device_connection import DeviceConnection
from .encryption import (
    AES_BLOCK_SIZE,
    KEX_MAGIC,
    BluettiEncryption,
    Message,
    MessageType,
    hexsum,
)
from .write_result import WriteResult, WriteOutcome
from .raw_read import MAX_RAW_READ_COUNT, RawReadOutcome, RawReadResult

RESPONSE_TIMEOUT = 5
"""Seconds to wait for the device to answer one request."""

NODE_LIST_ADDRESS = 21002
NODE_LIST_COUNT = 32
"""Where the node list starts and how much of it read_nodes reads."""

ENCRYPTION_DETECT_TIMEOUT = 3
"""Seconds to wait, in auto mode, for an encrypted device to open the key
exchange. Encrypted devices send their challenge as soon as notifications are
subscribed; a device that stays silent this long speaks the plain protocol."""

ENCRYPTION_HANDSHAKE_TIMEOUT = 15
"""Seconds allowed for the whole key exchange once it is expected."""

MAX_ENCRYPTED_MESSAGE = 4096
"""Upper bound for one encrypted message. A larger length header means the
stream is out of step, not that the device sent something that big."""

LATE_REPLY_GRACE = 1
"""Seconds to wait after a probe register times out, so a reply that was
merely slow lands now rather than inside the next request's response."""


class DeviceReaderConfig:
    def __init__(
        self,
        timeout: int = 60,
        use_encryption: bool | None = False,
        keep_alive_seconds: float = 0,
        slow_poll_seconds: float = 600,
    ):
        self.timeout = timeout
        self.use_encryption = use_encryption
        """Whether the device speaks BLUETTI's encrypted protocol.

        True waits for the key exchange before the first request and fails
        the conversation if it does not complete. False sends plain requests
        straight away. None detects it: the reader waits briefly for the
        device to open the key exchange and carries on in the clear if it
        does not. `DeviceReader.encryption_detected` reports the outcome.
        """

        self.keep_alive_seconds = keep_alive_seconds
        """How long to hold a shared connection open after a conversation.

        Only meaningful when a DeviceConnection is supplied. Zero disconnects
        immediately, matching the old behaviour. A value comfortably longer
        than the polling interval keeps the link up between polls, removing
        connection setup from every cycle. Negative holds it indefinitely.
        """

        self.slow_poll_seconds = slow_poll_seconds
        """How often fields marked slow_poll() are read again. Their last
        values are reported on every poll in between."""



class DeviceReader:
    def __init__(
        self,
        mac: str,
        bluetti_device: BluettiDevice,
        future_builder_method: Callable[[], asyncio.Future[Any]],
        config: DeviceReaderConfig = DeviceReaderConfig(),
        lock: asyncio.Lock = asyncio.Lock(),
        ble_client: BleakClient | None = None,
        connection: DeviceConnection | None = None,
    ):
        self.mac = mac
        self.bluetti_device = bluetti_device
        self.create_future = future_builder_method
        self.config = config
        self.polling_lock = lock

        self.ble_client = ble_client
        """Used for unittests"""

        self.connection = connection
        """A connection shared with anything else that talks to this device.

        When supplied, the reader borrows it instead of opening its own link,
        and leaves it open afterwards for `keep_alive_seconds`. That is what
        lets a write and its confirmation share a session, and stops a write
        from tearing down a connection a poll is using.

        When None the reader behaves exactly as before: connect, read,
        disconnect.
        """

        self.logger = logging.getLogger(
            f"{__name__}.{mac_loggable(mac).replace(':', '_')}"
        )

        self.device = None
        self.client = None

        self.has_notifier = False
        self.current_registers = None
        self.notify_response = bytearray()
        self.notify_future: asyncio.Future[Any] | None = None

        self.encryption = BluettiEncryption()
        self.encrypted_buffer = bytearray()
        self._challenge_seen = asyncio.Event()
        self._handshake_done = asyncio.Event()
        self._challenge: bytes | None = None
        self._pubkey_answered = False

        self.encryption_detected: bool | None = None
        """Whether the device turned out to be encrypted. None until a
        conversation has got far enough to tell."""

        self.last_exception_code: int | None = None
        """Exception code from the most recent command, if the device refused
        it. Cleared at the start of every command."""

        self.rejected_groups: set[tuple[int, int, int]] = set()
        """Merged reads the device refused with a Modbus exception, as
        (slave, start, quantity). Their fields are read one request each
        from then on, so a range that spans an unserved address costs one
        refused request for the life of this reader rather than one per poll.
        CRC errors and timeouts are not counted - they are not the device
        saying no."""

        self._slow_values: dict = {}
        self._last_slow_read: float | None = None

        self.skipped_probe_registers: set[int] = set()
        """Probe registers the device refused or ignored. Not asked for again
        for the life of this reader, so an unserved address costs one failed
        request rather than one per poll."""

    async def read(
        self, only_registers: List[ReadableRegisters] | None = None, raw: bool = False
    ) -> dict | None:

        registers = self.bluetti_device.get_polling_registers()
        pack_registers = self.bluetti_device.get_pack_polling_registers()

        if only_registers is not None:
            registers = only_registers
            pack_registers = []

        parsed_data: dict = {}

        self.logger.debug("Reading device registers")

        async with self.polling_lock:
            try:
                async with async_timeout.timeout(self.config.timeout):
                    if not await self._open():
                        return None

                    for register in registers:
                        parsed_data.update(await self._read_registers(register, raw))

                    if only_registers is None:
                        await self._read_slow_registers(raw)
                        # Last known values of the slow fields, under the
                        # values read this poll.
                        parsed_data = {**self._slow_values, **parsed_data}

                    for pack in range(1, self.bluetti_device.max_packs + 1):
                        # Selecting a pack is a write - it returns no register
                        # data of its own, so there is nothing here to parse.
                        await self._async_send_command(
                            self.bluetti_device.get_pack_selector(pack),
                        )

                        # We need to wait for the powerstation to populate all registers
                        await asyncio.sleep(3)

                        for register in pack_registers:
                            parsed_data.update(
                                await self._read_registers(register, raw, pack_num=pack)
                            )

                    # Probe registers last, so one that never answers has
                    # nothing after it in this poll to disturb.
                    if only_registers is None:
                        for register in self.bluetti_device.get_optional_registers():
                            parsed_data.update(
                                await self._read_probe_register(register, raw)
                            )

            except TimeoutError:
                self.logger.warning("Timeout")
                return None
            except BleakError as err:
                self.logger.warning("Bleak error: %s", err)
                return None
            except BaseException as err:
                self.logger.warning("Unknown error %s", err)
                return None
            finally:
                await self._close()

            if not parsed_data:
                return None

            return parsed_data

    async def write(self, field: str, value: Any) -> WriteResult:
        """Write one field and report what the device said about it.

        The Modbus write function answers every request - echoing the value
        on success, or returning an exception frame with a reason. Sending a
        write and disconnecting without reading that answer, which is what
        DeviceWriter does, discards the only trustworthy signal about whether
        a setting actually took.

        This sends the write on the same connection and notification handler
        the reader already uses, so the answer arrives where it can be seen.
        """

        known = [f.name for f in self.bluetti_device.fields]

        if field not in known:
            self.logger.error("Field not supported: %s", field)
            return WriteResult(
                WriteOutcome.FAILED, field, value, detail="field not supported"
            )

        writeable = [
            f
            for f in self.bluetti_device.fields
            if f.name == field and f.is_writeable()
        ]

        if (
            writeable
            and isinstance(writeable[0], WriteableUIntField)
            and not writeable[0].allowed_write_type(value)
        ):
            # Never send a number the field does not allow. Home Assistant
            # bounds its sliders too, but a service call or script can
            # still pass anything.
            self.logger.error("Value out of range for %s: %s", field, value)
            return WriteResult(
                WriteOutcome.FAILED, field, value, detail="value out of range"
            )

        command = self.bluetti_device.build_write_command(field, value)

        if command is None:
            self.logger.error("Field is not writeable: %s", field)
            return WriteResult(
                WriteOutcome.FAILED, field, value, detail="field is not writeable"
            )

        return await self._send_write(field, value, command)

    async def write_schedule(self, periods: Iterable[SchedulePeriod]) -> WriteResult:
        """Replace the whole time-control schedule in one write.

        The periods are validated (at most six, each within one day, no
        overlaps) and written earliest first to 2030-2047 as a single
        function-16 request at the device's write slave, the way BLUETTI's
        app does it. Unused slots are written as off 00:00-00:00. Nothing is
        sent if the schedule is not valid.
        """
        name = "schedule"
        periods = list(periods)

        if not any(
            f.name == FieldName.SCHEDULE_SLOT_1.value for f in self.bluetti_device.fields
        ):
            return WriteResult(
                WriteOutcome.FAILED, name, periods, detail="schedule not supported"
            )

        try:
            words = encode_schedule(periods)
        except ScheduleError as err:
            self.logger.error("Schedule not written: %s", err)
            return WriteResult(WriteOutcome.FAILED, name, periods, detail=str(err))

        command = WriteableRegisters(
            SCHEDULE_ADDRESS, words, self.bluetti_device.write_slave_addr
        )
        return await self._send_write(name, periods, command)

    async def _send_write(
        self, field: str, value: Any, command: DeviceRegister
    ) -> WriteResult:
        """Send one write on the shared connection and interpret the reply."""

        async with self.polling_lock:
            try:
                async with async_timeout.timeout(self.config.timeout):
                    if not await self._open():
                        return WriteResult(
                            WriteOutcome.FAILED, field, value, detail="not connected"
                        )

                    self.logger.debug("Writing %s = %s", field, value)

                    response = await self._async_send_command(command)

                    return self._interpret_write(field, value, command, response)
            except (TimeoutError, asyncio.TimeoutError):
                self.logger.warning("Timeout writing %s", field)
                return WriteResult(WriteOutcome.NO_RESPONSE, field, value)
            except BleakError as err:
                self.logger.warning("Bleak error writing %s: %s", field, err)
                return WriteResult(
                    WriteOutcome.FAILED, field, value, detail=f"bleak error: {err}"
                )
            except BaseException as err:
                self.logger.warning("Unknown error writing %s: %s", field, err)
                return WriteResult(
                    WriteOutcome.FAILED, field, value, detail=str(err)
                )
            finally:
                await self._close()

    def _interpret_write(
        self, field: str, value: Any, command: DeviceRegister, response: bytes
    ) -> WriteResult:
        """Turn a raw write response into a verdict."""

        # _async_send_command swallows Modbus exceptions and returns empty
        # bytes, having already recorded the code.
        if not response:
            code = self.last_exception_code

            if code is not None:
                result = WriteResult(
                    WriteOutcome.REFUSED, field, value, exception_code=code
                )
                self.logger.warning("Write refused - %s", result)
                return result

            self.logger.warning("No response writing %s", field)
            return WriteResult(WriteOutcome.NO_RESPONSE, field, value)

        if isinstance(command, WriteableRegister):
            echoed = int.from_bytes(command.parse_response(response), "big")

            if echoed == command.value:
                self.logger.debug("Write accepted: %s = %s", field, echoed)
                return WriteResult(
                    WriteOutcome.ACCEPTED, field, value, echoed=echoed
                )

            result = WriteResult(
                WriteOutcome.MISMATCHED, field, value, echoed=echoed
            )
            self.logger.warning("%s", result)
            return result

        # Multi-register writes echo the start address and quantity rather
        # than the values; check both.
        if isinstance(command, WriteableRegisters):
            address, quantity = struct.unpack("!HH", command.parse_response(response))
            if address != command.address or quantity != len(command.values):
                result = WriteResult(
                    WriteOutcome.MISMATCHED,
                    field,
                    value,
                    detail=f"echoed address {address}, quantity {quantity}",
                )
                self.logger.warning("%s", result)
                return result

        self.logger.debug("Multi-register write acknowledged: %s", field)
        return WriteResult(WriteOutcome.ACCEPTED, field, value)

    @property
    def is_connected(self) -> bool:
        """Whether a live GATT connection is currently held open."""
        if self.connection is not None:
            return self.connection.is_connected

        return self.client is not None and getattr(self.client, "is_connected", False)

    async def _open(self) -> bool:
        """Get a usable client and notification subscription."""

        if self.connection is not None:
            # A fresh link means a fresh key exchange. The callback has to be
            # in place before connecting: an encrypted device sends its
            # challenge the moment notifications are subscribed.
            if not self.connection.is_connected:
                self._reset_encryption()

            self.connection.set_data_callback(self._handle_data)
            self.connection.set_disconnect_callback(self._abandon_pending)

            if not await self.connection.ensure_connected():
                self.logger.error("Shared connection unavailable")
                return False

            self.client = self.connection.client
            self.logger.debug("Using shared connection")
            return await self._await_encryption()

        self.logger.debug("Searching for device")

        if self.ble_client:
            self.device = None
            self.client = self.ble_client
        else:
            self.device = await BleakScanner.find_device_by_address(
                self.mac, timeout=5
            )

            if self.device is None:
                self.logger.error("Device not found")
                return False

            self.logger.debug("Connecting to device")
            self.client = await establish_connection(
                BleakClientWithServiceCache,
                self.device,
                self.device.name or "Unknown Device",
                max_attempts=10,
            )

        self.logger.debug("Connected to device")

        if not self.has_notifier:
            # An owned connection is new every time, so is the key exchange.
            self._reset_encryption()
            await self.client.start_notify(NOTIFY_UUID, self._notification_handler)
            self.has_notifier = True
            self.logger.debug("Notification handler setup complete")

        return await self._await_encryption()

    def _reset_encryption(self) -> None:
        """Forget all key material, ready for a new connection."""
        self.encryption = BluettiEncryption()
        self.encrypted_buffer.clear()
        self._challenge_seen.clear()
        self._handshake_done.clear()
        self._challenge = None
        self._pubkey_answered = False

    async def _await_encryption(self) -> bool:
        """Hold the first request back until the key exchange has finished.

        Returns False only when encryption is required (or was started by the
        device) and the exchange did not complete in time.
        """
        mode = self.config.use_encryption

        if mode is False:
            return True

        if self.encryption.is_ready_for_commands:
            return True

        if mode is None and self.encryption_detected is False:
            # Already found to be a plain device; do not wait again.
            return True

        if mode is None and not self._challenge_seen.is_set():
            try:
                await asyncio.wait_for(
                    self._challenge_seen.wait(), ENCRYPTION_DETECT_TIMEOUT
                )
            except (TimeoutError, asyncio.TimeoutError):
                self.logger.debug("No key exchange offered - plain protocol")
                self.encryption_detected = False
                return True

        try:
            await asyncio.wait_for(
                self._handshake_done.wait(), ENCRYPTION_HANDSHAKE_TIMEOUT
            )
        except (TimeoutError, asyncio.TimeoutError):
            self.logger.warning("Encryption key exchange did not complete")
            return False

        return True

    async def _close(self) -> None:
        """Release the connection, or hand it back if it is shared."""

        if self.connection is not None:
            self.connection.clear_data_callback()
            self.connection.set_disconnect_callback(None)
            self.connection.schedule_disconnect(self.config.keep_alive_seconds)
            return

        if self.has_notifier:
            try:
                await self.client.stop_notify(NOTIFY_UUID)
                self.logger.debug("Stopped notifier")
            except Exception:
                pass
            self.has_notifier = False

        if self.client:
            await self.client.disconnect()
            self.logger.debug("Disconnected from device")

    def _abandon_pending(self) -> None:
        """Fail a waiting command when the link drops underneath it."""
        if self.notify_future is not None and not self.notify_future.done():
            self.notify_future.set_exception(
                BleakError("Disconnected while awaiting a response")
            )

    async def _read_registers(
        self,
        register: ReadableRegisters,
        raw: bool,
        pack_num: int | None = None,
    ) -> dict:
        """Read one register request, falling back to its members on failure.

        A merged request spans the unused registers between the fields it
        covers, so a single address the device refuses to serve can make it
        reject the whole range. When that happens, read the individual fields
        the request was merged from one at a time, so one bad register only
        costs its own field rather than every field grouped with it.
        """

        key = (register.slave, register.starting_address, register.quantity)

        if len(register.members) > 1 and key in self.rejected_groups:
            return await self._read_members(register, raw, pack_num)

        response = await self._async_send_command(register)

        if response:
            return self._parse_registers(register, response, raw, pack_num)

        # An atomic read has nothing to fall back to.
        if len(register.members) <= 1:
            return {}

        if self.last_exception_code is not None:
            self.rejected_groups.add(key)
            self.logger.info(
                "Grouped read %s refused (exception 0x%02x) - reading it as %d "
                "smaller requests from now on",
                register,
                self.last_exception_code,
                len(register.fallback or register.members),
            )
        else:
            self.logger.debug(
                "Grouped read %s failed - falling back to %d individual reads",
                register,
                len(register.members),
            )

        return await self._read_members(register, raw, pack_num)

    async def _read_slow_registers(self, raw: bool) -> None:
        """Read the slow-poll fields when they are due, keeping the values."""

        registers = self.bluetti_device.get_slow_polling_registers()

        if not registers:
            return

        now = time.monotonic()

        if (
            self._last_slow_read is not None
            and now - self._last_slow_read < self.config.slow_poll_seconds
        ):
            return

        values: dict = {}

        for register in registers:
            values.update(await self._read_registers(register, raw))

        # Keep earlier values for anything that did not answer this time. If
        # nothing answered, try again on the next poll rather than waiting.
        self._slow_values.update(values)

        if values:
            self._last_slow_read = now

    async def _read_members(
        self, register: ReadableRegisters, raw: bool, pack_num: int | None
    ) -> dict:
        """Read a refused merged request in smaller pieces.

        Uses its conservative fallback groups when it has them (each of which
        can itself be refused and split further), otherwise its fields one
        at a time.
        """

        parsed_data: dict = {}

        for part in register.fallback or register.members:
            parsed_data.update(await self._read_registers(part, raw, pack_num))

        return parsed_data

    async def read_nodes(self) -> List[Node] | None:
        """The devices in the system, from the battery's own node list.

        Reads 32 registers at 21002 (four 8-word entries). Returns None if
        the list could not be read.
        """
        result = await self.read_raw(NODE_LIST_ADDRESS, NODE_LIST_COUNT)

        if not result.ok:
            return None

        data = b"".join(word.to_bytes(2, "big") for word in result.words)
        return parse_node_list(data)

    async def read_raw(
        self, address: int, count: int = 1, slave: int = 1
    ) -> RawReadResult:
        """Read a block of registers once and return the raw words.

        For exploring addresses the device definition does not cover. It is
        strictly a read (Modbus function 3) at the read slave, never merged
        with anything else, and it takes the same lock as polling and writes,
        so it can never interleave with them.

        A refusal or silence is reported in the result rather than raised:
        exploring means asking for addresses the device may not serve, and
        neither answer says anything is wrong with the link.
        """

        if not 1 <= count <= MAX_RAW_READ_COUNT:
            raise ValueError(f"count must be 1-{MAX_RAW_READ_COUNT}, got {count}")

        if not 0 <= address or address + count > 0x10000:
            raise ValueError(f"address range {address}+{count} is outside 0-65535")

        if not 0 <= slave <= 247:
            raise ValueError(f"slave must be 0-247, got {slave}")

        register = ReadableRegisters(address, count, slave)

        def result(outcome, **kwargs):
            return RawReadResult(address, count, outcome, slave=slave, **kwargs)

        async with self.polling_lock:
            try:
                if not await self._open():
                    return result(RawReadOutcome.NOT_CONNECTED)

                try:
                    response = await self._async_send_command(register)
                except (TimeoutError, asyncio.TimeoutError):
                    await asyncio.sleep(LATE_REPLY_GRACE)
                    return result(RawReadOutcome.NO_REPLY)

                if not response:
                    if self.last_exception_code is not None:
                        return result(
                            RawReadOutcome.REFUSED,
                            exception_code=self.last_exception_code,
                        )

                    return result(
                        RawReadOutcome.ERROR,
                        detail="corrupted or unreadable reply",
                    )

                body = register.parse_response(response)
                words = [
                    int.from_bytes(body[i : i + 2], "big")
                    for i in range(0, len(body) - 1, 2)
                ]

                self.logger.info(
                    "Raw read %d+%d at slave %d: %s", address, count, slave, words
                )

                return result(RawReadOutcome.OK, words=words)
            except BleakError as err:
                return result(RawReadOutcome.ERROR, detail=f"Bluetooth error: {err}")
            finally:
                await self._close()

    async def _read_probe_register(
        self, register: ReadableRegisters, raw: bool
    ) -> dict:
        """Read one unconfirmed register without letting it fail the poll.

        An ordinary register that times out aborts the whole poll, because a
        missing reply usually means the link is gone. An unconfirmed address
        is different: the device may simply not serve it, and may say so by
        staying silent. Here a timeout or a refusal only drops this register,
        and it is not asked for again. A real connection failure (BleakError)
        still propagates as usual.
        """

        address = register.starting_address

        if address in self.skipped_probe_registers:
            return {}

        try:
            response = await self._async_send_command(register)
        except (TimeoutError, asyncio.TimeoutError):
            self.skipped_probe_registers.add(address)
            self.logger.warning(
                "Probe register %d did not answer - not reading it again "
                "until the integration restarts",
                address,
            )
            await asyncio.sleep(LATE_REPLY_GRACE)
            return {}

        if not response:
            self.skipped_probe_registers.add(address)
            self.logger.info(
                "Probe register %d was refused by the device (code %s) - not "
                "reading it again until the integration restarts",
                address,
                f"0x{self.last_exception_code:02x}"
                if self.last_exception_code is not None
                else "none",
            )
            return {}

        return self._parse_registers(register, response, raw)

    def _parse_registers(
        self,
        register: ReadableRegisters,
        response: bytes,
        raw: bool,
        pack_num: int | None = None,
    ) -> dict:
        """Turn one register response into parsed field values."""

        body = register.parse_response(response)

        self.logger.debug("Raw data: %s", body)

        if raw:
            if register.slave != 1:
                return {(register.slave, register.starting_address): body}
            return {register.starting_address: body}

        parsed = self.bluetti_device.parse(
            register.starting_address, body, pack_num=pack_num, slave=register.slave
        )

        self.logger.debug("Parsed data: %s", parsed)

        return parsed

    async def _async_send_command(self, registers: DeviceRegister) -> bytes:
        """Send command and return response"""
        self.current_registers = registers
        self.notify_response = bytearray()
        self.notify_future = self.create_future()
        self.last_exception_code = None

        command_bytes = bytes(registers)

        if self.encryption.is_ready_for_commands:
            command_bytes = self.encryption.aes_encrypt(
                command_bytes, self.encryption.secure_aes_key, None
            )

        try:
            await self.client.write_gatt_char(WRITE_UUID, command_bytes)

            self.logger.debug("Request sent (%s)", registers)

            res = await asyncio.wait_for(self.notify_future, timeout=RESPONSE_TIMEOUT)

            self.logger.debug("Got response")

            return cast(bytes, res)
        except (BleakError, asyncio.TimeoutError):
            # Connection-level failures - re-raise so the caller can abort
            # cleanly and disconnect, rather than continuing to attempt
            # further register reads on an already-broken connection.
            self.logger.warning("Error while reading data")
            raise
        except ModbusError as err:
            # Device explicitly rejected this register - log and move on.
            self.logger.debug("Modbus exception for %s: %s", registers, err)
        except ParseError as err:
            # Failed CRC validation - log and move on.
            self.logger.debug("Parse error for %s: %s", registers, err)
        except Exception:
            self.logger.warning("Error while reading data")

        return bytes()

    async def _notification_handler(self, _: int, data: bytearray):
        """Notification callback for a connection this reader owns."""
        self._handle_data(bytes(data))

    def _handle_data(self, data: bytes) -> None:
        """Accumulate a response and resolve the waiting command.

        Reached either directly from bleak on a standalone connection, or
        from DeviceConnection when the link is shared. Identical either way.
        """
        self.logger.debug("Got new data (%d bytes)", len(data))

        plain = self._unwrap(data)

        if plain is None:
            return

        self._handle_plain(plain)

    def _is_kex_frame(self, data: bytes) -> bool:
        """Whether this is a key-exchange frame rather than protocol data.

        The magic alone is not enough - register data can start with the same
        two bytes - so the checksum and the message type have to agree too.
        """
        if len(data) < 6 or bytes(data[:2]) != KEX_MAGIC:
            return False

        body = bytes(data[2:-2])

        if hexsum(body, 2) != bytes(data[-2:]):
            return False

        return body[0] in {t.value for t in MessageType}

    def _unwrap(self, data: bytes) -> bytes | None:
        """Strip the encryption layer, returning protocol bytes if any.

        Key-exchange frames are answered here and never reach the protocol
        parser. On a plain device this returns the data untouched.
        """
        if not self.encrypted_buffer and self._is_kex_frame(data):
            self._handle_kex(Message(bytes(data)))
            return None

        if self.encryption.unsecure_aes_key is None:
            return data

        self.encrypted_buffer.extend(data)
        plain = bytearray()

        while len(self.encrypted_buffer) >= 2:
            key, iv = self.encryption.getKeyIv()
            data_len = (self.encrypted_buffer[0] << 8) + self.encrypted_buffer[1]
            header_size = 6 if iv is None else 2
            padded_len = -(-data_len // AES_BLOCK_SIZE) * AES_BLOCK_SIZE
            expected = header_size + padded_len

            if data_len == 0 or expected > MAX_ENCRYPTED_MESSAGE:
                self.logger.warning(
                    "Encrypted stream out of step (length %d), dropping buffer",
                    data_len,
                )
                self.encrypted_buffer.clear()
                break

            if len(self.encrypted_buffer) < expected:
                self.logger.debug(
                    "Buffering fragment: %d/%d bytes",
                    len(self.encrypted_buffer),
                    expected,
                )
                break

            message = bytes(self.encrypted_buffer[:expected])
            del self.encrypted_buffer[:expected]

            try:
                decrypted = self.encryption.aes_decrypt(message, key, iv)
            except ValueError as err:
                self.logger.error("Decryption failed: %s", err)
                self.encrypted_buffer.clear()
                break

            if self._is_kex_frame(decrypted):
                self._handle_kex(Message(decrypted))
                continue

            plain.extend(decrypted)

        return bytes(plain) if plain else None

    def _handle_kex(self, message: Message) -> None:
        """Advance the key exchange by one step."""
        try:
            kind = message.type

            # The device repeats a step until it has seen the answer, so the
            # same message can arrive several times. Each step is answered
            # once: answering a repeated public key again would generate a
            # second local keypair, and the shared secret would then be
            # derived from a key the device never accepted.
            if kind == MessageType.CHALLENGE:
                challenge = bytes(message.data)

                if challenge == self._challenge:
                    self.logger.debug("Repeated challenge, already answered")
                    return

                # The device (re)starts the exchange; drop any older keys.
                self._reset_encryption()
                self._challenge = challenge
                self._challenge_seen.set()
                self._send_kex(self.encryption.msg_challenge(message))
            elif kind == MessageType.CHALLENGE_ACCEPTED:
                self.logger.debug("Challenge accepted")
            elif kind == MessageType.PEER_PUBKEY:
                if self._pubkey_answered:
                    self.logger.debug("Repeated public key, already answered")
                    return

                reply = self.encryption.msg_peer_pubkey(message)
                self._pubkey_answered = True
                self._send_kex(reply)
            elif kind == MessageType.PUBKEY_ACCEPTED:
                self.encryption.msg_key_accepted(message)
                self.encryption_detected = True
                self._handshake_done.set()
                self.logger.debug("Key exchange complete")
        except Exception as err:  # a bad signature, a malformed frame
            self.logger.error("Key exchange failed: %s", err)

    def _send_kex(self, payload: bytes | None) -> None:
        """Queue one key-exchange reply; the notification path cannot await."""
        if payload is None:
            return

        client = self.connection.client if self.connection is not None else self.client

        if client is None:
            return

        async def _send() -> None:
            try:
                await client.write_gatt_char(WRITE_UUID, payload)
            except Exception as err:
                self.logger.warning("Could not send key-exchange reply: %s", err)

        asyncio.ensure_future(_send())

    def _handle_plain(self, data: bytes) -> None:
        """Accumulate decrypted (or plain) protocol bytes."""
        if self.encryption.is_ready_for_commands:
            self.logger.debug("Decrypted: %s", data.hex())

        self.notify_response.extend(data)

        if self.notify_future is None or self.notify_future.done():
            return

        if self.current_registers is None:
            return

        expected_size = self.current_registers.response_size()

        # A Modbus exception response is shorter than a normal response,
        # so check for it independently rather than gating on full length -
        # an exception frame will never reach the "normal" expected_size.
        if len(
            self.notify_response
        ) != expected_size and self.current_registers.is_exception_response(
            self.notify_response
        ):
            # Byte 2 carries the reason the device refused. Keep it: for a
            # write it is the difference between "out of range" and "the
            # device will not let you set this", and it is the only place
            # that distinction is ever stated.
            if len(self.notify_response) >= 3:
                self.last_exception_code = self.notify_response[2]

            self.notify_future.set_exception(
                ModbusError(
                    f"Device returned a Modbus exception response for "
                    f"{self.current_registers} "
                    f"(code 0x{self.last_exception_code:02x})"
                    if self.last_exception_code is not None
                    else f"Device returned a Modbus exception response for {self.current_registers}"
                )
            )
            return

        # Responses can arrive across multiple BLE fragments - wait for
        # the full expected length before validating/resolving.
        if len(self.notify_response) < expected_size:
            return

        if not self.current_registers.is_valid_response(self.notify_response):
            self.logger.debug(
                "CRC validation failed for %s: %s",
                self.current_registers,
                self.notify_response.hex(),
            )
            self.notify_future.set_exception(
                ParseError("Response failed CRC validation")
            )
            return

        self.notify_future.set_result(self.notify_response)
