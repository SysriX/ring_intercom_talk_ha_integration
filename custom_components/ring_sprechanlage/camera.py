"""camera-Plattform: WebRTC-Vermittler für das Gegensprechen.

Kein Bild, keine Snapshots – die Entität existiert, weil HA die
WebRTC-Signalisierung (camera/webrtc/offer) nur für Kameras anbietet.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ring_doorbell.webrtcstream import RingWebRtcMessage

from homeassistant.components.camera import (
    Camera,
    CameraEntityFeature,
    RTCIceCandidateInit,
    WebRTCAnswer,
    WebRTCCandidate,
    WebRTCError,
    WebRTCSendMessage,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType

from . import find_intercoms
from .const import CONF_VIDEO_ANFORDERN, DATA_CONFIG, DOMAIN, KIND_AUDIO
from .session import get_tracker

_LOGGER = logging.getLogger(__name__)

RETRY_SECONDS = 60
RETRY_MAX = 15


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Intercoms suchen; ist Ring noch nicht geladen, später erneut versuchen."""
    added: set[int] = set()

    @callback
    def _scan(attempt: int = 0) -> None:
        new = [d for d in find_intercoms(hass) if d.device_api_id not in added]
        for d in new:
            added.add(d.device_api_id)
            _LOGGER.info("Ring Intercom gefunden: %s (%s, id %s)", d.name, d.kind, d.device_api_id)
        if new:
            async_add_entities([RingSprechCamera(hass, d) for d in new])
        elif not added and attempt < RETRY_MAX:
            async_call_later(hass, RETRY_SECONDS, callback(lambda _now: _scan(attempt + 1)))
        elif not added:
            _LOGGER.warning("Keine Ring Intercom gefunden – ist die Ring-Integration geladen?")

    _scan()


class RingSprechCamera(Camera):
    """Gegensprech-Entität einer Ring Intercom."""

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, device) -> None:
        super().__init__()
        self._device_api_id = device.device_api_id
        self._kind = device.kind
        self._audio_only = device.kind == KIND_AUDIO
        self._sessions = get_tracker(hass, device.device_api_id)
        self._streams: dict[str, Any] = {}
        self._attr_name = f"{device.name} Gegensprechen"
        self._attr_unique_id = f"ring_sprechanlage_{device.device_api_id}"
        self._attr_icon = "mdi:phone-in-talk"
        self._attr_brand = "Ring"
        self._attr_model = "Intercom" if self._audio_only else "Intercom Video"
        self._attr_supported_features = CameraEntityFeature.STREAM

    def _device(self):
        """Aktuelles Geräteobjekt (nach einem Reload der Ring-Integration neu)."""
        for d in find_intercoms(self.hass):
            if d.device_api_id == self._device_api_id:
                return d
        return None

    @property
    def available(self) -> bool:
        return self._device() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "device_id": self._device_api_id,
            "device_kind": self._kind,
            "audio_only": self._audio_only,
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._sessions.add_listener(self.async_write_ha_state))

    @property
    def is_streaming(self) -> bool:
        return self._sessions.active

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        return None

    # ---- WebRTC-Signalisierung ----

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Angebot des Browsers an Ring weiterreichen."""
        device = self._device()
        if device is None:
            send_message(WebRTCError("unavailable", "Ring Intercom nicht geladen"))
            return

        t_start = time.monotonic()
        seen = {"answer": False}

        def _on_ring(msg: RingWebRtcMessage) -> None:
            if msg.error_code:
                _LOGGER.debug("Sitzung %s: Ring meldet %s %s", session_id, msg.error_code, msg.error_message)
                send_message(WebRTCError(str(msg.error_code), msg.error_message or "Ring hat die Verbindung beendet"))
            elif msg.answer:
                if not seen["answer"]:
                    seen["answer"] = True
                    _LOGGER.debug("Sitzung %s: Antwort von Ring nach %d ms", session_id, (time.monotonic() - t_start) * 1000)
                send_message(WebRTCAnswer(msg.answer))
            elif msg.candidate:
                send_message(
                    WebRTCCandidate(
                        RTCIceCandidateInit(msg.candidate, sdp_m_line_index=msg.sdp_m_line_index or 0)
                    )
                )

        conf = self.hass.data[DOMAIN][DATA_CONFIG]
        video = not self._audio_only or conf.get(CONF_VIDEO_ANFORDERN, False)
        stream_cls = self.hass.data[DOMAIN]["stream_class"]

        async def _on_close() -> None:
            # Ring hat aufgelegt
            self._streams.pop(session_id, None)
            self._sessions.discard(session_id)

        stream = stream_cls(
            device._ring,
            device.device_api_id,
            stream_options={"audio_enabled": True, "video_enabled": video},
            on_message_callback=_on_ring,
            keep_alive_timeout=None,
            on_close_callback=_on_close,
        )
        self._streams[session_id] = stream
        self._sessions.add(session_id)
        _LOGGER.debug("Sitzung %s: Angebot an Ring (video=%s)", session_id, video)
        try:
            await stream.generate(offer_sdp)
        except Exception:
            # HA ruft close_webrtc_session für nie gestartete Sitzungen nicht auf
            self._streams.pop(session_id, None)
            self._sessions.discard(session_id)
            raise

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit
    ) -> None:
        """ICE-Kandidat des Browsers an Ring."""
        if candidate.sdp_m_line_index is None:
            return
        if stream := self._streams.get(session_id):
            await stream.on_ice_candidate(candidate.candidate, candidate.sdp_m_line_index)

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        """Browser hat aufgelegt bzw. die Verbindung verloren."""
        self._sessions.discard(session_id)
        if stream := self._streams.pop(session_id, None):
            stream.sync_close()
