"""HUD text session: pagination state plus TouchBar paging."""

from __future__ import annotations

import asyncio
import logging

from .ble import G1Glasses
from .paginate import DEFAULT_CHARS_PER_LINE, DEFAULT_LINES_PER_PAGE, paginate
from .protocol import EventKind, G1Event, ScreenStatus

logger = logging.getLogger(__name__)


class HudText:
    """Displays paginated text on the glasses; left/right taps page through it."""

    def __init__(
        self,
        glasses: G1Glasses,
        *,
        max_chars: int = DEFAULT_CHARS_PER_LINE,
        lines_per_page: int = DEFAULT_LINES_PER_PAGE,
    ):
        self.glasses = glasses
        self.max_chars = max_chars
        self.lines_per_page = lines_per_page
        self.pages: list[str] = []
        self.index = 0
        glasses.add_listener(self._handle_event)

    async def show(self, text: str) -> int:
        """Paginate `text` and display page 1. Returns the page count."""
        self.pages = paginate(
            text, max_chars=self.max_chars, lines_per_page=self.lines_per_page
        )
        self.index = 0
        if not self.pages:
            return 0
        await self._render(initial=True)
        return len(self.pages)

    async def _render(self, initial: bool = False) -> None:
        total = len(self.pages)
        on_last_page = self.index == total - 1
        if initial:
            status = (
                ScreenStatus.AI_COMPLETE if on_last_page else ScreenStatus.AI_DISPLAYING
            )
        else:
            status = ScreenStatus.AI_MANUAL
        await self.glasses.send_text_page(
            self.pages[self.index],
            page=self.index + 1,
            total_pages=total,
            status=status,
        )

    def _handle_event(self, event: G1Event) -> None:
        if event.kind is not EventKind.SINGLE_TAP or len(self.pages) < 2:
            return
        # Stock Even AI convention: right TouchBar pages forward, left pages back.
        step = 1 if event.side == "right" else -1
        new_index = min(max(self.index + step, 0), len(self.pages) - 1)
        if new_index == self.index:
            return
        self.index = new_index
        logger.debug("paging to %d/%d", self.index + 1, len(self.pages))
        asyncio.get_running_loop().create_task(self._render())
