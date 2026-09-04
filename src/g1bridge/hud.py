"""HUD text session: pagination state plus TouchBar paging."""

from __future__ import annotations

import asyncio
import logging

from .display import Display
from .paginate import DEFAULT_CHARS_PER_LINE, DEFAULT_LINES_PER_PAGE, paginate
from .protocol import EventKind, G1Event, ScreenStatus

logger = logging.getLogger(__name__)


class HudText:
    """Displays paginated text on the glasses; left/right taps page through it."""

    def __init__(
        self,
        glasses: Display,
        *,
        max_chars: int = DEFAULT_CHARS_PER_LINE,
        lines_per_page: int = DEFAULT_LINES_PER_PAGE,
        auto_page: bool = True,
        ai_mode: bool = False,
    ):
        self.glasses = glasses
        self.max_chars = max_chars
        self.lines_per_page = lines_per_page
        # Even AI statuses (0x3x/0x4x/0x5x) belong to the voice-reply flow; plain
        # "Text Show" (0x7x) is the mode for everything else. Hardware 2026-09-03:
        # an AI-status page put the left arm into "Even AI is listening".
        self.ai_mode = ai_mode
        self.pages: list[str] = []
        self.index = 0
        # The hub routes taps itself; standalone commands let taps page directly.
        if auto_page:
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
        if not self.ai_mode:
            status = ScreenStatus.TEXT_SHOW
        elif initial:
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

    async def preview(self, text: str) -> str:
        """Show the first page of a still-growing answer; pagination state untouched.

        Returns the page text sent, so callers can skip unchanged previews.
        """
        pages = paginate(
            text, max_chars=self.max_chars, lines_per_page=self.lines_per_page
        )
        if not pages:
            return ""
        status = ScreenStatus.AI_DISPLAYING if self.ai_mode else ScreenStatus.TEXT_SHOW
        await self.glasses.send_text_page(
            pages[0], page=1, total_pages=1, status=status
        )
        return pages[0]

    async def page(self, step: int) -> bool:
        """Move `step` pages (negative = back). Returns False if nothing changed."""
        if len(self.pages) < 2:
            return False
        new_index = min(max(self.index + step, 0), len(self.pages) - 1)
        if new_index == self.index:
            return False
        self.index = new_index
        logger.debug("paging to %d/%d", self.index + 1, len(self.pages))
        await self._render()
        return True

    def _handle_event(self, event: G1Event) -> None:
        if event.kind is not EventKind.SINGLE_TAP:
            return
        # Stock Even AI convention: right TouchBar pages forward, left pages back.
        step = 1 if event.side == "right" else -1
        asyncio.get_running_loop().create_task(self.page(step))
