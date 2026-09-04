"""HubSession end-to-end on the simulator with a stub agent (no claude CLI)."""

import asyncio
from datetime import datetime

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


async def stub_transcriber(audio: bytes) -> str:
    return f"heard {len(audio)} bytes"


def run_script(
    script, *, sim_gestures=True, transcriber=None, rest=False, mic_cmd=False
):
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
        now=lambda: datetime(2026, 9, 3, 19, 27),
        transcriber=transcriber,
        rest=rest,
        mic_cmd=mic_cmd,
        tail_max_s=0.0,  # tests: finish right after the release
    )
    asyncio.run(session.run(lines_from(script)))
    return sim, said


def test_home_menu_select_ask_answer_back_exit():
    sim, said = run_script(["r", "r", "hold", "what time is it", "back", "ll"])
    pages = sim.pages_shown
    assert pages[0].startswith("CLAUDE-TEC") and "23:14" not in pages[0]  # home
    assert pages[1].startswith("CLAUDE HUB") and "> Ask" in pages[1]  # tap -> list
    assert pages[2].startswith("CLAUDE HUB") and "> Research" in pages[2]
    assert pages[3].startswith("Research: deep dives")  # opened, asks for typing
    assert pages[4] == "Thinking..."
    assert "RESEARCH SAYS: WHAT TIME IS IT" in pages[5].upper()
    assert pages[6].startswith("CLAUDE HUB")  # "back" -> menu
    assert sim.dashboard_calls == 1  # ll -> exit hub
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
    sim, said = run_script(["ask", "rr"], sim_gestures=False)
    assert StubAgent.instances[0].asked == ["rr"]


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
    sim, said = run_script(["ask", "hi", "back", "2", "yo", "home"])
    assert [a.spec.id for a in StubAgent.instances] == ["ask", "research"]
    assert StubAgent.instances[0].asked == ["hi"]
    assert StubAgent.instances[1].asked == ["yo"]
    menus = [p for p in sim.pages_shown if p.startswith("CLAUDE HUB")]
    assert len(menus) == 1  # only after "back" (start is the home screen)
    assert sim.pages_shown[-1].startswith("CLAUDE-TEC")  # "home" from inside an agent


def test_hold_on_home_talks_to_default_agent():
    sim, said = run_script(["hold", "hey"])
    assert [a.spec.id for a in StubAgent.instances] == ["ask"]
    assert StubAgent.instances[0].asked == ["hey"]


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


def test_voice_round_trip_with_a_transcriber():
    sim, said = run_script(
        ["hold", "mic", "mic", "mic", "mic", "mic", "release"],
        transcriber=stub_transcriber,
        mic_cmd=True,
    )
    assert sim.mic_calls == [True, False]
    assert [a.spec.id for a in StubAgent.instances] == ["ask"]  # default from home
    assert StubAgent.instances[0].asked == ["heard 1000 bytes"]
    assert any("voice" in line and "0.5s" in line for line in said)
    listening = [p for p in sim.pages_shown if "Listening" in p]
    assert listening and listening[0].startswith("Ask: quick answers")


def test_voice_too_short_is_not_sent():
    sim, said = run_script(
        ["hold", "mic", "release"], transcriber=stub_transcriber, mic_cmd=True
    )
    assert sim.mic_calls == [True, False]
    assert StubAgent.instances == []
    assert "Didn't catch that" in sim.pages_shown[-1]


def test_release_without_hold_is_ignored_and_no_transcriber_means_typing():
    sim, said = run_script(["release", "hold", "mic", "release", "typed q"])
    assert sim.mic_calls == []  # no transcriber: the mic is never opened
    assert any("Type your question" in p for p in sim.pages_shown)
    assert StubAgent.instances[0].asked == ["typed q"]


def test_mic_packets_outside_a_capture_are_dropped():
    sim, said = run_script(
        ["mic", "mic", "hold", "mic", "mic", "mic", "mic", "release"],
        transcriber=stub_transcriber,
    )
    assert StubAgent.instances[0].asked == ["heard 800 bytes"]


