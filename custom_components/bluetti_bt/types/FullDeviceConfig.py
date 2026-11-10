from typing import Any, Dict

from .InitialDeviceConfig import InitialDeviceConfig
from .OptionalDeviceConfig import OptionalDeviceConfig


class FullDeviceConfig:
    def __init__(
        self,
        initial: InitialDeviceConfig,
        optional: OptionalDeviceConfig,
    ):
        self.address = initial.address
        self.name = initial.name
        self.dev_type = initial.dev_type
        self.use_encryption = initial.use_encryption
        self.polling_interval = optional.polling_interval
        self.polling_timeout = optional.polling_timeout
        self.max_retries = optional.max_retries
        self.encrypted_controls = optional.encrypted_controls

    @property
    def controls_enabled(self) -> bool:
        """Whether switches and selects are created for this device.

        Always for a plain device. For an encrypted one only when the user
        opted in, because writing to encrypted devices is untested on
        hardware; until then its writable fields show as read-only entities.
        """
        return not self.use_encryption or self.encrypted_controls

    @staticmethod
    def from_dict(raw: Dict[str, Any]):
        initial = InitialDeviceConfig.from_dict(raw)

        if initial is None:
            return None

        return FullDeviceConfig(
            initial,
            OptionalDeviceConfig.from_dict(raw),
        )
