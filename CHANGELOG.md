# Changelog: bluetti-bt-connect-lib

Newest first. Each section is also published as the GitHub release for that version.

## 2.1.0 (2026-11-10)

### Encrypted devices are back, and the Apex 300 works

bluetti-bt-lib 0.1.7 could read encrypted devices; 2.0 of this fork removed
that. This release ports the encryption layer onto the current reader, so
encrypted units can be read locally again with no BLUETTI account or cloud.

- New `bluetooth/encryption.py` (from bluetti-bt-lib, based on
  nhurman/bluetti_mqtt): ECDH key exchange, then AES-CBC around the usual
  protocol. `cryptography` and `pyasn1` are new dependencies.
- `DeviceReaderConfig.use_encryption` works again. `True` waits for the key
  exchange, `False` is the plain protocol, and the new `None` detects it.
  `DeviceReader.encryption_detected` reports what was found.
- `recognize_device()` detects encryption and reports it as
  `DeviceRecognizerResult.encrypted`. It now does all its reads over one
  connection, which matters on an encrypted device because every new
  connection costs a key exchange.
- Fixed while testing on hardware: the device repeats a key-exchange step
  when the answer is slow, and answering a repeated public key generated a
  second local keypair, leaving the two sides with different keys. Each step
  is now answered once.
- Key-exchange frames are recognised by magic, checksum and type together, so
  register data that happens to start with the magic bytes is not mistaken
  for one.
- **Apex 300 (`AP300`)**: it answers at slave 1 with every register zero and
  serves the real data at slave 0, so it was never recognised. Its fields now
  read from slave 0 (`BaseDeviceV2(slave=0)`), the recognizer tries slave 0,
  and the field list gained output frequency and voltage, input frequency,
  power generation and the AC/DC output states. All checked on a real unit.
- Writes use the encrypted channel too, but are untested on hardware.

## 2.0.13 (2026-10-26)

### EP2000: 14 requests a poll, no refused read at startup

On a real EP2000 the 2.0.12 merged read of 1290-1327 was refused (Modbus
exception 0x03: the 1200 block ends before 1300). The fallback handled it, but
that left 15 requests a poll and one refused request every time the
integration started.

- New `BluettiDevice(max_register_block=...)`: a merged read never crosses a
  multiple of this address. The EP2000 uses 100, matching how BLUETTI's
  devices serve registers (blocks at 100, 1100, 1200, 1300, ...). Off by
  default, so other models are unchanged.
- On the EP2000 this only splits 1290 from 1300-1327: 14 requests a poll,
  down from 28 before 2.0.12. Every other merged range was confirmed on
  hardware.
- The log line for a refused merged read now gives the number of requests it
  is split into, rather than its field count.

## 2.0.12 (2026-10-26)

### EP2000: faster polls

A poll now takes 13 requests instead of 28, so each update over Bluetooth
finishes in roughly half the time.

- **Wider merged reads.** The EP2000 merges fields up to 32 registers apart
  into reads of up to 100 registers (`max_register_gap=32`,
  `max_register_quantity=100`). Every merged range sits inside blocks read
  successfully on a real EP2000 + EBOX. Other models keep the default
  grouping (8 / 32).
- **Slow-poll tier.** Values that rarely change are read on the first poll and
  then every 10 minutes: inverter and IoT firmware, WiFi name, connected
  devices, cell / sensor / module counts and the grid voltage and frequency
  limits. Their last values are reported on every poll in between, so nothing
  goes unavailable. New `DeviceField.slow_poll()`,
  `BluettiDevice.get_slow_polling_registers()` and
  `DeviceReaderConfig(slow_poll_seconds=600)`.
- **Refused ranges are remembered.** If the device refuses a merged read with
  a Modbus exception, it is read in the default-size pieces instead
  (`ReadableRegisters.fallback`), and that choice is kept for the life of the
  reader (`DeviceReader.rejected_groups`), so a bad range costs one refused
  request, not one per poll. CRC errors and timeouts do not count as a
  refusal. An INFO log line names any range that gets split.

## 2.0.11 (2026-10-26)

### EP2000: write the time-control schedule