def test_trace_echoes_gestures_but_not_audio():
    StubAgent.instances = []
    sim = SimGlasses(out=lambda _: None, max_chars=40, lines_per_page=5)
    said: list[str] = []
    session = HubSession(
        sim,
        AGENTS,
        agent_factory=StubAgent,
        say=said.append,
        sim_gestures=True,
        transcriber=stub_transcriber,
        trace=True,
        tail_max_s=0.0,
    )
    asyncio.run(session.run(lines_from(["r", "hold", "mic", "release"])))
    kinds = [line.strip() for line in said if line.startswith("  [")]
    assert kinds == ["[right] single_tap", "[ left] ai_start", "[ left] ai_stop"]


def test_second_press_sends_when_release_is_never_reported():
    sim, said = run_script(
        ["hold", "mic", "mic", "mic", "mic", "mic", "mic", "hold", "release"],
        transcriber=stub_transcriber,
        mic_cmd=True,
    )
    assert StubAgent.instances[0].asked == ["heard 1200 bytes"]
    assert sim.mic_calls == [True, False]  # the stray release afterwards is ignored


def test_double_tap_while_listening_cancels_and_leaves():
    sim, said = run_script(
        ["hold", "mic", "mic", "mic", "mic", "mic", "ll"],
        transcriber=stub_transcriber,
        mic_cmd=True,
    )
    assert StubAgent.instances == []
    assert sim.mic_calls == [True, False]
    assert sim.dashboard_calls == 1


def test_capture_cap_sends_on_its_own():
    from g1bridge import session as session_module

    packets = int(session_module.MAX_CAPTURE_S * 10)
    sim, said = run_script(
        ["hold"] + ["mic"] * (packets + 5), transcriber=stub_transcriber
    )
    assert StubAgent.instances[0].asked == [f"heard {packets * 200} bytes"]


def test_ai_mode_uses_even_ai_status_for_home_and_menu():
    from g1bridge.protocol import ScreenStatus

    class StatusSpy(SimGlasses):
        def __init__(self):
            super().__init__(out=lambda _: None)
            self.statuses = []

        async def send_text_page(self, text, *, page=1, total_pages=1, status=None):
            self.statuses.append(status)
            await super().send_text_page(
                text, page=page, total_pages=total_pages, status=status
            )

    for ai_mode, expected in (
        (False, ScreenStatus.TEXT_SHOW),
        (True, ScreenStatus.AI_COMPLETE),
    ):
        spy = StatusSpy()
        session = HubSession(
            spy, AGENTS, agent_factory=StubAgent, sim_gestures=True, ai_mode=ai_mode
        )
        asyncio.run(session.run(lines_from(["r"])))
        assert spy.statuses[:2] == [expected, expected]


def test_mic_failure_does_not_kill_the_hub():
    class FlakyMic(SimGlasses):
        async def set_mic(self, enable: bool) -> bool:
            raise OSError("BLE write failed")

    StubAgent.instances = []
    sim = FlakyMic(out=lambda _: None)
    said: list[str] = []
    session = HubSession(
        sim,
        AGENTS,
        agent_factory=StubAgent,
        say=said.append,
        sim_gestures=True,
        transcriber=stub_transcriber,
        mic_cmd=True,
        tail_max_s=0.0,
    )
    script = ["hold", "mic", "mic", "mic", "mic", "mic", "release", "back", "2"]
    asyncio.run(session.run(lines_from(script)))
    assert StubAgent.instances[0].asked == ["heard 1000 bytes"]  # voice still worked
    assert any(p.startswith("Research") for p in sim.pages_shown)  # kept serving


def test_handler_exception_is_logged_and_the_hub_keeps_serving():
    class Fragile(SimGlasses):
        async def send_text_page(self, text, *, page=1, total_pages=1, status=None):
            if "Research" in text:
                raise OSError("arm went away")
            await super().send_text_page(
                text, page=page, total_pages=total_pages, status=status
            )

    StubAgent.instances = []
    sim = Fragile(out=lambda _: None)
    said: list[str] = []
    session = HubSession(
        sim, AGENTS, agent_factory=StubAgent, say=said.append, sim_gestures=True
    )
    asyncio.run(session.run(lines_from(["2", "home"])))
    assert any("failed" in line for line in said)
    assert sim.pages_shown[-1].startswith("CLAUDE-TEC")


# ---- rest flow: the default on the glasses ----------------------------------

MIC5 = ["mic"] * 5


