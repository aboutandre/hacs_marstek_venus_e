"""Switch platform for Marstek Venus E."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MarstekDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the battery LED switch."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MarstekLedSwitch(coordinator, entry)])


class MarstekLedSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity for Marstek Venus E LED control."""
    
    _attr_has_entity_name = True
    _attr_translation_key = "led_ctrl"
    _attr_assumed_state = True

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the switch.
        
        Args:
            coordinator: Data update coordinator
            entry: Configuration entry
        """
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_led_ctrl"
        self._attr_icon = "mdi:led-on"
        self._is_on: bool | None = None
        
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Marstek",
            "model": "Venus E",
        }
    
    @property
    def is_on(self) -> bool | None:
        """Return True if LED is on."""
        return self._is_on
    
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the LED on."""
        try:
            await self.coordinator.client.set_led_ctrl(True)
            self._is_on = True
            _LOGGER.info("LED control: Command ON sent to Marstek device at %s", self.coordinator.client.ip_address)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to turn on LED: %s", err)
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the LED off."""
        try:
            await self.coordinator.client.set_led_ctrl(False)
            self._is_on = False
            _LOGGER.info("LED control: Command OFF sent to Marstek device at %s", self.coordinator.client.ip_address)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to turn off LED: %s", err)
            raise