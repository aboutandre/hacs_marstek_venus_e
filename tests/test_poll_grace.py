"""Unit tests: riding out short device silences (hel-138, EMS firmware V150).

V150 closes the Local API for ~5-30 s every ~34 min. Covers:
  - coordinator: a failed ES.GetStatus inside POLL_GRACE_S serves the last good
    reading and re-polls after POLL_RETRY_S instead of going unavailable,
  - coordinator: the scan interval comes back on the first good poll,
  - coordinator: past the grace window (or with no reading yet) it still raises
    UpdateFailed, so a battery that is really gone goes unavailable,
  - udp_client: "connection refused" is retried like a timeout, and a refused
    attempt waits out its window so the request budget is unchanged.

Headless — no Home Assistant, no network.

Run directly:   python3 tests/test_poll_grace.py
Or with pytest: pytest tests/test_poll_grace.py
"""
import asyncio
import importlib.util
import sys
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

# ── stub homeassistant + package imports ─────────────────────────────────────
sys.modules["homeassistant"] = MagicMock()
sys.modules["homeassistant.core"] = MagicMock()
sys.modules["homeassistant.config_entries"] = MagicMock()

_uc = ModuleType("homeassistant.helpers.update_coordinator")


class DataUpdateCoordinator:
    def __init__(self, hass, logger, name=None, update_interval=None):
        self.hass = hass
        self.data = {}
        self.update_interval = update_interval


class UpdateFailed(Exception):
    pass


_uc.DataUpdateCoordinator = DataUpdateCoordinator
_uc.UpdateFailed = UpdateFailed
sys.modules["homeassistant.helpers"] = MagicMock()
sys.modules["homeassistant.helpers.update_coordinator"] = _uc

_pkg = ModuleType("hacs_marstek_venus_e")
sys.modules.setdefault("hacs_marstek_venus_e", _pkg)

_const = ModuleType("hacs_marstek_venus_e.const")
_const.DOMAIN = "hacs_marstek_venus_e"
_const.CONF_IP_ADDRESS = "ip_address"
_const.CONF_PORT = "port"
_const.CONF_SCAN_INTERVAL = "scan_interval"
sys.modules["hacs_marstek_venus_e.const"] = _const

