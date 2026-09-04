"""Hub state machine: a menu of agents on the HUD, driven by TouchBar gestures.

Pure functions over an immutable HubState.

Two flows. **Rest** (default, `rest=True`): the hub shows nothing and the
glasses sit on the firmware's own dashboard; a long-press on the left temple
starts a question, the answer is shown as an Even AI result, a double tap
dismisses it (the firmware clears the screen itself) and the hub is back at
rest. Measured on hardware 2026-09-03: while app content is on screen the
firmware keeps the TouchBars to itself, so a home page of our own would make
every gesture dead. **Home** (`rest=False`): our own clock page and agent
list, kept for experiments.

    rest   long-press left = talk to the selected agent; taps do nothing
    home   any tap = open the agent list, long-press left = talk
    menu   right tap = next agent, left tap = previous, long-press left = open
    agent  right/left tap = page the answer, long-press left = new question
    double tap: rest flow = dismiss, back to rest; home flow = leave the hub
    triple tap: never used (it toggles the firmware's silent mode)

Navigation by voice: "research what is LC3" opens Research with the question,
"translate" alone switches agents, "back"/"home"/"menu" as words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from .agents import MAX_NAME_CHARS, AgentSpec
from .paginate import DEFAULT_CHARS_PER_LINE, DEFAULT_LINES_PER_PAGE
from .protocol import EventKind, G1Event

HUB_TITLE = "CLAUDE HUB"
HOME_TITLE = "Claude Hub"
CURSOR_MARK = "> "
NO_MARK = "  "


class Mode(Enum):
    REST = "rest"
    HOME = "home"
    MENU = "menu"
    AGENT = "agent"


class Action(Enum):
    NONE = "none"
    SHOW_REST = "show_rest"  # nothing to draw; the firmware owns the screen
    SHOW_HOME = "show_home"
    SHOW_MENU = "show_menu"
    OPEN_AGENT = "open_agent"
    CLOSE_AGENT = "close_agent"
    EXIT_HUB = "exit_hub"
    PAGE_NEXT = "page_next"
    PAGE_PREV = "page_prev"
    NEW_PROMPT = "new_prompt"


@dataclass(frozen=True)
class HubState:
    agents: tuple[AgentSpec, ...]
    mode: Mode = Mode.HOME
    cursor: int = 0
    rest: bool = False  # rest flow: "up" from anywhere is REST, not HOME/EXIT

    def __post_init__(self) -> None:
        if not self.agents:
            raise ValueError("the hub needs at least one agent")
        if not 0 <= self.cursor < len(self.agents):
            raise ValueError(f"cursor {self.cursor} out of range")

    @property
    def selected(self) -> AgentSpec:
        return self.agents[self.cursor]

    @property
    def active(self) -> AgentSpec | None:
        """The agent the wearer is talking to, or None while in the menu."""
        return self.selected if self.mode is Mode.AGENT else None


def step(state: HubState, event: G1Event) -> tuple[HubState, Action]:
    """Apply one glasses event; returns the new state and what to do about it."""
    if event.kind is EventKind.DOUBLE_TAP:
        if state.rest:
            return replace(state, mode=Mode.REST), Action.SHOW_REST
        return state, Action.EXIT_HUB
    if state.mode is Mode.REST:
        return _step_rest(state, event)
    if state.mode is Mode.HOME:
        return _step_home(state, event)
    if state.mode is Mode.MENU:
        return _step_menu(state, event)
    return _step_agent(state, event)


def _step_rest(state: HubState, event: G1Event) -> tuple[HubState, Action]:
    if event.kind is EventKind.AI_START:
        return replace(state, mode=Mode.AGENT), Action.OPEN_AGENT
    return state, Action.NONE


def _step_home(state: HubState, event: G1Event) -> tuple[HubState, Action]:
    if event.kind is EventKind.SINGLE_TAP:
        return replace(state, mode=Mode.MENU), Action.SHOW_MENU
    if event.kind is EventKind.AI_START:
        # The stock long-press-for-Even-AI habit: straight to the default agent.
        return replace(state, mode=Mode.AGENT, cursor=0), Action.OPEN_AGENT
    return state, Action.NONE


def _step_menu(state: HubState, event: G1Event) -> tuple[HubState, Action]:
    if event.kind is EventKind.SINGLE_TAP:
        delta = 1 if event.side == "right" else -1
        cursor = (state.cursor + delta) % len(state.agents)
        return replace(state, cursor=cursor), Action.SHOW_MENU
    if event.kind is EventKind.AI_START:
        return replace(state, mode=Mode.AGENT), Action.OPEN_AGENT
    return state, Action.NONE


def _step_agent(state: HubState, event: G1Event) -> tuple[HubState, Action]:
    if event.kind is EventKind.SINGLE_TAP:
        return state, Action.PAGE_NEXT if event.side == "right" else Action.PAGE_PREV
    if event.kind is EventKind.AI_START:
        return state, Action.NEW_PROMPT
    return state, Action.NONE


def select(state: HubState, index: int) -> tuple[HubState, Action]:
    """Jump straight to an agent (terminal shortcut: number or name)."""
    if not 0 <= index < len(state.agents):
        raise IndexError(index)
    return replace(state, mode=Mode.AGENT, cursor=index), Action.OPEN_AGENT


def close_agent(state: HubState) -> tuple[HubState, Action]:
    """One level up by word ("back"): agent -> menu -> home, or -> rest."""
    if state.rest and state.mode is not Mode.REST:
        return replace(state, mode=Mode.REST), Action.SHOW_REST
    if state.mode is Mode.AGENT:
        return replace(state, mode=Mode.MENU), Action.CLOSE_AGENT
    if state.mode is Mode.MENU:
        return replace(state, mode=Mode.HOME), Action.SHOW_HOME
    return state, Action.NONE


def go_home(state: HubState) -> tuple[HubState, Action]:
    """Straight to the resting screen from anywhere ("home")."""
    target = Mode.REST if state.rest else Mode.HOME
    if state.mode is target:
        return state, Action.NONE
    action = Action.SHOW_REST if state.rest else Action.SHOW_HOME
    return replace(state, mode=target), action


_VOICE_LEAD = re.compile(
    r"^(?:(?:open|ask|use|switch to|talk to)\s+)?([a-z][a-z0-9_-]*)[,:.!?]?(?:\s+(.*))?$",
    re.IGNORECASE | re.DOTALL,
)


def parse_voice(agents: tuple[AgentSpec, ...], text: str) -> tuple[int | None, str]:
    """Split a spoken line into (agent index or None, the rest of the text).

    Agent names double as verbs: "translate good morning" picks Translate and
    asks "good morning"; "research" alone just switches.
    """
    match = _VOICE_LEAD.match(text.strip())
    if match:
        index = find_agent(agents, match.group(1))
        if index is not None:
            return index, (match.group(2) or "").strip()
    return None, text.strip()


def find_agent(agents: tuple[AgentSpec, ...], query: str) -> int | None:
    """Index of the agent matching a 1-based number, an id, or a name."""
    needle = query.strip().lower()
    if needle.isdigit():
        number = int(needle)
        return number - 1 if 1 <= number <= len(agents) else None
    for index, spec in enumerate(agents):
        if needle in (spec.id.lower(), spec.name.lower()):
            return index
    return None


def render_home(
    state: HubState,
    now: datetime,
    *,
    lines_per_page: int = DEFAULT_LINES_PER_PAGE,
    max_chars: int = DEFAULT_CHARS_PER_LINE,
) -> str:
    """The dashboard page: clock, title, and the two gestures that matter."""
    clock = now.strftime("%H:%M")
    date = f"{now.strftime('%a')} {now.day} {now.strftime('%b')}"
    default = state.agents[0].name
    lines = [
        f"{clock}  {date}",
        HOME_TITLE,
        f"hold left temple: talk to {default}",
        f"tap: {len(state.agents)} agents",
    ]
    return "\n".join(line[:max_chars] for line in lines[:lines_per_page])


def render_menu(
    state: HubState,
    *,
    lines_per_page: int = DEFAULT_LINES_PER_PAGE,
    max_chars: int = DEFAULT_CHARS_PER_LINE,
) -> str:
    """One HUD page: a title row, then the window of agents around the cursor."""
    rows = max(lines_per_page - 1, 1)
    start = (state.cursor // rows) * rows
    window = state.agents[start : start + rows]
    name_width = min(max(len(spec.name) for spec in state.agents), MAX_NAME_CHARS)
    counter = f"{state.cursor + 1}/{len(state.agents)}"
    gap = max(max_chars - len(HUB_TITLE) - len(counter), 1)
    header = f"{HUB_TITLE}{' ' * gap}{counter}"
    lines = [header[:max_chars]]
    for index, spec in enumerate(window, start=start):
        mark = CURSOR_MARK if index == state.cursor else NO_MARK
        row = f"{mark}{spec.name:<{name_width}} {spec.blurb}"
        lines.append(row[:max_chars].rstrip())
    return "\n".join(lines)
