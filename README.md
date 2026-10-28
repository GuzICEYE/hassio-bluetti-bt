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
