"""The Marstek Venus E integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_ENTRY_TYPE, DOMAIN, ENTRY_TYPE_MANAGER
from .coordinator import MarstekDataUpdateCoordinator
from .ev_coordinator import EvCoordinator
from .manager import EnergyManagerCoordinator

_LOGGER = logging.getLogger(__name__)

# Battery (device) entries
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.BUTTON,
    Platform.SWITCH,
]

# Energy Manager entries
MANAGER_PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.SELECT,
]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Marstek Venus E integration."""
    hass.data.setdefault(DOMAIN, {})
    return True


def _is_manager(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_ENTRY_TYPE) == ENTRY_TYPE_MANAGER


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a config entry (battery device OR energy manager)."""
    hass.data.setdefault(DOMAIN, {})
    if _is_manager(entry):
        return await _setup_manager(hass, entry)
    return await _setup_battery(hass, entry)


async def _setup_battery(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Marstek battery device entry (unchanged behavior)."""
    coordinator = MarstekDataUpdateCoordinator(hass, entry)

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(f"Unable to connect to device: {err}") from err

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services (battery-side)
    from .services import async_setup_services as setup_services
    await setup_services(hass)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def _setup_manager(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Energy Manager (zero-grid coordination brain) + EV coordinator."""
    coordinator = EnergyManagerCoordinator(hass, entry)
    # The manager tick never raises, so first refresh always succeeds; it simply
    # starts in SAFE/hold until the grid sensor is fresh and batteries are known.
    await coordinator.async_config_entry_first_refresh()

    ev_coordinator = EvCoordinator(hass, entry)
    await ev_coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    hass.data[DOMAIN][entry.entry_id + "_ev"] = ev_coordinator
    await hass.config_entries.async_forward_entry_setups(entry, MANAGER_PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_manager_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    platforms = MANAGER_PLATFORMS if _is_manager(entry) else PLATFORMS
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, platforms):
        coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
        if coordinator is not None:
            if isinstance(coordinator, EnergyManagerCoordinator):
                await coordinator._release_batteries()
            await coordinator.async_shutdown()
        ev_coord = hass.data[DOMAIN].pop(entry.entry_id + "_ev", None)
        if ev_coord is not None:
            await ev_coord.async_shutdown()
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry (battery options change)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_manager_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply changed manager options live (no full reload)."""
    domain_data = hass.data.get(DOMAIN, {})
    coordinator = domain_data.get(entry.entry_id)
    if isinstance(coordinator, EnergyManagerCoordinator):
        await coordinator.async_apply_options()
    ev_coord = domain_data.get(entry.entry_id + "_ev")
    if isinstance(ev_coord, EvCoordinator):
        await ev_coord.async_apply_options()
