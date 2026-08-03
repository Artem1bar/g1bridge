"""CoreBluetooth-level diagnostics: what macOS itself thinks about the arms.

bleak can only tell us that a connection attempt timed out. These queries ask
CoreBluetooth directly — is Bluetooth authorized for this process, and does the
system already hold the peripherals? That distinguishes "the arms are refusing
us" from "macOS already has them", which look identical from bleak's side but
need completely different fixes.

Everything here degrades to a populated `error` field rather than raising: a
diagnostic that crashes tells you less than one that reports what it could not
determine.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field

DIAGNOSTIC_QUEUE_NAME = b"g1bridge.diagnostics"
DELEGATE_CLASS_NAME = "G1BridgeStateDelegate"
STATE_TIMEOUT_S = 8.0

# Set once the ObjC delegate class is registered; registering it twice in one
# process raises, so it is built lazily and cached.
_delegate_class = None


@dataclass(frozen=True)
class SystemPeripheral:
    """A peripheral as CoreBluetooth currently sees it."""

    identifier: str
    name: str
    state: str


@dataclass(frozen=True)
class SystemView:
    """The Mac's own view of Bluetooth and of the glasses."""

    authorization: str = "unknown"
    power: str = "unknown"
    system_connected: tuple[SystemPeripheral, ...] = field(default_factory=tuple)
    known: tuple[SystemPeripheral, ...] = field(default_factory=tuple)
    error: str | None = None


def _state_names(module) -> dict[int, str]:
    return {
        module.CBManagerStateUnknown: "unknown",
        module.CBManagerStateResetting: "resetting",
        module.CBManagerStateUnsupported: "unsupported",
        module.CBManagerStateUnauthorized: "unauthorized",
        module.CBManagerStatePoweredOff: "powered off",
        module.CBManagerStatePoweredOn: "powered on",
    }


def _authorization_names(module) -> dict[int, str]:
    return {
        module.CBManagerAuthorizationNotDetermined: "not determined (never prompted)",
        module.CBManagerAuthorizationRestricted: "restricted",
        module.CBManagerAuthorizationDenied: "DENIED — grant Bluetooth in System Settings > Privacy & Security",
        module.CBManagerAuthorizationAllowedAlways: "allowed",
    }


def _peripheral_state_names(module) -> dict[int, str]:
    return {
        module.CBPeripheralStateDisconnected: "disconnected",
        module.CBPeripheralStateConnecting: "connecting",
        module.CBPeripheralStateConnected: "CONNECTED (macOS is holding this arm)",
        module.CBPeripheralStateDisconnecting: "disconnecting",
    }


def describe_peripheral(peripheral, state_names: dict[int, str]) -> SystemPeripheral:
    """Snapshot one CBPeripheral (or any object with the same three getters)."""
    state = peripheral.state()
    return SystemPeripheral(
        identifier=str(peripheral.identifier().UUIDString()),
        name=str(peripheral.name() or "(unnamed)"),
        state=state_names.get(state, f"unknown ({state})"),
    )


def _get_delegate_class():
    """Build (once) a minimal CBCentralManagerDelegate that signals readiness."""
    global _delegate_class
    if _delegate_class is not None:
        return _delegate_class

    import objc
    from Foundation import NSObject

    class G1BridgeStateDelegate(NSObject):
        def initWithCallback_(self, callback):  # noqa: N802 - ObjC selector
            self = objc.super(G1BridgeStateDelegate, self).init()
            if self is None:
                return None
            self._callback = callback
            return self

        def centralManagerDidUpdateState_(self, _manager):  # noqa: N802
            self._callback()

    _delegate_class = G1BridgeStateDelegate
    return _delegate_class


async def inspect(
    identifiers: Sequence[str],
    service_uuid: str,
    timeout: float = STATE_TIMEOUT_S,
) -> SystemView:
    """Ask CoreBluetooth about Bluetooth authorization and the saved arms."""
    try:
        import CoreBluetooth as cb
        from Foundation import NSUUID
        from libdispatch import DISPATCH_QUEUE_SERIAL, dispatch_queue_create
    except ImportError as exc:  # not macOS, or pyobjc missing
        return SystemView(error=f"CoreBluetooth bindings unavailable ({exc})")

    authorizations = _authorization_names(cb)
    authorization = authorizations.get(cb.CBCentralManager.authorization(), "unknown")

    loop = asyncio.get_running_loop()
    ready = asyncio.Event()
    delegate = (
        _get_delegate_class()
        .alloc()
        .initWithCallback_(lambda: loop.call_soon_threadsafe(ready.set))
    )
    # A private serial queue, not the main queue: an asyncio program spins no
    # run loop, so main-queue callbacks would never be delivered.
    manager = cb.CBCentralManager.alloc().initWithDelegate_queue_(
        delegate, dispatch_queue_create(DIAGNOSTIC_QUEUE_NAME, DISPATCH_QUEUE_SERIAL)
    )

    try:
        await asyncio.wait_for(ready.wait(), timeout)
    except (TimeoutError, asyncio.TimeoutError):
        return SystemView(
            authorization=authorization,
            error=(
                f"CoreBluetooth never reported its state within {timeout:.0f}s — "
                "usually an unanswered or denied Bluetooth permission prompt for "
                "the app running this command."
            ),
        )

    power = _state_names(cb).get(manager.state(), "unknown")
    peripheral_states = _peripheral_state_names(cb)

    service = cb.CBUUID.UUIDWithString_(service_uuid)
    connected = manager.retrieveConnectedPeripheralsWithServices_([service]) or []

    wanted = [
        uuid
        for uuid in (NSUUID.alloc().initWithUUIDString_(i) for i in identifiers)
        if uuid is not None
    ]
    known = manager.retrievePeripheralsWithIdentifiers_(wanted) or [] if wanted else []

    return SystemView(
        authorization=authorization,
        power=power,
        system_connected=tuple(
            describe_peripheral(p, peripheral_states) for p in connected
        ),
        known=tuple(describe_peripheral(p, peripheral_states) for p in known),
    )
