"""Hub state machine + menu rendering. Pure logic, no I/O."""

import pytest

from g1bridge.agents import AgentSpec
from g1bridge.hub import (
    HUB_TITLE,
    Action,
    HubState,
    Mode,
    close_agent,
    find_agent,
    render_menu,
    select,
    step,
)
from g1bridge.protocol import EventKind, G1Event

AGENTS = tuple(
    AgentSpec(f"a{i}", f"Agent{i}", f"blurb {i}", "prompt") for i in range(1, 8)
)


def ev(kind: EventKind, side: str = "right") -> G1Event:
    return G1Event(kind=kind, side=side, raw=b"")


def test_menu_taps_move_cursor_and_wrap():
    state = HubState(AGENTS)
    state, action = step(state, ev(EventKind.SINGLE_TAP, "right"))
    assert (state.cursor, action) == (1, Action.SHOW_MENU)
    state, _ = step(state, ev(EventKind.SINGLE_TAP, "left"))
    state, _ = step(state, ev(EventKind.SINGLE_TAP, "left"))
    assert state.cursor == len(AGENTS) - 1  # wrapped backwards
    state, _ = step(state, ev(EventKind.SINGLE_TAP, "right"))
    assert state.cursor == 0  # wrapped forwards


def test_long_press_opens_selected_agent():
    state = HubState(AGENTS, cursor=2)
    assert state.active is None
    state, action = step(state, ev(EventKind.AI_START, "left"))
    assert action is Action.OPEN_AGENT
    assert state.mode is Mode.AGENT
    assert state.active == AGENTS[2]


def test_agent_mode_gestures():
    state = HubState(AGENTS, mode=Mode.AGENT, cursor=1)
    assert step(state, ev(EventKind.SINGLE_TAP, "right"))[1] is Action.PAGE_NEXT
    assert step(state, ev(EventKind.SINGLE_TAP, "left"))[1] is Action.PAGE_PREV
    assert step(state, ev(EventKind.AI_START, "left"))[1] is Action.NEW_PROMPT
    back, action = step(state, ev(EventKind.TRIPLE_TAP))
    assert action is Action.CLOSE_AGENT
    assert back.mode is Mode.MENU and back.cursor == 1  # selection remembered


def test_double_tap_exits_hub_in_both_modes():
    # The firmware treats a double tap as "exit app"; the hub must agree.
    assert step(HubState(AGENTS), ev(EventKind.DOUBLE_TAP))[1] is Action.EXIT_HUB
    in_agent = HubState(AGENTS, mode=Mode.AGENT)
    assert step(in_agent, ev(EventKind.DOUBLE_TAP))[1] is Action.EXIT_HUB


def test_irrelevant_events_are_noops():
    state = HubState(AGENTS)
    for kind in (EventKind.HEARTBEAT_ACK, EventKind.WEARING, EventKind.TRIPLE_TAP):
        new_state, action = step(state, ev(kind))
        assert new_state == state and action is Action.NONE


def test_step_is_pure():
    state = HubState(AGENTS)
    step(state, ev(EventKind.SINGLE_TAP))
    assert state.cursor == 0


def test_select_by_index_and_lookup():
    state = HubState(AGENTS)
    opened, action = select(state, 4)
    assert action is Action.OPEN_AGENT and opened.active == AGENTS[4]
    with pytest.raises(IndexError):
        select(state, 99)
    assert find_agent(AGENTS, "3") == 2  # 1-based number in, 0-based index out
    assert find_agent(AGENTS, "agent5") == 4  # case-insensitive name
    assert find_agent(AGENTS, "a6") == 5  # id
    assert find_agent(AGENTS, "nope") is None
    assert find_agent(AGENTS, "0") is None


def test_render_menu_marks_cursor_and_fits_page():
    text = render_menu(HubState(AGENTS, cursor=1), lines_per_page=5, max_chars=40)
    lines = text.split("\n")
    assert len(lines) == 5
    assert lines[0].startswith(HUB_TITLE) and lines[0].endswith("2/7")
    assert lines[1].startswith("  Agent1")
    assert lines[2].startswith("> Agent2")
    assert "blurb 2" in lines[2]
    assert all(len(line) <= 40 for line in lines)


def test_render_menu_scrolls_in_windows():
    # 4 item rows per page: cursor 5 (Agent6) is on the second window.
    text = render_menu(HubState(AGENTS, cursor=5), lines_per_page=5, max_chars=40)
    lines = text.split("\n")
    assert lines[1].startswith("  Agent5")
    assert lines[2].startswith("> Agent6")
    assert lines[3].startswith("  Agent7")
    assert len(lines) == 4  # short last window, no padding rows


def test_render_menu_truncates_long_blurbs():
    agents = (AgentSpec("x", "X", "a" * 26, "p"),)
    text = render_menu(HubState(agents), lines_per_page=5, max_chars=20)
    row = text.split("\n")[1]
    assert len(row) <= 20 and row.startswith("> X")


def test_hub_state_rejects_empty_agent_list():
    with pytest.raises(ValueError):
        HubState(())


def test_close_agent_by_word():
    in_agent = HubState(AGENTS, mode=Mode.AGENT, cursor=3)
    back, action = close_agent(in_agent)
    assert action is Action.CLOSE_AGENT and back.mode is Mode.MENU
    assert back.cursor == 3
    same, action = close_agent(HubState(AGENTS))
    assert action is Action.NONE and same == HubState(AGENTS)
