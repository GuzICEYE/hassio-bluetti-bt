import struct
import unittest
from decimal import Decimal

from bluetti_bt_connect_lib import build_device
from bluetti_bt_connect_lib.fields import FieldName


class TestApex300SolarX(unittest.TestCase):
    """The SolarX 4K's input, as the Apex 300 (or Hub A1) it feeds reports it."""

    def setUp(self):
        self.device = build_device("AP3002300000000000")

    def test_solarx_block_is_parsed(self):
        # Values read from a real system: 593 W at 357.0 V and 1.6 A.
        body = struct.pack("!3H", 593, 3570, 16)

        parsed = self.device.parse(1228, body, slave=0)

        self.assertEqual(parsed[FieldName.SOLARX_POWER.value], 593)
        self.assertEqual(parsed[FieldName.SOLARX_VOLTAGE.value], Decimal("357"))
        self.assertEqual(parsed[FieldName.SOLARX_CURRENT.value], Decimal("1.6"))

    def test_solarx_block_is_one_read_at_slave_0(self):
        requests = [
            (r.slave, r.starting_address, r.quantity)
            for r in self.device.get_polling_registers()
        ]

        self.assertIn((0, 1228, 3), requests)

    def test_every_read_stays_at_slave_0(self):
        # Slave 1 answers with zeros on this device.
        slaves = {r.slave for r in self.device.get_polling_registers()}

        self.assertEqual(slaves, {0})


if __name__ == "__main__":
    unittest.main()
