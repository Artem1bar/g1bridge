"""A dropped arm is retried with a growing pause until it is back."""

import asyncio

from g1bridge import ble
from g1bridge.ble import G1Glasses


def test_dropped_arm_is_reconnected(monkeypatch):
    monkeypatch.setattr(ble, "RECONNECT_PAUSES_S", (0.01, 0.01, 0.01))
    glasses = G1Glasses("L", "R", "left", "right")
    attempts_log: list[int] = []
    state = {"connected": True}

    async def connect(attempts: int = 1) -> None:
        attempts_log.append(attempts)
        if len(attempts_log) < 3:
            raise RuntimeError("still dozing")
        state["connected"] = True

    monkeypatch.setattr(glasses.left, "connect", connect)
    monkeypatch.setattr(
        type(glasses.left), "is_connected", property(lambda self: state["connected"])
    )

    async def go():
        state["connected"] = False
        glasses.left._handle_disconnect(None)  # what bleak does when the link drops
        glasses.left._handle_disconnect(
            None
        )  # a duplicate must not start a second loop
        assert len(glasses._reconnects) == 1
        await asyncio.sleep(0.2)
        assert attempts_log == [1, 1, 1] and state["connected"]
        await glasses.disconnect()

    asyncio.run(go())


def test_no_reconnect_after_disconnect_was_requested(monkeypatch):
    glasses = G1Glasses("L", "R", "left", "right")
    started: list[str] = []

    async def connect(attempts: int = 1) -> None:
        started.append("x")

    monkeypatch.setattr(glasses.right, "connect", connect)

    async def go():
        await glasses.disconnect()  # sets closing
        glasses.right._handle_disconnect(None)
        await asyncio.sleep(0.05)
        assert glasses._reconnects == {} and started == []

    asyncio.run(go())
