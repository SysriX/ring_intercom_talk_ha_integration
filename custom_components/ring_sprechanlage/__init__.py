"""Ring Sprechanlage – Gegensprechen mit der Ring Intercom über das HA-Dashboard.

Die offizielle Ring-Integration legt für die Intercom nur Klingel- und
Türöffner-Entitäten an. Diese Komponente ergänzt eine camera-Entität, die als
WebRTC-Vermittler dient: Der Browser baut die Sprechverbindung direkt mit Ring
auf, Home Assistant reicht nur Angebot/Antwort/ICE weiter (HA-WebRTC-API der
camera-Plattform – eine reine Audio-Entität gibt es dafür nicht).

Basiert auf https://github.com/cmos486/ring-intercom-video (Apache-2.0),
reduziert auf Gegensprechen und ergänzt um die reine Audio-Intercom
(intercom_handset_audio).

configuration.yaml:
    ring_sprechanlage:
      video_anfordern: false   # optional; true = Ring wie bei der Video-Intercom auch Bild anbieten
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import uuid
from json import dumps as json_dumps

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, discovery
from homeassistant.helpers.typing import ConfigType

from .const import CONF_VIDEO_ANFORDERN, DATA_CONFIG, DOMAIN, INTERCOM_KINDS

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CAMERA, Platform.BINARY_SENSOR]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Any(
            None,
            vol.Schema({vol.Optional(CONF_VIDEO_ANFORDERN, default=False): cv.boolean}),
        )
    },
    extra=vol.ALLOW_EXTRA,
)


def find_intercoms(hass: HomeAssistant) -> list:
    """Alle Intercoms aus den geladenen Ring-Einträgen (aktuelle Objekte)."""
    found = []
    for entry in hass.config_entries.async_entries("ring"):
        ring_data = getattr(entry, "runtime_data", None)
        if ring_data is None:
            continue
        try:
            found.extend(d for d in ring_data.devices.other if d.kind in INTERCOM_KINDS)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Ring-Geräte konnten nicht gelesen werden")
    return found


def _make_stream_class():
    """RingWebRtcStream mit einstellbaren stream_options.

    Die Bibliothek schickt immer audio+video. Für die Audio-Intercom fordern
    wir nur Ton an – so wie es die Ring-App für dieses Gerät vermutlich tut.
    """
    from ring_doorbell.const import (
        APP_API_URI,
        RTC_STREAMING_TICKET_ENDPOINT,
        RTC_STREAMING_WEB_SOCKET_ENDPOINT,
    )
    from ring_doorbell.exceptions import RingError
    from ring_doorbell.webrtcstream import RingWebRtcStream
    from websockets.asyncio.client import connect

    class SprechStream(RingWebRtcStream):
        def __init__(self, *args, stream_options: dict, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.stream_options = stream_options

        async def _generate(self, sdp_offer: str) -> None:
            # Wie RingWebRtcStream._generate, nur mit self.stream_options.
            try:
                req = await self._ring.async_query(
                    RTC_STREAMING_TICKET_ENDPOINT, method="POST", base_uri=APP_API_URI
                )
                ticket = req.json()["ticket"]
                ws_uri = RTC_STREAMING_WEB_SOCKET_ENDPOINT.format(uuid.uuid4(), ticket)
                if not self.ssl_context:
                    loop = asyncio.get_running_loop()
                    self.ssl_context = await loop.run_in_executor(
                        None, ssl.create_default_context
                    )
                self.websocket = await connect(
                    ws_uri, user_agent_header="android:com.ringapp", ssl=self.ssl_context
                )
                self.dialog_id = str(uuid.uuid4())
                offer_msg = {
                    "method": "live_view",
                    "dialog_id": self.dialog_id,
                    "body": {
                        "doorbot_id": self.device_api_id,
                        "stream_options": self.stream_options,
                        "sdp": sdp_offer,
                        "type": "offer",
                    },
                }
                _LOGGER.debug("live_view an Ring: %s", self.stream_options)
                self.read_task = asyncio.create_task(self.reader())
                await self.websocket.send(json_dumps(offer_msg))
                self._offered_event.set()
            except Exception as ex:
                raise RingError("Error generating RTC stream", ex) from ex

    return SprechStream


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Komponente aus configuration.yaml einrichten."""
    conf = config.get(DOMAIN) or {CONF_VIDEO_ANFORDERN: False}
    data = hass.data.setdefault(DOMAIN, {})
    data[DATA_CONFIG] = conf
    data["stream_class"] = _make_stream_class()

    for platform in PLATFORMS:
        hass.async_create_task(
            discovery.async_load_platform(hass, platform, DOMAIN, {}, config)
        )
    return True
