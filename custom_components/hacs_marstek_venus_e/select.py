"""Select platform for Marstek Venus E."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_CHEAP_TARGET,
    CONF_EV_MODE,
    DEFAULT_CHEAP_TARGET,
    DEFAULT_EV_MODE,
    DOMAIN,
    ATTR_OPERATING_MODE,
    MODE_AUTO,
    MODE_AI,
    MODE_MANUAL,
    MODE_PASSIVE,
    VALID_MODES,
)
from .coordinator import MarstekDataUpdateCoordinator
from .ev_coordinator import EvCoordinator
from .ev_planner import TARGET_BATTERY, TARGET_BOTH, TARGET_CAR, TARGET_NONE
from .manager import EnergyManagerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up select entities (EV mode + cheap target for manager, operating mode for battery)."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    if isinstance(coordinator, EnergyManagerCoordinator):
        ev_coord: EvCoordinator | None = hass.data[DOMAIN].get(entry.entry_id + "_ev")
        if ev_coord is not None:
            async_add_entities([
                EvModeSelect(ev_coord, entry),
                CheapTargetSelect(ev_coord, entry),
            ])
        return

    async_add_entities([MarstekOperatingModeSelect(coordinator, entry)])


class MarstekOperatingModeSelect(CoordinatorEntity, SelectEntity):
    """Select entity for Marstek Venus E operating mode."""
    
    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the select entity.
        
        Args:
            coordinator: Data update coordinator
            entry: Configuration entry
        """
        super().__init__(coordinator)
        self._attr_name = "Operating Mode"
        self._attr_unique_id = f"{entry.entry_id}_operating_mode"
        self._attr_options = VALID_MODES
        self._attr_icon = "mdi:cog"
        
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Marstek",
            "model": "Venus E",
        }
    
    @property
    def current_option(self) -> str | None:
        """Return the current operating mode."""
        # First try to get mode from mode_data (ES.GetMode response)
        if self.coordinator.mode_data and "mode" in self.coordinator.mode_data:
            mode = self.coordinator.mode_data["mode"]
            if mode in VALID_MODES:
                return mode
        
        # Fallback to main data (mode was copied there in coordinator update)
        if self.coordinator.data and "mode" in self.coordinator.data:
            mode = self.coordinator.data["mode"]
            if mode in VALID_MODES:
                return mode
        
        return None
    
    async def async_select_option(self, option: str) -> None:
        """Change the operating mode.
        
        Args:
            option: New operating mode
        """
        if option not in VALID_MODES:
            _LOGGER.error("Invalid mode: %s", option)
            return
        
        try:
            await self.coordinator.set_mode(option)
            _LOGGER.info("Changed operating mode to: %s", option)
        except Exception as err:
            _LOGGER.error("Failed to set mode to %s: %s", option, err)
            raise


class EvModeSelect(CoordinatorEntity, SelectEntity):
    """Select entity for EV charging mode."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator: EvCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = "EV Charging Mode"
        self._attr_unique_id = f"{entry.entry_id}_ev_mode"
        self._attr_options = ["off", "solar", "solar_cheap", "fast"]
        self._entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id + "_ev")},
            "name": f"{entry.title} EV",
            "manufacturer": "go-e",
            "model": "EV Charger",
            "via_device": (DOMAIN, entry.entry_id),
        }

    @property
    def current_option(self) -> str:
        return self.coordinator.entry.options.get(CONF_EV_MODE, DEFAULT_EV_MODE)

    async def async_select_option(self, option: str) -> None:
        new_options = {**self.coordinator.entry.options, CONF_EV_MODE: option}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        self.coordinator._ev_mode = option
        await self.coordinator.async_request_refresh()


class CheapTargetSelect(CoordinatorEntity, SelectEntity):
    """Select entity for what the cheap-price window should charge."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:cash-clock"

    def __init__(self, coordinator: EvCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_name = "EV Cheap-Price Target"
        self._attr_unique_id = f"{entry.entry_id}_ev_cheap_target"
        self._attr_options = [TARGET_NONE, TARGET_BATTERY, TARGET_CAR, TARGET_BOTH]
        self._entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id + "_ev")},
            "name": f"{entry.title} EV",
            "manufacturer": "go-e",
            "model": "EV Charger",
            "via_device": (DOMAIN, entry.entry_id),
        }

    @property
    def current_option(self) -> str:
        return self.coordinator.entry.options.get(CONF_CHEAP_TARGET, DEFAULT_CHEAP_TARGET)

    async def async_select_option(self, option: str) -> None:
        new_options = {**self.coordinator.entry.options, CONF_CHEAP_TARGET: option}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        self.coordinator._cheap_target = option
        await self.coordinator.async_request_refresh()
