"""HudText: which screen status each page carries."""

import asyncio

from g1bridge.hud import HudText
from g1bridge.protocol import ScreenStatus
from g1bridge.sim import SimGlasses


class StatusSpy(SimGlasses):
    def __init__(self):
        super().__init__(out=lambda _: None, max_chars=10, lines_per_page=1)
        self.statuses: list[ScreenStatus] = []

    async def send_text_page(self, text, *, page=1, total_pages=1, status=None):
        self.statuses.append(status)
        await super().send_text_page(
            text, page=page, total_pages=total_pages, status=status
        )


def test_text_show_is_the_default_for_every_page():
    spy = StatusSpy()
    hud = HudText(spy, max_chars=10, lines_per_page=1, auto_page=False)

    async def go():
        await hud.show("one two")  # wraps into two pages at 10 chars? no: 7 chars
        await hud.show("a\nb")  # two lines, one per page
        await hud.page(+1)

    asyncio.run(go())
    assert spy.statuses == [ScreenStatus.TEXT_SHOW] * 3


def test_ai_mode_uses_even_ai_statuses():
    spy = StatusSpy()
    hud = HudText(spy, max_chars=10, lines_per_page=1, auto_page=False, ai_mode=True)

    async def go():
        await hud.show("a\nb")
        await hud.page(+1)

    asyncio.run(go())
    assert spy.statuses == [ScreenStatus.AI_DISPLAYING, ScreenStatus.AI_MANUAL]