New `DeviceReader.write_schedule(periods)` replaces the whole charge/discharge
schedule in one write, the way BLUETTI's app does it: registers 2030-2047 as a
single function-16 request to the EBOX (slave 0), periods earliest first,
unused slots as off 00:00-00:00.

- New module `bluetti_bt_connect_lib.schedule`: `SchedulePeriod(action,
  start, end)`, `validate_schedule()`, `encode_schedule()`,
  `decode_schedule()` and `ScheduleError`.
- A schedule is refused before anything is sent if it has more than six
  periods, a period set to off, a period that ends at or before its start
  (periods stay within one day, as in the app), or overlapping periods.
  Periods may touch.
- The schedule only acts in the Custom working mode. The weekday mask (5000)
  is not served on the EP2000, so it applies every day.
- `time_control_enabled` (2029) is now a `SwitchField`, written to the EBOX.
- Function-16 writes now check the echoed start address and quantity and
  report `MISMATCHED` if they differ.

### EP2000: battery current and power from the BMS

| field | register | notes |
|---|---|---|
| `battery_current` | 6112 | A, signed, /10 (BLUETTI's scale) |
| `battery_power_bms` | 6111-6112 | W, pack voltage x current |

Positive while charging, negative while discharging. Checked against Home
Assistant's own figures: -8.4 A x 732 V = -6.1 kW while the house drew
6.0-6.2 kW from the battery, and +0.3 A (+220 W) with the battery holding in
backup mode. Both come from the pack read already made.

### Releases

Pushing a `vX.Y.Z` tag now runs the whole release: tests, build, publish to
PyPI, then a GitHub release whose notes are this file's section for that
version. A tag without a matching section, or one that disagrees with
`__version__`, fails before anything is published.

## 2.0.10 (2026-10-25)

### EP2000: battery maintenance status

BLUETTI's battery maintenance (a full discharge then a full charge, which the
app suggests every 3-6 months to rebalance the cells) reports its state in home
register 106 at the EBOX (slave 0). Three new read-only fields decode it the
way BLUETTI's app does:

| field | bits | values |
|---|---|---|
| `battery_maintenance_status` | 0-3 | `BatteryMaintenanceStatus`: NOT_NEEDED, DUE, IN_PROGRESS, FINISHED, STOPPED, DUE_CONDITIONS_NOT_MET |
| `battery_maintenance_progress` | 4-7 | percent, in 10% steps |
| `battery_maintenance_stop_reason` | 8-11 | `BatteryMaintenanceStopReason`: NONE, GRID_OFFLINE, TEMPERATURE_HIGH, TEMPERATURE_LOW, PACK_COUNT_ABNORMAL, CHARGE_FAILED, DISCHARGE_FAILED, NOT_TRIGGERED |

Checked on a real run: 106 read 0x0022, in progress at 20%. Starting or
stopping maintenance is not included.

### EP2000: lowest, highest and spread of cell voltages

| field | register | notes |
|---|---|---|
| `cell_voltage_min` | 6116 | V, from mV |
| `cell_voltage_max` | 6117 | V, from mV |
| `cell_voltage_spread` | 6116-6117 | highest minus lowest, mV: how far out of balance the cells are |

Read from the inverter (slave 1), next to the pack voltage: 3.224 V / 3.232 V,
an 8 mV spread, on a seven-B700 HV800. A reading of 0 means no cells reported
and is dropped.

All five values ride in reads that were already made, so a poll sends no extra
requests. `MaskedEnumField` gained an optional `shift`.

## 2.0.9 (2026-10-15)

### EP2000: battery SOC limits are writable

`soc_range_start` (discharge floor, register 2022) and `soc_range_end`
(charge ceiling, 2023) were read-only sensors. They are now writable numbers,
5-100% in 1% steps.

- Writes go to the EBOX (slave 0) as a single register, the way BLUETTI's app
  writes them. Reads are unchanged.
- The range and step match BLUETTI's app, which sets both limits to any
  whole percent from 5 to 100.
- Every reading from 0 to 100 is still reported, so a value set outside the
  write range by another tool shows up instead of disappearing.
- New `SocLimitField` and `check_soc_limits(low, high)`. The check keeps the
  high limit at least 5% above the low one (`SOC_LIMIT_MIN_GAP`).
- Other models (EP600, EP500, AC300, ...) keep their read-only SOC sensors.

