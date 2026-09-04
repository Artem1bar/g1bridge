"""Hub state machine + menu rendering. Pure logic, no I/O."""

import pytest

from g1bridge.agents import AgentSpec
from datetime import datetime

from g1bridge.hub import (
    HOME_TITLE,
    HUB_TITLE,
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
from g1bridge.protocol import EventKind, G1Event

AGENTS = tuple(
    AgentSpec(f"a{i}", f"Agent{i}", f"blurb {i}", "prompt") for i in range(1, 8)
)


def ev(kind: EventKind, side: str = "right") -> G1Event:
    return G1Event(kind=kind, side=side, raw=b"")


def test_menu_taps_move_cursor_and_wrap():
    state = HubState(AGENTS, mode=Mode.MENU)
    state, action = step(state, ev(EventKind.SINGLE_TAP, "right"))
    assert (state.cursor, action) == (1, Action.SHOW_MENU)
    state, _ = step(state, ev(EventKind.SINGLE_TAP, "left"))
    state, _ = step(state, ev(EventKind.SINGLE_TAP, "left"))
    assert state.cursor == len(AGENTS) - 1  # wrapped backwards
    state, _ = step(state, ev(EventKind.SINGLE_TAP, "right"))
    assert state.cursor == 0  # wrapped forwards


def test_long_press_opens_selected_agent():
    state = HubState(AGENTS, mode=Mode.MENU, cursor=2)
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
    back, action = close_agent(state)
    assert action is Action.CLOSE_AGENT
    assert back.mode is Mode.MENU and back.cursor == 1  # selection remembered


def test_double_tap_exits_hub_in_every_mode():
    # The firmware treats a double tap as "exit app"; the hub must agree.
    for mode in Mode:
        state = HubState(AGENTS, mode=mode)
        assert step(state, ev(EventKind.DOUBLE_TAP))[1] is Action.EXIT_HUB


def test_home_gestures():
    home = HubState(AGENTS)
    assert home.mode is Mode.HOME
    menu, action = step(home, ev(EventKind.SINGLE_TAP, "left"))
    assert action is Action.SHOW_MENU and menu.mode is Mode.MENU
    talk, action = step(HubState(AGENTS, cursor=4), ev(EventKind.AI_START, "left"))
    assert action is Action.OPEN_AGENT and talk.active == AGENTS[0]
    assert step(home, ev(EventKind.TRIPLE_TAP))[1] is Action.NONE


def test_triple_tap_is_left_to_the_firmware():
    # It toggles silent mode on the glasses (F5 04 / F5 05), so the hub ignores it.
    for mode in Mode:
        state = HubState(AGENTS, mode=mode, cursor=2)
        assert step(state, ev(EventKind.TRIPLE_TAP)) == (state, Action.NONE)


def test_go_home_from_anywhere():
    for mode in (Mode.MENU, Mode.AGENT):
        home, action = go_home(HubState(AGENTS, mode=mode))
        assert action is Action.SHOW_HOME and home.mode is Mode.HOME
    assert go_home(HubState(AGENTS))[1] is Action.NONE


def test_render_home_shows_clock_and_hints():
    when = datetime(2026, 9, 3, 19, 27)
    text = render_home(HubState(AGENTS), when, lines_per_page=5, max_chars=40)
    lines = text.split("\n")
    assert lines[0] == "19:27  Thu 3 Sep"
    assert lines[1] == HOME_TITLE
    assert "Agent1" in lines[2]
    assert "7 agents" in lines[3]
    assert len(lines) <= 5 and all(len(line) <= 40 for line in lines)


def test_irrelevant_events_are_noops():
    state = HubState(AGENTS, mode=Mode.MENU)
    for kind in (EventKind.HEARTBEAT_ACK, EventKind.WEARING, EventKind.AI_STOP):
        new_state, action = step(state, ev(kind))
        assert new_state == state and action is Action.NONE


def test_step_is_pure():
    state = HubState(AGENTS, mode=Mode.MENU)
    step(state, ev(EventKind.SINGLE_TAP))
    assert state.cursor == 0 and state.mode is Mode.MENU


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


def test_close_agent_by_word_goes_one_level_up():
    in_agent = HubState(AGENTS, mode=Mode.AGENT, cursor=3)
    menu, action = close_agent(in_agent)
    assert action is Action.CLOSE_AGENT and menu.mode is Mode.MENU
    assert menu.cursor == 3
    home, action = close_agent(menu)
    assert action is Action.SHOW_HOME and home.mode is Mode.HOME
    same, action = close_agent(home)
    assert action is Action.NONE and same == home


def test_rest_flow_gestures():
    rest = HubState(AGENTS, mode=Mode.REST, rest=True)
    talk, action = step(rest, ev(EventKind.AI_START, "left"))
    assert action is Action.OPEN_AGENT and talk.mode is Mode.AGENT
    dismissed, action = step(talk, ev(EventKind.DOUBLE_TAP))
    assert action is Action.SHOW_REST and dismissed.mode is Mode.REST
    assert step(rest, ev(EventKind.SINGLE_TAP))[1] is Action.NONE
    assert close_agent(talk) == (dismissed, Action.SHOW_REST)
    assert go_home(talk) == (dismissed, Action.SHOW_REST)
    assert go_home(rest)[1] is Action.NONE


def test_parse_voice_prefixes():
    assert parse_voice(AGENTS, "Agent3, what is LC3?") == (2, "what is LC3?")
    assert parse_voice(AGENTS, "open agent5") == (4, "")
    assert parse_voice(AGENTS, "switch to Agent2 please") == (1, "please")
    assert parse_voice(AGENTS, "what is the weather") == (None, "what is the weather")
    assert parse_voice(AGENTS, "  ") == (None, "")
