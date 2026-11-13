"""Konstanten für Ring Sprechanlage."""

from __future__ import annotations

DOMAIN = "ring_sprechanlage"

KIND_VIDEO = "intercom_handset_video"
KIND_AUDIO = "intercom_handset_audio"
INTERCOM_KINDS = (KIND_AUDIO, KIND_VIDEO)

# configuration.yaml
CONF_VIDEO_ANFORDERN = "video_anfordern"

# hass.data[DOMAIN]-Schlüssel
DATA_CONFIG = "config"
DATA_SESSIONS = "sessions"
