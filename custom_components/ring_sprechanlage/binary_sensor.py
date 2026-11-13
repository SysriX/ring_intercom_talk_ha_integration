"""binary_sensor: an, solange ein Gerät mit der Intercom spricht."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import find_intercoms
from .camera import RETRY_MAX, RETRY_SECONDS
from .session import SessionTracker, get_tracker


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    added: set[int] = set()

    @callback
    def _scan(attempt: int = 0) -> None:
        new = [d for d in find_intercoms(hass) if d.device_api_id not in added]
        for d in new:
            added.add(d.device_api_id)
        if new:
            async_add_entities(
                [GespraechAktiv(d, get_tracker(hass, d.device_api_id)) for d in new]
            )
        elif not added and attempt < RETRY_MAX:
            async_call_later(hass, RETRY_SECONDS, callback(lambda _now: _scan(attempt + 1)))

    _scan()


class GespraechAktiv(BinarySensorEntity):
    """Gespräch mit der Intercom offen."""

    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:phone-in-talk"

    def __init__(self, device, sessions: SessionTracker) -> None:
        self._sessions = sessions
        self._device_api_id = device.device_api_id
        self._attr_name = f"{device.name} Gespräch aktiv"
        self._attr_unique_id = f"ring_sprechanlage_gespraech_{device.device_api_id}"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._sessions.add_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool:
        return self._sessions.active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"sitzungen": self._sessions.count}
