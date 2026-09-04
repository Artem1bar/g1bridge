"""Arm lookup order: the Mac's remembered handle before any scan."""

import asyncio

import pytest

from g1bridge import ble
from g1bridge.ble import GlassArm
from g1bridge.macos import KnownDevice


def test_known_handle_wins_without_scanning(monkeypatch):
    async def fake_known(identifier, timeout=0):
        assert identifier == "ABCD"
        return KnownDevice(device="handle", state="connected")

    async def no_scan(*_args, **_kwargs):
        raise AssertionError("scan must not run when macOS holds the arm")

    monkeypatch.setattr(ble.macos, "known_device", fake_known)
    monkeypatch.setattr(ble.BleakScanner, "find_device_by_address", no_scan)
    assert asyncio.run(GlassArm("left", "ABCD")._find_device()) == "handle"


def test_scan_fallback_when_system_does_not_know_the_arm(monkeypatch):
    async def unknown(identifier, timeout=0):
        return None

    async def scan(address, timeout):
        return f"scanned:{address}"

    monkeypatch.setattr(ble.macos, "known_device", unknown)
    monkeypatch.setattr(ble.BleakScanner, "find_device_by_address", scan)
    assert asyncio.run(GlassArm("right", "EF01")._find_device()) == "scanned:EF01"


def test_not_advertising_error_names_the_phone(monkeypatch):
    async def unknown(identifier, timeout=0):
        return None

    async def nothing(address, timeout):
        return None

    monkeypatch.setattr(ble.macos, "known_device", unknown)
    monkeypatch.setattr(ble.BleakScanner, "find_device_by_address", nothing)
    with pytest.raises(RuntimeError, match="phone"):
        asyncio.run(GlassArm("left", "EF01")._find_device())