def test_rest_flow_shows_nothing_until_the_answer():
    sim, said = run_script(
        ["hold", *MIC5, "release", "ll", "hold", *MIC5, "release"],
        transcriber=stub_transcriber,
        rest=True,
    )
    assert sim.pages_shown[:2] == ["Heard you. Transcribing...", "Thinking..."]
    assert sim.mic_calls == [False, False]  # only the off command, per capture
    assert sim.dashboard_calls == 1  # only the EOF exit; the double tap sent nothing
    assert [a.spec.id for a in StubAgent.instances] == ["ask"]
    assert StubAgent.instances[0].asked == ["heard 1000 bytes", "heard 1000 bytes"]
    assert any("At rest" in line for line in said)


async def routing_transcriber(audio: bytes) -> str:
    return routing_transcriber.lines.pop(0)


def test_voice_routing_by_agent_name():
    routing_transcriber.lines = [
        "Research what is LC3?",
        "ask",
        "good morning",
        "back",
    ]
    sim, said = run_script(
        ["hold", *MIC5, "release"] * 4, transcriber=routing_transcriber, rest=True
    )
    ids = [a.spec.id for a in StubAgent.instances]
    assert ids == ["research", "ask"]
    assert StubAgent.instances[0].asked == ["what is LC3?"]
    assert StubAgent.instances[1].asked == ["good morning"]
    assert any(p.startswith("Ask ready") for p in sim.pages_shown)
    assert said[-1].startswith("At rest")  # "back" -> rest


def test_rest_flow_ignores_taps():
    sim, said = run_script(["r", "l", "rrr"], rest=True)
    assert sim.pages_shown == []


def test_capture_ends_after_a_quiet_tail_not_at_the_release():
    StubAgent.instances = []
    sim = SimGlasses(out=lambda _: None)
    session = HubSession(
        sim,
        AGENTS,
        agent_factory=StubAgent,
        say=lambda _: None,
        sim_gestures=True,
        transcriber=stub_transcriber,
        rest=True,
        tail_quiet_s=0.2,  # two quiet (all-zero) packets end the capture
        tail_max_s=5.0,
    )
    script = ["hold", "mic", "mic", "mic", "mic", "mic", "release", "mic", "mic", "mic"]
    asyncio.run(session.run(lines_from(script)))
    # 5 while held + 2 quiet ones after the release; the third arrives after
    assert StubAgent.instances[0].asked == ["heard 1400 bytes"]


def test_backstop_timer_ends_a_capture_when_packets_stop():
    StubAgent.instances = []
    sim = SimGlasses(out=lambda _: None)
    session = HubSession(
        sim,
        AGENTS,
        agent_factory=StubAgent,
        say=lambda _: None,
        sim_gestures=True,
        transcriber=stub_transcriber,
        rest=True,
        tail_quiet_s=1.0,
        tail_max_s=0.05,
    )

    async def script():
        for line in ["hold", "mic", "mic", "mic", "mic", "mic", "release"]:
            await asyncio.sleep(0)
            yield line
        await asyncio.sleep(0.2)  # no more packets; the backstop fires

    asyncio.run(session.run(script()))
    assert StubAgent.instances[0].asked == ["heard 1000 bytes"]


def test_captures_are_recorded_when_asked(tmp_path):
    StubAgent.instances = []
    sim = SimGlasses(out=lambda _: None)
    said: list[str] = []
    session = HubSession(
        sim,
        AGENTS,
        agent_factory=StubAgent,
        say=said.append,
        sim_gestures=True,
        transcriber=stub_transcriber,
        rest=True,
        tail_max_s=0.0,
        record_dir=tmp_path / "caps",
        now=lambda: datetime(2026, 9, 3, 22, 41, 5),
    )
    asyncio.run(session.run(lines_from(["hold", *MIC5, "release"])))
    saved = tmp_path / "caps" / "224105.lc3"
    assert saved.stat().st_size == 1000
    assert any("capture 0.5s, 5 packets" in line for line in said)


def test_nothing_recognised_is_reported():
    async def silent(audio: bytes) -> str:
        return ""

    sim, said = run_script(["hold", *MIC5, "release"], transcriber=silent, rest=True)
    assert any("nothing recognised" in line for line in said)
    assert "Didn't catch that" in sim.pages_shown[-1]
    assert StubAgent.instances == []


