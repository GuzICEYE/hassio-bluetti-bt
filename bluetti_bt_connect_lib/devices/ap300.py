from ..base_devices import BaseDeviceV2
from ..fields import FieldName, UIntField, DecimalField, BoolFieldNonZero

SLAVE = 0
"""The Apex 300 is a 2nd-generation IoT device. It answers at slave 1 too,
but with every register zero; the real values are served at slave 0."""


class AP300(BaseDeviceV2):
    """BLUETTI Apex 300.

    Encrypted, so it is read through the key exchange in
    bluetooth/encryption.py. The fields below were checked against a real
    unit. Keep to register blocks the device serves: after refusing a read
    (exception 0x02) it stays silent for the next few requests.
    """

    def __init__(self):
        super().__init__(
            [
                UIntField(FieldName.DC_OUTPUT_POWER, 140).at_slave(SLAVE),
                UIntField(FieldName.AC_OUTPUT_POWER, 142).at_slave(SLAVE),
                UIntField(FieldName.DC_INPUT_POWER, 144).at_slave(SLAVE),
                UIntField(FieldName.AC_INPUT_POWER, 146).at_slave(SLAVE),
                DecimalField(FieldName.POWER_GENERATION, 154).at_slave(SLAVE),
                DecimalField(FieldName.AC_INPUT_FREQUENCY, 1300, 1).at_slave(SLAVE),
                DecimalField(FieldName.AC_INPUT_VOLTAGE, 1314, 1).at_slave(SLAVE),
                DecimalField(FieldName.AC_OUTPUT_FREQUENCY, 1500, 1).at_slave(SLAVE),
                DecimalField(FieldName.AC_OUTPUT_VOLTAGE, 1511, 1).at_slave(SLAVE),
                BoolFieldNonZero(FieldName.AC_OUTPUT_ON, 2011).at_slave(SLAVE),
                BoolFieldNonZero(FieldName.DC_OUTPUT_ON, 2012).at_slave(SLAVE),
            ],
            slave=SLAVE,
            write_slave_addr=SLAVE,
            # V2 devices serve registers in blocks of 100.
            max_register_block=100,
        )
