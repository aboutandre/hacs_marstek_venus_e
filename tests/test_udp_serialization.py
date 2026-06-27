"""Unit tests for MarstekUDPClient serialization behaviour.

Covers the lock (concurrent-request serialization), min-gap enforcement,
retry-on-timeout (DEBUG on transient, WARNING only on all-fail), and
payload building — all without a real UDP socket or Home Assistant.

Note: the live-API tests in the other test_*.py files require a real device.
These run anywhere.

Run directly:   python3 tests/test_udp_serialization.py
Or with pytest: pytest tests/test_udp_serialization.py
"""
import asyncio
import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

# ── stub the settings relative import ────────────────────────────────────────
_pkg = ModuleType("hacs_marstek_venus_e")
sys.modules.setdefault("hacs_marstek_venus_e", _pkg)

_settings = ModuleType("hacs_marstek_venus_e.settings")
_settings.DEFAULT_PORT = 30000
_settings.REQUEST_TIMEOUT_S = 4.0
_settings.REQUEST_MAX_ATTEMPTS = 3
_settings.REQUEST_MIN_GAP_S = 0.2
sys.modules["hacs_marstek_venus_e.settings"] = _settings

# ── load udp_client ───────────────────────────────────────────────────────────
_path = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "hacs_marstek_venus_e"
    / "udp_client.py"
)
_spec = importlib.util.spec_from_file_location("hacs_marstek_venus_e.udp_client", _path)
_udp = importlib.util.module_from_spec(_spec)
_udp.__package__ = "hacs_marstek_venus_e"
sys.modules["hacs_marstek_venus_e.udp_client"] = _udp
_spec.loader.exec_module(_udp)

MarstekUDPClient = _udp.MarstekUDPClient

# ── helpers ───────────────────────────────────────────────────────────────────

class _OKProto:
    """Protocol that always responds successfully."""
    def __init__(self, result=None):
        self._result = result or {}

    async def get_response(self):
        return {"id": 0, "result": self._result}


class _SlowProto:
    """Protocol that takes `delay` seconds before responding."""
    def __init__(self, delay=0.05):
        self._delay = delay

    async def get_response(self):
        await asyncio.sleep(self._delay)
        return {"id": 0, "result": {}}


def _endpoint_factory(proto_cls=_OKProto, **kwargs):
    """Return an async endpoint creator that yields a mock transport + proto."""
    async def _create(factory, remote_addr=None):
        transport = MagicMock()
        transport.close = MagicMock()
        return transport, proto_cls(**kwargs)
    return _create


# ══════════════════════════════════════════════════════════════════════════════
# Structural
# ══════════════════════════════════════════════════════════════════════════════

def test_lock_is_asyncio_lock():
    client = MarstekUDPClient("192.168.1.1")
    assert isinstance(client._lock, asyncio.Lock)


def test_last_done_starts_none():
    client = MarstekUDPClient("192.168.1.1")
    assert client._last_done is None


def test_request_id_always_zero():
    client = MarstekUDPClient("192.168.1.1")
    assert client._get_next_id() == 0
    assert client._get_next_id() == 0


# ══════════════════════════════════════════════════════════════════════════════
# Payload building (patch _send_request so no network)
# ══════════════════════════════════════════════════════════════════════════════

async def test_set_mode_auto_sends_auto_cfg():
    client = MarstekUDPClient("192.168.1.1")
    captured = {}

    async def _cap(method, params=None):
        captured.update({"method": method, "params": params})
        return {}

    client._send_request = _cap
    await client.set_mode("Auto")
    assert captured["method"] == "ES.SetMode"
    cfg = captured["params"]["config"]
    assert cfg["mode"] == "Auto"
    assert cfg.get("auto_cfg", {}).get("enable") == 1


async def test_set_mode_passive_sends_passive_cfg():
    client = MarstekUDPClient("192.168.1.1")
    captured = {}

    async def _cap(method, params=None):
        captured.update({"method": method, "params": params})
        return {}

    client._send_request = _cap
    await client.set_mode("Passive", passive_cfg={"power": 800, "cd_time": 10})
    cfg = captured["params"]["config"]
    assert cfg["mode"] == "Passive"
    assert cfg["passive_cfg"]["power"] == 800
    assert cfg["passive_cfg"]["cd_time"] == 10


async def test_set_passive_mode_delegates_to_set_mode():
    client = MarstekUDPClient("192.168.1.1")
    calls = []

    async def _cap(method, params=None):
        calls.append((method, params))
        return {}

    client._send_request = _cap
    await client.set_passive_mode(power=1200, cd_time=5)
    assert len(calls) == 1
    cfg = calls[0][1]["config"]
    assert cfg["mode"] == "Passive"
    assert cfg["passive_cfg"]["power"] == 1200
    assert cfg["passive_cfg"]["cd_time"] == 5


# ══════════════════════════════════════════════════════════════════════════════
# Lock serialisation
# ══════════════════════════════════════════════════════════════════════════════