### Writes are range-checked before they are sent

`DeviceReader.write()` now refuses a number outside a writable field's range
and returns `FAILED` with detail `value out of range`, without touching the
device. Before, only Home Assistant's slider bounds stood in the way; a
service call or script could pass any value. This covers the grid import and
export limits too.

## 2.0.8 (2026-10-05)

### Fixed: EP2000 active cell count

`active_cell_count` read register 6153, which holds the pack's number of
temperature sensors (NTCs), not cells. On a seven-B700 HV800 stack it showed
112 instead of 224. BLUETTI's app reads three counts side by side:

| register (low byte) | count | example |
|---|---|---|
| 6152 | cells | 224 |
| 6153 | temperature sensors (NTCs) | 112 |
| 6154 | battery modules (BMUs, one per B700) | 7 |

`active_cell_count` now reads 6152. A new `temperature_sensor_count` field
reads 6153. `battery_stack_count` (6154) is unchanged and is the
battery-module count. All three are read in one request.

## 2.0.7 (2026-10-05)

Everything in this release was decoded the way BLUETTI's own app decodes it,
and checked against a real EP2000 + EBOX + HV800. All new fields are
read-only.

### EP2000

**Working mode 11.** `WorkingMode.SELF_CONSUMPTION_EXPORT` (self-consumption
with export) is now known. BLUETTI's app offers it only on the EP2000 and
EP19K. Before this, the working mode read as unknown whenever it was set.

**From the inverter (slave 1)**

| field | register | notes |
|---|---|---|
| `ems_control_mode` | 2241, low nibble | `EmsControlMode`: LOCAL 0, CLOUD 3, LOCAL_SELECTED 4, DYNAMIC_PRICING 5, VPP 7, AI 8. `CLOUD_CONTROLLED_EMS_MODES` lists 3/5/7 |
| `firmware_arm`, `firmware_dsp` | software list at 1112 | e.g. "5032.22" |

**From the EBOX (slave 0)**

| field | register | notes |
|---|---|---|
| `battery_status` | 103 | `BatteryStatus`: IDLE, CHARGING, DISCHARGING |
| `time_remaining_minutes` | 104 | minutes to full or to empty (the EBOX reports one value in both registers) |
| `active_alarms` | 122–138 | active warnings and faults as text, e.g. "B104 PV2 Voltage Low", or "None" |
| `alarm_count` | 122–138 | number of active warnings and faults |
| `inverter_status` | 161 | `InverterStatus`: OFF, OFF_GRID, GRID_BYPASS, GRID_CONNECTED, GRID_CONNECTED_CHARGING, GRID_CONNECTED_DISCHARGING, ERROR, OFF_GRID_ABNORMAL |
| `system_error` | 174, bits 5/6/7/10 | IoT, BMS, other or meter error flag |
| `ac_pv_power`, `ac_pv_l1/l2/l3_power`, `ac_pv_l1/l2/l3_voltage` | PV slots 3–5 (1226, 1234, 1242) | AC-coupled PV (for example Enphase) through the AC PV meter. Only reported for slots of type 101; 0 W without a meter |
| `time_control_enabled` | 2029 | the schedule used by Custom mode |
| `schedule_slot_1` … `_6` | 2030 + 3n | e.g. "Charge 11:01-13:59", "Discharge 18:00-21:00", "Off" |
| `max_charge_current`, `max_discharge_current` | 6011, 6012 | BMS limits, A |
| `firmware_iot` | 11014 | e.g. "9052.26" |
| `cloud_connected` | 11018, bit 6 | the EBOX's MQTT link to BLUETTI's cloud |
| `wifi_signal` | 11026 | dBm (low byte − 256) |

Alarm texts come from the app's own tables. Table B is used for high-power
inverters (register 122 = 3) and table E otherwise; see
`fields/alarm_codes.py`. A bit with no table entry is shown as `R<register>.<bit>`.

### New field types

`MaskedEnumField`, `BitMaskField`, `LowByteOffsetField`,
`FirmwareVersionField`, `U32VersionField`, `ScheduleSlotField`,
`AlarmListField` / `AlarmCountField`, `AcPvSlotField` / `AcPvTotalField`.

### Polling

The EP2000 poll grows from 20 to 28 requests. The eight new ones go to the
EBOX and cover only ranges a real unit was confirmed to serve.

