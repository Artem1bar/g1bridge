"""HubSession: glues a display, the hub state machine, and a pool of Claude agents.

Two input streams feed one loop: events from the display (TouchBar gestures)
and lines from the terminal (typed questions; in simulator mode, gesture words
too). Voice input will plug in as a third source once the mic path exists.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime
from pathlib import Path
from typing import AsyncIterable, Callable, Protocol

from .agents import AgentSpec
from .display import Display
from .hub import (
    Action,
    HubState,
    Mode,
    close_agent,
    find_agent,
    go_home,
    parse_voice,
    render_home,
    render_menu,
    select,
    step,
)
from .hud import HudText
from .paginate import DEFAULT_CHARS_PER_LINE, DEFAULT_LINES_PER_PAGE
from .protocol import EventKind, G1Event, ScreenStatus
from .sim import GESTURE_HELP, parse_gesture
from .voice import PACKET_SECONDS, QUIET_RMS, Capture, Transcriber, packet_rms

logger = logging.getLogger(__name__)

THINKING_TEXT = "Thinking..."
LISTENING_TEXT = "Listening... release to send."
TYPE_TEXT = "Voice not set up yet.\nType your question in the terminal."
TRANSCRIBING_TEXT = "Heard you. Transcribing..."
TOO_SHORT_TEXT = "Didn't catch that.\nHold the left temple and speak."
MAX_CAPTURE_S = 20.0  # finish on our own if the firmware never reports the release
# After the firmware reports the release the stream keeps going; keep collecting
# until the wearer has been quiet for TAIL_QUIET_S, or TAIL_MAX_S at most.
# Hardware 2026-09-03: the release came after ~1 s while the wearer was still talking.
TAIL_QUIET_S = 1.0
TAIL_MAX_S = 6.0
REST_HINT = "At rest on the glasses' dashboard: hold the left temple and ask."
AGENT_ERROR_TEXT = "Agent error. Check the terminal for details."
QUIT_WORDS = frozenset({"/quit", "/exit"})
QUIET_EVENTS = frozenset(
    {EventKind.MIC_DATA, EventKind.HEARTBEAT_ACK, EventKind.TEXT_ACK}
)
BACK_WORDS = frozenset({"back", "/back"})
MENU_WORDS = frozenset({"menu", "agents", "/menu"})
HOME_WORDS = frozenset({"home", "/home"})


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
        now: Callable[[], datetime] = datetime.now,
        transcriber: Transcriber | None = None,
        trace: bool = False,
        rest: bool = False,
        mic_cmd: bool = False,
        tail_quiet_s: float = TAIL_QUIET_S,
        tail_max_s: float = TAIL_MAX_S,
        record_dir: Path | None = None,
    ):
        self._display = display
        mode = Mode.REST if rest else Mode.HOME
        self._state = HubState(tuple(agents), mode=mode, rest=rest)
        # Hardware 2026-09-03: the glasses stream the mic on a long-press by
        # themselves, and after an explicit 0x0E 0x01 they never report the
        # release. So the documented mic-on command is opt-in.
        self._mic_cmd = mic_cmd
        self._tail_quiet_s = tail_quiet_s
        self._tail_max_s = tail_max_s
        self._record_dir = record_dir
        self._closing = False  # release seen; finishing once the wearer is quiet
        self._quiet_packets = 0
        self._finish_token = 0  # stale backstop timers must not end a new capture
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
        self._now = now
        self._transcriber = transcriber
        self._trace = trace  # echo every gesture event to the terminal
        self._ai_mode = ai_mode
        self._capture: Capture | None = None  # not None while the mic is open
        self._mic_seen = 0  # packets since the last trace line
        self._queue: asyncio.Queue[_Item] = asyncio.Queue()

    async def run(self, lines: AsyncIterable[str]) -> None:
        """Show the menu and serve gestures and typed lines until the hub exits."""
        self._display.add_listener(self._on_event)
        pump = asyncio.create_task(self._pump(lines))
        clock = asyncio.create_task(self._clock())
        try:
            if self._state.rest:
                self._say(REST_HINT)
            else:
                await self._show_home()
            self._say(self._menu_help())
            while True:
                kind, payload = await self._queue.get()
                if kind == "eof":
                    if self._capture is not None:  # send what was heard first
                        await self._finish_listening()
                    await self._exit()
                    break
                if kind == "finish":
                    if payload == self._finish_token and self._closing:
                        await self._finish_listening()
                    continue
                handler = self._handle_event if kind == "event" else self._handle_line
                try:
                    keep_going = await handler(payload)
                except Exception:  # a BLE write failing must not kill the hub
                    logger.exception("hub handler failed; still running")
                    self._say("(that one failed; see the log above. Still listening.)")
                    continue
                if not keep_going:
                    break
        finally:
            for task in (pump, clock):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await self._pool.close_all()

    # -- input plumbing ---------------------------------------------------

    def _on_event(self, event: G1Event) -> None:
        self._queue.put_nowait(("event", event))

    async def _clock(self) -> None:
        """Redraw the home screen on every minute boundary while it is showing."""
        while True:
            await asyncio.sleep(60 - self._now().second)
            if self._state.mode is Mode.HOME:
                await self._show_home()

    async def _pump(self, lines: AsyncIterable[str]) -> None:
        try:
            async for line in lines:
                self._queue.put_nowait(("line", line))
        finally:
            self._queue.put_nowait(("eof", None))

    # -- handlers: return False to stop the session -----------------------

    async def _handle_event(self, event: G1Event) -> bool:
        if self._trace and event.kind not in QUIET_EVENTS:
            detail = (
                f" raw={event.raw.hex()}" if event.kind is EventKind.UNKNOWN else ""
            )
            self._say(f"  [{event.side:>5}] {event.kind.value}{detail}")
        if event.kind is EventKind.MIC_DATA:
            return await self._on_mic_packet(event)
        if event.kind is EventKind.AI_STOP:
            if self._tail_max_s <= 0:
                return await self._finish_listening()
            self._schedule_finish()
            return True
        if self._capture is not None and event.kind is EventKind.AI_START:
            # The firmware did not report the release (seen on hardware once the
            # mic is enabled): a second press means "send what you heard".
            return await self._finish_listening()
        if self._capture is not None and event.kind is EventKind.DOUBLE_TAP:
            await self._cancel_listening()
        self._state, action = step(self._state, event)
        return await self._act(action)

    async def _on_mic_packet(self, event: G1Event) -> bool:
        if self._capture is None:
            return True
        self._capture = self._capture.add(event)
        self._mic_seen += 1
        if self._trace and self._mic_seen % 10 == 0:
            self._say(f"  [  mic] {self._capture.seconds:.1f}s captured")
        if self._closing:
            quiet = packet_rms(event.payload) < QUIET_RMS
            self._quiet_packets = self._quiet_packets + 1 if quiet else 0
            if self._quiet_packets * PACKET_SECONDS >= self._tail_quiet_s:
                return await self._finish_listening()
        if self._capture.seconds >= MAX_CAPTURE_S:
            self._say("(capture cap reached; sending)")
            return await self._finish_listening()
        return True

    async def _handle_line(self, line: str) -> bool:
        text = line.strip()
        if not text:
            return True
        if text in QUIT_WORDS:
            return await self._exit()
        if self._sim_gestures and (event := parse_gesture(text)) is not None:
            return await self._handle_event(event)
        word = text.lower()
        if word in HOME_WORDS:
            self._state, action = go_home(self._state)
            return await self._act(action)
        if word in BACK_WORDS:
            self._state, action = close_agent(self._state)
            return await self._act(action)
        if self._state.mode is not Mode.AGENT:
            if word in MENU_WORDS:
                self._state = HubState(
                    self._state.agents, Mode.MENU, self._state.cursor, self._state.rest
                )
                return await self._act(Action.SHOW_MENU)
            return await self._pick_by_text(text)
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
        if action is Action.SHOW_REST:
            self._say(REST_HINT)
        elif action is Action.SHOW_HOME:
            await self._show_home()
        elif action in (Action.SHOW_MENU, Action.CLOSE_AGENT):
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
            await self._start_listening(self._state.selected.name)
        return True

    # -- voice ------------------------------------------------------------

    async def _start_listening(self, title: str) -> None:
        """Long-press began: open the mic and collect packets until it ends."""
        if self._transcriber is None:
            await self._hud.show(f"{title}\n{TYPE_TEXT}")
            return
        self._capture = Capture()
        self._closing = False
        self._mic_seen = 0
        if self._mic_cmd and not await self._mic(True):
            self._say("(the glasses refused to enable the mic; listening anyway)")
        if self._state.rest:
            self._say(f"[{title}] listening...")  # the firmware draws its own screen
        else:
            await self._hud.show(f"{title}\n{LISTENING_TEXT}")

    async def _mic(self, enable: bool) -> bool:
        """The display's mic switch, with BLE trouble logged instead of raised."""
        try:
            return await self._display.set_mic(enable)
        except Exception:  # BleakError/OSError: keep the hub alive
            logger.exception("mic %s failed", "on" if enable else "off")
            return False

    def _schedule_finish(self) -> None:
        """Release seen: keep collecting until the wearer is quiet, with a backstop."""
        if self._capture is None or self._closing:
            return
        self._closing = True
        self._quiet_packets = 0
        self._finish_token += 1
        asyncio.get_running_loop().call_later(
            self._tail_max_s, self._queue.put_nowait, ("finish", self._finish_token)
        )

    async def _cancel_listening(self) -> None:
        self._capture = None
        self._closing = False
        await self._mic(False)

    async def _finish_listening(self) -> bool:
        """Long-press ended: close the mic, transcribe, and ask the open agent."""
        capture, self._capture = self._capture, None
        self._closing = False
        if capture is None:
            return True
        self._mic_seen = 0
        await self._mic(False)
        self._say(
            f"(capture {capture.seconds:.1f}s, {capture.stats.packets} packets, "
            f"{capture.stats.dropped} dropped)"
        )
        if self._record_dir is not None and capture.payloads:
            path = self._record_dir / f"{self._now():%H%M%S}.lc3"
            self._record_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(capture.audio)
            self._say(f"(saved to {path})")
        if capture.too_short:
            await self._hud.show(TOO_SHORT_TEXT)
            return True
        await self._hud.show(TRANSCRIBING_TEXT)
        assert self._transcriber is not None
        try:
            text = (await self._transcriber(capture.audio)).strip()
        except Exception:  # keep the hub alive; the terminal gets the traceback
            logger.exception("transcription failed")
            text = ""
        if not text:
            self._say(f"you (voice, {capture.seconds:.1f}s)> (nothing recognised)")
            await self._hud.show(TOO_SHORT_TEXT)
            return True
        self._say(f"you (voice, {capture.seconds:.1f}s)> {text}")
        return await self._route_voice(text)

    async def _route_voice(self, text: str) -> bool:
        """Spoken navigation words, then agent-name prefixes, then the question."""
        word = text.lower().strip(" .!?,")
        if word in BACK_WORDS | HOME_WORDS | MENU_WORDS:
            return await self._handle_line(word)
        index, question = parse_voice(self._state.agents, text)
        cursor = self._state.cursor if index is None else index
        self._state = HubState(self._state.agents, Mode.AGENT, cursor, self._state.rest)
        if not question:
            spec = self._state.selected
            await self._hud.show(f"{spec.name} ready.\nHold the left temple and ask.")
            return True
        await self._ask(question)
        return True

    # -- screens ----------------------------------------------------------

    async def _show_home(self) -> None:
        home = render_home(
            self._state,
            self._now(),
            lines_per_page=self._lines_per_page,
            max_chars=self._max_chars,
        )
        await self._display.send_text_page(home, status=self._screen_status)

    async def _show_menu(self) -> None:
        menu = render_menu(
            self._state, lines_per_page=self._lines_per_page, max_chars=self._max_chars
        )
        await self._display.send_text_page(menu, status=self._screen_status)

    @property
    def _screen_status(self) -> ScreenStatus:
        """Single-page screens: Even AI 'complete' status in --ai mode, else Text Show."""
        return ScreenStatus.AI_COMPLETE if self._ai_mode else ScreenStatus.TEXT_SHOW

    async def _open_agent(self) -> None:
        """Opened by a long-press (or a typed pick): go straight to listening."""
        spec = self._state.selected
        self._say(f"[{spec.name}] type a question; 'back' = menu, 'home' = home")
        await self._start_listening(f"{spec.name}: {spec.blurb}")

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
        typed = (
            f"Type a number or name to open an agent ({names}); 'menu', 'home', "
            "'back'; /quit to leave."
        )
        if self._sim_gestures:
            return f"{typed} {GESTURE_HELP}."
        if self._state.rest:
            return (
                f"{typed} On the glasses: hold the left temple and speak; say an "
                "agent's name first to switch; double-tap dismisses the answer."
            )
        return (
            f"{typed} On the glasses: tap to open the list, tap right/left to "
            "move, long-press left to open, double-tap to leave."
        )