_base = Path(__file__).resolve().parents[1] / "custom_components" / "hacs_marstek_venus_e"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"hacs_marstek_venus_e.{name}", _base / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "hacs_marstek_venus_e"
    sys.modules[f"hacs_marstek_venus_e.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


# The real settings module, so the tests check the shipped numbers.
_settings = _load("settings")
_udp = _load("udp_client")
_coord_mod = _load("coordinator")

MarstekDataUpdateCoordinator = _coord_mod.MarstekDataUpdateCoordinator
MarstekUDPClient = _udp.MarstekUDPClient

SCAN = timedelta(seconds=_settings.SCAN_INTERVAL_S)
RETRY = timedelta(seconds=_settings.POLL_RETRY_S)
GOOD = {"bat_soc": 80, "ongrid_power": 100}


class _Clock:
    """Stand-in for time.monotonic inside the coordinator."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _make_coordinator():
    entry = MagicMock()
    entry.options = {}
    entry.data = {"ip_address": "192.0.2.10", "port": 30000}
    coord = MarstekDataUpdateCoordinator(MagicMock(), entry)
    coord.client = MagicMock()
    coord.client.ip_address = "192.0.2.10"
    coord._refreshes = 5  # past the one-time poll stagger
    coord._ct_present = False
    coord._last_mode_update = coord._last_battery_update = _coord_mod.datetime.now()
    return coord


async def _poll(coord, clock, result):
    """One coordinator refresh; result is a dict or an exception to raise."""
    if isinstance(result, BaseException):
        coord.client.get_energy_system_status = AsyncMock(side_effect=result)
    else:
        coord.client.get_energy_system_status = AsyncMock(return_value=dict(result))
    with patch.object(_coord_mod.time, "monotonic", clock):
        data = await coord._async_update_data()
    coord.data = data  # what HA's DataUpdateCoordinator does with the return value
    return data


# ── settings relationships ───────────────────────────────────────────────────

def test_settings_relationships():
    assert _settings.POLL_RETRY_S < _settings.SCAN_INTERVAL_S
    # longer than the longest V150 silence seen (~30 s) plus one retry round
    assert _settings.POLL_GRACE_S >= 30 + _settings.SCAN_INTERVAL_S
    # bounded: a dead battery must still leave the brain's dispatch set quickly
    assert _settings.POLL_GRACE_S <= 180


# ── coordinator grace ────────────────────────────────────────────────────────

async def test_short_silence_serves_last_reading_and_repolls_fast():
    coord, clock = _make_coordinator(), _Clock()
    assert await _poll(coord, clock, GOOD) == GOOD
    assert coord.update_interval == SCAN

    clock.t += _settings.SCAN_INTERVAL_S
    data = await _poll(coord, clock, asyncio.TimeoutError())
    assert data == GOOD, "a silence inside the grace window must keep the last reading"
    assert coord.update_interval == RETRY


async def test_recovery_restores_scan_interval():
    coord, clock = _make_coordinator(), _Clock()
    await _poll(coord, clock, GOOD)
    clock.t += _settings.SCAN_INTERVAL_S
    await _poll(coord, clock, ConnectionRefusedError())
    clock.t += _settings.POLL_RETRY_S
    fresh = {"bat_soc": 79, "ongrid_power": 120}
    assert await _poll(coord, clock, fresh) == fresh
    assert coord.update_interval == SCAN
    assert coord._silent_since is None


async def test_past_grace_goes_unavailable():
    coord, clock = _make_coordinator(), _Clock()
    await _poll(coord, clock, GOOD)
    clock.t += _settings.SCAN_INTERVAL_S
    await _poll(coord, clock, asyncio.TimeoutError())
    while True:
        clock.t += _settings.POLL_RETRY_S
        try:
            await _poll(coord, clock, asyncio.TimeoutError())
        except UpdateFailed:
            break
        assert clock.t - 1000.0 < _settings.POLL_GRACE_S, "grace window overran"
    assert clock.t - 1000.0 >= _settings.POLL_GRACE_S
    assert coord.update_interval == SCAN, "unavailable device polls at the normal cadence"


async def test_no_reading_yet_raises():
    coord, clock = _make_coordinator(), _Clock()
    try:
        await _poll(coord, clock, asyncio.TimeoutError())
        raise AssertionError("expected UpdateFailed with no reading to fall back on")
    except UpdateFailed:
        pass


# ── udp_client: refused is transient ─────────────────────────────────────────

class _RefusedProto:
    async def get_response(self):
        raise ConnectionRefusedError(111, "Connection refused")


class _OKProto:
    async def get_response(self):
        return {"id": 0, "result": {"bat_soc": 80}}


def _loop_with(protos):
    """A fake event loop whose datagram endpoints hand out `protos` in order."""
    seq = iter(protos)

    async def _create(factory, remote_addr=None):
        return MagicMock(), next(seq)

    loop = MagicMock()
    loop.create_datagram_endpoint = _create
    return loop


async def test_refused_then_ok_is_retried():
    client = MarstekUDPClient("192.0.2.10")
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    with patch("asyncio.get_event_loop", return_value=_loop_with([_RefusedProto(), _OKProto()])):
        with patch("asyncio.sleep", fake_sleep):
            result = await client._send_request("ES.GetStatus", {"id": 0})
    assert result == {"bat_soc": 80}
    assert len(slept) == 1 and 0 < slept[0] <= client.timeout, "refusal waits out its attempt window"


async def test_refused_every_attempt_raises_after_all_attempts():
    client = MarstekUDPClient("192.0.2.10")
    protos = [_RefusedProto() for _ in range(_settings.REQUEST_MAX_ATTEMPTS)]
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    with patch("asyncio.get_event_loop", return_value=_loop_with(protos)):
        with patch("asyncio.sleep", fake_sleep):
            try:
                await client._send_request("ES.GetStatus", {"id": 0})
                raise AssertionError("expected ConnectionRefusedError")
            except ConnectionRefusedError:
                pass
    # no wait after the last attempt; total stays inside ATTEMPTS × TIMEOUT
    assert len(slept) == _settings.REQUEST_MAX_ATTEMPTS - 1
    assert sum(slept) <= _settings.REQUEST_MAX_ATTEMPTS * _settings.REQUEST_TIMEOUT_S


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        if asyncio.iscoroutinefunction(t):
            asyncio.run(t())
        else:
            t()
        print(f"  PASS {t.__name__}")
    print(f"\n{len(tests)} poll-grace tests passed ✓")
