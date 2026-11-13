"""Offene Gespräche je Intercom, geteilt zwischen camera und binary_sensor.

Die Intercom hat nur einen Sprechweg: es kann immer nur ein Gerät abheben.
Der Zähler macht sichtbar, ob gerade jemand spricht (und ob eine Sitzung hängt).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .const import DATA_SESSIONS, DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class SessionTracker:
    """Offene Browser-Sitzungen einer Intercom."""

    def __init__(self) -> None:
        self._sessions: set[str] = set()
        self._listeners: list[Callable[[], None]] = []

    @property
    def active(self) -> bool:
        return bool(self._sessions)

    @property
    def count(self) -> int:
        return len(self._sessions)

    def add(self, session_id: str) -> None:
        if session_id not in self._sessions:
            self._sessions.add(session_id)
            self._notify()

    def discard(self, session_id: str) -> None:
        if session_id in self._sessions:
            self._sessions.discard(session_id)
            self._notify()

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()


def get_tracker(hass: HomeAssistant, device_api_id: int) -> SessionTracker:
    """Tracker einer Intercom, beim ersten Zugriff angelegt."""
    trackers: dict[int, SessionTracker] = hass.data.setdefault(DOMAIN, {}).setdefault(
        DATA_SESSIONS, {}
    )
    return trackers.setdefault(device_api_id, SessionTracker())
