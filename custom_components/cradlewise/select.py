"""Cradlewise select entities."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CRADLE_ID
from .coordinator import CradlewiseCoordinator

_DEVICE_INFO = DeviceInfo(
    identifiers={(DOMAIN, CRADLE_ID)},
    manufacturer="Cradlewise",
    model="Smart Crib",
    name="Cradlewise avo",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([])
