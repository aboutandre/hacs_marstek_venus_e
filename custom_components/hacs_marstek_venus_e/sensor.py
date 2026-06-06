"""Sensors for Marstek Venus E integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfEnergy,
    UnitOfPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, ALL_SENSORS
from .manager import EnergyManagerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform (manager status sensors, or battery sensors)."""
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    if isinstance(coordinator, EnergyManagerCoordinator):
        async_add_entities(
            ManagerSensor(coordinator, entry.entry_id, entry.title, d)
            for d in MANAGER_SENSORS
        )
        return

    entities: list[MarstekSensor] = []
    for sensor_id, sensor_config in ALL_SENSORS.items():
        entities.append(
            MarstekSensor(
                coordinator=coordinator,
                entry_id=entry.entry_id,
                sensor_id=sensor_id,
                sensor_config=sensor_config,
            )
        )

    async_add_entities(entities)


# (key in status dict, name, unit, device_class, icon)
MANAGER_SENSORS: tuple[tuple[str, str, str | None, str | None, str], ...] = (
    ("state", "Status", None, None, "mdi:state-machine"),
    ("grid_power", "Grid Power Seen", "W", "power", "mdi:transmission-tower"),
    ("command_total", "Total Battery Command", "W", "power", "mdi:home-battery"),
    ("target_grid_w", "Target Grid Power", "W", "power", "mdi:target"),
)


class ManagerSensor(CoordinatorEntity, SensorEntity):
    """A status sensor of the Energy Manager (reads the coordinator status dict)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry_id, title, desc) -> None:
        super().__init__(coordinator)
        key, name, unit, device_class, icon = desc
        self._key = key
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_icon = icon
        self._attr_unique_id = f"{entry_id}_mgr_{key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": title,
            "manufacturer": "Marstek",
            "model": "Energy Manager",
        }

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        return data.get(self._key)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._key != "state":
            return None
        data = self.coordinator.data or {}
        return {"setpoints": data.get("setpoints"), "safety": data.get("safety")}


class MarstekSensor(CoordinatorEntity, SensorEntity):
    """Marstek Venus E sensor entity."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry_id: str,
        sensor_id: str,
        sensor_config: dict[str, Any],
    ) -> None:
        """Initialize the sensor.
        
        Args:
            coordinator: Data update coordinator
            entry_id: Configuration entry ID
            sensor_id: Sensor identifier
            sensor_config: Sensor configuration dictionary
        """
        super().__init__(coordinator)
        
        self.coordinator = coordinator
        self.entry_id = entry_id
        self.sensor_id = sensor_id
        self.sensor_config = sensor_config
        
        self._attr_name = sensor_config["name"]
        self._attr_icon = sensor_config.get("icon")
        self._attr_device_class = sensor_config.get("device_class")
        self._attr_native_unit_of_measurement = sensor_config.get("unit")
        
        # Set state class for energy sensors
        if sensor_config.get("state_class"):
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        
        # Create unique ID
        self._attr_unique_id = f"{DOMAIN}_{entry_id}_{sensor_id}"
        
        # Device info for grouping
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": f"Marstek Venus E",
            "manufacturer": "Marstek",
            "model": "Venus E",
        }

    @property
    def native_value(self) -> Any:
        """Return the state value.
        
        Returns:
            Current sensor value
        """
        attr_path = self.sensor_config.get("attr")
        source = self.sensor_config.get("source", "auto")
        
        # Check appropriate data source based on sensor configuration
        if source == "battery" and self.coordinator.battery_data:
            # From Bat.GetStatus (manual refresh)
            if attr_path in self.coordinator.battery_data:
                return self.coordinator.battery_data[attr_path]
        elif source == "mode" and self.coordinator.mode_data:
            # From ES.GetMode (manual refresh)
            if attr_path in self.coordinator.mode_data:
                return self.coordinator.mode_data[attr_path]
        elif source == "auto" and self.coordinator.data:
            # From ES.GetStatus (automatic updates)
            if attr_path in self.coordinator.data:
                return self.coordinator.data[attr_path]
        
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available.
        
        Returns:
            True if data is available
        """
        return self.coordinator.last_update_success

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