class StreamingStub(StubAgent):
    chunks = ["Paris", "Paris is the capital", "Paris is the capital of France."]

    async def ask_stream(self, prompt: str):
        self.asked.append(prompt)
        for chunk in self.chunks:
            yield chunk


def test_answers_are_previewed_while_streaming():
    StubAgent.instances = []
    sim = SimGlasses(out=lambda _: None)
    ticks = iter(range(0, 100, 1))  # every preview is 1 s after the last
    session = HubSession(
        sim,
        AGENTS,
        agent_factory=StreamingStub,
        say=lambda _: None,
        sim_gestures=True,
        clock=lambda: float(next(ticks)),
    )
    asyncio.run(session.run(lines_from(["ask", "hi"])))
    pages = sim.pages_shown
    final = pages.index("Paris is the capital of France.")
    assert "Paris is the capital" in pages[:final]  # a preview went out first
    assert pages[final - 1] != "Thinking..."  # the preview replaced the placeholder


def test_previews_are_throttled():
    StubAgent.instances = []
    sim = SimGlasses(out=lambda _: None)
    session = HubSession(
        sim,
        AGENTS,
        agent_factory=StreamingStub,
        say=lambda _: None,
        sim_gestures=True,
        clock=lambda: 0.0,  # no time passes: never a second preview
    )
    asyncio.run(session.run(lines_from(["ask", "hi"])))
    previews = [p for p in sim.pages_shown if p.startswith("Paris")]
    assert previews[-1] == "Paris is the capital of France."
    assert len(previews) <= 2  # at most one preview plus the final page


def test_home_flow_shows_the_pipboy_page():
    sim, said = run_script(["r"])  # home flow: any tap opens the list
    assert sim.pages_shown[0].startswith("CLAUDE-TEC")
    assert "BATT L[??????]--%" in sim.pages_shown[0]


def test_look_up_dashboard_in_rest_flow(monkeypatch):
    from g1bridge.protocol import EventKind
    from g1bridge.sim import GESTURES

    monkeypatch.setitem(GESTURES, "up", (EventKind.DASHBOARD_OPEN, "left"))
    monkeypatch.setitem(GESTURES, "down", (EventKind.DASHBOARD_CLOSE, "left"))
    monkeypatch.setitem(GESTURES, "batt", (EventKind.BATTERY, "right"))
    StubAgent.instances = []
    sim = SimGlasses(out=lambda _: None)
    session = HubSession(
        sim,
        AGENTS,
        agent_factory=StubAgent,
        say=lambda _: None,
        sim_gestures=True,
        rest=True,
        dashboard=True,
        now=lambda: datetime(2026, 9, 3, 23, 14),
    )
    asyncio.run(session.run(lines_from(["down", "up", "down"])))
    assert len(sim.pages_shown) == 1  # only the look-up draws; the look-down does not
    assert sim.pages_shown[0].startswith("CLAUDE-TEC")


def test_battery_events_feed_the_home_screen():
    from g1bridge.protocol import EventKind, G1Event

    StubAgent.instances = []
    sim = SimGlasses(out=lambda _: None)
    session = HubSession(
        sim,
        AGENTS,
        agent_factory=StubAgent,
        say=lambda _: None,
        sim_gestures=True,
        rest=True,
        dashboard=True,
    )

    async def script():
        sim.inject(G1Event(EventKind.BATTERY, "left", b"", payload=b"\x21"))
        sim.inject(G1Event(EventKind.BATTERY, "right", b"", payload=b"\x25"))
        sim.inject(G1Event(EventKind.DASHBOARD_OPEN, "left", b""))
        await asyncio.sleep(0)
        yield "/quit"

    asyncio.run(session.run(script()))
    assert "BATT L[##....]33%  R[##....]37%" in sim.pages_shown[0]


def test_dashboard_off_ignores_look_up():
    from g1bridge.protocol import EventKind, G1Event

    StubAgent.instances = []
    sim = SimGlasses(out=lambda _: None)
    session = HubSession(
        sim,
        AGENTS,
        agent_factory=StubAgent,
        say=lambda _: None,
        sim_gestures=True,
        rest=True,
    )

    async def script():
        sim.inject(G1Event(EventKind.DASHBOARD_OPEN, "left", b""))
        await asyncio.sleep(0)
        yield "/quit"

    asyncio.run(session.run(script()))
    assert sim.pages_shown == []
