"""Setup for pypi package"""

import os
import codecs
import re
from setuptools import setup, find_packages

here = os.path.abspath(os.path.dirname(__file__))

with codecs.open(os.path.join(here, "README.md"), encoding="utf-8") as fh:
    long_description = "\n" + fh.read()


def read_version() -> str:
    """Read __version__ out of the package without importing it.

    Importing would pull in bleak and friends, which are not necessarily
    installed while the package is being built.
    """
    init = os.path.join(here, "bluetti_bt_connect_lib", "__init__.py")
    with codecs.open(init, encoding="utf-8") as handle:
        match = re.search(
            r'^__version__\s*=\s*["\']([^"\']+)["\']', handle.read(), re.MULTILINE
        )

    if match is None:
        raise RuntimeError("Could not find __version__ in bluetti_bt_connect_lib")

    return match.group(1)


# The release workflow sets LIB_VERSION from the git tag. Falling back to the
# in-package __version__ means an install straight from git still reports a
# real version - without it setuptools substitutes 0.0.0, which leaves Home
# Assistant unable to tell a stale copy from a current one.
VERSION = os.getenv("LIB_VERSION") or read_version()
DESCRIPTION = "Bluetti BT Connect - Bluetooth library for Bluetti power stations (fork of bluetti-bt-lib by Patrick762)"

# Setting up
setup(
    name="bluetti-bt-connect-lib",
    version=VERSION,
    author="Ellmondo",
    description=DESCRIPTION,
    long_description_content_type="text/markdown",
    long_description=long_description,
    url="https://github.com/Ellmondo/bluetti-bt-connect-lib",
    packages=find_packages(exclude=["tests", "tests.*"]),
    python_requires=">=3.11",
    # asyncio and logging were listed here but are both standard library. On
    # PyPI they resolve to unrelated packages: "asyncio" is a placeholder whose
    # own description says not to install it, and "logging" is the Python 2
    # original from 2004, which drops a top-level logging/ package into
    # site-packages. Neither belongs in a dependency list.
    install_requires=[
        "async_timeout",
        "bleak",
        "bleak_retry_connector",
        "crcmod",
        # The encrypted protocol (bluetooth/encryption.py).
        "cryptography",
        "pyasn1",
        # utils.bleak_client_mock needs collections.abc.Buffer, which only
        # exists from 3.12 onwards.
        'typing_extensions; python_version < "3.12"',
    ],
    keywords=[],
    entry_points={
        "console_scripts": [
            "bluetti-scan = bluetti_bt_connect_lib.scripts.bluetti_scan:start",
            "bluetti-detect = bluetti_bt_connect_lib.scripts.bluetti_detect:start",
            "bluetti-read = bluetti_bt_connect_lib.scripts.bluetti_read:start",
            "bluetti-readall = bluetti_bt_connect_lib.scripts.bluetti_readall:start",
            "bluetti-write = bluetti_bt_connect_lib.scripts.bluetti_write:start",
            "bluetti-parse = bluetti_bt_connect_lib.scripts.bluetti_parse:start",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
    ],
)
