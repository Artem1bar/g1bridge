from g1bridge.agent import text_delta


def test_text_delta_reads_only_text_deltas():
    assert (
        text_delta(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Par"},
            }
        )
        == "Par"
    )
    assert (
        text_delta(
            {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": "{"},
            }
        )
        == ""
    )
    assert text_delta({"type": "message_start"}) == ""
    assert text_delta({"type": "content_block_delta"}) == ""


def test_streaming_keeps_only_the_latest_turn():
    import asyncio

    from claude_agent_sdk import AssistantMessage, StreamEvent, TextBlock

    from g1bridge.agent import GlassesAgent

    def ev(kind, text=None):
        event = {"type": kind}
        if text is not None:
            event["delta"] = {"type": "text_delta", "text": text}
        return StreamEvent(uuid="u", session_id="s", event=event)

    messages = [
        ev("message_start"),
        ev("content_block_delta", "Let me search"),
        AssistantMessage(content=[TextBlock(text="Let me search")], model="m"),
        ev("message_start"),
        ev("content_block_delta", "Paris"),
        ev("content_block_delta", " is the capital."),
        AssistantMessage(content=[TextBlock(text="Paris is the capital.")], model="m"),
    ]

    class FakeClient:
        async def query(self, prompt):
            pass

        async def receive_response(self):
            for message in messages:
                yield message

    agent = GlassesAgent.__new__(GlassesAgent)
    agent._client = FakeClient()

    async def collect():
        return [text async for text in agent.ask_stream("q")]

    seen = asyncio.run(collect())
    assert seen[-1] == "Paris is the capital."
    assert "Paris" in seen[-2] and "search" not in seen[-2]  # narration reset