## 2.0.6 (2026-10-05)

### Fixed: EP2000 pack temperature is °C + 40, not °F

`pack_temperature` (6115) is now decoded as **raw − 40, in °C**. This is
how BLUETTI's own app decodes it: the pack item block's average temperature,
and the same for 6007, the cell NTCs and the inverter's temperatures. 2.0.4
read it as °F based on a fit against air temperature, and the vendor decoding
replaces that. A raw 66 now reads 26 °C, where 2.0.4 showed 18.9 °C.

New field type `OffsetIntField` (raw + a fixed offset) carries it.

### New: fields read from another slave

A field can now be read from a slave other than 1 with `.at_slave(n)`:

```python
SInt32Field(FieldName.HOME_LOAD_POWER, 142).at_slave(0)
```

Reads are grouped per slave and never mixed in one request. A response is
only matched against fields of the slave that answered, so the same address
can be read from two slaves side by side. `ReadableRegisters.slave` and
`BluettiDevice.parse(..., slave=)` carry it through. With `raw=True`, results
from a slave other than 1 are keyed `(slave, address)`.

### New: EP2000 home data from the EBOX (slave 0)

The inverter (slave 1) leaves most of the home data block at 0. The EBOX
(slave 0) fills it in. Read from there, all in one request (142, 23
registers):

| field | register | unit |
|---|---|---|
| `home_load_power` | 142 (s32) | W |
| `home_consumption_energy` | 152 (u32 ×0.1) | kWh |
| `solar_energy` | 154 (u32 ×0.1) | kWh |
| `grid_import_energy` | 156 (u32 ×0.1) | kWh |
| `grid_export_energy` | 158 (u32 ×0.1) | kWh |
| `self_sufficiency` | 164 | % |

The layout comes from BLUETTI's app. The energy totals were checked against
the app's lifetime statistics on a real system. They are 32-bit, low word
first, so they keep counting past 6553.5 kWh.

This also brings back the consumption and grid feed-in totals that 2.0.5
removed: they were 0 because they were being read from the inverter.

## 2.0.5 (2026-10-05)

### EP2000

Removed three energy totals that never reported anything on the EP2000:

| field | register |
|---|---|
| `total_ac_consumption` | 152 |
| `total_grid_feed` | 158 |
| `power_generation` | 1202 |

All three read a flat 0 through more than a week of normal running,
including days of solar generation, in Home Assistant history and in direct
register reads. They aren't scaled or offset wrongly. The registers simply
don't hold those totals on this model.

`total_ac_consumption` and `total_grid_feed` were only used by the EP2000,
so their names are gone from `FieldName`. `power_generation` stays, because
the AC2A, AC2P, EP600 and AC300 still read it from their own registers.

For kWh totals, use Home Assistant's Integral helper on the power sensors,
such as Total PV Power for solar generation.

## 2.0.4 (2026-10-04)

### EP2000

- **Pack temperature** (`pack_temperature`, register 6115). Reported in
  **°F**, exactly as the device sends it, with the unit declared so Home
  Assistant converts it to each user's own unit. Signed.

  The unit was settled in bluetti-community/bluetti-registers#42. Over an
  idle day the raw value tracked a nearby air sensor at about 1.7 per °C and
  stayed a few degrees below it in shade. That fits °F; °C + 40 would have
  needed the idle pack to run warmer than the air. It also matches a Modbus
  TCP reading against a thermal camera on another EP2000, and BLUETTI's
  documented -40..160 range. 6115 now rides in the normal grouped pack read.

- **Connected devices** (`connected_devices`), counted from the battery's
  own node list at 21002. It replaces `total_node_count` (21001), which was
  always 0 and was never a count. The count reads four entries, so a system
  with more than four devices reports four.

- **Removed:** the raw probe registers 6007 and 6115 from 2.0.2. 6115 became
  the pack temperature; 6007 holds the same value on a single-pack system.

### Node list

`parse_node_list()` and `Node` decode the NODE_INFO block: one 8-word entry
per device, with its Modbus slave address, 64-bit serial and model code.
`DeviceReader.read_nodes()` reads it. Decoded against a real system:

