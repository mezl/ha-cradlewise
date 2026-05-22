"""Cradlewise smart crib integration."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN
from .coordinator import CradlewiseCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["switch", "number", "sensor", "binary_sensor", "camera", "select"]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = CradlewiseCoordinator(hass)
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.async_add_executor_job(coordinator.start)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        coordinator: CradlewiseCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await hass.async_add_executor_job(coordinator.stop)
    return ok
