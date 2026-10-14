"""Shared fixtures for the pysovol tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestServer

# Import pysovol on its own, without Home Assistant.
sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "sovol_sv08"))

from fake_moonraker import FakeMoonraker  # noqa: E402


@pytest.fixture
async def moonraker():
    fake = FakeMoonraker()
    server = TestServer(fake.app)
    await server.start_server()
    fake.port = server.port
    yield fake
    await server.close()


@pytest.fixture
async def client(moonraker):
    from pysovol import SovolClient

    sv08 = SovolClient("127.0.0.1", moonraker.port)
    assert await sv08.async_init()
    yield sv08
    await sv08.async_stop()