| slave | model | device |
|---|---|---|
| 0 | 3004 | EBOX - the settings controller that writes go to |
| 1 | 1004 | EP2000 inverter |
| 41 | 4001 | HV800 battery pack |

### Raw reads at other slaves

`DeviceReader.read_raw(address, count, slave=1)` can now read at another
slave address. It is still function 3 only. Choosing which slaves are
safe to ask is left to the caller; the integration only allows slaves that
appear in the node list.

## 2.0.3 (2026-10-04)

Adds `DeviceReader.read_raw(address, count)`: a one-off, read-only read of up
to 32 registers, for exploring addresses a device definition does not cover.
No change to polling or writes.

- **Read-only.** It only ever sends Modbus function 3, at the read slave.
- **Shares the polling lock and connection**, so it never interleaves with a
  poll or a write.
- **Refusal and silence are results, not exceptions.** It returns a
  `RawReadResult` whose `outcome` is `ok`, `refused` (with the Modbus
  exception code), `no_reply`, `not_connected` or `error`. Exploring means
  asking for addresses the device may not serve, and neither answer says
  anything is wrong with the link.
- `RawReadResult.as_dict()` gives each word unsigned, signed and in hex.
- Bounds are enforced: 1 to `MAX_RAW_READ_COUNT` (32) registers, within
  0-65535.

Used by bluetti-bt-connect 2.0.4's `read_registers` action.

## 2.0.2 (2026-10-04)

Adds two raw, read-only registers to the EP2000 to look for the battery pack
temperature, which nothing on this device has exposed so far. No other
behaviour changes.

### Why

