"""What the hub needs from a display: the G1 over BLE, or the terminal simulator."""

from __future__ import annotations

from typing import Callable, Protocol

from .protocol import G1Event, ScreenStatus


class Display(Protocol):
    """Structural interface satisfied by `ble.G1Glasses` and `sim.SimGlasses`."""

    async def send_text_page(
        self,
        text: str,
        *,
        page: int = 1,
        total_pages: int = 1,
        status: ScreenStatus = ScreenStatus.AI_COMPLETE,
    ) -> None: ...

    async def send_bitmap(self, image: bytes) -> bool: ...

    def add_listener(self, listener: Callable[[G1Event], None]) -> None: ...

    async def exit_to_dashboard(self) -> None: ...

    async def set_mic(self, enable: bool) -> bool: ...
