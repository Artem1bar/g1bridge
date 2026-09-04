"""Hub state machine: a menu of agents on the HUD, driven by TouchBar gestures.

Pure functions over an immutable HubState. Gesture map (both modes):

    menu   right tap = next agent, left tap = previous, long-press left = open
    agent  right/left tap = page the answer, long-press left = new question,
           triple tap = back to the menu
    both   double tap = leave the hub (the G1 firmware treats a double tap as
           "exit app" itself, so the hub must never rely on it for anything else)
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from .agents import MAX_NAME_CHARS, AgentSpec
from .paginate import DEFAULT_CHARS_PER_LINE, DEFAULT_LINES_PER_PAGE
from .protocol import EventKind, G1Event

HUB_TITLE = "CLAUDE HUB"
CURSOR_MARK = "> "
NO_MARK = "  "


class Mode(Enum):
    MENU = "menu"
    AGENT = "agent"


class Action(Enum):
    NONE = "none"
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
    mode: Mode = Mode.MENU
    cursor: int = 0

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
    if state.mode is Mode.MENU:
        return _step_menu(state, event)
    return _step_agent(state, event)


def _step_menu(state: HubState, event: G1Event) -> tuple[HubState, Action]:
    if event.kind is EventKind.SINGLE_TAP:
        delta = 1 if event.side == "right" else -1
        cursor = (state.cursor + delta) % len(state.agents)
        return replace(state, cursor=cursor), Action.SHOW_MENU
    if event.kind is EventKind.AI_START:
        return replace(state, mode=Mode.AGENT), Action.OPEN_AGENT
    if event.kind is EventKind.DOUBLE_TAP:
        return state, Action.EXIT_HUB
    return state, Action.NONE


def _step_agent(state: HubState, event: G1Event) -> tuple[HubState, Action]:
    if event.kind is EventKind.SINGLE_TAP:
        return state, Action.PAGE_NEXT if event.side == "right" else Action.PAGE_PREV
    if event.kind is EventKind.AI_START:
        return state, Action.NEW_PROMPT
    if event.kind is EventKind.TRIPLE_TAP:
        return replace(state, mode=Mode.MENU), Action.CLOSE_AGENT
    if event.kind is EventKind.DOUBLE_TAP:
        return state, Action.EXIT_HUB
    return state, Action.NONE


def select(state: HubState, index: int) -> tuple[HubState, Action]:
    """Jump straight to an agent (terminal shortcut: number or name)."""
    if not 0 <= index < len(state.agents):
        raise IndexError(index)
    return replace(state, mode=Mode.AGENT, cursor=index), Action.OPEN_AGENT


def close_agent(state: HubState) -> tuple[HubState, Action]:
    """Back to the menu by word ("back"/"menu", typed today, spoken later)."""
    if state.mode is Mode.MENU:
        return state, Action.NONE
    return replace(state, mode=Mode.MENU), Action.CLOSE_AGENT


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