A Modbus TCP read of an EP2000 (bluetti-community/bluetti-registers#42) shows
a pack average temperature, `b_t_avg` (51224), whose unit is in doubt - °C,
°C with an offset, or °F. Reading the same quantity over Bluetooth at the same
moment settles it. The BLUETTI app's register list points at two candidates:

| field | register | where |
|---|---|---|
| `raw_register_6007` | 6007 | pack main-info block (6000+) |
| `raw_register_6115` | 6115 | directly after pack SOH (6114) |

Both are reported **exactly as the device sends them** - no scaling, offset
or bounds - and named by address, because what they hold is not confirmed.

### How they are read safely

Neither address had been read on this device before. A device can answer an
address it does not serve with silence instead of an error, and a silent
register inside an ordinary read would time out and fail the whole poll. So
these use a new `ProbeUIntField`:

- **Never merged into grouped reads.** Each is read on its own, after
  everything else in the poll.
- **A refusal or a timeout only drops that register.** The rest of the poll is
  unaffected, and the register is not asked for again until the integration
  restarts - an unserved address costs one failed request, not one per poll.
- **A real connection failure still aborts the poll as before.**

Read-only: nothing is written to the device. Reads use slave 1, like every
other read.

## 2.0.1 (2026-09-29)

Maintenance release. **No code or behaviour changes** - the library is
identical to 2.0.0, so there is nothing to update in Home Assistant.

### Changes

- **PyPI project links now point to the new GitHub account**
  (`https://github.com/Ellmondo/bluetti-bt-connect-lib`). PyPI only reads
  links from a published release, so 2.0.0 kept showing the old username.
- **Release workflow updated to current GitHub Actions** (`checkout@v7`,
  `setup-python@v7`, `upload-artifact@v7`, `download-artifact@v8`), which run
  on Node.js 24 and clear the Node.js 20 deprecation warnings.
- **Runners pinned to `ubuntu-24.04`** instead of `ubuntu-latest`, so the
  build environment only changes when the workflow is deliberately updated.

## 2.0.0 (2026-09-21)

**Local write control of the Bluetti EP2000 (+ EBOX) works.** The long-standing
"a write is accepted and then silently reverts" problem is solved. Settings
written over local Bluetooth - grid import/export limits, working mode, the
switches - now persist. This is the headline of 2.0.

### TL;DR

- **The fix: writes go to Modbus slave 0, not slave 1.** On this 2nd-generation
  IoT device the inverter answers on slave 1, but the authoritative settings
  controller is on slave 0. A write to slave 1 was echoed by the inverter and
  then overwritten within a few seconds from the slave-0 setpoint. Writing to
  slave 0 makes it stick. Reads stay on slave 1 (they work and reflect slave-0
  writes after a few seconds).
- **New "EMS / AI Control Mode" switch** (register 2241). When on, Bluetti's AI
  actively manages the system and overrides manual settings; off = manual control.
- **Grid import entities now appear** - their read bounds were too low and were
  silently discarding the device's real values.
- **The v1.8.0 register-7 "unlock" experiment is removed** - it was a wrong turn
  (register 7 sets the Bluetooth password; it is not a session login).

### The story (why this took a while)

Writes into the EP2000's grid/mode registers came back with a clean, protocol-
valid acknowledgement and then didn't take effect. Earlier releases suspected a
cloud/MQTT commit step, a licensed BLE encryption handshake, or a "Pro Mode"
authentication gate, because that is the industry-wide pattern for grid-facing
settings. Several hypotheses were tested on real hardware and ruled out:

- **Register 7 "unlock"** (v1.8.0) - decompiling the app showed register 7 is the
  *set Bluetooth password* action from the settings screen, not a login. Writing
  to it did nothing for persistence. Removed.
- **EMS/AI control mode alone** - turning it off helped at slave 1 briefly but the
  value still reverted; not the root cause on its own.
- **A commit pulse** (`CTRL_EVENT` 2006, `CTRL_POWER_OUTPUT_STATE_SAVE` 2226) -
  the value still reverted after sending them.
- **A slow revert** - ruled in as a symptom; the value snapped back to an enforced
  setpoint within seconds.

The decisive test wrote the same register at **slave 0** and watched it: it held,
and read back correct at slave 0, propagating to the slave-1 reading after ~5s -
exactly the delay the official app shows before it opens its settings screen. The
app itself writes settings to `getSettingsSlaveAddr()`, which returns 0 for
2nd-generation IoT devices. Mystery solved: it was the slave address all along.

### Changes since 1.7.0

#### Fixed
- **Settings writes now target Modbus slave 0 on the EP2000** so they persist.
  Added a configurable `BluettiDevice.write_slave_addr` (default 1, so no other
  device changes behaviour), threaded through `DeviceRegister`,
  `WriteableRegister` and `WriteableRegisters`; EP2000 sets it to 0.
- **Max Grid Import Power / Current now read correctly.** Their bounds were capped
  at 6600 W / 40 A - values taken from an earlier, lower app setting - so the
  device's real values (8600 W / 45 A) were being discarded by range validation
  and the entities showed unavailable. Raised to 9600 W / 50 A.
- **Working Mode stays at register 2005** (corrected in 1.6/1.7 from the old 2013,
  which is the device power on/off control).

#### Added
- **`ValueSwitchField`** - a switch whose on/off states are arbitrary register
  values rather than 1/0.
- **EMS / AI Control Mode** field on the EP2000 (register 2241, on=8 / off=0),
  surfaced as a switch by the integration.
- **Standalone BLE diagnostics** at the repo root (`scan_addr.py`,
  `probe_slave.py`, `probe_grid.py`, `probe_writepath.py`, `probe_commit.py`) -
  raw-Modbus tools, no library import needed, used to find and confirm the fix.

#### Removed
- The register-7 "unlock" feature added in 1.8.0 (`unlock.py`, the DeviceReader
  hook and config field, and its test). It never authorised anything.

#### Verified register map (EP2000, against the app's `ProtocolAddrV2`)
`2005` working mode - `2213` grid import power (W) - `2214` grid import current (A)
- `2215` grid export power (W) - `2216` grid export current (A) - `2207` charge
from grid - `2208` grid export enable - `2241` EMS/AI control (8=on).

### Upgrading

`pip install -U bluetti-bt-connect-lib` (or let the companion Home Assistant
integration pull `==2.0.0`). After changing a setting, allow a few seconds for it
to settle before trusting the read-back. For manual control, keep AI Control Mode
**off**.

### Credits

Built on [bluetti-bt-lib](https://github.com/Patrick762/bluetti-bt-lib) by
[Patrick762](https://github.com/Patrick762) and the upstream contributors, with
`bluetti_mqtt` and [atiweb/hassio-bluetti-bt](https://github.com/atiweb/hassio-bluetti-bt)
cross-referenced for specific fixes. This release's EP2000 work is device-specific
and intended to feed back upstream where it generalises.
