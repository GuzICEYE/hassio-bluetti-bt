from typing import List

from . import BluettiDevice
from ..fields import DeviceField
from ..fields import FieldName, SwapStringField, UIntField, SerialNumberField
from ..registers import ReadableRegisters


class BaseDeviceV2(BluettiDevice):
    def __init__(
        self,
        additional_fields: List[DeviceField] = [],
        pack_fields: List[DeviceField] = [],
        max_packs: int = 0,
        slave: int = 1,
        **kwargs,
    ):
        self.slave = slave
        """Modbus slave address the base fields (type, serial, state of
        charge) are read from. Slave 1 on most devices. 2nd-generation IoT
        devices such as the Apex 300 answer at slave 1 with every register
        zero and serve the real values at slave 0."""

        super().__init__(
            [
                SwapStringField(FieldName.DEVICE_TYPE, 110, 6).at_slave(slave),
                SerialNumberField(FieldName.DEVICE_SN, 116).at_slave(slave),
                UIntField(FieldName.BATTERY_SOC, 102, min=0, max=100).at_slave(slave),
            ]
            + additional_fields,
            pack_fields,
            max_packs,
            **kwargs,
        )

    def get_full_registers_range(self) -> List[ReadableRegisters]:
        return [ReadableRegisters(i, 10) for i in range(1, 20000, 10)]

    def get_device_type_registers(self) -> List[ReadableRegisters]:
        return [
            ReadableRegisters(110, 6, self.slave),
        ]

    def get_device_sn_registers(self) -> List[ReadableRegisters]:
        return [
            ReadableRegisters(116, 4, self.slave),
        ]

    def get_iot_version(self) -> int:
        return 2

    # def get_pack_selector(self, pack: int) -> WriteableRegister:
    #     return WriteableRegister(7000, pack)  # TODO test
