"""HubSession: glues a display, the hub state machine, and a pool of Claude agents.

Two input streams feed one loop: events from the display (TouchBar gestures)
and lines from the terminal (typed questions; in simulator mode, gesture words
too). Voice input will plug in as a third source once the mic path exists.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import AsyncIterable, Callable, Protocol

from .agents import AgentSpec
from .display import Display
from .hub import (
    Action,
    HubState,
    Mode,
    close_agent,
    find_agent,
    render_menu,
    select,
    step,
)
from .hud import HudText
from .paginate import DEFAULT_CHARS_PER_LINE, DEFAULT_LINES_PER_PAGE
from .protocol import G1Event, ScreenStatus
from .sim import GESTURE_HELP, parse_gesture

logger = logging.getLogger(__name__)

THINKING_TEXT = "Thinking..."
LISTENING_TEXT = "Listening... (type your question in the terminal)"
AGENT_ERROR_TEXT = "Agent error. Check the terminal for details."
QUIT_WORDS = frozenset({"/quit", "/exit"})
BACK_WORDS = frozenset({"back", "menu", "/back", "/menu"})


class Asker(Protocol):
    """A live agent session; `agent.GlassesAgent` satisfies this."""

    async def __aenter__(self) -> "Asker": ...
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool | None: ...
    async def ask(self, prompt: str) -> str: ...


AgentFactory = Callable[[AgentSpec], Asker]


class AgentPool:
    """Opens one agent session per spec on first use; closes them all at the end."""

    def __init__(self, factory: AgentFactory):
        self._factory = factory
        self._open: dict[str, Asker] = {}

    async def get(self, spec: AgentSpec) -> Asker:
        agent = self._open.get(spec.id)
        if agent is None:
            agent = self._factory(spec)
            await agent.__aenter__()
            self._open = {**self._open, spec.id: agent}
        return agent

    async def close_all(self) -> None:
        """Close every session; one failing to close must not leak the others."""
        agents, self._open = dict(self._open), {}
        results = await asyncio.gather(
            *(agent.__aexit__(None, None, None) for agent in agents.values()),
            return_exceptions=True,
        )
        for agent_id, result in zip(agents, results):
            if isinstance(result, BaseException):
                logger.warning("agent %s did not close cleanly: %r", agent_id, result)


_Item = tuple[str, object]  # ("event", G1Event) | ("line", str) | ("eof", None)


class HubSession:
    def __init__(
        self,
        display: Display,
        agents: tuple[AgentSpec, ...],
        *,
        agent_factory: AgentFactory,
        max_chars: int = DEFAULT_CHARS_PER_LINE,
        lines_per_page: int = DEFAULT_LINES_PER_PAGE,
        say: Callable[[str], None] = print,
        sim_gestures: bool = False,
        ai_mode: bool = False,
    ):
        self._display = display
        self._state = HubState(tuple(agents))
        self._pool = AgentPool(agent_factory)
        self._hud = HudText(
            display,
            max_chars=max_chars,
            lines_per_page=lines_per_page,
            auto_page=False,
            ai_mode=ai_mode,
        )
        self._max_chars = max_chars
        self._lines_per_page = lines_per_page
        self._say = say
        self._sim_gestures = sim_gestures
        self._queue: asyncio.Queue[_Item] = asyncio.Queue()

    async def run(self, lines: AsyncIterable[str]) -> None:
        """Show the menu and serve gestures and typed lines until the hub exits."""
        self._display.add_listener(self._on_event)
        pump = asyncio.create_task(self._pump(lines))
        try:
            await self._show_menu()
            self._say(self._menu_help())
            while True:
                kind, payload = await self._queue.get()
                if kind == "eof":
                    await self._exit()
                    break
                handler = self._handle_event if kind == "event" else self._handle_line
                if not await handler(payload):
                    break
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            await self._pool.close_all()

    # -- input plumbing ---------------------------------------------------

    def _on_event(self, event: G1Event) -> None:
        self._queue.put_nowait(("event", event))

    async def _pump(self, lines: AsyncIterable[str]) -> None:
        try:
            async for line in lines:
                self._queue.put_nowait(("line", line))
        finally:
            self._queue.put_nowait(("eof", None))

    # -- handlers: return False to stop the session -----------------------

    async def _handle_event(self, event: G1Event) -> bool:
        self._state, action = step(self._state, event)
        return await self._act(action)

    async def _handle_line(self, line: str) -> bool:
        text = line.strip()
        if not text:
            return True
        if text in QUIT_WORDS:
            return await self._exit()
        if self._sim_gestures and (event := parse_gesture(text)) is not None:
            return await self._handle_event(event)
        if self._state.mode is Mode.MENU:
            return await self._pick_by_text(text)
        if text.lower() in BACK_WORDS:
            self._state, action = close_agent(self._state)
            return await self._act(action)
        await self._ask(text)
        return True

    async def _pick_by_text(self, text: str) -> bool:
        index = find_agent(self._state.agents, text)
        if index is None:
            self._say(f"No agent called {text!r}. {self._menu_help()}")
            return True
        self._state, action = select(self._state, index)
        return await self._act(action)

    async def _act(self, action: Action) -> bool:
        if action in (Action.SHOW_MENU, Action.CLOSE_AGENT):
            await self._show_menu()
        elif action is Action.OPEN_AGENT:
            await self._open_agent()
        elif action is Action.EXIT_HUB:
            return await self._exit()
        elif action is Action.PAGE_NEXT:
            await self._hud.page(+1)
        elif action is Action.PAGE_PREV:
            await self._hud.page(-1)
        elif action is Action.NEW_PROMPT:
            await self._hud.show(LISTENING_TEXT)
        return True

    # -- screens ----------------------------------------------------------

    async def _show_menu(self) -> None:
        menu = render_menu(
            self._state, lines_per_page=self._lines_per_page, max_chars=self._max_chars
        )
        await self._display.send_text_page(menu, status=ScreenStatus.TEXT_SHOW)

    async def _open_agent(self) -> None:
        spec = self._state.selected
        await self._hud.show(f"{spec.name}\n{spec.blurb}\nSpeak or type your question.")
        back = "rrr" if self._sim_gestures else "triple-tap"
        self._say(f"[{spec.name}] type a question; 'back' or {back} = menu")

    async def _ask(self, text: str) -> None:
        spec = self._state.selected
        await self._hud.show(THINKING_TEXT)
        try:
            agent = await self._pool.get(spec)
            answer = await agent.ask(text)
        except Exception:  # keep the hub alive; the terminal gets the traceback
            logger.exception("agent %s failed", spec.id)
            answer = AGENT_ERROR_TEXT
        self._say(f"{spec.name}> {answer}\n")
        pages = await self._hud.show(answer)
        if pages > 1:
            self._say(f"({pages} pages on the HUD: tap right for next, left for back)")

    async def _exit(self) -> bool:
        await self._display.exit_to_dashboard()
        return False

    def _menu_help(self) -> str:
        names = ", ".join(
            f"{index}={spec.name}" for index, spec in enumerate(self._state.agents, 1)
        )
        typed = f"Type a number or name to open an agent ({names}); /quit to leave."
        if self._sim_gestures:
            return f"{typed} {GESTURE_HELP}."
        return (
            f"{typed} On the glasses: tap right/left to move, long-press left to "
            "open, double-tap to leave."
        )
