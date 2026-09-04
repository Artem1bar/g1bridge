"""Tests for the pure parts of the CoreBluetooth diagnostics.

`inspect()` itself talks to the OS and is exercised on hardware via `g1 probe`;
what is testable here is the snapshotting of a peripheral, which is where a
silent mislabel would send an investigation down the wrong path.
"""

from g1bridge.macos import SystemView, describe_peripheral

STATE_NAMES = {0: "disconnected", 2: "connected"}


class FakeUUID:
    def __init__(self, value: str):
        self._value = value

    def UUIDString(self) -> str:  # noqa: N802 - mirrors the ObjC selector
        return self._value


class FakePeripheral:
    """Duck-types the three CBPeripheral getters the snapshot uses."""

    def __init__(self, identifier: str, name: str | None, state: int):
        self._identifier = identifier
        self._name = name
        self._state = state

    def identifier(self) -> FakeUUID:
        return FakeUUID(self._identifier)

    def name(self) -> str | None:
        return self._name

    def state(self) -> int:
        return self._state


def test_describe_peripheral_maps_known_state():
    peripheral = FakePeripheral("AC01E669-8D23", "Even G1_24_L_8EDD5D", 2)

    described = describe_peripheral(peripheral, STATE_NAMES)

    assert described.identifier == "AC01E669-8D23"
    assert described.name == "Even G1_24_L_8EDD5D"
    assert described.state == "connected"


def test_describe_peripheral_keeps_unknown_state_visible():
    described = describe_peripheral(FakePeripheral("id", "arm", 9), STATE_NAMES)

    assert described.state == "unknown (9)"


def test_describe_peripheral_handles_missing_name():
    described = describe_peripheral(FakePeripheral("id", None, 0), STATE_NAMES)

    assert described.name == "(unnamed)"


def test_system_view_defaults_are_empty_not_none():
    view = SystemView()

    assert view.system_connected == ()
    assert view.known == ()
    assert view.error is None


def test_signal_hint_threshold():
    from g1bridge.ble import WEAK_RSSI_DBM, signal_hint

    assert signal_hint(None) is None
    assert signal_hint(-66) is None
    assert signal_hint(WEAK_RSSI_DBM + 1) is None
    hint = signal_hint(-90)
    assert hint is not None and "-90 dBm" in hint and "30 cm" in hint


def test_known_device_rejects_malformed_identifier_without_touching_bluetooth():
    import asyncio

    from g1bridge.macos import known_device

    # A bad UUID string must come back None before any CBCentralManager exists
    # (creating one would raise the OS permission prompt in a headless run).
    assert asyncio.run(known_device("not-a-uuid")) is None
