"""Fixtures for loading the integration into a real Home Assistant."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestServer
from pytest_homeassistant_custom_component.common import MockConfigEntry

sys.path.insert(0, str(Path(__file__).parents[1] / "tests"))

from fake_moonraker import FakeMoonraker  # noqa: E402

from custom_components.sovol_sv08.const import DOMAIN  # noqa: E402


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/sovol_sv08."""
    return


@pytest.fixture
async def moonraker(socket_enabled):
    fake = FakeMoonraker()
    server = TestServer(fake.app, host="127.0.0.1")
    await server.start_server()
    fake.port = server.port
    yield fake
    await server.close()


@pytest.fixture
def entry(moonraker) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Sovol SV08",
        unique_id=f"127.0.0.1:{moonraker.port}",
        data={"host": "127.0.0.1", "port": moonraker.port, "api_key": None},
    )


@pytest.fixture
async def setup_entry(hass, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
