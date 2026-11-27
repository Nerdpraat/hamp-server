import pytest


@pytest.fixture(autouse=True)
def _allow_local_sockets(socket_enabled):
    """The forwarder tests talk to an in-process fake hub on localhost.

    pytest-homeassistant-custom-component blocks sockets by default.
    """
    yield


@pytest.fixture
def expected_lingering_timers() -> bool:
    """aiohttp deliberately leaves a stale (harmless) websocket heartbeat timer behind."""
    return True