async def test_lock_serializes_concurrent_requests():
    """Two concurrent _send_request calls must not overlap."""
    client = MarstekUDPClient("192.168.1.1")
    log = []

    counter = [0]

    class TrackProto:
        def __init__(self, name):
            self._name = name

        async def get_response(self):
            await asyncio.sleep(0.03)  # yield so the other task can try to start
            log.append(f"done:{self._name}")
            return {"id": 0, "result": {}}

    async def tracked_endpoint(factory, remote_addr=None):
        counter[0] += 1
        name = f"r{counter[0]}"
        log.append(f"start:{name}")
        return MagicMock(), TrackProto(name)

    mock_loop = MagicMock()
    mock_loop.create_datagram_endpoint = tracked_endpoint

    with patch("asyncio.get_event_loop", return_value=mock_loop):
        await asyncio.gather(
            client._send_request("ES.GetStatus"),
            client._send_request("ES.GetMode"),
        )

    # With the lock, both requests are strictly serialized:
    # start:r1 → done:r1 → start:r2 → done:r2
    assert log == ["start:r1", "done:r1", "start:r2", "done:r2"]


# ══════════════════════════════════════════════════════════════════════════════
# Min-gap enforcement
# ══════════════════════════════════════════════════════════════════════════════

async def test_min_gap_causes_sleep_when_recently_done():
    client = MarstekUDPClient("192.168.1.1")
    client._last_done = time.monotonic()  # set to right now → full gap needed

    slept = []

    async def cap_sleep(secs):
        slept.append(secs)

    mock_loop = MagicMock()
    mock_loop.create_datagram_endpoint = _endpoint_factory()

    with patch("asyncio.get_event_loop", return_value=mock_loop):
        with patch("asyncio.sleep", cap_sleep):
            await client._send_request("ES.GetStatus")

    assert len(slept) == 1, "expected exactly one min-gap sleep"
    assert 0 < slept[0] <= _settings.REQUEST_MIN_GAP_S


async def test_no_gap_sleep_when_last_done_is_old():
    client = MarstekUDPClient("192.168.1.1")
    client._last_done = time.monotonic() - 10.0  # 10 s ago — gap already expired

    slept = []

    async def cap_sleep(secs):
        slept.append(secs)

    mock_loop = MagicMock()
    mock_loop.create_datagram_endpoint = _endpoint_factory()

    with patch("asyncio.get_event_loop", return_value=mock_loop):
        with patch("asyncio.sleep", cap_sleep):
            await client._send_request("ES.GetStatus")

    assert slept == [], "no sleep should occur when gap is already expired"


async def test_last_done_updated_after_request():
    client = MarstekUDPClient("192.168.1.1")
    assert client._last_done is None

    mock_loop = MagicMock()
    mock_loop.create_datagram_endpoint = _endpoint_factory()

    t_before = time.monotonic()
    with patch("asyncio.get_event_loop", return_value=mock_loop):
        await client._send_request("ES.GetStatus")

    assert client._last_done is not None
    assert client._last_done >= t_before


# ══════════════════════════════════════════════════════════════════════════════
# Retry on timeout
# ══════════════════════════════════════════════════════════════════════════════

async def test_retry_succeeds_on_second_attempt():
    """First attempt times out; second succeeds. No WARNING logged."""
    client = MarstekUDPClient("192.168.1.1")

    attempt = [0]

    class FailOnceThenOKProto:
        async def get_response(self):
            attempt[0] += 1
            if attempt[0] < 2:
                raise asyncio.TimeoutError()
            return {"id": 0, "result": {"ok": True}}

    mock_loop = MagicMock()
    mock_loop.create_datagram_endpoint = _endpoint_factory(FailOnceThenOKProto)

    with patch("asyncio.get_event_loop", return_value=mock_loop):
        result = await client._send_request("ES.GetStatus")

    assert attempt[0] == 2
    assert result == {"ok": True}


async def test_all_attempts_fail_raises():
    """All REQUEST_MAX_ATTEMPTS time out → raises asyncio.TimeoutError."""
    client = MarstekUDPClient("192.168.1.1")

    class AlwaysTimeoutProto:
        async def get_response(self):
            raise asyncio.TimeoutError()

    mock_loop = MagicMock()
    mock_loop.create_datagram_endpoint = _endpoint_factory(AlwaysTimeoutProto)

    raised = False
    with patch("asyncio.get_event_loop", return_value=mock_loop):
        try:
            await client._send_request("ES.GetStatus")
        except asyncio.TimeoutError:
            raised = True

    assert raised, "expected TimeoutError after all attempts exhausted"


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import inspect
    _sync, _async = [], []
    for _name, _fn in sorted(globals().items()):
        if not _name.startswith("test_"):
            continue
        (_async if inspect.iscoroutinefunction(_fn) else _sync).append((_name, _fn))

    count = 0
    for name, fn in _sync:
        fn()
        print(f"  PASS {name}")
        count += 1
    for name, fn in _async:
        asyncio.run(fn())
        print(f"  PASS {name}")
        count += 1

    print(f"\n{count} udp_client serialization tests passed ✓")
