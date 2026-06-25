"""Service handlers for Marstek Venus E integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.const import ATTR_AREA_ID, ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    API_SET_MODE,
    API_SET_MANUAL_SCHEDULE,
    API_SET_PASSIVE_MODE,
    VALID_MODES,
)
from .coordinator import MarstekDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# The set of target attribute keys HA recognizes (entity_id/device_id/area_id/
# floor_id/label_id). Derived from cv.ENTITY_SERVICE_FIELDS so it stays correct
# across HA versions that add new target types.
_TARGET_FIELD_KEYS = {key.schema for key in cv.ENTITY_SERVICE_FIELDS}


def _battery_coordinators(
    hass: HomeAssistant,
) -> dict[str, MarstekDataUpdateCoordinator]:
    """Return only the real battery device coordinators, keyed by entry_id.

    hass.data[DOMAIN] also holds the Energy Manager and EV coordinators (the
    latter keyed ``<entry_id>_ev``). Those must never receive battery device
    commands, so filter by type — mirrors EnergyManagerCoordinator._battery_coordinators.
    """
    return {
        entry_id: coordinator
        for entry_id, coordinator in hass.data.get(DOMAIN, {}).items()
        if isinstance(coordinator, MarstekDataUpdateCoordinator)
    }


@callback
def _target_battery_coordinators(
    hass: HomeAssistant, call: ServiceCall
) -> dict[str, MarstekDataUpdateCoordinator]:
    """Resolve a service call's target to battery coordinators, keyed by entry_id.

    If the call carries no target (entity/device/area/...), fall back to every
    battery — preserving the original broadcast behavior so existing untargeted
    calls keep working. Otherwise map the referenced entities/devices to their
    config entries and return only the matching battery coordinators. This lets
    an external brain dispatch per-battery setpoints via device targeting, and
    read back a per-battery result (the entry_id keys).
    """
    batteries = _battery_coordinators(hass)
    if not any(call.data.get(key) for key in _TARGET_FIELD_KEYS):
        return dict(batteries)

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    entry_ids: set[str] = set()

    # Resolve each target kind straight off the registries (no deprecated
    # service helpers) into the owning config entries.
    entity_ids = call.data.get(ATTR_ENTITY_ID)
    if isinstance(entity_ids, str):
        entity_ids = [entity_ids]
    for entity_id in entity_ids or []:
        entity = ent_reg.async_get(entity_id)
        if entity and entity.config_entry_id:
            entry_ids.add(entity.config_entry_id)

    device_ids = call.data.get(ATTR_DEVICE_ID)
    if isinstance(device_ids, str):
        device_ids = [device_ids]
    for device_id in device_ids or []:
        device = dev_reg.async_get(device_id)
        if device:
            entry_ids.update(device.config_entries)

    area_ids = call.data.get(ATTR_AREA_ID)
    if isinstance(area_ids, str):
        area_ids = [area_ids]
    for area_id in area_ids or []:
        for device in dr.async_entries_for_area(dev_reg, area_id):
            entry_ids.update(device.config_entries)
        for entity in er.async_entries_for_area(ent_reg, area_id):
            if entity.config_entry_id:
                entry_ids.add(entity.config_entry_id)

    return {eid: batteries[eid] for eid in entry_ids if eid in batteries}

# Service schemas
SERVICE_SET_MODE_SCHEMA = vol.Schema(
    {
        vol.Required("mode"): vol.In(VALID_MODES),
        # Optional target (entity/device/area). No target => all batteries.
        **cv.ENTITY_SERVICE_FIELDS,
    }
)

SERVICE_SET_MANUAL_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required("time_num"): vol.All(vol.Coerce(int), vol.Range(min=0, max=9)),
        vol.Required("start_time"): cv.time,
        vol.Required("end_time"): cv.time,  # Note: end_time must be > start_time (validated by device)
        vol.Required("week_set"): vol.All(vol.Coerce(int), vol.Range(min=1, max=127)),
        vol.Required("mode"): vol.In(["Charging", "Discharging"]),  # Charging or Discharging
        vol.Required("power"): vol.All(vol.Coerce(int), vol.Range(min=-1, max=2500)),  # Power magnitude (allow -1 for testing)
        vol.Optional("enable", default=True): cv.boolean,
    }
)

SERVICE_SET_PASSIVE_MODE_SCHEMA = vol.Schema(
    {
        vol.Required("power"): vol.Coerce(int),
        vol.Optional("cd_time", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
        # Optional target (entity/device/area). No target => all batteries.
        **cv.ENTITY_SERVICE_FIELDS,
    }
)

# Schema for set_ble_adv service
SERVICE_SET_BLE_ADV_SCHEMA = vol.Schema(
    {
        vol.Required("enable"): cv.boolean,
    }
)

# Schema for set_led_ctrl service
SERVICE_SET_LED_CTRL_SCHEMA = vol.Schema(
    {
        vol.Required("enabled"): cv.boolean,
    }
)

# Schema for change_operating_mode service (mode change + optional manual schedules)
SERVICE_CHANGE_OPERATING_MODE_SCHEMA = vol.Schema(
    {
        vol.Required("mode"): vol.In(VALID_MODES),
        # Slot 0
        vol.Optional("slot_0_enable", default=False): cv.boolean,
        vol.Optional("slot_0_start_time"): cv.time,
        vol.Optional("slot_0_end_time"): cv.time,
        vol.Optional("slot_0_power", default=100): vol.All(vol.Coerce(int), vol.Range(min=-1, max=2500)),
        vol.Optional("slot_0_mode", default="Discharging"): vol.In(["Charging", "Discharging"]),
        vol.Optional("slot_0_days", default=127): vol.All(vol.Coerce(int), vol.Range(min=1, max=127)),
        # Slot 1
        vol.Optional("slot_1_enable", default=False): cv.boolean,
        vol.Optional("slot_1_start_time"): cv.time,
        vol.Optional("slot_1_end_time"): cv.time,
        vol.Optional("slot_1_power", default=100): vol.All(vol.Coerce(int), vol.Range(min=-1, max=2500)),
        vol.Optional("slot_1_mode", default="Discharging"): vol.In(["Charging", "Discharging"]),
        vol.Optional("slot_1_days", default=127): vol.All(vol.Coerce(int), vol.Range(min=1, max=127)),
        # Slot 2
        vol.Optional("slot_2_enable", default=False): cv.boolean,
        vol.Optional("slot_2_start_time"): cv.time,
        vol.Optional("slot_2_end_time"): cv.time,
        vol.Optional("slot_2_power", default=100): vol.All(vol.Coerce(int), vol.Range(min=-1, max=2500)),
        vol.Optional("slot_2_mode", default="Discharging"): vol.In(["Charging", "Discharging"]),
        vol.Optional("slot_2_days", default=127): vol.All(vol.Coerce(int), vol.Range(min=1, max=127)),
        # Slot 3
        vol.Optional("slot_3_enable", default=False): cv.boolean,
        vol.Optional("slot_3_start_time"): cv.time,
        vol.Optional("slot_3_end_time"): cv.time,
        vol.Optional("slot_3_power", default=100): vol.All(vol.Coerce(int), vol.Range(min=-1, max=2500)),
        vol.Optional("slot_3_mode", default="Discharging"): vol.In(["Charging", "Discharging"]),
        vol.Optional("slot_3_days", default=127): vol.All(vol.Coerce(int), vol.Range(min=1, max=127)),
        # Slot 4
        vol.Optional("slot_4_enable", default=False): cv.boolean,
        vol.Optional("slot_4_start_time"): cv.time,
        vol.Optional("slot_4_end_time"): cv.time,
        vol.Optional("slot_4_power", default=100): vol.All(vol.Coerce(int), vol.Range(min=-1, max=2500)),
        vol.Optional("slot_4_mode", default="Discharging"): vol.In(["Charging", "Discharging"]),
        vol.Optional("slot_4_days", default=127): vol.All(vol.Coerce(int), vol.Range(min=1, max=127)),
        # Slot 5
        vol.Optional("slot_5_enable", default=False): cv.boolean,
        vol.Optional("slot_5_start_time"): cv.time,
        vol.Optional("slot_5_end_time"): cv.time,
        vol.Optional("slot_5_power", default=100): vol.All(vol.Coerce(int), vol.Range(min=-1, max=2500)),
        vol.Optional("slot_5_mode", default="Discharging"): vol.In(["Charging", "Discharging"]),
        vol.Optional("slot_5_days", default=127): vol.All(vol.Coerce(int), vol.Range(min=1, max=127)),
        # Slot 6
        vol.Optional("slot_6_enable", default=False): cv.boolean,
        vol.Optional("slot_6_start_time"): cv.time,
        vol.Optional("slot_6_end_time"): cv.time,
        vol.Optional("slot_6_power", default=100): vol.All(vol.Coerce(int), vol.Range(min=-1, max=2500)),
        vol.Optional("slot_6_mode", default="Discharging"): vol.In(["Charging", "Discharging"]),
        vol.Optional("slot_6_days", default=127): vol.All(vol.Coerce(int), vol.Range(min=1, max=127)),
        # Slot 7
        vol.Optional("slot_7_enable", default=False): cv.boolean,
        vol.Optional("slot_7_start_time"): cv.time,
        vol.Optional("slot_7_end_time"): cv.time,
        vol.Optional("slot_7_power", default=100): vol.All(vol.Coerce(int), vol.Range(min=-1, max=2500)),
        vol.Optional("slot_7_mode", default="Discharging"): vol.In(["Charging", "Discharging"]),
        vol.Optional("slot_7_days", default=127): vol.All(vol.Coerce(int), vol.Range(min=1, max=127)),
        # Slot 8
        vol.Optional("slot_8_enable", default=False): cv.boolean,
        vol.Optional("slot_8_start_time"): cv.time,
        vol.Optional("slot_8_end_time"): cv.time,
        vol.Optional("slot_8_power", default=100): vol.All(vol.Coerce(int), vol.Range(min=-1, max=2500)),
        vol.Optional("slot_8_mode", default="Discharging"): vol.In(["Charging", "Discharging"]),
        vol.Optional("slot_8_days", default=127): vol.All(vol.Coerce(int), vol.Range(min=1, max=127)),
        # Slot 9
        vol.Optional("slot_9_enable", default=False): cv.boolean,
        vol.Optional("slot_9_start_time"): cv.time,
        vol.Optional("slot_9_end_time"): cv.time,
        vol.Optional("slot_9_power", default=100): vol.All(vol.Coerce(int), vol.Range(min=-1, max=2500)),
        vol.Optional("slot_9_mode", default="Discharging"): vol.In(["Charging", "Discharging"]),
        vol.Optional("slot_9_days", default=127): vol.All(vol.Coerce(int), vol.Range(min=1, max=127)),
    }
)


async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for Marstek Venus E.
    
    Args:
        hass: Home Assistant instance
    """

    async def set_mode_handler(call: ServiceCall) -> dict[str, Any]:
        """Handle set_mode service call.

        Returns a per-battery result keyed by config entry_id so an external
        caller (e.g. Wattsmith) can tell which batteries acked.

        Args:
            call: Service call object
        """
        mode = call.data.get("mode")

        results: dict[str, Any] = {}
        for entry_id, coordinator in _target_battery_coordinators(hass, call).items():
            try:
                await coordinator.set_mode(mode)
                results[entry_id] = {"ok": True}
                _LOGGER.info("Set mode to %s on %s", mode, coordinator.client.ip_address)
            except Exception as err:
                results[entry_id] = {"ok": False, "error": str(err)}
                _LOGGER.error("Error setting mode: %s", err)
        return {"results": results}

    async def set_manual_schedule_handler(call: ServiceCall) -> None:
        """Handle set_manual_schedule service call.
        
        Args:
            call: Service call object
        """
        time_num = call.data.get("time_num")
        start_time = call.data.get("start_time").strftime("%H:%M")
        end_time = call.data.get("end_time").strftime("%H:%M")
        week_set = call.data.get("week_set")
        mode = call.data.get("mode")
        power_magnitude = call.data.get("power")
        enable = call.data.get("enable", True)
        
        # Convert power based on mode: Charging = negative, Discharging = positive
        power = -power_magnitude if mode == "Charging" else power_magnitude
        
        for coordinator in _battery_coordinators(hass).values():
            try:
                await coordinator.set_manual_schedule(
                    time_num=time_num,
                    start_time=start_time,
                    end_time=end_time,
                    week_set=week_set,
                    power=power,
                    enable=enable,
                )
                _LOGGER.info(
                    "Set manual schedule: time_num=%s, start=%s, end=%s, mode=%s, power=%s",
                    time_num,
                    start_time,
                    end_time,
                    mode,
                    power,
                )
            except Exception as err:
                _LOGGER.error("Error setting manual schedule: %s", err)

    async def set_passive_mode_handler(call: ServiceCall) -> dict[str, Any]:
        """Handle set_passive_mode service call.

        Returns a per-battery result keyed by config entry_id so an external
        caller (e.g. Wattsmith) can record which batteries acked the setpoint
        (UDP can silently drop) and flag "degraded" on repeated misses.

        Args:
            call: Service call object
        """
        power = call.data.get("power")
        cd_time = call.data.get("cd_time", 0)

        results: dict[str, Any] = {}
        for entry_id, coordinator in _target_battery_coordinators(hass, call).items():
            try:
                await coordinator.set_passive_mode(power=power, cd_time=cd_time)
                results[entry_id] = {"ok": True}
                _LOGGER.info(
                    "Set passive mode: power=%s, cd_time=%s on %s",
                    power,
                    cd_time,
                    coordinator.client.ip_address,
                )
            except Exception as err:
                results[entry_id] = {"ok": False, "error": str(err)}
                _LOGGER.error("Error setting passive mode: %s", err)
        return {"results": results}

    # Register services. set_mode/set_passive_mode return an optional per-battery
    # response (SupportsResponse.OPTIONAL) — callers may ignore it.
    hass.services.async_register(
        DOMAIN,
        "set_mode",
        set_mode_handler,
        schema=SERVICE_SET_MODE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    hass.services.async_register(
        DOMAIN,
        "set_manual_schedule",
        set_manual_schedule_handler,
        schema=SERVICE_SET_MANUAL_SCHEDULE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        "set_passive_mode",
        set_passive_mode_handler,
        schema=SERVICE_SET_PASSIVE_MODE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def clear_all_schedules_handler(call: ServiceCall) -> None:
        """Handle clear_all_schedules service call.
        
        Args:
            call: Service call object
        """
        for coordinator in _battery_coordinators(hass).values():
            try:
                results = await coordinator.clear_all_manual_schedules()
                _LOGGER.info(
                    "Cleared manual schedules: %d/%d slots disabled",
                    results["success_count"],
                    results["total_slots"],
                )
                if results["failed_slots"]:
                    _LOGGER.warning("Failed to disable slots: %s", results["failed_slots"])
            except Exception as err:
                _LOGGER.error("Error clearing manual schedules: %s", err)

    hass.services.async_register(
        DOMAIN,
        "clear_all_schedules",
        clear_all_schedules_handler,
    )

    async def set_ble_adv_handler(call: ServiceCall) -> None:
        """Handle set_ble_adv service call.
        
        Args:
            call: Service call object
        """
        enable = call.data.get("enable")
        
        for coordinator in _battery_coordinators(hass).values():
            try:
                await coordinator.client.set_ble_adv(enable)
                _LOGGER.info("Bluetooth advertising %s", "enabled" if enable else "disabled")
            except Exception as err:
                _LOGGER.error("Error setting Bluetooth advertising: %s", err)

    hass.services.async_register(
        DOMAIN,
        "set_ble_adv",
        set_ble_adv_handler,
        schema=SERVICE_SET_BLE_ADV_SCHEMA,
    )

    async def set_led_ctrl_handler(call: ServiceCall) -> None:
        """Handle set_led_ctrl service call.
        
        Args:
            call: Service call object
        """
        enabled = call.data.get("enabled")
        
        for coordinator in _battery_coordinators(hass).values():
            try:
                await coordinator.client.set_led_ctrl(enabled)
                _LOGGER.info("Service Call LED: Turning %s for device at %s", "ON" if enabled else "OFF", coordinator.client.ip_address)
            except Exception as err:
                _LOGGER.error("Error setting LED: %s", err)

    hass.services.async_register(
        DOMAIN,
        "set_led_ctrl",
        set_led_ctrl_handler,
        schema=SERVICE_SET_LED_CTRL_SCHEMA,
    )

    async def change_operating_mode_handler(call: ServiceCall) -> None:
        """Handle change_operating_mode service call.
        
        This service changes the operating mode and optionally configures manual schedules.
        
        Args:
            call: Service call object
        """
        mode = call.data.get("mode")
        
        for coordinator in _battery_coordinators(hass).values():
            try:
                # First, set the operating mode
                await coordinator.set_mode(mode)
                _LOGGER.info("Changed operating mode to %s", mode)
                
                # If Manual mode is selected, configure schedules for enabled slots
                if mode == "Manual":
                    for slot_num in range(10):
                        enable_key = f"slot_{slot_num}_enable"
                        
                        if call.data.get(enable_key, False):
                            # This slot is enabled, configure it
                            start_time_key = f"slot_{slot_num}_start_time"
                            end_time_key = f"slot_{slot_num}_end_time"
                            power_key = f"slot_{slot_num}_power"
                            mode_key = f"slot_{slot_num}_mode"
                            days_key = f"slot_{slot_num}_days"
                            
                            start_time = call.data.get(start_time_key)
                            end_time = call.data.get(end_time_key)
                            power_magnitude = call.data.get(power_key, 100)
                            slot_mode = call.data.get(mode_key, "Discharging")
                            week_set = call.data.get(days_key, 127)
                            
                            if start_time and end_time:
                                # Convert datetime.time to string format
                                start_time_str = start_time.strftime("%H:%M")
                                end_time_str = end_time.strftime("%H:%M")
                                
                                # Convert power based on mode
                                power = -power_magnitude if slot_mode == "Charging" else power_magnitude
                                
                                await coordinator.set_manual_schedule(
                                    time_num=slot_num,
                                    start_time=start_time_str,
                                    end_time=end_time_str,
                                    week_set=week_set,
                                    power=power,
                                    enable=True,
                                )
                                
                                _LOGGER.info(
                                    "Configured slot %d: %s-%s, %s, %dW, days=%d",
                                    slot_num,
                                    start_time_str,
                                    end_time_str,
                                    slot_mode,
                                    power,
                                    week_set,
                                )
                            else:
                                _LOGGER.warning(
                                    "Slot %d is enabled but missing start_time or end_time",
                                    slot_num,
                                )
                        else:
                            # Slot is disabled, explicitly disable it
                            await coordinator.set_manual_schedule(
                                time_num=slot_num,
                                start_time="00:00",
                                end_time="00:01",
                                week_set=127,
                                power=100,
                                enable=False,
                            )
                            _LOGGER.debug("Disabled slot %d", slot_num)
                
            except Exception as err:
                _LOGGER.error("Error in change_operating_mode: %s", err)
                raise

    hass.services.async_register(
        DOMAIN,
        "change_operating_mode",
        change_operating_mode_handler,
        schema=SERVICE_CHANGE_OPERATING_MODE_SCHEMA,
    )

    _LOGGER.debug("Services registered for %s", DOMAIN)
