"""Terminal stand-in for the glasses: pages render as ASCII frames, gestures are typed.

Lets the hub be developed and demoed without a BLE link. Typed gesture words:

    r / l        single tap right / left TouchBar
    rr / ll      double tap
    rrr / lll    triple tap
    hold         long-press the left TouchBar (starts Even AI on real glasses)
    release      long-press released
    on / off     glasses put on / taken off
"""

from __future__ import annotations

from typing import Callable

from .paginate import DEFAULT_CHARS_PER_LINE, DEFAULT_LINES_PER_PAGE
from .protocol import EventKind, G1Event, ScreenStatus

GESTURES: dict[str, tuple[EventKind, str]] = {
    "r": (EventKind.SINGLE_TAP, "right"),
    "l": (EventKind.SINGLE_TAP, "left"),
    "rr": (EventKind.DOUBLE_TAP, "right"),
    "ll": (EventKind.DOUBLE_TAP, "left"),
    "rrr": (EventKind.TRIPLE_TAP, "right"),
    "lll": (EventKind.TRIPLE_TAP, "left"),
    "hold": (EventKind.AI_START, "left"),
    "release": (EventKind.AI_STOP, "left"),
    "on": (EventKind.WEARING, "left"),
    "off": (EventKind.TAKEN_OFF, "left"),
}
GESTURE_HELP = (
    "gestures: r/l = tap right/left, rrr/lll = triple tap (back to menu), "
    "rr/ll = double tap (leave), hold = long-press left temple; anything else "
    "is text for the agent"
)


def parse_gesture(line: str) -> G1Event | None:
    """A typed gesture word becomes the event the glasses would have sent."""
    hit = GESTURES.get(line.strip().lower())
    if hit is None:
        return None
    kind, side = hit
    return G1Event(kind=kind, side=side, raw=b"")


def frame_page(
    text: str,
    *,
    page: int = 1,
    total_pages: int = 1,
    max_chars: int = DEFAULT_CHARS_PER_LINE,
    lines_per_page: int = DEFAULT_LINES_PER_PAGE,
) -> str:
    """Draw one HUD page as a fixed-size box with a page label on the bottom edge."""
    rows = text.split("\n")[:lines_per_page]
    padded = rows + [""] * (lines_per_page - len(rows))
    body = [f"|{row[:max_chars]:<{max_chars}}|" for row in padded]
    top = "+" + "-" * max_chars + "+"
    bottom = "+" + f" {page}/{total_pages} ".center(max_chars, "-") + "+"
    return "\n".join([top, *body, bottom])


class SimGlasses:
    """Implements `display.Display` against a terminal instead of a BLE link."""

    def __init__(
        self,
        *,
        out: Callable[[str], None] = print,
        max_chars: int = DEFAULT_CHARS_PER_LINE,
        lines_per_page: int = DEFAULT_LINES_PER_PAGE,
    ):
        self._out = out
        self.max_chars = max_chars
        self.lines_per_page = lines_per_page
        self._listeners: list[Callable[[G1Event], None]] = []
        self.pages_shown: list[str] = []
        self.dashboard_calls = 0

    async def send_text_page(
        self,
        text: str,
        *,
        page: int = 1,
        total_pages: int = 1,
        status: ScreenStatus = ScreenStatus.AI_COMPLETE,
    ) -> None:
        self.pages_shown.append(text)
        self._out(
            frame_page(
                text,
                page=page,
                total_pages=total_pages,
                max_chars=self.max_chars,
                lines_per_page=self.lines_per_page,
            )
        )

    def add_listener(self, listener: Callable[[G1Event], None]) -> None:
        self._listeners.append(listener)

    def inject(self, event: G1Event) -> None:
        """Deliver an event as if the glasses had sent it."""
        for listener in self._listeners:
            listener(event)

    async def exit_to_dashboard(self) -> None:
        self.dashboard_calls += 1
        self._out("[HUD] back to the dashboard")

    async def disconnect(self) -> None:
        return None
