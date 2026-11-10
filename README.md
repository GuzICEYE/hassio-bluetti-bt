# hassio-bluetti-bt
[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate with hassfest](https://github.com/GuzICEYE/hassio-bluetti-bt/actions/workflows/hassfest_validation.yml/badge.svg)](https://github.com/GuzICEYE/hassio-bluetti-bt/actions/workflows/hassfest_validation.yml)
[![HACS Action](https://github.com/GuzICEYE/hassio-bluetti-bt/actions/workflows/HACS.yml/badge.svg)](https://github.com/GuzICEYE/hassio-bluetti-bt/actions/workflows/HACS.yml)

Bluetti Integration for Home Assistant

> **Fork notice:** this is a fork of [Patrick762/hassio-bluetti-bt](https://github.com/Patrick762/hassio-bluetti-bt).
> It uses [bluetti-bt-connect-lib](https://github.com/Ellmondo/bluetti-bt-connect-lib) (a maintained fork of bluetti-bt-lib)
> instead of `bluetti-bt-lib==0.1.7`, whose package metadata declares the standard-library modules `asyncio` and `logging`
> as PyPI dependencies. Those names resolve to an unrelated 2015 backport and a Python 2 era package, so the install fails
> inside Home Assistant and the integration never loads (`RequirementsNotFound`). bluetti-bt-connect-lib declares only
> real dependencies and is a drop-in replacement for the API this integration uses.
>
> **Encrypted devices:** since 0.4.0 this fork reads encrypted devices such as the Apex 300 (`AP300`) locally over
> Bluetooth, with no BLUETTI account or cloud. It installs bluetti-bt-connect-lib from the
> [`bluetti-bt-connect-lib` branch](https://github.com/GuzICEYE/hassio-bluetti-bt/tree/bluetti-bt-connect-lib) of this
> repository, which restores the encryption layer the library had dropped. The Bluetooth link to an encrypted device is
> held open between polls, so the BLUETTI app cannot connect to it over Bluetooth at the same time.
>
> **Controls on encrypted devices (0.5.0, experimental):** encrypted devices are read-only by default. The integration
> options of an encrypted device have a switch that creates its controls (on the Apex 300: AC and DC output) after a
> restart. A write is sent inside the encrypted session and counts as done only when the device confirms it; anything
> else raises an error in Home Assistant. **This has not been tried on hardware yet.**

The current [Roadmap for this project and the library can be found here](https://github.com/users/Patrick762/projects/4)

## Disclaimer
This integration is provided without any warranty or support by Bluetti. I do not take responsibility for any problems it may cause in all cases. Use it at your own risk.

## Installation
To install this integration, you first need [HACS](https://hacs.xyz/) installed.
After the installation, you can use this button to install the integration:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=GuzICEYE&repository=hassio-bluetti-bt&category=integration)

### Supported devices:

See [bluetti-bt-connect-lib](https://github.com/Ellmondo/bluetti-bt-connect-lib?tab=readme-ov-file#supported-powerstations-and-data)

### Available controls:
See [bluetti-bt-connect-lib](https://github.com/Ellmondo/bluetti-bt-connect-lib?tab=readme-ov-file#supported-powerstations-and-data)

### Adding devices or fields

Please use the issue template at [bluetti-bt-connect-lib](https://github.com/Ellmondo/bluetti-bt-connect-lib?tab=readme-ov-file#supported-powerstations-and-data)
