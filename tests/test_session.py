"""HubSession end-to-end on the simulator with a stub agent (no claude CLI)."""

import asyncio

from g1bridge.agents import AgentSpec
from g1bridge.session import AgentPool, HubSession
from g1bridge.sim import SimGlasses

AGENTS = (
    AgentSpec("ask", "Ask", "quick answers", "p1", web=False),
    AgentSpec("research", "Research", "deep dives", "p2"),
)


class StubAgent:
    instances: list["StubAgent"] = []

    def __init__(self, spec: AgentSpec):
        self.spec = spec
        self.asked: list[str] = []
        self.closed = False
        StubAgent.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False

    async def ask(self, prompt: str) -> str:
        self.asked.append(prompt)
        return f"{self.spec.name} says: {prompt.upper()}"


async def lines_from(script):
    for line in script:
        await asyncio.sleep(0)  # let the display/event side interleave
        yield line


def run_script(script, *, sim_gestures=True):
    StubAgent.instances = []
    sim = SimGlasses(out=lambda _: None, max_chars=40, lines_per_page=5)
    said: list[str] = []
    session = HubSession(
        sim,
        AGENTS,
        agent_factory=StubAgent,
        max_chars=40,
        lines_per_page=5,
        say=said.append,
        sim_gestures=sim_gestures,
    )
    asyncio.run(session.run(lines_from(script)))
    return sim, said


def test_menu_select_ask_answer_back_exit():
    sim, said = run_script(["r", "hold", "what time is it", "rrr", "ll"])
    pages = sim.pages_shown
    assert pages[0].startswith("CLAUDE HUB")
    assert pages[1].startswith("CLAUDE HUB") and "> Research" in pages[1]
    assert pages[2].startswith("Research")  # opened
    assert pages[3] == "Thinking..."
    assert "RESEARCH SAYS: WHAT TIME IS IT" in pages[4].upper()
    assert pages[5].startswith("CLAUDE HUB")  # rrr -> back to menu
    assert sim.dashboard_calls == 1  # ll in menu -> exit hub
    assert len(StubAgent.instances) == 1
    assert StubAgent.instances[0].spec.id == "research"
    assert StubAgent.instances[0].closed  # pool closed on exit


def test_typed_selection_and_eof_closes_cleanly():
    sim, said = run_script(["ask", "hello there", "2", "again"])
    assert [a.spec.id for a in StubAgent.instances] == ["ask"]
    # "2" while talking to an agent is text for it, not a menu pick.
    assert StubAgent.instances[0].asked == ["hello there", "2", "again"]
    assert StubAgent.instances[0].closed
    assert sim.dashboard_calls == 1  # EOF leaves the HUD tidy


def test_gesture_words_are_text_when_sim_gestures_off():
    sim, said = run_script(["ask", "rrr"], sim_gestures=False)
    assert StubAgent.instances[0].asked == ["rrr"]


def test_unknown_text_in_menu_is_reported_not_sent():
    sim, said = run_script(["frobnicate"])
    assert StubAgent.instances == []
    assert any("frobnicate" in line for line in said)


def test_agent_pool_reuses_sessions():
    async def go():
        pool = AgentPool(StubAgent)
        first = await pool.get(AGENTS[0])
        second = await pool.get(AGENTS[0])
        other = await pool.get(AGENTS[1])
        assert first is second and other is not first
        await pool.close_all()
        assert first.closed and other.closed

    StubAgent.instances = []
    asyncio.run(go())


def test_back_word_returns_to_menu_then_pick_another():
    sim, said = run_script(["ask", "hi", "back", "2", "yo"])
    assert [a.spec.id for a in StubAgent.instances] == ["ask", "research"]
    assert StubAgent.instances[0].asked == ["hi"]
    assert StubAgent.instances[1].asked == ["yo"]
    menus = [p for p in sim.pages_shown if p.startswith("CLAUDE HUB")]
    assert len(menus) == 2  # start + after "back"


class FlakyCloseAgent(StubAgent):
    async def __aexit__(self, *exc):
        self.closed = True
        raise RuntimeError("subprocess already gone")


def test_pool_closes_every_agent_even_if_one_fails():
    async def go():
        StubAgent.instances = []
        pool = AgentPool(
            lambda spec: FlakyCloseAgent(spec) if spec.id == "ask" else StubAgent(spec)
        )
        await pool.get(AGENTS[0])
        await pool.get(AGENTS[1])
        await pool.close_all()  # must not raise
        assert all(a.closed for a in StubAgent.instances)

    asyncio.run(go())
