"""Cradlewise smart crib integration."""
from __future__ import annotations

import io
import logging

import av

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN
from .coordinator import CradlewiseCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["switch", "number", "sensor", "camera", "binary_sensor", "select"]

_SVC_TALK = "talk"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = CradlewiseCoordinator(hass)
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.async_add_executor_job(coordinator.start)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _handle_talk(call: ServiceCall) -> None:
        """Fetch an audio URL, decode it, and send to the crib speakers."""
        media_url: str = call.data.get("media_url", "")
        if not media_url:
            _LOGGER.warning("cradlewise.talk called without media_url")
            return

        cam = hass.data.get(DOMAIN, {}).get("camera")
        if cam is None:
            _LOGGER.error("cradlewise.talk: camera entity not ready")
            return

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(media_url) as resp:
                    raw = await resp.read()
        except Exception as exc:
            _LOGGER.error("cradlewise.talk: failed to fetch %s — %s", media_url, exc)
            return

        def _decode(data: bytes) -> bytes:
            """Decode any audio format → 48 kHz stereo s16le PCM (blocking)."""
            container = av.open(io.BytesIO(data))
            resampler = av.AudioResampler(format="s16", layout="stereo", rate=48000)
            chunks: list[bytes] = []
            for frame in container.decode(audio=0):
                for rf in resampler.resample(frame):
                    chunks.append(bytes(rf.planes[0]))
            for rf in resampler.resample(None):
                chunks.append(bytes(rf.planes[0]))
            return b"".join(chunks)

        try:
            pcm = await hass.async_add_executor_job(_decode, raw)
        except Exception as exc:
            _LOGGER.error("cradlewise.talk: audio decode failed — %s", exc)
            return

        cam.send_audio(pcm)
        _LOGGER.debug("cradlewise.talk: sent %d bytes PCM to crib", len(pcm))

    hass.services.async_register(DOMAIN, _SVC_TALK, _handle_talk)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        coordinator: CradlewiseCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await hass.async_add_executor_job(coordinator.stop)
        hass.data[DOMAIN].pop("camera", None)
        hass.services.async_remove(DOMAIN, _SVC_TALK)
    return ok
