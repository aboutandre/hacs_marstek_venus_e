"""Unit tests: a device RESPONSE is not an ACK (audit F-04) + UDP dup guard (F-12).

Covers:
  - coordinator.set_passive_mode / set_mode raise when the Marstek replies
    {"set_result": false} — so the services report {ok: false} and an external
    brain (Wattsmith) sees the rejection instead of a phantom ack,
  - set_passive_mode does NOT trigger a data refresh (F-10: the brain re-arms
    setpoints every few seconds; refresh-per-write doubled the radio traffic),
  - _UDPClientProtocol drops duplicated datagrams instead of raising
    InvalidStateError on the already-resolved future.

Headless — no Home Assistant, no network.

Run directly:   python3 tests/test_set_result_ack.py
Or with pytest: pytest tests/test_set_result_ack.py
"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

# ── stub homeassistant + package imports ─────────────────────────────────────
sys.modules["homeassistant"] = MagicMock()
sys.modules["homeassistant.core"] = MagicMock()
sys.modules["homeassistant.config_entries"] = MagicMock()

_uc = ModuleType("homeassistant.helpers.update_coordinator")


class DataUpdateCoordinator:
    def __init__(self, hass, logger, name=None, update_interval=None):
        self.hass = hass
        self.data = {}


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

_settings = ModuleType("hacs_marstek_venus_e.settings")
_settings.DEFAULT_PORT = 30000
_settings.REQUEST_TIMEOUT_S = 2.0
_settings.REQUEST_MAX_ATTEMPTS = 3
_settings.REQUEST_MIN_GAP_S = 0.2
_settings.SCAN_INTERVAL_S = 60
_settings.MODE_POLL_INTERVAL_S = 300
_settings.BATTERY_INFO_INTERVAL_S = 3600
_settings.POLL_RETRY_S = 10
_settings.POLL_GRACE_S = 120
sys.modules["hacs_marstek_venus_e.settings"] = _settings

_base = Path(__file__).resolve().parents[1] / "custom_components" / "hacs_marstek_venus_e"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"hacs_marstek_venus_e.{name}", _base / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "hacs_marstek_venus_e"
    sys.modules[f"hacs_marstek_venus_e.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


_udp = _load("udp_client")
_coord_mod = _load("coordinator")

MarstekDataUpdateCoordinator = _coord_mod.MarstekDataUpdateCoordinator
_UDPClientProtocol = _udp._UDPClientProtocol


def _make_coordinator():
    entry = MagicMock()
    entry.options = {}
    entry.data = {"ip_address": "192.0.2.10", "port": 30000}
    coord = MarstekDataUpdateCoordinator(MagicMock(), entry)
    coord.client = MagicMock()
    coord.async_request_refresh = AsyncMock()
    return coord


def _run(coro):
    return asyncio.run(coro)


# ── set_result rejection → raises (services turn this into ok:false) ─────────

def test_passive_mode_rejection_raises():
    coord = _make_coordinator()
    coord.client.set_passive_mode = AsyncMock(return_value={"set_result": False})
    try:
        _run(coord.set_passive_mode(power=500, cd_time=10))
        raise AssertionError("expected rejection to raise")
    except Exception as err:
        assert "set_result" in str(err)


def test_passive_mode_accepted_ok():
    coord = _make_coordinator()
    coord.client.set_passive_mode = AsyncMock(return_value={"set_result": True})
    _run(coord.set_passive_mode(power=500, cd_time=10))  # no raise


def test_passive_mode_missing_set_result_is_permissive():
    # older firmware may omit the field — only an explicit false is a rejection
    coord = _make_coordinator()
    coord.client.set_passive_mode = AsyncMock(return_value={})
    _run(coord.set_passive_mode(power=500, cd_time=10))  # no raise


def test_passive_mode_does_not_refresh():
    # F-10: no data refresh per setpoint write (the brain writes every ≤7 s)
    coord = _make_coordinator()
    coord.client.set_passive_mode = AsyncMock(return_value={"set_result": True})
    _run(coord.set_passive_mode(power=500, cd_time=10))
    coord.async_request_refresh.assert_not_awaited()


def test_set_mode_rejection_raises():
    coord = _make_coordinator()
    coord.client.set_mode = AsyncMock(return_value={"set_result": False})
    try:
        _run(coord.set_mode("Auto"))
        raise AssertionError("expected rejection to raise")
    except Exception as err:
        assert "set_result" in str(err)


def test_set_mode_accepted_still_refreshes():
    # mode changes are rare — the refresh there is fine and stays
    coord = _make_coordinator()
    coord.client.set_mode = AsyncMock(return_value={"set_result": True})
    _run(coord.set_mode("Auto"))
    coord.async_request_refresh.assert_awaited()


# ── duplicated datagram guard (F-12) ─────────────────────────────────────────

def test_duplicate_datagram_does_not_raise():
    async def scenario():
        proto = _UDPClientProtocol(0)
        payload = json.dumps({"id": 0, "result": {"set_result": True}}).encode()
        proto.datagram_received(payload, ("192.0.2.10", 30000))
        proto.datagram_received(payload, ("192.0.2.10", 30000))  # duplicate — must not raise
        resp = await proto.get_response()
        assert resp["result"]["set_result"] is True

    _run(scenario())


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS {t.__name__}")
    print(f"\n{len(tests)} set_result/ack tests passed ✓")
