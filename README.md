# bluetti-bt-connect-lib

> **This is a fork** of [bluetti-bt-lib](https://github.com/Patrick762/bluetti-bt-lib) by [Patrick762](https://github.com/Patrick762), extended with additional device support, register corrections, and new writable fields for the Bluetti EP2000. All credit for the original protocol reverse-engineering, architecture, and core library design goes to Patrick762 and the project's other contributors. This fork exists to track device-specific fixes and additions on a faster iteration cycle; where possible, improvements are intended to be contributed back upstream.
>
> Original repository: https://github.com/Patrick762/bluetti-bt-lib
>
> Additional credit to [atiweb/hassio-bluetti-bt](https://github.com/atiweb/hassio-bluetti-bt), a separate fork of the original project, cross-referenced for two specific fixes: correcting `consumption_power_all`, `pv_input_power_all`, and `grid_power_all` from incorrectly-assumed 32-bit fields to plain 16-bit fields (validated against real captured data), and the pattern for proper Modbus response validation (CRC checking and exception-response detection) now used in `device_reader.py`.

Inofficial Library for basic communication to bluetti powerstations.
Core functions based on https://github.com/warhammerkid/bluetti_mqtt

## Disclaimer
This library is provided without any warranty or support by Bluetti. I do not take responsibility for any problems it may cause in all cases. Use it at your own risk.

## Encrypted devices (v2.1)

Newer BLUETTI units (the Apex 300 among them) wrap the protocol in an
encrypted session: an ECDH key exchange followed by AES-CBC. This build
restores the encryption layer that bluetti-bt-lib 0.1.7 had and that
bluetti-bt-connect-lib 2.0 removed, ported onto the current reader. It needs
no BLUETTI account and no cloud connection.

- `recognize_device()` detects encryption by itself and reports it as
  `DeviceRecognizerResult.encrypted`.
- `DeviceReaderConfig(use_encryption=True)` reads an encrypted device.
  `use_encryption=None` detects it per connection.
- A key exchange runs on every new connection, so hold the link between polls
  with a `DeviceConnection` and `keep_alive_seconds`.
- Checked against a real Apex 300 (`AP300`). It serves its data at Modbus
  slave 0; slave 1 answers with every register zero.
- Writes: use `DeviceReader.write()`, which sends the request inside the
  encrypted session and reports the device's answer as a `WriteResult`. The
  older `DeviceWriter` sends plain frames on its own connection and does not
  work on an encrypted device. If the key exchange does not complete,
  nothing is sent. `bluetti-write -e true` uses this path.
- **Writing to an encrypted device has not been tried on hardware** (v2.2).
  It is covered by tests against a simulated device only. The Apex 300 has
  two writable fields, `ctrl_ac` and `ctrl_dc`.

The encryption code is based on
[nhurman/bluetti_mqtt](https://github.com/nhurman/bluetti_mqtt) by way of
bluetti-bt-lib.

## ✅ EP2000: full local write control (v2.0)

Earlier versions of this fork carried a long warning that grid and working-mode
writes were accepted by the device and then silently reverted, and speculated it
was a cloud/authentication or "Pro Mode" gate. **That is solved as of v2.0**, and
the cause turned out to be mundane: this is a 2nd-generation IoT device whose
authoritative settings controller sits on **Modbus slave 0**, while the inverter
answers on **slave 1**. The fork was writing to slave 1 - the inverter echoed the
write and then overwrote it within a few seconds from the slave-0 setpoint.
Writing to slave 0 makes the change persist. This matches the official Bluetti
app, which sends settings to slave 0 on this device class; the few-second delay
you may notice before a change shows up is the value propagating back to the
slave-1 reading (the same delay the app shows before it opens its settings
screen).

**What this means on the EP2000 now:**

- **Grid export power/current, grid import power/current, working mode, AC
  output, charge-from-grid and grid-export enables all write and persist** over
  local BLE. Give a write a few seconds to settle before trusting the read-back.
- **Battery SOC limits** (`soc_range_start` 2022, `soc_range_end` 2023) are
  writable from 2.0.9, 5-100% in 1% steps, written to slave 0 like BLUETTI's
  app does.
- A new **AI Control Mode** switch exposes register 2241. When Bluetti's AI/EMS
  is on it actively manages the system and will override your manual settings
  (the old "accept-then-revert" you would then see). Leave it **off** for manual
  control; the switch lets you see and change that state from Home Assistant.

**Still worth knowing:**

- **Grid export and grid-protection parameters are regulated for interconnection
  safety in most jurisdictions** (anti-islanding, voltage/frequency ride-through).
  Whether or not a write persists, changing them may carry real compliance
  implications depending on where you live and how your system is connected.
- Reliability can still vary by firmware version and by unit. This is confirmed
  on an EP2000 + EBOX; if a control misbehaves on your setup, the standalone BLE
  diagnostics below will show you exactly what the device is doing.

Full technical detail - the investigation and the register-level evidence - is in
the [2.0.0 section of the changelog](https://github.com/Ellmondo/bluetti-bt-connect-lib/blob/main/CHANGELOG.md#200-2026-09-21).

## Projects using this library

- [Bluetti BT Connect - Home Assistant Integration](https://github.com/Ellmondo/bluetti-bt-connect) (this fork's companion integration)
- [Original Home Assistant Integration](https://github.com/Patrick762/hassio-bluetti-bt)
- [UPS Server (NUT compatible)](https://github.com/Patrick762/nut-server-bluetti)

## Supported Powerstations and data

Validated

|Device Name|total_battery_percent|dc_input_power|ac_input_power|dc_output_power|ac_output_power|
|-----------|---------------------|--------------|--------------|---------------|---------------|
|AC70       |✅                   |✅            |✅            |✅             |✅             |
|AC180      |✅                   |✅            |✅            |✅             |✅             |
|EB3A       |✅                   |✅            |✅            |✅             |✅             |
|EP600      |✅                   |PV            |Grid          |❌             |AC Phases      |
|EP2000     |✅                   |PV            |Grid          |❌             |AC Phases; writable grid import/export + working mode, persist via slave 0 (see v2.0 notes)|
|Handsfree 1|✅                   |✅            |✅            |✅             |✅             |

Added and mostly validated by contributors (some are moved here from the HA Integration https://github.com/Patrick762/hassio-bluetti-bt):


|Device Name|Contributor                                                                        |total_battery_percent|dc_input_power|ac_input_power|dc_output_power|ac_output_power|
|-----------|-----------------------------------------------------------------------------------|---------------------|--------------|--------------|---------------|---------------|
|AC2A       |[@ruanmed](https://github.com/ruanmed)                                             |✅                   |✅            |✅            |✅             |✅             |
|AC50B      |[@goetzc](https://github.com/goetzc)                                               |✅                   |❌            |✅            |✅             |✅             |
|AC60       |[@mzpwr](https://github.com/mzpwr)                                                 |✅                   |✅            |✅            |✅             |✅             |
|AC60P      |[@mzpwr](https://github.com/mzpwr)                                                 |✅                   |✅            |✅            |✅             |✅             |
|AC70P      |[@matthewpucc](https://github.com/matthewpucc)                                     |✅                   |✅            |✅            |✅             |✅             |
|AC180P     |@Patrick762                                                                        |✅                   |✅            |✅            |✅             |✅             |
|AC200L     |bluetti-mqtt                                                                       |✅                   |✅            |✅            |✅             |✅             |
|AC200M     |bluetti-mqtt                                                                       |✅                   |✅            |✅            |✅             |✅             |
|AC200PL    |[@0x4E4448](https://github.com/0x4E4448)                                           |✅                   |✅            |✅            |✅             |✅             |
|AC300      |bluetti-mqtt                                                                       |✅                   |✅            |✅            |✅             |✅             |
|AC500      |bluetti-mqtt                                                                       |✅                   |✅            |✅            |✅             |✅             |
|AP300      |[@seaburger](https://github.com/seaburger), [@sidieje](https://github.com/sidieje) |✅                   |✅            |✅            |✅             |✅             |
|EL30V2     |[@dgudim](https://github.com/dgudim)                                               |✅                   |✅            |✅            |✅             |✅             |
|EL100V2    |[@seaburger](https://github.com/seaburger)                                         |✅                   |✅            |✅            |✅             |✅             |
|EP500      |bluetti-mqtt                                                                       |✅                   |✅            |✅            |✅             |✅             |
|EP500P     |bluetti-mqtt                                                                       |✅                   |✅            |✅            |✅             |✅             |
|EP760      |[@Apfuntimes](https://github.com/Apfuntimes)                                       |✅                   |PV            |Grid          |❌             |AC Phases      |
|EP800      |[@jhagenk](https://github.com/jhagenk)                                             |✅                   |❌            |❌            |❌             |❌             |
|PR30V2     |@gentoo90                                                                          |✅                   |✅            |✅            |✅             |✅             |
|PR100V2    |shares PR30V2 register layout (pending validation)                                 |✅                   |✅            |✅            |✅             |✅             |

## Controls

Validated:

|Device Name|ctrl_ac|ctrl_dc|
|-----------|-------|-------|
|EB3A       |✅     |✅     |

Added and mostly validated by contributors:
|Device Name|Contributor                                              |ctrl_ac|ctrl_dc|ctrl_ups_mode|soc_range_start|soc_range_end|
|-----------|---------------------------------------------------------|-------|-------|-------------|---------------|-------------|
|AC200L     |bluetti-mqtt, [@seaburger](https://github.com/seaburger) |✅     |✅     |✅           |❌             |❌           |
|EL30V2     |[@x3ccd4828](https://github.com/x3ccd4828)               |✅     |✅     |❌           |❌             |❌           |

## Battery pack data

|Device Name|voltage|battery_soc|cell_voltages|
|-----------|-------|-----------|-------------|
|AC300      |✅     |✅         |✅           |

## Installation

```bash
pip install bluetti-bt-connect-lib
```

## Commands for testing

Commands included in this library should only be used for testing.

### Scan for supported devices

```bash
usage: bluetti-scan [-h] [-r REGEX] [-s SCAN_TIME]

Detect bluetti devices by bluetooth name

options:
  -h, --help            show this help message and exit
  -r REGEX, --regex REGEX
                        Custom regex to match device name
  -s SCAN_TIME, --scan-time SCAN_TIME
                        How long to scan for devices (seconds)
```

Example output: `['EB3A', '00:00:00:00:00:00']`

### Detect device type by mac address

```bash
usage: bluetti-detect [-h] mac

Detect bluetti devices

positional arguments:
  mac         Mac-address of the powerstation

options:
  -h, --help  show this help message and exit
```

Example:

```bash
bluetti-detect 00:00:00:00:00:00
```

Example output: `Device type is 'EB3A' with iot version 1 and serial 0000000000000. Full name: EB3A0000000000000`

### Read device data for supported devices

```bash
usage: bluetti-read [-h] [-m MAC] [-t TYPE] [-e ENCRYPTION]

Detect bluetti devices

options:
  -h, --help            show this help message and exit
  -m MAC, --mac MAC     Mac-address of the powerstation
  -t TYPE, --type TYPE  Type of the powerstation (AC70 f.ex.)
  -e ENCRYPTION, --encryption ENCRYPTION
                        Add this if encryption is needed
```

Example:

```bash
bluetti-read -m 00:00:00:00:00:00 -t EB3A
```

Example output:
```bash
FieldName.DEVICE_TYPE: EB3A
FieldName.DEVICE_SN: 0000000000000
FieldName.BATTERY_SOC: 92%
FieldName.DC_INPUT_POWER: 0W
FieldName.AC_INPUT_POWER: 0W
FieldName.AC_OUTPUT_POWER: 0W
FieldName.DC_OUTPUT_POWER: 0W
FieldName.CTRL_AC: False
FieldName.CTRL_DC: True
FieldName.CTRL_LED_MODE: LedMode.OFF
FieldName.CTRL_POWER_OFF: False
FieldName.CTRL_ECO: False
FieldName.CTRL_ECO_TIME_MODE: EcoMode.HOURS1
FieldName.CTRL_CHARGING_MODE: ChargingMode.STANDARD
FieldName.CTRL_POWER_LIFTING: False
```

### Write to supported device

INFO: Writing to devices with encryption (`-e true`) has not been tested on hardware!

```bash
usage: bluetti-write [-h] [-m MAC] [-t TYPE] [--on ON] [--off OFF] [-v VALUE] [-e ENCRYPTION] field

Write to bluetti device

positional arguments:
  field                 Field name (ctrl_dc f.ex.)

options:
  -h, --help            show this help message and exit
  -m MAC, --mac MAC     Mac-address of the powerstation
  -t TYPE, --type TYPE  Type of the powerstation (AC70 f.ex.)
  --on ON               Value to write
  --off OFF             Value to write
  -v VALUE, --value VALUE
                        Value to write (integer, see enum for value)
  -e ENCRYPTION, --encryption ENCRYPTION
                        Add this if encryption is needed
```

Example:

```bash
bluetti-write -m 00:00:00:00:00:00 -t EB3A --on on ctrl_ac
```

## Standalone BLE diagnostics (repo scripts)

These live at the repo root and are self-contained (raw Modbus over the GATT
write characteristic - no library import needed). They were written while
diagnosing the slave-0 write behaviour and are kept for future debugging. Run
them from a venv that has `bleak`, `bleak-retry-connector` and `crcmod`
installed, with the device free (turn off any "Hold Bluetooth connection" switch
in Home Assistant first so the station is not already connected).

- `scan_addr.py` - list nearby BLE devices and their addresses (on macOS the
  address is a CoreBluetooth UUID).
- `probe_grid.py` - dump the grid import/export registers at both slaves, sweep
  nearby, and optionally test that an import write persists (`--test-import N`).
- `probe_slave.py` - A/B a write at slave 0 vs slave 1 with read-back (this is
  the one that pinned down the fix).
- `probe_writepath.py` - test write strategies (EMS on/off, slave, commit pulse).
- `probe_commit.py` - write a value, then watch it for ~45s to catch a slow
  revert and test commit pulses.

```bash
python probe_grid.py --mac <address>
python probe_slave.py --mac <address> --reg 2215 --value 1500
```

Get `<address>` from `scan_addr.py` or `bluetti-scan`.

## Adding fields

To add new fields, you can use the `bluetti-detect` command to first find out which version of iot protocol is used and if it uses encryption.

After you got this information, you can use the `bluetti-readall` command to read every registry and save the data to a json file. You should also note all values you see in the app to later compare the data.

Here's how to use the `bluetti-readall` command:

```bash
usage: bluetti-readall [-h] [-m MAC] [-v VERSION] [-e ENCRYPTION]

Detect bluetti devices

options:
  -h, --help            show this help message and exit
  -m MAC, --mac MAC     Mac-address of the powerstation
  -v VERSION, --version VERSION
                        IoT protocol version
  -e ENCRYPTION, --encryption ENCRYPTION
                        Add this if encryption is needed
```

With the separate tool at [bluetti-bt-raw-reader](https://github.com/Patrick762/bluetti-bt-raw-reader) you can view those values in a more understandable way.

You can also share the output with me using [this form](https://forms.gle/ewp7DYigtaN3ZLc68)


To test added fields with the created json file, use `bluetti-parse`:

```bash
usage: bluetti-parse [-h] file

Parse readall output files

positional arguments:
  file        JSON file of the powerstation readall output

options:
  -h, --help  show this help message and exit
```
