"""Energy Manager coordinator — zero-grid multi-battery coordination brain.

Drives the proven ZeroGridController + SafetySupervisor inside Home Assistant:
  - reads the grid power sensor (HA entity) each tick,
  - reads SOC from the per-battery coordinators,
  - splits dispatch across batteries by SOC,
  - sends Passive setpoints (cd_time auto-revert) via each battery's UDP client.

Runs as a DataUpdateCoordinator whose _async_update_data IS the control tick, so
entities (CoordinatorEntity) get fresh status every tick. It never raises out of the
tick: any failure is recorded and the system degrades to SAFE (batteries -> Auto).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEADBAND_W,
    CONF_EV_SENSOR,
    CONF_GRID_SENSOR,
    CONF_KD,
    CONF_KP,
    CONF_MAX_STEP_W,
    CONF_MIN_SOC,
    CONF_TARGET_GRID_W,
    DEFAULT_DEADBAND_W,
    DEFAULT_KD,
    DEFAULT_KP,
    DEFAULT_MAX_BATTERY_POWER,
    DEFAULT_MAX_STEP_W,
    DEFAULT_MIN_SOC,
    DEFAULT_TARGET_GRID_W,
    DOMAIN,
    MANAGER_BATTERY_FAIL_THRESHOLD,
    MANAGER_CD_TIME_S,
    MANAGER_CYCLE_FAIL_THRESHOLD,
    MANAGER_GRID_MAX_AGE_S,
    MANAGER_TICK_S,
)
from .controller import BatteryState, ControllerConfig, ZeroGridController
from .coordinator import MarstekDataUpdateCoordinator
from .safety import Mode, SafetyConfig, SafetySupervisor

_LOGGER = logging.getLogger(__name__)


class EnergyManagerCoordinator(DataUpdateCoordinator):
    """Zero-grid coordination brain."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_energy_manager",
            update_interval=timedelta(seconds=MANAGER_TICK_S),
        )
        self.entry = entry
        self.grid_sensor: str = entry.data[CONF_GRID_SENSOR]
        self.ev_sensor: str | None = entry.options.get(CONF_EV_SENSOR) or None
        self.enabled: bool = entry.options.get("enabled", True)
        # Cache of last good EV reading (fails toward "exclude" = safe: never dump battery into the car)
        self._last_ev_power: float = 0.0
        self._last_ev_ts: float = 0.0
        self._ev_power: float = 0.0  # EV power excluded this tick (for status)

        self.controller = ZeroGridController(self._build_controller_config())
        self.supervisor = SafetySupervisor(
            SafetyConfig(
                grid_max_age_s=MANAGER_GRID_MAX_AGE_S,
                battery_fail_threshold=MANAGER_BATTERY_FAIL_THRESHOLD,
                cycle_fail_threshold=MANAGER_CYCLE_FAIL_THRESHOLD,
            )
        )
        self.min_soc: float = entry.options.get(CONF_MIN_SOC, DEFAULT_MIN_SOC)
        self._released = False  # whether batteries were released to Auto
        self._prev_state: str | None = None  # for transition logging

    # ---- configuration --------------------------------------------------
    def _opt(self, key: str, default: Any) -> Any:
        return self.entry.options.get(key, self.entry.data.get(key, default))

    def _build_controller_config(self) -> ControllerConfig:
        return ControllerConfig(
            target_grid_w=int(self._opt(CONF_TARGET_GRID_W, DEFAULT_TARGET_GRID_W)),
            kp=float(self._opt(CONF_KP, DEFAULT_KP)),
            kd=float(self._opt(CONF_KD, DEFAULT_KD)),
            deadband_w=int(self._opt(CONF_DEADBAND_W, DEFAULT_DEADBAND_W)),
            max_step_w=int(self._opt(CONF_MAX_STEP_W, DEFAULT_MAX_STEP_W)),
        )

    async def async_apply_options(self) -> None:
        """Re-read options into the live controller (called after a setting changes)."""
        self.controller.config = self._build_controller_config()
        self.min_soc = float(self._opt(CONF_MIN_SOC, DEFAULT_MIN_SOC))
        self.ev_sensor = self.entry.options.get(CONF_EV_SENSOR) or None
        self.enabled = self.entry.options.get("enabled", True)

    # ---- battery access -------------------------------------------------
    def _battery_coordinators(self) -> list[MarstekDataUpdateCoordinator]:
        out: list[MarstekDataUpdateCoordinator] = []
        for coord in self.hass.data.get(DOMAIN, {}).values():
            if isinstance(coord, MarstekDataUpdateCoordinator):
                out.append(coord)
        return out

    def _read_grid(self) -> tuple[float | None, bool]:
        """Return (grid_power_w, fresh). + = importing."""
        state = self.hass.states.get(self.grid_sensor)
        if state is None or state.state in ("unknown", "unavailable", None, ""):
            return None, False
        try:
            value = float(state.state)
        except (ValueError, TypeError):
            return None, False
        age = (dt_util.utcnow() - state.last_updated).total_seconds()
        fresh = age <= MANAGER_GRID_MAX_AGE_S
        return value, fresh

    def _read_ev(self, now: float) -> float:
        """EV charger power to EXCLUDE from dispatch (W, >=0).

        Fails safe: if the EV sensor is configured but unreadable, keep using the last
        good value for a short window (so a sensor blip never drops the exclusion and
        dumps battery into the car). After that window, fall back to 0.
        """
        if not self.ev_sensor:
            return 0.0
        state = self.hass.states.get(self.ev_sensor)
        if state is not None and state.state not in ("unknown", "unavailable", None, ""):
            try:
                value = max(0.0, float(state.state))  # charging is a load; ignore sign noise
                self._last_ev_power = value
                self._last_ev_ts = now
                return value
            except (ValueError, TypeError):
                pass
        # Unreadable: reuse last good value if recent, else assume no EV.
        if now - self._last_ev_ts <= MANAGER_GRID_MAX_AGE_S:
            return self._last_ev_power
        return 0.0

    async def _release_batteries(self) -> None:
        """Hand all batteries back to their own Auto mode (safe idle)."""
        coords = self._battery_coordinators()
        await asyncio.gather(
            *(self._safe_call(c.client.set_mode, "Auto") for c in coords),
            return_exceptions=True,
        )

    @staticmethod
    async def _safe_call(func, *args):
        try:
            return await asyncio.wait_for(func(*args), timeout=MANAGER_TICK_S)
        except Exception:  # noqa: BLE001 - isolation: never let one battery break the tick
            return None

    # ---- the control tick ----------------------------------------------
    async def _async_update_data(self) -> dict[str, Any]:
        now = time.monotonic()
        try:
            result = await self._tick(now)
        except Exception as err:  # noqa: BLE001 - tick must never raise
            _LOGGER.exception("Energy manager tick failed: %s", err)
            self.supervisor.record_cycle(ok=False)
            result = self._status("error", grid=None, command=0, setpoints={}, reason=str(err))
        self._log_transition(result, now)
        return result

    def _log_transition(self, result: dict[str, Any], now: float) -> None:
        """Log only when the manager's state CHANGES (avoids per-tick spam)."""
        state = result.get("state")
        # Per-tick detail is available at DEBUG for deep diagnosis.
        _LOGGER.debug(
            "tick: state=%s grid=%s ev=%s cmd=%s reason=%s safety=%s",
            state, result.get("grid_power"), result.get("ev_power"),
            result.get("command_total"), result.get("reason"), result.get("safety"),
        )
        if state == self._prev_state:
            return
        healthy = [b for b in self.supervisor._battery_fails  # noqa: SLF001
                   if self.supervisor.battery_healthy(b)] or "all/unknown"
        msg = ("Energy manager state: %s → %s | reason=%s | grid=%sW ev=%sW cmd=%sW | "
               "healthy=%s safety=%s")
        args = (self._prev_state, state, result.get("reason") or "-",
                result.get("grid_power"), result.get("ev_power"),
                result.get("command_total"), healthy, result.get("safety"))
        if state in ("safe", "hold", "degraded", "error"):
            _LOGGER.warning(msg, *args)
        else:
            _LOGGER.info(msg, *args)
        self._prev_state = state

    def _safe_reason(self, now: float) -> str:
        if not self.supervisor.grid_fresh(now):
            return "grid sensor stale/unavailable"
        return "repeated control-cycle failures"

    async def _tick(self, now: float) -> dict[str, Any]:
        # Disabled: release once, then idle.
        if not self.enabled:
            if not self._released:
                await self._release_batteries()
                self._released = True
                self.controller.reset()
            return self._status("disabled", grid=None, command=0, setpoints={},
                                reason="Zero-Grid Control switch is off")
        self._released = False

        # Grid + EV (the EV load is excluded so batteries cover the house, not the car)
        grid, fresh = self._read_grid()
        self.supervisor.record_grid(grid is not None and fresh, now)
        self._ev_power = self._read_ev(now)

        # Batteries
        states: list[BatteryState] = []
        coord_by_id: dict[str, MarstekDataUpdateCoordinator] = {}
        for coord in self._battery_coordinators():
            bid = coord.entry.data.get("ip_address", coord.entry.entry_id)
            ok = bool(coord.last_update_success) and isinstance(coord.data, dict)
            self.supervisor.record_battery(bid, ok)
            if ok and self.supervisor.battery_healthy(bid):
                soc = coord.data.get("bat_soc")
                if soc is None:
                    continue
                coord_by_id[bid] = coord
                states.append(
                    BatteryState(
                        id=bid,
                        soc=float(soc),
                        power=int(coord.data.get("ongrid_power") or 0),
                        min_soc=self.min_soc,
                        max_power=DEFAULT_MAX_BATTERY_POWER,
                    )
                )

        mode = self.supervisor.mode(now)

        # SAFE: actively release, don't dispatch blind.
        if mode is Mode.SAFE:
            self.controller.reset()
            await self._release_batteries()
            self.supervisor.record_cycle(ok=False)
            return self._status("safe", grid=grid, command=0, setpoints={},
                                reason=self._safe_reason(now))

        # No fresh grid this tick, or no healthy batteries -> hold (cd_time covers).
        if grid is None or not fresh or not states:
            reasons = []
            if grid is None:
                reasons.append("no grid value")
            elif not fresh:
                reasons.append("grid stale this tick")
            if not states:
                reasons.append("no healthy batteries")
            self.supervisor.record_cycle(ok=False)
            return self._status("hold", grid=grid, command=0, setpoints={},
                                reason=", ".join(reasons))

        # NORMAL dispatch — subtract the EV load so the controller drives the HOUSE
        # grid toward target and the car is left to be served by the grid.
        effective_grid = grid - self._ev_power
        setpoints = self.controller.update(grid_power=effective_grid, batteries=states)
        results = await asyncio.gather(
            *(
                self._safe_call(coord_by_id[bid].client.set_passive_mode, sp, MANAGER_CD_TIME_S)
                for bid, sp in setpoints.items()
                if bid in coord_by_id
            ),
            return_exceptions=True,
        )
        sent_ok = all(r is not None and not isinstance(r, Exception) for r in results) if results else False
        for bid in setpoints:
            self.supervisor.record_battery(bid, sent_ok)
        self.supervisor.record_cycle(ok=sent_ok and bool(states))

        return self._status(
            "normal" if sent_ok else "degraded",
            grid=grid,
            command=sum(setpoints.values()),
            setpoints=setpoints,
            reason="" if sent_ok else "a battery did not acknowledge its setpoint",
        )

    def _status(self, state: str, grid, command: int, setpoints: dict[str, int],
                reason: str = "") -> dict[str, Any]:
        return {
            "state": state,
            "reason": reason,
            "enabled": self.enabled,
            "grid_power": grid,
            "ev_power": self._ev_power,
            "effective_grid": (grid - self._ev_power) if grid is not None else None,
            "command_total": command,
            "setpoints": setpoints,
            "safety": self.supervisor.status(time.monotonic()),
            "target_grid_w": self.controller.config.target_grid_w,
        }
